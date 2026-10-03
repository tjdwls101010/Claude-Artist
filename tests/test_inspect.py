"""zoom and measure on synthetic images whose answers are known from how they were drawn, plus published colour-science values."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from artist import inspect
from conftest import make_png


def draw(path, size, ground=(255, 255, 255), rects=(), mode="RGB"):
    """rects: (x0, y0, x1, y1, colour) in pixels."""
    im = Image.new(mode, size, ground)
    for x0, y0, x1, y1, colour in rects:
        im.paste(colour, (x0, y0, x1, y1))
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


def run(sb, *args):
    r = sb.run(*args)
    assert r.code == 0, r.raw + r.err
    return r.out


# ---- zoom -------------------------------------------------------------------------------------

def test_zoom_grid_tiles_cover_the_image_without_resampling(sandbox):
    rng = np.random.default_rng(1)
    img = sandbox.root / "noise.png"
    Image.fromarray(rng.integers(0, 255, (301, 401, 3), dtype=np.uint8)).save(img)
    out = run(sandbox, "zoom", img)
    tiles = out["tiles"]
    assert len(tiles) == 4
    assert sum(t["w"] * t["h"] for t in tiles) == 401 * 301
    original = np.asarray(Image.open(img))
    for t in tiles:
        tile = np.asarray(Image.open(t["path"]))
        assert tile.shape == (t["h"], t["w"], 3)
        assert np.array_equal(tile, original[t["y"]:t["y"] + t["h"], t["x"]:t["x"] + t["w"]])
    assert {(t["x"], t["y"]) for t in tiles} == {(0, 0), (200, 0), (0, 150), (200, 150)}


def test_zoom_region_then_grid(sandbox):
    img = make_png(sandbox.root / "a.png", (400, 300))
    single = run(sandbox, "zoom", img, "--region", "25,25,50,50")["tiles"]
    assert [(t["x"], t["y"], t["w"], t["h"]) for t in single] == [(100, 75, 200, 150)]
    split = run(sandbox, "zoom", img, "--region", "25,25,50,50", "--grid", "2")["tiles"]
    assert sorted((t["x"], t["y"], t["w"], t["h"]) for t in split) == [(100, 75, 100, 75), (100, 150, 100, 75), (200, 75, 100, 75), (200, 150, 100, 75)]


def test_zoom_stores_tiles_per_run_without_overwriting(sandbox):
    sandbox.run("init", "job", "--request", "x", "--aspect", "1:1")
    job = sandbox.data / "job"
    img = make_png(job / "v1-1.png", (200, 200))
    first = run(sandbox, "zoom", img, "--job", "job")["tiles"]
    second = run(sandbox, "zoom", img, "--job", "job", "--grid", "3")["tiles"]
    assert all(t["path"].startswith(str(job / ".inspect" / "v1-1.png")) for t in first + second)
    assert all(Path(t["path"]).exists() for t in first + second)
    first_dirs, second_dirs = {Path(t["path"]).parent for t in first}, {Path(t["path"]).parent for t in second}
    assert len(first_dirs) == len(second_dirs) == 1 and first_dirs != second_dirs
    loose = run(sandbox, "zoom", make_png(sandbox.root / "pics" / "b.png"))["tiles"]
    assert loose[0]["path"].startswith(str(sandbox.root / "pics" / ".inspect" / "b.png"))


# ---- measure ----------------------------------------------------------------------------------

def measure(sb, *args):
    return run(sb, "measure", *args)["results"]


def test_zones_known_band(sandbox):
    # 900x1000: a black band across rows 100-299 (10%-30% of the height) on white.
    img = draw(sandbox.root / "z.png", (900, 1000), rects=[(0, 100, 900, 300, (0, 0, 0))])
    [res] = measure(sandbox, img, "--axis", "zones", "--bands", "0,10,30,100", "--box", "0,0,50,20")
    v = res["values"]
    assert v["bands"] == {"0-10": 0.0, "10-30": 1.0, "30-100": 0.0}
    assert v["first_ink_row"] == pytest.approx(10.0, abs=0.2) and v["last_ink_row"] == pytest.approx(30.0, abs=0.2)
    assert v["box_ink"] == pytest.approx(0.5, abs=0.01)
    assert v["ground_L"] == pytest.approx(100.0, abs=0.5)
    assert "ground_unreliable" not in v
    assert "short=900" in res["fingerprint"] and "ink_delta=10" in res["fingerprint"]


def test_zones_default_bands_and_resize(sandbox):
    # 1800x2000 halves to 900x1000; the black band sits in the second quarter.
    img = draw(sandbox.root / "z.png", (1800, 2000), rects=[(0, 500, 1800, 1000, (0, 0, 0))])
    [res] = measure(sandbox, img, "--axis", "zones")
    assert res["values"]["bands"]["0-25"] == 0.0
    assert res["values"]["bands"]["25-50"] == pytest.approx(1.0, abs=0.01)
    assert res["values"]["bands"]["50-75"] == pytest.approx(0.0, abs=0.01)


def test_contrast_sign_and_value(sandbox):
    # sRGB 119 grey is L* 50.0 (to 0.1); black ink is L* 0, white ink L* 100.
    dark = draw(sandbox.root / "d.png", (900, 900), ground=(119, 119, 119), rects=[(100, 100, 300, 300, (0, 0, 0))])
    light = draw(sandbox.root / "l.png", (900, 900), ground=(119, 119, 119), rects=[(100, 100, 300, 300, (255, 255, 255))])
    d, l = measure(sandbox, dark, light, "--axis", "contrast")
    assert d["values"]["contrast"] == pytest.approx(-50.0, abs=0.5)
    assert l["values"]["contrast"] == pytest.approx(50.0, abs=0.5)
    assert d["values"]["ground_L"] == pytest.approx(50.0, abs=0.2)


def test_transparent_pixels_are_left_out(sandbox):
    # Fully transparent black would read as ink everywhere if it were counted.
    img = draw(sandbox.root / "t.png", (900, 900), ground=(0, 0, 0, 0), rects=[(0, 0, 900, 450, (250, 250, 250, 255)), (0, 450, 900, 900, (250, 250, 250, 255))], mode="RGBA")
    rgba = Image.open(img)
    arr = np.asarray(rgba).copy()
    arr[400:500, :, :] = (0, 0, 0, 0)
    Image.fromarray(arr, "RGBA").save(img)
    [res] = measure(sandbox, img, "--axis", "zones", "--bands", "0,50,100")
    assert res["values"]["bands"] == {"0-50": 0.0, "50-100": 0.0}
    [edges] = measure(sandbox, img, "--axis", "edges")
    assert edges["values"]["all"] == 0.0


def test_edges_one_side(sandbox):
    # A 9px black strip on the left of a 900x900 white square: the border ring is 9px (1% of 900).
    img = draw(sandbox.root / "e.png", (900, 900), rects=[(0, 0, 9, 900, (0, 0, 0))])
    [res] = measure(sandbox, img, "--axis", "edges")
    v = res["values"]
    assert v["left"] == pytest.approx(1.0) and v["right"] == 0.0
    assert v["top"] == pytest.approx(9 / 900, abs=1e-3)
    assert v["all"] == pytest.approx(8100 / (4 * 9 * 900 - 4 * 81), abs=1e-3)


def test_colors_two_inks_and_target_distance(sandbox):
    img = draw(sandbox.root / "c.png", (800, 800), rects=[(0, 0, 400, 800, (255, 0, 0)), (400, 0, 800, 800, (0, 0, 255))])
    [res] = measure(sandbox, img, "--axis", "colors", "--target-color", "FF0000")
    clusters = res["values"]["clusters"]
    top = {c["hex"]: c["share"] for c in clusters[:2]}
    assert set(top) == {"#FF0000", "#0000FF"}
    assert all(s == pytest.approx(0.5, abs=0.03) for s in top.values())
    assert res["values"]["target"]["nearest"] == "#FF0000" and res["values"]["target"]["delta_e"] == pytest.approx(0.0, abs=0.5)
    assert "k=6" in res["fingerprint"] and "seed=0" in res["fingerprint"]


def test_colors_are_deterministic(sandbox):
    rng = np.random.default_rng(3)
    img = sandbox.root / "n.png"
    Image.fromarray(rng.integers(0, 255, (300, 300, 3), dtype=np.uint8)).save(img)
    a = measure(sandbox, img, "--axis", "colors")[0]["values"]
    b = measure(sandbox, img, "--axis", "colors")[0]["values"]
    assert a == b


def test_noisy_ground_is_flagged(sandbox):
    rng = np.random.default_rng(2)
    img = sandbox.root / "noise.png"
    Image.fromarray(rng.integers(0, 255, (900, 900, 3), dtype=np.uint8)).save(img)
    [res] = measure(sandbox, img, "--axis", "zones")
    assert res["values"]["ground_unreliable"] is True


def test_measure_argument_rules(sandbox):
    img = make_png(sandbox.root / "a.png")
    assert sandbox.run("measure", img, "--axis", "edges", "--bands", "0,50,100").code == 2
    assert sandbox.run("measure", img, "--axis", "zones", "--target-color", "FFFFFF").code == 2
    assert sandbox.run("measure", img, "--axis", "zones", "--bands", "10,50,100").code == 2
    r = sandbox.run("measure", sandbox.root / "missing.png", "--axis", "zones")
    assert r.code == 1


def test_measure_into_the_ledger(sandbox):
    from artist import ledger

    sandbox.run("init", "job", "--request", "x", "--aspect", "1:1")
    job = sandbox.data / "job"
    sandbox.run("add", job, "--direction", "d", "--method", "generate", stdin="x")
    row = ledger.start_attempt(job, 1)
    draw(job / "v1-1.png", (900, 900), rects=[(0, 0, 900, 90, (0, 0, 0))])
    ledger.finish_attempt(job, 1, row["id"], {"status": "ok", "file": "v1-1.png", "w": 900, "h": 900})
    run(sandbox, "measure", job / "v1-1.png", "--axis", "edges", "--job", "job")
    run(sandbox, "measure", job / "v1-1.png", "--axis", "colors", "--job", "job", "--target-color", "000000")
    stored = json.loads((job / "job.json").read_text())["measurements"]
    assert [(m["file"], m["version"], m["axis"]) for m in stored] == [("v1-1.png", 1, "edges"), ("v1-1.png", 1, "colors")]
    assert stored[1]["target_color"] == "#000000" and stored[0]["fingerprint"]
    assert "측정 2건" in (job / "contact-sheet.html").read_text()


# ---- colour science against published values ---------------------------------------------------

@pytest.mark.parametrize("rgb, lab", [((255, 255, 255), (100.0, 0.0, 0.0)), ((255, 0, 0), (53.24, 80.09, 67.20)), ((0, 0, 255), (32.30, 79.19, -107.86)), ((0, 0, 0), (0.0, 0.0, 0.0))])
def test_srgb_to_lab(rgb, lab):
    got = inspect.srgb_to_lab(np.array([[rgb]], dtype=float) / 255)[0, 0]
    assert got == pytest.approx(lab, abs=0.05)


# Sharma, Wu & Dalal (2005), "The CIEDE2000 color-difference formula", test data pairs 1, 2, 3, 7, 17.
@pytest.mark.parametrize("lab1, lab2, expected", [
    ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
    ((50.0, 3.1571, -77.2803), (50.0, 0.0, -82.7485), 2.8615),
    ((50.0, 2.8361, -74.0200), (50.0, 0.0, -82.7485), 3.4412),
    ((50.0, 0.0, 0.0), (50.0, -1.0, 2.0), 2.3669),
    ((50.0, 2.5, 0.0), (73.0, 25.0, -18.0), 27.1492),
])
def test_ciede2000_against_sharma(lab1, lab2, expected):
    assert inspect.delta_e2000(np.array(lab1), np.array(lab2)) == pytest.approx(expected, abs=1e-4)
