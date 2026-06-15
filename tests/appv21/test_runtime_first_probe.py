from __future__ import annotations

import sys
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "appV2.1"))

from appv21 import AppV21AgentRuntime
from appv21.context.prompt_builder import PromptBuilder
from appv21.extensions.verifier import VerifierExtension
from appv21.providers.base import AgentProvider
from appv21.runtime.decisions import RuntimeDecision
from appv21.runtime.services import create_appv21_runtime_services
from appv21.state.events import RuntimeEvent
from appv21.state.models import AgentState, MutationLease, RequestEnvelope
from appv21.tools.broker import ToolBroker


def test_appv21_runtime_observes_plans_mutates_and_verifies(tmp_path: Path) -> None:
    (tmp_path / "notes/drafts").mkdir(parents=True)
    (tmp_path / "notes/drafts/task_notes.md").write_text("Task notes\n", encoding="utf-8")
    (tmp_path / "artifacts/tmp").mkdir(parents=True)
    (tmp_path / "artifacts/tmp/old_build.log").write_text("build\n", encoding="utf-8")
    (tmp_path / "notes/raw").mkdir(parents=True)
    (tmp_path / "notes/raw/old_blob.txt").write_text("keep\n", encoding="utf-8")

    result = AppV21AgentRuntime(root_path=tmp_path).run("Clean up this workspace.")

    assert result["status"] == "completed"
    assert (tmp_path / "docs/task_notes.md").is_file()
    assert (tmp_path / "artifacts/logs/old_build.log").is_file()
    assert (tmp_path / "notes/raw/old_blob.txt").is_file()
    assert (tmp_path / "docs/workspace_manifest.json").is_file()
    event_types = [event["event_type"] for event in result["events"]]
    assert event_types.index("WorldRefAdded") < event_types.index("PlanAccepted")
    assert event_types.index("DecisionProposed") < event_types.index("WorldRefAdded")
    assert event_types.index("PromptContextPrepared") < event_types.index("PlanAccepted")
    assert "MutationLeaseIssued" in event_types
    assert "VerificationRecorded" in event_types
    assert "ExtensionTraceRecorded" in event_types
    assert event_types[-1] == "RunCompleted"


def test_appv21_broker_denies_unissued_or_tampered_lease(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("a\n", encoding="utf-8")
    broker = ToolBroker(root_path=tmp_path)

    forged = MutationLease(
        lease_id="lease_forged",
        operation_batch_id="forged",
        allowed_operations=[{"action": "move", "source": "a.md", "destination": "docs/a.md"}],
        allowed_sources=["a.md"],
        allowed_destinations=["docs/a.md"],
    )
    forged_receipt = broker.apply_mutation_lease(forged)

    assert forged_receipt.status == "denied"
    assert "lease_not_issued" in forged_receipt.errors
    assert (tmp_path / "a.md").is_file()

    lease = broker.derive_mutation_lease(
        operation_batch_id="move_a",
        operations=[{"action": "move", "source": "a.md", "destination": "docs/a.md"}],
    )
    tampered = MutationLease(
        lease_id=lease.lease_id,
        operation_batch_id=lease.operation_batch_id,
        allowed_operations=[{"action": "move", "source": "a.md", "destination": "secret/a.md"}],
        allowed_sources=lease.allowed_sources,
        allowed_destinations=["secret/a.md"],
    )
    tampered_receipt = broker.apply_mutation_lease(tampered)

    assert tampered_receipt.status == "denied"
    assert any(error.startswith("operation_not_in_lease") for error in tampered_receipt.errors)
    assert (tmp_path / "a.md").is_file()


def test_appv21_move_requires_source_even_when_destination_exists(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("existing\n", encoding="utf-8")
    broker = ToolBroker(root_path=tmp_path)
    lease = broker.derive_mutation_lease(
        operation_batch_id="move_missing",
        operations=[{"action": "move", "source": "a.md", "destination": "docs/a.md"}],
    )

    receipt = broker.apply_mutation_lease(lease)

    assert receipt.status == "failed"
    assert "missing source: a.md" in receipt.errors


def test_appv21_verifier_rejects_stale_manifest_contents(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("a\n", encoding="utf-8")
    (tmp_path / "docs" / "workspace_manifest.json").write_text(
        json.dumps({"moves": [{"source": "wrong.md", "destination": "docs/wrong.md"}], "held": []}),
        encoding="utf-8",
    )

    result = VerifierExtension().verify(
        root_path=tmp_path,
        verification_intent={"manifest_path": "docs/workspace_manifest.json", "moves": [{"source": "a.md", "destination": "docs/a.md"}], "held": []},
    )

    assert result["status"] == "failed"
    checks = {check["name"]: check["passed"] for check in result["checks"]}
    assert checks["manifest_moves_match_intent"] is False


def test_appv21_verifier_rejects_manifest_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside_manifest.json"
    outside.write_text(json.dumps({"moves": [], "held": []}), encoding="utf-8")

    result = VerifierExtension().verify(
        root_path=tmp_path,
        verification_intent={"manifest_path": "../outside_manifest.json", "moves": [], "held": []},
    )

    assert result["status"] == "failed"
    assert result["checks"][0] == {"name": "manifest_path_inside_root", "passed": False}


def test_appv21_planner_holds_destination_collisions(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "notes.md").write_text("a\n", encoding="utf-8")
    (tmp_path / "b" / "notes.md").write_text("b\n", encoding="utf-8")

    result = AppV21AgentRuntime(root_path=tmp_path).run("Clean up workspace.")

    assert result["status"] == "completed"
    assert (tmp_path / "docs" / "notes.md").is_file()
    assert (tmp_path / "a" / "notes.md").is_file() != (tmp_path / "b" / "notes.md").is_file()


def test_appv21_services_persist_terminal_events_and_dead_letter_subscribers(tmp_path: Path) -> None:
    (tmp_path / "note.md").write_text("note\n", encoding="utf-8")
    session_path = tmp_path / ".appv21-test" / "session.jsonl"
    services = create_appv21_runtime_services(root_path=tmp_path, session_path=session_path)

    def broken_handler(_event: RuntimeEvent) -> None:
        raise RuntimeError("subscriber failed")

    services.event_bus.subscribe("RunCompleted", broken_handler)
    result = AppV21AgentRuntime(root_path=tmp_path, services=services).run("Clean up workspace.")

    assert result["status"] == "completed"
    persisted = services.session_store.read_all()
    assert persisted[-1]["event_type"] == "RunCompleted"
    assert services.event_bus.dead_letters()[0]["event_type"] == "RunCompleted"


def test_appv21_extension_runner_is_advisory_and_failure_is_event(tmp_path: Path) -> None:
    class BrokenExtension:
        extension_id = "broken"
        capabilities = {"before_plan"}

        def handle(self, hook: str, state: AgentState, payload: dict[str, Any]) -> list[RuntimeEvent]:
            raise RuntimeError(f"bad hook {hook}")

    (tmp_path / "a.md").write_text("a\n", encoding="utf-8")
    services = create_appv21_runtime_services(root_path=tmp_path, enable_trace_extension=False)
    services.extension_runner.extensions.append(BrokenExtension())
    result = AppV21AgentRuntime(root_path=tmp_path, services=services).run("Clean up workspace.")

    assert result["status"] == "completed"
    extension_failures = [event for event in result["events"] if event["event_type"] == "ExtensionFailed"]
    assert extension_failures[0]["payload"]["extension_id"] == "broken"


def test_appv21_prompt_builder_exposes_runtime_contract(tmp_path: Path) -> None:
    request = RequestEnvelope(request_id="req", user_goal="Clean up workspace.", root_path=str(tmp_path))
    state = AgentState(session_id="sess", run_id="run", request=request)
    broker = ToolBroker(root_path=tmp_path)
    payload = PromptBuilder().build(
        state=state,
        turn_context={"world_refs": []},
        active_skills=[{"skill_id": "workspace_cleanup"}],
        tool_specs=broker.tool_specs(),
    )

    assert payload["system"]["identity"] == "AppV2.1 runtime-first coding agent"
    assert payload["output_contract"]["write_boundary"] == "MutationLease"
    assert {tool["name"] for tool in payload["tools"]} == {"repo_snapshot", "read_file"}


def test_matrix_probe_reports_include_top_level_counts(tmp_path: Path) -> None:
    from scripts.live_appv21_file_management_matrix_probe import _build_report

    report = _build_report(
        repo=tmp_path,
        result={
            "status": "completed",
            "events": [
                RuntimeEvent("DecisionProposed", {"kind": "observe"}).to_dict(),
                RuntimeEvent("ToolCallDenied", {"tool_name": "read_file", "status": "denied"}).to_dict(),
                RuntimeEvent("RunPaused", {"pause_id": "pause"}).to_dict(),
                RuntimeEvent("ContextCompacted", {}).to_dict(),
            ],
        },
        provider=None,
        max_turns=1,
    )

    assert report["decision_count"] == 1
    assert report["tool_count"] == 1
    assert report["denied_count"] == 1
    assert report["pause_count"] == 1
    assert report["compaction_count"] == 1


def test_appv21_runtime_rejects_missing_decision_evidence(tmp_path: Path) -> None:
    class BadEvidenceProvider:
        provider_id = "bad-evidence"
        observed = False

        def decide(self, prompt_payload: dict) -> RuntimeDecision:
            if not self.observed:
                self.observed = True
                return RuntimeDecision(kind="observe", reason="observe first")
            return RuntimeDecision(kind="plan", reason="bad evidence", evidence_refs=["world://missing"])

    services = create_appv21_runtime_services(root_path=tmp_path, provider=BadEvidenceProvider())
    result = AppV21AgentRuntime(root_path=tmp_path, services=services, max_turns=4).run("Clean up workspace.")

    assert result["status"] == "failed"
    assert result["reason"] == "repeated_rejected_decision"
    assert [event["event_type"] for event in result["events"]].count("DecisionRejected") == 3
    rejected_indexes = [index for index, event in enumerate(result["events"]) if event["event_type"] == "DecisionRejected"]
    for index in rejected_indexes:
        previous_mode = next(
            event["payload"]["mode"]
            for event in reversed(result["events"][:index])
            if event["event_type"] == "ModeChanged"
        )
        assert previous_mode == "OBSERVE"
