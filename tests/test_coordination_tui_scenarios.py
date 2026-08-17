from __future__ import annotations

import json
from pathlib import Path
import shlex

import pytest

from travis.ai.providers.faux import (
    create_faux_provider,
    faux_model,
    text_response_events,
    tool_call_response_events,
)
from travis.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage
from travis.app import CodingApp
from travis.coding_agent.subagents import CallableSubagentBackend, SubagentResult
from travis.tui.terminal import FakeTerminal
from tests._provider_runtime import register_api_provider, reset_api_providers


def setup_function() -> None:
    reset_api_providers()


def _plan(
    *,
    route: str = "subagents",
    owners: tuple[str, ...] = ("subagent", "subagent"),
    access: tuple[str, ...] | None = None,
    scopes: tuple[str, ...] | None = None,
) -> dict[str, object]:
    accesses = access or tuple("read" for _ in owners)
    owned_scopes = scopes or tuple(f"scope-{index}" for index in range(len(owners)))
    tasks = [
        {
            "id": f"task-{index}",
            "objective": f"bounded objective {index}",
            "owner": owner,
        }
        for index, owner in enumerate(owners)
    ]
    return {
        "route": route,
        "rationale": "The scopes are independent and have separate evidence.",
        "tasks": tasks,
        "dependencies": [],
        "ownership": [
            {
                "taskId": task["id"],
                "access": accesses[index],
                "scopes": [owned_scopes[index]],
            }
            for index, task in enumerate(tasks)
        ],
        "risks": ["Worker reports require parent verification."],
        "approvalGates": [
            {"kind": "commit", "condition": "Only with explicit user approval."},
            {"kind": "replay", "condition": "Never replay uncertain effects."},
        ],
        "verification": [
            {"taskId": task["id"], "evidence": [f"observe {owned_scopes[index]}"]}
            for index, task in enumerate(tasks)
        ],
        "stopConditions": {
            "success": "All evidence is independently observed.",
            "failure": "A required check fails.",
            "cancellation": "Settle exact worker identifiers.",
            "blocker": "Report the boundary without substitution.",
        },
    }


def _typed_result(task, output: object) -> SubagentResult:
    if isinstance(output, str):
        final_response = output
    else:
        final_response = json.dumps(
            {"summary": "bounded coordination plan", "output": output, "artifacts": []}
        )
    return SubagentResult(
        task_id=task.id,
        backend=task.backend,
        role=task.role,
        status="completed",
        summary="planner completed",
        final_response=final_response,
    )


def _tool_results(app: CodingApp, tool_name: str | None = None) -> list[ToolResultMessage]:
    return [
        message
        for message in app.messages
        if isinstance(message, ToolResultMessage)
        and (tool_name is None or message.tool_name == tool_name)
    ]


def _tool_calls(app: CodingApp) -> list[ToolCall]:
    return [
        block
        for message in app.messages
        if isinstance(message, AssistantMessage)
        for block in message.content
        if isinstance(block, ToolCall)
    ]


def _visible_text(app: CodingApp) -> str:
    return "\n".join(app.tui.render(180))


def _coordination_payload(text: str) -> dict[str, str]:
    return json.loads(text.rsplit("\n", 1)[-1])


def test_tui_coordination_auto_simple_stays_direct_without_planner(tmp_path: Path) -> None:
    prompts: list[str] = []

    def provider(model, context):
        prompts.append(context.messages[-1].content[0].text)
        assert "# Coordination" in prompts[-1]
        return text_response_events(
            model,
            "Preflight: mode=auto; route=direct. Outcome: explained locally; no workers.",
        )

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=180, rows=40),
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    try:
        app.run_turn("/coordination explain the purpose of this request in one sentence")

        assert len(prompts) == 1
        assert _coordination_payload(prompts[0]) == {
            "mode": "auto",
            "goal": "explain the purpose of this request in one sentence",
        }
        assert app.session.subagents.list_tasks() == []
        assert _tool_calls(app) == []
        assert not (tmp_path / ".worktrees").exists()
        assert "route=direct" in _visible_text(app)
    finally:
        app.close()


@pytest.mark.parametrize(
    ("command", "expected_mode", "final_text"),
    [
        (
            "/coordination compare two independent modules and verify each separately",
            "auto",
            "Route: subagents; validated plan ready.",
        ),
        (
            "/coordination --deep explain one small module",
            "deep",
            "Route: subagents; deep planning completed once.",
        ),
        (
            "/coordination --plan compare two independent modules",
            "plan",
            "Route: subagents; plan only; execution stopped.",
        ),
    ],
)
def test_tui_coordination_complex_and_forced_modes_use_one_typed_planner(
    tmp_path: Path,
    command: str,
    expected_mode: str,
    final_text: str,
) -> None:
    parent_calls = {"count": 0}
    backend_tasks = []

    def backend(task):
        backend_tasks.append(task)
        return _typed_result(task, _plan())

    def provider(model, context):
        parent_calls["count"] += 1
        if parent_calls["count"] == 1:
            payload = _coordination_payload(context.messages[-1].content[0].text)
            assert payload["mode"] == expected_mode
            return tool_call_response_events(
                model,
                "spawn_subagent",
                {
                    "role": "coordination-planner",
                    "goal": f"Mode={expected_mode}; preserve exact goal and produce one typed plan.",
                    "wait": True,
                },
                call_id=f"planner-{expected_mode}",
            )
        assert _tool_results_from_context(context)[-1].details["status"] == "completed"
        return text_response_events(model, final_text)

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=180, rows=40),
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    app.session.subagents.register_backend(CallableSubagentBackend("internal", backend))
    try:
        app.run_turn(command)

        assert len(backend_tasks) == 1
        planner = backend_tasks[0]
        assert planner.role == "coordination-planner"
        assert planner.role_definition_name == "coordination-planner"
        assert set(planner.allowed_tools) <= {"find", "ls"}
        assert planner.allowed_effects == ("read",)
        assert planner.result_schema is not None
        assert planner.artifact_policy == "none"
        assert len(app.session.subagents.list_tasks()) == 1
        assert [call.name for call in _tool_calls(app)] == ["spawn_subagent"]
        assert final_text in _visible_text(app)
        if expected_mode == "plan":
            assert not (tmp_path / ".worktrees").exists()
            assert not any(
                call.name in {"bash", "write", "edit", "tmux"}
                for call in _tool_calls(app)
            )
    finally:
        app.close()


@pytest.mark.parametrize(
    "planner_output",
    [
        "not-json",
        {"route": "subagents"},
        _plan(
            access=("write", "write"),
            scopes=("travis/coding_agent", "travis/coding_agent/session.py"),
        ),
        _plan(
            route="mixed",
            owners=("subagent", "travis-b"),
            access=("write", "write"),
            scopes=("travis", "tests"),
        ),
    ],
    ids=(
        "invalid-json",
        "invalid-schema",
        "overlapping-write-scopes",
        "mutating-subagent-plus-travis-b",
    ),
)
def test_tui_coordination_invalid_planner_result_is_not_retried_and_falls_back(
    tmp_path: Path,
    planner_output: object,
) -> None:
    calls = {"count": 0}
    backend_tasks = []

    def backend(task):
        backend_tasks.append(task)
        return _typed_result(task, planner_output)

    def provider(model, context):
        calls["count"] += 1
        if calls["count"] == 1:
            return tool_call_response_events(
                model,
                "spawn_subagent",
                {
                    "role": "coordination-planner",
                    "goal": "Produce one bounded plan; do not execute.",
                    "wait": True,
                },
                call_id="planner-invalid",
            )
        result = _tool_results_from_context(context)[-1]
        assert result.details["status"] == "failed"
        assert result.details["validationErrors"]
        return text_response_events(
            model,
            "Conservative fallback (planner failed): one parent task owns '.'; execution stopped.",
        )

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=180, rows=40),
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    app.session.subagents.register_backend(CallableSubagentBackend("internal", backend))
    try:
        app.run_turn("/coordination --plan plan two independent inspections")

        assert len(backend_tasks) == 1
        assert len(app.session.subagents.list_tasks()) == 1
        assert app.session.subagents.list_results()[0].status == "failed"
        assert [call.name for call in _tool_calls(app)] == ["spawn_subagent"]
        assert "Conservative fallback (planner failed)" in _visible_text(app)
    finally:
        app.close()


def _tool_results_from_context(context) -> list[ToolResultMessage]:
    return [message for message in context.messages if isinstance(message, ToolResultMessage)]


def test_tui_coordination_planner_leaves_two_worker_slots_and_blocks_a_fourth_spawn(
    tmp_path: Path,
) -> None:
    provider_call = {"count": 0}
    backend_tasks = []

    def backend(task):
        backend_tasks.append(task)
        if task.role == "coordination-planner":
            return _typed_result(task, _plan())
        return f"Observed evidence for {task.role}."

    def provider(model, context):
        provider_call["count"] += 1
        call = provider_call["count"]
        if call == 1:
            arguments = {
                "role": "coordination-planner",
                "goal": "Plan two independent read-only inspections.",
                "wait": True,
            }
        elif call in (2, 3):
            arguments = {
                "role": f"reviewer-{call - 1}",
                "goal": f"Inspect scope-{call - 2}; return observed evidence only.",
                "wait": True,
            }
        elif call == 4:
            arguments = {
                "role": "reviewer-extra",
                "goal": "Inspect a fourth scope.",
                "wait": True,
            }
        else:
            blocked = _tool_results_from_context(context)[-1]
            assert blocked.details["status"] == "blocked"
            assert blocked.details["reason"] == "subagent_spawn_limit_per_turn"
            return text_response_events(
                model,
                "Blocker: planner plus two workers consumed all three model spawn slots.",
            )
        return tool_call_response_events(
            model,
            "spawn_subagent",
            arguments,
            call_id=f"planned-spawn-{call}",
        )

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=180, rows=40),
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    app.session.subagents.register_backend(CallableSubagentBackend("internal", backend))
    try:
        app.run_turn("/coordination --deep inspect three independent scopes")

        assert [task.role for task in backend_tasks] == [
            "coordination-planner",
            "reviewer-1",
            "reviewer-2",
        ]
        assert len(app.session.subagents.list_tasks()) == 3
        assert app.session._model_subagents_spawned_this_turn == 3
        blocked = _tool_results(app, "spawn_subagent")[-1]
        assert blocked.details["reason"] == "subagent_spawn_limit_per_turn"
        assert "consumed all three" in _visible_text(app)
    finally:
        app.close()


def test_tui_coordination_without_planner_retains_three_spawn_slots(tmp_path: Path) -> None:
    calls = {"count": 0}
    backend_tasks = []

    def backend(task):
        backend_tasks.append(task)
        return f"Evidence from {task.role}."

    def provider(model, context):
        calls["count"] += 1
        if calls["count"] <= 3:
            index = calls["count"]
            return tool_call_response_events(
                model,
                "spawn_subagent",
                {
                    "role": f"reviewer-{index}",
                    "goal": f"Inspect pre-classified disjoint scope-{index}.",
                    "wait": True,
                },
                call_id=f"direct-worker-{index}",
            )
        return text_response_events(model, "Outcome: three direct worker slots completed.")

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=180, rows=40),
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    app.session.subagents.register_backend(CallableSubagentBackend("internal", backend))
    try:
        app.run_turn(
            "/coordination use the three already-defined disjoint reviewers; planning is unnecessary"
        )

        assert len(backend_tasks) == 3
        assert all(task.role != "coordination-planner" for task in backend_tasks)
        assert app.session._model_subagents_spawned_this_turn == 3
        assert all(
            result.details["status"] == "completed"
            for result in _tool_results(app, "spawn_subagent")
        )
    finally:
        app.close()


def test_tui_coordination_no_subagents_refusal_prevents_even_planner_probe(
    tmp_path: Path,
) -> None:
    prompts: list[str] = []

    def provider(model, context):
        prompts.append(context.messages[-1].content[0].text)
        tool_names = {tool.name for tool in context.tools or []}
        assert "spawn_subagent" not in tool_names
        return text_response_events(
            model,
            "Preflight: no subagents; route=direct; no worker or planner was probed.",
        )

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=180, rows=40),
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    try:
        app.run_turn("/coordination no subagents; explain one local fact")

        assert len(prompts) == 1
        assert app.session.subagents.list_tasks() == []
        assert _tool_calls(app) == []
        assert "no worker or planner" in _visible_text(app)
    finally:
        app.close()


@pytest.mark.parametrize(
    ("refused_name", "tool_name"),
    [
        ("Bash", "bash"),
        ("spawn_subagent", "spawn_subagent"),
        ("Bash, network, MCP, Git, edits, or memory", "edit"),
    ],
)
def test_tui_coordination_named_tool_refusal_removes_tool_for_the_turn(
    tmp_path: Path,
    refused_name: str,
    tool_name: str,
) -> None:
    available_tools: list[set[str]] = []

    def provider(model, context):
        available_tools.append({tool.name for tool in context.tools or []})
        return text_response_events(model, "Outcome: direct read-only answer; Bash unavailable.")

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=180, rows=40),
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    try:
        app.run_turn(f"/coordination explain one fact. Do not use {refused_name}.")

        assert len(available_tools) == 1
        assert tool_name not in available_tools[0]
        assert "read" in available_tools[0]
        assert tool_name in app.session.get_active_tool_names()
    finally:
        app.close()


def test_tui_coordination_propagates_refusals_to_planner_and_every_worker(
    tmp_path: Path,
) -> None:
    constraints = "no network; no MCP; no commit; local only"
    provider_call = {"count": 0}
    backend_tasks = []

    def backend(task):
        backend_tasks.append(task)
        if task.role == "coordination-planner":
            return _typed_result(task, _plan())
        return f"Read-only local report from {task.role}."

    def provider(model, context):
        provider_call["count"] += 1
        call = provider_call["count"]
        if call == 1:
            arguments = {
                "role": "coordination-planner",
                "goal": f"Plan two disjoint inspections. Constraints: {constraints}.",
                "contextPack": f"Hard boundaries: {constraints}.",
                "wait": True,
            }
        elif call in (2, 3):
            arguments = {
                "role": f"reviewer-{call - 1}",
                "goal": f"Inspect scope-{call - 2}. Hard boundaries: {constraints}.",
                "contextPack": f"Authority ceiling: {constraints}.",
                "wait": True,
            }
        else:
            return text_response_events(
                model,
                "Outcome: local read-only evidence collected; refusals remained active.",
            )
        return tool_call_response_events(
            model,
            "spawn_subagent",
            arguments,
            call_id=f"refusal-spawn-{call}",
        )

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=180, rows=40),
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    app.session.subagents.register_backend(CallableSubagentBackend("internal", backend))
    try:
        app.run_turn(
            f"/coordination --deep inspect two independent local scopes; {constraints}"
        )

        assert len(backend_tasks) == 3
        for task in backend_tasks:
            combined = f"{task.goal}\n{task.context_pack}".lower()
            for boundary in ("no network", "no mcp", "no commit", "local only"):
                assert boundary in combined
        assert not any(call.name in {"bash", "tmux", "write", "edit"} for call in _tool_calls(app))
    finally:
        app.close()


def test_tui_coordination_parent_observation_catches_false_child_success(
    tmp_path: Path,
) -> None:
    claimed_file = tmp_path / "claimed.txt"
    calls = {"count": 0}
    backend_tasks = []

    def backend(task):
        backend_tasks.append(task)
        if task.role == "coordination-planner":
            return _typed_result(
                task,
                _plan(owners=("subagent",), scopes=("claimed.txt",)),
            )
        return "Success: wrote claimed.txt and verified its contents."

    def provider(model, context):
        calls["count"] += 1
        if calls["count"] == 1:
            return tool_call_response_events(
                model,
                "spawn_subagent",
                {
                    "role": "coordination-planner",
                    "goal": "Plan one delegated file outcome and require parent evidence.",
                    "wait": True,
                },
                call_id="false-plan",
            )
        if calls["count"] == 2:
            return tool_call_response_events(
                model,
                "spawn_subagent",
                {
                    "role": "writer",
                    "goal": "Write claimed.txt and verify it.",
                    "wait": True,
                },
                call_id="false-worker",
            )
        if calls["count"] == 3:
            assert "Success: wrote claimed.txt" in _tool_results_from_context(context)[-1].content[0].text
            return tool_call_response_events(
                model,
                "read",
                {"path": "claimed.txt"},
                call_id="parent-proof",
            )
        observed = _tool_results_from_context(context)[-1]
        assert observed.is_error is True
        return text_response_events(
            model,
            "Outcome: FAILED verification; the child claimed success but claimed.txt is absent.",
        )

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=180, rows=40),
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    app.session.subagents.register_backend(CallableSubagentBackend("internal", backend))
    try:
        app.run_turn("/coordination --deep delegate creation of claimed.txt and verify the result")

        assert not claimed_file.exists()
        assert [call.name for call in _tool_calls(app)] == [
            "spawn_subagent",
            "spawn_subagent",
            "read",
        ]
        assert "FAILED verification" in _visible_text(app)
    finally:
        app.close()


def test_tui_coordination_selects_versioned_orchestration_helper_without_raw_tmux(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orchestration_skill = (
        Path(__file__).parents[1]
        / "travis/resources/skills/orchestration/SKILL.md"
    )
    helper = orchestration_skill.parent / "scripts/orchestrate.py"
    monkeypatch.setenv("TRAVIS234_ORCHESTRATION_HELPER", str(helper))
    calls = {"count": 0}

    def provider(model, context):
        calls["count"] += 1
        if calls["count"] == 1:
            return tool_call_response_events(
                model,
                "read",
                {"path": str(orchestration_skill)},
                call_id="load-orchestration-route",
            )
        if calls["count"] == 2:
            skill_text = _tool_results_from_context(context)[-1].content[0].text
            assert "Never call the tmux tool or tmux executable directly" in skill_text
            assert "A timeout does not permit bypassing the helper" in skill_text
            assert '"$TRAVIS234_ORCHESTRATION_HELPER" guide' in skill_text
            return tool_call_response_events(
                model,
                "bash",
                {
                    "command": (
                        'python3 "$TRAVIS234_ORCHESTRATION_HELPER" guide'
                    )
                },
                call_id="versioned-orchestration-guide",
            )
        guide = _tool_results_from_context(context)[-1]
        assert guide.is_error is False
        assert "run-create" in guide.content[0].text
        return text_response_events(
            model,
            "Preflight: route=travis-b; version-matched helper confirmed; stopped before dispatch.",
        )

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=180, rows=40),
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    try:
        app.run_turn(
            "/coordination start durable work in a new worktree, but stop after route preflight"
        )

        actual_calls = _tool_calls(app)
        assert [call.name for call in actual_calls] == ["read", "bash"]
        assert "$TRAVIS234_ORCHESTRATION_HELPER" in actual_calls[1].arguments["command"]
        assert all(call.name != "tmux" for call in actual_calls)
        assert not (tmp_path / ".worktrees").exists()
        assert "route=travis-b" in _visible_text(app)
    finally:
        app.close()


def test_tui_coordination_runtime_blocks_direct_tmux_but_allows_helper(
    tmp_path: Path,
) -> None:
    helper = (
        Path(__file__).parents[1]
        / "travis/resources/skills/orchestration/scripts/orchestrate.py"
    )
    calls = {"count": 0}

    def provider(model, context):
        calls["count"] += 1
        if calls["count"] == 1:
            return tool_call_response_events(
                model,
                "tmux",
                {"action": "list"},
                call_id="raw-tmux-tool",
            )
        if calls["count"] == 2:
            result = _tool_results_from_context(context)[-1]
            assert result.is_error is True
            assert "coordination" in result.content[0].text.lower()
            return tool_call_response_events(
                model,
                "bash",
                {"command": "tmux list-sessions"},
                call_id="raw-tmux-bash",
            )
        if calls["count"] == 3:
            result = _tool_results_from_context(context)[-1]
            assert result.is_error is True
            assert "version-matched orchestration helper" in result.content[0].text
            return tool_call_response_events(
                model,
                "bash",
                {"command": f"python3 {shlex.quote(str(helper))} guide"},
                call_id="allowed-orchestration-helper",
            )
        result = _tool_results_from_context(context)[-1]
        assert result.is_error is False
        assert "run-create" in result.content[0].text
        return text_response_events(model, "Direct tmux blocked; helper allowed.")

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=180, rows=40),
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    try:
        app.run_turn(
            "/coordination inspect a durable worker with the supported orchestration route"
        )

        results = _tool_results(app)
        assert [result.is_error for result in results] == [True, True, False]
        assert "Direct tmux blocked; helper allowed." in _visible_text(app)
    finally:
        app.close()


def test_coordination_route_contract_keeps_coupled_lsp_and_authority_in_parent() -> None:
    root = Path(__file__).parents[1]
    skill = (root / "travis/resources/skills/coordination/SKILL.md").read_text(
        encoding="utf-8"
    )
    contract = (
        root
        / "travis/resources/skills/coordination/references/planning-contract.md"
    ).read_text(encoding="utf-8")
    role = json.loads(
        (root / "travis/resources/roles/coordination-planner.json").read_text(
            encoding="utf-8"
        )
    )

    assert "Keep simple, tightly coupled, integration, LSP apply" in skill
    assert "one durable, isolated, cross-turn Travis B" in skill
    assert "never mutating subagents and Travis B together" in skill
    assert "A failed planner ends execution for that turn" in skill
    assert (
        "If Travis B performs the delegated task and the parent verifies afterward, "
        "the route is `mixed`, with one `travis-b` task followed by one `parent` task."
        in contract
    )
    assert "A plan does not authorize commit, integration, push, publication" in contract
    assert "memory retention, or replay" in contract
    assert role["allowedTools"] == ["find", "ls"]
    assert "lsp" not in role["allowedTools"]
    assert role["allowedEffects"] == ["read"]
    assert (
        "The planner must not read or grep delegated goal-file contents" in contract
    )
    assert (
        "Answer the user's question first in ordinary language" in skill
    )
    assert (
        "Do not show route names, orchestration identifiers, worker plumbing" in skill
    )
    assert (
        "Report every failed attempt, uncertainty, or blocker" in skill
    )
    assert (
        "Preserve the user's requested scope and detail level in worker prompts "
        "and the final reply." in skill
    )
    assert (
        "If the user asks for only the answer or no behind-the-scenes details, "
        "omit workflow assurance and ancillary analysis." in skill
    )
    assert "must not broaden the requested scope or detail level" in role[
        "resultSchema"
    ]["description"]


def test_coordination_skill_makes_explicitly_refused_tools_unavailable() -> None:
    skill = (
        Path(__file__).parents[1]
        / "travis/resources/skills/coordination/SKILL.md"
    ).read_text(encoding="utf-8")

    assert "Treat each refused tool as unavailable for the whole turn" in skill
    assert "Do not use Bash to list files, resolve paths, or inspect a refused mechanism" in skill
    assert "In automatic mode, call the planner when any applies" in skill
    assert "Otherwise stay direct" in skill


def test_coordination_skill_plans_before_selecting_a_durable_travis_b_route() -> None:
    skill = (
        Path(__file__).parents[1]
        / "travis/resources/skills/coordination/SKILL.md"
    ).read_text(encoding="utf-8")

    assert (
        "Before selecting or loading an execution route, automatic mode must call "
        "exactly one planner when the goal asks for another Travis, a separate "
        "workspace or worktree, durable handoff, retention, recovery, or release."
        in skill
    )
    assert (
        "Neither the parent nor the planner may inspect delegated goal-file contents "
        "or leak their expected answer into Travis B's handoff; the parent may inspect "
        "them only after B reports, for independent verification."
        in skill
    )
    assert (
        "After a valid plan, start and dispatch Travis B before any parent read of "
        "delegated goal-file contents; parent verification depends on B's terminal "
        "packet and happens afterward."
        in skill
    )
    assert (
        "Do not read or expand an execution-route skill until the planner has returned "
        "and the plan is validated." in skill
    )
    assert (
        "Before planner completion, the only permitted reads are the planning contract "
        "and allowed path metadata; never batch or perform an execution-route skill read."
        in skill
    )
    assert (
        "Before the final answer, inspect this turn's tool results for every error, "
        "denial, and retry." in skill
    )


def test_orchestration_skill_forbids_raw_tmux_even_after_timeouts() -> None:
    skill = (
        Path(__file__).parents[1]
        / "travis/resources/skills/orchestration/SKILL.md"
    ).read_text(encoding="utf-8")

    assert (
        "Never call the tmux tool or tmux executable directly, including "
        "list-sessions, capture-pane, send-keys, or screen scraping."
        in skill
    )
    assert (
        "A timeout does not permit bypassing the helper; use only helper receipts, "
        "bounded waits, recovery, cancellation, retention, and release."
        in skill
    )
    assert (
        "Treat protocol read -> guide -> mutation as a gate: do not infer request fields, "
        "guess a request body, or retry a guessed body."
        in skill
    )


@pytest.mark.parametrize("terminal_status", ["timeout", "cancelled"])
def test_tui_coordination_terminal_planner_failure_is_visible_and_not_retried(
    tmp_path: Path,
    terminal_status: str,
) -> None:
    calls = {"count": 0}
    backend_tasks = []

    def backend(task):
        backend_tasks.append(task)
        return SubagentResult(
            task_id=task.id,
            backend=task.backend,
            role=task.role,
            status=terminal_status,
            summary=f"Planner {terminal_status} before a recommendation.",
            errors=[f"planner_{terminal_status}"],
        )

    def provider(model, context):
        calls["count"] += 1
        if calls["count"] == 1:
            return tool_call_response_events(
                model,
                "spawn_subagent",
                {
                    "role": "coordination-planner",
                    "goal": "Plan once and settle terminal failure without retry.",
                    "wait": True,
                },
                call_id=f"planner-{terminal_status}",
            )
        result = _tool_results_from_context(context)[-1]
        assert result.details["status"] == terminal_status
        return text_response_events(
            model,
            f"Blocker: planner {terminal_status}; no retry; no execution.",
        )

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=180, rows=40),
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    app.session.subagents.register_backend(CallableSubagentBackend("internal", backend))
    try:
        app.run_turn("/coordination --deep inspect a bounded scope")

        assert len(backend_tasks) == 1
        assert len(app.session.subagents.list_tasks()) == 1
        assert app.session.subagents.list_results()[0].status == terminal_status
        assert [call.name for call in _tool_calls(app)] == ["spawn_subagent"]
        assert f"planner {terminal_status}; no retry" in _visible_text(app)
    finally:
        app.close()


def test_tui_coordination_one_child_correction_uses_the_last_remaining_slot(
    tmp_path: Path,
) -> None:
    calls = {"count": 0}
    backend_tasks = []
    first_worker_id = {"value": ""}

    def backend(task):
        backend_tasks.append(task)
        if task.role == "coordination-planner":
            return _typed_result(
                task,
                _plan(owners=("subagent",), scopes=("scope-a",)),
            )
        if task.role == "reviewer":
            return "Uncertain: required evidence was not observed."
        return "Correction complete with observed evidence."

    def provider(model, context):
        calls["count"] += 1
        if calls["count"] == 1:
            arguments = {
                "role": "coordination-planner",
                "goal": "Plan one inspection and preserve one correction slot.",
                "wait": True,
            }
        elif calls["count"] == 2:
            arguments = {
                "role": "reviewer",
                "goal": "Inspect scope-a and return observed evidence.",
                "wait": True,
            }
        elif calls["count"] == 3:
            first_worker = _tool_results_from_context(context)[-1]
            first_worker_id["value"] = first_worker.details["taskId"]
            assert "Uncertain" in first_worker.content[0].text
            arguments = {
                "role": "reviewer-correction",
                "goal": (
                    f"Correct only missing evidence from {first_worker_id['value']}; "
                    "inspect scope-a once and stop."
                ),
                "wait": True,
            }
        else:
            return text_response_events(
                model,
                "Outcome: one correction settled; all three spawn slots are accounted for.",
            )
        return tool_call_response_events(
            model,
            "spawn_subagent",
            arguments,
            call_id=f"correction-{calls['count']}",
        )

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=180, rows=40),
        agent_dir=str(tmp_path / "agent"),
        project_trust_override=True,
    )
    app.session.subagents.register_backend(CallableSubagentBackend("internal", backend))
    try:
        app.run_turn("/coordination --deep inspect scope-a and correct one missing fact")

        assert [task.role for task in backend_tasks] == [
            "coordination-planner",
            "reviewer",
            "reviewer-correction",
        ]
        assert first_worker_id["value"] in backend_tasks[-1].goal
        assert app.session._model_subagents_spawned_this_turn == 3
        assert "one correction settled" in _visible_text(app)
    finally:
        app.close()
