"""PreToolUse hook: allowlisted Bash for agents that do not write files (plan §8.3; 0009).

Registered for Bash in the frontmatter of the reviewer agents (quant-referee, tax-auditor,
data-auditor) and of the optimizer. Every part of a command, split at `;`, `&&`, `||`, `|`
and newlines, must be on the allowlist:

- `uv run pytest ...`, `uv run python -m tiferet[.module] ...`, `uv run ruff check ...`
  without fixes, `uv run ruff format --check|--diff ...` and `uv run mypy ...`;
- `git diff`, `log`, `show`, `status`, `blame`, `ls-files` and `rev-parse`;
- read-only shell commands such as `ls`, `cat`, `head`, `tail`, `wc`, `grep` and `find`.

Everything else is blocked (exit code 2, reason on stderr), including output redirection
other than to `/dev/null` or between file descriptors, here-documents, command and process
substitution, subshells, background jobs, environment assignments, `tee`, `rm`, `mv`, `cp`,
`git commit`, `push`, `checkout` or `reset`, `pip` and `uv add`, and the flags of allowed
commands that write files or run other programs (`find -exec`, `rg --pre`, `sort -o`,
`ruff check --fix`, `pytest --junitxml`, `mypy --install-types`, `git -c`, `--output`).

Unreadable input is refused. The hook uses only the standard library.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from typing import Any

READ_ONLY_COMMANDS = {
    "ls", "cat", "head", "tail", "wc", "grep", "egrep", "fgrep", "rg", "find", "diff", "cmp",
    "sort", "cut", "tr", "stat", "file", "du", "tree", "pwd", "echo", "printf", "true",
    "test", "basename", "dirname", "realpath", "sha256sum", "md5sum", "nl", "cd",
}  # fmt: skip
# Flags that make an otherwise read-only command write files or run other programs. A
# trailing "=" also matches the flag's "--flag=value" form.
FORBIDDEN_FLAGS = {
    "find": {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0", "-fprintf",
             "-fls"},
    "rg": {"--pre", "--pre-glob"},
    "sort": {"-o", "--output"},
    "tree": {"-o"},
    "file": {"-C", "--compile"},
    "pytest": {"--junitxml", "--junit-xml", "--basetemp", "--report-log", "--result-log",
               "--resultlog", "--pastebin"},
    "ruff check": {"--fix", "--unsafe-fixes", "--fix-only", "--add-noqa", "--output-file", "-o",
                   "--watch", "-w"},
    "mypy": {"--install-types", "--junit-xml"},
    "git": {"--output", "--ext-diff", "--textconv"},
}  # fmt: skip
# Short-flag letters that write, checked inside clustered flags such as `sort -uo out`.
FORBIDDEN_SHORT_LETTERS = {"sort": "o", "tree": "o"}
UV_RUN_OPTIONS = {"--no-sync", "--frozen", "--locked", "--offline", "-q", "--quiet"}
GIT_SUBCOMMANDS = {"diff", "log", "show", "status", "blame", "ls-files", "rev-parse"}
SEPARATORS = {";", "&&", "||", "|", "|&"}
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# Redirections that write no file: descriptor duplication (2>&1) and output to /dev/null.
HARMLESS_REDIRECT = re.compile(r"\d*>&\d+|\d*>\s*/dev/null(?=$|[\s;|&)])")
SUBSTITUTION = re.compile(r"\$\(|`|<\(|>\(")


class BlockedError(Exception):
    """Raised with the reason a command is refused."""


def tokenize(command: str) -> list[str]:
    text = HARMLESS_REDIRECT.sub(" ", command).replace("\r", "").replace("\n", " ; ")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|<>()")
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError as error:
        raise BlockedError(f"the command cannot be parsed ({error})") from error


def split_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in SEPARATORS:
            segments.append([])
        elif token == "<":
            segments[-1].append(token)
        elif token in ("(", ")"):
            raise BlockedError("subshells are not allowed")
        elif token == "&":
            raise BlockedError("background jobs are not allowed")
        elif set(token) <= set(";&|<>()"):
            raise BlockedError(f"the operator '{token}' is not allowed (redirection or here-doc)")
        else:
            segments[-1].append(token)
    return [segment for segment in segments if segment]


def check_flags(key: str, args: list[str]) -> None:
    forbidden = FORBIDDEN_FLAGS.get(key, set())
    letters = FORBIDDEN_SHORT_LETTERS.get(key, "")
    for arg in args:
        flag = arg.split("=", 1)[0]
        if flag in forbidden:
            raise BlockedError(f"'{key} {flag}' writes files or runs other programs")
        if letters and re.fullmatch(r"-[A-Za-z]+", arg) and set(letters) & set(arg[1:]):
            raise BlockedError(f"'{key} {arg}' writes files")
        if key == "mypy" and flag.startswith("--") and flag.endswith("-report"):
            raise BlockedError(f"'mypy {flag}' writes report files")


def check_uv(args: list[str]) -> None:
    if not args or args[0] != "run":
        raise BlockedError("only 'uv run' is allowed (no uv add, pip, sync or tool)")
    rest = args[1:]
    while rest and rest[0].startswith("-"):
        if rest[0] not in UV_RUN_OPTIONS:
            raise BlockedError(f"the uv run option '{rest[0]}' is not allowed")
        rest = rest[1:]
    if not rest:
        raise BlockedError("'uv run' needs an allowlisted command")
    tool, tool_args = rest[0], rest[1:]
    if tool == "pytest":
        check_flags("pytest", tool_args)
    elif tool == "python":
        module = tool_args[1] if len(tool_args) > 1 else ""
        if tool_args[:1] != ["-m"] or not (module == "tiferet" or module.startswith("tiferet.")):
            raise BlockedError("'uv run python' is allowed only as 'uv run python -m tiferet...'")
    elif tool == "ruff" and tool_args[:1] == ["check"]:
        check_flags("ruff check", tool_args[1:])
    elif tool == "ruff" and tool_args[:1] == ["format"]:
        if "--check" not in tool_args and "--diff" not in tool_args:
            raise BlockedError("'ruff format' is allowed only with --check or --diff")
    elif tool == "mypy":
        check_flags("mypy", tool_args)
    else:
        raise BlockedError(f"'uv run {tool}' is not on the allowlist")


def check_git(args: list[str]) -> None:
    rest = list(args)
    while rest and rest[0].startswith("-"):
        if rest[0] == "--no-pager":
            rest = rest[1:]
        elif rest[0] == "-C" and len(rest) > 1:
            rest = rest[2:]
        else:
            raise BlockedError(f"the git option '{rest[0]}' is not allowed")
    if not rest or rest[0] not in GIT_SUBCOMMANDS:
        sub = rest[0] if rest else ""
        raise BlockedError(f"'git {sub}' is not allowed; only {', '.join(sorted(GIT_SUBCOMMANDS))}")
    check_flags("git", rest[1:])


def check_segment(segment: list[str]) -> None:
    if ENV_ASSIGNMENT.match(segment[0]):
        raise BlockedError("environment assignments are not allowed")
    name, args = segment[0], [arg for arg in segment[1:] if arg != "<"]
    if "/" in name or "\\" in name:
        raise BlockedError(f"'{name}': commands are run by name, not by path")
    if name == "uv":
        check_uv(args)
    elif name == "git":
        check_git(args)
    elif name in READ_ONLY_COMMANDS:
        check_flags(name, args)
    else:
        raise BlockedError(f"'{name}' is not on the allowlist")


def check_command(command: str) -> None:
    if SUBSTITUTION.search(command):
        raise BlockedError("command and process substitution are not allowed")
    segments = split_segments(tokenize(command))
    if not segments:
        raise BlockedError("the command is empty")
    for segment in segments:
        check_segment(segment)


def check(payload: dict[str, Any]) -> None:
    if payload.get("tool_name") != "Bash":
        return
    tool_input = payload.get("tool_input") or {}
    check_command(str(tool_input.get("command", "")))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input is not a JSON object")
        check(payload)
    except BlockedError as reason:
        print(
            f"readonly_bash: blocked, {reason}. This agent runs only allowlisted commands "
            "that do not write files (decision 0009).",
            file=sys.stderr,
        )
        return 2
    except Exception as error:  # fail closed: an unreadable request is refused
        print(f"readonly_bash: blocked, could not check the request ({error}).", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
