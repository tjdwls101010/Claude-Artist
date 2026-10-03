"""The job ledger, job.json: the only place a job's prompts, attempts, judgments and final live.

Every write goes through `edit` (or `create`), which holds the job's lock, checks the whole document against schema 3 and replaces the file atomically. Reading closes attempts left `running` by a process that has died, as `interrupted`.

An attempt is one try at producing an image. Its id and file name, `v{N}-{k}`, count every try of the version, so no run ever reuses a file name. A running attempt changes to its result exactly once; a finished one never changes.
"""

from __future__ import annotations

import os
from pathlib import Path

from imagen.errors import Failure

from .legacy import inherit
from .schema import SCHEMA, ok_files, validate
from .store import create, edit, exists, now, read

__all__ = ["SCHEMA", "create", "edit", "exists", "read", "now", "validate", "ok_files", "inherit", "version", "add_version", "new_attempt", "start_attempt", "finish_attempt", "find_file"]


def version(job: dict, v: int) -> dict:
    for entry in job["versions"]:
        if entry["v"] == v:
            return entry
    raise Failure(f"no version {v} in this job (has: {[e['v'] for e in job['versions']] or 'none'})")


def add_version(job: dict, fields: dict) -> dict:
    """Append a version with the next number; `fields` holds everything but v, created and attempts."""
    entry = {"v": max((e["v"] for e in job["versions"]), default=0) + 1, **fields, "created": now(), "attempts": []}
    if entry.get("parent") is not None:
        version(job, entry["parent"])
    job["versions"].append(entry)
    return entry


def new_attempt(entry: dict) -> dict:
    """Open a running attempt on a version held inside `edit`. Returns the row; its file name is `row['id'] + '.png'`."""
    k = len(entry["attempts"]) + 1
    row = {"id": f"v{entry['v']}-{k}", "status": "running", "started": now(), "pid": os.getpid()}
    entry["attempts"].append(row)
    return row


def start_attempt(job_dir: Path, v: int) -> dict:
    with edit(job_dir) as job:
        return dict(new_attempt(version(job, v)))


def finish_attempt(job_dir: Path, v: int, attempt_id: str, fields: dict) -> dict:
    """Turn a running attempt into its result. `fields` must include the final `status`."""
    with edit(job_dir) as job:
        for row in version(job, v)["attempts"]:
            if row["id"] == attempt_id:
                if row["status"] != "running":
                    raise Failure(f"attempt {attempt_id} already finished as {row['status']}")
                row.update(fields)
                return dict(row)
    raise Failure(f"no attempt {attempt_id} in version {v}")


def find_file(job: dict, name: str) -> tuple[dict, dict]:
    """The version and attempt that produced a file, by its name as the ledger lists it (a path to it works too)."""
    base = Path(name).name
    for entry in job["versions"]:
        for row in entry["attempts"]:
            if row.get("file") == base:
                return entry, row
    raise Failure(f"{name!r} is not an image of this job")
