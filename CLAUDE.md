# CLAUDE.md

Rules for agents working in this repository. The specification is [docs/plan.md](docs/plan.md); decisions and their reasons are in [docs/decisions/](docs/decisions/). Every rule below cites the plan section or decision record it comes from. If this file and the plan disagree, the plan wins and this file is corrected.

## Invariants

These hold for every task. Hooks enforce some of them in every session that loads the project settings, local and cloud; hooks can be bypassed, so the binding protections live in the code and CI (§8.2).

1. **Point-in-time data only.** Every silver table carries a `known_at` column. Python reads data only through `pit.get(series, as_of=date)` in `src/tiferet/data/pit.py`; SQL features and dbt gold models filter on `known_at <= as_of`. Only raw ingestion by the adapters in `src/tiferet/data/` is exempt. Any other direct read of raw files is a bug. (§8.1, §10.1, 0003, 0005)
2. **The holdout is never read.** Data from 2023-01-01 onward is the holdout, stored under a separate encrypted prefix in R2, with `data/holdout/` as its local equivalent. Never read, list or search either, and never set the unseal flag `TIFERET_UNSEAL_HOLDOUT`. (§4, §8.1, 0002, 0008)
   - **Research mode** (backtests, optimization, model selection) refuses any date after 2022-12-31 unless the unseal flag is set.
   - **Production mode** (nightly ingestion, data-quality pages and, after the phase 5 freeze, the live weights) may read current data. Before the freeze, post-2022 data is only ingested and stored, never used by any model or backtest.
3. **Protected files are not edited.** The owner writes `research/preregistration.yaml`; agents may create only an empty template of it. Agents may draft new tests in `tests/locked/`, which the owner approves before they are tagged. After tagging, no one edits either. (§4, §8.1)
4. **Every backtest goes through `run_backtest()`** in `src/tiferet/research/`, which appends the run to `research/ledger.parquet`. No backtest runs outside it, exploratory ones included. (§4, §8.1, 0005)
5. **Every cost and tax number is sourced.** All fees, including TER, and every tax parameter live in `config/costs_taxes.yaml` with a source URL and the date checked. `config/holdings.yaml` holds instrument identity only. No cost or tax constant is written into code. (§3.8, §8.1, §10.6, 0004)
6. **No language model produces or alters a published number.** The nightly pipeline is deterministic code. (§6)

### Definition of done

A task is done only when all of the following hold (§8.1, §8.5):

- `uv run pytest` is green.
- `ruff check` and `ruff format --check` report nothing.
- `mypy --strict` on `src` and `tests` reports no errors.
- The trial ledger is updated, for tasks that run a backtest.
- A referee report is written in `research/referee/`, for tasks that produce a published result.
- The task's own acceptance checks pass.

## Publication hygiene

Everything tracked by git is written for an external reader: code, comments, docs, commit messages and pull-request descriptions. They contain no conversational text, personal notes, notes addressed to the repository owner, or exploratory leftovers. (§8.1)

Commit messages and pull-request descriptions describe the change only. They contain no `Co-Authored-By` or `Claude-Session` trailers, no "Generated with" lines, and no notes about approval, review status or who made the change; `.claude/settings.json` sets `attribution` so Claude Code adds none of these. (§8.1)

Working notes, scratch analyses and messages for the owner go in `notes/`, which is gitignored. Nothing under `notes/` is staged or committed. (§8.1, §9)

## Repository map

Where things live (§9). Details are read on demand, not copied here (§8.5). Folders are created by the roadmap phase that needs them (§11).

| Path | Contents |
| --- | --- |
| `docs/plan.md` | The specification |
| `docs/decisions/` | Decision records; read the relevant ones before revisiting a settled question |
| `config/` | `holdings.yaml` (instrument identity: ISIN, tax class, launch date, `exposure_available_from`, sources) and `costs_taxes.yaml` (every fee and tax parameter, sourced and dated) |
| `research/` | `preregistration.yaml` (protected), `ledger.parquet` (every backtest), `referee/` (one report per published result) |
| `src/tiferet/data/` | One adapter per source; point-in-time accessor in `pit.py` |
| `src/tiferet/features/` | Feature library (SQL in DuckDB and Python) |
| `src/tiferet/models/` | Cycle-phase models, Markov switching, spillover |
| `src/tiferet/portfolio/` | Ladder, optimizer, costs, tax engine, scorecard, Monte Carlo |
| `src/tiferet/research/` | `run_backtest()` and the ledger writer; I/O allowed |
| `src/tiferet/reports/` | Excel, PowerPoint and website data exports |
| `dbt/` | Bronze, silver and gold models and their tests |
| `web/` | Website (React, Three.js); design system in `web/ui` |
| `tests/fixtures/` | Small datasets so every test runs without credentials |
| `tests/locked/` | Protected tests, including the no-look-ahead test for Python and SQL features |
| `.claude/` | Settings and hooks, agents, workflows, skills |
| `.github/workflows/` | CI, nightly pipeline, manually triggered agent jobs |
| `data/` | Local data, gitignored; `data/holdout/` is never read |
| `notes/` | Untracked working notes, gitignored |

Top-level files: `REVIEW.md` (review checklist), `DESIGN.md` (design system), `README.md` (headline result, how to run), `pyproject.toml` (uv-managed, pinned dependencies).

## Task contracts and change receipts

### Before starting

Every task starts from a contract (§8.5):

- **Objective.**
- **Scope:** the files and modules it may change.
- **Constraints:** what must not change.
- **Acceptance checks:** the exact commands and tests that prove completion.
- **Approvals needed:** actions that require a human. Always included: pushing, unsealing the holdout, changing the pre-registration.

Larger tasks keep their contract in the pull-request description. (§8.5)

### While working

- A task is complete only when its acceptance checks pass. (§8.5)
- Decisions and their reasons are recorded in `docs/decisions/`, so later sessions do not reopen them. (§8.5, 0001)
- General facts about the environment, and lessons from failures that should change future behaviour, are added to this file. Personal working notes go in `notes/`. (§8.5)
- Every change is reviewed with `/code-review` before merge, `ultra` at phase gates. Reviews run in a fresh context against [REVIEW.md](REVIEW.md) and try to break the result rather than confirm it. (§8.4, §8.5)
- Additional agents, tools or orchestration are added only when a recorded failure shows they are needed. (§8.5)

### At the end

Every run ends with a change receipt in the pull-request description, or in the session's final message when there is no pull request (§8.5):

- Objective
- What changed
- What was verified (commands and results)
- What was not verified
- Decisions taken
- Open risks
- Approvals needed

Backtest runs also write their receipt to the trial ledger. (§8.5)

## Hooks and permissions

`.claude/settings.json` registers four standard-library Python hooks from `.claude/hooks/` for every session and agent, each started with `uv run python` in exec form (`"command": "uv"` with an `args` list). Claude Code substitutes `${CLAUDE_PROJECT_DIR}` itself and no shell is involved, so the hooks run the same under Git Bash, PowerShell and `sh` (§8.2). Tests for them are in `tests/hooks/`.

| Hook | Event | Behaviour |
| --- | --- | --- |
| `vault_guard.py` | PreToolUse (Read, Grep, Glob, Bash) | Blocks paths, wildcards and commands that reach `data/holdout` or the R2 holdout prefix (`s3://`, `r2:`), or walk all of `data/`, in any case, slash direction, `..` or symlink; blocks commands that set, export or pass `TIFERET_UNSEAL_HOLDOUT` |
| `prereg_guard.py` | PreToolUse (Edit, Write, MultiEdit, NotebookEdit, Bash) | Blocks changes to `research/preregistration.yaml` and to existing files in `tests/locked/`; allows creating a new draft there with Write, and read-only Bash commands |
| `lint_on_edit.py` | PostToolUse (Edit, Write, MultiEdit) | Runs `ruff format` and `ruff check --fix` on the changed Python file and reports what remains; never blocks |
| `test_gate.py` | Stop | Blocks stopping while `uv run pytest -m "not slow" -q` fails; allows it when `stop_hook_active` is set |

`permissions.deny` refuses pushes to `main` and force pushes (agents push their own branches for pull requests), edits to the pre-registration, and reads of `data/holdout`.

Hooks and permission rules apply in every session that loads the project settings, local and cloud. Hooks can be bypassed, so the binding protections are still the code and CI checks: the point-in-time loader's holdout refusal, the locked tests, the CI check on tagged protected files, and the holdout's encryption (§8.2). The vault guard blocks access, not words: commit messages, test names and searches for `holdout`, `unseal` or the flag's name are allowed (0008). The pre-registration guard blocks a Bash command that names a protected file and also writes anything, so keep such commands separate from writes such as `git push`. It also refuses any change to a file that already exists in `tests/locked/`, an untracked draft included, so a locked-test draft is developed and run outside that folder and written there once, with Write, when final.

## Agents

Six project subagents live in `.claude/agents/` (§8.3, §8.6). Their frontmatter pins name, tools, model and effort, and adds agent-scoped hooks on top of the project hooks above (0009). `tests/hooks/test_agents.py` pins all of it.

| Agent | Model, effort | Tools | Agent-scoped hooks | Responsibility |
| --- | --- | --- | --- | --- |
| `builder` | sonnet, medium | Edit, Write, Bash, read tools | `test_gate.py` on stop | Implements an agreed plan only; ends with a change receipt |
| `optimizer` | opus, high | Edit, Write, Bash, read tools | `research_scope.py`, `readonly_bash.py`, `test_gate.py` on stop | Strategies inside the pre-registered space, development data only, every backtest through `run_backtest()`; edits only `src/tiferet/research/` and `research/strategies/` |
| `quant-referee` | opus, high | Read, Grep, Glob, Bash | `readonly_bash.py` | Tries to break a result with the `REVIEW.md` rubric; accept or reject with numbered findings |
| `tax-auditor` | opus, high | Read, Grep, Glob, Bash, WebFetch | `readonly_bash.py` | Checks each tax rule against its fetched source in `config/costs_taxes.yaml` and worked examples; an unfetchable source is "not verified against source" with the URL tried |
| `data-auditor` | haiku, low | Read, Grep, Glob, Bash | `readonly_bash.py` | Checks gaps, outliers, units, publication dates and `known_at` |
| `fixer` | sonnet, medium | Edit, Write, Bash, read tools | `test_gate.py` on stop | Applies numbered findings one by one; reports each as fixed or not fixed with the reason |

`readonly_bash.py` allows only allowlisted commands that write no files (tests, `python -m tiferet`, lint and type checks, read-only `git` and shell). `research_scope.py` blocks file changes outside the optimizer's two directories. No agent commits, pushes or opens a pull request unless its task says so.

## Models and effort

| Task | Model | Effort |
| --- | --- | --- |
| Phase planning, architecture, pre-registration review | Opus | high |
| Tax rules, cost model, look-ahead checks | Opus | medium, step by step |
| Implementation from an agreed plan | Sonnet, Opus as advisor on long builds | medium |
| File scanning, data checks, documentation lookups | Haiku | low |
| Referee and audit reviews, including the tax-auditor | Opus | high |
| Large multi-file builds at the start of a phase | Opus with ultracode | as needed |

Sessions start fresh for unrelated tasks, plans precede implementation, and exploration runs in subagents to keep the main context small. This file stays under 200 lines. (§8.6)

## Commands

Run from the repository root once `pyproject.toml` exists (§9, §11 phase 1).

```sh
uv sync                        # install pinned dependencies from uv.lock (§10.7)
uv run pytest                  # full test suite (§8.1)
uv run pytest -m "not slow"    # fast suite, run by the Stop hook (§8.2)
uv run ruff format .           # format (§8.2)
uv run ruff format --check .   # format check (§8.1)
uv run ruff check .            # lint (§8.1)
uv run mypy --strict src tests # type check (§8.1, §10.3)
```
