"""PreToolUse hook: keep the optimizer's edits inside research code (plan §8.3; 0009).

Registered for Edit, Write, MultiEdit and NotebookEdit in the optimizer's frontmatter. A
file change is allowed only inside `src/tiferet/research/` or `research/strategies/`;
anything else is blocked (exit code 2, reason on stderr).

The target is resolved against the session's working directory, with backslashes read as
slashes and `..` and symlinks resolved, and compared with the project root. The comparison
is case-sensitive: a spelling such as `SRC/Tiferet/Research/` is refused on every platform,
so a case-insensitive file system cannot turn it into a different directory. Unreadable
input is refused. The hook uses only the standard library.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

ALLOWED_DIRS = ("src/tiferet/research/", "research/strategies/")
PATH_KEYS = {"Edit": "file_path", "MultiEdit": "file_path", "Write": "file_path"}
PATH_KEYS["NotebookEdit"] = "notebook_path"


class BlockedError(Exception):
    """Raised with the reason a change is refused."""


def relative_to_root(path: str, cwd: str, root: str) -> str | None:
    """The target as a slash-separated path relative to the project, or None if outside."""
    absolute = os.path.realpath(os.path.join(cwd, path.replace("\\", "/")))
    try:
        rel = os.path.relpath(absolute, os.path.realpath(root))
    except ValueError:  # another drive on Windows
        return None
    rel = rel.replace("\\", "/")
    return None if rel == ".." or rel.startswith("../") else rel


def check(payload: dict[str, Any]) -> None:
    tool = payload.get("tool_name", "")
    if tool not in PATH_KEYS:
        return
    path = str((payload.get("tool_input") or {}).get(PATH_KEYS[tool], ""))
    root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    rel = relative_to_root(path, payload.get("cwd") or root, root) if path else None
    if rel is None or not rel.startswith(ALLOWED_DIRS):
        raise BlockedError(f"'{path}' is outside {' and '.join(ALLOWED_DIRS)}")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input is not a JSON object")
        check(payload)
    except BlockedError as reason:
        print(
            f"research_scope: blocked, {reason}. The optimizer edits only research code "
            "(plan §8.3, decision 0009).",
            file=sys.stderr,
        )
        return 2
    except Exception as error:  # fail closed: an unreadable request is refused
        print(f"research_scope: blocked, could not check the request ({error}).", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
