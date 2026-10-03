"""Installed font files, read with fontTools: the family names they declare and their weights.

Names come from the name table (typographic family, ID 16, when present; legacy family, ID 1, otherwise). The same file can answer to both, which is why the list keeps aliases; whether CSS really resolves a name is decided by Chrome afterwards, not here.
"""

from __future__ import annotations

import logging
from pathlib import Path

FOLDERS = [Path.home() / "Library" / "Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts"), Path("/System/Library/Fonts/Supplemental")]
SUFFIXES = {".ttf", ".otf", ".ttc", ".otc"}
HANGUL = (0xAC00, 0xD55C, 0xAE00)  # 가 한 글


def _faces(path: Path):
    from fontTools.ttLib import TTCollection, TTFont

    if path.suffix.lower() in (".ttc", ".otc"):
        yield from TTCollection(str(path), lazy=True).fonts
    else:
        yield TTFont(str(path), lazy=True, fontNumber=0)


def families(hangul_only: bool) -> list[dict]:
    logging.getLogger("fontTools").setLevel(logging.ERROR)  # malformed but usable system fonts log a warning each
    found: dict[str, dict] = {}
    for folder in FOLDERS:
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.suffix.lower() not in SUFFIXES:
                continue
            try:
                for face in _faces(path):
                    names = face["name"]
                    legacy = names.getDebugName(1)
                    typographic = names.getDebugName(16) or legacy
                    if not typographic:
                        continue
                    if hangul_only:
                        cmap = face.getBestCmap() or {}
                        if not all(cp in cmap for cp in HANGUL):
                            continue
                    entry = found.setdefault(typographic, {"family": typographic, "aliases": set(), "weights": set()})
                    if legacy and legacy != typographic:
                        entry["aliases"].add(legacy)
                    if "OS/2" in face:
                        entry["weights"].add(int(face["OS/2"].usWeightClass))
            except Exception:  # noqa: BLE001 -- one unreadable font file must not hide the rest
                continue
    return [{"family": e["family"], "aliases": sorted(e["aliases"]), "weights": sorted(e["weights"])} for e in sorted(found.values(), key=lambda e: e["family"].lower())]
