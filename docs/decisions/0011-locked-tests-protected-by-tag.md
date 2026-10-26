# 0011. Locked tests are protected by tag

- Status: Accepted
- Date: 2026-10-26

## Context

Plan §4 and §8.1 let agents draft tests in `tests/locked/`. The owner approves them before they are tagged, and after tagging no one edits them. The first version of `prereg_guard.py` protected every file that existed in `tests/locked/`, tagged or not. A draft could therefore be written there once, with Write, and never corrected, moved or deleted. During the first look-ahead test, superseded drafts could not be removed. The final draft was parked in a staging folder with a `.draft` suffix, so that pytest, ruff and mypy skipped it, and that folder needed a move once the rule was corrected. The protection belongs to the act of tagging, not to the folder.

## Decision

- **What is protected.** A path under `tests/locked/` is protected when it is in a git tag whose name matches `locked/*`. Untagged files there are drafts, and agents may edit, move or delete them. `research/preregistration.yaml` stays protected at all times, tagged or not.
- **Tag names.** Tags are named `locked/<name>-v<n>`, for example `locked/no-look-ahead-v1`. `<name>` is lowercase words joined by hyphens, and `<n>` starts at 1. A changed locked test gets a new tag `v<n+1>`, approved by the owner like the first. A tag is never moved or deleted, so every earlier version stays protected.
- **Who manages tags.** The owner creates `locked/*` tags. Agents never create, delete, move or rewrite one. `prereg_guard.py` blocks Bash commands that would:
  - create, delete or move a tag: `git tag` with a `locked/*` name, outside `--list` and `--verify`;
  - delete or rewrite one on the remote: `git push` naming a `locked/*` tag, `--delete`, a `:refs/tags/locked/...` refspec, `--mirror` or `--prune`;
  - rewrite the ref: `git update-ref` on `refs/tags/locked/...`, or with `--stdin`;
  - replace or prune local tags: `git fetch` or `git pull` with `--prune-tags`, `-P`, `--force` with `--tags`, a forced tag refspec or a `refs/tags/locked/` refspec;
  - substitute the objects a tag points to: `git replace`, outside `--list`;
  - write anything inside `.git`, where the tags live.
- **How the hook reads tags.** It lists the tags with `git tag --list --ignore-case 'locked/*'`, then each tag's files with `git ls-tree -r -z --name-only`.
  - Paths are compared without case and slash direction, like the rest of the guard. A case-sensitive probe such as `git cat-file -e <tag>:<path>` would let `tests\LOCKED\Test_X.py` through on Windows and macOS.
  - A directory holding a tagged file, and a wildcard that can match one, count as protected.
  - Git is called only when a request touches `tests/locked` or a folder above it.
- **Fail closed.** If git cannot be run, exits with an error, times out or returns undecodable output, every change under `tests/locked/` is blocked with "could not read the locked/* tags".

## Consequences

- A locked-test draft is written, run and corrected in `tests/locked/` itself, next to its harness, with no staging folder.
- The hook trusts the local tags, so it protects only as well as they can be trusted. The binding protections are:
  - a CI check that fails if a file in the highest `locked/*` tag of a name differs from its tagged version, or if `research/preregistration.yaml` changes after its tagged commit (§8.2). It exists as `scripts/check_protected.py`, run by `.github/workflows/ci.yml`; decision 0012 records its rules and the `prereg/v<n>` tag format;
  - a GitHub tag ruleset on `locked/*` that blocks deleting or moving those tags. The owner sets it after the first tag.
- A command run inside a folder that holds a tagged file, or one that names such a folder, is blocked when it writes anything, even if it changes only a draft. Its other arguments cannot all be read as paths.
- A session outside a git repository, or one where git fails, cannot change `tests/locked/` until git works. Requests that do not touch `tests/locked` never call git.
- A clone without the tags protects nothing under `tests/locked/`, because every file there reads as a draft. Examples are `--no-tags` clones and CI checkouts that fetch no tags. Agent sessions work in clones that fetch tags. The CI check fetches the tags explicitly and fails if a `locked/*` tag it expects is missing.
- Commands that act on the whole tree without naming paths are not checked against the tagged files, for example `uv run ruff format .`, `git rm -r .` and `git checkout <branch>`. Blocking them would block the documented format command. The CI check catches any change they make to a tagged file.
