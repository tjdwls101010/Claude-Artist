"""Checking the environment generate and render need: Codex installed and logged in, Chrome launchable, data/ resolving to the creatives folder, and which installed font families CSS can name."""

from __future__ import annotations

import os

from imagen import chrome, codex, paths


def check(*, fonts: str) -> dict:
    problems = []
    path = codex.executable()
    codex_info = {"path": path, "version": None, "logged_in": False}
    if not path:
        problems.append("codex is not on PATH: install the Codex CLI")
    else:
        codex_info["version"] = codex.version()
        logged_in, status = codex.login_status()
        codex_info["logged_in"] = logged_in
        if not logged_in:
            problems.append(f"codex is not logged in ({status}): run `codex login`")

    chrome_ok, detail = chrome.available()
    if not chrome_ok:
        problems.append(f"Chrome cannot be launched for render: {detail}")

    data = paths.data_root()
    link = {"path": str(data), "target": os.readlink(data) if data.is_symlink() else None, "ok": data.is_dir()}
    if not link["ok"]:
        problems.append(f"data folder {data} does not resolve to a folder" + (f" (link target {link['target']} is missing)" if link["target"] else ""))

    out = {"ok": not problems, "problems": problems, "codex": codex_info, "chrome": {"ok": chrome_ok, "detail": detail}, "data_link": link}
    out["fonts"] = chrome.fonts(hangul_only=fonts == "hangul") if chrome_ok else []
    return out
