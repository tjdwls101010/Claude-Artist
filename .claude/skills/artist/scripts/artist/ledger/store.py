"""Reading and writing job.json: one lock per job across processes, a full check before every write, and an atomic rename."""

from __future__ import annotations

import datetime
import fcntl
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path

from artist.errors import Failure

from .schema import SCHEMA, validate

LEDGER = "job.json"
LOCK = "job.json.lock"


def now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def exists(job_dir: Path) -> bool:
    return (job_dir / LEDGER).is_file()


@contextmanager
def _locked(job_dir: Path):
    # flock is held per open file description, so threads of one process exclude each other as well as other processes.
    with open(job_dir / LOCK, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _load(job_dir: Path) -> dict:
    path = job_dir / LEDGER
    if not path.is_file():
        raise Failure(f"no job.json in {job_dir}; start the job with init")
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise Failure(f"{path} is not valid JSON ({exc})") from exc
    if not isinstance(job, dict) or job.get("schema") != SCHEMA:
        raise Failure(f"{path} is a schema {job.get('schema') if isinstance(job, dict) else '?'} ledger; this skill writes schema {SCHEMA}. Start a new job and pass this one as --like to carry over its aspect, targets and notes.")
    return job


def _write(job_dir: Path, job: dict) -> None:
    problems = validate(job)
    if problems:
        raise Failure("refusing to write job.json: it would not validate", problems=problems)
    tmp = job_dir / f".{LEDGER}.{os.getpid()}.{threading.get_ident()}.tmp"
    tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, job_dir / LEDGER)


def _alive(pid: int) -> bool:
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _close_dead(job: dict) -> bool:
    """A running attempt whose process is gone will never finish: close it as interrupted."""
    changed = False
    for version in job.get("versions", []):
        for attempt in version.get("attempts", []):
            if attempt.get("status") == "running" and not _alive(attempt.get("pid", 0)):
                attempt["status"] = "interrupted"
                changed = True
    return changed


def create(job_dir: Path, job: dict) -> None:
    job_dir.mkdir(parents=True, exist_ok=True)
    with _locked(job_dir):
        if exists(job_dir):
            raise Failure(f"{job_dir / LEDGER} already exists; a ledger is never overwritten")
        _write(job_dir, job)


def read(job_dir: Path) -> dict:
    if not exists(job_dir):
        raise Failure(f"no job.json in {job_dir}; start the job with init")
    with _locked(job_dir):
        job = _load(job_dir)
        if _close_dead(job):
            _write(job_dir, job)
        return job


@contextmanager
def edit(job_dir: Path):
    """Read, change and write under the job's lock. Nothing is written if the body raises or the result would not validate."""
    if not exists(job_dir):
        raise Failure(f"no job.json in {job_dir}; start the job with init")
    with _locked(job_dir):
        job = _load(job_dir)
        _close_dead(job)
        yield job
        _write(job_dir, job)
