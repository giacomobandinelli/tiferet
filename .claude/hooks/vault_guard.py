"""PreToolUse hook: block agent access to the holdout vault (plan §4, §8.2; decisions 0002, 0008).

Registered for Read, Grep, Glob and Bash. A tool call is blocked (exit code 2, reason on
stderr) when it reaches the vault, not when it merely mentions it:

- a Read, Grep or Glob path or pattern with a `holdout` path segment, or a wildcard segment
  that would expand into `data/holdout`, or a search of all of `data/`;
- a Bash path argument with a `holdout` path segment (`data/holdout` in any spelling, or the
  R2 holdout prefix in an `s3://` or `r2:` URI), or a recursive walk of `data/`;
- a Bash command that sets, exports or passes the unseal flag `TIFERET_UNSEAL_HOLDOUT`.

Plain words are allowed, so commit messages, test names and searches for `holdout`,
`unseal` or the flag's name pass. Paths are compared after lowercasing, turning backslashes
into slashes and resolving `..` and symlinks, so relative, Windows-style and mixed-case
spellings are caught too.

The hook applies to every session that loads the project settings, local and cloud. Hooks
can be bypassed, so the binding protections are the point-in-time loader's research-mode
refusal and the encryption of the holdout (§8.2). The hook uses only the standard library.
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
UNSEAL_FLAG = "tiferet_unseal_holdout"  # compared lowercase; Windows names ignore case
GLOB_CHARS = frozenset("*?[")
# Characters that end a path-like token in a shell command.
TOKEN_SEPARATORS = re.compile(r"[\s'\"`=;|&()<>,{}$]+")
SEGMENT_SEPARATORS = re.compile(r"[/:]+")
CHARACTER_CLASS = re.compile(r"\[[^\]]*\]")
URI_PREFIX = re.compile(r"^(s3a?|r2)://|^r2:")
COMMAND_SEPARATORS = re.compile(r"[;&|\n]+")
WALK_COMMANDS = frozenset({"find", "rg", "tree", "du", "tar", "zip", "rsync"})
WALK_WITH_RECURSIVE_FLAG = frozenset({"grep", "egrep", "fgrep", "ls", "cp", "chmod", "chown"})
RECURSIVE_FLAG = re.compile(r"^-[a-z]*r[a-z]*$|^--recursive$")
FLAG = re.escape(UNSEAL_FLAG)
# Ways a shell, PowerShell, Python or a container runtime sets or passes an environment
# variable. Reading it ($TIFERET_UNSEAL_HOLDOUT, printenv, grep) does not match.
SETS_UNSEAL_FLAG = [
    re.compile(rf"(?<![\w$]){FLAG}\s*:?=(?!=)"),  # FLAG=1, export FLAG=1, $env:FLAG = 1
    re.compile(rf"\b(export|setx|declare|typeset)\b[^;&|\n]*(?<![\w$]){FLAG}\b"),
    re.compile(rf"\b(set-item|new-item)\b[^;&|\n]*env:{FLAG}\b"),
    re.compile(rf"['\"]{FLAG}['\"]\s*\]\s*=(?!=)"),  # os.environ["FLAG"] = "1"
    re.compile(rf"(setenv|putenv|setdefault|setenvironmentvariable)\s*\(\s*['\"]?{FLAG}"),
    re.compile(rf"(\s-e|--env)[\s=]+{FLAG}\b"),  # docker run -e FLAG
]


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


def is_data_dir(path: str, cwd: str, root: str) -> bool:
    resolved = os.path.realpath(os.path.join(cwd, path.replace("\\", "/")))
    return normalize(resolved) == normalize(os.path.realpath(os.path.join(root, DATA)))


def check_path(path: str, cwd: str, root: str) -> None:
    """Refuse a tool path that names the holdout or resolves into it."""
    if not path:
        return
    resolved = os.path.realpath(os.path.join(cwd, path.replace("\\", "/")))
    for candidate in (path, resolved):
        if touches_holdout(path_segments(candidate)):
            raise BlockedError(f"path '{path}' is inside the holdout vault")
    if is_data_dir(path, cwd, root):
        raise BlockedError(f"path '{path}' searches all of data/, which contains the holdout vault")


def is_path_token(token: str) -> bool:
    """A token is a path when it has a separator or is an s3:// or r2: URI; else a word."""
    return "/" in token or bool(URI_PREFIX.match(token))


def walks_data(text: str, cwd: str, root: str) -> bool:
    """True if a command segment recursively walks the data/ directory."""
    for segment in COMMAND_SEPARATORS.split(text):
        words = segment.split()
        if not words:
            continue
        name = os.path.basename(words[0])
        recursive = any(RECURSIVE_FLAG.match(w) for w in words[1:])
        if name not in WALK_COMMANDS and not (name in WALK_WITH_RECURSIVE_FLAG and recursive):
            continue
        if any(is_data_dir(w, cwd, root) for w in words[1:] if not w.startswith("-")):
            return True
    return False


def check_command(command: str, cwd: str, root: str) -> None:
    """Refuse a shell command that reaches the holdout or sets the unseal flag."""
    # Two readings: backslashes as Windows separators, and backslashes and quotes as shell
    # escapes that bash removes (hold\out, hol""dout).
    readings = [normalize(command), re.sub(r"[\\'\"]", "", command.lower())]
    for text in readings:
        if any(pattern.search(text) for pattern in SETS_UNSEAL_FLAG):
            raise BlockedError("the command sets or passes the unseal flag TIFERET_UNSEAL_HOLDOUT")
        for token in TOKEN_SEPARATORS.split(text):
            if is_path_token(token) and touches_holdout(path_segments(token)):
                raise BlockedError(f"the command touches the holdout vault ('{token}')")
        if walks_data(text, cwd, root):
            raise BlockedError("the command walks data/, which contains the holdout vault")


def check(payload: dict[str, Any]) -> None:
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    cwd = payload.get("cwd") or root
    if tool == "Bash":
        check_command(str(tool_input.get("command", "")), cwd, root)
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
            "holdout and is never read (CLAUDE.md invariant 2, plan §4, decision 0008).",
            file=sys.stderr,
        )
        return 2
    except Exception as error:  # fail closed: an unreadable request is refused
        print(f"vault_guard: blocked, could not check the request ({error}).", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
