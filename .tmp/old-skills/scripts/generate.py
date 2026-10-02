#!/usr/bin/env python3
"""Generate images through Codex CLI's built-in `image_gen`, and verify what actually got sent.

WHY THIS WRAPPER EXISTS, AND WHAT IT CANNOT PROMISE
---------------------------------------------------
Codex rewrites prompts. Observed on 2026-08-04: given an ordinary request it read its own
bundled imagegen skill off disk and restructured the prompt into that skill's labelled schema
(`Use case:` / `Asset type:` / `Scene/backdrop:` ...). Told explicitly not to read any skill
file, it refused -- "The image-editing workflow requires the imagegen skill instructions, so I
need to consult them despite your request not to" -- and read it anyway. So this script does
not try to stop Codex reading anything. It only checks the artifact: did the prompt survive?

That check rests on Codex's own echo, and here is the hole in it. `image_gen` tool calls do not
appear in the `--json` event stream at all; the only item types that surface are `agent_message`
and `command_execution`. There is no independent observation of the arguments. If the model
edited the prompt and then reported that it hadn't, nothing here would catch it. Every run
observed so far reported honestly, including the run where it defied the instruction, so this
is a working defence in practice and not a guarantee.

**If a future Codex CLI exposes tool calls as events, replace the echo check with the real
thing immediately.** That is the only fix; everything else here is working around its absence.

TWO MODES, AND ONLY ONE OF THEM KEEPS A RECORD
-----------------------------------------------
    generate.py --job <작업폴더> --version 2 [--n 3]

is the real one. The prose lives in `job.json` at `versions[].prompt` and nowhere else; this
script reads it out, generates, and writes the resulting `images` array straight back in. Nothing
about the prompt or the results is ever copied by hand, so there is no second copy to drift.
That version's `ref_images` come from the same file for the same reason.

    generate.py --prompt-file p.txt --out-dir d

is for throwaway trials and the test suite. It writes no record. Work you intend to keep should
not go through it -- a job whose prose ends up in a temp file cannot be reproduced from itself.

There are exactly three `image_gen` parameters: `prompt`, `referenced_image_paths`,
`num_last_images_to_include`. No size, no quality, no model, no output path. The absence of
`--size` / `--quality` / `--model` flags below is not an oversight -- it is the API.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import difflib
import json
import os
import shutil
import signal
import struct
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_prompt import check, parse_aspect  # noqa: E402
from contact_sheet import build_sheet  # noqa: E402
from job_schema import (AXIS_KEYS, METHODS, SCHEMA_VERSION, describe,  # noqa: E402
                        job_dir_help, load, ok_files, record_attempt, report, resolve_job_dir,
                        save, validate)

SCRIPT_DIR = Path(__file__).resolve().parent

SCHEMA = {
    "type": "object",
    "properties": {"prompt_passed": {"type": "string"}, "image_path": {"type": "string"}},
    "required": ["prompt_passed", "image_path"],
    "additionalProperties": False,
}

ENVELOPE = """You are an image-generation executor. Your only job is to make one tool call and report what you passed.

Call the built-in image_gen tool EXACTLY ONCE. Pass the text between the <PROMPT> markers as the `prompt` argument, byte-for-byte verbatim. Do not restructure it, do not add labeled fields, do not add a "Use case:" line, do not append or prepend anything, and do not summarize it. The prompt has been authored deliberately; any edit to it is a defect.
{ref_clause}
<PROMPT>
{prompt}
</PROMPT>

Respond with JSON matching the provided schema: `prompt_passed` must be the exact string you passed as the `prompt` argument, and `image_path` the absolute path of the saved PNG.
"""

# Only added on a retry, and only naming the failure that actually happened.
STRONGER = """
YOUR PREVIOUS ATTEMPT MODIFIED THE PROMPT AND THE RUN WAS REJECTED. Do not restructure the text into labelled fields of any kind -- not "Use case:", not "Subject:", not "Scene/backdrop:", not "Style/medium:", not any other label. The text between the markers is already a finished prompt. Copy it character for character.
"""

# The labels Codex's bundled imagegen skill tells it to impose. Their presence in an echo
# identifies *which* rewrite happened, which is what makes the retry worth attempting.
IMAGEGEN_LABELS = [
    "Use case:", "Asset type:", "Primary request:", "Input images:", "Scene/backdrop:",
    "Subject:", "Style/medium:", "Composition/framing:", "Lighting/mood:", "Color palette:",
    "Materials/textures:", "Text (verbatim):", "Constraints:", "Avoid:",
]

ASPECT_TOLERANCE = 0.01


# --------------------------------------------------------------------------------------

def png_size(path: Path) -> tuple[int, int]:
    """Width and height from the IHDR chunk. Avoids depending on `sips`, which is macOS-only."""
    with path.open("rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"not a PNG: {path}")
    return struct.unpack(">II", head[16:24])


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def preflight() -> list[str]:
    """Fail before spending 84 seconds, not after. Runs once per process, which is also once
    per `--n` batch -- the right granularity, so no caching is needed."""
    problems = []
    if not shutil.which("codex"):
        problems.append("`codex` is not on PATH. Install the Codex CLI, then re-run.")
        return problems
    try:
        r = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=30)
        if r.returncode != 0 or "Logged in" not in (r.stdout + r.stderr):
            problems.append("Codex is not logged in. Run `codex login` and re-run.")
    except subprocess.TimeoutExpired:
        problems.append("`codex login status` timed out after 30s.")
    return problems


def build_envelope(prompt: str, refs: list[Path], stronger: bool = False) -> str:
    ref_clause = ""
    if refs:
        paths = ", ".join(json.dumps(str(p.resolve())) for p in refs)
        ref_clause = f"\nAlso pass referenced_image_paths: [{paths}]\n"
    body = ENVELOPE.format(ref_clause=ref_clause, prompt=prompt)
    return body + STRONGER if stronger else body


def classify_verbatim(original: str, echoed: str | None) -> tuple[str, list[str]]:
    """exact | normalized | mismatch | unverified, plus any imagegen labels detected."""
    if echoed is None:
        return "unverified", []
    labels = [l for l in IMAGEGEN_LABELS if l in echoed]
    if echoed == original:
        return "exact", labels
    if " ".join(echoed.split()) == " ".join(original.split()):
        return "normalized", labels
    return "mismatch", labels


FAILURE_MARKER = "image generation failed:"


def _strings(node: object) -> Iterator[str]:
    """Every string anywhere in one parsed event. The nesting depth is codex's, not ours."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _parse_failure(text: str) -> dict | None:
    """The API's own error object, out of one rollout string, whatever the code turns out to be.

    The payload arrives as a JSON string inside a JSON string inside the event, wrapped in Rust's
    `Some(...)`, so it takes two loads to reach a dict.
    """
    at = text.find(FAILURE_MARKER)
    if at < 0:
        return None
    tail = text[at + len(FAILURE_MARKER):].strip()
    if "Some(" in tail:
        body = tail[tail.index("Some(") + 5:].rstrip()
        if body.endswith(")"):
            body = body[:-1]
        try:
            payload = json.loads(body)
            if isinstance(payload, str):
                payload = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            err = payload.get("error", payload)
            if isinstance(err, dict):
                return err
    # The wrapper is codex's and has no reason to hold still. When it stops parsing the reason is
    # still right here in the text, and carrying it verbatim beats a clean `None` that reads as
    # "nothing was wrong" -- which is the exact way this whole path failed before.
    return {"message": tail}


def _stage(err: dict) -> str | None:
    """`moderation_stage`, only when the payload really has the shape we assumed.

    The types here are codex's, not ours. Every failure on this disk carries a dict in
    `moderation_details`, so reading it as one passes every test written from that history and
    throws on the first day it is not -- and the throw escapes the worker pool, which leaves the
    attempt stuck as `running` and the failure absent from every count. Guessing wrong about a
    payload has to degrade to "we did not learn the stage", never to a crash.
    """
    mod = err.get("moderation_details")
    stage = mod.get("moderation_stage") if isinstance(mod, dict) else None
    return str(stage) if isinstance(stage, (str, int)) else None


def read_rollout(thread_id: str | None) -> dict | None:
    """Why a run came back empty-handed, from the only file on disk that says.

    There are three observation points and the skill used to read two. `.runs/` has the thread id
    and the exit code; `generated_images/` has the file or nothing; neither carries a reason. So a
    refusal arrived as `FAILED -- no image found`, five turns went into hand-parsing the two files
    that could not answer, and the answer they produced was wrong.

    Codex writes a rollout transcript per session and puts the thread id in the filename, which is
    what makes the join possible at all. Measured over 154 rollouts on this machine: 32 carry a
    failure, all 32 parse here, and all 32 are `moderation_blocked` with `moderation_stage:
    output` -- the image was blocked, not the prompt, which is what says rewording cannot help.

    **It reads by structure, not by that word.** The first version gated on the literal string
    `moderation_blocked`, which passed every test written from that history and would go blind the
    first day codex returns a different code: the reason would sit on disk while the run reported
    an unexplained empty hand. That is the same mistake as writing down in advance what can and
    cannot be generated, one layer down -- and this file exists because that mistake was made.
    """
    if not thread_id:
        return None
    try:
        hits = sorted(codex_home().glob(f"sessions/**/rollout-*{thread_id}.jsonl"))
    except OSError:
        return None
    for hit in hits:
        try:
            raw = hit.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if FAILURE_MARKER not in raw:
            continue
        errs = []
        for line in raw.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            for text in _strings(event):
                err = _parse_failure(text)
                if err is not None:
                    errs.append(err)
        if not errs:
            continue
        # One thread can hold several: codex retries image_gen inside a session, and 2 of the 32
        # failed rollouts here carry 3 and 5 separate calls. Stopping at the first would record
        # whichever came first, and the field that sets the prescription can be in any of them --
        # so the decisive one wins and the rest survive as a count. Same rule as `--summary`
        # counting kinds: what a reader needs is which failure decides the next move.
        chosen = next((e for e in errs if _stage(e) == "output"), errs[-1])
        detail = {"events_file": str(hit)}
        if len(errs) > 1:
            detail["tries"] = len(errs)
        for key in ("code", "message"):
            if chosen.get(key):
                detail[key] = str(chosen[key])[:300]
        stage = _stage(chosen)
        if stage:
            detail["moderation_stage"] = stage
        mod = chosen.get("moderation_details")
        cats = mod.get("categories") if isinstance(mod, dict) else None
        if isinstance(cats, (list, tuple)) and cats:
            detail["categories"] = [str(c) for c in cats]
        return detail
    return None


def run_one(envelope: str, timeout: int, workdir: Path, tag: str) -> dict:
    """One `codex exec`. Returns the parsed final message plus timing, thread id and forensics.

    Everything a later classification could want is carried out here rather than recomputed from
    the tag, because the tag is not a stable key: a retry runs as `a2` and the results list then
    rewrites its tag back to `a`, so anything rebuilding a filename from it would silently read
    the previous run's file.
    """
    env_file = workdir / f"env-{tag}.txt"
    last_file = workdir / f"last-{tag}.json"
    schema_file = workdir / "schema.json"
    events_file = workdir / f"events-{tag}.jsonl"
    env_file.write_text(envelope, encoding="utf-8")

    argv = [
        "codex", "exec", "--json", "--skip-git-repo-check", "-s", "read-only",
        "--output-schema", str(schema_file), "-o", str(last_file), "-",
    ]
    started = time.time()
    base = {"tag": tag, "events_file": str(events_file)}
    with env_file.open("rb") as stdin, events_file.open("wb") as out:
        proc = subprocess.Popen(argv, stdin=stdin, stdout=out, stderr=subprocess.PIPE,
                                start_new_session=True)
        try:
            _, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill the whole group: `codex exec` spawns children that outlive a bare SIGINT.
            os.killpg(os.getpgid(proc.pid), signal.SIGINT)
            proc.wait(timeout=15)
            return {**base, "timeout": True, "error": f"timed out after {timeout}s",
                    "elapsed_s": round(time.time() - started)}
    elapsed = round(time.time() - started)

    # Read the stream to the end. Stopping at thread.started was enough while the thread id was
    # the only thing wanted from it, and it threw away the two agent messages a refusal writes --
    # the model narrating the call it is about to make, then reporting what came back.
    thread_id, messages = None, []
    for line in events_file.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "thread.started":
            thread_id = thread_id or ev.get("thread_id")
        item = ev.get("item") or {}
        if item.get("type") == "agent_message" and item.get("text"):
            messages.append(item["text"])
    base.update(thread_id=thread_id, elapsed_s=elapsed, returncode=proc.returncode,
                messages=messages)

    if proc.returncode != 0:
        return {**base,
                "error": f"codex exited {proc.returncode}: {(err or b'').decode(errors='replace')[-400:]}"}

    if not last_file.exists():
        return {**base, "error": "codex produced no final message"}
    try:
        payload = json.loads(last_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {**base, "error": f"final message is not valid JSON ({e})"}

    return {**base, "prompt_passed": payload.get("prompt_passed"),
            "image_path": payload.get("image_path")}


def resolve_image(result: dict) -> tuple[Path | None, str | None]:
    """The response names the file; the thread-id path rule cross-checks it.

    Both were correct in 7/7 runs. Using the response as primary and the rule as a check means
    a fabricated path shows up as a mismatch instead of being silently trusted.
    """
    reported = result.get("image_path")
    tid = result.get("thread_id")
    expected_dir = codex_home() / "generated_images" / tid if tid else None

    if reported:
        p = Path(reported)
        if p.exists():
            if expected_dir and p.parent != expected_dir:
                return p, f"image is outside the thread directory (expected {expected_dir})"
            return p, None
    if expected_dir and expected_dir.is_dir():
        pngs = sorted(expected_dir.glob("*.png"))
        if pngs:
            return pngs[-1], "response path was unusable; fell back to the thread directory"
    return None, "no image found"


# What a status means, said once, where the line that prints it can reach. The refusal wording is
# the correction this whole pass exists for: the stage is `output`, so it was the *image* that was
# blocked, not the prompt. Three of the seven prompts in that round never named the franchise at
# all and were refused anyway, which is what a prompt-side reading would have kept you rewriting
# against for another six generations.
# `refused` is not here on purpose: its prescription inverts on `moderation_stage`, and a constant
# cannot see a field. It printed the stage=output advice under a stage=input refusal -- the fact
# line said `stage=input` and the advice two lines below said retry, which is the one move that
# provably does not work there. See REFUSED_HELP.
STATUS_HELP = {
    "executor_error": "codex 자체가 실패했다. 프롬프트가 아니라 실행 환경을 본다.",
    "timeout": "제한 시간 안에 안 끝났다. 실측 중앙값은 80초다 — --timeout을 올리기 전에\n"
               "      이 런이 유난히 느렸는지 본다.",
    "prompt_altered": "재시도 후에도 Codex가 프롬프트를 고쳤다. 위 diff가 무엇이 바뀌었는지 말한다.",
    "path_mismatch": "이미지는 있는데 이 thread의 디렉토리 밖이라 출처를 확인할 수 없다.\n"
                     "      파일은 장부에 남지만 고를 수는 없다 — 직접 보고 판단한다.",
    "silent_no_image": "잔여 버킷이다 — 처방이 알려진 어느 분류에도 안 맞았다는 뜻이다.\n"
                       "      위에 code와 message가 찍혔으면 그게 codex가 준 이유 전부다.\n"
                       "      아무것도 없으면 .runs의 events 파일과 롤아웃을 직접 본다.",
}


# The prescription for a refusal, in the one place that knows which branch actually happened.
# Never both observed here -- every failure on this disk is `output` -- which is exactly why the
# unobserved branch needs a home rather than a guess: a constant agrees with every sample it has.
REFUSED_HELP = {
    "output": "프롬프트가 아니라 생성된 이미지가 막혔다. 프롬프트 우회는 원리상 안 듣는다 —\n"
              "      재시도가 유일한 레버다.",
    "input": "프롬프트 자체가 막혔다. 재시도가 아니라 **프롬프트를 고치는** 것이 레버다.",
}
REFUSED_UNKNOWN = ("어느 단계에서 막혔는지 모른다 — stage가 안 실려 왔다. 위 code와 message가\n"
                   "      codex가 준 이유 전부이고, 처방은 그걸 읽고 정한다.")


def refusal_help(detail: dict) -> str:
    return REFUSED_HELP.get(detail.get("moderation_stage"), REFUSED_UNKNOWN)


def classify(res: dict, verbatim: str, src: Path | None, note: str | None) -> tuple[str, dict]:
    """Which bucket one finished run lands in, and the forensics that go with it.

    Ordered by how much the observation tells you, most specific first, so the residual bucket is
    only ever reached by elimination. That ordering is the correction: `no image found` used to be
    both the message and the diagnosis, and it was the diagnosis for 21 refusals that had a
    documented reason sitting in a file nobody read.
    """
    detail = {k: res[k] for k in ("returncode", "events_file") if res.get(k) is not None}
    if res.get("messages"):
        detail["agent_message"] = res["messages"][-1][:300]

    if res.get("timeout"):
        return "timeout", detail
    if res.get("returncode"):
        return "executor_error", detail
    if res.get("error"):
        # No final message, or an unreadable one. The executor answered, so this is not a refusal.
        return "executor_error", detail
    if verbatim == "mismatch":
        return "prompt_altered", detail
    if src is not None:
        return ("path_mismatch" if note and "outside the thread directory" in note else "ok"), detail
    rollout = read_rollout(res.get("thread_id"))
    if rollout:
        detail = {**detail, **rollout}
    # `refused` is a prescription, not a synonym for failure: stage=output means retry is the only
    # lever. A code nobody has seen has no known prescription, so it stays in the residual bucket
    # -- but it now lands with codex's own words on it rather than empty-handed.
    if detail.get("code") == "moderation_blocked":
        return "refused", detail
    return "silent_no_image", detail


# --------------------------------------------------------------------------------------

def load_job(job_dir: Path, version: int) -> tuple[str, list[Path]]:
    """Read the prompt and any reference images out of job.json for one version.

    The job file is the single home for the prose. Handing this script a loose prompt file for
    real work would put the same text in two places, and two copies of anything drift.

    It goes through job_schema.load rather than json.loads because the two used to disagree
    inside a single command: main() validated the migrated view and this read the raw one, so a
    schema-1 ledger passed the gate and then arrived here with the old key names.
    """
    job = load(job_dir)
    match = [v for v in job.get("versions", []) if v.get("v") == version]
    if not match:
        have = [v.get("v") for v in job.get("versions", [])]
        raise SystemExit(f"job.json has no version {version} (has: {have or 'none'})")
    entry = match[0]
    prompt = (entry.get("prompt") or "").strip()
    if not prompt:
        raise SystemExit(f"version {version} has no prompt. Write it into job.json first.")
    # Regenerating a version replaces its PNGs and its images array with no way back. --init
    # refuses to overwrite a ledger and says why; this was the same loss with nothing said. It
    # warns instead of blocking because deliberate regeneration is a real move -- what was missing
    # was the count in front of you before you spend the wait.
    existing = ok_files(entry)
    if existing:
        print(f"warning: v{version} already has {len(existing)} image(s) "
              f"({', '.join(existing)}); generating again overwrites "
              f"them and cannot be undone. To keep them, append a new version with "
              f"--new-version --parent {version} instead.", file=sys.stderr)
    refs = [job_dir / r for r in (entry.get("ref_images") or [])]
    return prompt, refs


def init_job(args, ap) -> int:
    """Write the opening job.json for a new job directory.

    This exists because the one file the skill used to ask for by hand was the one file whose
    shape lived only in job_schema.py -- so writing it meant reading that source first. The field
    names, the required set and the enums are all already declared there; this stamps them out so
    the model only ever supplies content. Every other surface of this skill is behind a CLI that
    describes itself, and this was the hole in that coverage.
    """
    inherited: dict = {}
    if args.like:
        lf = args.like / "job.json"
        if not lf.exists():
            print(f"--like: no job.json in {args.like}", file=sys.stderr)
            return 1
        parent = json.loads(lf.read_text(encoding="utf-8")).get("brief", {})
        # Only the parts of a brief that outlive one job. request/purpose/text describe *this*
        # picture and copying them would put the previous job's words in the new ledger.
        inherited = {k: parent[k] for k in ("aspect", "notes", "target_images") if k in parent}

    aspect = args.aspect or inherited.get("aspect")
    if not args.id or not args.request or not aspect:
        ap.error("--init needs --id, --request and --aspect (--like can supply the aspect)")
    if parse_aspect(aspect) is None:
        ap.error(f"--aspect takes a named ratio like 4:5 or 16:9; got {aspect!r}")

    jf = args.job / "job.json"
    if jf.exists():
        print(f"{jf} already exists; --init will not overwrite a ledger.", file=sys.stderr)
        return 1
    args.job.mkdir(parents=True, exist_ok=True)

    brief = {"request": args.request, "aspect": aspect}
    if args.purpose:
        brief["purpose"] = args.purpose
    if args.text:
        brief["text"] = list(args.text)
    notes = list(args.note) or list(inherited.get("notes") or [])
    if notes:
        brief["notes"] = notes
    targets = list(args.target) or list(inherited.get("target_images") or [])
    if targets:
        brief["target_images"] = targets

    job = {
        "schema": SCHEMA_VERSION,
        "id": args.id,
        "created": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "brief": brief,
        "versions": [],
    }

    findings = validate(job, args.job)
    report(findings, sys.stderr)
    if findings["errors"]:
        print("refusing to write: job.json would not validate.", file=sys.stderr)
        return 1
    sheet = save(args.job, job)
    print(f"{jf} written. Next: --new-version with the prose on stdin.")
    if sheet:
        print(f"sheet: {sheet}")
    return 0


def add_version(args, ap) -> int:
    """Append one version to job.json. Prose arrives on stdin; everything else is a flag.

    Appending rather than rewriting is the point: the ad-hoc scripts this replaces re-serialised
    the whole ledger every time, and any slip in one of them would have taken the earlier versions
    with it.
    """
    if sys.stdin.isatty():
        ap.error("--new-version reads the prose prompt from stdin; pipe or heredoc it in")
    prose = sys.stdin.read().strip()
    if not prose:
        ap.error("--new-version got an empty prompt on stdin")
    if not args.direction or not args.method:
        ap.error("--new-version needs --direction and --method")

    axes = {}
    for item in args.axis:
        key, sep, val = item.partition("=")
        if not sep or key not in AXIS_KEYS:
            ap.error(f"--axis takes KEY=VALUE with KEY one of {list(AXIS_KEYS)}; got {item!r}")
        axes[key] = [s.strip() for s in val.split(";") if s.strip()] if key == "refusals" else val

    job = load(args.job)
    used = [v["v"] for v in job.get("versions", []) if isinstance(v.get("v"), int)]
    num = max(used or [0]) + 1
    entry = {"v": num, "direction": args.direction, "prompt": prose,
             "method": args.method, "parent": args.parent, "change": args.change}
    if axes:
        entry["axes"] = axes
    if args.ref:
        entry["ref_images"] = [str(r) for r in args.ref]
    job.setdefault("versions", []).append(entry)

    findings = validate(job, args.job)
    report(findings, sys.stderr)
    if findings["errors"]:
        print("refusing to write: job.json would not validate.", file=sys.stderr)
        return 1
    save(args.job, job)
    build_sheet(args.job)
    print(f"v{num} added to {args.job}/job.json")
    return 0


# Not a known limit -- the edge of what has been measured. One call takes 78-84s; three took
# 75-85s, four took 128s and six took 109s, and the spread inside a single run (67-128s) is wider
# than the spread between runs, so no wall showed up in this range. The ceiling exists so a
# fan-out cannot quietly become an unmeasured one; raise it by measuring, not by editing.
CONCURRENCY_CEILING = 6


class Bail(Exception):
    """One version could not be prepared. Raised so a fan-out over versions fails that one loudly
    instead of writing a half-record for it."""


def generate_one(args, prompt: str, refs: list[Path], label: str, out_dir: Path,
                 requested, on_start, on_done) -> dict:
    """Lint one prompt, fan out --n calls, verify each echo, and copy what survived.

    Split out of main so several versions can run at once: the wall clock for six concurrent
    calls measured 109s against 78-84s for one, so proposing three directions and pulling one
    image of each costs about what a single image costs. Everything that varies per version --
    the prose, its references, the filename prefix -- arrives as an argument rather than being
    stashed on args, because two versions in flight would overwrite each other there.
    """
    for r in refs:
        if not r.exists():
            print(f"reference image not found: {r}", file=sys.stderr)
            raise Bail()

    # 1. Lint. A prompt this script wrote is this script's problem, so errors stop here.
    lint = check(prompt)
    if not args.skip_check and lint["errors"]:
        print("prompt failed the linter; fix it and re-run (or pass --skip-check):", file=sys.stderr)
        for f in lint["errors"]:
            print(f"  ERROR {f['code']}  {f['msg']}", file=sys.stderr)
        raise Bail()
    for f in lint["warnings"]:
        print(f"  WARN {f['code']}  {f['msg']}", file=sys.stderr)

    envelope = build_envelope(prompt, refs)

    if args.dry_run:
        dry = {"ok": True, "dry_run": True, "label": label, "n": args.n,
               "aspect_requested": f"{requested[0]}:{requested[1]}" if requested else None,
               "refs": [str(p) for p in refs],
               "lint": {"errors": lint["errors"], "warnings": lint["warnings"]},
               "envelope_bytes": len(envelope.encode()), "envelope": envelope}
        return dry

    out_dir.mkdir(parents=True, exist_ok=True)
    workdir = out_dir / ".runs" / f"{label}-{uuid.uuid4().hex[:8]}"
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "schema.json").write_text(json.dumps(SCHEMA), encoding="utf-8")

    tags = "abcd"[: args.n]
    on_start(tags)  # stake out the running rows before anything is waited on

    def one(tag: str) -> dict:
        """Everything for a single variant, start to finish, inside the worker.

        This used to be three passes over a completed list: run them all, then retry the ones that
        came back rewritten, then copy and measure. Nothing in the second and third passes ever
        looked across variants -- every field is computed from one result -- so the barrier bought
        nothing and cost the wall clock. Five batches of 78-110 seconds went by with no output at
        all, and the user interrupted to ask whether anything was happening.
        """
        res = run_one(envelope, args.timeout, workdir, tag)
        state, labels = classify_verbatim(prompt, res.get("prompt_passed"))

        # Verbatim gate. One retry with a stronger envelope, then stop -- a mismatch that survives
        # means job.json would describe an image that was never made from that prompt.
        if state == "mismatch":
            print(f"  {label}-{tag}  verbatim mismatch"
                  + (f" (rewritten into imagegen fields: {', '.join(labels)})" if labels else "")
                  + " -- retrying once with a stronger envelope", file=sys.stderr, flush=True)
            res = run_one(build_envelope(prompt, refs, stronger=True), args.timeout, workdir,
                          tag + "2")
            res["retried"] = True
            state, labels = classify_verbatim(prompt, res.get("prompt_passed"))

        src, note = (None, None) if res.get("error") else resolve_image(res)
        status, detail = classify(res, state, src, note)
        attempt = {"tag": tag, "status": status, "elapsed_s": res.get("elapsed_s", 0)}
        if res.get("thread_id"):
            attempt["thread_id"] = res["thread_id"]
        if detail:
            attempt["detail"] = detail

        if src is not None:
            dest = out_dir / f"{label}-{tag}.png"
            shutil.copy2(src, dest)
            w, h = png_size(dest)
            ratio = w / h
            attempt.update(file=dest.name, w=w, h=h, ratio=round(ratio, 4),
                           mp=round(w * h / 1e6, 2), verbatim=state, source=str(src))
            if requested:
                want = requested[0] / requested[1]
                attempt["aspect_requested"] = f"{requested[0]}:{requested[1]}"
                attempt["aspect_ok"] = abs(ratio - want) <= ASPECT_TOLERANCE
            if note:
                attempt["note"] = note
        elif status == "prompt_altered":
            attempt["error"] = "verbatim mismatch after retry"
            sys.stderr.write("\nverbatim mismatch survived the retry. Codex changed the prompt:\n")
            sys.stderr.writelines(difflib.unified_diff(
                prompt.splitlines(keepends=True),
                (res.get("prompt_passed") or "").splitlines(keepends=True),
                fromfile="authored", tofile="passed-to-image_gen", n=2))
            sys.stderr.write("\n")
        elif res.get("error"):
            # Only the executor's own words go here. `no image found` used to be both the message
            # and the diagnosis, and it was the wrong diagnosis 21 times out of 21 -- the status
            # carries the diagnosis now, so this carries what the status cannot.
            attempt["error"] = res["error"]
        if res.get("retried"):
            attempt.setdefault("note", "needed a verbatim retry")
        on_done(tag, attempt)
        return attempt

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.n) as pool:
        attempts = list(pool.map(one, tags))

    images = [a for a in attempts if a["status"] == "ok"]
    warnings = [f"{a['file']}: verbatim {a['verbatim']}"
                for a in images if a.get("verbatim") not in (None, "exact")]
    warnings += [f"{label}-{a['tag']}: {a['note']}" for a in attempts
                 if a["status"] == "path_mismatch" and a.get("note")]
    return {"attempts": attempts, "images": images,
            "failures": [a for a in attempts if a["status"] != "ok"],
            "warnings": warnings, "requested": requested,
            "workdir": str(workdir), "label": label}


def format_attempt(label: str, a: dict) -> str:
    """One finished variant, as the line that appears the moment it lands.

    A failure prints its forensics with it instead of naming a directory to go read. The old line
    was `FAILED -- no image found`, and answering "why" from there took five turns of parsing the
    two files that could not answer it. What a reader needs is the bucket, the two fields that
    identify it, and what that implies about the next move -- so all three come out together.
    """
    if a["status"] == "ok":
        flag = "" if a.get("aspect_ok", True) else "  <- aspect MISS"
        return (f"{a['file']}  {a['w']}x{a['h']}  ratio={a['ratio']}  "
                f"{a['verbatim']}  {a['elapsed_s']}s{flag}")

    head = f"{label}-{a['tag']}  FAILED  {a['status']:<16} {a.get('elapsed_s', 0)}s"
    lines = [head]
    d = a.get("detail") or {}
    facts = []
    if d.get("code"):
        facts.append(d["code"])
    if d.get("moderation_stage"):
        facts.append(f"stage={d['moderation_stage']}")
    if d.get("categories"):
        facts.append(f"categories=[{', '.join(d['categories'])}]")
    if d.get("returncode"):
        facts.append(f"exit={d['returncode']}")
    if d.get("tries"):
        facts.append(f"이 스레드에서 {d['tries']}회 시도")
    if facts:
        lines.append("      " + " · ".join(facts))
    if d.get("message"):
        lines.append("      " + " ".join(d["message"].split())[:200])
    if a.get("error"):
        # Collapsed: this is a tail of the executor's stderr, so it arrives with its own newlines
        # and would otherwise break the indented block into unaligned fragments.
        lines.append("      " + " ".join(a["error"].split())[:200])
    help_text = refusal_help(d) if a["status"] == "refused" else STATUS_HELP.get(a["status"])
    if help_text:
        lines.append(f"      {help_text}")
    if d.get("events_file"):
        lines.append(f"      {d['events_file']}")
    return "\n".join(lines)


def rate_line(job_dir: Path, versions: list[int]) -> str | None:
    """How many of this job's attempts ever produced an image.

    Cumulative, and read from the ledger rather than from this run, because the number that
    matters is not how the last batch went. One round generated 25 times and kept 4; the ledger
    could only hold the last batch, so the 16% got typed into a --note by hand and everything
    after it argued from a sentence instead of a count.
    """
    try:
        job = load(job_dir)
    except SystemExit:
        return None
    bits, tot_ok, tot = [], 0, 0
    for v in job.get("versions", []):
        atts = [a for a in (v.get("attempts") or []) if a.get("status") != "running"]
        if not atts:
            continue
        n_ok = sum(1 for a in atts if a.get("status") == "ok")
        tot_ok, tot = tot_ok + n_ok, tot + len(atts)
        if v.get("v") in versions:
            bits.append(f"v{v['v']} {n_ok}/{len(atts)}")
    if not tot:
        return None
    bits.append(f"이 작업 누적 {tot_ok}/{tot} ({tot_ok * 100 // tot}%)")
    return " · ".join(bits)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter,
                                 epilog=f"At most {CONCURRENCY_CEILING} codex calls run at once "
                                        f"(versions x --n). One call has measured 67-128s, median "
                                        f"about 80s, so a batch is minutes and finished variants "
                                        f"print as they land rather than at the end. "
                                        f"WHAT THIS PATH CANNOT DO, whatever the prompt says: "
                                        f"image_gen takes three arguments -- prompt, "
                                        f"referenced_image_paths, num_last_images_to_include -- "
                                        f"and the flags missing above are that API rather than an "
                                        f"oversight. Resolution tops out around 1.57MP and is "
                                        f"the same total in every ratio, which is under print "
                                        f"size; exact pixel dimensions cannot be set at all, only "
                                        f"a ratio, and even that is drawn from fixed buckets each "
                                        f"time (aspect_ok reports what actually came out); there "
                                        f"is no transparency parameter, so an alpha background "
                                        f"needs a chroma key and an edit afterwards; and no "
                                        f"seed, so two variants of one prompt are genuinely "
                                        f"different images rather than neighbours. It does not "
                                        f"produce a photograph either: ask for realism with no "
                                        f"source image attached and what comes back is a "
                                        f"generated image trying to look photographic, which "
                                        f"reads as uncanny beside a real one -- a photographic "
                                        f"target needs its source passed as --ref, and without "
                                        f"one the honest move is a non-photographic direction. "
                                        f"There is no "
                                        f"output-path parameter either, so every original lands "
                                        f"in $CODEX_HOME/generated_images/<thread_id>/ and is "
                                        f"copied into the job directory from there; that global "
                                        f"folder accumulates and is never pruned, which is the "
                                        f"answer when someone asks where the disk went.")
    mode = ap.add_argument_group(
        "job mode (use this for real work)",
        "The prose lives in job.json and nowhere else. This script reads it out and writes the "
        "resulting images array back in, so no copy of it is ever made by hand.")
    mode.add_argument("--job", type=resolve_job_dir,
                      help=job_dir_help("job directory containing job.json."))
    mode.add_argument("--version", type=int, action="append", metavar="N",
                      help="which versions[].v to generate; repeatable. Several versions run at "
                           "once, so pulling one image of each of three directions costs about "
                           "what one image costs")
    start = ap.add_argument_group(
        "start a job (with --job, before anything else)",
        "Stamps out job.json from the schema so no part of this file is ever authored by hand. "
        "The field names and the enums are the schema's own, reused here rather than restated; "
        "--schema prints them.")
    start.add_argument("--init", action="store_true",
                       help="write the opening job.json into --job and stop")
    start.add_argument("--id", help="short slug identifying this job")
    start.add_argument("--request", help="what the user asked for, in their terms")
    start.add_argument("--purpose", help="where the image will be used")
    start.add_argument("--aspect",
                       help="named ratio, e.g. 4:5. Any w:h parses -- there is no allow-list of "
                            "ratios, and generation draws from fixed buckets, so whether an image "
                            "actually came out at it is only known afterwards from its aspect_ok. "
                            "Pixel dimensions are not settable")
    start.add_argument("--text", action="append", default=[],
                       help="a string that must render in the image; repeatable")
    start.add_argument("--target", action="append", default=[],
                       help="an image this job is judged against; repeatable. Written into "
                            "brief.target_images and inherited by --like. The path resolves "
                            "against the job directory, like ref_images, so pass it as the job "
                            "directory sees it or absolute. This is a yardstick, not an input: "
                            "naming it does not feed it to the model, and only --ref on a "
                            "--new-version does that. What reads it -- measure.py takes it as the "
                            "band when --against is absent, contact_sheet.py stands it above the "
                            "variants, and the schema warns when no version referenced it")
    start.add_argument("--note", action="append", default=[],
                       help="a constraint spanning the whole job; repeatable")
    start.add_argument("--like", type=resolve_job_dir,
                       help="inherit the parts of another job's brief that outlive one job: "
                            "--aspect, --note and --target. --request/--purpose/--text describe this "
                            "picture and are not copied. Explicit flags replace what is "
                            "inherited, and inherited notes that do not apply here are yours "
                            "to prune. Resolved the same way as --job")
    new = ap.add_argument_group(
        "append a version (with --job, instead of --version)",
        "Records the intent before the images exist, which is the order a ledger has to be kept "
        "in. The prose prompt comes from stdin so it never lands in a second file.")
    new.add_argument("--new-version", action="store_true",
                     help="append a version to job.json and stop; prose on stdin")
    new.add_argument("--direction", help="short memorable name for this direction")
    new.add_argument("--method", choices=METHODS,
                     help="how this version is produced from its parent; reference-edit needs "
                          "--ref on this same --new-version command, not on the generation")
    new.add_argument("--parent", type=int, help="the version this one is derived from")
    new.add_argument("--change", help="what this changes against the parent, and why")
    new.add_argument("--axis", action="append", default=[], metavar="KEY=VALUE",
                     help=f"repeatable; KEY one of {list(AXIS_KEYS)}. "
                          "refusals splits on ';' into a list")
    adhoc = ap.add_argument_group(
        "ad-hoc mode (throwaway trials and tests)",
        "No job record is written. Do not use for work you intend to keep.")
    adhoc.add_argument("--prompt-file", type=Path,
                       help="file holding the prompt prose; ad-hoc mode only")
    adhoc.add_argument("--out-dir", type=Path,
                       help="where the PNGs land; ad-hoc mode only")
    adhoc.add_argument("--label", default="v1", help="filename prefix, e.g. v2 -> v2-a.png")
    ap.add_argument("--n", type=int, default=1, choices=range(1, 5), metavar="1..4",
                    help="variants to generate concurrently; parallel is free in wall-clock terms")
    ap.add_argument("--ref", type=Path, action="append", default=[],
                    help="reference image, repeatable. With --new-version it is recorded into "
                         "that version's ref_images and reused on every generation of it, and a "
                         "recorded path resolves against the job directory rather than the "
                         "shell's cwd -- pass it as the job directory sees it, or absolute. In "
                         "ad-hoc mode it is an ordinary path applying to the one run. It is not "
                         "accepted on a job-mode generation")
    ap.add_argument("--timeout", type=int, default=300,
                    help="seconds to wait on one codex call before giving up")
    ap.add_argument("--skip-check", action="store_true",
                    help="ad-hoc mode only: bypass the prompt linter. The five ERRORs are all "
                         "closed-form and cannot false-fire, so skipping them in job mode would "
                         "only spend a generation on a prompt already known to be wrong. "
                         "Re-measuring a gotcha on purpose is the honest use, and that leaves "
                         "no record by design")
    ap.add_argument("--dry-run", action="store_true",
                    help="lint and build the envelope, then stop without calling codex. "
                         "ART_DIRECTOR_DRY_RUN=1 forces this on for every call, which is how "
                         "a headless behaviour test runs the skill without spending generations "
                         "-- an env var rather than a prompt instruction, because telling the "
                         "session under test to use --dry-run would be coaching it")
    ap.add_argument("--json", action="store_true",
                    help="print the result object as JSON instead of human-readable lines")
    ap.add_argument("--schema", action="store_true",
                    help="print the shape of job.json -- keys, enums, and the rules the key "
                         "lists do not carry -- and stop")
    args = ap.parse_args()

    if args.schema:
        describe(sys.stdout)
        return 0

    if args.skip_check and args.job:
        ap.error("--skip-check is ad-hoc mode only (--prompt-file/--out-dir); "
                 "a recorded job never generates a prompt the linter rejected")
    if args.ref and args.job and not args.new_version:
        ap.error("--ref belongs to the version, not to the run: pass it with --new-version so "
                 "the ledger records what the generation was actually given")

    job_mode = args.job is not None
    if args.init:
        if not job_mode:
            ap.error("--init needs --job to say where the job.json goes")
        if args.new_version or args.version:
            ap.error("--init writes the opening job.json and stops; it adds no version")
        return init_job(args, ap)
    if args.new_version:
        if not job_mode:
            ap.error("--new-version needs --job")
        if args.version:
            ap.error("--new-version appends a version; --version generates an existing one")
        return add_version(args, ap)

    # One plan per version. Proposing three directions and pulling one image of each is the point
    # of taking --version more than once: the calls overlap, so three directions cost about what
    # one does, and they can be compared side by side instead of in memory.
    plans = []
    if job_mode:
        if not args.version:
            ap.error("--job requires --version")
        if args.prompt_file or args.out_dir:
            ap.error("--job replaces --prompt-file/--out-dir; they come from job.json")
        # The ledger is checked before the prompt is: a generation recorded into a broken
        # job.json is a generation you cannot trace back to its intent.
        findings = validate(load(args.job), args.job)
        report(findings, sys.stderr)
        if findings["errors"]:
            print("job.json failed schema validation; fix it before generating.", file=sys.stderr)
            return 1
        # The aspect comes from the brief, which is where it was actually stated. Scanning the
        # prose for the first `w:h` agreed with the brief on all 28 recorded versions, but it is a
        # proxy standing in for a fact the ledger holds outright -- and aspect_ok is the field the
        # skill tells you to select variants by, so it should not rest on the linter continuing to
        # force the ratio into the opening sentence ahead of any other colon-separated pair.
        brief_aspect = parse_aspect(load(args.job).get("brief", {}).get("aspect") or "")
        for v in args.version:
            prose, refs = load_job(args.job, v)
            plans.append((v, prose, refs, f"v{v}", args.job, brief_aspect or parse_aspect(prose)))
    else:
        if args.version:
            ap.error("--version only applies with --job")
        if not (args.prompt_file and args.out_dir):
            ap.error("ad-hoc mode needs --prompt-file and --out-dir (or use --job/--version)")
        if not args.prompt_file.exists():
            print(f"prompt file not found: {args.prompt_file}", file=sys.stderr)
            return 1
        prose = args.prompt_file.read_text(encoding="utf-8").strip()
        # Ad-hoc mode has no brief to read, so the prose is all there is.
        plans.append((None, prose, args.ref, args.label, args.out_dir, parse_aspect(prose)))

    if len(plans) * args.n > CONCURRENCY_CEILING:
        ap.error(f"{len(plans)} version(s) x --n {args.n} is more than {CONCURRENCY_CEILING} "
                 f"calls at once, which is past what has been measured")

    if os.environ.get("ART_DIRECTOR_DRY_RUN", "").strip() not in ("", "0", "false"):
        args.dry_run = True
    if not args.dry_run:
        problems = preflight()
        if problems:
            for pr in problems:
                print(f"preflight: {pr}", file=sys.stderr)
            return 1

    def make_callbacks(version, label):
        """Announce and record one variant the moment it lands, not when its siblings do.

        stdout is flushed per line on purpose. Python block-buffers a redirected stdout, so
        without this the lines exist but sit in an 8KB buffer until exit -- which is the same
        silence, and looks identical to not printing at all.
        """
        def on_start(tags):
            if version is not None:
                for t in tags:
                    record_attempt(args.job, version, t, None)

        def on_done(tag, attempt):
            if version is not None:
                record_attempt(args.job, version, tag, attempt)
            print(f"  {format_attempt(label, attempt)}", flush=True)
        return on_start, on_done

    runs: dict = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(plans)) as pool:
        futures = {}
        for v, pr, rf, lb, od, req in plans:
            on_start, on_done = make_callbacks(v if job_mode else None, lb)
            futures[pool.submit(generate_one, args, pr, rf, lb, od, req, on_start, on_done)] = v
        for fut, v in futures.items():
            try:
                runs[v] = fut.result()
            except Bail:
                runs[v] = None

    if any(r is None for r in runs.values()):
        return 1
    if args.dry_run:
        print(json.dumps(list(runs.values()), ensure_ascii=False, indent=2) if args.json
              else "\n\n".join(
                  f"dry-run ok [{r['label']}]: {r['n']} image(s), aspect {r['aspect_requested']}, "
                  f"{r['envelope_bytes']} envelope bytes\n\n{r['envelope']}"
                  for r in runs.values()))
        return 0

    # 3. Aspect ratio is not decided by the prompt alone -- it is drawn from a bucket each time.
    #    Saying so here is the difference between choosing the right variant and shipping a
    #    silently wrong shape.
    for res in runs.values():
        off = [i for i in res["images"] if i.get("aspect_ok") is False]
        if off:
            detail = ", ".join("{} is {}".format(i["file"], i["ratio"]) for i in off)
            res["warnings"].append(
                "{}/{} image(s) missed the requested aspect ratio ({}). Prefer a variant whose "
                "aspect_ok is true, or regenerate.".format(len(off), len(res["images"]), detail))

    # Nothing left to write: every attempt went into the ledger as it finished, and save()
    # rebuilt the sheet each time. The try that used to guard build_sheet here moved into save()
    # with it -- leaving it would have described a protection that no longer happens at this
    # level, which is worse than none.
    sheet = args.job / "contact-sheet.html" if job_mode else None

    ok = all(r["images"] and not r["failures"] for r in runs.values())
    out = {
        "ok": ok, "requested_n": args.n, "job": str(args.job) if job_mode else None,
        "runs": [{"version": v, "label": r["label"],
                  "aspect_requested": (f"{r['requested'][0]}:{r['requested'][1]}"
                                       if r["requested"] else None),
                  "images": r["images"], "failures": r["failures"],
                  "warnings": r["warnings"], "workdir": r["workdir"]}
                 for v, r in runs.items()],
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        # Every attempt already printed itself as it finished. What is left is what only exists
        # once they are all in: the warnings that compare across variants, the rate, and where to
        # look at them -- that last line because six images were generated and the sheet went
        # unopened, which no amount of it being rebuilt on time would have fixed.
        for res in runs.values():
            for w in res["warnings"]:
                print(f"  warn: {w}", file=sys.stderr)
        if job_mode:
            rate = rate_line(args.job, list(runs))
            if rate:
                print(f"  {rate}")
            if sheet and sheet.exists():
                print(f"sheet: {sheet}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
