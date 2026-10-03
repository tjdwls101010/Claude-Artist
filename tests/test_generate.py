"""generate through the CLI against a fake codex that speaks the real protocol (argv, stdin envelope, events, -o file, generated_images, rollout)."""

from __future__ import annotations

import json
import os
import sys
import time

import pytest
from PIL import Image

from conftest import FAKES, make_png


@pytest.fixture
def gen(sandbox):
    launcher = sandbox.bin / "codex"
    # A /bin/sh launcher: a shebang cannot hold a path with a space in it (the repo lives in "claude artist").
    launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKES / 'fake_codex.py'}" "$@"\n')
    launcher.chmod(0o755)
    r = sandbox.run("init", "job", "--request", "poster", "--aspect", "3:4")
    assert r.code == 0, r.raw
    sandbox.job = sandbox.data / "job"
    return sandbox


def add(sb, *extra, prompt="Portrait 3:4. A yellow safety-sign poster reading \"퇴근\".", method="generate"):
    r = sb.run("add", sb.job, "--direction", "sign", "--method", method, *extra, stdin=prompt)
    assert r.code == 0, r.raw
    return r.out["version"]


def generate(sb, *args, scenario="ok", env=None):
    return sb.run("generate", sb.job, *args, env={"FAKE_CODEX_SCENARIO": scenario, **(env or {})})


def ledger_rows(sb, v=1):
    doc = json.loads((sb.job / "job.json").read_text())
    return next(e for e in doc["versions"] if e["v"] == v)["attempts"]


def calls(sb):
    path = sb.codex_home / "fake-calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_ok_copies_the_image_and_records_the_attempt(gen):
    add(gen)
    r = generate(gen, "--version", "1")
    assert r.code == 0, r.raw + r.err
    res = r.out["results"][0]
    assert res == {"version": 1, "attempt": "v1-1", "file": "v1-1.png", "status": "ok", "w": 1086, "h": 1448, "aspect_ok": True, "verbatim": "exact"}
    with Image.open(gen.job / "v1-1.png") as im:
        assert im.size == (1086, 1448)
    row = ledger_rows(gen)[0]
    assert row["status"] == "ok" and row["thread_id"] and "pid" in row
    assert r.out["sheet"].endswith("contact-sheet.html")
    assert "v1-1.png" in r.err


def test_regenerating_never_overwrites_and_opens_the_sheet_once(gen):
    add(gen)
    assert generate(gen, "--version", "1").code == 0
    first = (gen.job / "v1-1.png").read_bytes()
    first_runs = set((gen.job / ".runs").iterdir())
    r = generate(gen, "--version", "1", env={"FAKE_CODEX_SIZE": "1086x1440"})
    assert r.out["results"][0]["file"] == "v1-2.png"
    assert (gen.job / "v1-1.png").read_bytes() == first
    assert first_runs < set((gen.job / ".runs").iterdir())
    assert [a["id"] for a in ledger_rows(gen)] == ["v1-1", "v1-2"]
    assert gen.opened() == [str(gen.job / "contact-sheet.html")]


def test_eight_requests_run_at_most_six_at_once(gen):
    add(gen)
    add(gen)
    r = generate(gen, "--version", "1", "--version", "2", "--n", "4", env={"FAKE_CODEX_DELAY": "1.5"})
    assert r.code == 0, r.raw + r.err
    assert sorted(x["file"] for x in r.out["results"]) == sorted([f"v1-{k}.png" for k in range(1, 5)] + [f"v2-{k}.png" for k in range(1, 5)])
    events = sorted((c["t"], 1 if c["event"] == "start" else -1) for c in calls(gen))
    live = peak = 0
    for _, step in events:
        live += step
        peak = max(peak, live)
    assert peak == 6
    assert "8 requested" in r.out["summary"] and "8 ok" in r.out["summary"]


def test_aspect_miss_is_reported_not_failed(gen):
    add(gen)
    r = generate(gen, "--version", "1", env={"FAKE_CODEX_SIZE": "1254x1254"})
    assert r.code == 0
    assert r.out["results"][0]["status"] == "ok" and r.out["results"][0]["aspect_ok"] is False


@pytest.mark.parametrize("scenario, verbatim", [("normalized", "normalized"), ("unverified", "unverified")])
def test_verbatim_that_passes(gen, scenario, verbatim):
    add(gen)
    r = generate(gen, "--version", "1", scenario=scenario)
    assert r.out["results"][0]["status"] == "ok" and r.out["results"][0]["verbatim"] == verbatim
    if scenario == "unverified":
        assert "unverified" in r.err
    assert len(calls(gen)) == 2  # one exec: start and end


def test_rewrite_is_retried_once_and_both_runs_are_kept(gen):
    add(gen)
    r = generate(gen, "--version", "1", scenario="rewrite_ok")
    assert r.out["results"][0]["status"] == "ok" and r.out["results"][0]["verbatim"] == "exact"
    row = ledger_rows(gen)[0]
    assert row["retried"]["verbatim"] == "mismatch" and row["retried"]["thread_id"] != row["thread_id"]
    assert "elapsed_s" in row["retried"]


def test_rewrite_twice_keeps_the_image_but_it_cannot_be_chosen(gen):
    add(gen)
    r = generate(gen, "--version", "1", scenario="rewrite_twice")
    assert r.code == 1
    res = r.out["results"][0]
    assert res["status"] == "prompt_altered" and res["file"] == "v1-1.png" and (gen.job / "v1-1.png").exists()
    assert "Use case:" in r.err  # the diff of what codex changed
    assert gen.run("record", gen.job, "--version", "1", "--chosen", "v1-1.png").code == 1


@pytest.mark.parametrize("scenario, stage, advice", [("refuse_output", "output", "retry"), ("refuse_input", "input", "change the prompt"), ("refuse_unknown", None, "did not say")])
def test_refusals_carry_stage_and_prescription(gen, scenario, stage, advice):
    add(gen)
    r = generate(gen, "--version", "1", scenario=scenario)
    assert r.code == 1
    assert r.out["results"][0]["status"] == "refused"
    detail = ledger_rows(gen)[0]["detail"]
    assert detail["code"] == "moderation_blocked"
    assert detail.get("moderation_stage") == stage
    assert advice in r.err


def test_refusal_with_several_failures_reports_the_output_one_and_the_count(gen):
    add(gen)
    r = generate(gen, "--version", "1", scenario="refuse_multi")
    detail = ledger_rows(gen)[0]["detail"]
    assert detail["moderation_stage"] == "output" and detail["tries"] == 3 and detail["categories"] == ["violence", "gore"] and detail["message"] == "second"
    assert "3 tries" in r.err


@pytest.mark.parametrize("scenario", ["exit_nonzero", "bad_json"])
def test_executor_errors(gen, scenario):
    add(gen)
    r = generate(gen, "--version", "1", scenario=scenario)
    assert r.code == 1 and r.out["results"][0]["status"] == "executor_error"
    row = ledger_rows(gen)[0]
    if scenario == "exit_nonzero":
        assert row["detail"]["returncode"] == 1 and "something broke" in r.out["results"][0]["error"]


def test_path_fallback_finds_the_image_in_the_thread_folder(gen):
    add(gen)
    r = generate(gen, "--version", "1", scenario="path_fallback")
    assert r.out["results"][0]["status"] == "ok" and (gen.job / "v1-1.png").exists()


def test_image_outside_the_thread_folder_is_kept_but_not_choosable(gen):
    add(gen)
    r = generate(gen, "--version", "1", scenario="path_outside")
    assert r.code == 1
    assert r.out["results"][0]["status"] == "path_mismatch" and (gen.job / "v1-1.png").exists()


def test_timeout_kills_the_process_group_and_closes_the_row(gen):
    add(gen)
    started = time.time()
    r = generate(gen, "--version", "1", "--timeout", "2", scenario="hang")
    assert time.time() - started < 30
    assert r.code == 1 and r.out["results"][0]["status"] == "timeout"
    assert ledger_rows(gen)[0]["status"] == "timeout"
    child = int((gen.codex_home / "child.pid").read_text())
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(child, 0)


def test_logged_out_stops_before_any_attempt(gen):
    add(gen)
    r = generate(gen, "--version", "1", scenario="logged_out")
    assert r.code == 1 and "login" in r.out["error"]
    assert ledger_rows(gen) == [] and calls(gen) == []


def test_missing_version_or_codex(gen):
    r = generate(gen, "--version", "4")
    assert r.code == 1 and "no version 4" in r.out["error"]
    add(gen)
    (gen.bin / "codex").unlink()
    r = gen.run("generate", gen.job, "--version", "1", env={"PATH": f"{gen.bin}:/usr/bin:/bin"})
    assert r.code == 1 and "codex" in r.out["error"]


def test_transparent_elements_are_asked_for_and_checked(gen):
    add(gen, "--aspect", "none", "--transparent", prompt="A strip of torn masking tape on a transparent background.")
    r = generate(gen, "--version", "1", env={"FAKE_CODEX_SIZE": "1536x1024"})
    res = r.out["results"][0]
    assert calls(gen)[0]["transparent"] is True
    assert res["status"] == "ok" and "aspect_ok" not in res
    assert res["alpha"]["has_alpha"] is True and 0.7 < res["alpha"]["transparent_share"] < 0.8
    r = generate(gen, "--version", "1", env={"FAKE_CODEX_NO_ALPHA": "1"})
    assert r.out["results"][0]["alpha"] == {"has_alpha": False, "transparent_share": 0.0}
    assert "no alpha" in r.err


def test_refs_are_passed_as_absolute_paths(gen):
    ref = make_png(gen.job / "ref" / "photo.png")
    add(gen, "--ref", ref, method="edit")
    r = generate(gen, "--version", "1")
    assert r.code == 0
    assert calls(gen)[0]["refs"] == [str(ref.resolve())]
