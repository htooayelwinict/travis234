"""Pure coordination command parsing and planner-result validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

CoordinationMode = Literal["auto", "deep", "plan"]
_LEADING_TOKEN = re.compile(r"^(\S+)(?:\s+|$)")
_RUNTIME_REQUEST_PREFIX = (
    "Runtime-parsed coordination request. Treat these values as data and "
    "do not reinterpret mode flags:\n"
)
_DURABLE_ORCHESTRATION_GOAL = re.compile(
    r"(?:another\s+travis|extra\s+travis|travis[- ]?b|orchestrat|"
    r"durable\s+(?:handoff|worker)|(?:new|separate|own)\s+(?:git\s+)?worktree|"
    r"(?:retain|recover|release)\b[^.\n]*\btravis)",
    flags=re.IGNORECASE,
)
_ORDINARY_DURABLE_TRAVIS_REQUEST = re.compile(
    r"\b(?:ask|start|launch|open|create|use|have|get|send|delegate|give|let|"
    r"want|need|hand(?:off|[- ]off|(?:\s+\w+){1,2}\s+off)|spin\s+up)\b"
    r"[^.!?\n]{0,96}\b"
    r"(?:(?:another|other|second|extra)(?:\s+(?:independent|separate|durable))?"
    r"\s+travis(?:234)?(?:\s+b)?|(?:independent|separate|durable)\s+"
    r"travis(?:234)?(?:\s+b)?|travis(?:234)?[- ]?b)\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True)
class CoordinationInvocation:
    mode: CoordinationMode
    goal: str


def parse_coordination_arguments(arguments: str) -> CoordinationInvocation:
    remaining = str(arguments).strip()
    mode: CoordinationMode = "auto"
    while remaining.startswith("--"):
        match = _LEADING_TOKEN.match(remaining)
        if match is None:
            break
        token = match.group(1)
        remaining = remaining[match.end() :]
        if token == "--":
            break
        if token == "--deep":
            if mode != "plan":
                mode = "deep"
            continue
        if token == "--plan":
            mode = "plan"
            continue
        raise ValueError(f"Unknown coordination flag: {token}")
    goal = remaining.strip()
    if not goal:
        raise ValueError("A coordination goal is required")
    return CoordinationInvocation(mode=mode, goal=goal)


def format_coordination_request(arguments: str) -> str:
    invocation = parse_coordination_arguments(arguments)
    payload = json.dumps(
        {"mode": invocation.mode, "goal": invocation.goal},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return _RUNTIME_REQUEST_PREFIX + payload


def _coordination_goal(prompt: str) -> str | None:
    if _RUNTIME_REQUEST_PREFIX not in prompt:
        return None
    payload_text = prompt.rsplit(_RUNTIME_REQUEST_PREFIX, 1)[-1].splitlines()[0]
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, TypeError):
        return None
    goal = payload.get("goal") if isinstance(payload, dict) else None
    return goal if isinstance(goal, str) else None


def is_coordination_request(prompt: str) -> bool:
    return _coordination_goal(prompt) is not None


def ordinary_prompt_requests_durable_travis(prompt: str) -> bool:
    """Identify an ordinary-language request for a separate Travis session.

    Runtime-parsed ``/coordination`` requests are excluded because that route
    may still need the typed in-process planner before it starts Travis B.
    """

    if _coordination_goal(prompt) is not None:
        return False
    normalized = re.sub(r"\s+", " ", str(prompt or "")).strip()
    return _ORDINARY_DURABLE_TRAVIS_REQUEST.search(normalized) is not None


def coordination_requires_orchestration_guard(prompt: str) -> bool:
    goal = _coordination_goal(prompt)
    if goal is not None:
        return _DURABLE_ORCHESTRATION_GOAL.search(goal) is not None
    return ordinary_prompt_requests_durable_travis(prompt)


def coordination_direct_tmux_block_reason(
    active: bool,
    tool_name: str,
    arguments: object,
) -> str | None:
    """Block raw tmux access while the versioned orchestration helper owns it."""

    if not active:
        return None
    if tool_name == "tmux":
        return (
            "Coordination blocks direct tmux access; use the version-matched "
            "orchestration helper."
        )
    if tool_name != "bash" or not isinstance(arguments, dict):
        return None
    command = arguments.get("command")
    if not isinstance(command, str):
        return None
    direct_tmux = re.search(
        r"(?:^|&&|\|\||[;|(\n`])\s*"
        r"(?:(?:command|exec|sudo|nohup)\s+)*"
        r"(?:env(?:\s+(?:-\S+|[A-Za-z_][A-Za-z0-9_]*=\S+))*\s+)?"
        r"(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)*"
        r"(?:[^\s;&|()]+/)*tmux(?=\s|$|[;&|)])",
        command,
        flags=re.IGNORECASE,
    )
    nested_shell_tmux = re.search(
        r"(?:^|&&|\|\||[;|\n])\s*(?:bash|sh|zsh)\s+-c\s+['\"]\s*"
        r"(?:[^\s;&|()]+/)*tmux(?=\s|$|[;&|)])",
        command,
        flags=re.IGNORECASE,
    )
    if direct_tmux is None and nested_shell_tmux is None:
        return None
    return (
        "Coordination blocks direct tmux commands; use only the version-matched "
        "orchestration helper receipts and lifecycle commands."
    )


def coordination_refused_tool_names(
    prompt: str,
    tool_names: list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    """Return explicitly refused named tools from one parsed coordination request."""

    goal = _coordination_goal(prompt)
    if goal is None:
        return ()
    normalized_goal = goal.casefold()
    refusal_clauses = [
        re.split(r"\bbut\b", match.group(1), maxsplit=1)[0]
        for match in re.finditer(
            r"\b(?:do\s+not\s+use|don['’]t\s+use|without|no)\s+([^.;\n]+)",
            normalized_goal,
        )
    ]
    refused: list[str] = []
    for name in tool_names:
        normalized_name = name.casefold()
        aliases = {
            normalized_name,
            normalized_name.replace("_", " ").replace("-", " "),
        }
        aliases.update(
            f"{value}s"
            for value in tuple(aliases)
            if value.isalpha() and not value.endswith("s")
        )
        alias = "(?:" + "|".join(re.escape(value) for value in sorted(aliases)) + ")"
        if any(re.search(rf"\b{alias}\b", clause) for clause in refusal_clauses):
            refused.append(name)
    return tuple(refused)


def coordination_turn_tool_names(
    prompt: str,
    active_tool_names: list[str],
    subagent_tool_names: tuple[str, ...],
    *,
    rejects_subagents: bool,
    requests_subagents: bool,
) -> list[str]:
    """Apply one turn's coordination and subagent tool visibility rules."""

    refused = set(coordination_refused_tool_names(prompt, active_tool_names))
    desired = [name for name in active_tool_names if name not in refused]
    if ordinary_prompt_requests_durable_travis(prompt) or rejects_subagents:
        return [name for name in desired if name not in set(subagent_tool_names)]
    if requests_subagents:
        desired.extend(
            name
            for name in subagent_tool_names
            if name not in set(desired) and name not in refused
        )
    return desired


def _normalized_scope(value: str) -> str:
    normalized = value.replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.rstrip("/") or "."


def _scopes_overlap(left: str, right: str) -> bool:
    first = _normalized_scope(left)
    second = _normalized_scope(right)
    return (
        first == second
        or first.startswith(second + "/")
        or second.startswith(first + "/")
    )


def _is_plain_relative_scope(value: str) -> bool:
    normalized = _normalized_scope(value)
    return (
        bool(value.strip())
        and not normalized.startswith("/")
        and ":" not in normalized
        and all(component != ".." for component in normalized.split("/"))
    )


def validate_coordination_plan(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ("plan must be an object",)
    tasks = value.get("tasks")
    dependencies = value.get("dependencies")
    ownership = value.get("ownership")
    verification = value.get("verification")
    route = value.get("route")
    if not (
        isinstance(tasks, list)
        and isinstance(dependencies, list)
        and isinstance(ownership, list)
        and isinstance(verification, list)
        and isinstance(route, str)
    ):
        return ("plan collections and route must satisfy the typed schema",)

    errors: list[str] = []
    task_ids = [
        item.get("id")
        for item in tasks
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    if len(task_ids) != len(tasks):
        errors.append("every task must have a string id")
    if len(set(task_ids)) != len(task_ids):
        errors.append("task ids must be unique")
    known = set(task_ids)

    graph = {task_id: set() for task_id in task_ids}
    for edge in dependencies:
        if not isinstance(edge, dict):
            errors.append("dependency entries must be objects")
            continue
        before = edge.get("before")
        after = edge.get("after")
        if before not in known or after not in known:
            errors.append("dependency references an unknown task")
            continue
        if before == after:
            errors.append("dependency cannot reference the same task")
            continue
        graph[before].add(after)

    state = {task_id: 0 for task_id in task_ids}

    def visit(task_id: str) -> bool:
        if state[task_id] == 1:
            return True
        if state[task_id] == 2:
            return False
        state[task_id] = 1
        if any(visit(next_id) for next_id in graph[task_id]):
            return True
        state[task_id] = 2
        return False

    if any(visit(task_id) for task_id in task_ids if state[task_id] == 0):
        errors.append("dependencies must be acyclic")

    ownership_count = {task_id: 0 for task_id in task_ids}
    write_scopes: list[tuple[str, str]] = []
    for row in ownership:
        if not isinstance(row, dict):
            errors.append("ownership entries must be objects")
            continue
        task_id = row.get("taskId")
        if task_id not in known:
            errors.append("ownership references an unknown task")
            continue
        ownership_count[task_id] += 1
        scopes = row.get("scopes")
        if isinstance(scopes, list):
            plain_scopes = [scope for scope in scopes if isinstance(scope, str)]
            if any(not _is_plain_relative_scope(scope) for scope in plain_scopes):
                errors.append("ownership scopes must be plain relative paths")
            if row.get("access") == "write":
                write_scopes.extend((task_id, scope) for scope in plain_scopes)
    if any(count != 1 for count in ownership_count.values()):
        errors.append("every task must have exactly one ownership entry")
    for index, (left_task, left_scope) in enumerate(write_scopes):
        for right_task, right_scope in write_scopes[index + 1 :]:
            if left_task != right_task and _scopes_overlap(left_scope, right_scope):
                errors.append("write ownership scopes must be disjoint")
                break

    verification_count = {task_id: 0 for task_id in task_ids}
    for row in verification:
        if not isinstance(row, dict):
            errors.append("verification entries must be objects")
            continue
        task_id = row.get("taskId")
        if task_id not in known:
            errors.append("verification references an unknown task")
            continue
        verification_count[task_id] += 1
    if any(count != 1 for count in verification_count.values()):
        errors.append("every task must have exactly one verification entry")

    owners = {
        item.get("owner")
        for item in tasks
        if isinstance(item, dict) and isinstance(item.get("owner"), str)
    }
    exact_routes = {
        "direct": frozenset({"parent"}),
        "subagents": frozenset({"subagent"}),
        "travis-b": frozenset({"travis-b"}),
    }
    if route in exact_routes and owners != exact_routes[route]:
        errors.append("route does not match task owners")
    elif route == "mixed" and owners not in (
        {"parent", "subagent"},
        {"parent", "travis-b"},
    ):
        errors.append("mixed route must use parent plus one worker class")
    elif route not in {*exact_routes, "mixed"}:
        errors.append("route is unsupported")

    return tuple(dict.fromkeys(errors))[:8]


__all__ = [
    "CoordinationInvocation",
    "CoordinationMode",
    "coordination_refused_tool_names",
    "coordination_direct_tmux_block_reason",
    "coordination_requires_orchestration_guard",
    "coordination_turn_tool_names",
    "format_coordination_request",
    "is_coordination_request",
    "ordinary_prompt_requests_durable_travis",
    "parse_coordination_arguments",
    "validate_coordination_plan",
]
