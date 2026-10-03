"""Why a run came back without an image, read from the rollout transcript Codex keeps per thread.

The event stream and the generated_images folder carry no reason; the rollout does, as the API's error object serialised as a JSON string inside Rust's `Some(...)` inside the event's own JSON. It is read by structure, not by a known error code, so a code never seen before still arrives with its message.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

MARKER = "image generation failed:"


def _strings(node) -> Iterator[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _parse(text: str) -> dict | None:
    at = text.find(MARKER)
    if at < 0:
        return None
    tail = text[at + len(MARKER):].strip()
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
    # The wrapper is Codex's and can change; the reason is still in the text, so keep it verbatim.
    return {"message": tail}


def _stage(err: dict) -> str | None:
    mod = err.get("moderation_details")
    stage = mod.get("moderation_stage") if isinstance(mod, dict) else None
    return str(stage) if isinstance(stage, (str, int)) else None


def failure(codex_home: Path, thread_id: str | None) -> dict | None:
    """The deciding failure of one thread, or None. A thread can hold several image_gen failures; one blocked at the output stage decides (only retrying helps there), the rest survive as `tries`."""
    if not thread_id:
        return None
    try:
        hits = sorted(codex_home.glob(f"sessions/**/rollout-*{thread_id}.jsonl"))
    except OSError:
        return None
    for hit in hits:
        try:
            raw = hit.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if MARKER not in raw:
            continue
        errors = []
        for line in raw.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            errors += [e for e in (_parse(t) for t in _strings(event)) if e is not None]
        if not errors:
            continue
        chosen = next((e for e in errors if _stage(e) == "output"), errors[-1])
        detail = {"events_file": str(hit)}
        if len(errors) > 1:
            detail["tries"] = len(errors)
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
