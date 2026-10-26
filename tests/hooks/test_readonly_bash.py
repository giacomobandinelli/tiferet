"""readonly_bash.py allows only allowlisted, non-writing Bash commands (plan §8.3; 0009)."""

import os
import tomllib
from pathlib import Path
from typing import Any

import pytest

from hooks.hookrun import HOOKS_DIR, run_hook, tool_payload

HOOK = "readonly_bash.py"
REPO_ROOT = HOOKS_DIR.parents[1]
RUFF_CONFIG_NAMES = {"pyproject.toml", "ruff.toml", ".ruff.toml"}
SKIPPED_DIRS = {".git", ".venv", "node_modules", "__pycache__", "data", "notes"}


def bash(project: Path, command: str) -> int:
    result = run_hook(HOOK, tool_payload("Bash", {"command": command}, project), project=project)
    if result.returncode == 2:
        assert "readonly_bash: blocked" in result.stderr
    return result.returncode


@pytest.mark.parametrize(
    "command",
    [
        "uv run pytest",
        "uv run pytest -m 'not slow' -q tests/test_config.py",
        "uv run --frozen pytest -k ledger",
        "uv run python -m tiferet",
        "uv run python -m tiferet.research.report --strategy ma10",
        "uv run ruff check .",
        "uv run ruff check src --statistics",
        "uv run ruff format --check .",
        "uv run ruff format --diff src",
        "uv run mypy --strict src tests",
        "git diff",
        "git diff main...HEAD -- src/tiferet/research",
        "git log --oneline -20",
        "git --no-pager show HEAD~1:config/costs_taxes.yaml",
        "git status --short",
        "git -C src blame tiferet/config.py",
        "ls -la src/tiferet",
        "cat config/costs_taxes.yaml",
        "head -n 40 docs/plan.md",
        "tail -5 README.md",
        "wc -l src/tiferet/config.py",
        "grep -rn known_at src",
        "rg -n 'run_backtest' src tests",
        "find src -name '*.py'",
        "cat config/holdings.yaml | grep isin | sort | head",
        "uv run pytest -q 2>&1 | tail -20",
        "ls missing 2>/dev/null || echo none",
        "cd src && ls",
        "sort -u < config/holdings.yaml",
    ],
)
def test_allowlisted_commands_are_allowed(project: Path, command: str) -> None:
    assert bash(project, command) == 0


@pytest.mark.parametrize(
    "command",
    [
        # Redirection, here-documents and substitution
        "echo x > src/tiferet/config.py",
        "cat a >> b",
        "uv run pytest &> out.log",
        "cat config/holdings.yaml >| copy.yaml",
        "cat <<EOF\ntext\nEOF",
        "cat <<< text",
        "echo $(rm -rf src)",
        "echo `touch x`",
        "diff <(ls) >(tee out)",
        "(cd src && ls)",
        "uv run pytest &",
        # Writing commands, alone and after a pipe
        "rm -rf src",
        "mv a b",
        "cp a b",
        "touch x",
        "mkdir notes",
        "cat config/holdings.yaml | tee copy.yaml",
        "ls | xargs rm",
        "sed -i 's/a/b/' src/tiferet/config.py",
        "awk '{print > \"out\"}' file",
        'python -c \'open("x", "w")\'',
        "bash -c 'rm x'",
        "sh script.sh",
        "./script.sh",
        "/bin/rm x",
        "chmod +x file",
        # git writes and dangerous options
        "git commit -m 'x'",
        "git push origin HEAD",
        "git checkout -- src",
        "git reset --hard",
        "git stash",
        "git add .",
        "git -c core.pager='rm -rf .' log",
        "git diff --output=patch.diff",
        "git log --ext-diff",
        # Package installation
        "pip install requests",
        "uv add requests",
        "uv pip install requests",
        "uv sync",
        "uv run --with requests python -m tiferet",
        "uv tool install ruff",
        # Allowed tools with writing or executing flags
        "uv run ruff check --fix .",
        "uv run ruff check --unsafe-fixes src",
        "uv run ruff check --output-file=out.txt .",
        "uv run ruff format .",
        "uv run pytest --junitxml=report.xml",
        "uv run pytest --basetemp=src",
        "uv run mypy --install-types src",
        "uv run mypy --html-report out src",
        "uv run python script.py",
        "uv run python -c 'import os'",
        "uv run python -m pip install requests",
        "uv run python -m tiferetx",
        "uv run bash",
        "find . -name '*.pyc' -delete",
        "find . -exec rm {} ;",
        "rg --pre 'rm -rf' x",
        "sort -o out.txt in.txt",
        "sort -uo out.txt in.txt",
        "tree -o out.txt",
        "file -C -m magic",
        # Environment assignments, quoting tricks and empty input
        "TIFERET_X=1 uv run pytest",
        "r'm' -rf src",
        "'git' commit -m x",
        "echo 'unterminated",
        "",
    ],
)
def test_writing_or_unlisted_commands_are_blocked(project: Path, command: str) -> None:
    assert bash(project, command) == 2


def test_other_tools_are_ignored(project: Path) -> None:
    payload = tool_payload("Read", {"file_path": "src/tiferet/config.py"}, project)
    assert run_hook(HOOK, payload, project=project).returncode == 0


@pytest.mark.parametrize("stdin", ["not json", "[1, 2]"])
def test_unreadable_input_is_blocked(project: Path, stdin: str) -> None:
    result = run_hook(HOOK, stdin, project=project)
    assert result.returncode == 2
    assert "could not check" in result.stderr


def ruff_configs() -> list[Path]:
    found = []
    for directory, subdirs, files in os.walk(REPO_ROOT):
        subdirs[:] = [d for d in subdirs if d not in SKIPPED_DIRS]
        found += [Path(directory) / name for name in files if name in RUFF_CONFIG_NAMES]
    return found


def test_ruff_never_fixes_by_default() -> None:
    # The allowlist lets `uv run ruff check` through only because it does not write files.
    # A `fix = true` in any ruff configuration would turn it into a write (decision 0009).
    configs = ruff_configs()
    assert REPO_ROOT / "pyproject.toml" in configs
    for path in configs:
        settings: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
        ruff = (
            settings.get("tool", {}).get("ruff", {}) if path.name == "pyproject.toml" else settings
        )
        for key in ("fix", "unsafe-fixes"):
            assert ruff.get(key) is not True, f"{path} sets ruff {key} = true"
            assert ruff.get("lint", {}).get(key) is not True, f"{path} sets ruff lint.{key} = true"
