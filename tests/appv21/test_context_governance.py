from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.1"))

from appv21.context.budget import ContextBudgetManager, DEFAULT_SECTION_BUDGETS
from appv21.context.compactor import RuntimeContextCompactor
from appv21.context.overflow import ContextOverflowPolicy
from appv21.context.selector import ContextSelector
from appv21.extensions.skills import SkillRouter
from appv21.runtime.agent_runtime import AppV21AgentRuntime
from appv21.runtime.decisions import RuntimeDecision
from appv21.runtime.services import create_appv21_runtime_services
from appv21.state.models import AgentState, Artifact, MutationLease, MutationReceipt, PauseState, PlanState, RequestEnvelope, WorldRef
from appv21.tools.broker import ToolBroker
from scripts.live_appv21_staged_file_management_matrix_report import _build_report


def test_context_budget_estimates_section_sizes() -> None:
    payload = {
        "system": {"role": "architect"},
        "agent": ["planner", "executor"],
        "untracked": "ignored for section budgets",
    }

    estimate = ContextBudgetManager().estimate(payload)

    expected_system_chars = len(json.dumps(payload["system"], sort_keys=True, default=str))
    expected_agent_chars = len(json.dumps(payload["agent"], sort_keys=True, default=str))
    expected_total_chars = len(json.dumps(payload, sort_keys=True, default=str))

    assert estimate["total_chars"] == expected_total_chars
    assert estimate["sections"]["system"] == {
        "chars": expected_system_chars,
        "budget": DEFAULT_SECTION_BUDGETS["system"],
        "over_budget": False,
    }
    assert estimate["sections"]["agent"] == {
        "chars": expected_agent_chars,
        "budget": DEFAULT_SECTION_BUDGETS["agent"],
        "over_budget": False,
    }
    assert estimate["sections"]["skills"]["chars"] == 0
    assert estimate["over_budget_sections"] == []


def test_context_budget_marks_over_budget_sections() -> None:
    manager = ContextBudgetManager(
        section_budgets={
            **DEFAULT_SECTION_BUDGETS,
            "system": 2,
            "tools": 3,
        }
    )
    payload = {
        "system": "abcd",
        "tools": {"names": ["search"]},
        "world": "within",
    }

    estimate = manager.estimate(payload)

    assert estimate["sections"]["system"]["over_budget"] is True
    assert estimate["sections"]["tools"]["over_budget"] is True
    assert estimate["sections"]["world"]["over_budget"] is False
    assert estimate["over_budget_sections"] == ["system", "tools"]


def test_context_budget_accepts_partial_section_budget_overrides() -> None:
    manager = ContextBudgetManager(section_budgets={"world": 10})

    estimate = manager.estimate({"world": "x" * 20, "system": "ok"})

    assert estimate["sections"]["world"]["budget"] == 10
    assert estimate["sections"]["world"]["over_budget"] is True
    assert estimate["sections"]["system"]["budget"] == DEFAULT_SECTION_BUDGETS["system"]


def test_context_budget_defensively_copies_constructor_budget_dict() -> None:
    budgets = {
        **DEFAULT_SECTION_BUDGETS,
        "system": 2,
    }
    manager = ContextBudgetManager(section_budgets=budgets)

    budgets["system"] = 1000

    estimate = manager.estimate({"system": "abcd"})

    assert estimate["sections"]["system"]["budget"] == 2
    assert estimate["sections"]["system"]["over_budget"] is True


def test_context_budget_section_budgets_cannot_be_mutated() -> None:
    manager = ContextBudgetManager()

    with pytest.raises(TypeError):
        manager.section_budgets["world"] = 1

    assert manager.section_budgets["world"] == DEFAULT_SECTION_BUDGETS["world"]


def test_context_budget_rejects_unsupported_payload_objects() -> None:
    class UnsupportedPayload:
        pass

    manager = ContextBudgetManager()

    with pytest.raises(TypeError, match="JSON serializable"):
        manager.estimate({"system": UnsupportedPayload()})


def test_context_budget_over_budget_sections_use_canonical_order() -> None:
    manager = ContextBudgetManager(
        section_budgets={
            "tools": 3,
            "system": 2,
            "agent": DEFAULT_SECTION_BUDGETS["agent"],
            "skills": DEFAULT_SECTION_BUDGETS["skills"],
            "world": DEFAULT_SECTION_BUDGETS["world"],
            "state": DEFAULT_SECTION_BUDGETS["state"],
            "output_contract": DEFAULT_SECTION_BUDGETS["output_contract"],
            "decomposition": DEFAULT_SECTION_BUDGETS["decomposition"],
        }
    )

    estimate = manager.estimate(
        {
            "tools": {"names": ["search"]},
            "system": "abcd",
        }
    )

    assert list(estimate["sections"]) == list(DEFAULT_SECTION_BUDGETS)
    assert estimate["over_budget_sections"] == ["system", "tools"]


def test_staged_probe_report_has_context_budget_matrix(tmp_path: Path) -> None:
    report = _build_report(repo=tmp_path, result={"status": "completed", "events": []}, provider=None, max_turns=1)

    assert report["context_budget_matrix"] == []
    assert report["selection_matrix"] == []


def test_staged_probe_report_captures_invalid_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "docs" / "workspace_manifest.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text("{not json", encoding="utf-8")

    report = _build_report(repo=tmp_path, result={"status": "completed", "events": []}, provider=None, max_turns=1)

    assert report["file_matrix"]["manifest"] is None
    assert "manifest_error" in report["file_matrix"]
    assert report["verdict"]["checks"]["manifest_valid"] is False
    assert report["verdict"]["overall"] == "fail"


def test_context_overflow_policy_classifies_provider_errors() -> None:
    policy = ContextOverflowPolicy()

    assert policy.is_context_overflow(RuntimeError("context length exceeded")) is True
    assert policy.is_context_overflow(RuntimeError("context_length limit reached")) is True
    assert policy.is_context_overflow(RuntimeError("maximum context window exceeded")) is True
    assert policy.is_context_overflow(RuntimeError("too many tokens in request")) is True
    assert policy.is_context_overflow(RuntimeError("HTTP 413 from provider")) is True
    assert policy.is_context_overflow(RuntimeError("413 request too large")) is True
    assert policy.is_context_overflow(RuntimeError("413 Payload Too Large")) is True
    assert policy.is_context_overflow(RuntimeError("request too large")) is True
    assert policy.is_context_overflow(RuntimeError("business id 413 failed validation")) is False
    assert policy.is_context_overflow(RuntimeError("rate limit exceeded")) is False


def test_workspace_cleanup_skill_activates_as_card() -> None:
    state = AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(
            request_id="req",
            user_goal="Please cleanup and organize this workspace.",
            root_path=".",
        ),
    )

    cards = SkillRouter().active_skills(state)

    assert cards == [
        {
            "skill_id": "workspace_cleanup",
            "triggers": ["cleanup", "organize", "move", "workspace"],
            "modes": ["START", "THINK", "OBSERVE", "PLAN", "ACT", "VERIFY"],
            "summary": "Organize observed workspace files while preserving protected project, documentation, asset, and secret paths.",
            "tool_preferences": ["repo_snapshot", "read_file"],
            "artifact_templates": ["workspace_manifest"],
            "preservation_rules": [
                "tests/**",
                "src/**",
                "assets/**",
                "secrets/**",
                "README.md",
                "docs/**",
                "**/keep*",
                "**/do_not_move*",
                "**/old_blob*",
            ],
            "verification_hints": [
                "Confirm protected paths are excluded from proposed moves.",
                "Workspace manifest must include observed_files, protected_paths, proposed_moves, and skipped_paths sections.",
                "Do not read or expose secret file contents.",
            ],
            "budget_priority": 80,
        }
    ]
    assert "prompt_patch" not in cards[0]


def test_workspace_cleanup_skill_does_not_activate_for_remove_imports() -> None:
    state = AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(
            request_id="req",
            user_goal="remove unused imports",
            root_path=".",
        ),
    )

    assert SkillRouter().active_skills(state) == []


def test_workspace_cleanup_skill_cards_are_isolated_from_returned_mutations() -> None:
    state = AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(
            request_id="req",
            user_goal="Organize this workspace safely.",
            root_path=".",
        ),
    )
    router = SkillRouter()

    cards = router.active_skills(state)
    cards[0]["tool_preferences"].append("mutated")
    cards[0]["preservation_rules"].clear()

    next_cards = router.active_skills(state)

    assert next_cards[0]["tool_preferences"] == ["repo_snapshot", "read_file"]
    assert next_cards[0]["preservation_rules"] == [
        "tests/**",
        "src/**",
        "assets/**",
        "secrets/**",
        "README.md",
        "docs/**",
        "**/keep*",
        "**/do_not_move*",
        "**/old_blob*",
    ]


def test_mutation_validation_rejects_protected_path_moves(tmp_path: Path) -> None:
    broker = ToolBroker(root_path=tmp_path)

    errors = broker.validate_mutation_intent(
        [
            {
                "action": "move",
                "source": "src/config/settings.json",
                "destination": "artifacts/logs/settings.json",
            }
        ]
    )

    assert errors == ["protected_source_path:src/config/settings.json"]


@pytest.mark.parametrize(
    ("destination", "expected_error"),
    [
        ("README.md", "protected_destination_path:README.md"),
        ("docs/archive/legacy_report.md", "protected_destination_path:docs/archive/legacy_report.md"),
    ],
)
def test_mutation_validation_rejects_protected_move_destinations(
    tmp_path: Path, destination: str, expected_error: str
) -> None:
    broker = ToolBroker(root_path=tmp_path)

    errors = broker.validate_mutation_intent(
        [{"action": "move", "source": "notes/new.md", "destination": destination}]
    )

    assert errors == [expected_error]


@pytest.mark.parametrize("source", ["bookkeeping.md", "unkeepable.md"])
def test_mutation_validation_marker_policy_uses_filename_prefixes(tmp_path: Path, source: str) -> None:
    broker = ToolBroker(root_path=tmp_path)

    errors = broker.validate_mutation_intent(
        [{"action": "move", "source": source, "destination": f"artifacts/{source}"}]
    )

    assert errors == []


@pytest.mark.parametrize(
    "source",
    [
        "README.md",
        "docs/archive/legacy_report.md",
        "secrets/.env",
        "notes/keep-plan.md",
        "notes/do_not_move-plan.md",
        "notes/old_blob-archive.md",
    ],
)
def test_mutation_validation_rejects_workspace_cleanup_preservation_sources(tmp_path: Path, source: str) -> None:
    broker = ToolBroker(root_path=tmp_path)

    errors = broker.validate_mutation_intent(
        [{"action": "move", "source": source, "destination": f"artifacts/{Path(source).name}"}]
    )

    assert errors == [f"protected_source_path:{source}"]


@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "docs/archive/legacy_report.md",
        "secrets/.env",
        "assets/keep-icon.svg",
    ],
)
def test_mutation_validation_rejects_protected_write_destinations(tmp_path: Path, path: str) -> None:
    broker = ToolBroker(root_path=tmp_path)

    errors = broker.validate_mutation_intent([{"action": "write", "path": path, "content": "replace"}])

    assert errors == [f"protected_destination_path:{path}"]


def test_mutation_validation_allows_workspace_manifest_write(tmp_path: Path) -> None:
    broker = ToolBroker(root_path=tmp_path)

    errors = broker.validate_mutation_intent(
        [{"action": "write", "path": "docs/workspace_manifest.json", "content": {"observed_files": []}}]
    )

    assert errors == []


@pytest.mark.parametrize(
    "user_goal",
    [
        "Organize this workspace safely.",
        "Clean up file management workspace.",
        "Move markdown notes into docs.",
    ],
)
def test_workspace_cleanup_skill_requires_file_or_workspace_context_to_activate(user_goal: str) -> None:
    state = AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(
            request_id="req",
            user_goal=user_goal,
            root_path=".",
        ),
    )

    cards = SkillRouter().active_skills(state)

    assert [card["skill_id"] for card in cards] == ["workspace_cleanup"]


@pytest.mark.parametrize(
    "user_goal",
    [
        "remove unused imports",
        "organize imports",
        "move the button left",
    ],
)
def test_workspace_cleanup_skill_ignores_action_words_without_file_or_workspace_context(user_goal: str) -> None:
    state = AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(
            request_id="req",
            user_goal=user_goal,
            root_path=".",
        ),
    )

    assert SkillRouter().active_skills(state) == []


def test_context_selector_preserves_repo_snapshot_and_latest_refs() -> None:
    state = AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(request_id="req", user_goal="Inspect the repo.", root_path="."),
    )
    state.world.refs["world://old"] = WorldRef(
        ref_id="world://old",
        kind="tool_result",
        summary="old ref",
        payload={"content": "must not leak"},
    )
    state.world.refs["world://repo_snapshot/latest"] = WorldRef(
        ref_id="world://repo_snapshot/latest",
        kind="repo_snapshot",
        summary="latest repo map",
        payload={"files": ["secret.txt"]},
    )
    for index in range(4):
        state.world.refs[f"world://latest/{index}"] = WorldRef(
            ref_id=f"world://latest/{index}",
            kind="tool_result",
            summary=f"latest ref {index}",
            payload={"raw": f"payload {index}"},
            trust="runtime_observed",
        )

    selected = ContextSelector(max_world_refs=2).select(
        state,
        active_skills=[],
        tool_specs=[],
    )

    world_refs = selected["world"]["world_refs"]
    selected_ref_ids = [ref["ref_id"] for ref in world_refs]
    assert selected_ref_ids == [
        "world://repo_snapshot/latest",
        "world://latest/3",
    ]
    assert selected["selection"]["selected_world_refs"] == selected_ref_ids
    assert all(set(ref) == {"ref_id", "kind", "summary", "trust"} for ref in world_refs)
    assert all("payload" not in ref for ref in world_refs)


def test_context_selector_caps_repeated_repo_snapshots_to_latest_ref() -> None:
    state = AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(request_id="req", user_goal="Inspect the repo.", root_path="."),
    )
    for index in range(5):
        state.world.refs[f"world://repo_snapshot/{index}"] = WorldRef(
            ref_id=f"world://repo_snapshot/{index}",
            kind="tool_result",
            summary=f"repo map {index}",
            payload={"tool_name": "repo_snapshot", "files": [f"{index}.md"]},
        )
    state.world.refs["world://repo_snapshot/latest"] = WorldRef(
        ref_id="world://repo_snapshot/latest",
        kind="repo_snapshot",
        summary="latest repo map",
        payload={"files": ["latest.md"]},
    )
    state.world.refs["world://latest/other"] = WorldRef(
        ref_id="world://latest/other",
        kind="tool_result",
        summary="other evidence",
        payload={"raw": "payload"},
    )

    selected = ContextSelector(max_world_refs=2).select(state, active_skills=[], tool_specs=[])

    assert selected["selection"]["selected_world_refs"] == [
        "world://repo_snapshot/latest",
        "world://latest/other",
    ]


def test_context_selector_uses_compaction_digest_refs_and_exposes_digest() -> None:
    state = AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(request_id="req", user_goal="Inspect the repo.", root_path="."),
    )
    for index in range(5):
        state.world.refs[f"world://latest/{index}"] = WorldRef(
            ref_id=f"world://latest/{index}",
            kind="tool_result",
            summary=f"latest ref {index}",
            payload={"raw": f"payload {index}"},
        )
    state.context.world_digest = {
        "preserved_world_refs": ["world://latest/1", "world://latest/3"],
        "compacted_world_ref_count": 5,
    }

    selected = ContextSelector(max_world_refs=4).select(state, active_skills=[], tool_specs=[])

    assert selected["selection"]["selected_world_refs"] == ["world://latest/1", "world://latest/3"]
    assert selected["world"]["compacted"] is True
    assert selected["world"]["world_digest"]["compacted_world_ref_count"] == 5


def test_compactor_preserves_receipts_and_repo_refs() -> None:
    state = AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(request_id="req", user_goal="Inspect the repo.", root_path="."),
    )
    state.world.refs["world://old"] = WorldRef(
        ref_id="world://old",
        kind="tool_result",
        summary="old ref",
        payload={"content": "may compact"},
    )
    state.world.refs["world://repo_snapshot/latest"] = WorldRef(
        ref_id="world://repo_snapshot/latest",
        kind="repo_snapshot",
        summary="latest repo map",
        payload={"files": ["README.md"]},
    )
    state.world.refs["world://artifact/evidence"] = WorldRef(
        ref_id="world://artifact/evidence",
        kind="tool_result",
        summary="artifact evidence",
        payload={"path": "docs/report.md"},
    )
    state.world.refs["world://artifact/evidence-a"] = WorldRef(
        ref_id="world://artifact/evidence-a",
        kind="tool_result",
        summary="artifact evidence a",
        payload={"path": "docs/a.md"},
    )
    state.world.refs["world://artifact/evidence-z"] = WorldRef(
        ref_id="world://artifact/evidence-z",
        kind="tool_result",
        summary="artifact evidence z",
        payload={"path": "docs/z.md"},
    )
    for index in range(4):
        state.world.refs[f"world://latest/{index}"] = WorldRef(
            ref_id=f"world://latest/{index}",
            kind="tool_result",
            summary=f"latest ref {index}",
            payload={"raw": f"payload {index}"},
        )
    state.world.artifacts["artifact"] = Artifact(
        artifact_id="artifact",
        kind="manifest",
        content={"paths": ["docs/report.md"]},
        producer="test",
        evidence_refs=["world://artifact/evidence"],
    )
    state.world.artifacts["artifact-multi"] = Artifact(
        artifact_id="artifact-multi",
        kind="manifest",
        content={"paths": ["docs/z.md", "docs/a.md"]},
        producer="test",
        evidence_refs=["world://artifact/evidence-z", "world://artifact/evidence-a"],
    )
    state.world.artifacts["artifact-a"] = Artifact(
        artifact_id="artifact-a",
        kind="manifest",
        content={"paths": ["README.md"]},
        producer="test",
        evidence_refs=["world://repo_snapshot/latest"],
    )
    state.pauses.append(
        PauseState(
            pause_id="pause",
            pause_type="approval",
            summary="Needs approval",
            options=[{"label": "continue"}],
        )
    )
    state.world.mutation_leases["lease"] = MutationLease(
        lease_id="lease",
        operation_batch_id="batch",
        allowed_operations=[{"op": "write", "path": "docs/report.md"}],
        allowed_sources=[],
        allowed_destinations=["docs/report.md"],
    )
    state.world.mutation_leases["lease-a"] = MutationLease(
        lease_id="lease-a",
        operation_batch_id="batch",
        allowed_operations=[{"op": "write", "path": "README.md"}],
        allowed_sources=[],
        allowed_destinations=["README.md"],
    )
    state.world.mutation_receipts["receipt"] = MutationReceipt(
        receipt_id="receipt",
        lease_id="lease",
        status="completed",
        operations=[{"op": "write", "path": "docs/report.md"}],
        touched_paths=["docs/report.md"],
    )
    state.world.mutation_receipts["receipt-a"] = MutationReceipt(
        receipt_id="receipt-a",
        lease_id="lease-a",
        status="completed",
        operations=[{"op": "write", "path": "README.md"}],
        touched_paths=["README.md"],
    )
    state.world.verification_receipts["verification"] = {"checks": [{"status": "passed"}]}
    state.world.verification_receipts["verification-a"] = {"checks": [{"status": "also-passed"}]}

    digest = RuntimeContextCompactor().compact(state)

    digest["immutable_classes"].append("mutated")
    digest["preservation_policy"]["keep_latest_world_ref_count"] = 99
    digest["open_pause"]["options"][0]["label"] = "mutated"
    digest["active_lease_snapshots"]["lease"]["allowed_operations"][0]["path"] = "mutated"
    digest["mutation_receipt_snapshots"]["receipt"]["operations"][0]["path"] = "mutated"
    digest["verification_receipt_snapshots"]["verification"]["checks"][0]["status"] = "mutated"
    digest["artifact_evidence_refs"]["artifact"].append("mutated")

    second_digest = RuntimeContextCompactor().compact(state)

    assert second_digest["open_pause"]["options"] == [{"label": "continue"}]
    assert second_digest["active_lease_snapshots"]["lease"]["allowed_operations"] == [
        {"op": "write", "path": "docs/report.md"}
    ]
    assert second_digest["mutation_receipt_snapshots"]["receipt"]["operations"] == [
        {"op": "write", "path": "docs/report.md"}
    ]
    assert second_digest["verification_receipt_snapshots"]["verification"] == {"checks": [{"status": "passed"}]}
    assert second_digest["artifact_evidence_refs"]["artifact"] == ["world://artifact/evidence"]
    assert second_digest["artifact_evidence_refs"]["artifact-multi"] == [
        "world://artifact/evidence-a",
        "world://artifact/evidence-z",
    ]
    assert state.pauses[0].options == [{"label": "continue"}]
    assert state.world.mutation_leases["lease"].allowed_operations == [{"op": "write", "path": "docs/report.md"}]
    assert state.world.mutation_receipts["receipt"].operations == [{"op": "write", "path": "docs/report.md"}]
    assert state.world.verification_receipts["verification"] == {"checks": [{"status": "passed"}]}
    assert state.world.artifacts["artifact"].evidence_refs == ["world://artifact/evidence"]
    assert state.world.artifacts["artifact-multi"].evidence_refs == [
        "world://artifact/evidence-z",
        "world://artifact/evidence-a",
    ]
    digest = second_digest

    assert digest["immutable_classes"] == [
        "user_request",
        "constraints",
        "pause_state",
        "mutation_receipts",
        "verification_receipts",
        "active_leases",
    ]
    assert digest["preservation_policy"] == {
        "keep_repo_snapshot_refs": True,
        "keep_artifact_evidence_refs": True,
        "keep_latest_world_ref_count": 3,
    }
    assert digest["latest_world_refs"] == ["world://latest/1", "world://latest/2", "world://latest/3"]
    assert digest["preserved_world_refs"] == [
        "world://artifact/evidence",
        "world://artifact/evidence-a",
        "world://artifact/evidence-z",
        "world://latest/1",
        "world://latest/2",
        "world://latest/3",
        "world://repo_snapshot/latest",
    ]
    assert digest["active_leases"] == ["lease", "lease-a"]
    assert list(digest["active_lease_snapshots"]) == ["lease", "lease-a"]
    assert digest["active_lease_snapshots"]["lease-a"]["allowed_destinations"] == ["README.md"]
    assert digest["mutation_receipts"] == ["receipt", "receipt-a"]
    assert list(digest["mutation_receipt_snapshots"]) == ["receipt", "receipt-a"]
    assert digest["mutation_receipt_snapshots"]["receipt-a"]["touched_paths"] == ["README.md"]
    assert digest["verification_receipts"] == ["verification", "verification-a"]
    assert list(digest["verification_receipt_snapshots"]) == ["verification", "verification-a"]
    assert digest["verification_receipt_snapshots"]["verification-a"] == {"checks": [{"status": "also-passed"}]}
    assert list(digest["artifact_evidence_refs"]) == ["artifact", "artifact-a", "artifact-multi"]
    assert digest["artifact_evidence_refs"]["artifact-multi"] == [
        "world://artifact/evidence-a",
        "world://artifact/evidence-z",
    ]
    assert digest["artifact_evidence_refs"] == {
        "artifact": ["world://artifact/evidence"],
        "artifact-a": ["world://repo_snapshot/latest"],
        "artifact-multi": ["world://artifact/evidence-a", "world://artifact/evidence-z"],
    }


def test_context_selector_filters_tools_by_mode() -> None:
    tool_specs = [
        {"name": "repo_snapshot", "guidance": "observe"},
        {"name": "read_file", "guidance": "inspect"},
        {"name": "write_file", "guidance": "mutate"},
    ]
    state = AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(request_id="req", user_goal="Inspect the repo.", root_path="."),
        mode="PLAN",
    )

    plan_selected = ContextSelector().select(state, active_skills=[], tool_specs=tool_specs)

    assert plan_selected["tools"] == []
    assert plan_selected["selection"]["selected_tools"] == []

    state.mode = "VERIFY"

    verify_selected = ContextSelector().select(state, active_skills=[], tool_specs=tool_specs)

    assert [tool["name"] for tool in verify_selected["tools"]] == ["repo_snapshot", "read_file"]
    assert verify_selected["selection"]["selected_tools"] == ["repo_snapshot", "read_file"]


def test_context_selector_unknown_mode_hides_tools() -> None:
    tool_specs = [
        {"name": "repo_snapshot", "guidance": "observe"},
        {"name": "read_file", "guidance": "inspect"},
    ]
    state = AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(request_id="req", user_goal="Inspect the repo.", root_path="."),
        mode="UNSUPPORTED",
    )

    selected = ContextSelector().select(state, active_skills=[], tool_specs=tool_specs)

    assert selected["tools"] == []
    assert selected["selection"]["selected_tools"] == []


def test_context_selector_state_output_is_isolated_from_agent_state_mutation() -> None:
    state = AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(request_id="req", user_goal="Inspect the repo.", root_path="."),
    )
    state.plan = PlanState(
        intent="preserve",
        steps=[{"step_id": "one", "notes": ["original"]}],
        runtime_plan={"nested": {"status": "original"}},
    )
    state.world.artifacts["artifact"] = Artifact(
        artifact_id="artifact",
        kind="manifest",
        content={"paths": ["README.md"]},
        producer="test",
    )
    state.world.mutation_leases["lease"] = MutationLease(
        lease_id="lease",
        operation_batch_id="batch",
        allowed_operations=[{"op": "move", "path": "README.md"}],
        allowed_sources=["README.md"],
        allowed_destinations=["docs/README.md"],
    )
    state.world.mutation_receipts["receipt"] = MutationReceipt(
        receipt_id="receipt",
        lease_id="lease",
        status="completed",
        operations=[{"op": "move", "path": "README.md"}],
        touched_paths=["README.md"],
    )
    state.world.verification_receipts["verification"] = {"checks": [{"status": "original"}]}
    state.pauses.append(
        PauseState(
            pause_id="pause",
            pause_type="approval",
            summary="Needs approval",
            options=[{"label": "continue"}],
        )
    )

    selected = ContextSelector().select(state, active_skills=[], tool_specs=[])

    selected["state"]["plan"]["steps"][0]["notes"].append("mutated")
    selected["state"]["plan"]["runtime_plan"]["nested"]["status"] = "mutated"
    selected["state"]["artifacts"]["artifact"]["content"]["paths"].append("mutated")
    selected["state"]["mutation_leases"]["lease"]["allowed_operations"][0]["path"] = "mutated"
    selected["state"]["mutation_receipts"]["receipt"]["operations"][0]["path"] = "mutated"
    selected["state"]["verification_receipts"]["verification"]["checks"][0]["status"] = "mutated"
    selected["state"]["pauses"][0]["options"][0]["label"] = "mutated"

    assert state.plan.steps == [{"step_id": "one", "notes": ["original"]}]
    assert state.plan.runtime_plan == {"nested": {"status": "original"}}
    assert state.world.artifacts["artifact"].content == {"paths": ["README.md"]}
    assert state.world.mutation_leases["lease"].allowed_operations == [{"op": "move", "path": "README.md"}]
    assert state.world.mutation_receipts["receipt"].operations == [{"op": "move", "path": "README.md"}]
    assert state.world.verification_receipts["verification"] == {"checks": [{"status": "original"}]}
    assert state.pauses[0].options == [{"label": "continue"}]


def test_prompt_context_prepared_records_budget_and_selection(tmp_path: Path) -> None:
    class PromptMetadataProvider:
        provider_id = "prompt-metadata"

        def __init__(self) -> None:
            self.seen_prompt = False

        def decide(self, prompt_payload: dict) -> RuntimeDecision:
            self.seen_prompt = True
            assert "context_budget" in prompt_payload
            assert "selection" in prompt_payload
            context_budget = prompt_payload["context_budget"]
            assert context_budget["measured_without_self"] is True
            assert context_budget["total_chars"] > 0
            assert context_budget["final_prompt_chars"] == len(json.dumps(prompt_payload, sort_keys=True))
            assert context_budget["final_prompt_chars"] > context_budget["total_chars"]
            assert prompt_payload["selection"]["mode"] == "START"
            assert prompt_payload["agent"]["mode"] == "START"
            assert set(prompt_payload["selection"]) == {"mode", "selected_world_refs", "selected_tools", "selected_skills"}
            return RuntimeDecision(kind="observe", reason="metadata captured")

    provider = PromptMetadataProvider()
    result = AppV21AgentRuntime(
        root_path=tmp_path,
        services=create_appv21_runtime_services(root_path=tmp_path, provider=provider),
        max_turns=1,
    ).run("Inspect the repo.")

    prompt_events = [event for event in result["events"] if event["event_type"] == "PromptContextPrepared"]
    assert provider.seen_prompt is True
    assert prompt_events
    event_budget = prompt_events[-1]["payload"]["context_budget"]
    assert event_budget["measured_without_self"] is True
    assert event_budget["total_chars"] > 0
    assert event_budget["final_prompt_chars"] > event_budget["total_chars"]
    assert prompt_events[-1]["payload"]["selection"]["mode"] == "START"
    assert "model" in prompt_events[-1]["payload"]
    assert "tool_count" in prompt_events[-1]["payload"]
    assert "skill_count" in prompt_events[-1]["payload"]


def test_context_overflow_retries_after_runtime_compaction(tmp_path: Path) -> None:
    for index in range(6):
        (tmp_path / f"note-{index}.txt").write_text(f"note {index}", encoding="utf-8")

    class OverflowOnceProvider:
        provider_id = "overflow-once"

        def __init__(self) -> None:
            self.calls = 0
            self.saw_retry = False

        def decide(self, prompt_payload: dict) -> RuntimeDecision:
            self.calls += 1
            if self.calls == 1:
                return RuntimeDecision(kind="observe", reason="Collect repo snapshot.")
            if 2 <= self.calls <= 7:
                return RuntimeDecision(
                    kind="read_file",
                    reason="Accumulate context refs.",
                    payload={"path": f"note-{self.calls - 2}.txt"},
                )
            if self.calls == 8:
                raise RuntimeError("provider rejected request: context length exceeded")
            self.saw_retry = True
            return RuntimeDecision(kind="finalize", reason="Recovered after compaction.", payload={"explicit_noop": True})

    provider = OverflowOnceProvider()
    result = AppV21AgentRuntime(
        root_path=tmp_path,
        services=create_appv21_runtime_services(root_path=tmp_path, provider=provider),
        max_turns=8,
    ).run("Summarize the notes without changes.")

    event_types = [event["event_type"] for event in result["events"]]
    assert result["status"] == "completed"
    assert provider.saw_retry is True
    assert event_types.count("ContextOverflowDetected") == 1
    assert "ContextCompactionRequested" in event_types
    assert "ContextCompacted" in event_types
    assert "ContextOverflowRecoveryFailed" not in event_types


def test_context_overflow_retry_is_exactly_once(tmp_path: Path) -> None:
    class AlwaysOverflowProvider:
        provider_id = "always-overflow"

        def __init__(self) -> None:
            self.calls = 0

        def decide(self, _prompt_payload: dict) -> RuntimeDecision:
            self.calls += 1
            raise RuntimeError("context length exceeded")

    provider = AlwaysOverflowProvider()
    runtime = AppV21AgentRuntime(
        root_path=tmp_path,
        services=create_appv21_runtime_services(root_path=tmp_path, provider=provider),
    )
    state = AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(request_id="req", user_goal="Inspect the repo.", root_path=str(tmp_path)),
    )
    for index in range(8):
        state.world.refs[f"world://ref/{index}"] = WorldRef(
            ref_id=f"world://ref/{index}",
            kind="tool_result",
            summary=f"ref {index}",
            payload={"index": index},
        )

    with pytest.raises(RuntimeError, match="context length exceeded"):
        runtime.run_turn(state)

    event_types = [event["event_type"] for event in runtime.store.to_dicts()]
    assert provider.calls == 2
    assert event_types.count("ContextOverflowDetected") == 1
    assert event_types.count("ContextOverflowRecoveryFailed") == 1


def test_non_overflow_provider_exception_is_reraised_without_recovery_events(tmp_path: Path) -> None:
    class NonOverflowProvider:
        provider_id = "non-overflow"

        def __init__(self) -> None:
            self.calls = 0

        def decide(self, _prompt_payload: dict) -> RuntimeDecision:
            self.calls += 1
            raise RuntimeError("rate limit exceeded")

    provider = NonOverflowProvider()
    runtime = AppV21AgentRuntime(
        root_path=tmp_path,
        services=create_appv21_runtime_services(root_path=tmp_path, provider=provider),
        max_turns=1,
    )

    with pytest.raises(RuntimeError, match="rate limit exceeded"):
        runtime.run("Summarize without changes.")

    event_types = [event["event_type"] for event in runtime.store.to_dicts()]
    assert provider.calls == 1
    assert "ContextOverflowDetected" not in event_types
    assert "ContextOverflowRecoveryFailed" not in event_types


def test_context_overflow_forces_compaction_before_recovery_failure(tmp_path: Path) -> None:
    class OverflowWithoutCompactionProvider:
        provider_id = "overflow-without-compaction"

        def __init__(self) -> None:
            self.calls = 0

        def decide(self, _prompt_payload: dict) -> RuntimeDecision:
            self.calls += 1
            raise RuntimeError("request too large")

    provider = OverflowWithoutCompactionProvider()
    runtime = AppV21AgentRuntime(
        root_path=tmp_path,
        services=create_appv21_runtime_services(root_path=tmp_path, provider=provider),
        max_turns=1,
    )

    with pytest.raises(RuntimeError, match="request too large"):
        runtime.run("Summarize without changes.")

    event_types = [event["event_type"] for event in runtime.store.to_dicts()]
    recovery_failure = next(event for event in runtime.store.to_dicts() if event["event_type"] == "ContextOverflowRecoveryFailed")
    assert provider.calls == 2
    assert event_types.count("ContextOverflowDetected") == 1
    assert event_types.count("ContextCompactionRequested") == 1
    compaction = next(event for event in runtime.store.to_dicts() if event["event_type"] == "ContextCompactionRequested")
    assert compaction["payload"]["reason"] == "context_overflow_forced"
    assert event_types.count("ContextOverflowRecoveryFailed") == 1
    assert recovery_failure["payload"]["reason"] == "retry_overflow"


def test_finalize_emits_runtime_verified_run_memory(tmp_path: Path) -> None:
    class ExplicitNoopFinalizeProvider:
        provider_id = "explicit-noop-finalize"

        def decide(self, prompt_payload: dict) -> RuntimeDecision:
            world = prompt_payload.get("world", {})
            if not any(ref.get("kind") == "repo_snapshot" for ref in world.get("world_refs", [])):
                return RuntimeDecision(kind="observe", reason="Observe before explicit noop finalize.")
            return RuntimeDecision(
                kind="finalize",
                reason="No mutation is needed.",
                payload={"explicit_noop": True},
            )

    result = AppV21AgentRuntime(
        root_path=tmp_path,
        services=create_appv21_runtime_services(root_path=tmp_path, provider=ExplicitNoopFinalizeProvider()),
        max_turns=2,
    ).run("Summarize without changing files.")

    artifact_events = [event for event in result["events"] if event["event_type"] == "ArtifactAccepted"]
    artifact_ids = [event["payload"]["artifact_id"] for event in artifact_events]

    assert result["status"] == "completed"
    assert artifact_ids[:2] == ["run_memory", "final_summary"]

    run_memory = artifact_events[0]["payload"]
    assert run_memory["kind"] == "context_summary"
    assert run_memory["producer"] == "appv21_runtime"
    assert run_memory["trust"] == "runtime_verified"
    assert run_memory["lifecycle"] == "runtime_verified"
    assert run_memory["evidence_refs"] == result["verification_receipts"]

    content = run_memory["content"]
    assert content["goal"] == "Summarize without changing files."
    assert content["outcome"] == "completed"
    assert content["mutation_receipts"] == []
    assert content["verification_receipts"] == result["verification_receipts"]
    assert content["event_counts"]["VerificationRecorded"] == 1
    assert content["decision_counts"]["finalize"] == 1
    assert content["tools_used"] == ["repo_snapshot"]
    assert content["open_risks"] == []


def test_run_memory_scopes_events_to_current_run_when_runtime_reused(tmp_path: Path) -> None:
    (tmp_path / "first.txt").write_text("first run only", encoding="utf-8")

    class ReusedRuntimeProvider:
        provider_id = "reused-runtime"

        def __init__(self) -> None:
            self.calls = 0

        def decide(self, _prompt_payload: dict) -> RuntimeDecision:
            self.calls += 1
            if self.calls == 1:
                return RuntimeDecision(kind="observe", reason="Observe first run.")
            if self.calls == 2:
                return RuntimeDecision(kind="read_file", reason="Read first-run-only file.", payload={"path": "first.txt"})
            if self.calls == 3:
                return RuntimeDecision(kind="finalize", reason="Finalize first run.", payload={"explicit_noop": True})
            if self.calls == 4:
                return RuntimeDecision(kind="observe", reason="Observe second run.")
            return RuntimeDecision(kind="finalize", reason="Finalize second run.", payload={"explicit_noop": True})

    runtime = AppV21AgentRuntime(
        root_path=tmp_path,
        services=create_appv21_runtime_services(root_path=tmp_path, provider=ReusedRuntimeProvider()),
        max_turns=3,
    )

    first_result = runtime.run("First run reads a file.")
    second_result = runtime.run("Second run only observes.")

    assert first_result["status"] == "completed"
    assert second_result["status"] == "completed"

    run_memory_events = [
        event
        for event in second_result["events"]
        if event["event_type"] == "ArtifactAccepted" and event["payload"]["artifact_id"] == "run_memory"
    ]
    second_run_memory = run_memory_events[-1]["payload"]["content"]

    assert second_run_memory["goal"] == "Second run only observes."
    assert second_run_memory["decision_counts"] == {"finalize": 1, "observe": 1}
    assert second_run_memory["event_counts"]["UserMessageReceived"] == 1
    assert second_run_memory["event_counts"]["DecisionProposed"] == 2
    assert second_run_memory["tools_used"] == ["repo_snapshot"]


def test_run_memory_uses_current_run_events_when_reused_runtime_interleaves_paused_run(tmp_path: Path) -> None:
    (tmp_path / "second.txt").write_text("second run only", encoding="utf-8")

    class InterleavedRuntimeProvider:
        provider_id = "interleaved-runtime"

        def __init__(self) -> None:
            self.calls = 0

        def decide(self, _prompt_payload: dict) -> RuntimeDecision:
            self.calls += 1
            if self.calls == 1:
                return RuntimeDecision(kind="pause", reason="Pause first run before completion.")
            if self.calls == 2:
                return RuntimeDecision(kind="read_file", reason="Read second-run-only file.", payload={"path": "second.txt"})
            if self.calls == 3:
                return RuntimeDecision(kind="finalize", reason="Finalize second run.", payload={"explicit_noop": True})
            return RuntimeDecision(kind="finalize", reason="Resume and finalize first run.", payload={"explicit_noop": True})

    runtime = AppV21AgentRuntime(
        root_path=tmp_path,
        services=create_appv21_runtime_services(root_path=tmp_path, provider=InterleavedRuntimeProvider()),
        max_turns=2,
    )

    paused_first_result = runtime.run("First run pauses before completion.")
    second_result = runtime.run("Second run reads a file.")
    resumed_first_result = runtime.resume(paused_first_result["pause_id"], {"acknowledged": True})

    assert paused_first_result["status"] == "paused"
    assert second_result["status"] == "completed"
    assert resumed_first_result["status"] == "completed"

    run_memory_events = [
        event
        for event in resumed_first_result["events"]
        if event["event_type"] == "ArtifactAccepted" and event["payload"]["artifact_id"] == "run_memory"
    ]
    first_run_memory = run_memory_events[-1]["payload"]["content"]

    assert first_run_memory["goal"] == "First run pauses before completion."
    assert first_run_memory["decision_counts"] == {"finalize": 1, "pause": 1}
    assert first_run_memory["event_counts"]["UserMessageReceived"] == 1
    assert first_run_memory["event_counts"]["DecisionProposed"] == 2
    assert first_run_memory["event_counts"].get("ToolCallCompleted", 0) == 0
    assert first_run_memory["tools_used"] == []


def test_run_memory_evidence_refs_include_mutation_and_verification_receipts(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text("move me", encoding="utf-8")

    result = AppV21AgentRuntime(
        root_path=tmp_path,
        services=create_appv21_runtime_services(root_path=tmp_path),
    ).run("Organize this workspace safely.")

    artifact_events = [event for event in result["events"] if event["event_type"] == "ArtifactAccepted"]
    run_memory = next(event["payload"] for event in artifact_events if event["payload"]["artifact_id"] == "run_memory")

    assert result["status"] == "completed"
    assert run_memory["content"]["mutation_receipts"] == result["mutation_receipts"]
    assert run_memory["content"]["verification_receipts"] == result["verification_receipts"]
    assert run_memory["evidence_refs"] == result["mutation_receipts"] + result["verification_receipts"]
