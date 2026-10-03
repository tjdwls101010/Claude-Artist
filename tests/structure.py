"""The structure rules of the imagen skill's code, as a checker that returns every violation it finds.

Kept apart from the test so the test can run it on the real tree and on deliberately broken copies.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

PACKAGE = "imagen"

SKILL_TOP = {"SKILL.md", "references", "data", "scripts"}
SCRIPTS_TOP = {"cli.py", PACKAGE}
IGNORED = {"__pycache__", ".DS_Store"}

# Every unit in the package and what kind it is. A new unit has to be classified here, which is the point.
UNITS = {
    "codex": "system",
    "chrome": "system",
    "ledger": "store",
    "paths": "helper",
    "errors": "helper",
    "regions": "helper",
    "generate": "feature",
    "record": "feature",
    "sheet": "feature",
    "inspect": "feature",
    "compose": "feature",
    "doctor": "feature",
}

# Imports run one way: features → systems and stores → helpers.
ALLOWED_KINDS = {
    "feature": {"system", "store", "helper"},
    "system": {"helper"},
    "store": {"helper"},
    "helper": {"helper"},
}

# Features built on another feature: every write rebuilds the sheet.
FEATURE_EDGES = {("generate", "sheet"), ("record", "sheet"), ("compose", "sheet"), ("inspect", "sheet")}

SYS_PATH_MUTATORS = {"insert", "append", "extend", "remove", "pop", "clear"}


def _units_on_disk(pkg: Path) -> dict[str, Path]:
    found = {}
    for child in pkg.iterdir():
        if child.name in IGNORED or child.name == "__init__.py":
            continue
        if child.is_dir():
            found[child.name] = child
        elif child.suffix == ".py":
            found[child.stem] = child
    return found


def _imports(path: Path, module: str, is_package: bool) -> list[tuple[str, list[str], int]]:
    """(absolute module, imported names, line) for every import in one file, relative imports resolved."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, [], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = module.split(".") if is_package else module.split(".")[:-1]
                base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                target = ".".join(base + ([node.module] if node.module else []))
            else:
                target = node.module or ""
            out.append((target, [a.name for a in node.names], node.lineno))
    return out


def _sys_path_edits(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def is_sys_path(node) -> bool:
        return isinstance(node, ast.Attribute) and node.attr == "path" and isinstance(node.value, ast.Name) and node.value.id == "sys"

    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in SYS_PATH_MUTATORS and is_sys_path(node.func.value):
            lines.append(node.lineno)
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if is_sys_path(t) or (isinstance(t, ast.Subscript) and is_sys_path(t.value)):
                    lines.append(node.lineno)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "addsitedir":
            lines.append(node.lineno)
    return lines


def _targets(module: str, names: list[str], units: dict[str, Path]) -> list[tuple[str, bool]]:
    """Which units an import reaches, and whether it reaches past the unit's interface."""
    parts = module.split(".")
    if parts[0] != PACKAGE:
        return []
    if len(parts) == 1:
        return [(n, False) for n in names if n in units]
    unit = parts[1]
    is_subpackage = unit in units and units[unit].is_dir()
    past = False
    if is_subpackage:
        if len(parts) > 2:
            past = True
        else:
            inner = {p.stem for p in units[unit].iterdir() if p.suffix == ".py" and p.stem != "__init__"} | {p.name for p in units[unit].iterdir() if p.is_dir() and p.name not in IGNORED}
            past = any(n in inner for n in names)
    return [(unit, past)]


def check_tree(skill_dir: Path, tests_dir: Path | None = None) -> list[str]:
    problems: list[str] = []
    scripts = skill_dir / "scripts"
    pkg = scripts / PACKAGE

    for child in skill_dir.iterdir():
        if child.name not in SKILL_TOP | IGNORED:
            problems.append(f"skill folder: unexpected top-level name {child.name!r} (allowed: {sorted(SKILL_TOP)})")
    for child in scripts.iterdir():
        if child.name not in SCRIPTS_TOP | IGNORED:
            problems.append(f"scripts/: unexpected top-level name {child.name!r} (allowed: {sorted(SCRIPTS_TOP)})")
    if PACKAGE in sys.stdlib_module_names:
        problems.append(f"package name {PACKAGE!r} shadows a standard-library module")
    if not pkg.is_dir():
        return problems + ["scripts/imagen/ is missing"]

    units = _units_on_disk(pkg)
    for name in units:
        if name not in UNITS:
            problems.append(f"unit {name!r} is not classified in tests/structure.py UNITS")
    for name in UNITS:
        if name not in units:
            problems.append(f"unit {name!r} is classified but missing from the package")

    files: list[tuple[Path, str | None, str, bool]] = []  # path, owning unit, module name, is package __init__
    for path in sorted(pkg.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(scripts).with_suffix("")
        parts = list(rel.parts)
        is_pkg = parts[-1] == "__init__"
        if is_pkg:
            parts = parts[:-1]
        module = ".".join(parts)
        owner = parts[1] if len(parts) > 1 else None
        files.append((path, owner, module, is_pkg))

    for path, owner, module, is_pkg in files:
        rel = path.relative_to(skill_dir)
        for line in _sys_path_edits(path):
            problems.append(f"{rel}:{line}: edits sys.path")
        for target, names, line in _imports(path, module, is_pkg):
            for unit, past in _targets(target, names, units):
                if owner is None or unit == owner:
                    continue
                if past:
                    problems.append(f"{rel}:{line}: reaches past the interface of {unit!r} ({target} {names})")
                src_kind, dst_kind = UNITS.get(owner), UNITS.get(unit)
                if src_kind is None or dst_kind is None:
                    continue
                if src_kind == "feature" and dst_kind == "feature":
                    if (owner, unit) not in FEATURE_EDGES:
                        problems.append(f"{rel}:{line}: feature {owner!r} imports feature {unit!r}, which is not an allowed edge")
                elif dst_kind not in ALLOWED_KINDS[src_kind]:
                    problems.append(f"{rel}:{line}: {src_kind} {owner!r} imports {dst_kind} {unit!r}; imports run feature → system/store → helper")

    outside = [(scripts / "cli.py", "cli")]
    if tests_dir is not None:
        outside += [(p, p.stem) for p in sorted(tests_dir.rglob("*.py")) if "__pycache__" not in p.parts]
    for path, module in outside:
        if not path.exists():
            continue
        for line in _sys_path_edits(path):
            problems.append(f"{path.name}:{line}: edits sys.path")
        for target, names, line in _imports(path, module, False):
            for unit, past in _targets(target, names, units):
                if past:
                    problems.append(f"{path.name}:{line}: reaches past the interface of {unit!r} ({target} {names})")
    return problems
