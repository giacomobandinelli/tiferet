"""research_scope.py keeps the optimizer's edits in research code (plan §8.3; 0009)."""

import importlib.util
import os
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from hooks.hookrun import HOOKS_DIR, run_hook, tool_payload

HOOK = "research_scope.py"


@pytest.fixture
def scoped(project: Path) -> Path:
    (project / "src" / "tiferet" / "research").mkdir(parents=True)
    (project / "research" / "strategies").mkdir(parents=True)
    (project / "src" / "tiferet" / "config.py").write_text("")
    return project


def edit(tool: str, path: str) -> dict[str, Any]:
    if tool == "NotebookEdit":
        return {"notebook_path": path, "new_source": "x"}
    return {"file_path": path, "old_string": "a", "new_string": "b", "content": "x"}


def scope(project: Path, tool: str, path: str, cwd: Path | None = None) -> int:
    result = run_hook(HOOK, tool_payload(tool, edit(tool, path), cwd or project), project=project)
    if result.returncode == 2:
        assert "research_scope: blocked" in result.stderr
    return result.returncode


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit", "NotebookEdit"])
@pytest.mark.parametrize(
    "path",
    [
        "src/tiferet/research/walk_forward.py",
        "./src/tiferet/research/sub/module.py",
        "src\\tiferet\\research\\walk_forward.py",
        "research/strategies/ma10.yaml",
        "research/strategies/../strategies/ladder.py",
    ],
)
def test_research_code_is_allowed(scoped: Path, tool: str, path: str) -> None:
    assert scope(scoped, tool, path) == 0


def test_absolute_and_parent_relative_paths_inside_scope_are_allowed(scoped: Path) -> None:
    absolute = str(scoped / "research" / "strategies" / "ma10.py")
    assert scope(scoped, "Write", absolute) == 0
    assert scope(scoped, "Write", "../research/strategies/ma10.py", cwd=scoped / "src") == 0


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit", "NotebookEdit"])
@pytest.mark.parametrize(
    "path",
    [
        "src/tiferet/config.py",
        "src/tiferet/portfolio/tax.py",
        "research/preregistration.yaml",
        "research/ledger.parquet",
        "research/strategies/../preregistration.yaml",
        "src/tiferet/research/../config.py",
        "src\\tiferet\\research\\..\\config.py",
        "SRC/Tiferet/Research/walk_forward.py",
        "Research\\Strategies\\ma10.py",
        "src/tiferet/research",
        "research/strategies",
        "src/tiferet/research_extra/x.py",
        "research/strategies_old/x.py",
        "config/costs_taxes.yaml",
        "tests/locked/test_new.py",
        ".claude/hooks/research_scope.py",
        "../outside.py",
        "/tmp/elsewhere.py",
        "",
    ],
)
def test_everything_else_is_blocked(scoped: Path, tool: str, path: str) -> None:
    assert scope(scoped, tool, path) == 2


def load_hook() -> ModuleType:
    spec = importlib.util.spec_from_file_location("research_scope", HOOKS_DIR / HOOK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical_case(real: Callable[[str], str]) -> Callable[[str], str]:
    """A realpath that also corrects the case of existing components, as Windows does."""

    def realpath(path: str) -> str:
        resolved = Path(real(path))
        canonical = Path(resolved.anchor)
        for part in resolved.parts[1:]:
            names = (
                {p.name.lower(): p.name for p in canonical.iterdir()} if canonical.is_dir() else {}
            )
            canonical /= names.get(part.lower(), part)
        return str(canonical)

    return realpath


@pytest.mark.parametrize(
    "path", ["SRC/Tiferet/Research/walk_forward.py", "Research\\Strategies\\ma10.py"]
)
def test_wrong_case_is_blocked_when_realpath_corrects_case(
    scoped: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    hook = load_hook()
    windows_realpath = canonical_case(os.path.realpath)
    assert windows_realpath(str(scoped / "SRC" / "Tiferet" / "Research" / "x.py")) == str(
        scoped / "src" / "tiferet" / "research" / "x.py"
    )
    monkeypatch.setattr(os.path, "realpath", windows_realpath)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(scoped))
    hook.check(tool_payload("Edit", edit("Edit", "src/tiferet/research/walk_forward.py"), scoped))
    with pytest.raises(hook.BlockedError):
        hook.check(tool_payload("Edit", edit("Edit", path), scoped))


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra privileges on Windows")
def test_symlink_out_of_scope_is_blocked(scoped: Path) -> None:
    link = scoped / "research" / "strategies" / "config_link.py"
    link.symlink_to(scoped / "src" / "tiferet" / "config.py")
    assert scope(scoped, "Edit", "research/strategies/config_link.py") == 2


def test_other_tools_are_ignored(scoped: Path) -> None:
    payload = tool_payload("Read", {"file_path": "src/tiferet/config.py"}, scoped)
    assert run_hook(HOOK, payload, project=scoped).returncode == 0


@pytest.mark.parametrize("stdin", ["not json", "[1, 2]"])
def test_unreadable_input_is_blocked(scoped: Path, stdin: str) -> None:
    result = run_hook(HOOK, stdin, project=scoped)
    assert result.returncode == 2
    assert "could not check" in result.stderr
