"""Fail when a protected file differs from its tagged version.

Checks (decisions 0011 and 0012, plan section 8.2):

* every tag in ``.github/protected-tags.txt`` exists, and no other ``locked/*`` tag does;
* every ``locked/*`` tag is named ``locked/<name>-v<n>``;
* for each ``<name>``, the files its highest version holds under ``tests/locked/`` are
  identical at HEAD (older versions must exist but are not compared);
* the pre-registration at HEAD equals the one in the highest ``prereg/v<n>`` tag.

Standard library only. Run from the repository root, or pass ``--repo <path>``.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

LOCKED_DIR = "tests/locked/"
PREREG_FILE = "research/preregistration.yaml"
REGISTRY = ".github/protected-tags.txt"
LOCKED_TAG = re.compile(r"^locked/([a-z0-9]+(?:-[a-z0-9]+)*)-v([1-9][0-9]*)$")
PREREG_TAG = re.compile(r"^prereg/v([1-9][0-9]*)$")


class GitError(Exception):
    """A git command failed; the check cannot be trusted and must not pass."""


def git(repo: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            capture_output=True,
            check=False,
            timeout=60,
        )
        out = proc.stdout.decode("utf-8")
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
        raise GitError(f"could not run git {' '.join(args)}: {exc}") from exc
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args)} failed: {err}")
    return out


def blob_id(repo: Path, rev: str, path: str) -> str | None:
    """Blob id of ``path`` at ``rev``, or None when the file is not there."""
    try:
        return git(repo, "rev-parse", "--verify", "--quiet", f"{rev}:{path}").strip() or None
    except GitError:
        return None


def list_tags(repo: Path, pattern: str) -> list[str]:
    return [t for t in git(repo, "tag", "--list", pattern).splitlines() if t]


def read_registry(repo: Path) -> list[str]:
    try:
        lines = (repo / REGISTRY).read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise GitError(f"cannot read {REGISTRY}: {exc}") from exc
    tags = [ln.strip() for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    if not tags:
        raise GitError(f"{REGISTRY} lists no tags; it must list every locked/* tag")
    return tags


def check_locked(repo: Path) -> list[str]:
    problems: list[str] = []
    listed = read_registry(repo)
    existing = set(list_tags(repo, "locked/*"))

    for tag in listed:
        if not LOCKED_TAG.match(tag):
            problems.append(
                f"{REGISTRY}: entry '{tag}' does not match locked/<name>-v<n>; fix the entry."
            )
        elif tag not in existing:
            problems.append(
                f"tag {tag} is listed in {REGISTRY} but does not exist. "
                "Fetch tags, or restore the tag; a protected tag is never deleted."
            )
    for tag in sorted(existing):
        if not LOCKED_TAG.match(tag):
            problems.append(
                f"tag {tag} does not match locked/<name>-v<n> (lowercase words joined by hyphens, "
                "n from 1 without leading zeros)."
            )
        elif tag not in listed:
            problems.append(
                f"tag {tag} exists but is not listed in {REGISTRY}. "
                "The owner adds every locked/* tag to the list."
            )

    # Only the highest version of each name is compared with HEAD.
    latest: dict[str, tuple[int, str]] = {}
    for tag in listed:
        match = LOCKED_TAG.match(tag)
        if match and tag in existing:
            name, version = match.group(1), int(match.group(2))
            if name not in latest or version > latest[name][0]:
                latest[name] = (version, tag)

    for _version, tag in (latest[name] for name in sorted(latest)):
        files = git(repo, "ls-tree", "-r", "-z", "--name-only", tag, "--", LOCKED_DIR)
        for path in (f for f in files.split("\0") if f):
            tagged = blob_id(repo, tag, path)
            current = blob_id(repo, "HEAD", path)
            if current is None:
                problems.append(
                    f"{path} is in tag {tag} but missing at HEAD. "
                    "Restore it, or the owner publishes a new tagged version."
                )
            elif current != tagged:
                problems.append(
                    f"{path} differs from its version in tag {tag}. "
                    "Revert the change, or the owner publishes a new tagged version."
                )
    return problems


def check_prereg(repo: Path) -> tuple[list[str], list[str]]:
    """Return (problems, notices)."""
    problems: list[str] = []
    versions: dict[int, str] = {}
    for tag in list_tags(repo, "prereg/*"):
        match = PREREG_TAG.match(tag)
        if match:
            versions[int(match.group(1))] = tag
        else:
            problems.append(f"tag {tag} does not match prereg/v<n> (n from 1, no leading zeros).")
    if not versions:
        if problems:
            return problems, []
        return [], [f"no prereg/v<n> tag exists yet; {PREREG_FILE} is not checked."]

    tag = versions[max(versions)]
    tagged = blob_id(repo, tag, PREREG_FILE)
    current = blob_id(repo, "HEAD", PREREG_FILE)
    if tagged is None:
        problems.append(f"{PREREG_FILE} is missing in tag {tag}; the tag is not usable.")
    elif current is None:
        problems.append(f"{PREREG_FILE} is missing at HEAD but is in tag {tag}. Restore it.")
    elif current != tagged:
        problems.append(
            f"{PREREG_FILE} differs from its version in tag {tag}. "
            "Revert the change, or the owner publishes a new prereg/v<n> tag."
        )
    return problems, []


def run(repo: Path) -> int:
    try:
        problems = check_locked(repo)
        prereg_problems, notices = check_prereg(repo)
    except GitError as exc:
        print(f"::error::{exc}")
        return 1
    problems += prereg_problems
    for notice in notices:
        print(f"::notice::{notice}")
    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        print(f"{len(problems)} protected-file problem(s).", file=sys.stderr)
        return 1
    print("Protected files match their tags.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository root")
    args = parser.parse_args(argv)
    return run(args.repo)


if __name__ == "__main__":
    sys.exit(main())
