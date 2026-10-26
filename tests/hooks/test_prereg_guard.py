"""prereg_guard.py protects the pre-registration and the tagged locked tests (§4, §8.2, 0011)."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hooks.hookrun import run_hook, tool_payload

HOOK = "prereg_guard.py"
PREREG = "research/preregistration.yaml"
LOCKED_TEST = "tests/locked/test_no_lookahead.py"
DRAFT = "tests/locked/test_draft.py"
TAG = "locked/no-lookahead-v1"


def remove_git_dir(project: Path) -> None:
    """Delete .git; git writes its objects read-only, which Windows refuses to remove."""

    def make_writable(func: Any, path: str, _exc: Any) -> None:
        os.chmod(path, 0o700)
        func(path)

    shutil.rmtree(project / ".git", onexc=make_writable)


def git(project: Path, *args: str) -> str:
    config = ["-c", "user.name=Test", "-c", "user.email=test@example.invalid"]
    config += ["-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false"]
    result = subprocess.run(
        ["git", "-C", str(project), *config, *args],
        input="",
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@pytest.fixture
def project(project: Path) -> Path:
    """The project as a git repository whose locked test is tagged and whose draft is not."""
    git(project, "init", "-q")
    git(project, "add", "research", LOCKED_TEST)
    git(project, "commit", "-q", "-m", "Lock the test")
    git(project, "tag", "-a", TAG, "-m", "Locked test")
    (project / DRAFT).write_text("def test_draft() -> None: ...\n")
    return project


def guard(
    project: Path,
    tool: str,
    tool_input: dict[str, Any],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> int:
    payload = tool_payload(tool, tool_input, cwd or project)
    result = run_hook(HOOK, payload, project=project, env=env)
    if result.returncode == 2:
        assert "prereg_guard: blocked" in result.stderr
    return result.returncode


def edit(path: str) -> dict[str, Any]:
    return {"file_path": path, "old_string": "a", "new_string": "b"}


def write(path: str) -> dict[str, Any]:
    return {"file_path": path, "content": "x = 1\n"}


def bash(command: str) -> dict[str, Any]:
    return {"command": command}


@pytest.mark.parametrize(
    "path",
    [
        PREREG,
        "./research/preregistration.yaml",
        "Research\\PreRegistration.YAML",
        "src/../research/preregistration.yaml",
        LOCKED_TEST,
        "tests\\LOCKED\\Test_No_Lookahead.py",
    ],
)
@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_changes_to_protected_files_are_blocked(project: Path, tool: str, path: str) -> None:
    assert guard(project, tool, edit(path) if tool != "Write" else write(path)) == 2


def test_absolute_and_parent_relative_paths_are_blocked(project: Path) -> None:
    assert guard(project, "Edit", edit(str(project / PREREG))) == 2
    assert guard(project, "Write", write("../" + PREREG), cwd=project / "src") == 2


def test_notebook_edit_of_a_tagged_notebook_is_blocked(project: Path) -> None:
    (project / "tests" / "locked" / "check.ipynb").write_text("{}")
    git(project, "add", "tests/locked/check.ipynb")
    git(project, "commit", "-q", "-m", "Add a notebook")
    git(project, "tag", "locked/check-v1")
    tool_input = {"notebook_path": "tests/locked/check.ipynb", "new_source": "x"}
    assert guard(project, "NotebookEdit", tool_input) == 2


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra privileges on Windows")
def test_symlink_to_protected_file_is_blocked(project: Path) -> None:
    (project / "notes.yaml").symlink_to(project / PREREG)
    assert guard(project, "Write", write("notes.yaml")) == 2


def test_tagged_file_missing_from_disk_stays_protected(project: Path) -> None:
    (project / LOCKED_TEST).unlink()
    assert guard(project, "Write", write(LOCKED_TEST)) == 2


def test_new_draft_in_locked_is_allowed(project: Path) -> None:
    assert guard(project, "Write", write("tests/locked/test_new_draft.py")) == 0


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit"])
def test_untagged_draft_may_be_changed(project: Path, tool: str) -> None:
    assert guard(project, tool, edit(DRAFT) if tool != "Write" else write(DRAFT)) == 0


def test_untagged_notebook_may_be_changed(project: Path) -> None:
    (project / "tests" / "locked" / "check.ipynb").write_text("{}")
    tool_input = {"notebook_path": "tests/locked/check.ipynb", "new_source": "x"}
    assert guard(project, "NotebookEdit", tool_input) == 0


def test_tags_outside_locked_do_not_protect(project: Path) -> None:
    (project / DRAFT).write_text("x = 2\n")
    git(project, "add", DRAFT)
    git(project, "commit", "-q", "-m", "Release")
    git(project, "tag", "release-v1")
    assert guard(project, "Edit", edit(DRAFT)) == 0


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
        "rm tests/locked/test_no_*.py",
        "rm -r tests/locked",
        "rm -r tests",
        "sed -i 's/a/b/' research/*.yaml",
        "python -c \"open('research/preregistration.yaml', 'w').write('')\"",
        "python -c \"open('tests/locked/test_no_lookahead.py', 'w')\"",
        "ls tests/locked | xargs rm",
        "echo x > tests/locked/test_no_lookahead.py",
        "echo x > tests/LOCKED/Test_No_Lookahead.py",
        "cd tests/locked && python -c \"open('test_no_lookahead.py', 'w')\"",
        "uv run ruff format tests/locked/test_no_lookahead.py",
        "mv tests/locked/test_no_lookahead.py /tmp/",
        "git mv tests/locked/test_no_lookahead.py tests/test_no_lookahead.py",
        "cat <<<hi\nrm tests/locked/test_no_lookahead.py",
        'echo "<<END"\nrm tests/locked/test_no_lookahead.py\nEND',
        "echo `rm tests/locked/test_no_lookahead.py`",
        "rm tests/locked/test_no_lookahead.p{y,}",
        "rm tests/{locked,other}/test_no_lookahead.py",
        "git diff --output=tests/locked/test_no_lookahead.py",
        "find . -name x -fprint0 tests/locked/test_no_lookahead.py",
        "find . -fls tests/locked/test_no_lookahead.py",
        # A command run inside a folder that holds a tagged file is blocked even when it
        # names only a draft, because its other arguments cannot all be read as paths.
        "cd tests/locked && rm test_draft.py",
    ],
)
def test_bash_writes_to_protected_files_are_blocked(project: Path, command: str) -> None:
    assert guard(project, "Bash", bash(command)) == 2


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
    assert guard(project, "Bash", bash(command)) == 0


@pytest.mark.parametrize(
    "command",
    [
        f"rm {DRAFT}",
        f"sed -i 's/a/b/' {DRAFT}",
        f"echo x > {DRAFT}",
        f"mv {DRAFT} tests/locked/test_renamed.py",
        "git mv tests/locked_staging/test_x.py.draft tests/locked/test_x.py",
        "rm -r tests/locked_staging",
        f"uv run ruff format {DRAFT}",
    ],
)
def test_bash_changes_to_untagged_drafts_are_allowed(project: Path, command: str) -> None:
    assert guard(project, "Bash", bash(command)) == 0


def test_untagged_locked_folder_may_be_cleared(project: Path) -> None:
    git(project, "tag", "-d", TAG)
    assert guard(project, "Bash", bash("rm tests/locked/*")) == 0
    assert guard(project, "Bash", bash("rm -r tests/locked")) == 0


@pytest.mark.parametrize(
    "command",
    [
        "git tag locked/no-lookahead-v2",
        "git tag -a locked/no-lookahead-v2 -m 'Locked test'",
        "git tag -d locked/no-lookahead-v1",
        "git tag --delete Locked/No-Lookahead-v1",
        "git tag -f locked/no-lookahead-v1 HEAD",
        "git -C . tag -f locked/no-lookahead-v1",
        "git -c tag.gpgsign=false tag locked/x-v1",
        "git push origin --delete locked/no-lookahead-v1",
        "git push origin :refs/tags/locked/no-lookahead-v1",
        "git push origin +refs/tags/locked/no-lookahead-v1",
        "git push origin locked/no-lookahead-v1",
        "git push --mirror origin",
        "git push --prune origin 'refs/tags/*:refs/tags/*'",
        "git update-ref refs/tags/locked/no-lookahead-v1 HEAD",
        "git update-ref -d refs/tags/locked/no-lookahead-v1",
        "git update-ref --stdin <<'EOF'\ndelete refs/tags/locked/x-v1\nEOF",
        "git fetch --prune-tags origin",
        "git fetch -pP origin",
        "git pull --prune-tags origin main",
        "git fetch origin +refs/tags/*:refs/tags/*",
        "git fetch origin refs/tags/locked/x-v1:refs/tags/locked/x-v1",
        "rm .git/refs/tags/locked/no-lookahead-v1",
        "sed -i '/locked/d' .git/packed-refs",
        "rm -rf .git/refs/tags",
        "rm -rf .git",
        "cd .git && rm packed-refs",
        "git replace -f HEAD HEAD~1",
        "git fetch --tags --force origin",
        "git fetch -f --tags origin",
    ],
)
def test_changes_to_locked_tags_are_blocked(project: Path, command: str) -> None:
    assert guard(project, "Bash", bash(command)) == 2


@pytest.mark.parametrize(
    "command",
    [
        "git tag --list 'locked/*'",
        "git tag -l",
        "git tag",
        "git tag -v locked/no-lookahead-v1",
        "git tag release-v1",
        "git push -u origin claude/some-branch",
        "git fetch -p origin",
        "git fetch origin claude/some-branch",
        "git fetch --tags origin",
        "git replace -l",
        "cat .git/HEAD",
        "git log --oneline -3",
    ],
)
def test_other_tag_commands_are_allowed(project: Path, command: str) -> None:
    assert guard(project, "Bash", bash(command)) == 0


def test_no_repository_blocks_locked_changes(project: Path) -> None:
    remove_git_dir(project)
    result = run_hook(HOOK, tool_payload("Edit", edit(DRAFT), project), project=project)
    assert result.returncode == 2
    assert "could not read the locked/* tags" in result.stderr


def test_missing_git_blocks_locked_changes(project: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty_bin"
    empty.mkdir()
    payload = tool_payload("Bash", bash(f"rm {DRAFT}"), project)
    result = run_hook(HOOK, payload, project=project, env={"PATH": str(empty)})
    assert result.returncode == 2
    assert "could not read the locked/* tags" in result.stderr


def test_unreadable_tag_blocks_locked_changes(project: Path) -> None:
    blob = git(project, "hash-object", "-w", "--stdin")
    git(project, "tag", "locked/bad-v1", blob)
    result = run_hook(HOOK, tool_payload("Write", write(DRAFT), project), project=project)
    assert result.returncode == 2
    assert "could not read the locked/* tags" in result.stderr


def test_changes_elsewhere_need_no_repository(project: Path, tmp_path: Path) -> None:
    remove_git_dir(project)
    empty = tmp_path / "empty_bin"
    empty.mkdir()
    env = {"PATH": str(empty)}
    assert guard(project, "Edit", edit("src/tiferet/config.py"), env=env) == 0
    assert guard(project, "Bash", bash("sed -i 's/a/b/' src/tiferet/config.py"), env=env) == 0
    assert guard(project, "Bash", bash("cat tests/locked/test_draft.py"), env=env) == 0


def test_other_tools_are_ignored(project: Path) -> None:
    assert guard(project, "Read", {"file_path": PREREG}) == 0


@pytest.mark.parametrize("stdin", ["not json", "[1, 2]"])
def test_unreadable_input_is_blocked(project: Path, stdin: str) -> None:
    result = run_hook(HOOK, stdin, project=project)
    assert result.returncode == 2
    assert "could not check" in result.stderr
