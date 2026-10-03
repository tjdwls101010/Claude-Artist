"""patch, render and doctor through the CLI. Render and doctor use the installed Google Chrome and skip when it cannot be launched."""

from __future__ import annotations

import json
import sys

import numpy as np
import pytest
from PIL import Image

from artist import ledger
from conftest import FAKES, make_png


def read(job):
    return json.loads((job / "job.json").read_text())


@pytest.fixture
def job(sandbox):
    assert sandbox.run("init", "job", "--request", "x", "--aspect", "4:5").code == 0
    sandbox.job = sandbox.data / "job"
    return sandbox


def attempt_from(job_dir, v, array, status="ok"):
    row = ledger.start_attempt(job_dir, v)
    name = row["id"] + ".png"
    Image.fromarray(array).save(job_dir / name)
    h, w = array.shape[:2]
    ledger.finish_attempt(job_dir, v, row["id"], {"status": status, "file": name, "w": w, "h": h})
    return name


def two_versions(sb, base_arr, from_arr):
    sb.run("add", sb.job, "--direction", "festival", "--method", "generate", stdin="x")
    sb.run("add", sb.job, "--direction", "fix", "--method", "generate", stdin="y")
    return attempt_from(sb.job, 1, base_arr), attempt_from(sb.job, 2, from_arr)


# ---- patch ------------------------------------------------------------------------------------

def test_patch_keeps_every_pixel_outside_the_region(job):
    rng = np.random.default_rng(0)
    base = rng.integers(0, 255, (250, 201, 4), dtype=np.uint8)
    source = rng.integers(0, 255, (250, 201, 4), dtype=np.uint8)
    b, f = two_versions(job, base, source)
    r = job.run("patch", job.job, "--base", b, "--from", f, "--region", "10,20,33,30", "--feather", "6")
    assert r.code == 0, r.raw
    assert r.out["version"] == 3 and r.out["file"] == "v3-1.png"
    out = np.asarray(Image.open(job.job / "v3-1.png"))
    # 10% of 201 = 20.1 -> 20 (down); 43% of 201 = 86.43 -> 87 (up); 20% of 250 = 50; 50% of 250 = 125
    x0, y0, x1, y1 = 20, 50, 87, 125
    outside = np.ones(out.shape[:2], bool)
    outside[y0:y1, x0:x1] = False
    assert np.array_equal(out[outside], base[outside])
    centre = (slice(y0 + 10, y1 - 10), slice(x0 + 10, x1 - 10))
    assert np.array_equal(out[centre], source[centre])
    entry = read(job.job)["versions"][2]
    assert entry["method"] == "patch" and entry["parent"] == 1 and entry["direction"] == "festival"
    assert entry["inputs"] == {"base": b, "from": f, "region_px": [x0, y0, x1, y1], "feather": 6}
    assert entry["attempts"][0]["status"] == "ok"


def test_patch_hard_edge_and_outside_diff(job):
    base = np.full((100, 80, 3), 100, np.uint8)
    source = np.full((100, 80, 3), 120, np.uint8)
    b, f = two_versions(job, base, source)
    r = job.run("patch", job.job, "--base", b, "--from", f, "--region", "0,0,50,50")
    assert r.code == 0, r.raw
    assert r.out["outside_diff"] == pytest.approx(20.0, abs=0.5)
    out = np.asarray(Image.open(job.job / r.out["file"]))
    assert (out[:50, :40] == 120).all() and (out[50:, :] == 100).all() and (out[:, 40:] == 100).all()


def test_patch_keeps_palette_transparency_outside_the_region(job):
    job.run("add", job.job, "--direction", "d", "--method", "generate", stdin="x")
    job.run("add", job.job, "--direction", "e", "--method", "generate", stdin="y")
    names = []
    for v, index in ((1, 0), (2, 1)):
        row = ledger.start_attempt(job.job, v)
        im = Image.new("P", (60, 60), index)
        im.putpalette([255, 0, 0, 0, 0, 255] + [0] * 762)
        im.info["transparency"] = 0
        im.save(job.job / (row["id"] + ".png"), transparency=0)
        ledger.finish_attempt(job.job, v, row["id"], {"status": "ok", "file": row["id"] + ".png", "w": 60, "h": 60})
        names.append(row["id"] + ".png")
    r = job.run("patch", job.job, "--base", names[0], "--from", names[1], "--region", "0,0,50,50")
    assert r.code == 0, r.raw
    out = np.asarray(Image.open(job.job / r.out["file"]).convert("RGBA"))
    base = np.asarray(Image.open(job.job / names[0]).convert("RGBA"))
    assert np.array_equal(out[30:, :], base[30:, :]) and out[50, 50, 3] == 0


def test_patch_refusals(job):
    b, f = two_versions(job, np.zeros((100, 80, 3), np.uint8), np.zeros((90, 80, 3), np.uint8))
    r = job.run("patch", job.job, "--base", b, "--from", f, "--region", "0,0,50,50")
    assert r.code == 1 and "size" in r.out["error"]
    altered = attempt_from(job.job, 2, np.zeros((100, 80, 3), np.uint8), status="prompt_altered")
    r = job.run("patch", job.job, "--base", b, "--from", altered, "--region", "0,0,50,50")
    assert r.code == 1 and "prompt_altered" in r.out["error"]
    r = job.run("patch", job.job, "--base", "elsewhere.png", "--from", f, "--region", "0,0,50,50")
    assert r.code == 1
    assert len(read(job.job)["versions"]) == 2


# ---- render -----------------------------------------------------------------------------------

def chrome_or_skip():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        try:
            p.chromium.launch(channel="chrome").close()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Google Chrome is not available: {exc}")


@pytest.fixture
def rjob(job):
    chrome_or_skip()
    make_png(job.job / "assets" / "photo.png", (40, 40), (0, 200, 0))
    return job


PAGE = """<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="assets/style.css">
<style>body {{ margin:0; width:300px; height:200px; background:#fff; font-family:{family}; }} .bg {{ position:absolute; left:200px; top:100px; width:40px; height:40px; background:url('assets/photo.png'); }}</style></head>
<body><div class="box"></div><div class="bg"></div><p style="position:absolute; left:10px; top:120px; margin:0; font-size:24px">{text}</p><img src="assets/photo.png" style="position:absolute; left:250px; top:10px"></body></html>"""


def write_page(job_dir, family="'Apple SD Gothic Neo'", text="한글날", colour="#ff0000", name="layout.html", extra=""):
    (job_dir / "assets").mkdir(exist_ok=True)
    (job_dir / "assets" / "style.css").write_text(f".box {{ position:absolute; left:0; top:0; width:100px; height:100px; background:{colour}; }}\n{extra}")
    path = job_dir / name
    path.write_text(PAGE.format(family=family, text=text))
    return path


def test_render_produces_exact_pixels_and_a_snapshot(rjob):
    html = write_page(rjob.job)
    r = rjob.run("render", rjob.job, "--html", html, "--size", "300x200", "--scale", "2", "--direction", "type")
    assert r.code == 0, r.raw + r.err
    assert r.out["px"] == [600, 400]
    with Image.open(rjob.job / r.out["file"]) as im:
        assert im.size == (600, 400)
        assert im.convert("RGB").getpixel((100, 100))[:3] == (255, 0, 0)  # the red box from the linked CSS, at scale 2
        assert im.convert("RGB").getpixel((440, 240))[1] > 150  # the CSS background image
    snap = rjob.job / "renders" / "v1"
    assert (snap / "layout.html").exists() and (snap / "assets" / "style.css").exists() and (snap / "assets" / "photo.png").exists()
    entry = read(rjob.job)["versions"][0]
    assert entry["method"] == "render" and entry["inputs"] == {"html_snapshot": "renders/v1/layout.html", "size": [300, 200], "scale": 2}
    assert rjob.opened() == [str(rjob.job / "contact-sheet.html")]


def test_rerender_after_an_edit_shows_the_edit(rjob):
    html = write_page(rjob.job, colour="#ff0000")
    assert rjob.run("render", rjob.job, "--html", html, "--size", "300x200", "--scale", "1", "--direction", "t").code == 0
    write_page(rjob.job, colour="#0000ff")
    r = rjob.run("render", rjob.job, "--html", html, "--size", "300x200", "--scale", "1", "--parent", "1")
    assert r.code == 0, r.raw
    with Image.open(rjob.job / r.out["file"]) as im:
        assert im.convert("RGB").getpixel((50, 50)) == (0, 0, 255)
    assert read(rjob.job)["versions"][1]["direction"] == "t"


def test_snapshot_reproduces_without_the_originals(rjob):
    html = write_page(rjob.job)
    first = rjob.run("render", rjob.job, "--html", html, "--size", "300x200", "--scale", "1", "--direction", "t")
    assert first.code == 0
    (rjob.job / "assets" / "photo.png").unlink()
    (rjob.job / "assets" / "style.css").unlink()
    again = rjob.run("render", rjob.job, "--html", rjob.job / "renders" / "v1" / "layout.html", "--size", "300x200", "--scale", "1", "--parent", "1")
    assert again.code == 0, again.raw
    a = np.asarray(Image.open(rjob.job / first.out["file"]))
    b = np.asarray(Image.open(rjob.job / again.out["file"]))
    assert np.array_equal(a, b)


@pytest.mark.parametrize("family, text", [("'NoSuchFamily'", "한글날"), ("'Helvetica'", "한글날 Hangul"), ("'NoSuchFamily', 'Apple SD Gothic Neo'", "한글날")])
def test_font_that_was_not_really_used_fails(rjob, family, text):
    html = write_page(rjob.job, family=family, text=text)
    r = rjob.run("render", rjob.job, "--html", html, "--size", "300x200", "--direction", "t")
    assert r.code == 1, r.raw
    problems = r.out["font_problems"]
    assert problems and problems[0]["intended"] == family.split(",")[0].strip("'")
    assert problems[0]["used"]
    attempt = read(rjob.job)["versions"][0]["attempts"][0]
    assert attempt["status"] == "executor_error" and "file" not in attempt


def test_missing_remote_and_outside_assets_stop_before_a_version(rjob):
    html = write_page(rjob.job, extra=".x { background:url('assets/missing.png'); }")
    r = rjob.run("render", rjob.job, "--html", html, "--size", "300x200", "--direction", "t")
    assert r.code == 1 and any("missing.png" in m for m in r.out["missing_assets"])
    outside = make_png(rjob.root / "outside.png")
    html = write_page(rjob.job, extra=f".x {{ background:url('https://example.com/a.png'); }} .y {{ background:url('{outside}'); }}")
    r = rjob.run("render", rjob.job, "--html", html, "--size", "300x200", "--direction", "t")
    assert r.code == 1
    refused = " ".join(r.out["refused_assets"])
    assert "https://example.com/a.png" in refused and "outside.png" in refused
    stray = rjob.root / "stray.html"
    stray.write_text("<p>x</p>")
    assert rjob.run("render", rjob.job, "--html", stray, "--size", "300x200", "--direction", "t").code == 1
    assert read(rjob.job)["versions"] == []


def test_scripts_cannot_reach_the_network(rjob):
    html = write_page(rjob.job)
    html.write_text(html.read_text().replace("</body>", "<script>fetch('https://example.com/f').catch(() => {}); window.open('https://example.org/p');</script></body>"))
    r = rjob.run("render", rjob.job, "--html", html, "--size", "300x200", "--direction", "t")
    assert r.code == 1
    refused = " ".join(r.out["refused_assets"])
    assert "example.com/f" in refused and "example.org/p" in refused


def test_absolute_paths_are_refused_even_inside_the_job(rjob):
    html = write_page(rjob.job, extra=f".x {{ background:url('{rjob.job / 'assets' / 'photo.png'}'); }}")
    r = rjob.run("render", rjob.job, "--html", html, "--size", "300x200", "--direction", "t")
    assert r.code == 1 and any("relative" in x for x in r.out["refused_assets"])


def test_assets_of_embedded_documents_are_snapshotted(rjob):
    make_png(rjob.job / "assets" / "inner.png", (20, 20), (0, 0, 255))
    (rjob.job / "inner.html").write_text('<!doctype html><meta charset="utf-8"><body style="margin:0"><img src="assets/inner.png"></body>')
    html = write_page(rjob.job)
    html.write_text(html.read_text().replace("</body>", '<iframe src="inner.html" style="position:absolute; left:0; top:150px; width:60px; height:50px; border:0"></iframe></body>'))
    r = rjob.run("render", rjob.job, "--html", html, "--size", "300x200", "--scale", "1", "--direction", "t")
    assert r.code == 0, r.raw
    assert (rjob.job / "renders" / "v1" / "assets" / "inner.png").exists()
    (rjob.job / "inner.html").write_text('<img src="assets/nope.png">')
    r = rjob.run("render", rjob.job, "--html", html, "--size", "300x200", "--direction", "t")
    assert r.code == 1 and any("nope.png" in m for m in r.out["missing_assets"])


def test_body_text_is_font_checked(rjob):
    (rjob.job / "b.html").write_text('<!doctype html><meta charset="utf-8"><body style="font-family:NoSuchFamily">한글날</body>')
    r = rjob.run("render", rjob.job, "--html", rjob.job / "b.html", "--size", "300x200", "--direction", "t")
    assert r.code == 1 and r.out["font_problems"][0]["intended"] == "NoSuchFamily"


@pytest.mark.parametrize("css", ["span, div { display:none !important; }", "body > *, html > * { display:none !important; visibility:hidden !important; } body > main, html > body { display:block !important; visibility:visible !important; }", "* { font-family: Courier !important; } p { font-family: Helvetica !important; }"])
def test_page_css_does_not_break_the_font_check(rjob, css):
    (rjob.job / "c.html").write_text(f'<!doctype html><meta charset="utf-8"><style>{css}</style><main><p style="font-family:Helvetica">Hello</p></main>')
    r = rjob.run("render", rjob.job, "--html", rjob.job / "c.html", "--size", "300x200", "--direction", "t")
    assert r.code == 0, r.raw


def test_data_uri_srcset_is_allowed(rjob):
    import base64
    import io
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (0, 0, 255)).save(buf, "PNG")
    uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    (rjob.job / "d.html").write_text(f'<!doctype html><meta charset="utf-8"><img srcset="{uri} 1x, assets/photo.png 2x">')
    r = rjob.run("render", rjob.job, "--html", rjob.job / "d.html", "--size", "300x200", "--direction", "t")
    assert r.code == 0, r.raw
    make_png(rjob.job / "assets" / "cover\u00a0wide.png", (4, 4))  # a no-break space is part of the URL, not a separator
    (rjob.job / "n.html").write_text('<!doctype html><meta charset="utf-8"><img srcset="assets/cover\u00a0wide.png 1x, assets/photo.png 2x">')
    r = rjob.run("render", rjob.job, "--html", rjob.job / "n.html", "--size", "300x200", "--direction", "t")
    assert r.code == 0, r.raw
    (rjob.job / "e.html").write_text('<!doctype html><meta charset="utf-8"><img srcset="assets/photo.png, assets/nope.png">')
    r = rjob.run("render", rjob.job, "--html", rjob.job / "e.html", "--size", "300x200", "--direction", "t")
    assert r.code == 1 and any("nope.png" in m for m in r.out["missing_assets"])


def test_a_chrome_that_will_not_start_closes_the_attempt(rjob):
    html = write_page(rjob.job)
    r = rjob.run("render", rjob.job, "--html", html, "--size", "300x200", "--direction", "t", env={"ARTIST_CHROME_PATH": "/nonexistent/chrome"})
    assert r.code == 1 and r.out and "error" in r.out
    assert read(rjob.job)["versions"][0]["attempts"][0]["status"] == "executor_error"


def test_page_errors_fail_the_render(rjob):
    html = write_page(rjob.job)
    html.write_text(html.read_text().replace("</body>", "<script>throw new Error('boom')</script></body>"))
    r = rjob.run("render", rjob.job, "--html", html, "--size", "300x200", "--direction", "t")
    assert r.code == 1 and any("boom" in e for e in r.out["console_errors"])


def test_render_needs_a_direction_without_parent(rjob):
    html = write_page(rjob.job)
    assert rjob.run("render", rjob.job, "--html", html, "--size", "300x200").code == 2


# ---- doctor -----------------------------------------------------------------------------------

@pytest.fixture
def doc(sandbox):
    launcher = sandbox.bin / "codex"
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKES / "fake_codex.py"}" "$@"\n')
    launcher.chmod(0o755)
    return sandbox


def test_doctor_ok(doc):
    chrome_or_skip()
    r = doc.run("doctor", timeout=300)
    assert r.code == 0, r.raw
    assert r.out["ok"] is True and r.out["codex"]["logged_in"] is True and r.out["chrome"]["ok"] is True
    families = {f["family"] for f in r.out["fonts"]}
    assert "Apple SD Gothic Neo" in families and "Helvetica" not in families


@pytest.mark.parametrize("env, word", [({"PATH": "/usr/bin:/bin"}, "codex"), ({"FAKE_CODEX_SCENARIO": "logged_out"}, "logged in"), ({"ARTIST_CHROME_PATH": "/nonexistent/chrome"}, "Chrome"), ({"ARTIST_DATA": "/nonexistent/data"}, "data")])
def test_doctor_blocking_problems_exit_3(doc, env, word):
    r = doc.run("doctor", env=env, timeout=300)
    assert r.code == 3, r.raw
    assert r.out["ok"] is False and any(word in p for p in r.out["problems"])
