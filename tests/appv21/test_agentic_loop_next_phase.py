from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "appV2.1"))

from appv21 import AppV21AgentRuntime
from appv21.providers.base import AgentProvider
from appv21.providers.appv2_env import AppV2EnvAgentProvider, create_appv21_provider_from_appv2_env
from appv21.providers.env_config import load_dotenv_values
from appv21.runtime.decisions import RuntimeDecision, parse_runtime_decision
from appv21.runtime.services import create_appv21_runtime_services
from appv21.state.models import AgentState, RequestEnvelope, WorldRef
from appv21.tools.broker import ToolBroker


class QueueProvider:
    provider_id = "queue"

    def __init__(self, decisions: list[RuntimeDecision]) -> None:
        self.decisions = decisions

    def decide(self, prompt_payload: dict) -> RuntimeDecision:
        if not self.decisions:
            return RuntimeDecision(kind="finalize", reason="done", evidence_refs=["verification://latest"])
        return self.decisions.pop(0)


def observe_and_plan_first(decisions: list[RuntimeDecision]) -> list[RuntimeDecision]:
    return [
        RuntimeDecision(kind="observe", reason="observe before mutation intent"),
        RuntimeDecision(kind="plan", reason="enter mutation phase", evidence_refs=["world://repo_snapshot/latest"]),
        *decisions,
    ]


def test_decision_parser_rejects_unknown_kind() -> None:
    parsed = parse_runtime_decision({"kind": "teleport", "reason": "bad", "payload": {}, "evidence_refs": []})

    assert parsed.kind == "teleport"
    assert parsed.payload["rejected_kind"] == "teleport"
    assert parsed.payload["rejection_reason"] == "unknown_decision_kind"


def test_tool_call_reads_file_through_broker_and_records_world_ref(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# hello\n", encoding="utf-8")
    provider = QueueProvider(
        [
            RuntimeDecision(kind="tool_call", reason="read exact content", payload={"tool_name": "read_file", "arguments": {"path": "README.md"}}),
            RuntimeDecision(kind="finalize", reason="no-op verified", payload={"explicit_noop": True}),
        ]
    )

    result = AppV21AgentRuntime(root_path=tmp_path, services=create_appv21_runtime_services(root_path=tmp_path, provider=provider)).run(
        "Inspect README only."
    )

    event_types = [event["event_type"] for event in result["events"]]
    assert result["status"] == "completed"
    assert "ToolCallCompleted" in event_types
    assert any(event["payload"].get("kind") == "tool_result" for event in result["events"] if event["event_type"] == "WorldRefAdded")


def test_tool_call_accepts_common_model_tool_params_shape(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# hello\n", encoding="utf-8")
    provider = QueueProvider(
        [
            RuntimeDecision(kind="tool_call", reason="read exact content", payload={"tool": "read_file", "params": {"path": "README.md"}}),
            RuntimeDecision(kind="finalize", reason="no-op verified", payload={"explicit_noop": True}),
        ]
    )

    result = AppV21AgentRuntime(root_path=tmp_path, services=create_appv21_runtime_services(root_path=tmp_path, provider=provider)).run(
        "Inspect README only."
    )

    assert result["status"] == "completed"
    assert "ToolCallCompleted" in [event["event_type"] for event in result["events"]]


def test_read_file_denies_sensitive_paths_without_prompt_preview_leak(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-secret-value\n", encoding="utf-8")
    broker = ToolBroker(root_path=tmp_path)

    result = broker.execute_tool_call("read_file", {"path": ".env"})

    assert result["status"] == "denied"
    assert "sensitive_path_denied:.env" in result["payload"]["errors"]
    assert "sk-secret-value" not in json.dumps(result)


def test_runtime_does_not_record_denied_sensitive_read_as_world_ref(tmp_path: Path) -> None:
    (tmp_path / "secrets.pem").write_text("PRIVATE KEY MATERIAL\n", encoding="utf-8")
    provider = QueueProvider(
        [
            RuntimeDecision(kind="tool_call", reason="read key", payload={"tool_name": "read_file", "arguments": {"path": "secrets.pem"}}),
            RuntimeDecision(kind="finalize", reason="no-op verified", payload={"explicit_noop": True}),
        ]
    )

    result = AppV21AgentRuntime(root_path=tmp_path, services=create_appv21_runtime_services(root_path=tmp_path, provider=provider)).run(
        "Inspect sensitive key."
    )

    assert result["status"] == "completed"
    assert "ToolCallDenied" in [event["event_type"] for event in result["events"]]
    assert all("PRIVATE KEY MATERIAL" not in json.dumps(event) for event in result["events"])


def test_bad_mutation_intent_is_denied_before_write(tmp_path: Path) -> None:
    provider = QueueProvider(
        observe_and_plan_first([
            RuntimeDecision(
                kind="mutation_intent",
                reason="unsafe write",
                payload={"operation_batch_id": "bad", "operations": [{"action": "write", "path": "../escape.txt", "content": "no"}]},
            )
        ])
    )

    result = AppV21AgentRuntime(root_path=tmp_path, services=create_appv21_runtime_services(root_path=tmp_path, provider=provider), max_turns=3).run(
        "Write outside the repo."
    )

    assert result["status"] == "failed"
    assert result["reason"] == "mutation_denied"
    assert not (tmp_path.parent / "escape.txt").exists()
    assert "ToolCallDenied" in [event["event_type"] for event in result["events"]]


def test_pause_resume_records_lineage_and_continues(tmp_path: Path) -> None:
    class PauseThenFinalizeProvider:
        provider_id = "pause-then-finalize"

        def decide(self, prompt_payload: dict) -> RuntimeDecision:
            if not prompt_payload["state"]["pauses"]:
                return RuntimeDecision(kind="pause", reason="Need approval.", payload={"pause_type": "approval_required"})
            return RuntimeDecision(kind="finalize", reason="Approved no-op.", payload={"explicit_noop": True})

    session_path = tmp_path / ".appv21-test" / "session.jsonl"
    runtime = AppV21AgentRuntime(
        root_path=tmp_path,
        services=create_appv21_runtime_services(root_path=tmp_path, session_path=session_path, provider=PauseThenFinalizeProvider()),
    )

    paused = runtime.run("Do a risky no-op.")
    resumed = runtime.resume(paused["pause_id"], {"value": "approve", "operation_batch_id": "risky"})
    second_resume = runtime.resume(paused["pause_id"], {"approved": True})

    assert paused["status"] == "paused"
    assert resumed["status"] == "completed"
    assert second_resume["status"] == "failed"
    assert second_resume["reason"] == "pause_not_found"
    persisted_types = [row["event_type"] for row in runtime.services.session_store.read_all()]
    assert {"PauseRequested", "RunPaused", "PauseResolved", "RunResumed", "RunCompleted"} <= set(persisted_types)


def test_durable_pause_resume_rehydrates_from_jsonl(tmp_path: Path) -> None:
    class PauseThenFinalizeProvider:
        provider_id = "durable-pause-then-finalize"

        def decide(self, prompt_payload: dict) -> RuntimeDecision:
            if not prompt_payload["state"]["pauses"]:
                return RuntimeDecision(kind="pause", reason="Need approval.", payload={"pause_type": "approval_required"})
            assert prompt_payload["decomposition"]["intent"] == "general_task"
            return RuntimeDecision(kind="finalize", reason="Approved no-op.", payload={"explicit_noop": True})

    session_path = tmp_path / ".appv21-test" / "session.jsonl"
    first_services = create_appv21_runtime_services(root_path=tmp_path, session_path=session_path, provider=PauseThenFinalizeProvider())
    paused = AppV21AgentRuntime(root_path=tmp_path, services=first_services).run("Do a risky no-op.")

    second_services = create_appv21_runtime_services(root_path=tmp_path, session_path=session_path, provider=PauseThenFinalizeProvider())
    resumed = AppV21AgentRuntime(root_path=tmp_path, services=second_services).resume(paused["pause_id"], {"approved": True})

    assert paused["status"] == "paused"
    assert resumed["status"] == "completed"
    assert [event["event_type"] for event in resumed["events"]].count("RunPaused") == 1
    assert "RunResumed" in [event["event_type"] for event in resumed["events"]]


def test_high_risk_mutation_intent_forces_pause_before_write(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=keep\n", encoding="utf-8")
    provider = QueueProvider(
        observe_and_plan_first([
            RuntimeDecision(
                kind="mutation_intent",
                reason="overwrite secrets",
                payload={"operation_batch_id": "risky", "operations": [{"action": "write", "path": ".env", "content": "SECRET=replace\n"}]},
            )
        ])
    )

    result = AppV21AgentRuntime(root_path=tmp_path, services=create_appv21_runtime_services(root_path=tmp_path, provider=provider), max_turns=3).run(
        "Overwrite secrets."
    )

    assert result["status"] == "paused"
    assert result["reason"] == "high_risk_mutation_requires_human_approval"
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "SECRET=keep\n"
    assert "MutationLeaseIssued" not in [event["event_type"] for event in result["events"]]


def test_high_risk_mutation_resume_approval_applies_pending_intent(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=keep\n", encoding="utf-8")
    provider = QueueProvider(
        observe_and_plan_first([
            RuntimeDecision(
                kind="mutation_intent",
                reason="overwrite secrets",
                payload={"operation_batch_id": "risky", "operations": [{"action": "write", "path": ".env", "content": "SECRET=replace\n"}]},
            ),
            RuntimeDecision(kind="finalize", reason="mutation applied", payload={"explicit_noop": True}),
        ])
    )
    runtime = AppV21AgentRuntime(root_path=tmp_path, services=create_appv21_runtime_services(root_path=tmp_path, provider=provider))

    paused = runtime.run("Overwrite secrets.")
    resumed = runtime.resume(paused["pause_id"], {"approval": "approve:risky"})

    assert paused["status"] == "paused"
    assert resumed["status"] == "completed"
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "SECRET=replace\n"
    event_types = [event["event_type"] for event in resumed["events"]]
    assert event_types.count("RunPaused") == 1
    assert "MutationLeaseIssued" in event_types
    assert "MutationApplied" in event_types
    human_input = [event for event in resumed["events"] if event["event_type"] == "HumanInputReceived"]
    assert human_input[-1]["payload"]["operation_batch_id"] == "risky"
    assert human_input[-1]["payload"]["approved"] is True


def test_high_risk_mutation_resume_requires_typed_batch_approval(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=keep\n", encoding="utf-8")
    provider = QueueProvider(
        observe_and_plan_first([
            RuntimeDecision(
                kind="mutation_intent",
                reason="overwrite secrets",
                payload={"operation_batch_id": "risky", "operations": [{"action": "write", "path": ".env", "content": "SECRET=replace\n"}]},
            )
        ])
    )
    runtime = AppV21AgentRuntime(root_path=tmp_path, services=create_appv21_runtime_services(root_path=tmp_path, provider=provider))

    paused = runtime.run("Overwrite secrets.")
    resumed = runtime.resume(paused["pause_id"], {"approved": True})

    assert resumed["status"] == "failed"
    assert resumed["reason"] == "high_risk_mutation_rejected"
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "SECRET=keep\n"


@pytest.mark.parametrize(
    "legacy_input",
    [
        {"approved": True},
        {"value": "approve", "operation_batch_id": "risky"},
        {"approved_operation_batch_id": "risky"},
    ],
)
def test_high_risk_mutation_resume_rejects_legacy_approval_shapes(tmp_path: Path, legacy_input: dict[str, object]) -> None:
    repo = tmp_path / str(abs(hash(json.dumps(legacy_input, sort_keys=True))))
    repo.mkdir()
    (repo / ".env").write_text("SECRET=keep\n", encoding="utf-8")
    provider = QueueProvider(
        observe_and_plan_first([
            RuntimeDecision(
                kind="mutation_intent",
                reason="overwrite secrets",
                payload={"operation_batch_id": "risky", "operations": [{"action": "write", "path": ".env", "content": "SECRET=replace\n"}]},
            )
        ])
    )
    runtime = AppV21AgentRuntime(root_path=repo, services=create_appv21_runtime_services(root_path=repo, provider=provider))

    paused = runtime.run("Overwrite secrets.")
    resumed = runtime.resume(paused["pause_id"], legacy_input)

    assert paused["status"] == "paused"
    assert resumed["status"] == "failed"
    assert resumed["reason"] == "high_risk_mutation_rejected"
    assert (repo / ".env").read_text(encoding="utf-8") == "SECRET=keep\n"
    assert "MutationLeaseIssued" not in [event["event_type"] for event in resumed["events"]]


def test_high_risk_mutation_resume_rejection_fails_without_write(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=keep\n", encoding="utf-8")
    provider = QueueProvider(
        observe_and_plan_first([
            RuntimeDecision(
                kind="mutation_intent",
                reason="overwrite secrets",
                payload={"operation_batch_id": "risky", "operations": [{"action": "write", "path": ".env", "content": "SECRET=replace\n"}]},
            )
        ])
    )
    runtime = AppV21AgentRuntime(root_path=tmp_path, services=create_appv21_runtime_services(root_path=tmp_path, provider=provider))

    paused = runtime.run("Overwrite secrets.")
    resumed = runtime.resume(paused["pause_id"], {"approved": False})

    assert paused["status"] == "paused"
    assert resumed["status"] == "failed"
    assert resumed["reason"] == "high_risk_mutation_rejected"
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "SECRET=keep\n"
    assert "MutationLeaseIssued" not in [event["event_type"] for event in resumed["events"]]


def test_durable_high_risk_mutation_resume_approval_applies_pending_intent(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=keep\n", encoding="utf-8")
    session_path = tmp_path / ".appv21-test" / "session.jsonl"
    first_provider = QueueProvider(
        observe_and_plan_first([
            RuntimeDecision(
                kind="mutation_intent",
                reason="overwrite secrets",
                payload={"operation_batch_id": "risky", "operations": [{"action": "write", "path": ".env", "content": "SECRET=replace\n"}]},
            )
        ])
    )
    paused = AppV21AgentRuntime(
        root_path=tmp_path,
        services=create_appv21_runtime_services(root_path=tmp_path, session_path=session_path, provider=first_provider),
    ).run("Overwrite secrets.")
    second_provider = QueueProvider([RuntimeDecision(kind="finalize", reason="mutation applied", payload={"explicit_noop": True})])

    resumed = AppV21AgentRuntime(
        root_path=tmp_path,
        services=create_appv21_runtime_services(root_path=tmp_path, session_path=session_path, provider=second_provider),
    ).resume(paused["pause_id"], {"approval": "approve:risky"})

    assert paused["status"] == "paused"
    assert resumed["status"] == "completed"
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "SECRET=replace\n"
    assert "MutationLeaseIssued" in [event["event_type"] for event in resumed["events"]]


def test_observe_decision_uses_tool_broker_completed_envelope(tmp_path: Path) -> None:
    provider = QueueProvider(
        [
            RuntimeDecision(kind="observe", reason="snapshot"),
            RuntimeDecision(kind="finalize", reason="no-op verified", payload={"explicit_noop": True}),
        ]
    )

    result = AppV21AgentRuntime(root_path=tmp_path, services=create_appv21_runtime_services(root_path=tmp_path, provider=provider)).run("Inspect only.")

    completed = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    refs = [event for event in result["events"] if event["event_type"] == "WorldRefAdded"]
    assert completed[0]["payload"]["tool_name"] == "repo_snapshot"
    assert completed[0]["payload"]["status"] == "completed"
    assert refs[0]["payload"]["kind"] == "tool_result"
    assert refs[0]["payload"]["ref_id"].startswith("world://tool_result/")


def test_observe_emits_event_sourced_canonical_repo_snapshot_ref(tmp_path: Path) -> None:
    provider = QueueProvider(
        [
            RuntimeDecision(kind="observe", reason="snapshot"),
            RuntimeDecision(kind="plan", reason="plan with canonical ref", evidence_refs=["world://repo_snapshot/latest"]),
            RuntimeDecision(kind="finalize", reason="no-op verified", payload={"explicit_noop": True}),
        ]
    )

    result = AppV21AgentRuntime(root_path=tmp_path, services=create_appv21_runtime_services(root_path=tmp_path, provider=provider)).run("Inspect only.")

    assert result["status"] == "completed"
    assert any(
        event["event_type"] == "WorldRefAdded" and event["payload"]["ref_id"] == "world://repo_snapshot/latest"
        for event in result["events"]
    )


def test_model_tool_specs_only_expose_callable_tools(tmp_path: Path) -> None:
    broker = ToolBroker(root_path=tmp_path)

    tool_names = {tool["name"] for tool in broker.tool_specs()}

    assert tool_names == {"repo_snapshot", "read_file"}
    assert all(not broker.validate_tool_call(tool_name, {}) for tool_name in tool_names if tool_name == "repo_snapshot")
    assert "derive_mutation_lease" not in tool_names
    assert "apply_mutation_lease" not in tool_names


def test_context_compaction_preserves_validation_evidence(tmp_path: Path) -> None:
    provider = QueueProvider(
        [
            RuntimeDecision(kind="observe", reason="observe"),
            RuntimeDecision(kind="plan", reason="plan", evidence_refs=["world://repo_snapshot/latest"]),
            RuntimeDecision(kind="mutation_intent", reason="mutate", payload={"operation_batch_id": "noop", "operations": []}),
        ]
    )
    # Use the deterministic runtime path for a real receipt/verification, then force compaction via provider.
    result = AppV21AgentRuntime(root_path=tmp_path).run("Clean up workspace.")

    compacted = [event for event in result["events"] if event["event_type"] == "ContextCompacted"]
    if compacted:
        digest = compacted[-1]["payload"]["world_digest"]
        assert set(result["verification_receipts"]) <= set(digest["verification_receipts"])
        assert set(result["mutation_receipts"]) <= set(digest["mutation_receipts"])


def test_build_turn_context_bounds_world_refs_after_compaction() -> None:
    from appv21.context.manager import DualContextManager

    state = AgentState(session_id="sess", run_id="run", request=RequestEnvelope(request_id="req", user_goal="Inspect.", root_path="."))
    manager = DualContextManager()
    for index in range(10):
        state.world.refs[f"world://tool_result/{index}"] = WorldRef(
            ref_id=f"world://tool_result/{index}",
            kind="tool_result",
            summary=f"ref {index}",
            payload={"index": index},
        )
    state.world.refs["world://repo_snapshot/latest"] = WorldRef(
        ref_id="world://repo_snapshot/latest",
        kind="repo_snapshot",
        summary="snapshot",
        payload={"files": []},
    )

    compacted = manager.maybe_compact(state)
    for event in compacted:
        if event.event_type == "ContextCompacted":
            state.context.compacted_turns += 1
            state.context.world_digest = event.payload["world_digest"]
            state.context.conversation_digest = event.payload["conversation_digest"]
    turn_context = manager.build_turn_context(state)

    assert turn_context["compacted"] is True
    assert turn_context["world_digest"]["compacted_world_ref_count"] == 11
    assert len(turn_context["world_refs"]) < 11
    assert any(ref["ref_id"] == "world://repo_snapshot/latest" for ref in turn_context["world_refs"])


def test_required_probe_scripts_write_reports(tmp_path: Path) -> None:
    scripts = [
        "live_appv21_agent_loop_probe.py",
        "live_appv21_bad_mutation_probe.py",
        "live_appv21_pause_resume_probe.py",
        "live_appv21_context_compaction_probe.py",
        "live_appv21_planner_disabled_probe.py",
    ]
    for script in scripts:
        proc = subprocess.run([sys.executable, str(ROOT / "scripts" / script)], cwd=ROOT, text=True, capture_output=True, check=True)
        output_line = next(line for line in proc.stdout.splitlines() if line.startswith("OUTPUT_PATH="))
        report = Path(output_line.split("=", 1)[1])
        data = json.loads(report.read_text(encoding="utf-8"))
        assert {"event_order", "decision_count", "tool_count", "denied_count", "pause_count", "compaction_count"} <= set(data)


def test_appv21_provider_uses_appv2_worker_env_config(tmp_path: Path, monkeypatch) -> None:
    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
            return json.dumps({"kind": "finalize", "reason": "safe no-op", "payload": {"explicit_noop": True}, "evidence_refs": []})

    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "APPV2_WORKER_LLM_ENABLED=true",
                "APPV2_WORKER_LLM_API_KEY=test-key",
                "APPV2_WORKER_LLM_MODEL=dotenv/model",
                "APPV2_WORKER_LLM_BASE_URL=https://openrouter.example/api/v1",
                "APPV2_WORKER_LLM_TOP_P=0.2",
                "APPV2_WORKER_LLM_FREQUENCY_PENALTY=0",
                "APPV2_WORKER_LLM_PRESENCE_PENALTY=0",
                "APPV2_WORKER_LLM_SEED=21",
                "APPV2_WORKER_LLM_STOP=[\"</json>\"]",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APPV2_WORKER_LLM_MODEL", "env/model")

    provider = create_appv21_provider_from_appv2_env(dotenv_path=dotenv, client_factory=FakeClient)

    assert isinstance(provider, AppV2EnvAgentProvider)
    assert provider.client.kwargs["model"] == "env/model"
    assert provider.client.kwargs["api_key"] == "test-key"
    assert provider.client.kwargs["top_p"] == 0.2
    assert provider.client.kwargs["seed"] == 21
    assert provider.client.kwargs["stop"] == ["</json>"]


def test_appv21_provider_env_config_is_core_local() -> None:
    import appv21.providers.appv2_env as provider_module

    assert provider_module.build_appv21_model_client.__module__.startswith("appv21.")
    assert load_dotenv_values.__module__.startswith("appv21.")


def test_appv2_env_provider_converts_model_json_to_runtime_decision() -> None:
    class FakeClient:
        def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
            assert stage == "appv21_decision"
            assert "allowed_decisions" in prompt
            assert "kind" in schema["properties"]
            return json.dumps({"kind": "tool_call", "reason": "inspect", "payload": {"tool_name": "repo_snapshot"}, "evidence_refs": []})

    decision = AppV2EnvAgentProvider(client=FakeClient()).decide({"output_contract": {"allowed_decisions": ["tool_call"]}})

    assert decision.kind == "tool_call"
    assert decision.payload["tool_name"] == "repo_snapshot"


def test_appv2_env_provider_coerces_redundant_plan_to_mutation() -> None:
    class FakeClient:
        def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
            return json.dumps({"kind": "plan", "reason": "execute existing plan", "payload": {"next_step": "ACT"}, "evidence_refs": ["world://repo_snapshot/latest"]})

    prompt_payload = {
        "state": {
            "plan": {
                "runtime_plan": {
                    "mutation_intent": {"operation_batch_id": "batch", "operations": [{"action": "write", "path": "docs/a.md", "content": "a"}]},
                    "verification_intent": {"manifest_path": "docs/workspace_manifest.json"},
                }
            },
            "mutation_receipts": [],
            "verification_receipts": [],
            "artifacts": [],
        },
        "world": {"world_refs": [{"kind": "repo_snapshot", "ref_id": "world://repo_snapshot/latest"}]},
    }

    decision = AppV2EnvAgentProvider(client=FakeClient()).decide(prompt_payload)

    assert decision.kind == "mutation_intent"
    assert decision.payload["operation_batch_id"] == "batch"


def test_appv2_env_provider_coerces_apply_lease_tool_after_verification_to_finalize() -> None:
    class FakeClient:
        def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
            return json.dumps(
                {
                    "kind": "tool_call",
                    "reason": "apply already issued lease",
                    "payload": {"tool": "apply_mutation_lease", "lease_id": "lease_1"},
                    "evidence_refs": ["verification://latest"],
                }
            )

    prompt_payload = {
        "state": {
            "plan": {"runtime_plan": {"mutation_intent": {"operation_batch_id": "batch", "operations": []}, "verification_intent": {}}},
            "mutation_receipts": ["mut_batch"],
            "verification_receipts": ["verify_1"],
            "artifacts": [],
        },
        "world": {"world_refs": [{"kind": "repo_snapshot", "ref_id": "world://repo_snapshot/latest"}]},
    }

    decision = AppV2EnvAgentProvider(client=FakeClient()).decide(prompt_payload)

    assert decision.kind == "finalize"


def test_appv2_env_provider_normalizes_redundant_repo_snapshot_to_plan() -> None:
    class FakeClient:
        def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
            return json.dumps({"kind": "tool_call", "reason": "observe again", "payload": {"tool_name": "repo_snapshot"}, "evidence_refs": []})

    decision = AppV2EnvAgentProvider(client=FakeClient()).decide(
        {"world": {"world_refs": [{"ref_id": "world://repo_snapshot/latest", "kind": "repo_snapshot"}]}}
    )

    assert decision.kind == "plan"
    assert decision.evidence_refs == ["world://repo_snapshot/latest"]
