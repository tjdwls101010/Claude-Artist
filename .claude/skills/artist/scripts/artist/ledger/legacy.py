"""What `init --like` carries over from another job, whichever schema its ledger is in.

Schema 1 and 2 ledgers came from the earlier skill: their brief holds `aspect`, `target_images` (relative to that job's folder) and `notes` as bare strings with no scope. Schema 3 notes carry a scope, and only reusable ones travel.
"""

from __future__ import annotations

import json
from pathlib import Path

from artist.errors import Failure


def inherit(source_dir: Path) -> dict:
    path = source_dir / "job.json"
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise Failure(f"--like: no job.json in {source_dir}") from exc
    except json.JSONDecodeError as exc:
        raise Failure(f"--like: {path} is not valid JSON ({exc})") from exc
    brief = job.get("brief") if isinstance(job, dict) else None
    if not isinstance(brief, dict):
        raise Failure(f"--like: {path} has no brief")
    schema = job.get("schema")

    def absolute(value: str) -> str:
        p = Path(value)
        return str(p if p.is_absolute() else (source_dir / p).resolve())

    if schema in (1, 2):
        return {
            "aspect": brief.get("aspect"),
            "targets": [absolute(t) for t in brief.get("target_images") or [] if isinstance(t, str)],
            "notes": [],
            "legacy_notes": [n for n in brief.get("notes") or [] if isinstance(n, str)],
        }
    if schema == 3:
        return {
            "aspect": brief.get("aspect"),
            "targets": [absolute(t) for t in brief.get("targets") or []],
            "notes": [n for n in brief.get("notes") or [] if isinstance(n, dict) and n.get("scope") == "reusable"],
            "legacy_notes": [],
        }
    raise Failure(f"--like: {path} has schema {schema!r}, which this skill cannot read")
