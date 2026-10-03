"""The shape of job.json (schema 3), checked in full before every write.

Unknown keys are errors: the ledger grows by decision, not by a typo that silently drops a field from the sheet.
"""

from __future__ import annotations

import re

SCHEMA = 3

METHODS = ("generate", "edit", "patch", "render")
STATUSES = ("ok", "running", "interrupted", "refused", "executor_error", "timeout", "prompt_altered", "path_mismatch", "silent_no_image")
VERBATIM = ("exact", "normalized", "unverified", "mismatch")
SCOPES = ("job", "reusable")
AXES = ("zones", "contrast", "edges", "colors")
DETAIL_KEYS = {"code", "message", "moderation_stage", "categories", "returncode", "events_file", "agent_message", "tries"}

ASPECT = re.compile(r"[1-9]\d{0,2}:[1-9]\d{0,2}")

# key: (type or tuple of types, required)
TOP = {"schema": (int, True), "id": (str, True), "created": (str, True), "sheet_opened": (str, False), "brief": (dict, True), "versions": (list, True), "measurements": (list, True), "final": (dict, False)}
BRIEF = {"request": (str, True), "aspect": (str, True), "purpose": (str, False), "text": (list, False), "targets": (list, False), "notes": (list, False)}
NOTE = {"text": (str, True), "scope": (str, True), "at": (str, True)}
VERSION = {"v": (int, True), "direction": (str, True), "method": (str, True), "created": (str, True), "attempts": (list, True), "parent": (int, False), "change": (str, False), "prompt": (str, False), "refs": (list, False), "aspect": (str, False), "transparent": (bool, False), "inputs": (dict, False), "critique": (str, False), "chosen": (str, False)}
PATCH_INPUTS = {"base": (str, True), "from": (str, True), "region_px": (list, True), "feather": (int, True)}
RENDER_INPUTS = {"html_snapshot": (str, True), "size": (list, True), "scale": (int, True)}
ATTEMPT = {"id": (str, True), "status": (str, True), "started": (str, True), "pid": (int, False), "elapsed_s": ((int, float), False), "file": (str, False), "w": (int, False), "h": (int, False), "aspect_ok": (bool, False), "alpha": (dict, False), "verbatim": (str, False), "retried": (dict, False), "thread_id": (str, False), "detail": (dict, False)}
ALPHA = {"has_alpha": (bool, True), "transparent_share": ((int, float), True)}
RETRIED = {"thread_id": (str, False), "elapsed_s": ((int, float), False), "verbatim": (str, False), "events_file": (str, False)}
MEASUREMENT = {"file": (str, True), "axis": (str, True), "values": (dict, True), "fingerprint": (str, True), "at": (str, True), "version": (int, False), "bands": (list, False), "box": (list, False), "target_color": (str, False)}
FINAL = {"file": (str, True), "exported": (list, True)}
EXPORT = {"path": (str, True), "size": (str, True), "format": (str, True), "at": (str, True), "crop": (list, False)}


def _shape(where: str, obj, spec: dict, errors: list) -> bool:
    if not isinstance(obj, dict):
        errors.append(f"{where}: expected an object, got {type(obj).__name__}")
        return False
    for key, (kind, required) in spec.items():
        if key not in obj:
            if required:
                errors.append(f"{where}: missing required key '{key}'")
            continue
        value = obj[key]
        if isinstance(value, bool) and kind in (int, (int, float)):
            errors.append(f"{where}: '{key}' must be a number, got bool")
        elif not isinstance(value, kind):
            names = kind.__name__ if isinstance(kind, type) else "/".join(k.__name__ for k in kind)
            errors.append(f"{where}: '{key}' must be {names}, got {type(value).__name__}")
    for key in sorted(set(obj) - set(spec)):
        errors.append(f"{where}: unknown key '{key}'")
    return True


def _strings(where: str, value, errors: list) -> None:
    if isinstance(value, list) and not all(isinstance(x, str) for x in value):
        errors.append(f"{where}: every entry must be a string")


def ok_files(version: dict) -> list[str]:
    """Files of this version's attempts that finished ok: the only ones that can be chosen or made final."""
    return [a["file"] for a in version.get("attempts", []) if isinstance(a, dict) and a.get("status") == "ok" and a.get("file")]


def validate(job) -> list[str]:
    errors: list[str] = []
    if not _shape("job", job, TOP, errors):
        return errors
    if job.get("schema") != SCHEMA:
        errors.append(f"job: schema must be {SCHEMA}, got {job.get('schema')!r}")

    brief = job.get("brief")
    if _shape("brief", brief, BRIEF, errors):
        if isinstance(brief.get("aspect"), str) and not ASPECT.fullmatch(brief["aspect"]):
            errors.append(f"brief: aspect must be W:H, got {brief['aspect']!r}")
        _strings("brief.text", brief.get("text"), errors)
        _strings("brief.targets", brief.get("targets"), errors)
        for i, note in enumerate(brief.get("notes") or []):
            if _shape(f"brief.notes[{i}]", note, NOTE, errors) and note.get("scope") not in SCOPES:
                errors.append(f"brief.notes[{i}]: scope must be one of {list(SCOPES)}, got {note.get('scope')!r}")

    versions = job.get("versions") if isinstance(job.get("versions"), list) else []
    numbers: set = set()
    ids: set = set()
    files: dict[str, int] = {}
    all_ok: set = set()
    for entry in versions:
        where = f"versions[v={entry.get('v')}]" if isinstance(entry, dict) else "versions[]"
        if not _shape(where, entry, VERSION, errors):
            continue
        num = entry.get("v")
        if num in numbers:
            errors.append(f"{where}: duplicate version number")
        numbers.add(num)
        parent = entry.get("parent")
        if parent is not None and (parent not in numbers or parent == num):
            errors.append(f"{where}: parent {parent} is not an earlier version")
        method = entry.get("method")
        if method not in METHODS:
            errors.append(f"{where}: method must be one of {list(METHODS)}, got {method!r}")
        if method in ("generate", "edit"):
            for key in ("prompt", "refs", "aspect", "transparent"):
                if key not in entry:
                    errors.append(f"{where}: a {method} version needs '{key}'")
            if "inputs" in entry:
                errors.append(f"{where}: a {method} version has no 'inputs'")
            if method == "edit" and not entry.get("refs"):
                errors.append(f"{where}: an edit version needs at least one ref")
            aspect = entry.get("aspect")
            if isinstance(aspect, str) and aspect != "none" and not ASPECT.fullmatch(aspect):
                errors.append(f"{where}: aspect must be W:H or none, got {aspect!r}")
            _strings(f"{where}.refs", entry.get("refs"), errors)
        elif method in ("patch", "render"):
            for key in ("prompt", "refs", "aspect", "transparent"):
                if key in entry:
                    errors.append(f"{where}: a {method} version has no '{key}'")
            spec = PATCH_INPUTS if method == "patch" else RENDER_INPUTS
            if "inputs" not in entry:
                errors.append(f"{where}: a {method} version needs 'inputs'")
            else:
                _shape(f"{where}.inputs", entry["inputs"], spec, errors)

        for i, attempt in enumerate(entry.get("attempts") or []):
            aw = f"{where}.attempts[{i}]"
            if not _shape(aw, attempt, ATTEMPT, errors):
                continue
            if attempt.get("id") in ids:
                errors.append(f"{aw}: duplicate attempt id {attempt.get('id')!r}")
            ids.add(attempt.get("id"))
            status = attempt.get("status")
            if status not in STATUSES:
                errors.append(f"{aw}: status must be one of {list(STATUSES)}, got {status!r}")
            if status == "running" and "pid" not in attempt:
                errors.append(f"{aw}: a running attempt needs its pid")
            if attempt.get("verbatim") is not None and attempt["verbatim"] not in VERBATIM:
                errors.append(f"{aw}: verbatim must be one of {list(VERBATIM)}")
            if "alpha" in attempt:
                _shape(f"{aw}.alpha", attempt["alpha"], ALPHA, errors)
            if "retried" in attempt:
                _shape(f"{aw}.retried", attempt["retried"], RETRIED, errors)
            if isinstance(attempt.get("detail"), dict):
                for key in sorted(set(attempt["detail"]) - DETAIL_KEYS):
                    errors.append(f"{aw}.detail: unknown key '{key}'")
            name = attempt.get("file")
            if name:
                if name in files:
                    errors.append(f"{aw}: file {name!r} already belongs to another attempt")
                files[name] = num
        oks = ok_files(entry)
        all_ok.update(oks)
        chosen = entry.get("chosen")
        if chosen is not None and chosen not in oks:
            errors.append(f"{where}: chosen {chosen!r} is not an ok attempt of this version ({oks or 'none yet'})")

    for i, m in enumerate(job.get("measurements") or []):
        mw = f"measurements[{i}]"
        if _shape(mw, m, MEASUREMENT, errors) and m.get("axis") not in AXES:
            errors.append(f"{mw}: axis must be one of {list(AXES)}")

    final = job.get("final")
    if final is not None and _shape("final", final, FINAL, errors):
        if final.get("file") not in all_ok:
            errors.append(f"final: {final.get('file')!r} is not an ok attempt of any version")
        for i, ex in enumerate(final.get("exported") or []):
            _shape(f"final.exported[{i}]", ex, EXPORT, errors)
    return errors
