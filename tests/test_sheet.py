"""The contact sheet in a real browser: the target stays pinned at the top, and an open tab picks up ledger changes by itself without losing its scroll position or closing an enlarged image."""

from __future__ import annotations

import time

import pytest

from artist import ledger
from conftest import make_png

playwright = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def page():
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Google Chrome is not available: {exc}")
        pg = browser.new_page(viewport={"width": 1000, "height": 700})
        yield pg
        browser.close()


def tall_job(sandbox):
    target = make_png(sandbox.root / "target.png", (300, 375), (30, 60, 140))
    r = sandbox.run("init", "job", "--request", "poster", "--aspect", "4:5", "--target", target)
    assert r.code == 0, r.raw
    job = sandbox.data / "job"
    for _ in range(6):
        assert sandbox.run("add", job, "--direction", "d", "--method", "generate", stdin="x").code == 0
    for v in range(1, 7):
        row = ledger.start_attempt(job, v)
        make_png(job / f"{row['id']}.png", (800, 1000))
        ledger.finish_attempt(job, v, row["id"], {"status": "ok", "file": f"{row['id']}.png", "w": 800, "h": 1000, "aspect_ok": True})
    sandbox.run("sheet", job)
    return job


def wait_for(predicate, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.2)
    return False


def test_target_is_pinned_above_the_versions(sandbox, page):
    job = tall_job(sandbox)
    page.goto((job / "contact-sheet.html").as_uri())
    page.mouse.wheel(0, 2500)
    page.wait_for_timeout(300)
    assert page.evaluate("window.scrollY") > 1000
    box = page.locator(".targets img").first.bounding_box()
    assert box is not None and box["y"] < 200


def test_open_tab_reloads_on_change_and_keeps_scroll(sandbox, page):
    job = tall_job(sandbox)
    page.goto((job / "contact-sheet.html").as_uri())
    page.evaluate("window.scrollTo(0, 1500)")
    page.wait_for_timeout(500)
    assert sandbox.run("record", job, "--version", "6", "--critique", "NEW-CRITIQUE-MARK").code == 0
    assert wait_for(lambda: page.locator("text=NEW-CRITIQUE-MARK").count() > 0), "the tab did not pick up the change"
    page.wait_for_timeout(300)
    assert abs(page.evaluate("window.scrollY") - 1500) < 5


def test_reload_waits_while_an_image_is_enlarged(sandbox, page):
    job = tall_job(sandbox)
    page.goto((job / "contact-sheet.html").as_uri())
    page.locator("figure img").first.click()
    assert page.evaluate("document.getElementById('zoom').open")
    assert sandbox.run("record", job, "--note", "WHILE-ZOOMED").code == 0
    page.wait_for_timeout(4500)
    assert page.evaluate("document.getElementById('zoom').open"), "the page reloaded under an open zoom"
    assert page.locator("text=WHILE-ZOOMED").count() == 0
    page.evaluate("document.getElementById('zoom').close()")
    assert wait_for(lambda: page.locator("text=WHILE-ZOOMED").count() > 0)


def test_sheet_open_uses_the_system_opener(sandbox):
    sandbox.run("init", "job", "--request", "x", "--aspect", "1:1")
    r = sandbox.run("sheet", "job", "--open")
    assert r.code == 0 and sandbox.opened() == [r.out["sheet"]]
