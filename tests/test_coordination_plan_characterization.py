"""Direct characterization of coordination-plan semantic validation."""

from __future__ import annotations

from copy import deepcopy

import pytest

from travis.coding_agent.coordination import validate_coordination_plan


def _plan(
    *,
    route: object = "subagents",
    tasks: list[object] | None = None,
    dependencies: list[object] | None = None,
    ownership: list[object] | None = None,
    verification: list[object] | None = None,
) -> dict[str, object]:
    return {
        "route": route,
        "tasks": (
            tasks
            if tasks is not None
            else [
                {"id": "task-a", "owner": "subagent"},
                {"id": "task-b", "owner": "subagent"},
            ]
        ),
        "dependencies": (
            dependencies
            if dependencies is not None
            else [{"before": "task-a", "after": "task-b"}]
        ),
        "ownership": (
            ownership
            if ownership is not None
            else [
                {
                    "taskId": "task-a",
                    "access": "read",
                    "scopes": ["travis/parser.py"],
                },
                {
                    "taskId": "task-b",
                    "access": "write",
                    "scopes": ["tests/parser"],
                },
            ]
        ),
        "verification": (
            verification
            if verification is not None
            else [
                {"taskId": "task-a", "checks": ["read evidence"]},
                {"taskId": "task-b", "checks": ["focused tests"]},
            ]
        ),
    }


def _route_plan(route: str, owners: list[object]) -> dict[str, object]:
    tasks: list[object] = [
        {"id": f"task-{index}", "owner": owner}
        for index, owner in enumerate(owners)
    ]
    task_ids = [f"task-{index}" for index in range(len(owners))]
    return _plan(
        route=route,
        tasks=tasks,
        dependencies=[],
        ownership=[{"taskId": task_id} for task_id in task_ids],
        verification=[{"taskId": task_id} for task_id in task_ids],
    )


@pytest.mark.parametrize("value", (None, [], (), "plan", 7, object()))
def test_non_object_plan_has_one_exact_error(value: object) -> None:
    assert validate_coordination_plan(value) == ("plan must be an object",)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("tasks", ()),
        ("dependencies", {}),
        ("ownership", None),
        ("verification", "checks"),
        ("route", ["subagents"]),
    ),
)
def test_invalid_plan_collections_have_one_exact_schema_error(
    field: str,
    replacement: object,
) -> None:
    plan = _plan()
    plan[field] = replacement

    assert validate_coordination_plan(plan) == (
        "plan collections and route must satisfy the typed schema",
    )


@pytest.mark.parametrize(
    "missing_field",
    ("tasks", "dependencies", "ownership", "verification", "route"),
)
def test_missing_plan_collections_have_one_exact_schema_error(
    missing_field: str,
) -> None:
    plan = _plan()
    del plan[missing_field]

    assert validate_coordination_plan(plan) == (
        "plan collections and route must satisfy the typed schema",
    )


@pytest.mark.parametrize(
    "malformed_task",
    (
        {},
        {"owner": "subagent"},
        {"id": 7, "owner": "subagent"},
        "task-b",
    ),
)
def test_missing_nonstring_and_malformed_task_ids_keep_exact_result(
    malformed_task: object,
) -> None:
    plan = _plan(
        tasks=[
            {"id": "task-a", "owner": "subagent"},
            malformed_task,
        ],
        dependencies=[],
        ownership=[{"taskId": "task-a"}],
        verification=[{"taskId": "task-a"}],
    )

    assert validate_coordination_plan(plan) == (
        "every task must have a string id",
    )


def test_duplicate_task_ids_are_compared_as_the_validator_graph_keys() -> None:
    plan = _plan(
        tasks=[
            {"id": "task-a", "owner": "subagent"},
            {"id": "task-a", "owner": "subagent"},
        ],
        dependencies=[],
        ownership=[{"taskId": "task-a"}],
        verification=[{"taskId": "task-a"}],
    )

    assert validate_coordination_plan(plan) == ("task ids must be unique",)


@pytest.mark.parametrize(
    ("dependencies", "expected"),
    (
        ([42, "edge", None], ("dependency entries must be objects",)),
        ([{}], ("dependency references an unknown task",)),
        (
            [{"before": "task-a", "after": "task-missing"}],
            ("dependency references an unknown task",),
        ),
        (
            [{"before": "task-a", "after": "task-a"}],
            ("dependency cannot reference the same task",),
        ),
        (
            [
                {"before": "task-a", "after": "task-b"},
                {"before": "task-b", "after": "task-a"},
            ],
            ("dependencies must be acyclic",),
        ),
        (
            [
                {"before": "task-a", "after": "task-b"},
                {"before": "task-a", "after": "task-b"},
            ],
            (),
        ),
    ),
)
def test_dependency_branch_groups_keep_exact_results(
    dependencies: list[object],
    expected: tuple[str, ...],
) -> None:
    assert validate_coordination_plan(_plan(dependencies=dependencies)) == expected


@pytest.mark.parametrize("collection", ("dependencies", "ownership", "verification"))
def test_unhashable_task_references_keep_their_compatibility_exception(
    collection: str,
) -> None:
    plan = _plan()
    if collection == "dependencies":
        plan[collection] = [{"before": [], "after": "task-a"}]
    else:
        plan[collection] = [{"taskId": []}]

    with pytest.raises(TypeError, match="unhashable type: 'list'"):
        validate_coordination_plan(plan)


@pytest.mark.parametrize(
    ("ownership", "expected"),
    (
        (
            [
                42,
                {"taskId": "task-b", "access": "read", "scopes": ["tests"]},
            ],
            (
                "ownership entries must be objects",
                "every task must have exactly one ownership entry",
            ),
        ),
        (
            [
                {"taskId": "task-missing"},
                {"taskId": "task-b"},
            ],
            (
                "ownership references an unknown task",
                "every task must have exactly one ownership entry",
            ),
        ),
        (
            [
                {"taskId": "task-a"},
                {"taskId": "task-a"},
                {"taskId": "task-b"},
            ],
            ("every task must have exactly one ownership entry",),
        ),
        (
            [{"taskId": "task-a"}],
            ("every task must have exactly one ownership entry",),
        ),
    ),
)
def test_ownership_shape_reference_duplicate_and_missing_results_are_exact(
    ownership: list[object],
    expected: tuple[str, ...],
) -> None:
    assert validate_coordination_plan(_plan(ownership=ownership)) == expected


@pytest.mark.parametrize(
    "scope",
    ("", "   ", "/absolute", "C:/windows", "../parent", "path/../sibling"),
)
def test_invalid_ownership_scopes_have_one_exact_error(scope: str) -> None:
    ownership: list[object] = [
        {"taskId": "task-a", "access": "read", "scopes": [scope]},
        {"taskId": "task-b", "access": "read", "scopes": ["tests"]},
    ]

    assert validate_coordination_plan(_plan(ownership=ownership)) == (
        "ownership scopes must be plain relative paths",
    )


@pytest.mark.parametrize(
    "scope",
    (
        "./travis//coding_agent/",
        ".\\travis\\\\coding_agent\\",
        ".",
        "travis/./coding_agent",
    ),
)
def test_scope_normalization_preserves_accepted_plain_relative_paths(
    scope: str,
) -> None:
    ownership: list[object] = [
        {"taskId": "task-a", "access": "read", "scopes": [scope]},
        {"taskId": "task-b", "access": "read", "scopes": ["tests"]},
    ]

    assert validate_coordination_plan(_plan(ownership=ownership)) == ()


@pytest.mark.parametrize("scopes", (None, "travis", {"path": "travis"}, [7, None]))
def test_non_list_or_nonstring_scope_values_remain_ignored(scopes: object) -> None:
    ownership: list[object] = [
        {"taskId": "task-a", "access": "write", "scopes": scopes},
        {"taskId": "task-b", "access": "read", "scopes": ["tests"]},
    ]

    assert validate_coordination_plan(_plan(ownership=ownership)) == ()


@pytest.mark.parametrize(
    ("left", "right"),
    (
        ("travis/coding_agent", "travis/coding_agent/session.py"),
        ("travis\\coding_agent", "./travis//coding_agent/session.py/"),
        ("./tests//coordination/", "tests/coordination"),
    ),
)
def test_normalized_cross_task_write_scope_overlap_is_rejected_once(
    left: str,
    right: str,
) -> None:
    ownership: list[object] = [
        {"taskId": "task-a", "access": "write", "scopes": [left, left]},
        {"taskId": "task-b", "access": "write", "scopes": [right, right]},
    ]

    assert validate_coordination_plan(_plan(ownership=ownership)) == (
        "write ownership scopes must be disjoint",
    )


def test_same_task_nested_write_scopes_are_safe() -> None:
    ownership: list[object] = [
        {
            "taskId": "task-a",
            "access": "write",
            "scopes": ["travis", "travis/coding_agent"],
        },
        {"taskId": "task-b", "access": "write", "scopes": ["tests"]},
    ]

    assert validate_coordination_plan(_plan(ownership=ownership)) == ()


def test_prefix_like_cross_task_write_scopes_are_safe() -> None:
    ownership: list[object] = [
        {
            "taskId": "task-a",
            "access": "write",
            "scopes": ["travis/coding_agent"],
        },
        {
            "taskId": "task-b",
            "access": "write",
            "scopes": ["travis/coding_agent_tools"],
        },
    ]

    assert validate_coordination_plan(_plan(ownership=ownership)) == ()


def test_overlapping_read_and_write_scopes_are_safe() -> None:
    ownership: list[object] = [
        {"taskId": "task-a", "access": "read", "scopes": ["travis"]},
        {
            "taskId": "task-b",
            "access": "write",
            "scopes": ["travis/coding_agent"],
        },
    ]

    assert validate_coordination_plan(_plan(ownership=ownership)) == ()


@pytest.mark.parametrize(
    ("verification", "expected"),
    (
        (
            [42, {"taskId": "task-b"}],
            (
                "verification entries must be objects",
                "every task must have exactly one verification entry",
            ),
        ),
        (
            [{"taskId": "task-missing"}, {"taskId": "task-b"}],
            (
                "verification references an unknown task",
                "every task must have exactly one verification entry",
            ),
        ),
        (
            [
                {"taskId": "task-a"},
                {"taskId": "task-a"},
                {"taskId": "task-b"},
            ],
            ("every task must have exactly one verification entry",),
        ),
        (
            [{"taskId": "task-a"}],
            ("every task must have exactly one verification entry",),
        ),
    ),
)
def test_verification_shape_reference_duplicate_and_missing_results_are_exact(
    verification: list[object],
    expected: tuple[str, ...],
) -> None:
    assert validate_coordination_plan(_plan(verification=verification)) == expected


@pytest.mark.parametrize(
    ("route", "owners"),
    (
        ("direct", ["parent"]),
        ("direct", ["parent", "parent"]),
        ("subagents", ["subagent"]),
        ("subagents", ["subagent", "subagent"]),
        ("travis-b", ["travis-b"]),
        ("travis-b", ["travis-b", "travis-b"]),
        ("mixed", ["parent", "subagent"]),
        ("mixed", ["subagent", "parent", "subagent"]),
        ("mixed", ["parent", "travis-b"]),
        ("mixed", ["travis-b", "parent", "travis-b"]),
    ),
)
def test_every_supported_exact_route_accepts_its_owner_set(
    route: str,
    owners: list[object],
) -> None:
    assert validate_coordination_plan(_route_plan(route, owners)) == ()


@pytest.mark.parametrize(
    ("route", "owners", "expected"),
    (
        ("direct", ["subagent"], "route does not match task owners"),
        ("subagents", ["parent"], "route does not match task owners"),
        ("travis-b", ["parent", "travis-b"], "route does not match task owners"),
        ("mixed", ["parent"], "mixed route must use parent plus one worker class"),
        ("mixed", ["subagent"], "mixed route must use parent plus one worker class"),
        ("mixed", ["travis-b"], "mixed route must use parent plus one worker class"),
        (
            "mixed",
            ["subagent", "travis-b"],
            "mixed route must use parent plus one worker class",
        ),
        (
            "mixed",
            ["parent", "subagent", "travis-b"],
            "mixed route must use parent plus one worker class",
        ),
        ("mixed", ["parent", 7], "mixed route must use parent plus one worker class"),
    ),
)
def test_exact_and_mixed_route_owner_mismatches_keep_exact_errors(
    route: str,
    owners: list[object],
    expected: str,
) -> None:
    assert validate_coordination_plan(_route_plan(route, owners)) == (expected,)


@pytest.mark.parametrize("route", ("", "auto", "DIRECT", "parent", "durable"))
def test_unsupported_routes_keep_one_exact_error(route: str) -> None:
    assert validate_coordination_plan(_route_plan(route, ["parent"])) == (
        "route is unsupported",
    )


def test_duplicate_errors_are_removed_at_their_first_position() -> None:
    plan = _plan(dependencies=[None, 7, "edge", None])

    assert validate_coordination_plan(plan) == (
        "dependency entries must be objects",
    )


def test_error_order_is_stable_and_the_unique_result_is_capped_at_eight() -> None:
    plan = _plan(
        route="unsupported",
        tasks=[
            {"id": "task-a", "owner": "subagent"},
            {"id": "task-a", "owner": "subagent"},
            {"id": "task-b", "owner": "subagent"},
            {"owner": "subagent"},
        ],
        dependencies=[
            None,
            7,
            {"before": "task-a", "after": "task-missing"},
            {"before": "task-a", "after": "task-a"},
            {"before": "task-a", "after": "task-b"},
            {"before": "task-b", "after": "task-a"},
        ],
        ownership=[
            None,
            {"taskId": "task-missing"},
            {
                "taskId": "task-a",
                "access": "write",
                "scopes": ["/absolute"],
            },
        ],
        verification=[None, {"taskId": "task-missing"}],
    )

    assert validate_coordination_plan(plan) == (
        "every task must have a string id",
        "task ids must be unique",
        "dependency entries must be objects",
        "dependency references an unknown task",
        "dependency cannot reference the same task",
        "dependencies must be acyclic",
        "ownership entries must be objects",
        "ownership references an unknown task",
    )


def test_validation_does_not_mutate_nested_input_collections() -> None:
    plan = _plan(
        route="direct",
        dependencies=[
            {"before": "task-a", "after": "task-b"},
            {"before": "task-b", "after": "task-a"},
        ],
        ownership=[
            {
                "taskId": "task-a",
                "access": "write",
                "scopes": [".\\travis\\coding_agent\\"],
            },
            {
                "taskId": "task-b",
                "access": "write",
                "scopes": ["./travis//coding_agent/session.py/"],
            },
        ],
        verification=[
            {"taskId": "task-a"},
            {"taskId": "task-a"},
            {"taskId": "task-b"},
        ],
    )
    before = deepcopy(plan)

    assert validate_coordination_plan(plan) == (
        "dependencies must be acyclic",
        "write ownership scopes must be disjoint",
        "every task must have exactly one verification entry",
        "route does not match task owners",
    )
    assert plan == before
