"""PreToolUse hook: protect the pre-registration and the tagged locked tests (plan §4, §8.1, §8.2).

Registered for Edit, Write, MultiEdit, NotebookEdit and Bash. Protected targets are
`research/preregistration.yaml` and every path under `tests/locked/` that is in a git tag
matching `locked/*` (decision 0011). Untagged files in `tests/locked/` are drafts and may be
edited, moved or deleted.

- Edit, MultiEdit, NotebookEdit and Write on a protected target are blocked, also when a tagged
  file is missing from disk. Write may create the empty pre-registration template when none
  exists.
- A Bash command that names a protected target, or a directory holding one, is allowed only
  when every part of it is read-only (cat, grep, git diff, pytest, ...). A redirection into a
  protected target is blocked.
- A Bash command that creates, deletes, moves or rewrites a `locked/*` tag, or that writes
  anything inside `.git`, is blocked: agents never manage those tags.
- Commands on the whole tree (`ruff format .`, `git checkout <branch>`) are not checked
  against the tagged paths; the CI check catches what they change (decision 0011).

The tagged paths are read with `git tag --list` and `git ls-tree`, only when a request touches
`tests/locked`. If git cannot be run or answers with an error, the request is blocked.

BlockedError calls exit with code 2 and the reason on stderr. Paths are resolved against the
session's working directory, `cd` is followed, symlinks are resolved, and comparison ignores
case and slash direction.

The hook applies to every session that loads the project settings, local and cloud. Hooks
can be bypassed, so the binding protections are the CI check that fails if a tagged file
changes and the repository's tag ruleset (§8.2, 0011). The hook uses only the standard library.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from fnmatch import fnmatchcase
from typing import Any

PREREG = "research/preregistration.yaml"
LOCKED = "tests/locked"
TAG_PATTERN = "locked/*"
TAG_PREFIX = "locked/"
TAG_REF = "refs/tags/locked/"
GIT_TIMEOUT = 10
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
GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
TAG_LIST_FLAGS = {"-l", "--list", "-v", "--verify"}
TAG_WRITE_FLAGS = {"-d", "--delete", "-f", "--force"}
FIND_WRITES = {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprint0"}
FIND_WRITES |= {"-fprintf", "-fls"}
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
HEREDOC_START = re.compile(r"(?<!<)<<(?!<)-?\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?")
LOCKED_MENTION = re.compile(r"(?<![\w.-])tests/locked(?:/[^\s'\"`;&|<>()]*)?(?![\w.-])")
GLOB_CHARS = set("*?[{")
BRACES = re.compile(r"\{[^{}]*\}")
GIT_STORE = ".git"
GIT_STORE_MENTION = re.compile(r"(?<![\w.-])\.git/")


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


def under_locked(rel: str) -> bool:
    return rel == LOCKED or rel.startswith(LOCKED + "/")


def in_git_store(rel: str | None) -> bool:
    return rel is not None and (rel == GIT_STORE or rel.startswith(GIT_STORE + "/"))


def may_hold_locked(rel: str) -> bool:
    """True for `tests/locked`, a path below it, or a directory above it other than the root."""
    return under_locked(rel) or LOCKED.startswith(rel + "/")


class Protection:
    """Decides which project-relative paths are protected, reading the tags at most once."""

    def __init__(self, root: str) -> None:
        self.root = root
        self._tagged: frozenset[str] | None = None

    def git(self, *args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", self.root, *args],
                capture_output=True,
                timeout=GIT_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise BlockedError(f"could not read the locked/* tags ({error})") from error
        if result.returncode != 0:
            message = result.stderr.decode("utf-8", "replace").strip()
            raise BlockedError(f"could not read the locked/* tags (git {args[0]}: {message})")
        try:
            return result.stdout.decode("utf-8")
        except UnicodeDecodeError as error:
            raise BlockedError(f"could not read the locked/* tags ({error})") from error

    def tagged(self) -> frozenset[str]:
        """Lowercased paths under `tests/locked/` in any `locked/*` tag, with their parents."""
        if self._tagged is None:
            found: set[str] = set()
            for tag in self.git("tag", "--list", "--ignore-case", TAG_PATTERN).split():
                listing = self.git("ls-tree", "-r", "-z", "--name-only", f"refs/tags/{tag}")
                for path in listing.split("\0"):
                    path = path.lower()
                    if path.startswith(LOCKED + "/"):
                        parts = path.split("/")
                        found.update("/".join(parts[:n]) for n in range(1, len(parts) + 1))
            self._tagged = frozenset(found)
        return self._tagged

    def covers(self, rel: str | None) -> bool:
        """True for the pre-registration, a tagged file, or a directory holding one."""
        if rel is None:
            return False
        if rel == PREREG:
            return True
        return may_hold_locked(rel) and rel in self.tagged()

    def covers_glob(self, rel: str | None) -> bool:
        """True if a wildcard path can match the pre-registration or a tagged path."""
        if rel is None:
            return False
        rel = BRACES.sub("*", rel)  # a brace expansion matches no more than a wildcard
        if fnmatchcase(PREREG, rel):
            return True
        literal = re.split(r"[*?\[]", rel, maxsplit=1)[0]
        if not (literal.startswith(LOCKED + "/") or (LOCKED + "/").startswith(literal)):
            return False
        return any(fnmatchcase(path, rel) for path in self.tagged())


def check_file_tool(tool: str, path: str, cwd: str, paths: Paths, guard: Protection) -> None:
    absolute = paths.absolute(path, cwd)
    rel = paths.relative(absolute)
    if not guard.covers(rel):
        return
    if rel == PREREG and tool == "Write" and not paths.exists(absolute):
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
        for match in HEREDOC_START.finditer(line):
            before = line[: match.start()]
            if before.count("'") % 2 == 0 and before.count('"') % 2 == 0:  # not quoted
                delimiters.append(match.group(1))
    return "\n".join(kept)


def tokenize(command: str) -> list[str]:
    text = strip_heredocs(command).replace("\\", "/").replace("\n", " ; ")
    text = text.replace("`", " ; ")  # a command substitution is a command of its own
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


def git_subcommand(rest: list[str]) -> tuple[str, list[str]]:
    """The git subcommand, lowercased, and its arguments, skipping global options."""
    args = iter(rest)
    for arg in args:
        if arg in GIT_OPTIONS_WITH_VALUE:
            next(args, "")
        elif not arg.startswith("-"):
            return arg.lower(), list(args)
    return "", []


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
        sub, sub_args = git_subcommand(rest)
        writes_output = any(a.lower().startswith("--output") for a in sub_args)
        return sub in READ_ONLY_GIT and not writes_output
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


def names_locked_tag(arg: str) -> bool:
    """True if a tag name, ref or refspec names a `locked/*` tag."""
    for side in arg.lower().lstrip("+").split(":"):
        if side.startswith(TAG_PREFIX) or TAG_REF in side:
            return True
    return False


def has_short_flag(args: list[str], flag: str) -> bool:
    return any(a.startswith("-") and not a.startswith("--") and flag in a[1:] for a in args)


def check_tag_change(args: list[str]) -> None:
    """Block git commands that create, delete, move or rewrite a `locked/*` tag (0011)."""
    if not args or os.path.basename(args[0]).lower().removesuffix(".exe") != "git":
        return
    sub, rest = git_subcommand(args[1:])
    lowered = [a.lower() for a in rest]
    options = {a.split("=")[0] for a in lowered if a.startswith("-")}
    positional = [a for a in rest if not a.startswith("-")]
    reason = ""
    if sub == "tag":
        listing = bool(TAG_LIST_FLAGS & options) and not TAG_WRITE_FLAGS & options
        if not listing and any(names_locked_tag(a) for a in positional):
            reason = "the command changes a locked/* tag"
    elif sub == "push":
        if {"--mirror", "--prune"} & options or any(names_locked_tag(a) for a in positional):
            reason = "the command pushes or deletes locked/* tags"
    elif sub == "update-ref":
        if "--stdin" in options or any(TAG_REF in a for a in lowered):
            reason = "the command rewrites a locked/* tag ref"
    elif sub == "replace":
        if positional and not {"-l", "--list"} & options:
            reason = "the command replaces objects that locked/* tags point to"
    elif sub in ("fetch", "pull"):
        prunes = "--prune-tags" in options or has_short_flag(rest, "P")
        forced = "--force" in options or has_short_flag(rest, "f")
        all_tags = "--tags" in options or has_short_flag(rest, "t")
        forced_tags = (forced and all_tags) or any(
            a.startswith("+") and "refs/tags/" in a for a in lowered
        )
        if prunes or forced_tags or any(TAG_REF in a for a in lowered):
            reason = "the command can delete or move locked/* tags"
    if reason:
        raise BlockedError(f"{reason}; agents never manage locked/* tags (decision 0011)")


def check_command(command: str, cwd: str, paths: Paths, guard: Protection) -> None:
    lowered = command.lower().replace("\\", "/")
    named: list[str | None] = []
    globs: list[str | None] = []
    if "preregistration.yaml" in lowered:
        named.append(PREREG)
    named.extend(mention.rstrip("/") for mention in LOCKED_MENTION.findall(lowered))
    git_store = bool(GIT_STORE_MENTION.search(lowered))
    writes = False
    for segment in split_segments(tokenize(command)):
        args: list[str] = []
        tokens = iter(segment)
        for token in tokens:
            if token in WRITE_REDIRECTS:
                target = next(tokens, "")
                if guard.covers(paths.relative(paths.absolute(target, cwd))):
                    raise BlockedError(f"the command writes to protected file '{target}'")
                writes = True
            elif token in READ_REDIRECTS:
                next(tokens, "")
            elif args or not ENV_ASSIGNMENT.match(token):
                args.append(token)
        if not args:
            continue
        check_tag_change(args)
        for arg in args[1:]:
            if arg.startswith("-"):
                continue
            rel = paths.relative(paths.absolute(arg, cwd))
            (globs if GLOB_CHARS & set(arg) else named).append(rel)
            git_store = git_store or in_git_store(rel)
        cwd_rel = paths.relative(cwd)
        if cwd_rel is not None and under_locked(cwd_rel):
            named.append(cwd_rel)
        git_store = git_store or in_git_store(cwd_rel)
        writes = writes or not is_read_only(args)
        if args[0] == "cd":
            cwd = paths.absolute(args[1], cwd) if len(args) > 1 else paths.root
    if not writes:
        return
    if git_store:
        raise BlockedError("the command writes inside .git, where the tags live")
    if any(guard.covers(rel) for rel in named) or any(guard.covers_glob(rel) for rel in globs):
        raise BlockedError("the command names a protected file and is not read-only")


def check(payload: dict[str, Any]) -> None:
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    cwd = payload.get("cwd") or root
    paths = Paths(root)
    guard = Protection(paths.root)
    if tool in FILE_TOOLS:
        check_file_tool(tool, str(tool_input.get(FILE_TOOLS[tool], "")), cwd, paths, guard)
    elif tool == "Bash":
        check_command(str(tool_input.get("command", "")), cwd, paths, guard)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise ValueError("hook input is not a JSON object")
        check(payload)
    except BlockedError as reason:
        print(
            f"prereg_guard: blocked, {reason}. research/preregistration.yaml and files in a "
            "locked/* tag are owner-approved and not edited by agents; untagged drafts in "
            "tests/locked/ may change (CLAUDE.md invariant 3, plan §4, decision 0011).",
            file=sys.stderr,
        )
        return 2
    except Exception as error:  # fail closed: an unreadable request is refused
        print(f"prereg_guard: blocked, could not check the request ({error}).", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
