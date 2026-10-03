"""init, add, record and show through the CLI; the ledger's guarantees through its interface (locking, attempt rows, interrupted runs, inheritance)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import unicodedata

import pytest
from PIL import Image

from imagen import ledger
from conftest import SCRIPTS, make_png


def init(sb, name="job", *extra):
    r = sb.run("init", name, "--request", "a poster", *(extra or ("--aspect", "4:5")))
    assert r.code == 0, r.raw
    return sb.data / name


def add(sb, job, *extra, prompt="Portrait 4:5. A poster.", method="generate"):
    r = sb.run("add", job, "--direction", "plain", "--method", method, *extra, stdin=prompt)
    assert r.code == 0, r.raw
    return r.out["version"]


def ok_attempt(job_dir, v, size=(400, 500), status="ok"):
    """Produce a finished attempt through the ledger's interface, the way generate does."""
    row = ledger.start_attempt(job_dir, v)
    name = row["id"] + ".png"
    make_png(job_dir / name, size)
    ledger.finish_attempt(job_dir, v, row["id"], {"status": status, "file": name, "w": size[0], "h": size[1]})
    return name


def read(job_dir):
    return json.loads((job_dir / "job.json").read_text())


# ---- init -------------------------------------------------------------------------------------

def test_init_creates_ledger_and_sheet_under_data(sandbox):
    r = sandbox.run("init", "기념일/한글날", "--request", "한글날 포스터", "--aspect", "4:5", "--text", "한글날", "--note", "지면은 밝게")
    assert r.code == 0, r.raw
    job = sandbox.data / "기념일" / "한글날"
    assert r.out["job"] == str(job.resolve())
    assert (job / "job.json").is_file() and (job / "contact-sheet.html").is_file()
    doc = read(job)
    assert doc["schema"] == 3 and doc["id"] == "한글날" and doc["brief"]["text"] == ["한글날"]
    assert doc["brief"]["notes"][0]["scope"] == "job"


def test_init_normalises_nfd_names(sandbox):
    nfd = unicodedata.normalize("NFD", "한글날")
    r = sandbox.run("init", nfd, "--request", "x", "--aspect", "1:1")
    assert r.code == 0, r.raw
    assert unicodedata.is_normalized("NFC", r.out["job"])


def test_init_never_overwrites(sandbox):
    init(sandbox)
    before = (sandbox.data / "job" / "job.json").read_bytes()
    r = sandbox.run("init", "job", "--request", "other", "--aspect", "1:1")
    assert r.code == 1 and "already exists" in r.out["error"]
    assert (sandbox.data / "job" / "job.json").read_bytes() == before


def test_init_needs_an_aspect(sandbox):
    r = sandbox.run("init", "job", "--request", "x")
    assert r.code == 2 and "help" in r.out
    legacy = sandbox.data / "old"
    legacy.mkdir()
    (legacy / "job.json").write_text(json.dumps({"schema": 1, "id": "old", "brief": {"request": "x"}, "versions": []}))
    r = sandbox.run("init", "job", "--request", "x", "--like", "old")
    assert r.code == 2 and "--aspect" in r.out["error"]


def test_bad_arguments_are_json_with_exit_2(sandbox):
    r = sandbox.run("init", "job", "--request", "x", "--aspect", "4x5")
    assert r.code == 2 and "W:H" in r.out["error"]


# ---- add --------------------------------------------------------------------------------------

def test_add_records_intent_with_brief_aspect(sandbox):
    job = init(sandbox)
    assert add(sandbox, job) == 1
    v = read(job)["versions"][0]
    assert v["prompt"] == "Portrait 4:5. A poster." and v["aspect"] == "4:5" and v["attempts"] == [] and v["transparent"] is False


def test_add_element_without_aspect(sandbox):
    job = init(sandbox)
    add(sandbox, job, "--aspect", "none", "--transparent")
    v = read(job)["versions"][0]
    assert v["aspect"] == "none" and v["transparent"] is True


def test_add_rejects_empty_prompt_and_edit_without_ref(sandbox):
    job = init(sandbox)
    assert sandbox.run("add", job, "--direction", "d", "--method", "generate", stdin="  \n").code == 2
    assert sandbox.run("add", job, "--direction", "d", "--method", "edit", stdin="x").code == 2
    assert read(job)["versions"] == []


def test_add_rejects_missing_ref_and_unknown_parent(sandbox):
    job = init(sandbox)
    r = sandbox.run("add", job, "--direction", "d", "--method", "edit", "--ref", sandbox.root / "nope.png", stdin="x")
    assert r.code == 1 and r.out["missing"]
    r = sandbox.run("add", job, "--direction", "d", "--method", "generate", "--parent", "7", stdin="x")
    assert r.code == 1 and "no version 7" in r.out["error"]


def test_add_stores_refs_relative_inside_the_job_and_absolute_outside(sandbox):
    job = init(sandbox)
    inside = make_png(job / "ref" / "a.png")
    outside = make_png(sandbox.root / "elsewhere" / "b.png")
    add(sandbox, job, "--ref", inside, "--ref", outside, method="edit")
    assert read(job)["versions"][0]["refs"] == ["ref/a.png", str(outside.resolve())]


# ---- record -----------------------------------------------------------------------------------

def test_record_critique_and_chosen(sandbox):
    job = init(sandbox)
    add(sandbox, job)
    name = ok_attempt(job, 1)
    r = sandbox.run("record", job, "--version", "1", "--critique", "line one\nline two", "--chosen", name)
    assert r.code == 0, r.raw
    v = read(job)["versions"][0]
    assert v["chosen"] == name and v["critique"].startswith("line one")


def test_record_refuses_choosing_a_file_that_is_not_ok(sandbox):
    job = init(sandbox)
    add(sandbox, job)
    add(sandbox, job)
    altered = ok_attempt(job, 1, status="prompt_altered")
    other = ok_attempt(job, 2)
    r = sandbox.run("record", job, "--version", "1", "--chosen", altered)
    assert r.code == 1 and "prompt_altered" in r.out["error"]
    r = sandbox.run("record", job, "--version", "1", "--chosen", other)
    assert r.code == 1 and "belongs to v2" in r.out["error"]
    r = sandbox.run("record", job, "--final", "v9-9.png")
    assert r.code == 1
    assert "chosen" not in read(job)["versions"][0] and "final" not in read(job)


def test_record_final_with_cropped_resized_export(sandbox):
    job = init(sandbox)
    add(sandbox, job)
    name = ok_attempt(job, 1, size=(1000, 1250))
    dest = sandbox.root / "delivery" / "poster.png"
    r = sandbox.run("record", job, "--final", name, "--export", dest, "--crop", "10,0,80,100", "--size", "640x1000")
    assert r.code == 0, r.raw
    with Image.open(dest) as im:
        assert im.size == (640, 1000)
    final = read(job)["final"]
    assert final["file"] == name and final["exported"][0]["path"] == str(dest) and final["exported"][0]["crop"] == [100, 0, 900, 1250]


def test_record_export_failures_leave_the_ledger_untouched(sandbox):
    job = init(sandbox)
    add(sandbox, job)
    name = ok_attempt(job, 1, size=(1000, 1250))
    dest = sandbox.root / "poster.png"
    r = sandbox.run("record", job, "--final", name, "--export", dest, "--size", "1080x1080")
    assert r.code == 1 and "1%" in r.out["error"]
    assert not dest.exists() and "final" not in read(job)
    dest.write_bytes(b"keep")
    r = sandbox.run("record", job, "--final", name, "--export", dest)
    assert r.code == 1 and "already exists" in r.out["error"]
    assert dest.read_bytes() == b"keep" and "final" not in read(job)
    r = sandbox.run("record", job, "--final", name, "--export", sandbox.root / "p.png", "--format", "jpg")
    assert r.code == 1 and "extension" in r.out["error"]
    assert not list(sandbox.root.glob(".*.tmp"))


def test_record_jpg_export_flattens_alpha(sandbox):
    job = init(sandbox)
    add(sandbox, job)
    row = ledger.start_attempt(job, 1)
    make_png(job / (row["id"] + ".png"), (100, 125), (0, 0, 0, 0), mode="RGBA")
    ledger.finish_attempt(job, 1, row["id"], {"status": "ok", "file": row["id"] + ".png"})
    dest = sandbox.root / "p.jpg"
    assert sandbox.run("record", job, "--final", row["id"] + ".png", "--export", dest).code == 0
    with Image.open(dest) as im:
        assert im.mode == "RGB" and im.getpixel((5, 5)) == (255, 255, 255)


def test_record_argument_rules(sandbox):
    job = init(sandbox)
    assert sandbox.run("record", job, "--critique", "x").code == 2
    assert sandbox.run("record", job, "--reusable").code == 2
    assert sandbox.run("record", job, "--export", "x.png").code == 2
    assert sandbox.run("record", job).code == 2


def test_record_notes_keep_their_scope(sandbox):
    job = init(sandbox)
    assert sandbox.run("record", job, "--note", "this poster only").code == 0
    assert sandbox.run("record", job, "--note", "prefers real photos", "--reusable").code == 0
    notes = read(job)["brief"]["notes"]
    assert [(n["text"], n["scope"]) for n in notes] == [("this poster only", "job"), ("prefers real photos", "reusable")]


# ---- locking, attempts, interrupted -------------------------------------------------------------

def test_concurrent_writers_lose_nothing(sandbox):
    job = init(sandbox)
    procs = [sandbox.popen("record", job, "--note", f"note {i}") for i in range(12)]
    for p in procs:
        p.communicate(timeout=60)
        assert p.returncode == 0
    texts = sorted(n["text"] for n in read(job)["brief"]["notes"])
    assert texts == sorted(f"note {i}" for i in range(12))


def test_concurrent_attempts_get_unique_ids(sandbox):
    job = init(sandbox)
    add(sandbox, job)
    ids, errors = [], []

    def start():
        try:
            ids.append(ledger.start_attempt(job, 1)["id"])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=start) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and sorted(ids) == sorted(f"v1-{k}" for k in range(1, 11))
    assert len(read(job)["versions"][0]["attempts"]) == 10


def test_an_attempt_finishes_once(sandbox):
    job = init(sandbox)
    add(sandbox, job)
    row = ledger.start_attempt(job, 1)
    ledger.finish_attempt(job, 1, row["id"], {"status": "refused"})
    with pytest.raises(Exception, match="already finished"):
        ledger.finish_attempt(job, 1, row["id"], {"status": "ok"})


def test_dead_running_attempt_becomes_interrupted(sandbox):
    job = init(sandbox)
    add(sandbox, job)
    code = f"from pathlib import Path; from imagen import ledger; ledger.start_attempt(Path({str(job)!r}), 1)"
    subprocess.run([sys.executable, "-c", code], check=True, env={**os.environ, "PYTHONPATH": str(SCRIPTS)})
    assert read(job)["versions"][0]["attempts"][0]["status"] == "running"
    r = sandbox.run("show", job)
    assert r.out["failures_by_kind"] == {"interrupted": 1}
    assert read(job)["versions"][0]["attempts"][0]["status"] == "interrupted"


def test_writes_that_would_not_validate_are_refused(sandbox):
    job = init(sandbox)
    before = (job / "job.json").read_bytes()
    with pytest.raises(Exception, match="would not validate"):
        with ledger.edit(job) as doc:
            doc["brief"]["colour"] = "blue"
    assert (job / "job.json").read_bytes() == before


def test_old_schema_ledgers_are_not_written(sandbox):
    old = sandbox.data / "old"
    old.mkdir()
    (old / "job.json").write_text(json.dumps({"schema": 2, "id": "old", "brief": {"request": "x", "aspect": "4:5"}, "versions": []}))
    r = sandbox.run("record", "old", "--note", "x")
    assert r.code == 1 and "--like" in r.out["error"]


# ---- --like -----------------------------------------------------------------------------------

def legacy_job(sandbox, schema):
    """A ledger shaped like the earlier skill's (fields taken from the real schema 1 and 2 ledgers)."""
    base = sandbox.data / "기념일"
    make_png(base / "예시" / "target.png")
    folder = base / f"old{schema}"
    folder.mkdir(parents=True)
    brief = {"request": "광복절 웹포스터", "aspect": "4:5", "purpose": "SNS", "text": ["광복절"], "notes": ["예약 구역: 상단 0~8% 로고 띠", "크기는 분수가 아니라 위치 앵커로"]}
    if schema == 2:
        brief["target_images"] = ["../예시/target.png"]
    versions = [{"v": 1, "direction": "d", "prompt": "p", "method": "regenerate", "parent": None, "change": None, ("images" if schema == 1 else "attempts"): []}]
    (folder / "job.json").write_text(json.dumps({"schema": schema, "id": f"old{schema}", "created": "2026-08-01", "brief": brief, "versions": versions}, ensure_ascii=False))
    return folder


@pytest.mark.parametrize("schema", [1, 2])
def test_like_from_old_schemas_maps_aspect_and_targets_and_only_shows_notes(sandbox, schema):
    source = legacy_job(sandbox, schema)
    r = sandbox.run("init", "new", "--request", "한글날", "--like", source)
    assert r.code == 0, r.raw
    brief = read(sandbox.data / "new")["brief"]
    assert brief["aspect"] == "4:5"
    assert "notes" not in brief
    assert r.out["legacy_notes"] == ["예약 구역: 상단 0~8% 로고 띠", "크기는 분수가 아니라 위치 앵커로"]
    if schema == 2:
        expected = str((sandbox.data / "기념일" / "예시" / "target.png").resolve())
        assert brief["targets"] == [expected] and r.out["inherited"]["targets"] == [expected]
    else:
        assert "targets" not in brief


def test_like_from_schema_3_inherits_only_reusable_notes(sandbox):
    target = make_png(sandbox.root / "t.png")
    source = init(sandbox, "first", "--aspect", "3:4", "--target", target)
    sandbox.run("record", source, "--note", "only for first")
    sandbox.run("record", source, "--note", "likes careful illustration", "--reusable")
    r = sandbox.run("init", "second", "--request", "x", "--like", "first", "--aspect", "1:1", "--note", "mine")
    assert r.code == 0, r.raw
    brief = read(sandbox.data / "second")["brief"]
    assert brief["aspect"] == "1:1"
    assert [(n["text"], n["scope"]) for n in brief["notes"]] == [("likes careful illustration", "reusable"), ("mine", "job")]
    assert brief["targets"] == [str(target.resolve())]
    assert "legacy_notes" not in r.out


# ---- show -------------------------------------------------------------------------------------

def test_show_summarises(sandbox):
    job = init(sandbox)
    add(sandbox, job)
    name = ok_attempt(job, 1)
    row = ledger.start_attempt(job, 1)
    ledger.finish_attempt(job, 1, row["id"], {"status": "refused", "detail": {"code": "moderation_blocked", "moderation_stage": "output"}})
    sandbox.run("record", job, "--version", "1", "--critique", "first line\nsecond", "--chosen", name)
    r = sandbox.run("show", job)
    assert r.code == 0
    assert r.out["versions"] == [{"v": 1, "direction": "plain", "method": "generate", "ok": 1, "failed": 1, "running": 0, "chosen": name, "critique_first_line": "first line"}]
    assert r.out["failures_by_kind"] == {"refused": 1}
    assert "1/2 attempts ok" in r.out["summary"]


def test_show_missing_job(sandbox):
    r = sandbox.run("show", "nothing")
    assert r.code == 1 and "init" in r.out["error"]


def test_copying_a_job_folder_keeps_relative_refs_working(sandbox):
    job = init(sandbox)
    make_png(job / "ref.png")
    add(sandbox, job, "--ref", job / "ref.png", method="edit")
    moved = sandbox.data / "moved"
    shutil.copytree(job, moved)
    assert read(moved)["versions"][0]["refs"] == ["ref.png"] and (moved / "ref.png").exists()


def test_a_reused_pid_does_not_keep_an_attempt_running(sandbox):
    job = init(sandbox)
    add(sandbox, job)
    stranger = subprocess.Popen(["sleep", "30"])  # alive, but started long after the attempt
    try:
        with ledger.edit(job) as doc:
            doc["versions"][0]["attempts"].append({"id": "v1-1", "status": "running", "started": "2026-01-01T00:00:00+09:00", "pid": stranger.pid})
        assert sandbox.run("show", job).out["failures_by_kind"] == {"interrupted": 1}
    finally:
        stranger.kill()


def test_missing_image_file_is_a_json_error(sandbox):
    job = init(sandbox)
    add(sandbox, job)
    name = ok_attempt(job, 1)
    (job / name).unlink()
    r = sandbox.run("record", job, "--final", name, "--export", sandbox.root / "out.png")
    assert r.code == 1 and r.out and "error" in r.out


def test_own_pid_on_an_old_attempt_is_not_this_process(sandbox):
    job = init(sandbox)
    add(sandbox, job)
    with ledger.edit(job) as doc:
        doc["versions"][0]["attempts"].append({"id": "v1-1", "status": "running", "started": "2000-01-01T00:00:00+09:00", "pid": os.getpid()})
    assert ledger.read(job)["versions"][0]["attempts"][0]["status"] == "interrupted"
    row = ledger.start_attempt(job, 1)
    assert ledger.read(job)["versions"][0]["attempts"][1]["status"] == "running" and row["id"] == "v1-2"
