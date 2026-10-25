"""Fixtures for the hook tests: a temporary project tree and a fake uv executable."""

import os
import sys
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A minimal project tree with the protected files and a local holdout."""
    root = tmp_path / "project"
    (root / "research").mkdir(parents=True)
    (root / "research" / "preregistration.yaml").write_text("objective: {}\n")
    (root / "tests" / "locked").mkdir(parents=True)
    (root / "tests" / "locked" / "test_no_lookahead.py").write_text("def test_x() -> None: ...\n")
    (root / "src").mkdir()
    (root / "data" / "holdout").mkdir(parents=True)
    (root / "data" / "holdout" / "prices.parquet").write_bytes(b"")
    (root / "data" / "raw").mkdir()
    return root


@pytest.fixture
def fake_uv(tmp_path: Path) -> Callable[[int, str], tuple[dict[str, str], Path]]:
    """Put a fake `uv` first on PATH that logs its arguments and exits with a given code.

    Returns the environment to pass to the hook and the path of the argument log.
    """

    def make(exit_code: int, output: str = "") -> tuple[dict[str, str], Path]:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        log = tmp_path / "uv_calls.jsonl"
        script = bin_dir / "fake_uv.py"
        script.write_text(
            "import json, sys\n"
            f"with open({str(log)!r}, 'a') as f:\n"
            "    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            f"sys.stdout.write({output!r})\n"
            f"sys.exit({exit_code})\n"
        )
        if os.name == "nt":
            (bin_dir / "uv.cmd").write_text(f'@"{sys.executable}" "{script}" %*\n')
        else:
            launcher = bin_dir / "uv"
            launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
            launcher.chmod(0o755)
        env = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
        return env, log

    return make
