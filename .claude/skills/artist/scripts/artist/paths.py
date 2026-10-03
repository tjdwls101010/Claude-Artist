"""Where a job lives, and how paths are written into its ledger.

A job is a folder holding job.json. A short name lands under the skill's `data/` (the creatives folder it links to); anything that starts with `/`, `~` or `.` is a path used as written. Names are NFC-normalised because APFS hands back NFD while argv carries NFC, and the two spellings of one Korean name compare unequal everywhere they are quoted.
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent


def data_root() -> Path:
    """`ARTIST_DATA` when set (tests, or a different creatives folder), else the skill's `data/`."""
    override = os.environ.get("ARTIST_DATA")
    return Path(override) if override else SKILL_DIR / "data"


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def resolve_job(raw: str) -> Path:
    """A short name such as `기념일/한글날` → data/기념일/한글날; `/…`, `~…`, `./…`, `../…` → that path."""
    raw = nfc(raw.strip())
    if not raw:
        raise ValueError("a job cannot be empty")
    if raw.startswith(("/", "~", ".")):
        return Path(raw).expanduser().resolve()
    return (data_root() / raw).resolve()


def resolve_input(raw: str | Path) -> Path:
    """An input file named on the command line: absolute, or relative to the caller's cwd."""
    return Path(nfc(str(raw))).expanduser().resolve()


def stored(job_dir: Path, path: Path) -> str:
    """How a path is written into the ledger: relative to the job folder when inside it, else absolute."""
    path = path.resolve()
    try:
        return str(path.relative_to(job_dir.resolve()))
    except ValueError:
        return str(path)


def from_stored(job_dir: Path, value: str) -> Path:
    """The inverse of `stored`."""
    p = Path(value)
    return p if p.is_absolute() else job_dir / p


def href(job_dir: Path, value: str) -> str:
    """A ledger path as a link from a page that sits in the job folder."""
    return os.path.relpath(from_stored(job_dir, value), job_dir)
