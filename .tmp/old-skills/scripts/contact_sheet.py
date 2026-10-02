#!/usr/bin/env python3
"""Record what you saw, then build a self-contained HTML contact sheet so variants can be compared.

A terminal cannot show images, and three Preview windows cannot be compared. One page with
every version in a row, the prose prompt that made it, and the critique that followed is what turns
a slow generate-and-wait loop into a decision.

The layout is fixed here on purpose. Claude fills job.json; it never designs the page. Two jobs a
month apart should look like the same tool made them, and a sheet whose structure moves between
sessions cannot be scanned by habit.

Writing the critique and the choice lives here, not in a hand edit of job.json, because those two
fields are written *after* generate.py has rewritten the file -- which is exactly when a
string-matching edit against it fails. `--chosen` is checked against the version's actual image
filenames: naming "c" instead of "v2-c.png" used to highlight nothing and say nothing.

The page is a build artifact, not a live view -- a `file://` page cannot fetch its sibling
job.json (CORS). Every command that writes job.json rebuilds it in the same pass instead, and
that now holds by construction rather than by each caller remembering: the rebuild lives inside
job_schema.save(). Two writers used to skip it.

While a batch is in flight the page reloads itself, because the ledger gains a `running` row per
variant the moment the calls go out and every finished variant rewrites the page. The loop is
capped from the build time, so it stops on its own if the run dies.

Images are referenced by relative path, never inlined as base64: three 3MB PNGs become an 8MB
document that way, and a `file://` page reads its siblings from disk perfectly well.

Usage:
    contact_sheet.py --job <작업폴더> [--version N --critique "..." --chosen v2-b.png]
                     [--note "..."] [--final-file ... --final-saved-to ...] [--summary] [--open]
"""

from __future__ import annotations

import argparse
import html
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from job_schema import (job_dir_help, load, report,  # noqa: E402
                        resolve_job_dir, save, validate)

CSS = """
:root {
  color-scheme: light dark;
  --bg: #fbfaf8; --fg: #1c1a17; --muted: #6d675f; --line: #e0dcd5;
  --card: #ffffff; --accent: #9a4b1f; --ok: #2f6f43; --warn: #9a6b12;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #161513; --fg: #eae6e0; --muted: #9b948a; --line: #302d29;
          --card: #1e1c1a; --accent: #e0894f; --ok: #7dc196; --warn: #d9ad5c; }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 2rem 1.5rem 4rem; background: var(--bg); color: var(--fg);
  font: 15px/1.6 ui-sans-serif, -apple-system, "Helvetica Neue", sans-serif; }
.wrap { max-width: 1200px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 .25rem; letter-spacing: -.01em; }
.sub { color: var(--muted); font-size: .875rem; margin-bottom: 2rem; }
.version { border-top: 1px solid var(--line); padding: 1.5rem 0; }
.vhead { display: flex; flex-wrap: wrap; gap: .5rem 1rem; align-items: baseline; margin-bottom: .875rem; }
.vnum { font-weight: 650; font-size: 1.05rem; }
.vdir { color: var(--accent); }
.vmeta { color: var(--muted); font-size: .8125rem; }
.row { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; }
figure { margin: 0; background: var(--card); border: 1px solid var(--line); border-radius: 10px;
  overflow: hidden; }
figure.chosen { border-color: var(--accent); box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 30%, transparent); }
figure img { display: block; width: 100%; height: auto; cursor: zoom-in; background: var(--line); }
figcaption { padding: .5rem .625rem; font-size: .75rem; color: var(--muted);
  display: flex; flex-wrap: wrap; gap: .375rem .625rem; align-items: center; }
.tag { border-radius: 4px; padding: .0625rem .3125rem; background: var(--line); color: var(--fg); }
.path { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .6875rem; color: var(--fg); }
.jobdir { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .75rem; color: var(--muted);
  background: var(--card); border: 1px solid var(--line); border-radius: 6px; padding: .375rem .625rem;
  display: inline-block; margin-bottom: 1.5rem; overflow-x: auto; max-width: 100%; }
.tag.ok { background: color-mix(in srgb, var(--ok) 22%, transparent); color: var(--ok); }
.tag.bad { background: color-mix(in srgb, var(--accent) 22%, transparent); color: var(--accent); }
.tag.warn { background: color-mix(in srgb, var(--warn) 22%, transparent); color: var(--warn); }
.crit { margin-top: .875rem; padding: .75rem .875rem; border-left: 3px solid var(--accent);
  background: var(--card); border-radius: 0 6px 6px 0; }
details { margin-top: .75rem; }
summary { cursor: pointer; color: var(--muted); font-size: .8125rem; }
pre { overflow-x: auto; background: var(--card); border: 1px solid var(--line); border-radius: 6px;
  padding: .75rem; font-size: .8125rem; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
dialog { border: none; background: transparent; padding: 0; max-width: 96vw; max-height: 96vh; }
dialog::backdrop { background: rgba(0,0,0,.82); }
dialog img { max-width: 96vw; max-height: 96vh; width: auto; height: auto; cursor: zoom-out; }
.final { margin-top: 2rem; padding: 1rem; border: 1px solid var(--accent); border-radius: 10px; }
.schema-warn { border: 1px solid var(--accent); border-radius: 8px; padding: .75rem .875rem;
  margin-bottom: 1.5rem; background: color-mix(in srgb, var(--accent) 10%, transparent); }
.schema-warn ul { margin: .5rem 0 0; padding-left: 1.1rem; font-size: .8125rem; }
.brief { color: var(--muted); font-size: .8125rem; margin-bottom: .75rem;
  display: flex; flex-wrap: wrap; gap: .25rem .75rem; align-items: center; }
.axes { display: grid; grid-template-columns: max-content 1fr; gap: .25rem .75rem; margin: .5rem 0 0; }
.axes dt { color: var(--muted); font-size: .8125rem; }
.axes dd { margin: 0; font-size: .8125rem; }
.refs { grid-template-columns: repeat(auto-fit, minmax(140px, 180px)); margin-top: .5rem; }
.target { border: 1px solid var(--line); border-radius: .5rem; padding: .625rem .75rem;
          margin: .75rem 0; font-size: .8125rem; }
.fails { margin: .5rem 0 0; padding-left: 1.1rem; font-size: .8125rem; color: var(--muted); }
"""

JS = """
const dlg = document.getElementById('zoom');
document.querySelectorAll('figure img').forEach(img => {
  img.addEventListener('click', () => {
    dlg.querySelector('img').src = img.src;
    dlg.showModal();
  });
});
dlg.addEventListener('click', () => dlg.close());
"""

# Emitted only while some attempt is still `running`. A batch is minutes long and every finished
# variant rewrites this page, so the tab left open is the natural place to watch it -- but only if
# it reloads itself, since a file:// page cannot be pushed to.
#
# The cap keys off when this page was built, not off a timer this page starts. Each rebuild stamps
# a newer time, so a live run keeps refreshing; if generate.py dies the stamp stops moving and the
# loop stops on its own about ten minutes later. Nothing has to be stored between reloads, which
# also means no storage API to be blocked on file://.
WATCH_JS = """
const BUILT = %d, CAP = 600000, EVERY = 4000;
if (Date.now() - BUILT < CAP) setTimeout(() => location.reload(), EVERY);
"""


def e(x) -> str:
    return html.escape(str(x if x is not None else ""))


def img_tags(im: dict) -> str:
    bits = []
    bits.append('<span class="path">{}</span>'.format(e(im.get("file", ""))))
    dims = "{}x{}".format(im.get("w", "?"), im.get("h", "?"))
    bits.append('<span class="tag">{}</span>'.format(e(dims)))
    if "ratio" in im:
        cls = "tag"
        if im.get("aspect_ok") is True:
            cls = "tag ok"
        elif im.get("aspect_ok") is False:
            cls = "tag bad"
        label = str(im["ratio"])
        if im.get("aspect_ok") is False and im.get("aspect_requested"):
            label += " (wanted {})".format(im["aspect_requested"])
        bits.append('<span class="{}">{}</span>'.format(cls, e(label)))
    vb = im.get("verbatim")
    if vb and vb != "exact":
        bits.append('<span class="tag warn">verbatim {}</span>'.format(e(vb)))
    if im.get("elapsed_s") is not None:
        bits.append('<span class="tag">{}s</span>'.format(e(im["elapsed_s"])))
    return "".join(bits)


def render(job: dict, job_dir: Path, findings: dict | None = None) -> str:
    parts = [
        '<div class="wrap">',
        "<h1>{}</h1>".format(e(job.get("id", job_dir.name))),
    ]
    brief = job.get("brief", {})
    sub = " · ".join(str(x) for x in [
        brief.get("request"), brief.get("aspect"), job.get("created")] if x)
    parts.append('<div class="sub">{}</div>'.format(e(sub)))

    # The sheet confesses its own ledger's errors, so a broken record cannot look like a clean one.
    problems = (findings or {}).get("errors", []) + (findings or {}).get("warnings", [])
    if problems:
        parts.append('<div class="schema-warn"><strong>job.json 스키마 위반 {}건</strong>'
                     "<ul>{}</ul></div>".format(
                         len(problems), "".join("<li>{}</li>".format(e(p)) for p in problems)))

    bits = []
    if brief.get("purpose"):
        bits.append("용도: {}".format(e(brief["purpose"])))
    for t in brief.get("text") or []:
        bits.append('<span class="tag">{}</span>'.format(e(t)))
    if bits:
        parts.append('<div class="brief">{}</div>'.format("".join(bits)))
    if brief.get("notes"):
        parts.append("<details><summary>브리프 메모 ({}개)</summary><ul>{}</ul></details>".format(
            len(brief["notes"]), "".join("<li>{}</li>".format(e(n)) for n in brief["notes"])))

    if brief.get("target_images"):
        # Open, never behind a <details>. This exists because a session had the target on disk,
        # looked at it once at the start, and then judged twelve images against a memory of it
        # that had already drifted. A summary you have to click is a summary you recall instead.
        thumbs = "".join(
            '<figure><img src="{0}" alt="{0}" loading="lazy">'
            '<figcaption><span class="path">{0}</span></figcaption></figure>'.format(e(t))
            for t in brief["target_images"])
        parts.append('<div class="target"><strong>목표 ({}장)</strong> — 아래 변형은 전부 이것과 '
                     '나란히 본다<div class="row refs">{}</div></div>'.format(
                         len(brief["target_images"]), thumbs))

    parts.append('<div class="jobdir">{}/</div>'.format(e(job_dir.resolve())))

    for v in job.get("versions", []):
        parts.append('<div class="version">')
        meta = []
        running = sum(1 for a in v.get("attempts") or [] if a.get("status") == "running")
        if running:
            meta.append("{}장 생성 중".format(running))
        if v.get("parent") is not None:
            meta.append("v{} 에서 파생".format(v["parent"]))
        if v.get("method"):
            meta.append(v["method"])
        if v.get("change"):
            meta.append(v["change"])
        parts.append(
            '<div class="vhead"><span class="vnum">v{}</span>'
            '<span class="vdir">{}</span><span class="vmeta">{}</span></div>'.format(
                e(v.get("v", "?")), e(v.get("direction", "")), e(" · ".join(meta))))

        parts.append('<div class="row">')
        for im in v.get("attempts", []):
            if im.get("status") != "ok" or not im.get("file"):
                continue
            chosen = " chosen" if im.get("file") == v.get("chosen") else ""
            src = e(im.get("file", ""))
            parts.append(
                '<figure class="{}"><img src="{}" alt="{}" loading="lazy">'
                "<figcaption>{}{}</figcaption></figure>".format(
                    chosen.strip(), src, src,
                    '<span class="tag ok">chosen</span>' if chosen else "", img_tags(im)))
        parts.append("</div>")

        # Failures get a row of their own. They used to leave no trace at all, so a version that
        # was refused six times looked like a version nobody had run -- and the page is where that
        # was least recoverable, because an absent thumbnail says nothing about why.
        failed = [a for a in v.get("attempts", [])
                  if a.get("status") not in (None, "ok") and a.get("status") != "running"]
        if failed:
            rows = "".join(
                "<li><span class=\"tag bad\">{}</span> {}{}</li>".format(
                    e(a.get("status")), e(a.get("tag", "?")),
                    " · " + e(" · ".join(
                        f"{k}={v2 if not isinstance(v2, list) else ','.join(map(str, v2))}"
                        for k, v2 in (a.get("detail") or {}).items()
                        if k in ("moderation_stage", "categories", "returncode")))
                    if a.get("detail") else "")
                for a in failed)
            parts.append('<details><summary>실패 {}건</summary><ul class="fails">{}</ul>'
                         "</details>".format(len(failed), rows))

        if v.get("measured"):
            rows = "".join(
                "<dt>{}</dt><dd>{}{}<br><span class=\"vmeta\">{}</span></dd>".format(
                    e(axis), e(m.get("value")),
                    " · 밴드 {}–{} (레퍼런스 {}장)".format(
                        e(m["band"][0]), e(m["band"][1]), e(m.get("band_from", "?")))
                    if m.get("band") else "",
                    e(m.get("fingerprint", "")))
                for axis, m in v["measured"].items())
            parts.append('<details><summary>측정</summary><dl class="axes">{}</dl>'
                         "</details>".format(rows))

        if v.get("critique"):
            parts.append('<div class="crit">{}</div>'.format(e(v["critique"])))
        if v.get("axes"):
            rows = "".join("<dt>{}</dt><dd>{}</dd>".format(
                e(k), e(", ".join(val) if isinstance(val, list) else val))
                for k, val in v["axes"].items())
            parts.append("<details><summary>축</summary><dl class=\"axes\">{}</dl></details>".format(rows))
        if v.get("prompt"):
            parts.append(
                "<details><summary>프롬프트 전문 ({} 자)</summary><pre>{}</pre></details>".format(
                    len(v["prompt"]), e(v["prompt"])))
        if v.get("ref_images"):
            # Shown, not just named: a reference edit is only readable next to what it referenced.
            thumbs = "".join(
                '<figure><img src="{0}" alt="{0}" loading="lazy">'
                '<figcaption><span class="path">{0}</span></figcaption></figure>'.format(e(r))
                for r in v["ref_images"])
            parts.append('<details><summary>참조 ({}장)</summary>'
                         '<div class="row refs">{}</div></details>'.format(
                             len(v["ref_images"]), thumbs))
        parts.append("</div>")

    fin = job.get("final")
    if fin:
        parts.append(
            '<div class="final"><strong>최종 선정:</strong> <span class="path">{}</span>{}</div>'.format(
                e(fin.get("file", "")),
                '<br><span class="vmeta">저장 위치: </span><span class="path">{}</span>'.format(
                    e(fin["saved_to"])) if fin.get("saved_to") else ""))

    parts.append("</div>")
    parts.append('<dialog id="zoom"><img alt="확대"></dialog>')
    parts.append("<style>{}</style>".format(CSS))
    parts.append("<script>{}</script>".format(JS))
    if any(a.get("status") == "running"
           for v in job.get("versions", []) for a in v.get("attempts") or []):
        parts.append("<script>{}</script>".format(WATCH_JS % int(time.time() * 1000)))
    return "\n".join(parts)


def build_sheet(job_dir: Path) -> tuple[Path, dict]:
    """Read job.json, render, write. Returns the page path and the schema findings.

    generate.py calls this after every write to job.json, so the page can never be more than one
    command behind the ledger it describes.
    """
    job = load(job_dir)
    findings = validate(job, job_dir)
    # The page lives beside the PNGs so every src is a bare filename. There is no flag to put it
    # anywhere else: every write to job.json rebuilds it here, so a copy elsewhere would go stale
    # the next time and nothing would say so.
    out = job_dir / "contact-sheet.html"
    doc = ("<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
           "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
           "<title>{}</title></head><body>{}</body></html>").format(
        e(job.get("id", job_dir.name)), render(job, job_dir, findings))
    out.write_text(doc, encoding="utf-8")
    return out, findings


def annotate(args) -> int:
    """Apply --critique/--chosen/--final-* to job.json, refusing to save a record that breaks."""
    job = load(args.job)
    if args.version is not None:
        match = [v for v in job.get("versions", []) if v.get("v") == args.version]
        if not match:
            print("job.json has no version {} (has: {})".format(
                args.version, [v.get("v") for v in job.get("versions", [])]), file=sys.stderr)
            return 1
        if args.critique:
            match[0]["critique"] = args.critique
        if args.chosen:
            match[0]["chosen"] = args.chosen
    if args.note:
        job["brief"].setdefault("notes", []).extend(args.note)
    if args.target:
        have = job["brief"].setdefault("target_images", [])
        have.extend(t for t in args.target if t not in have)
    if args.final_file:
        job["final"] = {"file": args.final_file}
        if args.final_saved_to:
            job["final"]["saved_to"] = args.final_saved_to

    findings = validate(job, args.job)
    if findings["errors"]:
        print("refusing to write: job.json would not validate", file=sys.stderr)
        report(findings, sys.stderr)
        return 1
    save(args.job, job)
    return 0


def _attempt_bit(atts: list | None) -> str:
    """What one version's attempts amount to, in one field.

    Three states, not two. `not recorded` is a version whose tries were never kept -- schema 1, or
    a version nobody has run -- and `0/6` is a version that ran six times and kept nothing. The
    old line printed `not generated` for both, which is how a round that spent 25 generations
    read as a round that had not started.
    """
    if atts is None:
        return "not recorded"
    ok = sum(1 for a in atts if a.get("status") == "ok" and a.get("file"))
    running = sum(1 for a in atts if a.get("status") == "running")
    return "{}/{} kept{}".format(ok, len(atts), f", {running} running" if running else "")


def _failure_line(job: dict) -> str | None:
    """Every failure in the ledger, counted by kind.

    `0/6 kept` says a batch was spent and nothing about how, and the kind is what splits the
    prescription: `stage=input` means rewrite the prompt, `stage=output` means the image was
    blocked and retrying is the only lever, and a code nobody has seen means read the message
    that now rides along with it. Kinds and not a rate, on purpose -- a pass rate pools subjects
    that behave differently (one franchise character is refused every time, another goes through
    untouched) and the pooled number decides nothing.
    """
    kinds: dict[str, int] = {}
    marks: dict[str, list[str]] = {}
    for v in job.get("versions", []):
        for a in v.get("attempts") or []:
            status = a.get("status")
            if not status or status in ("ok", "running"):
                continue
            kinds[status] = kinds.get(status, 0) + 1
            d = a.get("detail") or {}
            mark = f"stage={d['moderation_stage']}" if d.get("moderation_stage") else d.get("code")
            if mark:
                marks.setdefault(status, []).append(str(mark))
    if not kinds:
        return None
    bits = []
    for status, n in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0])):
        seen = list(dict.fromkeys(marks.get(status, [])))
        bits.append("{} x{}{}".format(status, n, " ({})".format(", ".join(seen)) if seen else ""))
    return "실패 누적  " + " · ".join(bits)


def summarise(job: dict) -> str:
    """One line per version. What a reader wants back is what was tried and what was chosen,
    which is four short fields -- not the prose that produced them."""
    brief = job.get("brief", {})
    head = "{}  {}  {} version(s)  {} note(s)".format(
        job.get("id", "?"), brief.get("aspect", "?"),
        len(job.get("versions", [])), len(brief.get("notes") or []))
    lines = [head, brief.get("request", "")]
    for v in job.get("versions", []):
        bits = ["v{}".format(v.get("v")), str(v.get("direction", ""))]
        if v.get("method") == "reference-edit":
            bits.append("<- {}".format(", ".join(v.get("ref_images") or [])))
        atts = v.get("attempts")
        bits.append(_attempt_bit(atts))
        if v.get("chosen"):
            bits.append("chosen {}".format(v["chosen"]))
        if v.get("critique"):
            bits.append(v["critique"].splitlines()[0])
        lines.append("  ".join(b for b in bits if b))
    final = job.get("final")
    if final:
        lines.append("final  {}  -> {}".format(final.get("file"), final.get("saved_to", "-")))
    failures = _failure_line(job)
    if failures:
        lines.append(failures)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--job", type=resolve_job_dir, required=True,
                    help=job_dir_help("job directory containing job.json."))
    ap.add_argument("--open", action="store_true", dest="do_open",
                    help="open the built sheet in the default browser")
    rec = ap.add_argument_group(
        "record what you saw",
        "Written here rather than by editing job.json, because these land after generate.py has "
        "rewritten the file. --chosen is checked against that version's real image filenames, and "
        "only an attempt that finished ok and still owns its file is a candidate. Any of these "
        "rebuilds the sheet.")
    rec.add_argument("--version", type=int, help="which versions[].v to annotate")
    rec.add_argument("--critique", help="2-3 lines on what the images actually show")
    rec.add_argument("--chosen", metavar="FILE", help="e.g. v2-b.png")
    rec.add_argument("--final-file", metavar="FILE", help="the confirmed final image")
    rec.add_argument("--final-saved-to", metavar="PATH", help="where it was copied")
    rec.add_argument("--target", action="append", default=[], metavar="PATH",
                     help="add an image this job is judged against, repeatable. Same field "
                          "generate.py --init --target writes; this is the way in for a job that "
                          "was already running when its target turned up. The path is recorded "
                          "as given and resolves against the job directory")
    rec.add_argument("--note", action="append", default=[], metavar="TEXT",
                     help="append a constraint that spans the whole job, repeatable. This is "
                          "where a round's finding lands -- --init could only take notes before "
                          "the first generation, so anything learned by looking had no way in")
    ap.add_argument("--summary", action="store_true",
                    help="print the ledger's versions one line each, then every failure counted "
                         "by kind, and stop. A finished job.json runs 40-70KB of prose; this is "
                         "the cheap way to answer what has been tried so far and how it failed. "
                         "On its own it only reads -- but combined with a flag that "
                         "writes (--note/--critique/--chosen/--final-*) the write still rebuilds "
                         "the sheet, because every write does")
    args = ap.parse_args()

    if not (args.job / "job.json").exists():
        print("job.json not found in {}".format(args.job), file=sys.stderr)
        return 1
    if args.version is None and (args.critique or args.chosen):
        ap.error("--critique/--chosen need --version")
    if args.version is not None and not (args.critique or args.chosen):
        ap.error("--version needs --critique and/or --chosen")
    if args.final_saved_to and not args.final_file:
        ap.error("--final-saved-to needs --final-file")

    if args.version is not None or args.final_file or args.note or args.target:
        rc = annotate(args)
        if rc:
            return rc

    if args.summary:
        print(summarise(load(args.job)))
        return 0

    out, findings = build_sheet(args.job)
    print(out)
    # Errors do not stop the sheet: seeing the images is how you find out what to fix.
    report(findings, sys.stderr)

    if args.do_open:
        subprocess.run(["open", str(out)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
