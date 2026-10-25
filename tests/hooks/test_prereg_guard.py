"""prereg_guard.py protects the pre-registration and the locked tests (plan §4, §8.2)."""

import os
from pathlib import Path
from typing import Any

import pytest

from hooks.hookrun import run_hook, tool_payload

HOOK = "prereg_guard.py"
PREREG = "research/preregistration.yaml"
LOCKED_TEST = "tests/locked/test_no_lookahead.py"


def guard(project: Path, tool: str, tool_input: dict[str, Any], cwd: Path | None = None) -> int:
    result = run_hook(HOOK, tool_payload(tool, tool_input, cwd or project), project=project)
    if result.returncode == 2:
        assert "prereg_guard: blocked" in result.stderr
    return result.returncode


def edit(path: str) -> dict[str, Any]:
    return {"file_path": path, "old_string": "a", "new_string": "b"}


def write(path: str) -> dict[str, Any]:
    return {"file_path": path, "content": "x = 1\n"}


@pytest.mark.parametrize(
    "path",
    [
        PREREG,
        "./research/preregistration.yaml",
        "Research\\PreRegistration.YAML",
        "src/../research/preregistration.yaml",
        LOCKED_TEST,
        "tests\\LOCKED\\test_no_lookahead.py",
    ],
)
@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_changes_to_protected_files_are_blocked(project: Path, tool: str, path: str) -> None:
    assert guard(project, tool, edit(path) if tool != "Write" else write(path)) == 2


def test_absolute_and_parent_relative_paths_are_blocked(project: Path) -> None:
    assert guard(project, "Edit", edit(str(project / PREREG))) == 2
    assert guard(project, "Write", write("../" + PREREG), cwd=project / "src") == 2


def test_notebook_edit_in_locked_is_blocked(project: Path) -> None:
    (project / "tests" / "locked" / "check.ipynb").write_text("{}")
    tool_input = {"notebook_path": "tests/locked/check.ipynb", "new_source": "x"}
    assert guard(project, "NotebookEdit", tool_input) == 2


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra privileges on Windows")
def test_symlink_to_protected_file_is_blocked(project: Path) -> None:
    (project / "notes.yaml").symlink_to(project / PREREG)
    assert guard(project, "Write", write("notes.yaml")) == 2


def test_new_draft_in_locked_is_allowed(project: Path) -> None:
    assert guard(project, "Write", write("tests/locked/test_new_draft.py")) == 0


def test_empty_template_may_be_created_when_absent(project: Path) -> None:
    (project / PREREG).unlink()
    assert guard(project, "Write", write(PREREG)) == 0


@pytest.mark.parametrize(
    "tool_input",
    [edit("src/tiferet/config.py"), edit("tests/test_config.py"), edit("research/notes.md")],
)
def test_other_files_are_allowed(project: Path, tool_input: dict[str, Any]) -> None:
    assert guard(project, "Edit", tool_input) == 0


@pytest.mark.parametrize(
    "command",
    [
        "echo 'metrics: []' > research/preregistration.yaml",
        "echo x >> Research\\Preregistration.yaml",
        "sed -i 's/a/b/' research/preregistration.yaml",
        "cat new.yaml | tee research/preregistration.yaml",
        "cp other.yaml research/preregistration.yaml",
        "rm research/preregistration.yaml",
        "git checkout HEAD~1 -- research/preregistration.yaml",
        "cd research && rm preregistration.yaml",
        "cd tests && sed -i 's/x/y/' locked/test_no_lookahead.py",
        "rm tests/locked/*",
        "sed -i 's/a/b/' research/*.yaml",
        "python -c \"open('research/preregistration.yaml', 'w').write('')\"",
        "ls tests/locked | xargs rm",
        "echo x > tests/locked/test_no_lookahead.py",
        "cd tests/locked && python -c \"open('test_no_lookahead.py', 'w')\"",
        "uv run ruff format tests/locked/test_no_lookahead.py",
        "mv tests/locked/test_no_lookahead.py /tmp/",
    ],
)
def test_bash_writes_to_protected_files_are_blocked(project: Path, command: str) -> None:
    assert guard(project, "Bash", {"command": command}) == 2


@pytest.mark.parametrize(
    "command",
    [
        "cat research/preregistration.yaml",
        "git diff research/preregistration.yaml tests/locked",
        "uv run pytest tests/locked -q",
        "uv run ruff check tests/locked",
        "grep -n holdout_start research/preregistration.yaml | head -5",
        "cat > tests/locked/test_new_draft.py <<'EOF'\ndef test_x() -> None: ...\nEOF",
        "echo 'x = 1' > research/scratch.yaml",
        "sed -i 's/a/b/' src/tiferet/config.py",
        "uv run ruff format .",
    ],
)
def test_bash_reads_and_unrelated_writes_are_allowed(project: Path, command: str) -> None:
    assert guard(project, "Bash", {"command": command}) == 0


def test_other_tools_are_ignored(project: Path) -> None:
    assert guard(project, "Read", {"file_path": PREREG}) == 0


@pytest.mark.parametrize("stdin", ["not json", "[1, 2]"])
def test_unreadable_input_is_blocked(project: Path, stdin: str) -> None:
    result = run_hook(HOOK, stdin, project=project)
    assert result.returncode == 2
    assert "could not check" in result.stderr
