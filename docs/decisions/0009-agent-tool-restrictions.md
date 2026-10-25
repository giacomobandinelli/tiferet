# 0009. Tool restrictions for project subagents

- Status: Accepted
- Date: 2026-10-25

## Context

Plan §8.3 defines six subagents. Three of them (quant-referee, tax-auditor, data-auditor) are read-only reviewers, and the optimizer may edit research code only. A subagent's `tools` field can remove the Edit and Write tools, but not narrow them to a directory, and a reviewer still needs Bash to run tests, type checks and the project's own entry points. Unrestricted Bash can write any file (`echo > file`, `sed -i`, `git checkout`), so read-only by tool list alone is not read-only. A denylist of writing commands would miss the many ways a shell can write; an allowlist fails closed when it meets a command it does not know.

## Decision

- **Reviewers** get `Read, Grep, Glob, Bash` and no edit tools. Their Bash goes through `.claude/hooks/readonly_bash.py`, registered in each reviewer's frontmatter. It allows only an explicit list:
  - `uv run pytest`, `uv run python -m tiferet...`, `uv run ruff check` without fixes, `uv run ruff format --check|--diff` and `uv run mypy`;
  - read-only `git` subcommands (`diff`, `log`, `show`, `status`, `blame`, `ls-files`, `rev-parse`);
  - read-only shell commands such as `ls`, `cat`, `head`, `wc`, `grep` and `find`.

  It blocks redirection to files, here-documents, command substitution, subshells, background jobs, environment assignments, and flags of allowed commands that write files or run other programs.
- **The optimizer** gets edit tools, but `.claude/hooks/research_scope.py` blocks Edit, Write, MultiEdit and NotebookEdit outside `src/tiferet/research/` and `research/strategies/`. Paths are resolved for `..`, symlinks and slash direction and compared case-sensitively, so no spelling reaches another directory. The optimizer's Bash also goes through `readonly_bash.py`, so it cannot write outside its scope through the shell. Its backtests run through `uv run python -m tiferet...`, which reaches `run_backtest()` and the ledger.
- **Edit-capable agents** (builder, optimizer, fixer) run `test_gate.py` as a `Stop` hook in their frontmatter, which Claude Code runs as `SubagentStop`. The project-level Stop hook fires only for the main session, so without this a subagent could finish with failing tests.
- The project-level hooks (`vault_guard.py`, `prereg_guard.py`, `lint_on_edit.py`) apply to every agent's tool calls in addition to these.
- All agent hooks are standard-library Python, registered in exec form like the project hooks (§8.2).

## Consequences

- Reviewers cannot fix what they find. Fixes go through the fixer, the only agent that edits after a review (§8.3).
- A reviewer that needs a command outside the allowlist is blocked and reports it, and the allowlist is widened by a reviewed change, not by the agent.
- `uv run pytest` and `uv run python -m tiferet...` run project code, which may write caches, temporary files and ledger rows. That is accepted: the code is reviewed, and ledger rows from reviewers' sensitivity runs are trials that must be counted.
- `research/strategies/` is a directory the optimizer owns. Plan §9 does not yet list it.
- Like all hooks, these can be bypassed (§8.2). They narrow what an agent does by mistake; the binding protections remain the code and CI checks.
