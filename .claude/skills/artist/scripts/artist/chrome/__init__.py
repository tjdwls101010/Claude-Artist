"""The installed Google Chrome as a renderer, driven by Playwright (channel "chrome"; nothing is downloaded).

`render` loads one local HTML file in a fresh browser at a CSS viewport and device scale, waits for fonts and images, and writes a PNG. It lets only file:// and data: requests through, and only file:// requests under `root`; everything it saw go wrong comes back as lists in the skill's terms. `ARTIST_CHROME_PATH` points it at another Chrome binary.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import catalog, probe

__all__ = ["available", "render", "fonts"]

LOAD_TIMEOUT_MS = 30_000


@contextmanager
def _browser():
    from playwright.sync_api import sync_playwright

    override = os.environ.get("ARTIST_CHROME_PATH")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=override) if override else p.chromium.launch(channel="chrome")
        try:
            yield browser
        finally:
            browser.close()


def available() -> tuple[bool, str]:
    try:
        with _browser() as browser:
            return True, f"Chrome {browser.version}"
    except Exception as exc:  # noqa: BLE001 -- any launch failure means render cannot run
        return False, " ".join(str(exc).split())[:300]


def render(html: Path, out: Path, *, size: tuple[int, int], scale: int, root: Path) -> dict:
    """Render `html` to `out`. Returns {"failed_requests", "refused_requests", "console_errors", "font_problems", "timed_out"}; all empty means a clean render."""
    report = {"failed_requests": [], "refused_requests": [], "console_errors": [], "font_problems": [], "timed_out": False}
    root = root.resolve()

    def route(r):
        url = r.request.url
        parsed = urlparse(url)
        if parsed.scheme == "data":
            return r.continue_()
        if parsed.scheme == "file":
            path = Path(unquote(parsed.path)).resolve()
            if path == root or root in path.parents:
                return r.continue_()
        report["refused_requests"].append(url)
        return r.abort()

    # Only what happens up to the screenshot belongs to the page: probing fonts afterwards makes DevTools re-request inline stylesheet sources, which a file:// page refuses and logs.
    listening = [True]

    def note(kind: str, text: str) -> None:
        if listening[0]:
            report[kind].append(text)

    with _browser() as browser:
        # Context-level routing also covers popups and workers; offline stops anything that slips past it from reaching the network.
        context = browser.new_context(viewport={"width": size[0], "height": size[1]}, device_scale_factor=scale, offline=True)
        context.route("**/*", route)
        page = context.new_page()
        page.on("requestfailed", lambda req: note("failed_requests", f"{unquote(req.url)} ({req.failure})") if req.url not in report["refused_requests"] else None)
        page.on("console", lambda msg: note("console_errors", msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: note("console_errors", str(err)))
        try:
            page.goto(html.resolve().as_uri(), wait_until="load", timeout=LOAD_TIMEOUT_MS)
            page.wait_for_function("document.fonts.status === 'loaded'", timeout=LOAD_TIMEOUT_MS)
            page.evaluate("() => Promise.all([...document.images].map(i => i.decode().catch(() => null)))")
            page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": size[0], "height": size[1]}, animations="disabled")
            listening[0] = False
            report["font_problems"] = probe.font_problems(page)
        except Exception as exc:  # noqa: BLE001 -- a timeout or crash is a failed render, reported like the rest
            report["timed_out"] = "Timeout" in type(exc).__name__
            report["console_errors"].append(f"{type(exc).__name__}: {' '.join(str(exc).split())[:300]}")
        context.close()
    return report


def fonts(hangul_only: bool) -> list[dict]:
    """Installed families as CSS can name them: each name is kept only if Chrome resolves it to a face that draws the sample on its own."""
    found = catalog.families(hangul_only)
    names = sorted({n for f in found for n in [f["family"], *f["aliases"]]})
    sample = "한글 Aa" if hangul_only else "Aa"
    with _browser() as browser:
        page = browser.new_page()
        ok = probe.family_available(page, names, sample)
    out = []
    for f in found:
        usable = [n for n in [f["family"], *f["aliases"]] if ok.get(n)]
        if not usable:
            continue
        entry = {"family": usable[0], "weights": f["weights"]}
        if usable[1:]:
            entry["aliases"] = usable[1:]
        out.append(entry)
    return out
