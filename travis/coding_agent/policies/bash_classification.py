"""Conservative, advisory classification of shell commands by mutation risk.

This module deliberately does not participate in authorization.  Its result is
only a hint for progress-loop bookkeeping: ``UNKNOWN`` must be treated as
potentially mutating by consumers.
"""

from __future__ import annotations

import ast
import re
import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePath


class BashMutationClass(str, Enum):
    READ_ONLY = "read_only"
    MUTATING = "mutating"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BashMutationHint:
    classification: BashMutationClass
    reason: str


_FILE_MUTATORS = frozenset(
    {
        "chmod",
        "chown",
        "chgrp",
        "cp",
        "install",
        "ln",
        "mkdir",
        "mkfifo",
        "mknod",
        "mv",
        "rm",
        "rmdir",
        "tee",
        "touch",
        "truncate",
    }
)
_PROCESS_MUTATORS = frozenset({"kill", "killall", "pkill"})
_READ_ONLY_EXECUTABLES = frozenset(
    {
        ":",
        "[",
        "awk",
        "basename",
        "cat",
        "cut",
        "date",
        "dirname",
        "du",
        "echo",
        "env",
        "false",
        "file",
        "grep",
        "head",
        "id",
        "ls",
        "printf",
        "ps",
        "pwd",
        "readlink",
        "rg",
        "sed",
        "sort",
        "stat",
        "tail",
        "test",
        "tr",
        "true",
        "uname",
        "uniq",
        "wc",
        "whoami",
    }
)
_PACKAGE_MUTATION_ACTIONS = frozenset(
    {"add", "ci", "i", "install", "remove", "sync", "uninstall", "update", "upgrade"}
)
_GIT_READ_ONLY_ACTIONS = frozenset(
    {
        "blame",
        "cat-file",
        "describe",
        "diff",
        "grep",
        "log",
        "ls-files",
        "rev-list",
        "rev-parse",
        "shortlog",
        "show",
        "status",
    }
)
_GIT_MUTATION_ACTIONS = frozenset(
    {
        "add",
        "am",
        "apply",
        "checkout",
        "cherry-pick",
        "clean",
        "clone",
        "commit",
        "fetch",
        "gc",
        "init",
        "merge",
        "mv",
        "pull",
        "push",
        "rebase",
        "reset",
        "restore",
        "revert",
        "rm",
        "stash",
        "switch",
        "worktree",
    }
)
_PYTHON_MUTATING_METHODS = frozenset(
    {
        "chmod",
        "hardlink_to",
        "link",
        "makedirs",
        "mkdir",
        "remove",
        "rename",
        "renames",
        "replace",
        "rmdir",
        "removedirs",
        "symlink",
        "symlink_to",
        "touch",
        "truncate",
        "unlink",
        "write",
        "write_bytes",
        "write_text",
        "writelines",
    }
)
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SHELL_SEPARATORS = frozenset({"&&", "||", ";", "|", "&"})
_INPUT_REDIRECTIONS = frozenset({"<", "<<", "<<<", "<&"})
_OUTPUT_REDIRECTIONS = frozenset({">", ">>", "&>", ">&", "<>", ">|"})


def classify_bash_mutation(command: str) -> BashMutationHint:
    """Return a conservative mutation hint for a static shell command."""

    stripped = command.strip()
    if not stripped:
        return BashMutationHint(BashMutationClass.READ_ONLY, "empty command")
    try:
        lexer = shlex.shlex(stripped, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError as error:
        return BashMutationHint(BashMutationClass.UNKNOWN, f"shell parse failed: {error}")

    tokens = _without_descriptor_duplications(tokens)
    if any(
        token in _OUTPUT_REDIRECTIONS or (">" in token and _is_shell_punctuation(token))
        for token in tokens
    ):
        return BashMutationHint(BashMutationClass.MUTATING, "shell output redirection")

    segment_hints = [_classify_segment(segment) for segment in _segments(tokens) if segment]
    for hint in segment_hints:
        if hint.classification is BashMutationClass.MUTATING:
            return hint

    if any(marker in stripped for marker in ("$(", "<(", ">(", "`")):
        return BashMutationHint(BashMutationClass.UNKNOWN, "dynamic command substitution")
    if "\n" in stripped:
        return BashMutationHint(BashMutationClass.UNKNOWN, "multi-line shell input")
    for hint in segment_hints:
        if hint.classification is BashMutationClass.UNKNOWN:
            return hint
    if not segment_hints:
        return BashMutationHint(BashMutationClass.READ_ONLY, "no executable command")
    return BashMutationHint(BashMutationClass.READ_ONLY, "all static command segments are read-only")


def _segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _SHELL_SEPARATORS or _is_shell_separator(token):
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _classify_segment(raw_tokens: list[str]) -> BashMutationHint:
    tokens = _without_input_redirections(raw_tokens)
    executable_index = _executable_index(tokens)
    if executable_index is None:
        return BashMutationHint(BashMutationClass.READ_ONLY, "assignment or redirection only")
    executable = PurePath(tokens[executable_index]).name.lower()
    args = tokens[executable_index + 1 :]
    lowered_args = [value.lower() for value in args]

    if executable in _FILE_MUTATORS:
        return BashMutationHint(BashMutationClass.MUTATING, f"state-mutating command: {executable}")
    if executable in _PROCESS_MUTATORS:
        return BashMutationHint(BashMutationClass.MUTATING, f"process-mutating command: {executable}")
    if executable == "find":
        if any(value in {"-delete", "-exec", "-execdir", "-ok", "-okdir"} for value in lowered_args):
            classification = BashMutationClass.MUTATING if "-delete" in lowered_args else BashMutationClass.UNKNOWN
            return BashMutationHint(classification, "find action can execute or mutate state")
        return BashMutationHint(BashMutationClass.READ_ONLY, "read-only find inventory")
    if executable == "sed":
        return _classify_sed(args)
    if executable == "perl":
        if any(_perl_option_enables_in_place(value) for value in args):
            return BashMutationHint(BashMutationClass.MUTATING, "in-place perl edit")
        return BashMutationHint(BashMutationClass.UNKNOWN, "arbitrary Perl execution")
    if executable == "sort" and any(
        value in {"-o", "--output"} or value.startswith("--output=") for value in lowered_args
    ):
        return BashMutationHint(BashMutationClass.MUTATING, "sort output file")
    if executable == "awk":
        if any(re.search(r"\b(?:print|printf)\b[^;{}]*>{1,2}", value) for value in args):
            return BashMutationHint(BashMutationClass.MUTATING, "awk output redirection")
        if any("system(" in value.replace(" ", "").lower() for value in args):
            return BashMutationHint(BashMutationClass.UNKNOWN, "awk executes a dynamic command")
        return BashMutationHint(BashMutationClass.READ_ONLY, "read-only awk program")
    if executable == "date" and any(
        value in {"-s", "--set"} or value.startswith("--set=") for value in lowered_args
    ):
        return BashMutationHint(BashMutationClass.MUTATING, "system clock update")
    if executable == "git":
        return _classify_git(lowered_args)
    if executable in {"python", "python3", "pypy", "pypy3"}:
        return _classify_python(lowered_args, args)
    package_hint = _classify_package_manager(executable, lowered_args)
    if package_hint is not None:
        return package_hint
    if executable == "cd":
        return BashMutationHint(BashMutationClass.READ_ONLY, "shell working-directory change")
    if executable in _READ_ONLY_EXECUTABLES:
        return BashMutationHint(BashMutationClass.READ_ONLY, f"known read-only command: {executable}")
    return BashMutationHint(BashMutationClass.UNKNOWN, f"unclassified executable: {executable}")


def _executable_index(tokens: list[str]) -> int | None:
    index = 0
    while index < len(tokens) and _ASSIGNMENT.match(tokens[index]):
        index += 1
    while index < len(tokens):
        executable = PurePath(tokens[index]).name.lower()
        if executable == "env":
            index += 1
            while index < len(tokens) and (tokens[index].startswith("-") or _ASSIGNMENT.match(tokens[index])):
                index += 1
            continue
        if executable in {"command", "builtin", "exec", "nohup", "sudo", "time"}:
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                index += 1
            continue
        break
    return index if index < len(tokens) else None


def _without_input_redirections(tokens: list[str]) -> list[str]:
    result: list[str] = []
    index = 0
    while index < len(tokens):
        if tokens[index] in _INPUT_REDIRECTIONS:
            index += 2
            continue
        result.append(tokens[index])
        index += 1
    return result


def _without_descriptor_duplications(tokens: list[str]) -> list[str]:
    """Remove redirects such as ``2>&1`` that only route existing descriptors."""

    result: list[str] = []
    index = 0
    while index < len(tokens):
        has_source_fd = index > 0 and tokens[index].isdigit()
        operator_index = index + 1 if has_source_fd else index
        if operator_index < len(tokens) and tokens[operator_index] == ">&":
            target_index = operator_index + 1
            if target_index < len(tokens) and (tokens[target_index].isdigit() or tokens[target_index] == "-"):
                index = target_index + 1
                continue
        if operator_index + 2 < len(tokens) and tokens[operator_index : operator_index + 2] == [">", "&"]:
            target = tokens[operator_index + 2]
            if target.isdigit() or target == "-":
                index = operator_index + 3
                continue
        result.append(tokens[index])
        index += 1
    return result


def _classify_git(args: list[str]) -> BashMutationHint:
    action = next((arg for arg in args if not arg.startswith("-")), "")
    if action in _GIT_MUTATION_ACTIONS:
        return BashMutationHint(BashMutationClass.MUTATING, f"mutating git subcommand: {action}")
    if action in _GIT_READ_ONLY_ACTIONS:
        return BashMutationHint(BashMutationClass.READ_ONLY, f"read-only git subcommand: {action}")
    return BashMutationHint(BashMutationClass.UNKNOWN, "unclassified git invocation")


def _classify_sed(args: list[str]) -> BashMutationHint:
    if any(
        value == "--in-place" or value.startswith("--in-place=") or value == "-i" or value.startswith("-i")
        for value in args
    ):
        return BashMutationHint(BashMutationClass.MUTATING, "in-place sed edit")
    scripts = _sed_scripts(args)
    if any(re.search(r"(?:^|[;{}])\s*(?:\d+|\$)?\s*[wW]\s+\S", script) for script in scripts):
        return BashMutationHint(BashMutationClass.MUTATING, "sed write command")
    if any(re.search(r"(?:^|[;{}])\s*(?:\d+|\$)?\s*[eE](?:\s|$)", script) for script in scripts):
        return BashMutationHint(BashMutationClass.UNKNOWN, "sed executes a dynamic command")
    return BashMutationHint(BashMutationClass.READ_ONLY, "read-only sed program")


def _sed_scripts(args: list[str]) -> list[str]:
    scripts: list[str] = []
    index = 0
    while index < len(args):
        value = args[index]
        if value in {"-e", "--expression"} and index + 1 < len(args):
            scripts.append(args[index + 1])
            index += 2
            continue
        if value.startswith("--expression="):
            scripts.append(value.split("=", 1)[1])
        elif value.startswith("-e") and len(value) > 2:
            scripts.append(value[2:])
        elif not value.startswith("-") and not scripts:
            scripts.append(value)
        index += 1
    return scripts


def _perl_option_enables_in_place(value: str) -> bool:
    if value == "--in-place" or value.startswith("--in-place="):
        return True
    return value.startswith("-") and not value.startswith("--") and "i" in value[1:]


def _classify_python(lowered_args: list[str], original_args: list[str]) -> BashMutationHint:
    if len(lowered_args) >= 3 and lowered_args[0] == "-m" and lowered_args[1] in {"pip", "pip3"}:
        if lowered_args[2] in _PACKAGE_MUTATION_ACTIONS:
            return BashMutationHint(BashMutationClass.MUTATING, "Python package mutation")
    if len(original_args) >= 2 and original_args[0] == "-c":
        if _python_source_mutates(original_args[1]):
            return BashMutationHint(BashMutationClass.MUTATING, "inline Python file-system mutation")
        return BashMutationHint(BashMutationClass.UNKNOWN, "arbitrary inline Python")
    return BashMutationHint(BashMutationClass.UNKNOWN, "arbitrary Python execution")


def _python_source_mutates(source: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr.lower() in _PYTHON_MUTATING_METHODS:
            return True
        if isinstance(node.func, ast.Name) and node.func.id == "open" and _open_call_has_mutating_mode(node):
            return True
    return False


def _open_call_has_mutating_mode(call: ast.Call) -> bool:
    mode_node: ast.AST | None = call.args[1] if len(call.args) >= 2 else None
    for keyword in call.keywords:
        if keyword.arg == "mode":
            mode_node = keyword.value
    if not isinstance(mode_node, ast.Constant) or not isinstance(mode_node.value, str):
        return False
    return any(marker in mode_node.value for marker in ("w", "a", "x", "+"))


def _classify_package_manager(executable: str, args: list[str]) -> BashMutationHint | None:
    if executable in {"pip", "pip3", "npm", "pnpm", "yarn", "bun", "poetry"}:
        action = args[0] if args else ""
        if action in _PACKAGE_MUTATION_ACTIONS:
            return BashMutationHint(BashMutationClass.MUTATING, f"package mutation: {executable} {action}")
        return BashMutationHint(BashMutationClass.UNKNOWN, f"unclassified package-manager action: {executable}")
    if executable == "uv":
        action = args[0] if args else ""
        mutates = action in {"add", "remove", "sync"} or (
            action == "pip" and len(args) >= 2 and args[1] in _PACKAGE_MUTATION_ACTIONS
        )
        if mutates:
            return BashMutationHint(BashMutationClass.MUTATING, "uv package mutation")
        return BashMutationHint(BashMutationClass.UNKNOWN, "unclassified uv action")
    return None


def _is_shell_separator(token: str) -> bool:
    return bool(token) and all(character in ";&|" for character in token)


def _is_shell_punctuation(token: str) -> bool:
    return bool(token) and all(character in ";&|<>" for character in token)


__all__ = ["BashMutationClass", "BashMutationHint", "classify_bash_mutation"]
