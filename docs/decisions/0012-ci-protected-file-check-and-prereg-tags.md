# 0012. CI check on protected files, and the pre-registration tag format

- Status: Accepted
- Date: 2026-10-26

## Context

Decision 0011 protects locked tests by tag and names a CI check as the binding protection, because the hooks can be bypassed. Plan §8.2 asks the same of `research/preregistration.yaml`. Both need a tag format the check can read, and a way to publish a new version without the first CI run failing.

## Decision

- **The check.** `scripts/check_protected.py` (standard library only) runs in the `protected-files` job of `.github/workflows/ci.yml`, after a full-history checkout and an explicit `git fetch --force --tags origin`. Its tests are in `tests/scripts/`.
- **Registry.** `.github/protected-tags.txt` lists every `locked/<name>-v<n>` tag that must exist. The check fails if a listed tag is missing, and if a `locked/*` tag exists that is not listed. An entry is never removed.
- **Locked tag format.** `^locked/[a-z0-9]+(-[a-z0-9]+)*-v([1-9][0-9]*)$`. A `locked/*` tag of any other form fails the check.
- **Versions.** Tags are grouped by `<name>`. HEAD is compared only with the highest `n` of each name: every file that version holds under `tests/locked/` must be present and identical at HEAD (blob ids). Older versions must still exist and stay listed, but their files are not compared, so a test can move from `v1` to `v2`. Files under `tests/locked/` that no tag holds are drafts and are not checked (0011).
- **Pre-registration tag format.** `prereg/v<n>` with `n` an integer from 1 without leading zeros, for example `prereg/v1`. The owner creates it on the commit that holds the pre-registration. A tag is never moved or deleted, and a revised pre-registration gets the next `n`. The check compares `research/preregistration.yaml` at HEAD with the highest `n` and fails on any difference, on a missing file, and on a `prereg/*` tag of another form. While no `prereg/v<n>` tag exists the check passes and prints a notice.
- **Failure to read git fails the check.** An unreadable registry, a failed git call or undecodable output exits non-zero.
- **Publishing a new version.** So that the first CI run on the new version already sees its tag, the owner commits, tags, and pushes both at once:

  ```sh
  git tag locked/<name>-v2
  git push --atomic origin main locked/<name>-v2
  ```

  The commit that adds the tag to `.github/protected-tags.txt` and the new test files is the one tagged. The same applies to `prereg/v<n>` with the pre-registration file. A pull request branch is pushed the same way, naming the branch instead of `main`.
- **Workflow security.** The workflow triggers on `push` and `pull_request` only, has top-level `permissions: contents: read` and no secrets, cancels superseded runs on the same ref, sets job timeouts, and pins each third-party action to a full commit SHA with its version in a comment.

## Consequences

- A change to the highest tagged version of a locked test, or to the pre-registration after its tag, fails CI on every push and pull request.
- The check protects only as well as the tags and the registry can be trusted. A tag deleted together with its registry entry passes the check. The owner therefore also sets a GitHub tag ruleset on `locked/*` and `prereg/*` that blocks deleting and moving those tags. This is an owner action and is not done by agents.
- A new `locked/*` tag fails CI until the owner lists it, which makes an unregistered tag visible rather than silently protected.
- Action SHAs are updated by hand when a new release is wanted; the version comment says which release each SHA is.
