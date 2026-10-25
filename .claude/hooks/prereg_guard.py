"""PreToolUse hook: protect the pre-registration and the locked tests (plan §4, §8.1, §8.2).

Registered for Edit, Write, MultiEdit, NotebookEdit and Bash. Protected targets are
`research/preregistration.yaml` and every existing file in `tests/locked/`.

- Edit, MultiEdit and NotebookEdit on a protected target are blocked.
- Write is blocked when the target exists. Creating a new file is allowed: a draft test in
  `tests/locked/` for the owner to approve, or the empty pre-registration template when none
  exists.
- A Bash command that names a protected target is allowed only when every part of it is
  read-only (cat, grep, git diff, pytest, ...). A redirection into a protected target is
  blocked unless it creates a new file in `tests/locked/`.

BlockedError calls exit with code 2 and the reason on stderr. Paths are resolved against the
session's working directory, `cd` is followed, symlinks are resolved, and comparison ignores
case and slash direction.

The hook applies to every session that loads the project settings, local and cloud. Hooks
can be bypassed, so the binding protection is the CI check that fails if either target
changes after its tagged commit (§8.2). The hook uses only the standard library.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from fnmatch import fnmatchcase
from typing import Any

PREREG = "research/preregistration.yaml"
LOCKED = "tests/locked"
FILE_TOOLS = {"Edit": "file_path", "MultiEdit": "file_path", "Write": "file_path"}
FILE_TOOLS["NotebookEdit"] = "notebook_path"
SEPARATORS = {";", "&", "&&", "||", "|", "|&", "(", ")"}
WRITE_REDIRECTS = {">", ">>", ">|", "&>", "&>>", "<>"}
READ_REDIRECTS = {"<", "<<", "<<<", ">&", "<&"}
READ_ONLY = {
    "cat", "head", "tail", "less", "more", "grep", "egrep", "fgrep", "rg", "wc", "diff",
    "cmp", "ls", "stat", "file", "sha256sum", "sha1sum", "md5sum", "echo", "printf",
    "true", "test", "[", "pwd", "pytest", "mypy", "cd",
}  # fmt: skip
READ_ONLY_GIT = {"status", "diff", "log", "show", "blame", "ls-files", "add", "commit"}
FIND_WRITES = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf"}
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
HEREDOC_START = re.compile(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")


class BlockedError(Exception):
    """Raised with the reason a tool call is refused."""


class Paths:
    """Resolves tool paths to lowercase, slash-separated paths relative to the project."""

    def __init__(self, root: str) -> None:
        self.root = os.path.realpath(root)

    def absolute(self, path: str, cwd: str) -> str:
        return os.path.realpath(os.path.join(cwd, os.path.expanduser(path.replace("\\", "/"))))

    def relative(self, absolute: str) -> str | None:
        try:
            rel = os.path.relpath(absolute, self.root)
        except ValueError:  # another drive on Windows
            return None
        rel = rel.replace("\\", "/").lower()
        return None if rel == ".." or rel.startswith("../") else rel

    def exists(self, absolute: str) -> bool:
        """True if the path exists, comparing names without case as Windows and macOS do."""
        if os.path.lexists(absolute):
            return True
        rel = self.relative(absolute)
        if rel is None:
            return False
        current = self.root
        for part in rel.split("/"):
            try:
                names = {name.lower(): name for name in os.listdir(current)}
            except OSError:
                return False
            if part not in names:
                return False
            current = os.path.join(current, names[part])
        return True


def is_protected(rel: str | None) -> bool:
    return rel is not None and (rel in (PREREG, LOCKED) or rel.startswith(LOCKED + "/"))


def glob_is_protected(rel: str | None) -> bool:
    if rel is None:
        return False
    samples = (PREREG, LOCKED, f"{LOCKED}/test_example.py")
    return any(fnmatchcase(sample, rel) for sample in samples)


def is_new_locked_file(paths: Paths, absolute: str) -> bool:
    rel = paths.relative(absolute)
    return rel is not None and rel.startswith(LOCKED + "/") and not paths.exists(absolute)


def check_file_tool(tool: str, path: str, cwd: str, paths: Paths) -> None:
    absolute = paths.absolute(path, cwd)
    if not is_protected(paths.relative(absolute)):
        return
    if tool == "Write" and not paths.exists(absolute):
        return
    raise BlockedError(f"'{path}' is protected; only the owner changes it")


def strip_heredocs(command: str) -> str:
    """Drop here-document bodies, which are input data rather than commands."""
    kept: list[str] = []
    delimiters: list[str] = []
    for line in command.split("\n"):
        if delimiters:
            if line.strip() == delimiters[0]:
                delimiters.pop(0)
            continue
        kept.append(line)
        delimiters.extend(HEREDOC_START.findall(line))
    return "\n".join(kept)


def tokenize(command: str) -> list[str]:
    text = strip_heredocs(command).replace("\\", "/").replace("\n", " ; ")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|<>()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:  # unbalanced quotes: fall back to a plain split
        return re.findall(r"[;&|<>()]+|[^\s;&|<>()]+", text.replace('"', "").replace("'", ""))


def split_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def is_read_only(args: list[str]) -> bool:
    if not args:
        return True
    name = os.path.basename(args[0]).lower().removesuffix(".exe")
    rest = args[1:]
    if name in READ_ONLY:
        return True
    if name == "find":
        return not FIND_WRITES.intersection(a.lower() for a in rest)
    if name == "git":
        sub = [a for a in rest if not a.startswith("-")]
        return bool(sub) and sub[0].lower() in READ_ONLY_GIT
    if name == "ruff":
        lowered = [a.lower() for a in rest]
        if "check" in lowered:
            return "--fix" not in lowered
        return "format" in lowered and ("--check" in lowered or "--diff" in lowered)
    if name in ("uv", "uvx"):
        if name == "uv":
            if not rest or rest[0] != "run":
                return False
            rest = rest[1:]
        while rest and rest[0].startswith("-"):
            rest = rest[1:]
        return is_read_only(rest)
    if name.startswith("python"):
        return len(rest) >= 2 and rest[0] == "-m" and rest[1] in ("pytest", "mypy")
    return False


def check_command(command: str, cwd: str, paths: Paths) -> None:
    lowered = command.lower().replace("\\", "/")
    mentioned = "preregistration.yaml" in lowered or LOCKED in lowered
    writes = False
    for segment in split_segments(tokenize(command)):
        args: list[str] = []
        tokens = iter(segment)
        for token in tokens:
            if token in WRITE_REDIRECTS:
                target = next(tokens, "")
                absolute = paths.absolute(target, cwd)
                if is_protected(paths.relative(absolute)):
                    if not is_new_locked_file(paths, absolute):
                        raise BlockedError(f"the command writes to protected file '{target}'")
                    mentioned = True
            elif token in READ_REDIRECTS:
                next(tokens, "")
            elif args or not ENV_ASSIGNMENT.match(token):
                args.append(token)
        if not args:
            continue
        for arg in args[1:]:
            if arg.startswith("-"):
                continue
            rel = paths.relative(paths.absolute(arg, cwd))
            if is_protected(rel) or (set("*?[") & set(arg) and glob_is_protected(rel)):
                mentioned = True
        cwd_rel = paths.relative(cwd)
        if cwd_rel is not None and is_protected(cwd_rel):
            mentioned = True
        writes = writes or not is_read_only(args)
        if args[0] == "cd":
            cwd = paths.absolute(args[1], cwd) if len(args) > 1 else paths.root
    if mentioned and writes:
        raise BlockedError("the command names a protected file and is not read-only")


def check(payload: dict[str, Any]) -> None:
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    cwd = payload.get("cwd") or root
    paths = Paths(root)
    if tool in FILE_TOOLS:
        check_file_tool(tool, str(tool_input.get(FILE_TOOLS[tool], "")), cwd, paths)
    elif tool == "Bash":
        check_command(str(tool_input.get("command", "")), cwd, paths)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input is not a JSON object")
        check(payload)
    except BlockedError as reason:
        print(
            f"prereg_guard: blocked, {reason}. research/preregistration.yaml and existing "
            "files in tests/locked/ are owner-approved and not edited by agents; new drafts "
            "in tests/locked/ are created with the Write tool (CLAUDE.md invariant 3, plan §4).",
            file=sys.stderr,
        )
        return 2
    except Exception as error:  # fail closed: an unreadable request is refused
        print(f"prereg_guard: blocked, could not check the request ({error}).", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
