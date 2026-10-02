#!/usr/bin/env python3
"""Measure the narrow band of things the eye gets systematically wrong, with fixed definitions.

WHY FIXED AXES, WHEN THE SKILL USED TO SAY THE OPPOSITE
------------------------------------------------------
SKILL.md used to say to write the measurement fresh each time, because the question is different
each time. The question is -- but the measurement is not, and writing it fresh produced three
ad-hoc scripts in one session of which **two were wrong**. So the axes are fixed here and the
question stays free: which axis to reach for, what to compare against, and what the number means
are all still judgment. This file only guarantees that the same question twice gets the same number.

EVERY NUMBER CARRIES ITS CONSTANTS
-----------------------------------
Not decoration. The same image reads **41.4 / 43.6 / 61.6** on flat-fill at blur 0.5 / 1.0 / 2.0,
and **39.1 / 48.1** at threshold 2.0 / 4.0. A measurement printed without the constants that
produced it is a number nobody can disagree with later, which is the opposite of the point. So
the fingerprint prints beside the value and is stored beside it in `versions[].measured`.

THE AXIS NAME IS NOT WHAT THE AXIS MEASURES
--------------------------------------------
`flat-fill` measures *local detail density seen at 900px*. Inside one subject and one treatment
that tracked the thing it was named for exactly (4.5 -> 30.7 -> 43.6 as the prescription was
followed). Read across jobs it stops meaning that: a greyscale memorial poster scores like a flat
two-ink print, and an image that is halftone edge to edge scores near zero. So this prints a
position within a band you assembled, never a verdict, and never "high on this axis = that style".

WHICH REFERENCES GO IN THE BAND IS A JUDGMENT, NOT A DEFAULT
-------------------------------------------------------------
That is why `--against` is required for a band and there is no automatic source for it. Pointing
it at a whole folder of examples produced a band of 3.27-76.35, inside which every image ever
measured "passes" -- including one that had already been rejected. `versions[].ref_images` is no
better a default: one job's two references gave 44.5-76.4, a band with no room in it. Name the
references you mean.

`brief.target_images` is the one exception, and it is not a scan. A person put it there with
`--target` to say what this job is judged against, so falling back to it when `--against` is
absent hands along a judgment already made rather than making one. What keeps that honest is
that the fallback says so: every line prints where its band came from. An unlabelled default is
how a named judgment turns back into an automatic source, which is the thing this section is
about. Naming `--against` still wins -- the fallback only fills a silence.

THIS IS THE ONLY SCRIPT HERE WITH A THIRD-PARTY DEPENDENCY
-----------------------------------------------------------
The other four are stdlib-only on purpose -- `png_size` unpacks IHDR by hand rather than shell out
to `sips`. This one needs a decoder, and **nothing else may import it**: `save()` imports
contact_sheet on every write, so a decoder reached at contact_sheet's import time would make
Pillow a requirement for writing any ledger at all. Measurements travel through
`versions[].measured`, never through an import. tests/test_skill.py walks the imports to keep it
that way.

    measure.py --axis flat-fill IMG [IMG ...]
    measure.py --axis flat-fill IMG --against REF --against REF
    measure.py --axis palette-distance IMG --against REF
    measure.py --list
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from job_schema import (MEASURE_AXES, job_dir_help, load,  # noqa: E402
                        resolve_job_dir, save)

# Every constant that moves a number, in one place, so the fingerprint cannot drift from the code
# that produced it. Changing any of these invalidates every stored measurement taken before it --
# which is exactly what the fingerprint is for: the old ones say what they were.
SHORT_SIDE = 900       # normalize before anything else; detail density is resolution-dependent
BLUR_SIGMA = 1.0       # sensor grain and JPEG ringing read as detail without it
MAX_FILTER = 7         # a gradient is thin; dilate before thresholding or every edge reads flat
FLAT_THRESHOLD = 3.0   # gradient magnitude below this counts as flat
INK_DELTA = 24         # luminance distance from the paper tone at which a pixel counts as ink
EDGE_BAND = 0.01       # border ring width, as a fraction of the short side
HUE_BINS = 24          # 15 degrees each
HUE_MIN_SHARE = 0.02   # a bin counts as a hue if it holds more than this of the coloured pixels
HUE_MIN_SAT = 0.15     # below this a pixel is grey and its hue is noise
PALETTE_K = 8          # adaptive-palette clusters per image, for palette-distance

FINGERPRINT = {
    "flat-fill": f"short={SHORT_SIDE} blur={BLUR_SIGMA} maxfilter={MAX_FILTER} "
                 f"thresh={FLAT_THRESHOLD}",
    "edge-contact": f"short={SHORT_SIDE} band={EDGE_BAND} ink_delta={INK_DELTA}",
    "value-contrast": f"short={SHORT_SIDE} ink_delta={INK_DELTA}",
    "palette-distance": f"short={SHORT_SIDE} k={PALETTE_K} lab=CIE76 weight=population "
                        f"agg=max(a->b,b->a)",
    "hue-count": f"short={SHORT_SIDE} bins={HUE_BINS} min_share={HUE_MIN_SHARE} "
                 f"min_sat={HUE_MIN_SAT}",
    "ink-coverage": f"short={SHORT_SIDE} ink_delta={INK_DELTA}",
}

UNIT = {"flat-fill": "%", "edge-contact": "%", "value-contrast": "/255",
        "palette-distance": "dE", "hue-count": "", "ink-coverage": "%"}

CAVEAT = {
    "flat-fill": "이 축이 실제로 재는 것은 900px에서 본 국소 디테일 밀도다. 같은 소재·같은 처방 "
                 "안의 준수도로는 유효했지만, 작업을 가로질러 '높다 = 어느 계열'로 읽으면 틀린다 "
                 "— 무채색 포스터가 2도 인쇄처럼, 전면 망점이 0%처럼 나온다.",
    "palette-distance": "구현 선택이 값을 크게 흔드는 축이다. 위 지문과 다른 정의로 잰 수치와는 "
                        "비교하지 않는다.",
    "hue-count": "채도 바닥(HUE_MIN_SAT) 아래는 세지 않는다. 무채색 작업에서는 0이 정상이다.",
}


def _pil():
    """Import the decoder at call time, with the install line in the failure.

    Deferred so that `--list` works without it, and so importing this module for its constants
    cannot pull a dependency into a process that only meant to read a ledger.
    """
    try:
        from PIL import Image, ImageFilter
    except ImportError:
        raise SystemExit(
            "measure.py needs a PNG decoder and Pillow is not installed.\n"
            "  python3 -m pip install --user Pillow\n"
            "Nothing else in this skill needs it -- generate.py and contact_sheet.py are "
            "stdlib-only and keep working without it.")
    return Image, ImageFilter


def _norm(path: Path):
    """Every axis starts from the same frame: short side at SHORT_SIDE, RGB."""
    Image, _ = _pil()
    im = Image.open(path).convert("RGB")
    w, h = im.size
    s = SHORT_SIDE / min(w, h)
    return im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)


def _luma(im):
    return im.convert("L")


# --------------------------------------------------------------------------------------

def flat_fill(path: Path) -> float:
    """Share of the frame holding no local detail.

    Central differences rather than a Sobel because that is what produced the numbers this axis
    was calibrated on, and the truncation to 8 bits before the max filter is part of the
    definition rather than an accident of it -- gradient magnitude tops out near 180 here, so
    nothing wraps, but rounding differently would move the value.
    """
    Image, ImageFilter = _pil()
    im = _luma(_norm(path)).filter(ImageFilter.GaussianBlur(BLUR_SIGMA))
    w, h = im.size
    px = im.load()
    mag = Image.new("L", (w, h))
    mp = mag.load()
    for y in range(h):
        y0, y1 = max(0, y - 1), min(h - 1, y + 1)
        dy_div = 2.0 if (y1 - y0) == 2 else 1.0
        for x in range(w):
            x0, x1 = max(0, x - 1), min(w - 1, x + 1)
            dx = (px[x1, y] - px[x0, y]) / (2.0 if (x1 - x0) == 2 else 1.0)
            dy = (px[x, y1] - px[x, y0]) / dy_div
            mp[x, y] = int(math.hypot(dx, dy))
    flat = mag.filter(ImageFilter.MaxFilter(MAX_FILTER))
    # tobytes() rather than getdata(): one byte per pixel for an "L" image, and getdata() is on
    # its way out of Pillow -- its deprecation warning would land in the middle of the numbers.
    data = flat.tobytes()
    return sum(1 for v in data if v < FLAT_THRESHOLD) / len(data) * 100


def _paper_and_ink(im) -> tuple[int, list[int]]:
    """The ground tone, and every pixel's luminance.

    The ground is the most common luminance, not the mean and not the lightest: a poster is mostly
    its paper, and the mean slides with how much ink is on it -- which is the thing being measured.
    """
    lum = _luma(im).tobytes()
    hist = [0] * 256
    for v in lum:
        hist[v] += 1
    # Smoothed over a small window so a bimodal paper texture cannot split its own peak.
    best, best_n = 0, -1
    for v in range(256):
        n = sum(hist[max(0, v - 2):v + 3])
        if n > best_n:
            best, best_n = v, n
    return best, lum


def ink_coverage(path: Path) -> float:
    paper, lum = _paper_and_ink(_norm(path))
    return sum(1 for v in lum if abs(v - paper) > INK_DELTA) / len(lum) * 100


def value_contrast(path: Path) -> float:
    """Mean luminance distance between the ground and what sits on it.

    The eye reads saturation as lightness and gets this backwards: yellow on dark kraft looked
    like it leapt off the page at a real difference of 18.7, and the ivory ground that looked
    flatter measured 87.4.
    """
    paper, lum = _paper_and_ink(_norm(path))
    ink = [v for v in lum if abs(v - paper) > INK_DELTA]
    if not ink:
        return 0.0
    return abs(sum(ink) / len(ink) - paper)


def edge_contact(path: Path) -> float:
    """Share of the border ring carrying ink.

    "Empty" is almost never about how much ink is on the page -- adding body copy raises coverage
    and leaves this untouched, because body copy sits in the middle too. The poster that read as
    empty measured 14.3 here while the rest of its series ran 48-81.
    """
    im = _norm(path)
    paper, _ = _paper_and_ink(im)
    w, h = im.size
    band = max(1, round(min(w, h) * EDGE_BAND))
    px = _luma(im).load()
    total = inked = 0
    for y in range(h):
        edge_row = y < band or y >= h - band
        for x in range(w):
            if not (edge_row or x < band or x >= w - band):
                continue
            total += 1
            if abs(px[x, y] - paper) > INK_DELTA:
                inked += 1
    return inked / total * 100 if total else 0.0


def hue_count(path: Path) -> float:
    """How many distinct hues actually hold territory."""
    im = _norm(path)
    bins = [0] * HUE_BINS
    coloured = 0
    raw = im.tobytes()
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        mx, mn = max(r, g, b), min(r, g, b)
        if mx == 0 or (mx - mn) / mx < HUE_MIN_SAT:
            continue
        coloured += 1
        d = mx - mn
        if mx == r:
            hue = (60 * ((g - b) / d)) % 360
        elif mx == g:
            hue = 60 * ((b - r) / d) + 120
        else:
            hue = 60 * ((r - g) / d) + 240
        bins[int(hue // (360 / HUE_BINS)) % HUE_BINS] += 1
    if not coloured:
        return 0.0
    return float(sum(1 for n in bins if n / coloured > HUE_MIN_SHARE))


def _lab(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    def f(c):
        c /= 255.0
        c = c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
        return c
    r, g, b = (f(c) for c in rgb)
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 1.00000
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def k(t):
        return t ** (1 / 3) if t > 0.008856 else (7.787 * t + 16 / 116)
    fx, fy, fz = k(x), k(y), k(z)
    return (116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz))


def _palette(path: Path) -> list[tuple[tuple[float, float, float], float]]:
    """PALETTE_K cluster centres in Lab, with their share of the frame."""
    Image, _ = _pil()
    im = _norm(path).convert("P", palette=Image.ADAPTIVE, colors=PALETTE_K)
    pal = im.getpalette() or []
    counts = im.getcolors(PALETTE_K * 4) or []
    total = sum(n for n, _ in counts) or 1
    out = []
    for n, idx in counts:
        rgb = tuple(pal[idx * 3:idx * 3 + 3])
        if len(rgb) == 3:
            out.append((_lab(rgb), n / total))
    return out


def _one_way(a, b) -> float:
    return sum(min(math.dist(ca, cb) for cb, _ in b) * wa for ca, wa in a)


def palette_distance(path: Path, refs: list[Path]) -> float:
    """Population-weighted nearest-cluster distance in Lab, symmetrised.

    Pairwise by nature, so `--against` here names what it is measured against rather than a band.
    Symmetrised with max because one-way distance is near zero whenever the subject's palette is a
    subset of the reference's -- a two-ink print would read as matching a full-colour photograph.
    """
    if not refs:
        raise SystemExit("palette-distance is measured against something: pass --against REF, "
                         "or --job a ledger whose brief.target_images names one")
    a = _palette(path)
    best = None
    for r in refs:
        b = _palette(r)
        d = max(_one_way(a, b), _one_way(b, a))
        best = d if best is None else min(best, d)
    return best or 0.0


AXES = {
    "flat-fill": flat_fill,
    "edge-contact": edge_contact,
    "value-contrast": value_contrast,
    "palette-distance": palette_distance,
    "hue-count": hue_count,
    "ink-coverage": ink_coverage,
}


def measure(axis: str, path: Path, refs: list[Path]) -> float:
    fn = AXES[axis]
    return fn(path, refs) if axis == "palette-distance" else fn(path)


def position(value: float, band: tuple[float, float]) -> str:
    """Where a value sits relative to a band, said without a verdict.

    No check mark and no cross. The axis whose name suggests one is the axis measuring something
    slightly different from its name, and a tick would put weight on exactly the inference that
    does not hold across jobs.
    """
    lo, hi = band
    if value < lo:
        return f"밴드 아래 {lo - value:.2f}"
    if value > hi:
        return f"밴드 위 {value - hi:.2f}"
    span = hi - lo
    return f"밴드 안 {((value - lo) / span * 100):.0f}%" if span else "밴드 안"


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n")[0],
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog="A band is only ever the references you name. There is no folder scan: pointing "
               "this at one job's example folder produced 3.27-76.35, a band that accepts "
               "everything including an image already rejected. The single fallback is "
               "brief.target_images when --job is given and --against is not, because a person "
               "named those with --target; every line prints which of the two it used.")
    ap.add_argument("images", nargs="*", type=Path, help="image(s) to measure")
    ap.add_argument("--axis", choices=list(MEASURE_AXES),
                    help="which axis to measure; --list prints what each one means")
    ap.add_argument("--against", type=Path, action="append", default=[], metavar="REF",
                    help="reference image, repeatable. For most axes these form a min-max band "
                         "and the subject's position in it is reported; for palette-distance "
                         "they are what the distance is measured to. Which references belong in "
                         "a band is a judgment -- that is why the only fallback is "
                         "brief.target_images, which is that judgment already written down")
    ap.add_argument("--job", type=resolve_job_dir,
                    help=job_dir_help("job directory; with --version, store the measurement in "
                                      "the ledger at versions[].measured with its fingerprint."))
    ap.add_argument("--version", type=int, help="which versions[].v the measurement belongs to")
    ap.add_argument("--json", action="store_true", help="print the result object as JSON")
    ap.add_argument("--list", action="store_true",
                    help="print each axis with the constants that define it, and stop")
    args = ap.parse_args()

    if args.list:
        for a in MEASURE_AXES:
            print(f"{a:<18} {FINGERPRINT[a]}")
            if CAVEAT.get(a):
                print(f"{'':<18} {CAVEAT[a]}")
        return 0
    if not args.axis:
        ap.error("--axis is required (or --list)")
    if not args.images:
        ap.error("give at least one image to measure")
    if args.version is not None and not args.job:
        ap.error("--version needs --job")

    against, band_from = list(args.against), "--against"
    if not against and args.job:
        targets = (load(args.job).get("brief") or {}).get("target_images") or []
        # Recorded relative to the job directory, the same rule ref_images follows.
        against = [args.job / t for t in targets]
        if against:
            band_from = "brief.target_images"
            missing = [str(t) for t in against if not t.exists()]
            if missing:
                print(f"brief.target_images points at missing file(s): {', '.join(missing)}",
                      file=sys.stderr)
                return 1

    band = None
    if against and args.axis != "palette-distance":
        vals = [measure(args.axis, r, []) for r in against]
        band = (min(vals), max(vals))

    results = []
    for img in args.images:
        if not img.exists():
            print(f"not found: {img}", file=sys.stderr)
            return 1
        val = measure(args.axis, img, against)
        row = {"file": img.name, "axis": args.axis, "value": round(val, 2),
               "fingerprint": FINGERPRINT[args.axis]}
        if against:
            row["refs_from"] = band_from
        if band:
            row["band"] = [round(band[0], 2), round(band[1], 2)]
            row["band_from"] = len(against)
            row["position"] = position(val, band)
        results.append(row)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for row in results:
            line = f"{row['file']:<16} {row['axis']:<16} {row['value']:.2f}{UNIT[row['axis']]}"
            if band:
                line += (f"   밴드 {row['band'][0]:.2f}–{row['band'][1]:.2f} "
                         f"(레퍼런스 {row['band_from']}장, min–max) · {row['position']}")
            print(line)
            if against:
                print(f"{'':<16} 레퍼런스 출처: {band_from} "
                      f"({', '.join(Path(r).name for r in against)})")
            print(f"{'':<16} {row['fingerprint']}")
        # A value pinned to the floor is not a small value; it is the axis failing to separate
        # anything down there. 15 of 21 images in one job tied at exactly 0.00.
        if any(r["value"] == 0.0 for r in results):
            print("  주의: 0.00은 '낮다'가 아니라 이 축이 그 구간에서 구별을 못 한다는 뜻일 수 "
                  "있다. 한 작업에서 21장 중 15장이 0.00 동률이었다.", file=sys.stderr)
        if CAVEAT.get(args.axis):
            print(f"  주의: {CAVEAT[args.axis]}", file=sys.stderr)

    if args.job and args.version is not None:
        job = load(args.job)
        hit = [v for v in job.get("versions", []) if v.get("v") == args.version]
        if not hit:
            print(f"job.json has no version {args.version}", file=sys.stderr)
            return 1
        stored = {k: results[0][k] for k in ("value", "fingerprint", "file") if k in results[0]}
        if band:
            stored["band"] = results[0]["band"]
            stored["band_from"] = results[0]["band_from"]
        hit[0].setdefault("measured", {})[args.axis] = stored
        save(args.job, job)
        print(f"recorded into v{args.version}.measured[{args.axis}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
