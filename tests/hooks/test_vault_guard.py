"""vault_guard.py blocks every route to the holdout and the unseal flag (plan §4, §8.2)."""

import os
from pathlib import Path
from typing import Any

import pytest

from hooks.hookrun import run_hook, tool_payload

HOOK = "vault_guard.py"


def guard(project: Path, tool: str, tool_input: dict[str, Any], cwd: Path | None = None) -> int:
    result = run_hook(HOOK, tool_payload(tool, tool_input, cwd or project), project=project)
    if result.returncode == 2:
        assert "vault_guard: blocked" in result.stderr
        assert "holdout" in result.stderr
    return result.returncode


@pytest.mark.parametrize(
    "path",
    [
        "data/holdout/prices.parquet",
        "./data/holdout/prices.parquet",
        "data//holdout",
        "DATA\\Holdout\\prices.parquet",
        "Data/HOLDOUT/prices.parquet",
        "src/../data/holdout/prices.parquet",
    ],
)
def test_read_of_holdout_is_blocked(project: Path, path: str) -> None:
    assert guard(project, "Read", {"file_path": path}) == 2


def test_absolute_and_parent_relative_paths_are_blocked(project: Path) -> None:
    absolute = str(project / "data" / "holdout" / "prices.parquet")
    assert guard(project, "Read", {"file_path": absolute}) == 2
    parent_relative = {"file_path": "../data/holdout/prices.parquet"}
    assert guard(project, "Read", parent_relative, cwd=project / "src") == 2


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra privileges on Windows")
def test_symlink_into_holdout_is_blocked(project: Path) -> None:
    (project / "innocent.parquet").symlink_to(project / "data" / "holdout" / "prices.parquet")
    assert guard(project, "Read", {"file_path": "innocent.parquet"}) == 2


@pytest.mark.parametrize(
    "tool_input",
    [
        {"pattern": "price", "path": "data/holdout"},
        {"pattern": "price", "path": "Data\\Holdout"},
        {"pattern": "price", "path": "data"},
        {"pattern": "price", "path": "data", "glob": "*.parquet"},
        {"pattern": "price", "glob": "data/holdout/*.parquet"},
    ],
)
def test_grep_into_holdout_is_blocked(project: Path, tool_input: dict[str, Any]) -> None:
    assert guard(project, "Grep", tool_input) == 2


@pytest.mark.parametrize(
    "tool_input",
    [
        {"pattern": "data/holdout/**"},
        {"pattern": "**/holdout/*.parquet"},
        {"pattern": "data/*/prices.parquet"},
        {"pattern": "data/h*/*"},
        {"pattern": "*.parquet", "path": "data/HOLDOUT"},
        {"pattern": "**/*.parquet", "path": "data"},
    ],
)
def test_glob_into_holdout_is_blocked(project: Path, tool_input: dict[str, Any]) -> None:
    assert guard(project, "Glob", tool_input) == 2


@pytest.mark.parametrize(
    "command",
    [
        "cat data/holdout/prices.parquet",
        "cat ../data/holdout/prices.parquet",
        "type DATA\\Holdout\\prices.parquet",
        "ls data/*",
        "head -c 100 data/h*/prices.parquet",
        "cd data && cat holdout/prices.parquet",
        "cat data/hold\\out/prices.parquet",
        'cat data/hol""dout/prices.parquet',
        "python -c \"import pandas; pandas.read_parquet('data/holdout/prices.parquet')\"",
        "uv run python script.py --input=data/holdout/prices.parquet",
        "find data -name '*.parquet'",
        "grep -r price data",
        "ls -R ./data/",
        "du -sh data",
        "aws s3 ls s3://tiferet/holdout/",
        "aws s3 cp s3://tiferet/HOLDOUT/2023.parquet .",
        "rclone copy r2:tiferet/holdout ./tmp",
        "rclone ls r2://tiferet/holdout",
    ],
)
def test_bash_access_to_holdout_is_blocked(project: Path, command: str) -> None:
    assert guard(project, "Bash", {"command": command}) == 2


@pytest.mark.parametrize(
    "command",
    [
        "export TIFERET_UNSEAL_HOLDOUT=1",
        "export TIFERET_UNSEAL_HOLDOUT",
        "declare -x TIFERET_UNSEAL_HOLDOUT=1",
        "TIFERET_UNSEAL_HOLDOUT=1 uv run pytest",
        "tiferet_unseal_holdout=1 uv run pytest",
        'TIFERET_UNSEAL_HOL""DOUT=1 uv run pytest',
        "env TIFERET_UNSEAL_HOLDOUT=1 uv run python -m tiferet.research",
        "echo ${TIFERET_UNSEAL_HOLDOUT:=1}",
        "echo 'TIFERET_UNSEAL_HOLDOUT=1' >> .env",
        "docker run -e TIFERET_UNSEAL_HOLDOUT tiferet",
        "$env:TIFERET_UNSEAL_HOLDOUT = '1'",
        "set TIFERET_UNSEAL_HOLDOUT=1",
        "setx TIFERET_UNSEAL_HOLDOUT 1",
        "Set-Item Env:TIFERET_UNSEAL_HOLDOUT 1",
        "[Environment]::SetEnvironmentVariable('TIFERET_UNSEAL_HOLDOUT', '1')",
        "python -c \"import os; os.environ['TIFERET_UNSEAL_HOLDOUT'] = '1'\"",
        "python -c \"import os; os.putenv('TIFERET_UNSEAL_HOLDOUT', '1')\"",
    ],
)
def test_setting_or_passing_the_unseal_flag_is_blocked(project: Path, command: str) -> None:
    result = run_hook(HOOK, tool_payload("Bash", {"command": command}, project), project=project)
    assert result.returncode == 2
    assert "TIFERET_UNSEAL_HOLDOUT" in result.stderr


@pytest.mark.parametrize(
    "command",
    [
        'git commit -m "Narrow the holdout guard and name the unseal flag"',
        'git commit -m "Name the unseal flag TIFERET_UNSEAL_HOLDOUT"',
        'uv run pytest -k "holdout or unseal" -q',
        "uv run pytest tests/hooks/test_vault_guard.py::test_holdout_words_are_allowed",
        "uv run pytest tests/test_holdout_loader.py",
        "grep -rn holdout docs src",
        'grep -n "unseal" docs/plan.md',
        "grep -rn TIFERET_UNSEAL_HOLDOUT src docs",
        "grep -r holdout src/tiferet/data",
        "cat docs/decisions/0008-holdout-unseal-flag.md",
        'echo "$TIFERET_UNSEAL_HOLDOUT"',
        'test "$TIFERET_UNSEAL_HOLDOUT" = 1 || echo sealed',
        "printenv TIFERET_UNSEAL_HOLDOUT",
        "unset TIFERET_UNSEAL_HOLDOUT",
    ],
)
def test_holdout_words_are_allowed(project: Path, command: str) -> None:
    assert guard(project, "Bash", {"command": command}) == 0


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "data/raw/prices.parquet"}),
        ("Read", {"file_path": "src/tiferet/data/pit.py"}),
        ("Read", {"file_path": "tests/test_holdout_loader.py"}),
        ("Grep", {"pattern": "holdout", "path": "src"}),
        ("Grep", {"pattern": "holdout"}),
        ("Grep", {"pattern": "price", "path": "data/raw"}),
        ("Glob", {"pattern": "src/**/*.py"}),
        ("Glob", {"pattern": "**/*.parquet", "path": "data/raw"}),
        ("Bash", {"command": "uv run pytest -m 'not slow' -q"}),
        ("Bash", {"command": "ls data/raw"}),
        ("Bash", {"command": "cat config/holdings.yaml"}),
        ("Bash", {"command": "git log --oneline -5"}),
        ("Bash", {"command": "ls data/"}),
        ("Bash", {"command": "grep -E '[A-Za-z_][A-Za-z0-9_]*' src/tiferet/config.py"}),
    ],
)
def test_ordinary_access_is_allowed(project: Path, tool: str, tool_input: dict[str, Any]) -> None:
    assert guard(project, tool, tool_input) == 0


def test_other_tools_are_ignored(project: Path) -> None:
    assert guard(project, "Write", {"file_path": "data/holdout/x", "content": ""}) == 0


@pytest.mark.parametrize("stdin", ["not json", "[1, 2]"])
def test_unreadable_input_is_blocked(project: Path, stdin: str) -> None:
    result = run_hook(HOOK, stdin, project=project)
    assert result.returncode == 2
    assert "could not check" in result.stderr
