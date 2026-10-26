"""Tests for scripts/check_protected.py, run against throwaway git repositories."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_protected.py"
LOCKED_FILE = "tests/locked/test_x.py"
PREREG = "research/preregistration.yaml"
REGISTRY = ".github/protected-tags.txt"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_protected", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_protected"] = module
    spec.loader.exec_module(module)
    return module


check_protected = _load()


class Repo:
    def __init__(self, root: Path) -> None:
        self.root = root

    def git(self, *args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", *args],
            cwd=self.root,
            check=True,
            capture_output=True,
        )

    def write(self, path: str, text: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")

    def commit(self, message: str = "change") -> None:
        self.git("add", "-A")
        self.git("commit", "-m", message)

    def tag(self, name: str) -> None:
        self.git("tag", name)

    def run(self, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
        code = check_protected.run(self.root)
        out = capsys.readouterr().out
        return int(code), out


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    r = Repo(tmp_path)
    r.git("init", "-q", "-b", "main")
    r.write(LOCKED_FILE, "v1\n")
    r.write(PREREG, "objective: a\n")
    r.write(REGISTRY, "# registry\nlocked/x-v1\n")
    r.commit("initial")
    r.tag("locked/x-v1")
    return r


Register = Callable[[str], None]


def test_clean_repo_passes_with_notice(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    code, out = repo.run(capsys)
    assert code == 0
    assert "no prereg/v<n> tag exists yet" in out


def test_modified_tagged_file_fails(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.write(LOCKED_FILE, "changed\n")
    repo.commit()
    code, out = repo.run(capsys)
    assert code == 1
    assert LOCKED_FILE in out
    assert "locked/x-v1" in out


def test_deleted_tagged_file_fails(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    (repo.root / LOCKED_FILE).unlink()
    repo.commit()
    code, out = repo.run(capsys)
    assert code == 1
    assert "missing at HEAD" in out


def test_missing_listed_tag_fails(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.write(REGISTRY, "locked/x-v1\nlocked/y-v1\n")
    repo.commit()
    code, out = repo.run(capsys)
    assert code == 1
    assert "locked/y-v1" in out
    assert "does not exist" in out


def test_unlisted_locked_tag_fails(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.tag("locked/z-v1")
    code, out = repo.run(capsys)
    assert code == 1
    assert "locked/z-v1" in out
    assert "not listed" in out


@pytest.mark.parametrize("name", ["locked/x", "locked/X-v1", "locked/x-v0", "locked/x-v01"])
def test_malformed_locked_tag_fails(
    repo: Repo, capsys: pytest.CaptureFixture[str], name: str
) -> None:
    repo.tag(name)
    code, out = repo.run(capsys)
    assert code == 1
    assert name in out


def test_untagged_draft_is_allowed(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.write("tests/locked/test_draft.py", "draft\n")
    repo.commit()
    code, _ = repo.run(capsys)
    assert code == 0


def _add_v2(repo: Repo, text: str = "v2\n") -> None:
    repo.write(LOCKED_FILE, text)
    repo.write(REGISTRY, "locked/x-v1\nlocked/x-v2\n")
    repo.commit("v2")
    repo.tag("locked/x-v2")


def test_head_equal_to_v2_passes(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    _add_v2(repo)
    code, _ = repo.run(capsys)
    assert code == 0


def test_head_equal_to_v1_but_not_v2_fails(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    _add_v2(repo)
    repo.write(LOCKED_FILE, "v1\n")
    repo.commit("back to v1")
    code, out = repo.run(capsys)
    assert code == 1
    assert "locked/x-v2" in out


def test_older_version_must_stay_listed(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    _add_v2(repo)
    repo.write(REGISTRY, "locked/x-v2\n")
    repo.commit("drop v1 from the list")
    code, out = repo.run(capsys)
    assert code == 1
    assert "locked/x-v1" in out


def test_v10_outranks_v2(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    _add_v2(repo)
    repo.write(LOCKED_FILE, "v10\n")
    repo.write(REGISTRY, "locked/x-v1\nlocked/x-v2\nlocked/x-v10\n")
    repo.commit("v10")
    repo.tag("locked/x-v10")
    code, _ = repo.run(capsys)
    assert code == 0
    repo.write(LOCKED_FILE, "v2\n")
    repo.commit("back to v2")
    code, out = repo.run(capsys)
    assert code == 1
    assert "locked/x-v10" in out


def test_changed_preregistration_fails(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.tag("prereg/v1")
    repo.write(PREREG, "objective: b\n")
    repo.commit()
    code, out = repo.run(capsys)
    assert code == 1
    assert PREREG in out
    assert "prereg/v1" in out


def test_unchanged_preregistration_passes(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.tag("prereg/v1")
    code, out = repo.run(capsys)
    assert code == 0
    assert "::notice::" not in out


def test_highest_prereg_version_is_used(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.tag("prereg/v1")
    repo.write(PREREG, "objective: b\n")
    repo.commit()
    repo.tag("prereg/v2")
    code, _ = repo.run(capsys)
    assert code == 0
    repo.write(PREREG, "objective: c\n")
    repo.commit()
    repo.tag("prereg/v10")
    repo.write(PREREG, "objective: b\n")
    repo.commit("back to v2 text")
    code, out = repo.run(capsys)
    assert code == 1
    assert "prereg/v10" in out


def test_malformed_prereg_tag_fails(repo: Repo, capsys: pytest.CaptureFixture[str]) -> None:
    repo.tag("prereg/final")
    code, out = repo.run(capsys)
    assert code == 1
    assert "prereg/final" in out


def test_unreadable_git_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / ".github").mkdir()
    (tmp_path / REGISTRY).write_text("locked/x-v1\n", encoding="utf-8")
    code = check_protected.run(tmp_path)
    assert code == 1
    assert "::error::" in capsys.readouterr().out
