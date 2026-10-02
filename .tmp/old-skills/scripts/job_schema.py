#!/usr/bin/env python3
"""The shape of job.json, in code, plus the reader and writer that keep its formatting stable.

WHY THIS EXISTS
---------------
job.json is the whole record of a job -- intent, prose, results, critique, choice. Until this
module the schema lived only as an example block in SKILL.md, which meant nothing checked it. A
key typed `critque` silently deleted the critique from the contact sheet; a `chosen` that named
"c" instead of "v2-c.png" silently highlighted nothing; a field invented mid-session (`brief.notes`)
was accepted without a word. All three fail quietly, have a closed shape, and are exactly the
class of mistake a check should own.

Unknown keys are ERRORs on purpose, and that is deliberately inconvenient. An invented field may
turn out to be a good idea -- `brief.notes` did -- but blocking it forces the schema to grow by
decision instead of by accident. The error message points at this file for that reason.

`save()` fixes the formatting (indent 2, no ASCII escaping, trailing newline) because generate.py
writes results back into the same file that Claude edits. When those two disagreed on formatting,
a string-matching edit against the file failed mid-session.

WHY A FAILURE IS A ROW (schema 2)
----------------------------------
`images[]` could only hold what worked, so a version that was refused six times recorded
`not generated` -- the same thing a version nobody had run yet recorded. One round spent 25
generations, kept 4, and the 16% had to be typed into a `--note` by hand because the ledger had
no place to put it. `attempts[]` holds every try with its `status`, which is what lets the rate
be read rather than remembered.

Two consequences worth stating, because neither follows from the rename:

  - Attempts **append**. Tags restart at "a" on every run, so a regeneration overwrites
    `v1-a.png` on disk while the ledger keeps both rows. The ledger accumulates, the directory
    holds only the latest -- and `ok_files()` is what resolves the two, by letting only the last
    row with a given filename still claim it.
  - An **absent** `attempts` is not an empty one. A version migrated up from schema 1 with no
    images had no record kept, which is a different fact from "every attempt failed", and the
    display layer says so differently. Migration must not invent an empty array.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import unicodedata
from pathlib import Path

# WHERE A JOB LIVES
# -----------------
# Not the shape of job.json, so this sits apart from everything below it. It is here because the
# three CLIs already import this module, and a fifth script whose only job is to join two strings
# would buy a new import edge for nothing.
#
# The root is computed from this file rather than named anywhere in prose. A path written into
# SKILL.md is a rail: it holds until the skill directory moves, and then it points at nothing
# while still reading as authoritative. Computed here, the workspace moves with the skill.
DEFAULT_PROJECTS_ROOT = Path(__file__).resolve().parent.parent / "projects"


def resolve_job_dir(raw: str) -> Path:
    """Turn a --job/--like argument into a directory. Wire as argparse `type=`, never after it.

    `type=Path` cannot be layered on top of, because Path erases the one thing this rule turns
    on: `Path("이름/")` is `Path("이름")` and `Path("")` is `Path(".")`. Whether the caller wrote
    a separator has to be readable, so the raw string arrives here.

    A separator anywhere, or `.`/`..`, means the caller is pointing at a place and the argument
    stands verbatim -- which is also the escape hatch, `./커피추출` being the cwd-relative
    reading of a name that would otherwise resolve into the workspace. A bare name is normalised
    to NFC first: APFS returns NFD while argv carries NFC, so one Korean folder name arrives in
    two spellings that compare unequal in every ledger path and log line that quotes them.
    """
    if not raw.strip():
        raise argparse.ArgumentTypeError("a job directory cannot be empty")
    if "/" in raw or raw in (".", ".."):
        return Path(raw)
    return DEFAULT_PROJECTS_ROOT / unicodedata.normalize("NFC", raw)


def job_dir_help(lead: str) -> str:
    """`lead`, then the resolution rule. Every argument wired to resolve_job_dir uses this.

    Written once because four arguments across three CLIs publish the same rule, and four hand
    written copies is four things that can disagree with the resolver -- the defect this rule was
    introduced to remove, reintroduced one layer down.
    """
    return (f"{lead} A bare name lands under {DEFAULT_PROJECTS_ROOT}, the skill's own workspace, "
            f"which moves wherever the skill moves. Anything with a separator in it is used as "
            f"written, so ./NAME and NAME/ resolve from the shell's cwd and an absolute path "
            f"stays absolute")


SCHEMA_VERSION = 2

METHODS = ("regenerate", "reference-edit")
# Closed set: the flag that writes these teaches the commit axes by refusing anything else.
# One key per axis in SKILL.md's commit axes, in the order they are numbered there.
AXIS_KEYS = ("process", "palette", "composition", "space", "typography", "material",
             "refusals")

TOP_REQ = {"schema", "id", "created", "brief", "versions"}
TOP_OPT = {"final"}
BRIEF_REQ = {"request", "aspect"}
# `style_block` and `targets` were removed (성진 확정, 2026-08-21). Neither had a writer, a
# reader, or a single use across the three ledgers on disk -- and the comment that used to sit
# here said style_block "is inherited", which `--like` never did. `--schema` published both on
# every call, so the one surface D37 promoted to the authority on this file's shape was teaching
# two keys nothing could write. Same removal as `brief.category` (D33), for the same reason. If a
# job actually needs a multi-paragraph verified style, this grows back by decision, not by
# accident -- which is what the docstring above already demands.
#
# `target_images` is that decision, taken 2026-09-01 with the writer and the readers named up
# front: generate.py `--init --target` writes it and `--like` inherits it; measure.py takes it as
# the band when `--against` is absent; contact_sheet.py stands it above the variants; and the
# check below warns when nothing referenced it. The failure that bought it: a user named the
# poster to beat in their first message, it never entered the ledger, and twelve images were
# generated against an impression of it that had already drifted into a wrong prescription.
BRIEF_OPT = {"purpose", "text", "notes", "target_images"}
VERSION_REQ = {"v", "direction", "prompt", "method"}
VERSION_OPT = {"axes", "parent", "change", "ref_images", "critique", "chosen", "attempts",
               "measured"}
FINAL_REQ = {"file"}
FINAL_OPT = {"saved_to"}
# Written by generate.py, never by hand. Exactly the keys it builds for an attempt that produced
# a file, `note` included -- that one is added only when the image path had to be recovered from
# the thread directory.
IMAGE_OPT = {"file", "thread_id", "w", "h", "ratio", "mp", "verbatim", "elapsed_s", "source",
             "aspect_requested", "aspect_ok", "note"}
ATTEMPT_REQ = {"status"}
ATTEMPT_OPT = IMAGE_OPT | {"tag", "error", "detail"}
# `refused` is first because it is the common one: 21 of 25 attempts in the one round that
# measured this. `silent_no_image` is the residual bucket and nothing else -- the name used to
# be the diagnosis, and reading a refusal as an unexplained blank cost a session.
ATTEMPT_STATUS = ("ok", "running", "refused", "executor_error", "timeout", "prompt_altered",
                  "path_mismatch", "silent_no_image")
# Forensics, closed like every other key set here. What a failure line prints comes from these.
# `code` and `message` are the API's own, carried verbatim. They are here so that a failure this
# machine has never seen still has somewhere to land: without them the schema rejects the record,
# and the reason -- which is sitting on disk in the rollout -- gets dropped on the way to the
# ledger. The other four are the fields whose prescriptions are already known.
# `tries` counts image_gen calls codex made inside ONE `codex exec` -- a different thing from the
# rows in `attempts[]`, which count our own calls. A row that reads as one failure can have burned
# five generations, and that gap is the difference between reading a rate honestly and not.
DETAIL_OPT = {"code", "message", "moderation_stage", "categories", "returncode", "events_file",
              "agent_message", "tries"}
# Named here rather than in measure.py so that nothing on the write path has to import the one
# script with a third-party dependency. measure.py reads this list; the traffic is one-way.
MEASURE_AXES = ("flat-fill", "edge-contact", "value-contrast", "palette-distance", "hue-count",
                "ink-coverage")
# A measurement without its constants is not reproducible: v6-b reads 41.4 / 43.6 / 61.6 at blur
# 0.5 / 1.0 / 2.0. So the fingerprint is required wherever a value is stored.
MEASURED_REQ = {"value", "fingerprint"}
MEASURED_OPT = {"file", "band", "band_from"}

_TYPES = {
    "schema": int, "id": str, "created": str, "brief": dict, "versions": list, "final": dict,
    "brief.request": str, "brief.purpose": str, "brief.aspect": str,
    "brief.text": list, "brief.notes": list, "brief.target_images": list,
    "version.v": int, "version.direction": str, "version.prompt": str, "version.method": str,
    "version.axes": dict, "version.ref_images": list, "version.critique": str,
    "version.chosen": str, "version.attempts": list, "version.measured": dict,
    "attempt.status": str, "attempt.tag": str, "attempt.error": str, "attempt.detail": dict,
    "attempt.file": str, "attempt.w": int, "attempt.h": int, "attempt.elapsed_s": int,
    "measured.value": float, "measured.fingerprint": str, "measured.file": str,
    "measured.band": list, "measured.band_from": int,
    "final.file": str, "final.saved_to": str,
}
# These two carry null in the schema's own example, so None is a legal value.
_NULLABLE = {"version.parent": int, "version.change": str}


def _shape(where: str, obj, req: set, opt: set, errors: list) -> bool:
    if not isinstance(obj, dict):
        errors.append(f"{where}: expected an object, got {type(obj).__name__}")
        return False
    for k in sorted(req - set(obj)):
        errors.append(f"{where}: missing required key '{k}'")
    for k in sorted(set(obj) - req - opt):
        errors.append(f"{where}: unknown key '{k}' -- if the field is real, add it to "
                      f"job_schema.py; otherwise it is a typo")
    return True


def _types(where: str, obj: dict, prefix: str, errors: list) -> None:
    for k, val in obj.items():
        key = f"{prefix}.{k}" if prefix else k
        if key in _NULLABLE:
            if val is not None and not isinstance(val, _NULLABLE[key]):
                errors.append(f"{where}: '{k}' must be {_NULLABLE[key].__name__} or null")
        elif key in _TYPES and not isinstance(val, _TYPES[key]):
            errors.append(f"{where}: '{k}' must be {_TYPES[key].__name__}, "
                          f"got {type(val).__name__}")


def ok_files(entry: dict) -> list[str]:
    """The filenames of one version that are actually on disk and actually chooseable.

    Everything downstream of "did this version produce anything" derives from here: `chosen`,
    `final.file`, the missing-critique nag, and the sheet's thumbnails. Two rules, both of which
    the array's own shape makes necessary rather than optional. An attempt has to have finished
    with `status == "ok"` -- a refusal carries no file and a `running` row carries one that is
    not written yet. And when a later run reuses a filename, only the later row still owns it:
    tags restart at "a" every run, so `v1-a.png` on disk is whatever wrote it last, and letting
    the superseded row keep the name would put a stale critique next to a new picture.
    """
    latest: dict[str, int] = {}
    for i, at in enumerate(entry.get("attempts") or []):
        if isinstance(at, dict) and at.get("status") == "ok" and at.get("file"):
            latest[at["file"]] = i
    return [f for f, _ in sorted(latest.items(), key=lambda kv: kv[1])]


def validate(job, job_dir: Path | None = None) -> dict:
    """Check one job record. `job_dir` enables the on-disk reference-image check when given."""
    errors: list[str] = []
    warnings: list[str] = []

    if not _shape("job.json", job, TOP_REQ, TOP_OPT, errors):
        return {"errors": errors, "warnings": warnings}
    _types("job.json", job, "", errors)

    if job.get("schema") != SCHEMA_VERSION:
        errors.append(f"job.json: schema must be {SCHEMA_VERSION}, got {job.get('schema')!r}")

    brief = job.get("brief")
    if _shape("brief", brief, BRIEF_REQ, BRIEF_OPT, errors):
        _types("brief", brief, "brief", errors)

    if job_dir is not None and isinstance(brief, dict):
        for t in brief.get("target_images") or []:
            if not (Path(job_dir) / t).exists():
                errors.append(f"brief: target_images entry '{t}' does not exist -- paths are "
                              f"relative to the job directory {Path(job_dir).resolve()}, so this "
                              f"looked for {(Path(job_dir) / t).resolve()}")

    versions = job.get("versions")
    all_files: set[str] = set()
    if not isinstance(versions, list):
        return {"errors": errors, "warnings": warnings}

    seen: set = set()
    referenced: set = set()
    numbers = {v.get("v") for v in versions if isinstance(v, dict)}
    for entry in versions:
        where = f"versions[v={entry.get('v')}]" if isinstance(entry, dict) else "versions[]"
        if not _shape(where, entry, VERSION_REQ, VERSION_OPT, errors):
            continue
        _types(where, entry, "version", errors)

        num = entry.get("v")
        if num in seen:
            errors.append(f"{where}: duplicate version number {num}")
        seen.add(num)

        if "method" in entry and entry["method"] not in METHODS:
            errors.append(f"{where}: method must be one of {list(METHODS)}, "
                          f"got {entry['method']!r}")
        # A reference-edit with nothing to reference is a regenerate wearing the wrong label: it
        # runs, it looks fine, and the reproducibility contract (prompt + method + ref_images) is
        # quietly broken because the record no longer says what was actually fed in.
        if entry.get("method") == "reference-edit" and not entry.get("ref_images"):
            errors.append(f"{where}: method is reference-edit but ref_images is empty -- pass "
                          f"--ref when appending the version, or use --method regenerate")

        parent = entry.get("parent")
        if parent is not None and parent not in numbers:
            errors.append(f"{where}: parent {parent} is not a version in this job")

        axes = entry.get("axes")
        if isinstance(axes, dict):
            for k in sorted(set(axes) - set(AXIS_KEYS)):
                errors.append(f"{where}: unknown axis '{k}' -- allowed: {list(AXIS_KEYS)}")

        if job_dir is not None:
            for r in entry.get("ref_images") or []:
                if not (Path(job_dir) / r).exists():
                    # The path and the point it is measured from, because the mistake this catches
                    # is almost always a path typed against the shell's cwd -- and "not in the job
                    # directory" left the reader guessing which directory that was.
                    errors.append(f"{where}: ref_images entry '{r}' does not exist -- paths are "
                                  f"relative to the job directory "
                                  f"{Path(job_dir).resolve()}, so this looked for "
                                  f"{(Path(job_dir) / r).resolve()}")

        for i, at in enumerate(entry.get("attempts") or []):
            aw = f"{where}.attempts[{i}]"
            if not _shape(aw, at, ATTEMPT_REQ, ATTEMPT_OPT, errors):
                continue
            _types(aw, at, "attempt", errors)
            if "status" in at and at["status"] not in ATTEMPT_STATUS:
                errors.append(f"{aw}: status must be one of {list(ATTEMPT_STATUS)}, "
                              f"got {at['status']!r}")
            detail = at.get("detail")
            if isinstance(detail, dict):
                for k in sorted(set(detail) - DETAIL_OPT):
                    errors.append(f"{aw}: unknown detail key '{k}' -- allowed: "
                                  f"{sorted(DETAIL_OPT)}")

        measured = entry.get("measured")
        if isinstance(measured, dict):
            for axis, m in measured.items():
                mw = f"{where}.measured[{axis}]"
                if axis not in MEASURE_AXES:
                    errors.append(f"{mw}: unknown axis -- allowed: {list(MEASURE_AXES)}")
                    continue
                if _shape(mw, m, MEASURED_REQ, MEASURED_OPT, errors):
                    _types(mw, m, "measured", errors)

        files = ok_files(entry)
        all_files.update(files)

        chosen = entry.get("chosen")
        if chosen is not None and chosen not in files:
            errors.append(f"{where}: chosen '{chosen}' is not one of this version's images "
                          f"({files or 'none generated yet'})")

        if files and not entry.get("critique"):
            warnings.append(f"{where}: has images but no critique -- look at them before moving on")

        referenced.update(entry.get("ref_images") or [])

    # A target nothing referenced. The ledger says what this job is judged against, and every
    # version chose to generate without showing the model that image -- which is sometimes right
    # (a target can be a yardstick you deliberately do not imitate) and is therefore a WARN. It is
    # here rather than in SKILL.md because it is decidable from the record alone: set membership,
    # no aesthetics. The session that bought it translated the target into prose instead, and the
    # four directions it wrote came back sharing one palette the target does not have.
    # `versions` gates this: a job between --init and its first --new-version has referenced
    # nothing yet and is not wrong for it, and a check that fires on every correct --init is the
    # kind that teaches the reader to skim past SCHEMA lines.
    targets = brief.get("target_images") if isinstance(brief, dict) else None
    if targets and versions and not (set(targets) & referenced):
        warnings.append(
            f"brief.target_images names {len(targets)} image(s) but no version passed any of "
            f"them as --ref -- prose is not a substitute for the reference, and the family "
            f"resemblance a target carries is the part prose translates worst")

    final = job.get("final")
    if final is not None and _shape("final", final, FINAL_REQ, FINAL_OPT, errors):
        _types("final", final, "final", errors)
        if final.get("file") not in all_files:
            errors.append(f"final: file '{final.get('file')}' is not an image in any version")

    return {"errors": errors, "warnings": warnings}


def _fmt(prefix: str, keys) -> str:
    """Key names, each carrying its type where this module declares one."""
    out = []
    for k in sorted(keys):
        full = f"{prefix}.{k}" if prefix else k
        if full in _NULLABLE:
            out.append(f"{k} ({_NULLABLE[full].__name__} | null)")
        elif full in _TYPES:
            out.append(f"{k} ({_TYPES[full].__name__})")
        else:
            out.append(k)
    return ", ".join(out)


def describe(stream) -> None:
    """Print the shape of job.json, for the flag that publishes it.

    Each section is written out by hand and filled from the constant, so a key added to one of
    those sets prints itself. What that leaves uncovered is a constant no section names, and
    tests/test_skill.py walks this module for exactly that. Rewriting the sections themselves as
    a loop over the constants would make the two sides the same code and the guard would stop
    being able to disagree.
    """
    def w(text: str = "") -> None:
        print(text, file=stream)

    w(f"job.json -- schema {SCHEMA_VERSION}. Written by generate.py, contact_sheet.py and "
      f"measure.py, never by hand.")
    w()
    w("top level")
    w(f"  required   {_fmt('', TOP_REQ)}")
    w(f"  optional   {_fmt('', TOP_OPT)}")
    w()
    w("brief")
    w(f"  required   {_fmt('brief', BRIEF_REQ)}")
    w(f"  optional   {_fmt('brief', BRIEF_OPT)}")
    w()
    w("versions[]")
    w(f"  required   {_fmt('version', VERSION_REQ)}")
    w(f"  optional   {_fmt('version', VERSION_OPT)}")
    w(f"  method     {' | '.join(METHODS)}")
    w(f"  axes keys  {', '.join(AXIS_KEYS)}")
    w()
    w("versions[].attempts[]   appended by generate.py, never by hand -- one row per try,")
    w("                        succeeded or not")
    w(f"  required   {_fmt('attempt', ATTEMPT_REQ)}")
    w(f"  optional   {_fmt('attempt', ATTEMPT_OPT)}")
    w(f"  status     {' | '.join(ATTEMPT_STATUS)}")
    w(f"  detail     {', '.join(sorted(DETAIL_OPT))}")
    w()
    w("versions[].measured   written by measure.py, keyed by axis")
    w(f"  required   {_fmt('measured', MEASURED_REQ)}")
    w(f"  optional   {_fmt('measured', MEASURED_OPT)}")
    w(f"  axes       {', '.join(MEASURE_AXES)}")
    w()
    w("final")
    w(f"  required   {_fmt('final', FINAL_REQ)}")
    w(f"  optional   {_fmt('final', FINAL_OPT)}")
    w()
    w("rules the key lists do not carry")
    w("  - method reference-edit requires a non-empty ref_images; a reference-edit with nothing")
    w("    to reference is a regenerate wearing the wrong label, so it is an ERROR")
    w("  - ref_images paths resolve relative to the job directory, not the shell's cwd, both")
    w("    when this checks them and when generate.py hands them to codex. brief.target_images")
    w("    resolves the same way")
    w("  - brief.target_images and versions[].ref_images are different roles and often the same")
    w("    file. A target is what the job is judged against -- the band measure.py falls back to,")
    w("    the image the sheet stands above the variants. A ref is what one generation was")
    w("    conditioned on. Naming a target does not feed it to the model; only --ref does, and a")
    w("    target no version referenced is a WARN rather than an ERROR because a yardstick you")
    w("    deliberately do not imitate is a real case")
    w("  - attempts append across runs and tags restart at 'a' each time, so a regeneration")
    w("    overwrites v1-a.png on disk while both rows stay in the ledger. Only the last row")
    w("    with a given file, and only one whose status is ok, can be chosen or made final")
    w("  - an absent attempts[] means no record was kept (schema 1, or never run); an attempts[]")
    w("    with no ok row means it ran and every try failed. Those are different facts")
    w("  - prompt + method + ref_images have to be enough to make that version again. There is no")
    w("    seed, so what is reproducible is the intent, not the pixels -- which is why the prompt")
    w("    is stored as one block of prose and never as slots reassembled at generation time")


def report(findings: dict, stream) -> None:
    """Print findings the same way everywhere, so the two callers cannot describe them differently."""
    for e in findings["errors"]:
        print(f"  SCHEMA ERROR  {e}", file=stream)
    for w in findings["warnings"]:
        print(f"  SCHEMA WARN   {w}", file=stream)


def migrate(job: dict) -> dict:
    """Bring an older record up to SCHEMA_VERSION in memory, on the way in.

    Five ledgers on disk are schema 1 and none of them are being rewritten in place: a bulk edit
    of records that are already finished work would risk every one of them to save a branch here.
    So the reader moves and the files stay put until something writes them for its own reasons.

    An image in schema 1 is an attempt that succeeded, which is the whole of the mapping -- the
    key sets are otherwise identical, checked against all five ledgers (60 images, key union
    exactly IMAGE_OPT). What is deliberately *not* mapped is a version with no images at all: it
    gets no `attempts` key, because schema 1 never recorded the failures and inventing an empty
    array would claim it did.
    """
    if job.get("schema") == SCHEMA_VERSION:
        return job
    for v in job.get("versions", []):
        if not isinstance(v, dict):
            continue
        images = v.pop("images", None)
        if images:
            v["attempts"] = [{**im, "status": "ok"} for im in images]
    job["schema"] = SCHEMA_VERSION
    return job


def load(job_dir: Path) -> dict:
    jf = Path(job_dir) / "job.json"
    if not jf.exists():
        raise SystemExit(f"job.json not found in {job_dir}. Write the job's intent before generating.")
    return migrate(json.loads(jf.read_text(encoding="utf-8")))


# Incremental saves come from the worker threads that generate concurrently, so two of them can
# reach the rename at once. Serialising here rather than at each call site means a writer added
# later cannot forget to. Reentrant because record_attempt() reads, edits and saves under it, and
# save() takes it again on the way through.
_WRITE_LOCK = threading.RLock()


def record_attempt(job_dir: Path, version: int, tag: str, attempt: dict | None) -> Path | None:
    """Put one attempt into the ledger, replacing this tag's `running` row if it has one.

    Called with `attempt=None` at the start of a batch and again with the result, which is what
    makes a batch visible while it is still running: a row exists from the moment the call goes
    out, so the sheet can say four are in flight rather than showing the previous version for the
    next two minutes.

    Read, edit and write are one critical section. Four variants finish independently and each
    appends, so a plain save-under-lock would still let two of them read the same array and one
    write win. The replacement scans backwards for this tag's most recent `running` row because
    tags repeat across runs -- `a` is in the ledger once per batch -- and only the open one is
    this run's.

    The lock is a thread lock, so it holds within one process and not between two. Running
    `measure.py --job ... --version N` against a ledger while a generation is still in flight can
    therefore lose the measurement: generate.py holds a copy read before that write and stamps
    over it on the next attempt. Each write is still atomic (temp file, then rename), so nothing
    is corrupted -- the measurement is simply gone and re-running it puts it back. Closing this
    would mean a lock file in every job directory, which is a visible artifact in a folder that
    is otherwise all deliverables; the honest trade is to say so and let the sequence be
    "generate, look, then measure", which is the order the round already runs in.
    """
    with _WRITE_LOCK:
        job = load(job_dir)
        for v in job.get("versions", []):
            if v.get("v") != version:
                continue
            atts = v.setdefault("attempts", [])
            row = attempt if attempt is not None else {"tag": tag, "status": "running"}
            for i in range(len(atts) - 1, -1, -1):
                if atts[i].get("tag") == tag and atts[i].get("status") == "running":
                    atts[i] = row
                    break
            else:
                atts.append(row)
        return save(job_dir, job)


def save(job_dir: Path, job: dict) -> Path | None:
    """Write through a temp file, then rebuild the sheet. Returns the sheet's path.

    The rebuild lives here rather than in the callers because contact_sheet.py's docstring has
    declared it a contract from the start -- "Every command that writes job.json rebuilds it in
    the same pass" -- and two of the four writers quietly broke it: `--init` left a ledger with
    no page beside it, and `--note` wrote a finding into a page still showing the previous
    version. A contract that each caller has to remember is a contract that gets forgotten.

    The import is deferred to the call rather than the module because contact_sheet imports this
    file; at module scope the two would be a cycle.

    A sheet that will not build must not take the ledger down with it. This is the only place
    that swallows, and it swallows in one direction: the write has already happened by the time
    anything here can fail.
    """
    jf = Path(job_dir) / "job.json"
    with _WRITE_LOCK:
        tmp = jf.with_name("job.json.tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(jf)
        try:
            from contact_sheet import build_sheet
            return build_sheet(Path(job_dir))[0]
        except Exception as exc:  # noqa: BLE001
            print(f"  warn: job.json saved but the contact sheet could not be rebuilt ({exc})",
                  file=sys.stderr)
            return None
