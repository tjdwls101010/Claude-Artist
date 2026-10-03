"""Deterministic image work Claude does itself: pasting a corrected region onto the approved image, and rendering an HTML layout to a PNG. Each produces a new version with one attempt, like a generation.

A render reads a snapshot, not the working files: the HTML and everything it references are copied under renders/v{N}/ first, keeping their paths relative to the job folder, so the version can be reproduced after the originals change or disappear.
"""

from __future__ import annotations

import re
import shutil
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

import numpy as np

from imagen import chrome, ledger, paths, regions, sheet
from imagen.errors import Failure

DIFF_SIDE = 128
CSS_URL = re.compile(r"""url\(\s*(['"]?)(.*?)\1\s*\)""", re.I)
DOCUMENTS = {".html", ".htm", ".svg"}
ASCII_SPACE = " \t\n\r\f"  # HTML separates srcset candidates on ASCII whitespace only
CSS_IMPORT = re.compile(r"""@import\s+(?:url\(\s*)?(['"])(.*?)\1""", re.I)


# ---- patch ------------------------------------------------------------------------------------

def _ok_file(job: dict, name: str, flag: str) -> tuple[dict, dict]:
    entry, row = ledger.find_file(job, name)
    if row["status"] != "ok":
        raise Failure(f"{flag} {row['file']} finished as {row['status']}; only an ok attempt can be patched")
    return entry, row


def _mask(shape: tuple[int, int], box: tuple[int, int, int, int], feather: int) -> np.ndarray:
    """1 inside the box, 0 outside, ramping up over `feather` pixels inward from the box's edge only."""
    h, w = shape
    x0, y0, x1, y1 = box
    mask = np.zeros((h, w), float)
    ys, xs = np.mgrid[y0:y1, x0:x1]
    if feather > 0:
        dist = np.minimum.reduce([xs - x0, x1 - 1 - xs, ys - y0, y1 - 1 - ys]).astype(float)
        mask[y0:y1, x0:x1] = np.clip((dist + 1) / (feather + 1), 0, 1)
    else:
        mask[y0:y1, x0:x1] = 1.0
    return mask


def patch(job_dir: Path, *, base: str, source: str, region, feather: int, direction: str | None) -> dict:
    from PIL import Image

    job = ledger.read(job_dir)
    base_entry, base_row = _ok_file(job, base, "--base")
    _, from_row = _ok_file(job, source, "--from")
    for r in (base_row, from_row):
        if not (job_dir / r["file"]).is_file():
            raise Failure(f"{r['file']} is in the ledger but its file is missing from {job_dir}")
    with Image.open(job_dir / base_row["file"]) as b, Image.open(job_dir / from_row["file"]) as f:
        if b.size != f.size:
            raise Failure(f"--base is {b.size[0]}x{b.size[1]} but --from is {f.size[0]}x{f.size[1]}; a patch needs two images of the same size")
        mode = "RGBA" if "A" in b.getbands() or b.has_transparency_data else "RGB"
        base_arr = np.asarray(b.convert(mode))
        from_arr = np.asarray(f.convert(mode))
        small = (DIFF_SIDE, max(1, round(DIFF_SIDE * b.height / b.width))) if b.width >= b.height else (max(1, round(DIFF_SIDE * b.width / b.height)), DIFF_SIDE)
        base_small = np.asarray(b.convert("L").resize(small, Image.Resampling.LANCZOS), float)
        from_small = np.asarray(f.convert("L").resize(small, Image.Resampling.LANCZOS), float)
    h, w = base_arr.shape[:2]
    box = regions.to_pixels(region, w, h)
    mask = _mask((h, w), box, feather)[..., None]
    blended = np.rint(base_arr * (1 - mask) + from_arr * mask).astype(np.uint8)
    # Pixels outside the box are copied, not re-computed, so they stay bit-identical whatever the arithmetic does.
    outside = mask[..., 0] == 0
    blended[outside] = base_arr[outside]

    sbox = regions.to_pixels(region, small[0], small[1])
    keep = np.ones(base_small.shape, bool)
    keep[sbox[1]:sbox[3], sbox[0]:sbox[2]] = False
    outside_diff = round(float(np.abs(base_small - from_small)[keep].mean()), 2) if keep.any() else 0.0

    with ledger.edit(job_dir) as doc:
        entry = ledger.add_version(doc, {"direction": direction or base_entry["direction"], "method": "patch", "parent": base_entry["v"], "inputs": {"base": base_row["file"], "from": from_row["file"], "region_px": list(box), "feather": feather}})
        row = dict(ledger.new_attempt(entry))
    name = row["id"] + ".png"
    try:
        Image.fromarray(blended).save(job_dir / name)
    except Exception as exc:  # noqa: BLE001 -- the attempt must close with the reason, never stay running
        ledger.finish_attempt(job_dir, entry["v"], row["id"], {"status": "executor_error", "detail": {"message": f"{type(exc).__name__}: {exc}"[:300]}})
        raise Failure(f"could not write the patched image: {exc}") from exc
    ledger.finish_attempt(job_dir, entry["v"], row["id"], {"status": "ok", "file": name, "w": w, "h": h})
    sheet.rebuild(job_dir)
    return {"version": entry["v"], "file": name, "outside_diff": outside_diff}


# ---- render -----------------------------------------------------------------------------------

class _Refs(HTMLParser):
    """Every URL an HTML document asks the browser to load, plus its inline CSS."""

    def __init__(self):
        super().__init__()
        self.urls: list[str] = []
        self.css: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): v for k, v in attrs if v is not None}
        if tag == "style":
            self._in_style = True
        for key in ("src", "poster", "data"):
            if key in a:
                self.urls.append(a[key])
        if "srcset" in a:
            self.urls += _srcset_urls(a["srcset"])
        if tag == "link" and "href" in a and a.get("rel", "").lower() in ("stylesheet", "preload", "icon", "prefetch"):
            self.urls.append(a["href"])
        if tag in ("image", "use", "feimage"):
            for key in ("href", "xlink:href"):
                if key in a and not a[key].startswith("#"):
                    self.urls.append(a[key])
        if "style" in a:
            self.css.append(a["style"])

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False

    def handle_data(self, data):
        if self._in_style:
            self.css.append(data)


def _srcset_urls(value: str) -> list[str]:
    """URLs of a srcset, tokenised the way the HTML spec does: a URL runs to whitespace (so data: URLs keep their commas), trailing commas end a candidate with no descriptor, otherwise descriptors run to the next comma outside parentheses."""
    urls, i, n = [], 0, len(value)
    while i < n:
        while i < n and (value[i] in ASCII_SPACE or value[i] == ","):
            i += 1
        start = i
        while i < n and value[i] not in ASCII_SPACE:
            i += 1
        url = value[start:i]
        if url.endswith(","):
            urls.append(url.rstrip(","))
            continue
        if url:
            urls.append(url)
        depth = 0
        while i < n and not (value[i] == "," and depth == 0):
            depth += {"(": 1, ")": -1}.get(value[i], 0)
            i += 1
    return urls


def _css_urls(css: str) -> list[str]:
    return [m.group(2) for m in CSS_IMPORT.finditer(css)] + [m.group(2) for m in CSS_URL.finditer(css)]


def _collect(html: Path, job_dir: Path) -> tuple[set[Path], list[str], list[str]]:
    """Files the page needs (HTML and linked CSS followed), plus refused and missing references."""
    files, refused, missing = {html}, [], []
    queue: list[tuple[str, Path, bool]] = []  # (url, document dir, is css)

    def document(path: Path) -> None:
        parser = _Refs()
        parser.feed(path.read_text(encoding="utf-8", errors="replace"))
        queue.extend((u, path.parent, False) for u in parser.urls)
        for css in parser.css:
            queue.extend((u, path.parent, False) for u in _css_urls(css))

    document(html)
    root = job_dir.resolve()
    seen = set()
    while queue:
        url, base, _ = queue.pop()
        url = url.strip()
        if not url or url.startswith(("#", "data:", "about:")) or (url, base) in seen:
            continue
        seen.add((url, base))
        parsed = urlparse(url)
        if parsed.scheme and parsed.scheme != "file" or url.startswith("//"):
            refused.append(f"{url} (remote; copy the file into the job folder)")
            continue
        raw = unquote(parsed.path)
        if parsed.scheme == "file" or Path(raw).is_absolute():
            refused.append(f"{url} (absolute path; use a path relative to the file that references it, so the snapshot can stand alone)")
            continue
        target = (base / raw).resolve()
        if target != root and root not in target.parents:
            refused.append(f"{url} (outside the job folder; copy it in first)")
            continue
        if not target.is_file():
            missing.append(f"{url} -> {target}")
            continue
        if target in files:
            continue
        files.add(target)
        if target.suffix.lower() == ".css":
            queue += [(u, target.parent, True) for u in _css_urls(target.read_text(encoding="utf-8", errors="replace"))]
        elif target.suffix.lower() in DOCUMENTS:
            document(target)
    return files, refused, missing


def render(job_dir: Path, *, html: Path, size: tuple[int, int], scale: int, direction: str | None, parent: int | None) -> dict:
    root = job_dir.resolve()
    html = html.resolve()
    if root not in html.parents:
        raise Failure(f"{html} is outside the job folder {root}; write the HTML inside it")
    if not html.is_file():
        raise Failure(f"{html} does not exist")
    files, refused, missing = _collect(html, job_dir)
    if refused or missing:
        raise Failure("the page references files that cannot be rendered", refused_assets=refused, missing_assets=missing)

    with ledger.edit(job_dir) as doc:
        if direction is None:
            direction = ledger.version(doc, parent)["direction"]
        v = max((e["v"] for e in doc["versions"]), default=0) + 1
        snapshot_html = Path("renders") / f"v{v}" / html.relative_to(root)
        fields = {"direction": direction, "method": "render", "inputs": {"html_snapshot": str(snapshot_html), "size": list(size), "scale": scale}}
        if parent is not None:
            fields["parent"] = parent
        entry = ledger.add_version(doc, fields)
        row = dict(ledger.new_attempt(entry))

    snap_root = job_dir / "renders" / f"v{v}"
    name = row["id"] + ".png"
    out = job_dir / name
    try:
        for f in files:
            dest = snap_root / f.relative_to(root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dest)
        sheet.ensure_opened(job_dir)
        report = chrome.render(job_dir / snapshot_html, out, size=size, scale=scale, root=snap_root)
    except Exception as exc:  # noqa: BLE001 -- the attempt must close with the reason, never stay running
        message = f"{type(exc).__name__}: {' '.join(str(exc).split())[:300]}"
        ledger.finish_attempt(job_dir, v, row["id"], {"status": "executor_error", "detail": {"code": "render_failed", "message": message}})
        sheet.rebuild(job_dir)
        raise Failure(f"render of v{v} failed: {message}", version=v) from exc
    problems = {
        "missing_assets": report["failed_requests"],
        "refused_assets": report["refused_requests"],
        "font_problems": report["font_problems"],
        "console_errors": report["console_errors"],
    }
    if any(problems.values()) or not out.is_file():
        kept = None
        if out.is_file():
            kept = snap_root / "_failed-render.png"
            out.replace(kept)
        counts = ", ".join(f"{len(v)} {k.replace('_', ' ')}" for k, v in problems.items() if v)
        ledger.finish_attempt(job_dir, v, row["id"], {"status": "executor_error", "detail": {"code": "render_failed", "message": counts or "no image was written"}})
        sheet.rebuild(job_dir)
        extra = {"version": v, **problems}
        if kept:
            extra["failed_render"] = str(kept)
        raise Failure(f"render of v{v} failed: {counts or 'no image was written'}", **extra)
    w, h = size[0] * scale, size[1] * scale
    ledger.finish_attempt(job_dir, v, row["id"], {"status": "ok", "file": name, "w": w, "h": h})
    sheet.rebuild(job_dir)
    return {"version": v, "file": name, "px": [w, h]}
