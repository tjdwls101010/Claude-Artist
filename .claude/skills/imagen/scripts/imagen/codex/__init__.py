"""The Codex CLI as an image generator: one `codex exec` per image through its built-in image_gen tool.

What this unit hands back is a `Run` in the skill's terms: the image file if one was made and whether it came from this run's thread folder, what Codex reports it passed as the prompt, and, when there is no image, whether moderation blocked it and at which stage. Codex's event shapes, file layout and error encoding stay inside. `prompt_warnings` checks, before generating, what image_gen needs from a prompt.

image_gen takes a prompt, optional referenced_image_paths and an optional transparent_background, and nothing else: no size, quality, mask, seed or output path. Its tool call does not appear in the event stream, so `prompt_passed` is Codex's own report, not an observation.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import envelope, rollout
from .prompt import warnings as prompt_warnings

__all__ = ["Run", "run", "executable", "version", "login_status", "home", "prompt_warnings"]


@dataclass
class Run:
    elapsed_s: float
    events_file: str
    thread_id: str | None = None
    returncode: int | None = None
    timed_out: bool = False
    error: str | None = None  # Codex itself failed: non-zero exit, no final message, unreadable final message
    prompt_passed: str | None = None
    image: Path | None = None
    image_in_thread: bool = True  # False: the reported file lies outside this thread's folder, so its origin is unverified
    agent_message: str | None = None
    refused: bool = False  # moderation blocked the image
    failure: dict = field(default_factory=dict)  # code, message, moderation_stage, categories, tries, events_file


def home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def executable() -> str | None:
    return shutil.which("codex")


def version() -> str | None:
    try:
        r = subprocess.run(["codex", "--version"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


def login_status() -> tuple[bool, str]:
    try:
        r = subprocess.run(["codex", "login", "status"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"codex login status failed: {exc}"
    text = (r.stdout + r.stderr).strip()
    return r.returncode == 0 and "Logged in" in text, text


def _stop(proc: subprocess.Popen) -> None:
    """codex exec spawns children that can outlive the parent, so the whole group gets SIGTERM, a grace period, then SIGKILL whether or not the parent already exited. The group id is the parent's pid (it started its own session)."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            break
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        if sig == signal.SIGTERM:
            time.sleep(0.5)  # give children that do honour SIGTERM a moment before the kill
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _resolve_image(reported: str | None, thread_id: str | None) -> tuple[Path | None, bool]:
    expected = home() / "generated_images" / thread_id if thread_id else None
    if reported:
        p = Path(reported)
        if p.is_file():
            return p, bool(expected) and p.resolve().parent == expected.resolve()
    if expected and expected.is_dir():
        pngs = sorted(expected.glob("*.png"), key=lambda q: q.stat().st_mtime)
        if pngs:
            return pngs[-1], True
    return None, True


def run(prompt: str, *, refs: list[Path], transparent: bool, stronger: bool, workdir: Path, tag: str, timeout: int) -> Run:
    """One `codex exec`. `workdir` receives the envelope, the event stream and the last message, named by `tag`."""
    workdir.mkdir(parents=True, exist_ok=True)
    schema_file = workdir / "schema.json"
    if not schema_file.exists():
        schema_file.write_text(json.dumps(envelope.SCHEMA), encoding="utf-8")
    env_file = workdir / f"envelope-{tag}.txt"
    last_file = workdir / f"last-{tag}.json"
    events_file = workdir / f"events-{tag}.jsonl"
    env_file.write_text(envelope.build(prompt, refs, transparent, stronger), encoding="utf-8")
    argv = ["codex", "exec", "--json", "--skip-git-repo-check", "-s", "read-only", "--output-schema", str(schema_file), "-o", str(last_file), "-"]
    started = time.monotonic()
    result = Run(elapsed_s=0, events_file=str(events_file))
    with env_file.open("rb") as stdin, events_file.open("wb") as out:
        try:
            proc = subprocess.Popen(argv, stdin=stdin, stdout=out, stderr=subprocess.PIPE, cwd=workdir, start_new_session=True)
        except OSError as exc:
            result.error = f"could not start codex: {exc}"
            return result
        try:
            _, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _stop(proc)
            err = b""
            result.timed_out = True
    result.elapsed_s = round(time.monotonic() - started, 1)
    result.returncode = proc.returncode

    messages = []
    for line in events_file.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            result.thread_id = result.thread_id or event.get("thread_id")
        item = event.get("item") or {}
        if item.get("type") == "agent_message" and item.get("text"):
            messages.append(item["text"])
    if messages:
        result.agent_message = messages[-1][:300]
    if result.timed_out:
        result.error = f"no answer within {timeout}s"
        return result
    if proc.returncode != 0:
        result.error = f"codex exited {proc.returncode}: {(err or b'').decode(errors='replace')[-400:].strip()}"
        return result
    if not last_file.exists():
        result.error = "codex produced no final message"
        return result
    try:
        payload = json.loads(last_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        result.error = f"codex's final message is not valid JSON ({exc})"
        return result
    if not isinstance(payload, dict):
        result.error = "codex's final message is not a JSON object"
        return result
    passed = payload.get("prompt_passed")
    result.prompt_passed = passed if isinstance(passed, str) else None
    result.image, result.image_in_thread = _resolve_image(payload.get("image_path"), result.thread_id)
    if result.image is None:
        detail = rollout.failure(home(), result.thread_id)
        if detail:
            result.failure = detail
            result.refused = detail.get("code") == "moderation_blocked"
    return result
