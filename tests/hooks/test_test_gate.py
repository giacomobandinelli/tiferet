"""test_gate.py blocks stopping while the fast suite fails (plan §8.2, §8.5)."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from hooks.hookrun import read_calls, run_hook

HOOK = "test_gate.py"

FakeUv = Callable[[int, str], tuple[dict[str, str], Path]]

FAILING_OUTPUT = (
    "..F.\n"
    "=================================== FAILURES ===================================\n"
    "____________________________ test_tax ____________________________\n"
    "    assert 1 == 2\n"
    "=========================== short test summary info ============================\n"
    "FAILED tests/test_tax.py::test_tax - assert 1 == 2\n"
    "1 failed, 3 passed in 0.12s\n"
)


def stop_payload(project: Path, *, active: bool) -> dict[str, Any]:
    return {
        "session_id": "test-session",
        "transcript_path": str(project / "transcript.jsonl"),
        "cwd": str(project),
        "permission_mode": "default",
        "hook_event_name": "Stop",
        "stop_hook_active": active,
    }


def test_failing_suite_blocks_with_summary(project: Path, fake_uv: FakeUv) -> None:
    env, log = fake_uv(1, FAILING_OUTPUT)
    result = run_hook(HOOK, stop_payload(project, active=False), project=project, env=env)
    assert result.returncode == 0
    decision = json.loads(result.stdout)
    assert decision["decision"] == "block"
    assert "FAILED tests/test_tax.py::test_tax - assert 1 == 2" in decision["reason"]
    assert "1 failed, 3 passed" in decision["reason"]
    assert "assert 1 == 2\n=" not in decision["reason"]
    assert read_calls(log) == [["run", "pytest", "-m", "not slow", "-q"]]


@pytest.mark.parametrize("exit_code", [0, 5])
def test_passing_or_empty_suite_allows_stop(project: Path, fake_uv: FakeUv, exit_code: int) -> None:
    env, log = fake_uv(exit_code, "4 passed in 0.10s\n")
    result = run_hook(HOOK, stop_payload(project, active=False), project=project, env=env)
    assert result.returncode == 0
    assert result.stdout == ""
    assert len(read_calls(log)) == 1


def test_stop_hook_active_allows_stop_without_running_tests(project: Path, fake_uv: FakeUv) -> None:
    env, log = fake_uv(1, FAILING_OUTPUT)
    result = run_hook(HOOK, stop_payload(project, active=True), project=project, env=env)
    assert result.returncode == 0
    assert result.stdout == ""
    assert read_calls(log) == []


def test_missing_uv_blocks_with_reason(project: Path, tmp_path: Path) -> None:
    no_uv = {"PATH": str(tmp_path / "empty")}
    result = run_hook(HOOK, stop_payload(project, active=False), project=project, env=no_uv)
    decision = json.loads(result.stdout)
    assert decision["decision"] == "block"
    assert "uv was not found" in decision["reason"]
