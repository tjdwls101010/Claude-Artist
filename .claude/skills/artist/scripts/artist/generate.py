"""Generating recorded versions: a queue of version × n attempts, at most six Codex calls at once, a verbatim gate on every prompt, a never-reused file per attempt, and one ledger row per try whatever happened.

Each variant is reported on stderr the moment it lands, with what to do next when it failed, because a batch takes minutes and silence reads as a hang.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import difflib
import shutil
import sys
import uuid
from pathlib import Path

from artist import codex, ledger, paths, sheet
from artist.errors import Failure

# Six calls have run together without failures; more is unmeasured.
CONCURRENCY = 6
ASPECT_TOLERANCE = 0.01

ADVICE = {
    "executor_error": "codex itself failed, not the prompt: check the environment (doctor) and the events file.",
    "timeout": "no answer in time; one call normally takes 40-180s, so look at whether this run was unusually slow before raising --timeout.",
    "prompt_altered": "codex changed the prompt even after a retry (diff above); the image is kept but cannot be chosen.",
    "path_mismatch": "the reported image lies outside this run's thread folder, so its origin is unverified; it is kept but cannot be chosen.",
    "silent_no_image": "no image and no reason found; read the events file and the rollout.",
    "interrupted": "the generating process died before this attempt finished.",
}
REFUSAL_ADVICE = {
    "output": "the generated image was blocked, not the prompt: rewording does not help, retrying is the only lever.",
    "input": "the prompt itself was blocked: change the prompt (subject or wording) rather than retrying it.",
}
REFUSAL_UNKNOWN = "codex did not say which stage blocked it; read the message above and decide."


def classify_verbatim(original: str, passed: str | None) -> str:
    if passed is None:
        return "unverified"
    if passed == original:
        return "exact"
    if " ".join(passed.split()) == " ".join(original.split()):
        return "normalized"
    return "mismatch"


def _say(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _ratio(aspect: str) -> float | None:
    if aspect == "none":
        return None
    w, h = aspect.split(":")
    return int(w) / int(h)


def _image_facts(path: Path, aspect: str, transparent: bool) -> dict:
    from PIL import Image

    with Image.open(path) as im:
        w, h = im.size
        facts = {"w": w, "h": h}
        want = _ratio(aspect)
        if want:
            facts["aspect_ok"] = abs((w / h) / want - 1) <= ASPECT_TOLERANCE
        if transparent:
            has_alpha = "A" in im.getbands() or "transparency" in im.info
            share = 0.0
            if has_alpha:
                hist = im.convert("RGBA").getchannel("A").histogram()
                share = round(hist[0] / (w * h), 3)
            facts["alpha"] = {"has_alpha": has_alpha, "transparent_share": share}
    return facts


def _line(row: dict, entry: dict, timeout: int) -> str:
    status = row["status"]
    if status == "ok":
        bits = [row["file"], f"{row['w']}x{row['h']}"]
        if "aspect_ok" in row:
            bits.append("aspect ok" if row["aspect_ok"] else f"aspect MISS (asked {entry['aspect']})")
        bits.append(row.get("verbatim", ""))
        if row.get("verbatim") == "unverified":
            bits.append("(codex did not report the prompt it passed)")
        if row.get("retried"):
            bits.append("after one verbatim retry")
        if "alpha" in row:
            bits.append(f"alpha {row['alpha']['transparent_share']:.0%} transparent" if row["alpha"]["has_alpha"] else "WARNING no alpha channel")
        bits.append(f"{round(row.get('elapsed_s', 0))}s")
        return "  ".join(b for b in bits if b)
    d = row.get("detail") or {}
    lines = [f"{row['id']}  FAILED  {status}  {round(row.get('elapsed_s', 0))}s"]
    facts = [d.get("code"), f"stage={d['moderation_stage']}" if d.get("moderation_stage") else None, f"categories={','.join(d['categories'])}" if d.get("categories") else None, f"exit={d['returncode']}" if d.get("returncode") else None, f"{d['tries']} tries in this thread" if d.get("tries") else None]
    facts = [f for f in facts if f]
    if facts:
        lines.append("    " + " · ".join(facts))
    if d.get("message"):
        lines.append("    " + " ".join(d["message"].split())[:200])
    if status == "refused":
        lines.append("    " + REFUSAL_ADVICE.get(d.get("moderation_stage"), REFUSAL_UNKNOWN))
    else:
        lines.append("    " + ADVICE.get(status, "").replace("in time", f"within {timeout}s"))
    if d.get("events_file"):
        lines.append(f"    {d['events_file']}")
    return "\n".join(lines)


def _one(job_dir: Path, entry: dict, row: dict, workdir: Path, timeout: int) -> dict:
    prompt = entry["prompt"]
    refs = [paths.from_stored(job_dir, r).resolve() for r in entry["refs"]]
    res = codex.run(prompt, refs=refs, transparent=entry["transparent"], stronger=False, workdir=workdir, tag=row["id"], timeout=timeout)
    verbatim = classify_verbatim(prompt, res.prompt_passed) if res.error is None else None
    retried = None
    elapsed = res.elapsed_s
    if verbatim == "mismatch":
        _say(f"{row['id']}  verbatim mismatch, retrying once with a stronger instruction")
        retried = {"verbatim": "mismatch", "elapsed_s": res.elapsed_s, "events_file": res.events_file}
        if res.thread_id:
            retried["thread_id"] = res.thread_id
        res = codex.run(prompt, refs=refs, transparent=entry["transparent"], stronger=True, workdir=workdir, tag=row["id"] + "-retry", timeout=timeout)
        verbatim = classify_verbatim(prompt, res.prompt_passed) if res.error is None else None
        elapsed += res.elapsed_s

    if res.timed_out:
        status = "timeout"
    elif res.error:
        status = "executor_error"
    elif verbatim == "mismatch":
        status = "prompt_altered"
        _say(f"{row['id']}  codex changed the prompt:\n" + "".join(difflib.unified_diff(prompt.splitlines(keepends=True), (res.prompt_passed or "").splitlines(keepends=True), fromfile="authored", tofile="passed to image_gen", n=1)))
    elif res.image is not None:
        status = "ok" if res.image_in_thread else "path_mismatch"
    else:
        status = "refused" if res.refused else "silent_no_image"

    fields: dict = {"status": status, "elapsed_s": round(elapsed, 1)}
    if res.thread_id:
        fields["thread_id"] = res.thread_id
    if verbatim:
        fields["verbatim"] = verbatim
    if retried:
        fields["retried"] = retried
    detail = {"events_file": res.events_file}
    if res.returncode is not None:
        detail["returncode"] = res.returncode
    if res.agent_message:
        detail["agent_message"] = res.agent_message
    detail.update(res.failure)
    if status != "ok":
        fields["detail"] = detail
    if res.image is not None:
        name = row["id"] + ".png"
        shutil.copy2(res.image, job_dir / name)
        fields["file"] = name
        fields.update(_image_facts(job_dir / name, entry["aspect"], entry["transparent"]))
    finished = ledger.finish_attempt(job_dir, entry["v"], row["id"], fields)
    result = {"version": entry["v"], "attempt": row["id"]}
    result.update({k: finished[k] for k in ("file", "status", "w", "h", "aspect_ok", "alpha", "verbatim") if k in finished})
    if res.error:
        result["error"] = res.error
    _say(_line(finished, entry, timeout))
    return result


def run(job_dir: Path, *, versions: list[int], n: int, timeout: int) -> dict:
    job = ledger.read(job_dir)
    entries = []
    for v in dict.fromkeys(versions):
        entry = ledger.version(job, v)
        if entry["method"] not in ("generate", "edit"):
            raise Failure(f"v{v} is a {entry['method']} version; only generate and edit versions are generated")
        missing = [r for r in entry["refs"] if not paths.from_stored(job_dir, r).is_file()]
        if missing:
            raise Failure(f"v{v} references images that no longer exist", missing=missing)
        entries.append(entry)
    if not codex.executable():
        raise Failure("codex is not on PATH; install the Codex CLI (doctor checks the environment)")
    logged_in, status = codex.login_status()
    if not logged_in:
        raise Failure(f"codex is not logged in; run `codex login` first ({status})")

    run_id = f"{datetime.datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:4]}"
    workdir = job_dir / ".runs" / run_id
    queue = [(entry, ledger.start_attempt(job_dir, entry["v"])) for entry in entries for _ in range(n)]
    sheet_path = sheet.ensure_opened(job_dir)
    _say(f"generating {len(queue)} image(s), at most {CONCURRENCY} at once; logs in {workdir}")

    def guarded(entry: dict, row: dict) -> dict:
        try:
            return _one(job_dir, entry, row, workdir, timeout)
        except Exception as exc:  # noqa: BLE001 -- a crash must still close the attempt, or it stays running forever
            ledger.finish_attempt(job_dir, entry["v"], row["id"], {"status": "executor_error", "detail": {"message": f"{type(exc).__name__}: {exc}"[:300]}})
            _say(f"{row['id']}  FAILED  executor_error  {type(exc).__name__}: {exc}")
            return {"version": entry["v"], "attempt": row["id"], "status": "executor_error", "error": f"{type(exc).__name__}: {exc}"}
        finally:
            try:
                sheet.rebuild(job_dir)
            except Exception as exc:  # noqa: BLE001 -- the attempt is recorded; a sheet that will not build must not lose it
                _say(f"warning: the sheet could not be rebuilt ({exc})")

    with concurrent.futures.ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        results = list(pool.map(lambda item: guarded(*item), queue))

    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    summary = f"{len(results)} requested · " + " · ".join(f"{c} {s}" for s, c in sorted(counts.items(), key=lambda kv: (kv[0] != "ok", kv[0])))
    misses = [r["file"] for r in results if r.get("aspect_ok") is False]
    if misses:
        summary += f" · aspect missed: {', '.join(misses)}"
    out = {"summary": summary, "results": results, "sheet": str(sheet_path)}
    if not counts.get("ok"):
        out["error"] = "no image succeeded; each result's status and the stderr lines say why"
    return out
