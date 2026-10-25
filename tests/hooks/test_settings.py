"""The hooks and deny rules in .claude/settings.json are registered as plan §8.2 lists."""

import json
from pathlib import Path
from typing import Any

import pytest

from hooks.hookrun import HOOKS_DIR

SETTINGS = HOOKS_DIR.parent / "settings.json"
PLACEHOLDER = "${CLAUDE_PROJECT_DIR}/.claude/hooks/"

EXPECTED = {
    ("PreToolUse", "Read|Grep|Glob|Bash"): "vault_guard.py",
    ("PreToolUse", "Edit|Write|MultiEdit|NotebookEdit|Bash"): "prereg_guard.py",
    ("PostToolUse", "Edit|Write|MultiEdit"): "lint_on_edit.py",
    ("Stop", None): "test_gate.py",
}


def settings() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(SETTINGS.read_text())
    return loaded


def registered() -> dict[tuple[str, str | None], dict[str, Any]]:
    found: dict[tuple[str, str | None], dict[str, Any]] = {}
    for event, groups in settings()["hooks"].items():
        for group in groups:
            for hook in group["hooks"]:
                found[(event, group.get("matcher"))] = hook
    return found


def test_each_hook_is_registered_once() -> None:
    assert {key: Path(hook["args"][-1]).name for key, hook in registered().items()} == EXPECTED


@pytest.mark.parametrize("key", list(EXPECTED))
def test_hooks_use_exec_form_with_uv(key: tuple[str, str | None]) -> None:
    # Exec form: Claude Code substitutes the placeholder and spawns uv without a shell, so the
    # command is the same under Git Bash, PowerShell and sh.
    hook = registered()[key]
    assert hook["type"] == "command"
    assert hook["command"] == "uv"
    assert hook["args"] == ["run", "python", PLACEHOLDER + EXPECTED[key]]
    assert "shell" not in hook
    assert (HOOKS_DIR / EXPECTED[key]).is_file()


@pytest.mark.parametrize(
    "rule",
    [
        "Bash(git push origin main*)",
        "Bash(git push * main*)",
        "Bash(git push --force*)",
        "Edit(/research/preregistration.yaml)",
        "Read(/data/holdout/**)",
    ],
)
def test_deny_rules_are_present(rule: str) -> None:
    assert rule in settings()["permissions"]["deny"]


def test_attribution_is_disabled() -> None:
    # Empty strings and a false sessionUrl, rather than `false` for the whole key, so that
    # Claude Code versions before v2.1.281 still load the file.
    assert settings()["attribution"] == {"commit": "", "pr": "", "sessionUrl": False}


def test_feature_branch_pushes_are_not_denied() -> None:
    deny = settings()["permissions"]["deny"]
    assert "Bash(git push *)" not in deny
    assert "Bash(git push:*)" not in deny
