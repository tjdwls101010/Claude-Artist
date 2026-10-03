"""Shared fixtures: run the real CLI in a sandbox (temporary data folder, CODEX_HOME and PATH), and make test images."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / ".claude" / "skills" / "artist" / "scripts"
CLI = SCRIPTS / "cli.py"
FAKES = Path(__file__).resolve().parent / "fakes"


@dataclass
class Result:
    code: int
    out: dict | None
    err: str
    raw: str


@dataclass
class Sandbox:
    root: Path
    data: Path
    codex_home: Path
    bin: Path
    env: dict

    def run(self, *args, stdin: str | None = None, env: dict | None = None, timeout: float = 120) -> Result:
        proc = subprocess.run([sys.executable, str(CLI), *map(str, args)], input=stdin, capture_output=True, text=True, env={**self.env, **(env or {})}, timeout=timeout, cwd=self.root)
        try:
            out = json.loads(proc.stdout) if proc.stdout.strip() else None
        except json.JSONDecodeError:
            out = None
        return Result(proc.returncode, out, proc.stderr, proc.stdout)

    def popen(self, *args, stdin: str | None = None, env: dict | None = None) -> subprocess.Popen:
        return subprocess.Popen([sys.executable, str(CLI), *map(str, args)], stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env={**self.env, **(env or {})}, cwd=self.root)

    def opened(self) -> list[str]:
        log = self.root / "opened.log"
        return log.read_text().splitlines() if log.exists() else []


@pytest.fixture
def sandbox(tmp_path) -> Sandbox:
    data = tmp_path / "data"
    data.mkdir()
    home = tmp_path / "codex-home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # A fake `open`/`xdg-open` so tests never launch a browser; it records what would have been opened.
    for name in ("open", "xdg-open"):
        fake = bin_dir / name
        fake.write_text(f"#!/bin/sh\necho \"$1\" >> '{tmp_path / 'opened.log'}'\n")
        fake.chmod(0o755)
    env = {**os.environ, "ARTIST_DATA": str(data), "CODEX_HOME": str(home), "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "PYTHONPATH": str(SCRIPTS), "PYTHONUNBUFFERED": "1"}
    env.pop("FAKE_CODEX_SCENARIO", None)
    return Sandbox(tmp_path, data, home, bin_dir, env)


def make_png(path: Path, size=(400, 500), color=(240, 235, 225), mode="RGB") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fill = color if mode == "RGB" else (*color[:3], color[3] if len(color) > 3 else 255)
    Image.new(mode, size, fill).save(path)
    return path
