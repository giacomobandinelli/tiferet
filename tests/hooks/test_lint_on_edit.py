"""lint_on_edit.py formats and lints changed Python files and never blocks (plan §8.2)."""

import json
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from hooks.hookrun import HOOKS_DIR, read_calls, run_hook, tool_payload

HOOK = "lint_on_edit.py"
REPO_ROOT = HOOKS_DIR.parents[1]

FakeUv = Callable[[int, str], tuple[dict[str, str], Path]]


def post_payload(tool: str, path: Path, cwd: Path) -> dict[str, object]:
    payload = tool_payload(tool, {"file_path": str(path), "content": ""}, cwd)
    payload["hook_event_name"] = "PostToolUse"
    payload["tool_response"] = {"filePath": str(path), "success": True}
    return payload


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_python_file_is_formatted_then_fixed(project: Path, fake_uv: FakeUv, tool: str) -> None:
    target = project / "src" / "module.py"
    target.write_text("x=1\n")
    env, log = fake_uv(0, "")
    result = run_hook(HOOK, post_payload(tool, target, project), project=project, env=env)
    assert result.returncode == 0
    assert result.stdout == ""
    assert read_calls(log) == [
        ["run", "ruff", "format", "--force-exclude", str(target)],
        ["run", "ruff", "check", "--fix", "--force-exclude", str(target)],
    ]


def test_remaining_findings_are_reported_without_blocking(project: Path, fake_uv: FakeUv) -> None:
    target = project / "src" / "module.py"
    target.write_text("import os\n")
    env, _ = fake_uv(1, "module.py:1:8: F401 `os` imported but unused")
    result = run_hook(HOOK, post_payload("Edit", target, project), project=project, env=env)
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert output["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "F401" in output["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize("name", ["notes.md", "config.yaml", "data.json", "missing.py"])
def test_non_python_or_missing_files_are_skipped(project: Path, fake_uv: FakeUv, name: str) -> None:
    target = project / name
    if name != "missing.py":
        target.write_text("x\n")
    env, log = fake_uv(0, "")
    result = run_hook(HOOK, post_payload("Write", target, project), project=project, env=env)
    assert result.returncode == 0
    assert read_calls(log) == []


def test_missing_uv_or_bad_input_never_blocks(project: Path, tmp_path: Path) -> None:
    target = project / "src" / "module.py"
    target.write_text("x=1\n")
    no_uv = {"PATH": str(tmp_path / "empty")}
    result = run_hook(HOOK, post_payload("Edit", target, project), project=project, env=no_uv)
    assert result.returncode == 0
    assert run_hook(HOOK, "not json", project=project).returncode == 0


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv")
def test_real_ruff_formats_and_fixes(tmp_path: Path) -> None:
    target = tmp_path / "messy.py"
    target.write_text("import os\nimport sys\nx=[1,2]\nprint(sys.argv,x)\n")
    payload = post_payload("Write", target, REPO_ROOT)
    result = run_hook(HOOK, payload, project=REPO_ROOT)
    assert result.returncode == 0
    assert target.read_text() == "import sys\n\nx = [1, 2]\nprint(sys.argv, x)\n"
