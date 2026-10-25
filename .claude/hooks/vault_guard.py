"""PreToolUse hook: block agent access to the holdout vault (plan §4, §8.2; decision 0002).

Registered for Read, Grep, Glob and Bash. A tool call is blocked (exit code 2, reason on
stderr) when it names a `holdout` path segment (the local `data/holdout/` or the R2 holdout
prefix), uses a wildcard that would expand into `data/holdout`, searches the whole `data/`
directory, or mentions the unseal flag. Paths are compared after lowercasing, turning
backslashes into slashes and resolving `..` and symlinks, so relative, Windows-style and
mixed-case spellings are caught too.

The hook applies to local sessions only. The binding protections are the point-in-time
loader's research-mode refusal and the encryption of the holdout (§8.2). The hook uses only
the standard library.
"""

from __future__ import annotations

import json
import os
import re
import sys
from fnmatch import fnmatchcase
from typing import Any

HOLDOUT = "holdout"
DATA = "data"
UNSEAL = "unseal"
GLOB_CHARS = frozenset("*?[")
# Characters that end a path-like token in a shell command.
TOKEN_SEPARATORS = re.compile(r"[\s'\"`=;|&()<>,{}$]+")
SEGMENT_SEPARATORS = re.compile(r"[/:]+")
CHARACTER_CLASS = re.compile(r"\[[^\]]*\]")
# Commands that walk a directory tree, and so reach data/holdout when pointed at data/.
RECURSIVE_WALK = re.compile(
    r"\b(find|rg|tree|du|tar|zip|rsync)\b"
    r"|\b(grep|ls|cp|chmod|chown)\b[^;|&]*\s-[a-z]*r"
)
DATA_DIR_TOKEN = re.compile(r"(^|[\s'\"=/])data/?(?=$|[\s'\";|&)])")


class BlockedError(Exception):
    """Raised with the reason a tool call is refused."""


def normalize(text: str) -> str:
    """Lowercase and turn backslashes into forward slashes."""
    return text.lower().replace("\\", "/")


def touches_holdout(segments: list[str]) -> bool:
    """True if a list of path segments names or would expand into the holdout."""
    for i, segment in enumerate(segments):
        if segment == HOLDOUT:
            return True
        if not GLOB_CHARS.intersection(segment) or not fnmatchcase(HOLDOUT, segment):
            continue
        # A wildcard right under data/ (data/*, data/h*) or one that spells out part of the
        # name outside a character class (h*, hold?ut) would expand into the holdout.
        literal = CHARACTER_CLASS.sub("", segment)
        if (i > 0 and segments[i - 1] == DATA) or any(c.isalpha() for c in literal):
            return True
    return False


def path_segments(path: str) -> list[str]:
    return [s for s in SEGMENT_SEPARATORS.split(normalize(path)) if s]


def check_path(path: str, cwd: str, root: str) -> None:
    """Refuse a tool path that names the holdout or resolves into it."""
    if not path:
        return
    resolved = os.path.realpath(os.path.join(cwd, path.replace("\\", "/")))
    for candidate in (path, resolved):
        if touches_holdout(path_segments(candidate)):
            raise BlockedError(f"path '{path}' is inside the holdout vault")
    if normalize(resolved) == normalize(os.path.join(root, DATA)):
        raise BlockedError(f"path '{path}' searches all of data/, which contains the holdout vault")


def check_command(command: str) -> None:
    """Refuse a shell command that names the holdout, its R2 prefix or the unseal flag."""
    # Two readings: backslashes as Windows separators, and backslashes and quotes as shell
    # escapes that bash removes (hold\out, hol""dout).
    readings = [normalize(command), re.sub(r"[\\'\"]", "", command.lower())]
    for text in readings:
        if UNSEAL in text:
            raise BlockedError("the command mentions the unseal flag")
        for token in TOKEN_SEPARATORS.split(text):
            if token and touches_holdout(path_segments(token)):
                raise BlockedError(f"the command touches the holdout vault ('{token}')")
        if RECURSIVE_WALK.search(text) and DATA_DIR_TOKEN.search(text):
            raise BlockedError("the command walks data/, which contains the holdout vault")


def check(payload: dict[str, Any]) -> None:
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    cwd = payload.get("cwd") or root
    if tool == "Bash":
        check_command(str(tool_input.get("command", "")))
    elif tool == "Read":
        check_path(str(tool_input.get("file_path", "")), cwd, root)
    elif tool in ("Grep", "Glob"):
        base = str(tool_input.get("path") or "")
        check_path(base, cwd, root)
        for key in ("pattern", "glob") if tool == "Glob" else ("glob",):
            pattern = str(tool_input.get(key) or "")
            if pattern:
                joined = f"{base}/{pattern}" if base else pattern
                if touches_holdout(path_segments(joined)):
                    raise BlockedError(f"pattern '{pattern}' would match the holdout vault")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input is not a JSON object")
        check(payload)
    except BlockedError as reason:
        print(
            f"vault_guard: blocked, {reason}. Data from 2023-01-01 onward is the sealed "
            "holdout and is never read (CLAUDE.md invariant 2, plan §4).",
            file=sys.stderr,
        )
        return 2
    except Exception as error:  # fail closed: an unreadable request is refused
        print(f"vault_guard: blocked, could not check the request ({error}).", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
