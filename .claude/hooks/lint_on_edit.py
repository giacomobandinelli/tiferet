"""PostToolUse hook: format and lint a Python file after an edit (plan §8.2).

Registered for Edit, Write and MultiEdit. For a changed `.py` or `.pyi` file it runs
`uv run ruff format` and then `uv run ruff check --fix`, so the project's pinned ruff is used.
Lint findings that remain are returned to Claude as additional context. The hook never
blocks: it always exits with code 0. It uses only the standard library.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any

PYTHON_SUFFIXES = (".py", ".pyi")
TIMEOUT_SECONDS = 50
MAX_REPORT_CHARS = 4000


def changed_python_file(payload: dict[str, Any]) -> str | None:
    tool_input = payload.get("tool_input") or {}
    path = str(tool_input.get("file_path") or "")
    if not path.lower().endswith(PYTHON_SUFFIXES):
        return None
    path = os.path.join(payload.get("cwd") or os.getcwd(), path)
    return path if os.path.isfile(path) else None


def run(uv: str, args: list[str], cwd: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            [uv, "run", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def lint(path: str, root: str) -> str | None:
    """Format and fix `path`; return the remaining lint report, if any."""
    uv = shutil.which("uv")
    if uv is None:
        return None
    run(uv, ["ruff", "format", "--force-exclude", path], root)
    result = run(uv, ["ruff", "check", "--fix", "--force-exclude", path], root)
    if result is None or result.returncode == 0:
        return None
    report = (result.stdout + result.stderr).strip()[:MAX_REPORT_CHARS]
    return f"ruff check still reports issues in {path}:\n{report}"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        path = changed_python_file(payload) if isinstance(payload, dict) else None
        if path is None:
            return 0
        root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
        report = lint(path, root)
        if report:
            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": report,
                }
            }
            print(json.dumps(output))
    except Exception as error:  # never block an edit because the linter failed
        print(f"lint_on_edit: skipped ({error}).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
