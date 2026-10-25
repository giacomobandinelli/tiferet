"""The six project subagents in .claude/agents/ match plan §8.3 and §8.6 (decision 0009)."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from hooks.hookrun import HOOKS_DIR

AGENTS_DIR = HOOKS_DIR.parent / "agents"
PLACEHOLDER = "${CLAUDE_PROJECT_DIR}/.claude/hooks/"

READ_ONLY = ["Read", "Grep", "Glob", "Bash"]
EDIT = ["Read", "Grep", "Glob", "Edit", "Write", "Bash"]

# name: (model, effort, tools, {(event, matcher): hook script})
EXPECTED: dict[str, tuple[str, str, list[str], dict[tuple[str, str | None], str]]] = {
    "builder": ("sonnet", "medium", EDIT, {("Stop", None): "test_gate.py"}),
    "optimizer": (
        "opus",
        "high",
        EDIT,
        {
            ("PreToolUse", "Edit|Write|MultiEdit|NotebookEdit"): "research_scope.py",
            ("PreToolUse", "Bash"): "readonly_bash.py",
            ("Stop", None): "test_gate.py",
        },
    ),
    "quant-referee": ("opus", "high", READ_ONLY, {("PreToolUse", "Bash"): "readonly_bash.py"}),
    "tax-auditor": ("opus", "high", READ_ONLY, {("PreToolUse", "Bash"): "readonly_bash.py"}),
    "data-auditor": ("haiku", "low", READ_ONLY, {("PreToolUse", "Bash"): "readonly_bash.py"}),
    "fixer": ("sonnet", "medium", EDIT, {("Stop", None): "test_gate.py"}),
}


def parse(name: str) -> tuple[dict[str, Any], str]:
    text = (AGENTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{name}.md has no frontmatter"
    _, frontmatter, body = text.split("---\n", 2)
    meta = yaml.safe_load(frontmatter)
    assert isinstance(meta, dict)
    return meta, body


def hooks_of(meta: dict[str, Any]) -> dict[tuple[str, str | None], dict[str, Any]]:
    found: dict[tuple[str, str | None], dict[str, Any]] = {}
    for event, groups in (meta.get("hooks") or {}).items():
        for group in groups:
            for hook in group["hooks"]:
                key = (event, group.get("matcher"))
                assert key not in found, f"duplicate hook {key}"
                found[key] = hook
    return found


def test_exactly_the_six_agents_exist() -> None:
    assert sorted(p.stem for p in AGENTS_DIR.glob("*.md")) == sorted(EXPECTED)


@pytest.mark.parametrize("name", list(EXPECTED))
def test_frontmatter_is_pinned(name: str) -> None:
    meta, body = parse(name)
    model, effort, tools, _ = EXPECTED[name]
    assert set(meta) <= {"name", "description", "tools", "model", "effort", "hooks"}
    assert meta["name"] == name
    assert meta["model"] == model
    assert meta["effort"] == effort
    assert [tool.strip() for tool in meta["tools"].split(",")] == tools
    assert len(meta["description"]) > 40
    assert body.strip()


@pytest.mark.parametrize("name", list(EXPECTED))
def test_hooks_are_pinned_and_use_exec_form(name: str) -> None:
    meta, _ = parse(name)
    hooks = hooks_of(meta)
    assert {key: Path(hook["args"][-1]).name for key, hook in hooks.items()} == EXPECTED[name][3]
    for hook in hooks.values():
        script = Path(hook["args"][-1]).name
        assert hook["type"] == "command"
        assert hook["command"] == "uv"
        assert hook["args"] == ["run", "python", PLACEHOLDER + script]
        assert (HOOKS_DIR / script).is_file()


@pytest.mark.parametrize("name", [n for n, spec in EXPECTED.items() if spec[2] == READ_ONLY])
def test_reviewers_cannot_edit(name: str) -> None:
    meta, _ = parse(name)
    tools = {tool.strip() for tool in meta["tools"].split(",")}
    assert not tools & {"Edit", "Write", "MultiEdit", "NotebookEdit"}


@pytest.mark.parametrize("name", list(EXPECTED))
def test_body_states_the_shared_rules(name: str) -> None:
    _, body = parse(name)
    assert "data/holdout/" in body
    assert "TIFERET_UNSEAL_HOLDOUT" in body
    assert "Never commit, push or open a pull request unless the task says so." in body
    assert "external reader" in body
    assert "CLAUDE.md" in body


@pytest.mark.parametrize("name", ["quant-referee", "tax-auditor", "data-auditor"])
def test_reviewers_return_a_verdict_with_numbered_findings(name: str) -> None:
    _, body = parse(name)
    assert "Verdict: accept | reject" in body
    assert "\n1. " in body
