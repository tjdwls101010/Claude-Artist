"""Writing intent and judgment into the ledger: starting a job, recording a version before it is generated, and recording what was seen and decided, up to the final image and its delivery copy."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from artist import ledger, paths, regions, sheet
from artist.errors import Failure, Usage

ASPECT_TOLERANCE = 0.01


def _existing(files: list[Path], what: str) -> None:
    missing = [str(p) for p in files if not p.is_file()]
    if missing:
        raise Failure(f"{what} not found", missing=missing)


def init(job_dir: Path, *, request: str, aspect: str | None, purpose: str | None, text: list[str], targets: list[Path], notes: list[str], like: Path | None) -> dict:
    if ledger.exists(job_dir):
        raise Failure(f"{job_dir / 'job.json'} already exists; a ledger is never overwritten")
    inherited = ledger.inherit(like) if like else {"aspect": None, "targets": [], "notes": [], "legacy_notes": []}
    aspect = aspect or inherited["aspect"]
    if not aspect:
        raise Usage("init: --aspect is required because --like supplied none")
    _existing(targets, "--target image")
    warnings = []
    kept_targets = []
    for t in inherited["targets"]:
        if Path(t).is_file():
            kept_targets.append(t)
        else:
            warnings.append(f"inherited target no longer exists, skipped: {t}")
    at = ledger.now()
    all_targets = list(dict.fromkeys(kept_targets + [paths.stored(job_dir, t) for t in targets]))
    brief = {"request": request, "aspect": aspect}
    if purpose:
        brief["purpose"] = purpose
    if text:
        brief["text"] = text
    if all_targets:
        brief["targets"] = all_targets
    all_notes = [dict(n) for n in inherited["notes"]] + [{"text": n, "scope": "job", "at": at} for n in notes]
    if all_notes:
        brief["notes"] = all_notes
    job = {"schema": ledger.SCHEMA, "id": job_dir.name, "created": at, "brief": brief, "versions": [], "measurements": []}
    ledger.create(job_dir, job)
    out = {"job": str(job_dir), "sheet": str(sheet.rebuild(job_dir)), "inherited": {"aspect": inherited["aspect"], "targets": kept_targets, "notes": [n["text"] for n in inherited["notes"]]} if like else {}}
    if inherited["legacy_notes"]:
        out["legacy_notes"] = inherited["legacy_notes"]
    if warnings:
        out["warnings"] = warnings
    return out


def add(job_dir: Path, *, prompt: str, direction: str, method: str, refs: list[Path], aspect: str | None, transparent: bool, parent: int | None, change: str | None) -> dict:
    _existing(refs, "--ref image")
    with ledger.edit(job_dir) as job:
        fields = {"direction": direction, "method": method, "prompt": prompt, "refs": [paths.stored(job_dir, r) for r in refs], "aspect": aspect or job["brief"]["aspect"], "transparent": transparent}
        if parent is not None:
            fields["parent"] = parent
        if change:
            fields["change"] = change
        entry = ledger.add_version(job, fields)
    sheet.rebuild(job_dir)
    return {"version": entry["v"]}


def _ok_attempt(job: dict, name: str, what: str) -> tuple[dict, dict]:
    entry, row = ledger.find_file(job, name)
    if row["status"] != "ok":
        raise Failure(f"{what} {row['file']} finished as {row['status']}; only an ok attempt can be {what.lstrip('-')}")
    return entry, row


def _export(job_dir: Path, final: str, dest: Path, crop, size, fmt: str | None) -> dict:
    from PIL import Image

    suffix = dest.suffix.lower()
    implied = {".png": "png", ".jpg": "jpg", ".jpeg": "jpg"}.get(suffix)
    fmt = fmt or implied or "png"
    if implied and implied != fmt:
        raise Failure(f"--format {fmt} disagrees with the export file's extension {suffix}")
    if dest.exists():
        raise Failure(f"{dest} already exists; exports never overwrite")
    with Image.open(job_dir / final) as im:
        im.load()
        image = im
    box = None
    if crop:
        box = regions.to_pixels(crop, image.width, image.height)
        image = image.crop(box)
    if size:
        want, have = size[0] / size[1], image.width / image.height
        if abs(have / want - 1) > ASPECT_TOLERANCE:
            raise Failure(f"the {'cropped ' if crop else ''}image is {image.width}x{image.height} (ratio {have:.4f}) but --size {size[0]}x{size[1]} has ratio {want:.4f}; they differ by more than 1%", hint="adjust --crop so its aspect matches --size")
        image = image.resize(size, Image.Resampling.LANCZOS)
    if fmt == "jpg":
        if image.mode in ("RGBA", "LA", "P"):
            rgba = image.convert("RGBA")
            flat = Image.new("RGB", rgba.size, (255, 255, 255))
            flat.paste(rgba, mask=rgba.getchannel("A"))
            image = flat
        else:
            image = image.convert("RGB")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f".{dest.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        image.save(tmp, format="PNG" if fmt == "png" else "JPEG", **({"quality": 95} if fmt == "jpg" else {}))
        try:
            os.link(tmp, dest)  # fails instead of overwriting if dest appeared meanwhile
        except FileExistsError as exc:
            raise Failure(f"{dest} already exists; exports never overwrite") from exc
    finally:
        tmp.unlink(missing_ok=True)
    entry = {"path": str(dest), "size": f"{image.width}x{image.height}", "format": fmt, "at": ledger.now()}
    if box:
        entry["crop"] = list(box)
    return entry


def write(job_dir: Path, *, version: int | None, critique: str | None, chosen: str | None, notes: list[str], reusable: bool, targets: list[Path], final: str | None, export: Path | None, crop, size, fmt: str | None) -> dict:
    _existing(targets, "--target image")

    def apply(job: dict) -> list[str]:
        written = []
        if version is not None and (critique or chosen):
            entry = ledger.version(job, version)
            if critique:
                entry["critique"] = critique
                written.append(f"critique v{version}")
            if chosen:
                owner, row = _ok_attempt(job, chosen, "--chosen")
                if owner is not entry:
                    raise Failure(f"--chosen {row['file']} belongs to v{owner['v']}, not v{version}")
                entry["chosen"] = row["file"]
                written.append(f"chosen v{version} {row['file']}")
        if notes:
            at = ledger.now()
            job["brief"].setdefault("notes", []).extend({"text": n, "scope": "reusable" if reusable else "job", "at": at} for n in notes)
            written.append(f"{len(notes)} {'reusable' if reusable else 'job'} note(s)")
        if targets:
            have = job["brief"].setdefault("targets", [])
            have.extend(t for t in (paths.stored(job_dir, p) for p in targets) if t not in have)
            written.append(f"{len(targets)} target(s)")
        if final:
            owner, row = _ok_attempt(job, final, "--final")
            if version is not None and owner["v"] != version:
                raise Failure(f"--final {row['file']} belongs to v{owner['v']}, not v{version}")
        return written

    exported = None
    if final:
        # Check everything against the current ledger before any file is written, so a refused write leaves no orphan export behind.
        apply(ledger.read(job_dir))
        name = Path(final).name
        if export:
            exported = _export(job_dir, name, export, crop, size, fmt)
    with ledger.edit(job_dir) as job:
        written = apply(job)
        if final:
            name = Path(final).name
            current = job.get("final")
            if not current or current["file"] != name:
                job["final"] = {"file": name, "exported": []}
                written.append(f"final {name}")
            if exported:
                job["final"]["exported"].append(exported)
                written.append(f"export {exported['path']}")
    sheet.rebuild(job_dir)
    out = {"written": written}
    if exported:
        out["exported"] = exported
    return out


def show(job_dir: Path) -> dict:
    job = ledger.read(job_dir)
    brief = job["brief"]
    versions = []
    failures: dict[str, int] = {}
    for entry in job["versions"]:
        statuses = [a["status"] for a in entry["attempts"]]
        for s in statuses:
            if s not in ("ok", "running"):
                failures[s] = failures.get(s, 0) + 1
        row = {"v": entry["v"], "direction": entry["direction"], "method": entry["method"], "ok": statuses.count("ok"), "failed": len([s for s in statuses if s not in ("ok", "running")]), "running": statuses.count("running")}
        if entry.get("parent") is not None:
            row["parent"] = entry["parent"]
        if entry.get("chosen"):
            row["chosen"] = entry["chosen"]
        if entry.get("critique"):
            row["critique_first_line"] = entry["critique"].splitlines()[0]
        versions.append(row)
    total_ok = sum(v["ok"] for v in versions)
    total = sum(v["ok"] + v["failed"] for v in versions)
    summary = f"{job['id']} · {brief['aspect']} · {len(versions)} version(s) · {total_ok}/{total} attempts ok"
    if job.get("final"):
        summary += f" · final {job['final']['file']}"
    out = {"summary": summary, "brief": {k: brief[k] for k in ("request", "aspect", "purpose", "text", "targets") if k in brief}, "notes": [f"[{n['scope']}] {n['text']}" for n in brief.get("notes", [])], "versions": versions, "failures_by_kind": failures, "sheet": str(job_dir / sheet.SHEET)}
    if job.get("final"):
        out["final"] = job["final"]
    if job.get("measurements"):
        out["measurements"] = len(job["measurements"])
    return out
