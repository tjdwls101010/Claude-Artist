# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pillow>=11",
#     "numpy>=2",
#     "fonttools>=4.50",
#     "playwright>=1.50",
# ]
# ///
"""artist: make images with Codex's built-in gpt-image, keep every attempt in a job ledger, and show them on a contact sheet."""

from __future__ import annotations

import argparse
import json
import re
import sys

from artist import compose, doctor, generate, inspect, paths, record, sheet
from artist.errors import Failure

TOP_EPILOG = """\
<job> is a short name or a path. A name with no leading /, ~ or . lands under the skill's data/ folder (subfolders allowed, e.g. 기념일/한글날); ./x, ../x, ~/x and /x are paths used as written. Other file arguments are ordinary paths, relative to your cwd or absolute; arguments that name a generated image (--chosen, --final, --base, --from) take its file name as the ledger lists it, e.g. v3-2.png.

Every command prints one JSON document on stdout and its progress on stderr. Exit codes: 0 success; 1 the command ran but produced no result (JSON has "error"); 2 bad arguments (JSON has "error" and "help"); 3 doctor found a blocking problem.

Environment: ARTIST_DATA overrides the data/ folder; CODEX_HOME is read the way codex reads it; ARTIST_CHROME_PATH points render at a Chrome binary other than the installed Google Chrome.

Run `<command> --help` for each command's arguments, output and failures."""


class Parser(argparse.ArgumentParser):
    """Argument errors go to stdout as JSON with exit 2, so the caller reads them the way it reads every other result."""

    def error(self, message: str):
        emit({"error": message, "help": f"{self.format_usage().strip()}  (run with --help for details)"})
        sys.exit(2)


def formatter(prog: str) -> argparse.HelpFormatter:
    # Wide enough that no help line is folded: each argument stays one line for grep.
    return argparse.RawDescriptionHelpFormatter(prog, width=1000, max_help_position=40)


def emit(doc: dict) -> None:
    print(json.dumps(doc, ensure_ascii=False, indent=1))


# ---- argument types --------------------------------------------------------------------------

def aspect_type(raw: str) -> str:
    if not re.fullmatch(r"[1-9]\d{0,2}:[1-9]\d{0,2}", raw):
        raise argparse.ArgumentTypeError(f"expected W:H such as 4:5, got {raw!r}")
    return raw


def aspect_or_none(raw: str) -> str:
    return raw if raw == "none" else aspect_type(raw)


def size_type(raw: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d{1,5})x(\d{1,5})", raw)
    if not m or not int(m[1]) or not int(m[2]):
        raise argparse.ArgumentTypeError(f"expected WxH in pixels such as 1080x1350, got {raw!r}")
    return int(m[1]), int(m[2])


def _numbers(raw: str, count: int, what: str) -> tuple[float, ...]:
    body = raw[:-1] if raw.endswith("%") else raw
    try:
        values = tuple(float(x) for x in body.split(","))
    except ValueError:
        values = ()
    if len(values) != count or any(v < 0 or v > 100 for v in values):
        raise argparse.ArgumentTypeError(f"expected {what} as {count} percentages 0-100, got {raw!r}")
    return values


def region_type(raw: str) -> tuple[float, float, float, float]:
    x, y, w, h = _numbers(raw, 4, "x,y,w,h")
    if w <= 0 or h <= 0 or x + w > 100 or y + h > 100:
        raise argparse.ArgumentTypeError(f"region {raw!r} must have positive size and stay inside the image")
    return x, y, w, h


def box_type(raw: str) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = _numbers(raw, 4, "x0,y0,x1,y1")
    if x1 <= x0 or y1 <= y0:
        raise argparse.ArgumentTypeError(f"box {raw!r} needs x1>x0 and y1>y0")
    return x0, y0, x1, y1


def bands_type(raw: str) -> list[float]:
    try:
        values = [float(x) for x in raw.split(",")]
    except ValueError:
        values = []
    if len(values) < 2 or values[0] != 0 or values[-1] != 100 or values != sorted(set(values)):
        raise argparse.ArgumentTypeError(f"expected increasing heights in percent from 0 to 100 such as 0,8,30,70,100, got {raw!r}")
    return values


def color_type(raw: str) -> str:
    body = raw.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", body):
        raise argparse.ArgumentTypeError(f"expected a hex colour such as 213F95, got {raw!r}")
    return "#" + body.upper()


def job_type(raw: str):
    try:
        return paths.resolve_job(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


# ---- parser ----------------------------------------------------------------------------------

def build_parser() -> Parser:
    ap = Parser(prog="cli.py", description=__doc__, epilog=TOP_EPILOG, formatter_class=formatter)
    sub = ap.add_subparsers(dest="command", metavar="<command>", parser_class=Parser)

    def command(name: str, summary: str, epilog: str) -> Parser:
        return sub.add_parser(name, help=summary, description=summary, epilog=epilog, formatter_class=formatter)

    p = command("init", "Create a job: its folder, job.json and contact sheet.", """\
output: {"job": <folder>, "sheet": <contact-sheet.html>, "inherited": {"aspect"?, "targets", "notes"}, "legacy_notes"?: [...]}

--like copies from another job only what outlives one job: the aspect, the targets (stored as absolute paths) and notes recorded as reusable. Notes in an older ledger (schema 1 or 2) carry no scope, so none is inherited; they come back as legacy_notes for you to read, and the ones that still hold go back in with `record --note ... --reusable`. An explicit --aspect beats the inherited one; explicit --note and --target are added after the inherited ones.

failures: exit 1 if job.json already exists (never overwritten) or --like names a folder without a readable job.json; exit 2 if no aspect is given and --like supplies none.""")
    p.add_argument("job", type=job_type, help="short name under data/ or a path")
    p.add_argument("--request", required=True, help="what was asked for, in the asker's words")
    p.add_argument("--aspect", type=aspect_type, help="delivery aspect ratio W:H such as 4:5; required unless --like supplies it")
    p.add_argument("--purpose", help="where the image will be used and what happens to it afterwards (finished / base for later work / blueprint)")
    p.add_argument("--text", action="append", default=[], metavar="S", help="a string that must appear exactly; repeatable")
    p.add_argument("--target", action="append", default=[], metavar="IMG", help="an image the results are judged against (shown at the top of the sheet, never fed to the model); repeatable")
    p.add_argument("--note", action="append", default=[], metavar="T", help="a note scoped to this job; repeatable (reusable notes go through record --note --reusable)")
    p.add_argument("--like", type=job_type, metavar="JOB", help="inherit aspect, targets and reusable notes from that job")

    p = command("add", "Record a version's intent before generating it. The prompt is read from stdin.", """\
output: {"version": N}

The prompt lives only in job.json; generate reads it from there. --method edit regenerates the whole image with the --ref images attached; it is not inpainting and keeps no pixels.

failures: exit 2 if stdin is empty or a terminal, --method edit has no --ref, or an argument is malformed; exit 1 if the job does not exist, a --ref file is missing, or --parent names no version.""")
    p.add_argument("job", type=job_type, help="short name under data/ or a path")
    p.add_argument("--direction", required=True, metavar="NAME", help="short memorable name of the direction this version belongs to")
    p.add_argument("--method", required=True, choices=["generate", "edit"], help="generate: from the prompt alone (refs optional, as style or content references); edit: rework the --ref image(s), at least one required")
    p.add_argument("--ref", action="append", default=[], metavar="IMG", help="image passed to the model with the prompt; repeatable")
    p.add_argument("--aspect", type=aspect_or_none, help="W:H the result is checked against (default: the brief's aspect); none for a cut-out element whose shape does not matter")
    p.add_argument("--transparent", action="store_true", help="ask image_gen for a transparent background; generate reports whether the PNG really has alpha")
    p.add_argument("--parent", type=int, metavar="N", help="the version this one revises")
    p.add_argument("--change", metavar="T", help="what changes against the parent and why")

    p = command("generate", "Generate recorded versions through Codex image_gen.", """\
output: {"summary": "...", "results": [{"version", "attempt", "file"?, "status", "w"?, "h"?, "aspect_ok"?, "alpha"?, "verbatim"?, "error"?}], "sheet": <path>}
Each finished variant also prints one line on stderr as it lands, with the failure kind, codex's reason and what to do next.

Every version is asked for --n images. All requests are queued and at most 6 run at once; one call takes roughly 40-180 seconds. Each try becomes an attempt row and a new file v{N}-{k}.png, where k keeps counting across runs, so generating again never overwrites an earlier image or log (logs: <job>/.runs/<run id>/). The first generate of a job opens the contact sheet in the browser, even if it fails; the open tab refreshes itself.

status: ok | refused (codex's moderation blocked it; the line says whether the prompt or the output image was blocked) | executor_error (codex itself failed) | timeout | prompt_altered (codex changed the prompt even after one retry; image kept, cannot be chosen) | path_mismatch (image came from outside this run's thread folder; kept, cannot be chosen) | silent_no_image (no image and no reason found) | interrupted (the generating process died).
verbatim compares the prompt with what codex reports it passed (its own report, not an observation of the tool call): exact or normalized (whitespace only) pass; unverified (no report) passes with a warning; mismatch is retried once with a stronger instruction.
aspect_ok compares the PNG with the version's aspect within 1% (absent for aspect none). alpha appears for --transparent versions: whether the PNG has an alpha channel and the share of fully transparent pixels.

What image_gen cannot do whatever the prompt says: no pixel size (about 1.57 MP in every ratio), no mask, no seed, no output path.

failures: exit 1 if every image failed, codex is missing or logged out, or a version does not exist or has no prompt; exit 0 when at least one image succeeded.""")
    p.add_argument("job", type=job_type, help="short name under data/ or a path")
    p.add_argument("--version", type=int, action="append", required=True, metavar="N", help="a recorded version to generate; repeatable")
    p.add_argument("--n", type=int, default=1, choices=range(1, 7), metavar="K", help="images per version, 1-6 (default 1)")
    p.add_argument("--timeout", type=int, default=300, metavar="S", help="seconds before one codex call is stopped and recorded as timeout (default 300)")

    p = command("record", "Write what you saw and decided: critique, choice, notes, targets, the final image and its export.", """\
output: {"written": [...], "exported"?: {"path", "size", "format", "crop"?}}

--chosen and --final accept only an attempt that finished ok (not prompt_altered or path_mismatch); --final finds its version from the file name, and --version, if also given, must match it. --export writes a delivery copy: crop, then resize to --size, then write to a temporary file and move it into place; the ledger records the final and the export only after all of that succeeded. A note is scoped to this job unless --reusable, which makes it inherited by `init --like`.

failures: exit 1 if the file is not an ok attempt of this job, the crop's aspect differs from --size by more than 1%, the export path already exists, or --format disagrees with the export file's extension; exit 2 if --critique/--chosen come without --version, --reusable without --note, or --export/--crop/--size/--format without --final.""")
    p.add_argument("job", type=job_type, help="short name under data/ or a path")
    p.add_argument("--version", type=int, metavar="N", help="the version --critique, --chosen or --final refers to")
    p.add_argument("--critique", metavar="T", help="what the version's images actually show and what to change; replaces the previous critique")
    p.add_argument("--chosen", metavar="FILE", help="the attempt picked from this version, e.g. v3-2.png")
    p.add_argument("--note", action="append", default=[], metavar="T", help="a finding or constraint; repeatable")
    p.add_argument("--reusable", action="store_true", help="the --note(s) state a preference that should carry over to later jobs (init --like), not a fact about this one")
    p.add_argument("--target", action="append", default=[], metavar="IMG", help="add an image the results are judged against; repeatable")
    p.add_argument("--final", metavar="FILE", help="the accepted image, e.g. v5-1.png")
    p.add_argument("--export", metavar="PATH", help="write a delivery copy of the final here (never overwrites)")
    p.add_argument("--crop", type=region_type, metavar="x,y,w,h%", help="crop before export, in percent of the image")
    p.add_argument("--size", type=size_type, metavar="WxH", help="export size in pixels (resampled with Lanczos)")
    p.add_argument("--format", choices=["png", "jpg"], help="export format (default: from the export file's extension, else png)")

    p = command("show", "Summarise the ledger.", """\
output: {"summary": "...", "brief": {...}, "versions": [{"v", "direction", "method", "ok", "failed", "running", "chosen"?, "critique_first_line"?}], "failures_by_kind": {status: count}, "final"?: {...}, "sheet": <path>}

failures: exit 1 if the job does not exist.""")
    p.add_argument("job", type=job_type, help="short name under data/ or a path")

    p = command("sheet", "Rebuild the contact sheet, optionally opening it.", """\
output: {"sheet": <path>}

The sheet is rebuilt automatically by every command that writes the ledger, and an open tab reloads itself when it changes; use this to open it again.

failures: exit 1 if the job does not exist.""")
    p.add_argument("job", type=job_type, help="short name under data/ or a path")
    p.add_argument("--open", action="store_true", help="open it in the default browser")

    p = command("zoom", "Cut an image into tiles at original resolution, to look at small detail.", """\
output: {"tiles": [{"path", "x", "y", "w", "h"}]} with x,y,w,h in pixels of the original.

The region, if given, is cut first and then split by the grid; nothing is resampled. Without --region the grid defaults to 2, with --region to a single tile. Tiles go to <job>/.inspect/<image name>/<time>/ (with --job) or .inspect/ beside the image, so a rerun never overwrites earlier tiles.

failures: exit 1 if the image cannot be read.""")
    p.add_argument("image", help="image file")
    p.add_argument("--grid", type=int, choices=[2, 3], help="split into grid x grid tiles")
    p.add_argument("--region", type=region_type, metavar="x,y,w,h%", help="only this part of the image, in percent")
    p.add_argument("--job", type=job_type, metavar="J", help="store the tiles in this job's folder")

    p = command("measure", "Measure what the eye gets systematically wrong: ink by zone, contrast, edge contact, palette.", """\
output: {"results": [{"file", "axis", "values", "fingerprint"}]}

Every image is first resized to a 900px short side (Lanczos) and converted to CIE L*a*b* (D65); pixels with alpha below 128 are left out of every axis. The ground is the most common L* (1-unit bins, after a 1px Gaussian blur); ink is any pixel whose L* differs from the ground by more than 10. If the most common bin holds under 15% of the pixels, values carry ground_unreliable: true (photographs, multi-colour grounds) and zones, contrast and edges are only indicative.
  zones     ink share per horizontal band (a row on a boundary belongs to the band above), the first and last inked row in percent of height (a row counts when at least 0.5% of it is ink), and with --box the ink share inside that box
  contrast  mean L* of the ink minus the ground L* (signed; null when there is no ink)
  edges     ink share in a border 1% of the short side wide: overall and per side
  colors    k-means (k=6, k-means++, seed 0, 20 iterations) on Lab pixels at a 200px short side: centres as hex with their area share; with --target-color, the CIEDE2000 distance from that colour to the nearest centre
The fingerprint lists every constant behind the numbers. With --job each result is also stored in the ledger (and shown on the sheet).

failures: exit 1 if an image cannot be read; exit 2 if --bands/--box are given for an axis other than zones, --target-color for an axis other than colors, or --version without --job.""")
    p.add_argument("image", nargs="+", help="image file(s)")
    p.add_argument("--axis", required=True, choices=["zones", "contrast", "edges", "colors"], help="what to measure")
    p.add_argument("--bands", type=bands_type, metavar="0,...,100", help="zones: band boundaries in percent of height (default 0,25,50,75,100)")
    p.add_argument("--box", type=box_type, metavar="x0,y0,x1,y1%", help="zones: also report the ink share inside this box")
    p.add_argument("--target-color", type=color_type, metavar="HEX", help="colors: report the CIEDE2000 distance to this colour")
    p.add_argument("--job", type=job_type, metavar="J", help="store the results in this job's ledger")
    p.add_argument("--version", type=int, metavar="N", help="with --job: the version the images belong to (default: found from the file name)")

    p = command("patch", "Paste one region of a corrected image onto the approved image, as a new version.", """\
output: {"version": N, "file": "v{N}-1.png", "outside_diff": <float>}

Use it when the approved image has a small defect and a reference edit fixed it but moved other things. Percent becomes pixels with the start rounded down and the end rounded up. --feather blends only inward from the region's edge, so every pixel outside the region keeps the approved image's decoded RGBA value exactly. outside_diff is the mean absolute lightness difference (0-255) between --base and --from outside the region, both reduced to 128px: a hint of how far the corrected image's composition moved, not a verdict. The new version's parent is the version of --base and its direction is inherited unless given.

failures: exit 1 if --base or --from is not an ok attempt of this job or the two images differ in size.""")
    p.add_argument("job", type=job_type, help="short name under data/ or a path")
    p.add_argument("--base", required=True, metavar="FILE", help="the approved image, e.g. v4-2.png")
    p.add_argument("--from", dest="source", required=True, metavar="FILE", help="the image whose region is taken, e.g. v6-1.png")
    p.add_argument("--region", required=True, type=region_type, metavar="x,y,w,h%", help="the region to take, in percent")
    p.add_argument("--feather", type=int, default=0, metavar="PX", help="width of the inward blend at the region's edge (default 0, a hard edge)")
    p.add_argument("--direction", metavar="NAME", help="direction name (default: the base's)")

    p = command("render", "Render an HTML layout you wrote into a PNG, as a new version.", """\
output: {"version": N, "file": "v{N}-1.png", "px": [W*scale, H*scale]}

The HTML must sit inside the job folder, and so must every file it references through src, stylesheet href, srcset, or url() in inline or linked CSS; data: URIs are fine. Remote URLs and paths outside the job are refused: copy the file into the job first, and use installed fonts by family name (doctor lists them). Before rendering, the HTML and everything it references are copied to renders/v{N}/ keeping their paths relative to the job folder, and the render reads that snapshot, so the version can be reproduced without the originals. --size is the CSS viewport; the PNG is size x scale pixels. Rendering waits for document.fonts.ready and every image to decode (30 s limit) in a fresh browser, so an edited file always renders anew.

Fonts are checked against what Chrome actually used: for each element with text, if the first family in its computed font-family is not among the fonts that drew it, or some of its glyphs fell back to another font, the render fails with the element, the intended family and the font actually used.

failures: exit 1 with a list (missing_assets, refused_assets, font_problems, console_errors) when an asset is missing, refused or failed to load, a font did not load or fell back, or the page logged an error; refused and missing assets stop before a version is created, problems during rendering record a failed attempt.""")
    p.add_argument("job", type=job_type, help="short name under data/ or a path")
    p.add_argument("--html", required=True, metavar="FILE", help="the HTML file inside the job folder")
    p.add_argument("--size", required=True, type=size_type, metavar="WxH", help="CSS viewport size, e.g. 1080x1350")
    p.add_argument("--scale", type=int, default=2, choices=[1, 2], help="device pixel ratio (default 2)")
    p.add_argument("--direction", metavar="NAME", help="direction name (default: the parent's; required without --parent)")
    p.add_argument("--parent", type=int, metavar="N", help="the version this render revises")

    p = command("doctor", "Check the environment generate and render need.", """\
output: {"ok": bool, "problems": [...], "codex": {"path", "version", "logged_in"}, "chrome": {"ok", "detail"}, "data_link": {"path", "target", "ok"}, "fonts": [{"family", "aliases"?, "weights"}]}

fonts lists installed families as CSS can name them, with their weights; aliases are other names the same files answer to. By default only families that contain Hangul are listed.

failures: exit 3 when codex is missing or logged out, Chrome cannot be launched, or data/ does not resolve to a folder.""")
    p.add_argument("--fonts", choices=["hangul", "all"], default="hangul", help="which installed families to list (default hangul)")
    return ap


# ---- dispatch --------------------------------------------------------------------------------

def run(args: argparse.Namespace, ap: Parser) -> tuple[dict, int]:
    cmd = args.command
    if cmd == "init":
        if not args.aspect and not args.like:
            ap.error("init: --aspect is required unless --like supplies it")
        return record.init(args.job, request=args.request, aspect=args.aspect, purpose=args.purpose, text=args.text, targets=[paths.resolve_input(t) for t in args.target], notes=args.note, like=args.like), 0
    if cmd == "add":
        if sys.stdin.isatty():
            ap.error("add: the prompt is read from stdin; pipe or heredoc it in")
        prompt = sys.stdin.read().strip()
        if not prompt:
            ap.error("add: the prompt on stdin is empty")
        if args.method == "edit" and not args.ref:
            ap.error("add: --method edit needs at least one --ref")
        return record.add(args.job, prompt=prompt, direction=args.direction, method=args.method, refs=[paths.resolve_input(r) for r in args.ref], aspect=args.aspect, transparent=args.transparent, parent=args.parent, change=args.change), 0
    if cmd == "generate":
        result = generate.run(args.job, versions=args.version, n=args.n, timeout=args.timeout)
        return result, 0 if any(r["status"] == "ok" for r in result["results"]) else 1
    if cmd == "record":
        if (args.critique or args.chosen) and args.version is None:
            ap.error("record: --critique and --chosen need --version")
        if args.reusable and not args.note:
            ap.error("record: --reusable needs --note")
        if (args.export or args.crop or args.size or args.format) and not args.final:
            ap.error("record: --export, --crop, --size and --format need --final")
        if (args.crop or args.size or args.format) and not args.export:
            ap.error("record: --crop, --size and --format need --export")
        if not any([args.critique, args.chosen, args.note, args.target, args.final]):
            ap.error("record: nothing to write; give --critique, --chosen, --note, --target or --final")
        return record.write(args.job, version=args.version, critique=args.critique, chosen=args.chosen, notes=args.note, reusable=args.reusable, targets=[paths.resolve_input(t) for t in args.target], final=args.final, export=paths.resolve_input(args.export) if args.export else None, crop=args.crop, size=args.size, fmt=args.format), 0
    if cmd == "show":
        return record.show(args.job), 0
    if cmd == "sheet":
        return {"sheet": str(sheet.rebuild(args.job, open_it=args.open))}, 0
    if cmd == "zoom":
        return inspect.zoom(paths.resolve_input(args.image), grid=args.grid, region=args.region, job_dir=args.job), 0
    if cmd == "measure":
        if args.axis != "zones" and (args.bands or args.box):
            ap.error("measure: --bands and --box belong to --axis zones")
        if args.axis != "colors" and args.target_color:
            ap.error("measure: --target-color belongs to --axis colors")
        if args.version is not None and args.job is None:
            ap.error("measure: --version needs --job")
        return inspect.measure([paths.resolve_input(i) for i in args.image], axis=args.axis, bands=args.bands, box=args.box, target_color=args.target_color, job_dir=args.job, version=args.version), 0
    if cmd == "patch":
        if args.feather < 0:
            ap.error("patch: --feather cannot be negative")
        return compose.patch(args.job, base=args.base, source=args.source, region=args.region, feather=args.feather, direction=args.direction), 0
    if cmd == "render":
        if args.direction is None and args.parent is None:
            ap.error("render: --direction is required without --parent")
        return compose.render(args.job, html=paths.resolve_input(args.html), size=args.size, scale=args.scale, direction=args.direction, parent=args.parent), 0
    if cmd == "doctor":
        result = doctor.check(fonts=args.fonts)
        return result, 0 if result["ok"] else 3
    ap.error("a command is required")
    raise AssertionError("unreachable")


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    try:
        doc, code = run(args, ap)
    except Failure as exc:
        emit(exc.payload())
        return 1
    emit(doc)
    return code


if __name__ == "__main__":
    sys.exit(main())
