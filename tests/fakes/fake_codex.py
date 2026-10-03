"""A stand-in for the Codex CLI, driven by FAKE_CODEX_SCENARIO.

It accepts only the argv the skill is supposed to send, reads the envelope from stdin, and writes what the real CLI writes: JSON events on stdout, the last message to the -o file, the image under $CODEX_HOME/generated_images/<thread id>/, and a rollout under $CODEX_HOME/sessions/YYYY/MM/DD/. Every exec appends start/end records to $CODEX_HOME/fake-calls.jsonl so tests can see concurrency and what was passed.

Scenarios: ok normalized unverified rewrite_ok rewrite_twice refuse_output refuse_input refuse_unknown refuse_multi exit_nonzero bad_json path_fallback path_outside hang hang_stubborn logged_out.
"""

from __future__ import annotations

import datetime
import fcntl
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

HOME = Path(os.environ["CODEX_HOME"])
SCENARIO = os.environ.get("FAKE_CODEX_SCENARIO", "ok")
EXPECTED_FLAGS = ["exec", "--json", "--skip-git-repo-check", "-s", "read-only", "--output-schema"]


def log(record: dict) -> None:
    with open(HOME / "fake-calls.jsonl", "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.write(json.dumps(record) + "\n")


def call_number() -> int:
    counter = HOME / "fake-counter"
    with open(counter, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        n = int(fh.read() or 0) + 1
        fh.seek(0)
        fh.truncate()
        fh.write(str(n))
    return n


def failure_line(error: dict) -> str:
    """The rollout carries the API error as a JSON string inside Rust's Some(...), inside the event's own JSON."""
    inner = json.dumps({"error": error})
    event = {"timestamp": datetime.datetime.now().isoformat(), "type": "event_msg", "payload": {"type": "error", "message": f"image generation failed: Some({json.dumps(inner)})"}}
    return json.dumps(event)


REFUSALS = {
    "refuse_output": [{"code": "moderation_blocked", "message": "Your request was rejected by the safety system.", "moderation_details": {"moderation_stage": "output", "categories": ["violence"]}}],
    "refuse_input": [{"code": "moderation_blocked", "message": "Your prompt was rejected.", "moderation_details": {"moderation_stage": "input", "categories": ["sexual"]}}],
    "refuse_unknown": [{"code": "moderation_blocked", "message": "Rejected."}],
    "refuse_multi": [
        {"code": "moderation_blocked", "message": "first", "moderation_details": {"moderation_stage": "input", "categories": ["a"]}},
        {"code": "moderation_blocked", "message": "second", "moderation_details": {"moderation_stage": "output", "categories": ["violence", "gore"]}},
        {"code": "moderation_blocked", "message": "third", "moderation_details": {"moderation_stage": "input", "categories": ["b"]}},
    ],
}


def write_png(path: Path, transparent: bool) -> None:
    from PIL import Image

    w, h = (int(x) for x in os.environ.get("FAKE_CODEX_SIZE", "1086x1448").split("x"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if transparent and not os.environ.get("FAKE_CODEX_NO_ALPHA"):
        im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        im.paste((200, 40, 40, 255), (w // 4, h // 4, 3 * w // 4, 3 * h // 4))
    else:
        im = Image.new("RGB", (w, h), (250, 220, 0))
    im.save(path)


def main() -> int:
    argv = sys.argv[1:]
    if argv == ["--version"]:
        print("codex-cli 0.160.0")
        return 0
    if argv[:2] == ["login", "status"]:
        if SCENARIO == "logged_out":
            print("Not logged in", file=sys.stderr)
            return 1
        print("Logged in using ChatGPT", file=sys.stderr)
        return 0
    if argv[:6] != EXPECTED_FLAGS or len(argv) != 10 or argv[7] != "-o" or argv[9] != "-":
        print(f"fake codex: unexpected argv {argv}", file=sys.stderr)
        return 2
    schema_file, last_file = Path(argv[6]), Path(argv[8])
    json.loads(schema_file.read_text())
    envelope = sys.stdin.read()
    m = re.search(r"<PROMPT>\n(.*)\n</PROMPT>", envelope, re.S)
    prompt = m.group(1) if m else ""
    transparent = "transparent_background" in envelope
    refs_m = re.search(r"referenced_image_paths: (\[.*?\])", envelope)
    refs = json.loads(refs_m.group(1)) if refs_m else []
    n = call_number()
    tid = str(uuid.uuid4())
    log({"event": "start", "t": time.time(), "pid": os.getpid(), "thread_id": tid, "transparent": transparent, "refs": refs, "prompt": prompt, "call": n})

    print(json.dumps({"type": "thread.started", "thread_id": tid}), flush=True)
    print(json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "error", "message": "Codex is ignoring 1 unrecognized configuration setting."}}), flush=True)
    print(json.dumps({"type": "turn.started"}), flush=True)

    if SCENARIO == "exit_nonzero":
        print("fatal: something broke", file=sys.stderr)
        return 1
    if SCENARIO in ("hang", "hang_stubborn"):
        # hang_stubborn: the child ignores SIGTERM while this parent dies of it, so only SIGKILL to the group stops it.
        child = subprocess.Popen(["sh", "-c", "trap '' TERM; while :; do sleep 1; done"] if SCENARIO == "hang_stubborn" else ["sleep", "600"])
        (HOME / "child.pid").write_text(str(child.pid))
        time.sleep(600)
        return 0

    time.sleep(float(os.environ.get("FAKE_CODEX_DELAY", "0.3")))
    rollout_dir = HOME / "sessions" / datetime.date.today().strftime("%Y/%m/%d")
    rollout_dir.mkdir(parents=True, exist_ok=True)
    rollout = rollout_dir / f"rollout-{datetime.datetime.now():%Y-%m-%dT%H-%M-%S}-{tid}.jsonl"
    lines = [json.dumps({"type": "session_meta", "payload": {"id": tid, "cli_version": "0.160.0"}})]

    image = HOME / "generated_images" / tid / f"exec-{uuid.uuid4()}.png"
    passed = prompt
    reported = str(image)
    if SCENARIO in REFUSALS:
        lines += [failure_line(e) for e in REFUSALS[SCENARIO]]
        reported = ""
    elif SCENARIO == "path_outside":
        image = HOME / "elsewhere" / "stray.png"
        write_png(image, transparent)
        reported = str(image)
    else:
        write_png(image, transparent)
        if SCENARIO == "path_fallback":
            reported = ""
        if SCENARIO == "normalized":
            passed = prompt.replace(" ", "  ", 1)
        if SCENARIO == "rewrite_twice" or (SCENARIO == "rewrite_ok" and n == 1):
            passed = "Use case: poster\nPrimary request: " + prompt
    rollout.write_text("\n".join(lines) + "\n")

    final = {"prompt_passed": passed, "image_path": reported}
    if SCENARIO == "unverified":
        final = {"image_path": reported}
    text = json.dumps(final, ensure_ascii=False)
    last_file.write_text("not json at all" if SCENARIO == "bad_json" else text)
    print(json.dumps({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": text}}), flush=True)
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}), flush=True)
    log({"event": "end", "t": time.time(), "pid": os.getpid(), "thread_id": tid})
    return 0


if __name__ == "__main__":
    sys.exit(main())
