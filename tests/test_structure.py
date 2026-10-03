"""The skill's code keeps the agreed tree: two top-level names, one-way imports, units used only through their interfaces, no sys.path edits."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from structure import check_tree

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / ".claude" / "skills" / "artist"


def test_real_tree_passes():
    assert check_tree(SKILL, ROOT / "tests") == []


@pytest.fixture
def broken(tmp_path):
    copy = tmp_path / "artist"
    shutil.copytree(SKILL, copy, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
    return copy


def append(path: Path, text: str) -> None:
    path.write_text(path.read_text(encoding="utf-8") + "\n" + text + "\n", encoding="utf-8")


def test_extra_top_level_name_fails(broken):
    (broken / "scripts" / "helpers.py").write_text("x = 1\n")
    (broken / "notes.txt").write_text("x\n")
    problems = check_tree(broken)
    assert any("scripts/: unexpected top-level name 'helpers.py'" in p for p in problems)
    assert any("skill folder: unexpected top-level name 'notes.txt'" in p for p in problems)


def test_reverse_import_fails(broken):
    append(broken / "scripts" / "artist" / "ledger" / "__init__.py", "from artist import sheet")
    assert any("store 'ledger' imports feature 'sheet'" in p for p in check_tree(broken))


def test_system_importing_store_fails(broken):
    append(broken / "scripts" / "artist" / "codex" / "__init__.py", "import artist.ledger")
    assert any("system 'codex' imports store 'ledger'" in p for p in check_tree(broken))


def test_disallowed_feature_edge_fails(broken):
    append(broken / "scripts" / "artist" / "sheet.py", "from artist import generate")
    assert any("feature 'sheet' imports feature 'generate'" in p for p in check_tree(broken))


def test_reaching_past_an_interface_fails(broken):
    inner = broken / "scripts" / "artist" / "codex" / "inner.py"
    inner.write_text("X = 1\n")
    append(broken / "scripts" / "artist" / "generate.py", "from artist.codex.inner import X")
    append(broken / "scripts" / "artist" / "record.py", "from artist.codex import inner")
    problems = check_tree(broken)
    assert any("generate.py" in p and "reaches past the interface of 'codex'" in p for p in problems)
    assert any("record.py" in p and "reaches past the interface of 'codex'" in p for p in problems)


def test_tests_reaching_past_an_interface_fails(broken, tmp_path):
    (broken / "scripts" / "artist" / "codex" / "inner.py").write_text("X = 1\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("from artist.codex.inner import X\n")
    assert any("test_x.py" in p and "reaches past" in p for p in check_tree(broken, tests))


def test_sys_path_edit_fails(broken):
    append(broken / "scripts" / "artist" / "paths.py", "import sys\nsys.path.insert(0, '/tmp')")
    append(broken / "scripts" / "cli.py", "sys.path[0:0] = ['/tmp']")
    problems = check_tree(broken)
    assert any("paths.py" in p and "edits sys.path" in p for p in problems)
    assert any("cli.py" in p and "edits sys.path" in p for p in problems)


def test_unclassified_unit_fails(broken):
    (broken / "scripts" / "artist" / "extra.py").write_text("x = 1\n")
    assert any("unit 'extra' is not classified" in p for p in check_tree(broken))
