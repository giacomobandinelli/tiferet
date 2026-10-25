"""Stop hook: keep the session open while the fast test suite fails (plan §8.2, §8.5).

Runs `uv run pytest -m "not slow" -q` in the project root. If it fails, the hook prints
`{"decision": "block", "reason": ...}` with the failure summary, so Claude continues and
fixes the failures. When `stop_hook_active` is true the session is already continuing
because of this hook, and stopping is allowed to avoid a loop. An empty test selection
(pytest exit code 5) counts as passing. The hook uses only the standard library.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Any

PYTEST_ARGS = ["run", "pytest", "-m", "not slow", "-q"]
TIMEOUT_SECONDS = 540
NO_TESTS_COLLECTED = 5
MAX_SUMMARY_LINES = 40


def summarize(output: str) -> str:
    """Keep the FAILED and ERROR lines and pytest's final summary line."""
    lines = [line for line in output.splitlines() if line.strip()]
    picked = [line for line in lines if line.startswith(("FAILED", "ERROR"))]
    picked = picked[:MAX_SUMMARY_LINES]
    if lines and (not picked or picked[-1] != lines[-1]):
        picked.append(lines[-1])
    return "\n".join(picked)


def run_fast_suite(root: str) -> str | None:
    """Return a failure summary, or None if the fast suite passes."""
    uv = shutil.which("uv")
    if uv is None:
        return "uv was not found on PATH, so the fast test suite could not run."
    try:
        result = subprocess.run(
            [uv, *PYTEST_ARGS],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"the fast test suite did not finish within {TIMEOUT_SECONDS} seconds."
    except OSError as error:
        return f"the fast test suite could not start ({error})."
    if result.returncode in (0, NO_TESTS_COLLECTED):
        return None
    return summarize(result.stdout + "\n" + result.stderr)


def decide(payload: dict[str, Any]) -> dict[str, str] | None:
    if payload.get("stop_hook_active"):
        return None
    root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    failure = run_fast_suite(root)
    if failure is None:
        return None
    reason = (
        'The fast test suite (uv run pytest -m "not slow" -q) fails, so the task is not '
        f"done (CLAUDE.md, definition of done):\n{failure}"
    )
    return {"decision": "block", "reason": reason}


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    decision = decide(payload if isinstance(payload, dict) else {})
    if decision is not None:
        print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main())
