import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "appV2.2"))

from appv22 import AppV22AgentRuntime
from appv22.extensions.base import SkillCard
from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.providers.deterministic import DeterministicAppV22Provider
from appv22.context.compressor import AgentContextCompressor
from appv22.context.gateway_guard import GatewayContextGuard
from appv22.runtime.decisions import RuntimeDecision
from appv22.runtime.reducer import apply_event
from appv22.runtime.services import create_appv22_services
from appv22.state.events import RuntimeEvent
from appv22.state.models import AgentState, RequestEnvelope


class RecordingProvider:
    provider_id = "recording"

    def __init__(self, decision):
        self.decision = decision
        self.prompts = []

    def decide(self, prompt: dict):
        self.prompts.append(prompt)
        return self.decision


class MalformedDecisionProvider:
    provider_id = "malformed"

    def decide(self, prompt: dict):
        class MalformedDecision:
            kind = "plan"

            def to_dict(self):
                raise TypeError("malformed decision payload")

        return MalformedDecision()


class SequenceProvider:
    provider_id = "sequence"

    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.prompts = []

    def decide(self, prompt: dict):
        self.prompts.append(prompt)
        return self.decisions.pop(0)


class PromptMutatingGuard:
    def guard(self, messages):
        guarded = []
        for message in messages:
            copied = dict(message)
            if copied.get("name") == "provider_context_section" and copied.get("section") == "agent":
                copied["payload"] = dict(copied["payload"])
                copied["payload"]["guard_marker"] = "actual-provider-context"
            guarded.append(copied)
        return guarded


class SummaryInjectingCompressor:
    def compress(self, messages, *, previous_summary):
        compressed = []
        for message in messages:
            copied = dict(message)
            if copied.get("name") == "provider_context_section":
                copied["summary"] = {
                    "goals": ["persisted summary"],
                    "decisions": [],
                    "progress": [],
                    "open_risks": [],
                    "evidence_refs": [],
                }
            compressed.append(copied)
        return compressed


class OneSkillExtension:
    extension_id = "one_skill"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="one_skill.active",
                extension_id=self.extension_id,
                triggers=("workspace",),
                modes=("START", "PLAN", "ACT", "VERIFY"),
                summary="test skill",
                planner_id="one_skill.planner",
                mutation_policy_id="one_skill.policy",
                mutation_executor_id="one_skill.executor",
                verifier_id="one_skill.verifier",
                tool_ids=(),
                artifact_schema_ids=(),
            )
        ]

    def register_capabilities(self, capabilities) -> None:
        capabilities.register_planner("one_skill.planner", InactiveCapabilityPlanner())
        capabilities.register_mutation_policy("one_skill.policy", PassingPolicy())
        capabilities.register_mutation_executor("one_skill.executor", PassingExecutor())
        capabilities.register_verifier("one_skill.verifier", PassingVerifier())
        capabilities.register_mutation_policy("other.policy", PassingPolicy())
        capabilities.register_mutation_executor("other.executor", PassingExecutor())
        capabilities.register_verifier("other.verifier", PassingVerifier())


class OversizedPromptExtension:
    extension_id = "oversized_prompt"

    def __init__(self, raw_marker):
        self.raw_marker = raw_marker

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="oversized_prompt.active",
                extension_id=self.extension_id,
                triggers=("workspace",),
                modes=("START",),
                summary=f"oversized prompt material {self.raw_marker}",
                planner_id="oversized_prompt.planner",
                mutation_policy_id="oversized_prompt.policy",
                mutation_executor_id="oversized_prompt.executor",
                verifier_id="oversized_prompt.verifier",
                tool_ids=(),
                artifact_schema_ids=(),
            )
        ]

    def register_capabilities(self, capabilities) -> None:
        capabilities.register_planner("oversized_prompt.planner", InactiveCapabilityPlanner())
        capabilities.register_mutation_policy("oversized_prompt.policy", PassingPolicy())
        capabilities.register_mutation_executor("oversized_prompt.executor", PassingExecutor())
        capabilities.register_verifier("oversized_prompt.verifier", PassingVerifier())


class InactiveCapabilityPlanner:
    def plan(self, state):
        return {
            "mutation_policy_id": "other.policy",
            "mutation_executor_id": "one_skill.executor",
            "verifier_id": "one_skill.verifier",
            "verification_intent": {"commands": []},
            "mutation_intent": {
                "operation_batch_id": "batch",
                "operations": [],
            },
        }


class PassingPolicy:
    def validate(self, operations, *, root_path):
        return []


class PassingExecutor:
    def apply(self, operations, *, root_path):
        return {"status": "applied"}


class PassingVerifier:
    def verify(self, *, root_path, verification_intent):
        return {"status": "passed"}


def test_agent_loop_uses_capability_registry_without_file_imports(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "a.md").write_text("a", encoding="utf-8")
    services = create_appv22_services(
        root_path=tmp_path,
        provider=DeterministicAppV22Provider(),
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=8).run(
        "make this workspace sane and keep a record"
    )

    assert result["status"] == "completed"
    assert (tmp_path / "docs" / "a.md").is_file()
    assert result["mutation_receipts"]
    assert result["verification_receipts"]
    assert isinstance(result["mutation_receipts"][0], dict)
    assert "receipt_id" in result["mutation_receipts"][0]
    assert isinstance(result["verification_receipts"][0], dict)
    assert "verification_id" in result["verification_receipts"][0]


def test_agent_loop_fails_when_max_turns_exceeded(tmp_path):
    services = create_appv22_services(
        root_path=tmp_path,
        provider=DeterministicAppV22Provider(),
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=0).run(
        "make this workspace sane and keep a record"
    )

    assert result["status"] == "failed"
    assert result["reason"] == "max_turns_exceeded"


def test_agent_loop_guards_actual_provider_bound_prompt_and_persists_summary(tmp_path):
    provider = RecordingProvider(RuntimeDecision("pause", "stop after prompt inspection"))
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )
    services.gateway_guard = PromptMutatingGuard()
    services.compressor = SummaryInjectingCompressor()

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=1).run(
        "make this workspace sane and keep a record"
    )

    assert provider.prompts[0]["agent"]["guard_marker"] == "actual-provider-context"
    assert any(
        message.get("summary", {}).get("goals") == ["persisted summary"]
        for message in provider.prompts[0]["messages"]
    )
    summary_events = [event for event in result["events"] if event["event_type"] == "ContextSummaryUpdated"]
    assert summary_events
    assert summary_events[0]["payload"]["goals"] == ["persisted summary"]


def test_agent_loop_default_context_governance_compacts_oversized_provider_prompt(tmp_path):
    raw_marker = "RAW_PROVIDER_PROMPT_LEAK_SENTINEL_" + ("x" * 35_000)
    provider = RecordingProvider(RuntimeDecision("pause", "stop after prompt inspection"))
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[OversizedPromptExtension(raw_marker)],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=1).run(
        "workspace cleanup"
    )

    provider_payload = json.dumps(provider.prompts[0], sort_keys=True, default=str)
    assert raw_marker not in provider_payload
    assert [skill["skill_id"] for skill in provider.prompts[0]["skills"]] == ["oversized_prompt.active"]
    assert any(message.get("name") == "context_summary" for message in provider.prompts[0]["messages"])
    summary_events = [event for event in result["events"] if event["event_type"] == "ContextSummaryUpdated"]
    assert summary_events


def test_dual_context_compacts_large_world_context_and_carries_summary_to_next_turn(tmp_path):
    raw_marker = "RAW_DUAL_CONTEXT_SENTINEL"
    noisy_root = tmp_path / "incoming"
    noisy_root.mkdir()
    for index in range(80):
        (noisy_root / f"{raw_marker}_{index:03d}_workspace_note.md").write_text("x", encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe oversized workspace",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision("pause", "stop after compacted prompt inspection"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )
    services.gateway_guard = GatewayContextGuard(max_chars=50_000, threshold=1.0)
    services.compressor = AgentContextCompressor(max_chars=2_500, threshold=0.50)

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=2).run(
        "make this workspace sane and keep a record"
    )

    assert result["status"] == "failed"
    assert result["reason"] == "paused"
    assert len(provider.prompts) == 2
    first_prompt_payload = json.dumps(provider.prompts[0], sort_keys=True, default=str)
    second_prompt_payload = json.dumps(provider.prompts[1], sort_keys=True, default=str)
    assert raw_marker not in first_prompt_payload
    assert raw_marker not in second_prompt_payload
    assert provider.prompts[1]["world"] == {"world_refs": {}}
    assert any(message.get("name") == "context_summary" for message in provider.prompts[1]["messages"])
    summary_events = [event for event in result["events"] if event["event_type"] == "ContextSummaryUpdated"]
    assert summary_events
    assert any(event["payload"].get("evidence_refs") for event in summary_events)


def test_dual_context_preserves_compact_observation_contract(tmp_path):
    raw_marker = "RAW_OBSERVATION_CONTRACT_SENTINEL"
    noisy_root = tmp_path / "incoming"
    noisy_root.mkdir()
    for index in range(100):
        (noisy_root / f"{raw_marker}_{index:03d}.md").write_text("x", encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe workspace",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision("pause", "inspect compacted prompt"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )
    services.gateway_guard = GatewayContextGuard(max_chars=50_000, threshold=1.0)
    services.compressor = AgentContextCompressor(max_chars=2_800, threshold=0.50)

    AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=2).run(
        "make this workspace sane and keep a record"
    )

    assert len(provider.prompts) == 2
    second_prompt_payload = json.dumps(provider.prompts[1], sort_keys=True, default=str)
    assert raw_marker not in second_prompt_payload
    assert provider.prompts[1]["world"] == {"world_refs": {}}
    assert any(message.get("name") == "context_summary" for message in provider.prompts[1]["messages"])
    contract = provider.prompts[1]["skills"][0]["observation_contract"]
    assert contract is not None
    assert contract["evidence_refs"] == ("world://repo_snapshot/latest",)
    assert contract["evidence_kinds"] == ("file_management.repo_snapshot",)
    assert contract["preferred_tool_id"] == "file_management.repo_snapshot"


def test_dual_context_allows_tool_rehydration_after_compaction(tmp_path):
    raw_marker = "RAW_REHYDRATION_SENTINEL"
    noisy_root = tmp_path / "incoming"
    noisy_root.mkdir()
    for index in range(120):
        (noisy_root / f"{raw_marker}_{index:03d}_workspace_note.md").write_text("x", encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe oversized workspace",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision(
                "tool_call",
                "rehydrate exact workspace evidence from compacted summary",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision("pause", "stop after rehydration proof"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )
    services.gateway_guard = GatewayContextGuard(max_chars=50_000, threshold=1.0)
    services.compressor = AgentContextCompressor(max_chars=2_500, threshold=0.50)

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=3).run(
        "make this workspace sane and recover exact repo details if needed"
    )

    assert result["status"] == "failed"
    assert result["reason"] == "paused"
    assert len(provider.prompts) == 3
    second_prompt_payload = json.dumps(provider.prompts[1], sort_keys=True, default=str)
    third_prompt_payload = json.dumps(provider.prompts[2], sort_keys=True, default=str)
    assert raw_marker not in second_prompt_payload
    assert raw_marker not in third_prompt_payload
    summary_messages = [
        message for message in provider.prompts[1]["messages"] if message.get("name") == "context_summary"
    ]
    assert summary_messages
    assert summary_messages[0]["summary"]["evidence_refs"]
    tool_events = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"]
    assert len(tool_events) == 2
    assert all(event["payload"]["tool_id"] == "file_management.repo_snapshot" for event in tool_events)


def test_agent_loop_denies_runtime_plan_capability_outside_active_scope(tmp_path):
    provider = SequenceProvider(
        [
            RuntimeDecision("plan", "accept inactive scoped plan"),
            RuntimeDecision(
                "mutation_intent",
                "attempt inactive scoped mutation",
                {"operation_batch_id": "batch", "operations": []},
            ),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[OneSkillExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=4).run(
        "workspace cleanup"
    )

    assert result["status"] == "failed"
    assert result["reason"] == "runtime_loop_error"
    assert result["error_type"] == "ValueError"
    assert "inactive mutation_policy" in result["message"]


def test_agent_loop_converts_malformed_decision_to_failed_result(tmp_path):
    services = create_appv22_services(
        root_path=tmp_path,
        provider=MalformedDecisionProvider(),
        extensions=[FileManagementExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=1).run(
        "make this workspace sane and keep a record"
    )

    assert result["status"] == "failed"
    assert result["reason"] == "runtime_loop_error"
    assert result["error_type"] == "TypeError"


def test_agent_loop_converts_capability_cardinality_ambiguity_to_failed_result(tmp_path):
    provider = RecordingProvider(RuntimeDecision("plan", "ambiguous active planners"))
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension(), OneSkillExtension()],
    )

    result = AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=1).run(
        "make this workspace sane and keep a record workspace"
    )

    assert result["status"] == "failed"
    assert result["reason"] == "runtime_loop_error"
    assert result["error_type"] == "ValueError"
    assert "expected exactly one active planner" in result["message"]


def test_reducer_deep_copies_payloads_stored_in_state():
    state = AgentState("session", "run", RequestEnvelope("request", "goal", "/tmp/root"))
    payload = {"ref_id": "world://x", "nested": {"items": ["original"]}}

    apply_event(state, RuntimeEvent("WorldRefAdded", payload))
    payload["nested"]["items"].append("mutated")

    assert state.world_refs["world://x"]["nested"]["items"] == ["original"]
