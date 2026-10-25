"""Run the Claude Code hooks in .claude/hooks/ as Claude Code does.

Each hook is started as a subprocess with the hook input as JSON on stdin, and the tests
assert on its exit code, stdout and stderr.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HOOKS_DIR = Path(__file__).resolve().parents[2] / ".claude" / "hooks"


def run_hook(
    name: str,
    payload: dict[str, Any] | str,
    *,
    project: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    full_env = {**os.environ, "CLAUDE_PROJECT_DIR": str(project), **(env or {})}
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / name)],
        input=stdin,
        capture_output=True,
        text=True,
        env=full_env,
        timeout=60,
        check=False,
    )


def tool_payload(tool: str, tool_input: dict[str, Any], cwd: Path) -> dict[str, Any]:
    """A PreToolUse or PostToolUse input in the shape Claude Code sends."""
    return {
        "session_id": "test-session",
        "transcript_path": str(cwd / "transcript.jsonl"),
        "cwd": str(cwd),
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": tool_input,
        "tool_use_id": "toolu_test",
    }


def read_calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]
