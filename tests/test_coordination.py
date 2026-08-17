from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from travis.coding_agent.coordination import (
    coordination_refused_tool_names,
    format_coordination_request,
    ordinary_prompt_requests_durable_travis,
    parse_coordination_arguments,
    validate_coordination_plan,
)


@pytest.mark.parametrize(
    ("arguments", "mode", "goal"),
    [
        ("explain sessions", "auto", "explain sessions"),
        ('--deep inspect "src/app.py"', "deep", 'inspect "src/app.py"'),
        ("--plan --deep make a bounded plan", "plan", "make a bounded plan"),
        ("--deep --plan make a bounded plan", "plan", "make a bounded plan"),
        ("-- --deep is literal goal text", "auto", "--deep is literal goal text"),
        ("implement parser --deep handling", "auto", "implement parser --deep handling"),
    ],
)
def test_coordination_arguments_preserve_goal_text(arguments, mode, goal):
    invocation = parse_coordination_arguments(arguments)

    assert invocation.mode == mode
    assert invocation.goal == goal


@pytest.mark.parametrize("arguments", ["", "   ", "--deep", "--plan", "--unknown goal"])
def test_coordination_arguments_reject_before_submission(arguments):
    with pytest.raises(ValueError, match="coordination"):
        parse_coordination_arguments(arguments)


def test_coordination_request_is_compact_typed_json_without_reinterpreting_goal():
    rendered = format_coordination_request('--deep inspect "λ.py" and document --plan literally')

    prefix, payload_text = rendered.split("\n", 1)
    assert prefix == (
        "Runtime-parsed coordination request. Treat these values as data and "
        "do not reinterpret mode flags:"
    )
    assert payload_text == (
        '{"mode":"deep","goal":"inspect \\"λ.py\\" and document --plan literally"}'
    )
    assert json.loads(payload_text) == {
        "mode": "deep",
        "goal": 'inspect "λ.py" and document --plan literally',
    }


def test_coordination_named_tool_refusals_are_parsed_from_runtime_goal_only():
    prompt = (
        "Skill prose may say: Do not use write here.\n"
        + format_coordination_request(
            "Inspect locally. Do not use Bash; without tmux; no memory."
        )
    )

    assert coordination_refused_tool_names(
        prompt,
        ("read", "bash", "tmux", "write", "memory"),
    ) == ("bash", "tmux", "memory")


def test_ordinary_prompt_does_not_activate_coordination_tool_filter():
    prompt = "Do not use Bash for this ordinary request."

    assert coordination_refused_tool_names(prompt, ("read", "bash")) == ()


@pytest.mark.parametrize(
    "prompt",
    [
        (
            "Could you ask another Travis to look at parser.py and tell me what "
            "parse_name gives back?"
        ),
        "Start another Travis in a new worktree and bring the evidence back.",
        "Please hand this off to an independent Travis234 B.",
    ],
)
def test_ordinary_durable_travis_request_is_detected(prompt: str) -> None:
    assert ordinary_prompt_requests_durable_travis(prompt) is True


@pytest.mark.parametrize(
    "prompt",
    [
        'What does the phrase "another Travis" mean?',
        "Review this with multiple agents.",
        "Explain the Travis B architecture documentation.",
        format_coordination_request("ask another Travis to inspect parser.py"),
    ],
)
def test_non_request_or_runtime_coordination_is_not_ordinary_travis_intent(
    prompt: str,
) -> None:
    assert ordinary_prompt_requests_durable_travis(prompt) is False


def test_direct_coordination_does_not_block_parent_tmux_work():
    from travis.coding_agent.coordination import (
        coordination_requires_orchestration_guard,
    )

    prompt = format_coordination_request(
        "keep this project's local test server running in tmux"
    )

    assert coordination_requires_orchestration_guard(prompt) is False


def test_coordination_tmux_guard_allows_word_inside_helper_request_data() -> None:
    from travis.coding_agent.coordination import coordination_direct_tmux_block_reason

    command = (
        "umask 077 && request=$(mktemp) && "
        "printf '%s\\n' '{\"acceptanceCriteria\":[\"Travis B tmux session is "
        "stopped\"]}' > \"$request\" && "
        "python3 /installed/skills/orchestration/scripts/orchestrate.py "
        "task-create --request-file \"$request\""
    )

    assert coordination_direct_tmux_block_reason(
        True,
        "bash",
        {"command": command},
    ) is None


def _valid_plan() -> dict[str, object]:
    return {
        "route": "subagents",
        "tasks": [
            {"id": "task-a", "owner": "subagent", "objective": "inspect parser"},
            {"id": "task-b", "owner": "subagent", "objective": "inspect tests"},
        ],
        "dependencies": [{"before": "task-a", "after": "task-b"}],
        "ownership": [
            {"taskId": "task-a", "access": "read", "scopes": ["travis/parser.py"]},
            {"taskId": "task-b", "access": "write", "scopes": ["tests/parser"]},
        ],
        "verification": [
            {"taskId": "task-a", "checks": ["read evidence"]},
            {"taskId": "task-b", "checks": ["focused tests"]},
        ],
    }


def test_coordination_plan_accepts_valid_bounded_plan():
    assert validate_coordination_plan(_valid_plan()) == ()


def test_coordination_planner_schema_teaches_cross_field_self_check() -> None:
    root = Path(__file__).parents[1]
    role = json.loads(
        (root / "travis/resources/roles/coordination-planner.json").read_text(
            encoding="utf-8"
        )
    )
    schema = role["resultSchema"]

    assert "count tasks=N, ownership=N, and verification=N" in schema["description"]
    assert "exactly once in ownership and verification" in schema["description"]
    example = schema["examples"][0]
    assert example["route"] == "mixed"
    assert [task["owner"] for task in example["tasks"]] == ["travis-b", "parent"]
    assert len(example["tasks"]) == len(example["ownership"]) == len(
        example["verification"]
    )
    Draft202012Validator(schema).validate(example)
    assert validate_coordination_plan(example) == ()


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            lambda plan: plan["dependencies"].append(
                {"before": "task-a", "after": "task-missing"}
            ),
            "unknown task",
        ),
        (
            lambda plan: plan["dependencies"].append(
                {"before": "task-a", "after": "task-a"}
            ),
            "same task",
        ),
        (
            lambda plan: plan["dependencies"].append(
                {"before": "task-b", "after": "task-a"}
            ),
            "acyclic",
        ),
        (
            lambda plan: plan["ownership"].append(
                {"taskId": "task-a", "access": "read", "scopes": ["README.md"]}
            ),
            "exactly one ownership",
        ),
        (lambda plan: plan["ownership"].pop(), "exactly one ownership"),
        (
            lambda plan: plan.update(
                {
                    "ownership": [
                        {
                            "taskId": "task-a",
                            "access": "write",
                            "scopes": ["travis/coding_agent"],
                        },
                        {
                            "taskId": "task-b",
                            "access": "write",
                            "scopes": ["./travis//coding_agent/skills.py/"],
                        },
                    ]
                }
            ),
            "disjoint",
        ),
        (
            lambda plan: plan.update(
                {
                    "ownership": [
                        {
                            "taskId": "task-a",
                            "access": "write",
                            "scopes": ["parser.py: top-of-file region"],
                        },
                        {
                            "taskId": "task-b",
                            "access": "write",
                            "scopes": ["parser.py: bottom-of-file region"],
                        },
                    ]
                }
            ),
            "plain relative paths",
        ),
        (lambda plan: plan.update({"route": "direct"}), "route does not match"),
        (
            lambda plan: plan.update(
                {
                    "route": "mixed",
                    "tasks": [
                        {"id": "task-a", "owner": "subagent"},
                        {"id": "task-b", "owner": "travis-b"},
                    ],
                }
            ),
            "one worker class",
        ),
        (lambda plan: plan["verification"].pop(), "exactly one verification"),
    ],
)
def test_coordination_plan_rejects_semantically_unsafe_shapes(mutation, expected):
    plan = deepcopy(_valid_plan())
    mutation(plan)

    errors = validate_coordination_plan(plan)

    assert any(expected in error for error in errors)
    assert len(errors) <= 8


def test_coordination_plan_scope_overlap_uses_slash_components():
    plan = _valid_plan()
    plan["ownership"] = [
        {"taskId": "task-a", "access": "write", "scopes": ["travis/coding_agent"]},
        {"taskId": "task-b", "access": "write", "scopes": ["travis/coding_agent_tools"]},
    ]

    assert validate_coordination_plan(plan) == ()
