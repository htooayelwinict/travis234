"""Structured consent policy for package-manager state mutation."""

from __future__ import annotations

import shlex
from pathlib import Path

from travis.coding_agent.policies.types import (
    Allow,
    CodingTurnContext,
    PolicyDecision,
    RequireConsent,
    ToolCallView,
)

PACKAGE_MUTATION_CAPABILITY = "package_mutation"
_MUTATING_ACTIONS = frozenset(
    {"ci", "i", "install", "uninstall", "add", "remove", "sync", "update", "upgrade"}
)
_SEPARATORS = frozenset({"&&", "||", ";", "|"})


class PackageMutationPolicy:
    def evaluate(self, call: ToolCallView, context: CodingTurnContext) -> PolicyDecision:
        if not _is_package_mutation(_package_mutation_payload(call)):
            return Allow()
        if context.capabilities.consume(PACKAGE_MUTATION_CAPABILITY):
            return Allow()
        return RequireConsent(
            PACKAGE_MUTATION_CAPABILITY,
            "Package installation or dependency mutation requires an explicit capability grant.",
        )


def _package_mutation_payload(call: ToolCallView) -> object:
    if call.name == "bash":
        return call.args.get("command")
    if call.name == "process" and call.args.get("action") == "write":
        return call.args.get("input")
    return None


def _is_package_mutation(command: object) -> bool:
    if not isinstance(command, str) or not command.strip():
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return False
    segment: list[str] = []
    for token in [*tokens, ";"]:
        if token in _SEPARATORS:
            if _segment_is_package_mutation(segment):
                return True
            segment = []
        else:
            segment.append(token)
    return False


def _segment_is_package_mutation(tokens: list[str]) -> bool:
    if not tokens:
        return False
    index = 0
    if Path(tokens[index]).name.lower() == "env":
        index += 1
        while index < len(tokens) and (tokens[index].startswith("-") or "=" in tokens[index]):
            index += 1
    if index >= len(tokens):
        return False
    executable = Path(tokens[index]).name.lower()
    args = [token.lower() for token in tokens[index + 1 :]]
    action = args[0] if args else ""
    if executable in {"pip", "pip3", "npm", "pnpm", "yarn", "bun", "poetry"}:
        return action in _MUTATING_ACTIONS
    if executable in {"python", "python3"} and len(args) >= 3:
        return args[0] == "-m" and Path(args[1]).name in {"pip", "pip3"} and args[2] in _MUTATING_ACTIONS
    if executable == "uv":
        return action in {"sync", "add", "remove"} or (
            len(args) >= 2 and action == "pip" and args[1] in _MUTATING_ACTIONS
        )
    return False


__all__ = [
    "PACKAGE_MUTATION_CAPABILITY",
    "PackageMutationPolicy",
]
