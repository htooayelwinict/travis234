import json
from typing import Any

import pytest

from app.planner.env_config import build_planner_model_client
from app.planner.prompt_chain import LLMPlanCompiler, PlannerPromptChainError
from app.planner.runtime import PlannerRuntime
from app.planner.validator import PlannerPlanValidator
from app.schemas import Envelope, Plan, ReplanRequest


class FakePlannerClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self._responses = responses
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        self.calls.append({"stage": stage, "prompt": prompt, "schema": schema})
        response = self._responses[stage]
        if isinstance(response, str):
            return response
        return json.dumps(response)


class FakeConfiguredPlannerClient(FakePlannerClient):
    configs: list[dict[str, Any]] = []

    def __init__(self, **config: Any) -> None:
        self.configs.append(config)
        super().__init__({"draft_plan": _complex_multi_intent_plan()})


def _permissions(
    *,
    read_files: bool,
    write_files: bool,
    run_commands: bool,
    web_research: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    permissions = {
        "read_files": read_files,
        "write_files": write_files,
        "run_commands": run_commands,
        "web_research": web_research,
    }
    permissions.update(extra)
    return permissions


def _envelope(**overrides: Any) -> Envelope:
    payload = {
        "request_id": "req_123",
        "raw_input": "integrate the sdk with async transaction apis and fix lag",
        "normalized_input": "Integrate the SDK with async transaction APIs and resolve performance lag.",
        "user_goal": "Determine SDK availability, integrate async transaction flow, and fix lag.",
        "input_type": "async_sdk_performance_refactor_request",
        "intents": ["sdk.integration", "code.fix", "performance.investigate", "research.lookup"],
        "domains": ["code", "research"],
        "risks": ["performance_cause_unknown", "ambiguous_scope", "needs_verification", "mutation_requested"],
        "artifacts": [
            {"name": "target SDK", "type": "sdk"},
            {"name": "transaction APIs", "type": "api"},
            {"name": "async function", "type": "code_pattern"},
        ],
        "context_needed": ["dependency_manifest", "repo_tree", "performance_evidence", "target_file"],
        "constraints": [
            "target_locations_must_be_identified_before_mutation",
            "performance_claims_require_evidence",
            "mutation_requires_verification",
        ],
        "complexity_hint": "high",
        "confidence": 0.6,
        "ambiguity": ["SDK package identity unspecified"],
        "assumptions": ["Async pattern is viable"],
        "metadata": {},
    }
    payload.update(overrides)
    return Envelope.model_validate(payload)


def test_planner_env_uses_openrouter_aliases_and_latency_sort(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "PLANNER_LLM_ENABLED",
        "PLANNER_LLM_API_KEY",
        "PLANNER_LLM_MODEL",
        "PLANNER_LLM_BASE_URL",
        "PLANNER_LLM_PROVIDER_SORT",
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        "OPENROUTER_BASE_URL",
        "OPENROUTER_PROVIDER_SORT",
    ):
        monkeypatch.delenv(key, raising=False)
    FakeConfiguredPlannerClient.configs = []
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "PLANNER_LLM_ENABLED=true",
                "OPENROUTER_API_KEY=test-key",
                "OPENROUTER_MODEL=test-model",
                "OPENROUTER_BASE_URL=https://openrouter.example/api/v1",
            ]
        )
    )

    client = build_planner_model_client(str(dotenv), client_factory=FakeConfiguredPlannerClient)

    assert client is not None
    assert FakeConfiguredPlannerClient.configs[0]["api_key"] == "test-key"
    assert FakeConfiguredPlannerClient.configs[0]["model"] == "test-model"
    assert FakeConfiguredPlannerClient.configs[0]["base_url"] == "https://openrouter.example/api/v1"
    assert FakeConfiguredPlannerClient.configs[0]["provider_sort"] == "latency"


def _observe_only_plan(request_id: str = "req_123") -> Plan:
    return Plan.model_validate(
        {
            "plan_id": f"plan_{request_id}",
            "request_id": request_id,
            "planner": "llm_planner",
            "objective": "Observe first.",
            "strategy": "observe_first",
            "steps": [
                {
                    "step_id": "discover_repo",
                    "worker_type": "repo_worker",
                    "instruction": "Inspect repository scope.",
                    "output_artifacts": ["repo_inventory"],
                    "max_tool_calls": 2,
                    "max_model_calls": 1,
                    "permissions": _permissions(read_files=True, write_files=False, run_commands=False),
                }
            ],
            "budget": {"max_tool_calls": 2, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
            "success_criteria": ["Scope identified."],
            "metadata": {},
        }
    )


def _complex_multi_intent_plan(request_id: str = "req_123") -> dict[str, Any]:
    return {
        "plan_id": f"plan_{request_id}",
        "request_id": request_id,
        "planner": "llm_planner",
        "objective": "Determine SDK availability, integrate async transaction APIs, and verify lag fixes.",
        "strategy": "discover_research_patch_verify",
        "execution_pattern": "discover_analyze_research_design_mutate_verify_finalize",
        "global_invariants": [
            "observe_before_mutate",
            "target_scope_before_write",
            "verify_after_mutation",
            "evidence_before_claim",
            "bounded_permissions",
        ],
        "steps": [
            {
                "step_id": "repo_discovery",
                "worker_type": "repo_worker",
                "phase": "DISCOVER",
                "mode": "observe_only",
                "task_id": "task_main",
                "instruction": "Scan repo tree, dependency manifests, and candidate transaction API modules.",
                "output_artifacts": ["repo_inventory", "target_files", "dependency_manifest"],
                "max_tool_calls": 4,
                "max_model_calls": 1,
                "permissions": _permissions(read_files=True, write_files=False, run_commands=False),
            },
            {
                "step_id": "performance_context",
                "worker_type": "repo_worker",
                "phase": "ANALYZE",
                "mode": "observe_only",
                "task_id": "task_main",
                "instruction": "Collect performance evidence and lag symptoms from code and logs.",
                "input_artifacts": ["repo_inventory"],
                "output_artifacts": ["performance_evidence"],
                "max_tool_calls": 4,
                "max_model_calls": 1,
                "permissions": _permissions(read_files=True, write_files=False, run_commands=False),
            },
            {
                "step_id": "sdk_research",
                "worker_type": "research_worker",
                "phase": "RESEARCH",
                "mode": "observe_only",
                "task_id": "task_main",
                "instruction": "Determine SDK package availability and integration constraints.",
                "input_artifacts": ["repo_inventory", "dependency_manifest"],
                "output_artifacts": ["sdk_dependency_notes"],
                "max_tool_calls": 3,
                "max_model_calls": 1,
                "permissions": _permissions(read_files=True, write_files=False, run_commands=False),
            },
            {
                "step_id": "integration_design",
                "worker_type": "code_worker",
                "phase": "DESIGN",
                "mode": "plan_only",
                "task_id": "task_main",
                "instruction": "Design the async integration patch and narrow discovered target files into writable mutation scope.",
                "input_artifacts": ["target_files", "performance_evidence", "sdk_dependency_notes"],
                "output_artifacts": ["mutation_scope", "patch_design", "rollback_plan", "verification_plan"],
                "max_tool_calls": 3,
                "max_model_calls": 1,
                "permissions": _permissions(read_files=True, write_files=False, run_commands=False),
            },
            {
                "step_id": "async_integration_patch",
                "worker_type": "code_worker",
                "phase": "MUTATE",
                "mode": "bounded_mutation",
                "task_id": "task_main",
                "instruction": "Patch async integration only within the approved mutation scope.",
                "input_artifacts": ["mutation_scope", "patch_design", "rollback_plan", "performance_evidence", "sdk_dependency_notes"],
                "output_artifacts": ["patch_result", "change_summary", "rollback_patch"],
                "max_tool_calls": 6,
                "max_model_calls": 1,
                "permissions": _permissions(
                    read_files=True,
                    write_files=True,
                    run_commands=False,
                    write_paths_from_artifacts=["mutation_scope"],
                ),
            },
            {
                "step_id": "verify_integration",
                "worker_type": "verify_worker",
                "phase": "VERIFY",
                "mode": "verify_only",
                "task_id": "task_main",
                "instruction": "Run focused verification checks for patched transaction integration.",
                "input_artifacts": [
                    "patch_result",
                    "change_summary",
                    "rollback_patch",
                    "mutation_scope",
                    "performance_evidence",
                    "rollback_plan",
                ],
                "output_artifacts": ["verification_result"],
                "max_tool_calls": 3,
                "max_model_calls": 0,
                "permissions": _permissions(read_files=True, write_files=False, run_commands=True),
            },
            {
                "step_id": "finalize_summary",
                "worker_type": "research_worker",
                "phase": "FINALIZE",
                "mode": "summarize_only",
                "task_id": "task_main",
                "instruction": "Summarize what changed, what was verified, and residual risk notes.",
                "input_artifacts": ["verification_result", "rollback_plan"],
                "output_artifacts": ["final_report"],
                "max_tool_calls": 1,
                "max_model_calls": 0,
                "permissions": _permissions(read_files=True, write_files=False, run_commands=False),
            },
        ],
        "budget": {"max_tool_calls": 24, "max_model_calls": 5, "max_workers": 7, "max_retries": 0},
        "success_criteria": [
            "Dependency and targets discovered before mutation.",
            "Mutation verified with focused checks.",
            "Final summary is produced.",
        ],
        "metadata": {
            "stop_conditions": [
                "Stop before mutation if target files are not identified.",
                "Stop before mutation if SDK/dependency availability is not confirmed.",
                "Stop before mutation if performance/root-cause evidence is absent.",
            ],
            "replan_triggers": [
                "Replan if discovered targets differ from the requested transaction APIs.",
                "Replan if verification fails or performance evidence contradicts the assumed root cause.",
            ],
        },
    }


def _direct_support_plan(request_id: str = "req_123") -> dict[str, Any]:
    return {
        "plan_id": f"plan_{request_id}_direct_support",
        "request_id": request_id,
        "planner": "direct_support_planner",
        "objective": "Provide clarification-first direct support without runtime tools.",
        "strategy": "phase_aware_direct_support",
        "execution_pattern": "finalize",
        "global_invariants": ["no_tools", "no_file_access", "answer_from_user_input_only"],
        "steps": [
            {
                "step_id": "direct_support_response",
                "worker_type": "direct_worker",
                "phase": "FINALIZE",
                "mode": "summarize_only",
                "task_id": "direct_support",
                "instruction": "Ask concise clarifying questions and provide immediate harmless guidance from the user input only.",
                "input_artifacts": [],
                "output_artifacts": ["direct_guidance"],
                "max_tool_calls": 0,
                "max_model_calls": 1,
                "permissions": _permissions(read_files=False, write_files=False, run_commands=False),
            }
        ],
        "budget": {"max_tool_calls": 0, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
        "success_criteria": ["User receives immediate guidance and focused clarifying questions."],
        "metadata": {},
    }


def test_validator_accepts_observe_only_plan() -> None:
    plan = _observe_only_plan()
    envelope = _envelope(constraints=[], context_needed=[], confidence=0.9)

    validated = PlannerPlanValidator().validate(envelope, plan)
    assert validated.plan_id == "plan_req_123"


def test_validator_accepts_observe_patch_verify_plan() -> None:
    envelope = _envelope()
    plan = Plan.model_validate(_complex_multi_intent_plan())

    validated = PlannerPlanValidator().validate(envelope, plan)
    assert validated.steps[-2].worker_type == "verify_worker"
    assert validated.steps[-1].phase == "FINALIZE"


def test_validator_accepts_phase_aware_direct_support_plan() -> None:
    envelope = _envelope(
        raw_input="my transit card is not working",
        normalized_input="Transit card is not working.",
        user_goal="Get help troubleshooting a transit card.",
        input_type="transit_card_troubleshoot",
        intents=["transit.fix"],
        domains=["transit", "general"],
        risks=["ambiguous_scope"],
        artifacts=[{"name": "transit_card", "type": "transit_card"}],
        context_needed=["error_message", "card_type"],
        constraints=["specific_issue_must_be_described_before_assistance"],
        complexity_hint="low",
        confidence=0.4,
    )
    plan = Plan.model_validate(_direct_support_plan())

    validated = PlannerPlanValidator().validate(envelope, plan)

    assert validated.steps[0].worker_type == "direct_worker"
    assert validated.steps[0].phase == "FINALIZE"
    assert validated.steps[0].mode == "summarize_only"
    assert validated.steps[0].input_artifacts == []


def test_validator_rejects_unknown_worker_type() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][0]["worker_type"] = "mystery_worker"

    with pytest.raises(ValueError, match="unknown worker_type"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_planner_name_that_is_worker_type() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["planner"] = "research_worker"

    with pytest.raises(ValueError, match="planner must not be a worker_type"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_missing_input_artifact() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][4]["input_artifacts"] = ["does_not_exist"]

    with pytest.raises(ValueError, match="not produced by an earlier step"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_future_artifact_dependency() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][2]["input_artifacts"] = ["patch_result"]

    with pytest.raises(ValueError, match="not produced by an earlier step"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_budget_undercount() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["budget"] = {"max_tool_calls": 1, "max_model_calls": 1, "max_workers": 1, "max_retries": 0}

    with pytest.raises(ValueError, match="budget"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_write_before_discovery_when_required() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"] = [payload["steps"][3], payload["steps"][4]]
    payload["budget"] = {"max_tool_calls": 9, "max_model_calls": 1, "max_workers": 2, "max_retries": 0}

    with pytest.raises(ValueError, match="mutation requires a prior read-only discovery step"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_write_without_verify() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"] = payload["steps"][:-2]
    payload["budget"] = {"max_tool_calls": 17, "max_model_calls": 4, "max_workers": 4, "max_retries": 0}

    with pytest.raises(ValueError, match="verify"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_phase_aware_step_without_explicit_permissions() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][0]["permissions"] = {}

    with pytest.raises(ValueError, match="permissions must explicitly include"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_phase_mode_mismatch() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][4]["mode"] = "observe_only"

    with pytest.raises(ValueError, match="phase MUTATE must use mode"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_mutation_without_read_permission() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][4]["permissions"]["read_files"] = False

    with pytest.raises(ValueError, match="MUTATE must set permissions.read_files=true"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_mutation_without_root_cause_or_design_context() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][4]["input_artifacts"] = ["mutation_scope", "rollback_plan"]

    with pytest.raises(ValueError, match="root-cause, evidence, or fix-design context"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_verify_without_evidence_context() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][5]["input_artifacts"] = ["patch_result", "change_summary", "rollback_patch", "mutation_scope", "rollback_plan"]

    with pytest.raises(ValueError, match="consume evidence/root-cause artifacts"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_unscoped_write_permissions() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][4]["permissions"] = _permissions(read_files=True, write_files=True, run_commands=False)

    with pytest.raises(ValueError, match="restrict writes"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_discovery_artifact_as_write_scope() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][4]["permissions"]["write_paths_from_artifacts"] = ["target_files"]

    with pytest.raises(ValueError, match="DESIGN-produced write-scope artifacts"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_mutation_without_change_summary() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][4]["output_artifacts"] = ["patch_result", "rollback_patch"]
    payload["steps"][5]["input_artifacts"] = ["patch_result", "rollback_patch", "mutation_scope", "performance_evidence"]

    with pytest.raises(ValueError, match="output change_summary"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_mutation_without_pre_write_rollback_artifact() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][3]["output_artifacts"] = ["mutation_scope", "patch_design"]
    payload["steps"][4]["input_artifacts"] = [
        "mutation_scope",
        "patch_design",
        "performance_evidence",
        "sdk_dependency_notes",
    ]
    payload["steps"][5]["input_artifacts"] = ["patch_result", "change_summary", "mutation_scope", "performance_evidence"]

    with pytest.raises(ValueError, match="rollback/revert artifact before write"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_verify_without_mutation_context_inputs() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][5]["input_artifacts"] = ["patch_result"]

    with pytest.raises(ValueError, match="consume write-scope artifacts"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_finalize_without_output_artifact() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][6]["output_artifacts"] = []

    with pytest.raises(ValueError, match="FINALIZE must output"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_mutation_without_design_rollback_plan() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][3]["output_artifacts"] = ["mutation_scope", "patch_design"]

    with pytest.raises(ValueError, match="DESIGN step output rollback_plan"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_validator_rejects_mutation_without_design_verification_plan() -> None:
    envelope = _envelope()
    payload = _complex_multi_intent_plan()
    payload["steps"][3]["output_artifacts"] = ["mutation_scope", "patch_design", "rollback_plan"]

    with pytest.raises(ValueError, match="DESIGN step output verification_plan or test_plan"):
        PlannerPlanValidator().validate(envelope, Plan.model_validate(payload))


def test_prompt_chain_draft_valid_plan_succeeds() -> None:
    envelope = _envelope()
    client = FakePlannerClient({"draft_plan": _complex_multi_intent_plan()})
    plan = LLMPlanCompiler(model_client=client).run(envelope)

    assert len(client.calls) == 1
    assert client.calls[0]["stage"] == "draft_plan"
    assert plan.metadata["llm_planner"]["mode"] == "completed"
    assert plan.metadata["llm_planner"]["validation_errors"] == []
    assert plan.metadata["llm_planner"]["resolved_validation_errors"] == []
    assert plan.metadata["llm_planner"]["budget_auto_aligned"] is False
    assert plan.steps[0].worker_type == "repo_worker"


def test_prompt_chain_draft_valid_direct_support_plan_succeeds() -> None:
    envelope = _envelope(
        raw_input="my transit card is not working",
        normalized_input="Transit card is not working.",
        user_goal="Get help troubleshooting a transit card.",
        input_type="transit_card_troubleshoot",
        intents=["transit.fix"],
        domains=["transit", "general"],
        risks=["ambiguous_scope"],
        artifacts=[{"name": "transit_card", "type": "transit_card"}],
        context_needed=["error_message", "card_type"],
        constraints=["specific_issue_must_be_described_before_assistance"],
        complexity_hint="low",
        confidence=0.4,
    )
    client = FakePlannerClient({"draft_plan": _direct_support_plan()})

    plan = LLMPlanCompiler(model_client=client).run(envelope)

    assert [call["stage"] for call in client.calls] == ["draft_plan"]
    assert plan.metadata["llm_planner"]["mode"] == "completed"
    assert plan.execution_pattern == "finalize"
    assert plan.steps[0].phase == "FINALIZE"
    assert plan.steps[0].input_artifacts == []


def test_prompt_chain_auto_aligns_budget_without_repair_when_only_budget_is_invalid() -> None:
    envelope = _envelope()
    budget_invalid = _complex_multi_intent_plan()
    budget_invalid["budget"] = {"max_tool_calls": 1, "max_model_calls": 1, "max_workers": 1}
    client = FakePlannerClient({"draft_plan": budget_invalid})

    plan = LLMPlanCompiler(model_client=client).run(envelope)

    assert [call["stage"] for call in client.calls] == ["draft_plan"]
    assert plan.metadata["llm_planner"]["mode"] == "completed"
    assert plan.metadata["llm_planner"]["budget_auto_aligned"] is True
    assert plan.budget["max_tool_calls"] >= sum(step.max_tool_calls for step in plan.steps)
    assert plan.budget["max_model_calls"] >= sum(step.max_model_calls for step in plan.steps)
    assert plan.budget["max_workers"] >= len(plan.steps)
    assert plan.budget["max_retries"] == 0


def test_prompt_chain_repairs_missing_phase_contract_fields() -> None:
    envelope = _envelope()
    draft_missing_phase_contract = _complex_multi_intent_plan()
    for step in draft_missing_phase_contract["steps"]:
        step.pop("phase", None)
        step.pop("mode", None)
        step.pop("task_id", None)
    repaired = _complex_multi_intent_plan()

    client = FakePlannerClient({"draft_plan": draft_missing_phase_contract, "repair_plan_1": repaired})

    plan = LLMPlanCompiler(model_client=client).run(envelope)

    assert [call["stage"] for call in client.calls] == ["draft_plan", "repair_plan_1"]
    assert plan.metadata["llm_planner"]["mode"] == "repaired"
    assert all(step.phase for step in plan.steps)
    assert all(step.mode for step in plan.steps)
    assert all(step.task_id for step in plan.steps)


def test_prompt_chain_repairs_missing_execution_pattern() -> None:
    envelope = _envelope()
    missing_execution_pattern = _complex_multi_intent_plan()
    missing_execution_pattern["execution_pattern"] = None
    repaired = _complex_multi_intent_plan()
    client = FakePlannerClient({"draft_plan": missing_execution_pattern, "repair_plan_1": repaired})

    plan = LLMPlanCompiler(model_client=client).run(envelope)

    assert [call["stage"] for call in client.calls] == ["draft_plan", "repair_plan_1"]
    assert plan.execution_pattern == "discover_analyze_research_design_mutate_verify_finalize"


def test_prompt_chain_repairs_scope_producer_phase() -> None:
    envelope = _envelope()
    misclassified_scope_producer = _complex_multi_intent_plan()
    misclassified_scope_producer["steps"][3]["phase"] = "RESEARCH"
    misclassified_scope_producer["steps"][3]["mode"] = "observe_only"
    repaired = _complex_multi_intent_plan()
    client = FakePlannerClient({"draft_plan": misclassified_scope_producer, "repair_plan_1": repaired})

    plan = LLMPlanCompiler(model_client=client).run(envelope)

    assert [call["stage"] for call in client.calls] == ["draft_plan", "repair_plan_1"]
    assert plan.steps[3].phase == "DESIGN"
    assert plan.steps[3].mode == "plan_only"


def test_prompt_chain_repairs_pre_mutation_phase_inversion() -> None:
    envelope = _envelope()
    inverted_phases = _complex_multi_intent_plan()
    inverted_phases["steps"][1]["phase"] = "RESEARCH"
    inverted_phases["steps"][2]["phase"] = "ANALYZE"
    repaired = _complex_multi_intent_plan()
    client = FakePlannerClient({"draft_plan": inverted_phases, "repair_plan_1": repaired})

    plan = LLMPlanCompiler(model_client=client).run(envelope)

    assert [call["stage"] for call in client.calls] == ["draft_plan", "repair_plan_1"]
    assert [step.phase for step in plan.steps[:5]] == ["DISCOVER", "ANALYZE", "RESEARCH", "DESIGN", "MUTATE"]


def test_prompt_chain_repairs_missing_write_scope_from_design() -> None:
    envelope = _envelope()
    missing_write_scope = _complex_multi_intent_plan()
    missing_write_scope["steps"][4]["permissions"].pop("write_paths_from_artifacts")
    repaired = _complex_multi_intent_plan()
    client = FakePlannerClient({"draft_plan": missing_write_scope, "repair_plan_1": repaired})

    plan = LLMPlanCompiler(model_client=client).run(envelope)

    assert [call["stage"] for call in client.calls] == ["draft_plan", "repair_plan_1"]
    assert plan.steps[4].permissions["write_paths_from_artifacts"] == ["mutation_scope"]


def test_prompt_chain_repairs_invalid_semantic_mode_name() -> None:
    envelope = _envelope()
    invalid_mode = _complex_multi_intent_plan()
    invalid_mode["steps"][1]["mode"] = "analysis"
    repaired = _complex_multi_intent_plan()
    client = FakePlannerClient({"draft_plan": invalid_mode, "repair_plan_1": repaired})

    plan = LLMPlanCompiler(model_client=client).run(envelope)

    assert [call["stage"] for call in client.calls] == ["draft_plan", "repair_plan_1"]
    assert plan.steps[1].phase == "ANALYZE"
    assert plan.steps[1].mode == "observe_only"


def test_prompt_chain_repairs_mutation_contract_hygiene() -> None:
    envelope = _envelope()
    invalid_contract = _complex_multi_intent_plan()
    invalid_contract["planner"] = "code_worker"
    invalid_contract["steps"][3]["output_artifacts"] = ["mutation_scope", "patch_design", "rollback_plan"]
    invalid_contract["steps"][4]["input_artifacts"] = ["mutation_scope", "rollback_plan"]
    invalid_contract["steps"][4]["permissions"]["read_files"] = False
    repaired = _complex_multi_intent_plan()
    client = FakePlannerClient({"draft_plan": invalid_contract, "repair_plan_1": repaired})

    plan = LLMPlanCompiler(model_client=client).run(envelope)

    assert [call["stage"] for call in client.calls] == ["draft_plan", "repair_plan_1"]
    assert plan.planner == "llm_planner"
    assert "verification_plan" in plan.steps[3].output_artifacts
    assert plan.steps[4].permissions["read_files"] is True
    assert "patch_design" in plan.steps[4].input_artifacts


def test_prompt_chain_repairs_invalid_plan_once() -> None:
    envelope = _envelope()
    invalid_draft = _complex_multi_intent_plan()
    invalid_draft["steps"][0]["worker_type"] = "unknown_worker"
    repaired = _complex_multi_intent_plan()
    client = FakePlannerClient({"draft_plan": invalid_draft, "repair_plan_1": repaired})

    plan = LLMPlanCompiler(model_client=client).run(envelope)

    assert [call["stage"] for call in client.calls] == ["draft_plan", "repair_plan_1"]
    assert plan.metadata["llm_planner"]["mode"] == "repaired"
    assert plan.metadata["llm_planner"]["repair_attempted"] is True
    assert plan.metadata["llm_planner"]["validation_errors"] == []
    assert plan.metadata["llm_planner"]["resolved_validation_errors"]


def test_prompt_chain_second_repair_attempts_remaining_validation_errors() -> None:
    envelope = _envelope()
    invalid_draft = _complex_multi_intent_plan()
    invalid_draft["steps"][0]["worker_type"] = "unknown_worker"

    still_invalid = _complex_multi_intent_plan()
    still_invalid["steps"][4]["permissions"].pop("write_paths_from_artifacts")
    final_repaired = _complex_multi_intent_plan()

    client = FakePlannerClient({"draft_plan": invalid_draft, "repair_plan_1": still_invalid, "repair_plan_2": final_repaired})

    plan = LLMPlanCompiler(model_client=client).run(envelope)

    assert [call["stage"] for call in client.calls] == ["draft_plan", "repair_plan_1", "repair_plan_2"]
    assert plan.metadata["llm_planner"]["mode"] == "repaired"
    assert plan.metadata["llm_planner"]["model_calls"] == 3
    assert plan.metadata["llm_planner"]["validation_errors"] == []


def test_prompt_chain_repair_prompt_contains_validation_errors() -> None:
    envelope = _envelope()
    invalid_draft = _complex_multi_intent_plan()
    invalid_draft["steps"][0]["worker_type"] = "unknown_worker"
    repaired = _complex_multi_intent_plan()
    client = FakePlannerClient({"draft_plan": invalid_draft, "repair_plan_1": repaired})

    LLMPlanCompiler(model_client=client).run(envelope)

    repair_prompt = client.calls[1]["prompt"]
    assert "validation_errors" in repair_prompt
    assert "unknown worker_type" in repair_prompt


def test_prompt_chain_repair_prompt_emphasizes_execution_pattern_when_missing() -> None:
    envelope = _envelope()
    invalid_draft = _complex_multi_intent_plan()
    invalid_draft["execution_pattern"] = None
    repaired = _complex_multi_intent_plan()
    client = FakePlannerClient({"draft_plan": invalid_draft, "repair_plan_1": repaired})

    LLMPlanCompiler(model_client=client).run(envelope)

    repair_prompt = client.calls[1]["prompt"]
    assert "plan.execution_pattern" in repair_prompt
    assert "do not leave it null" in repair_prompt


def test_prompt_chain_fails_after_invalid_repair() -> None:
    envelope = _envelope()
    bad_payload = {"not": "a plan"}
    client = FakePlannerClient({"draft_plan": bad_payload, "repair_plan_1": bad_payload, "repair_plan_2": bad_payload})

    with pytest.raises(PlannerPromptChainError):
        LLMPlanCompiler(model_client=client).run(envelope)


def test_prompt_chain_replan_returns_full_valid_plan() -> None:
    envelope = _envelope()
    current_plan = Plan.model_validate(_complex_multi_intent_plan())
    replan_request = ReplanRequest(
        request_id=envelope.request_id,
        plan_id=current_plan.plan_id,
        run_id=f"run_{current_plan.plan_id}",
        failed_step_id="sdk_research",
        reason="worker needs different research path",
        worker_result={"status": "needs_replan", "summary": "source evidence unavailable"},
        completed_artifacts=[{"id": "repo_inventory", "content": "repo context"}],
        completed_step_ids=["repo_discovery"],
        remaining_budget={"max_tool_calls": 10, "max_model_calls": 3, "max_workers": 4, "max_retries": 0},
        recommended_action="create a fresh self-contained plan with a new research step",
    )
    client = FakePlannerClient({"replan_plan": _complex_multi_intent_plan()})

    plan = LLMPlanCompiler(model_client=client).replan(
        envelope=envelope,
        current_plan=current_plan,
        replan_request=replan_request,
    )

    assert [call["stage"] for call in client.calls] == ["replan_plan"]
    assert plan.request_id == envelope.request_id
    assert plan.metadata["llm_planner"]["replan"] is True
    assert plan.metadata["llm_planner"]["parent_plan_id"] == current_plan.plan_id
    assert plan.metadata["llm_planner"]["failed_step_id"] == "sdk_research"


def test_replan_prompt_demands_full_existing_schema_plan() -> None:
    envelope = _envelope()
    current_plan = Plan.model_validate(_complex_multi_intent_plan())
    replan_request = ReplanRequest(
        request_id=envelope.request_id,
        plan_id=current_plan.plan_id,
        run_id=f"run_{current_plan.plan_id}",
        failed_step_id="sdk_research",
        reason="worker needs replan",
    )
    client = FakePlannerClient({"replan_plan": _complex_multi_intent_plan()})

    LLMPlanCompiler(model_client=client).replan(
        envelope=envelope,
        current_plan=current_plan,
        replan_request=replan_request,
    )

    replan_prompt = client.calls[0]["prompt"]
    assert "full replacement Plan" in replan_prompt
    assert "existing Plan schema" in replan_prompt
    assert "not a patch" in replan_prompt
    assert "Do not reference artifacts from the previous plan" in replan_prompt
    assert "completed_step_ids" in replan_prompt
    assert "authoritative execution history" in replan_prompt


def test_prompt_contains_worker_catalog_and_envelope() -> None:
    envelope = _envelope()
    client = FakePlannerClient({"draft_plan": _complex_multi_intent_plan()})
    LLMPlanCompiler(model_client=client).run(envelope)

    draft_prompt = client.calls[0]["prompt"]
    assert "worker_catalog" in draft_prompt
    assert "allowed_modes" in draft_prompt
    assert "bounded_mutation" in draft_prompt
    assert "repo_worker" in draft_prompt
    assert "web_research_worker" in draft_prompt
    assert "filesystem_worker" in draft_prompt
    assert "runtime_capabilities" in draft_prompt
    assert "write_many_files" in draft_prompt
    assert "Only safe on code_worker." not in draft_prompt
    assert "async_sdk_performance_refactor_request" in draft_prompt


def test_draft_prompt_contains_instruction_context_block_policy() -> None:
    envelope = _envelope()
    client = FakePlannerClient({"draft_plan": _complex_multi_intent_plan()})

    LLMPlanCompiler(model_client=client).run(envelope)

    draft_prompt = client.calls[0]["prompt"]
    assert "instruction_context_block" in draft_prompt
    assert "Every generated step.instruction must start" in draft_prompt
    assert "Known facts:" in draft_prompt
    assert "Unknowns:" in draft_prompt
    assert "Do now:" in draft_prompt
    assert "Do not do:" in draft_prompt
    assert "Output:" in draft_prompt
    assert "mutation_scope" in draft_prompt
    assert "rollback_plan" in draft_prompt


def test_repair_prompt_contains_instruction_context_block_policy() -> None:
    envelope = _envelope()
    invalid_draft = _complex_multi_intent_plan()
    invalid_draft["steps"][0]["worker_type"] = "unknown_worker"
    repaired = _complex_multi_intent_plan()
    client = FakePlannerClient({"draft_plan": invalid_draft, "repair_plan_1": repaired})

    LLMPlanCompiler(model_client=client).run(envelope)

    repair_prompt = client.calls[1]["prompt"]
    assert "instruction_context_block" in repair_prompt
    assert "Repair every missing, weak, or non-leading instruction context block" in repair_prompt
    assert "Known facts:" in repair_prompt
    assert "Unknowns:" in repair_prompt
    assert "Do now:" in repair_prompt
    assert "Do not do:" in repair_prompt
    assert "Output:" in repair_prompt


def test_prompt_contains_direct_support_archetype_and_artifact_mapping_rules() -> None:
    envelope = _envelope(
        raw_input="my transit card is not working",
        normalized_input="Transit card is not working.",
        user_goal="Get help troubleshooting a transit card.",
        input_type="transit_card_troubleshoot",
        intents=["transit.fix"],
        domains=["transit", "general"],
        risks=["ambiguous_scope"],
        artifacts=[{"name": "transit_card", "type": "transit_card"}],
        context_needed=["error_message", "card_type"],
        constraints=["specific_issue_must_be_described_before_assistance"],
        complexity_hint="low",
        confidence=0.4,
    )
    client = FakePlannerClient({"draft_plan": _direct_support_plan()})

    LLMPlanCompiler(model_client=client).run(envelope)

    draft_prompt = client.calls[0]["prompt"]
    assert "direct_support" in draft_prompt
    assert "phase-aware direct_support archetype" in draft_prompt
    assert "direct_support_plan_template" in draft_prompt
    assert "direct_support_response" in draft_prompt
    assert "direct_support_planner" in draft_prompt
    assert "Do not output null or omitted step.phase" in draft_prompt
    assert "Never copy envelope.artifacts into step.input_artifacts" in draft_prompt
    assert "direct_support plans should use an empty list" in draft_prompt


def test_prompt_direct_support_guidance_does_not_override_runtime_actions() -> None:
    envelope = _envelope()
    client = FakePlannerClient({"draft_plan": _complex_multi_intent_plan()})

    LLMPlanCompiler(model_client=client).run(envelope)

    draft_prompt = client.calls[0]["prompt"]
    assert "Never use direct_support" in draft_prompt
    assert "All newly generated plans must be phase-aware" in draft_prompt
    assert "mutation_requested" in draft_prompt
    assert "code.fix" in draft_prompt
    assert "rollback or verification" in draft_prompt


def test_runtime_uses_injected_compiler() -> None:
    envelope = _envelope()

    class FakeCompiler:
        def run(self, envelope: Envelope) -> Plan:
            return Plan.model_validate(_complex_multi_intent_plan(request_id=envelope.request_id))

    runtime = PlannerRuntime(compiler=FakeCompiler())
    plan = runtime.run(envelope)

    assert plan.planner == "llm_planner"
    assert plan.metadata["planner_runtime"]["mode"] == "llm_prompt_chain"


def test_runtime_falls_back_safely_when_compiler_fails() -> None:
    envelope = _envelope()

    class ExplodingCompiler:
        def run(self, envelope: Envelope) -> Plan:
            raise RuntimeError("boom")

    runtime = PlannerRuntime(compiler=ExplodingCompiler(), fallback_on_error=True)
    plan = runtime.run(envelope)

    assert plan.planner == "fallback"
    assert plan.steps[0].worker_type == "repo_worker"
    assert plan.metadata["planner_runtime"]["fallback_reason"] == "planner_llm_error"


def test_runtime_without_compiler_uses_safe_fallback() -> None:
    envelope = _envelope()
    plan = PlannerRuntime().run(envelope)

    assert plan.planner == "fallback"
    assert plan.metadata["planner_runtime"]["fallback_reason"] == "planner_llm_unavailable"


def test_runtime_replan_uses_injected_compiler() -> None:
    envelope = _envelope()
    current_plan = Plan.model_validate(_complex_multi_intent_plan())
    replan_request = ReplanRequest(
        request_id=envelope.request_id,
        plan_id=current_plan.plan_id,
        run_id=f"run_{current_plan.plan_id}",
        failed_step_id="sdk_research",
        reason="worker requested replan",
    )

    class FakeCompiler:
        def replan(self, *, envelope: Envelope, current_plan: Plan, replan_request: ReplanRequest) -> Plan:
            plan = Plan.model_validate(_complex_multi_intent_plan(request_id=envelope.request_id))
            return plan.model_copy(update={"metadata": {"compiler": "fake_replan"}})

    runtime = PlannerRuntime(compiler=FakeCompiler(), fallback_on_error=False)
    plan = runtime.replan(envelope, current_plan, replan_request)

    assert plan.planner == "llm_planner"
    assert plan.metadata["compiler"] == "fake_replan"
    assert plan.metadata["planner_runtime"]["mode"] == "llm_prompt_chain_replan"


def test_complex_multi_intent_plan_shape() -> None:
    envelope = _envelope()
    client = FakePlannerClient({"draft_plan": _complex_multi_intent_plan()})
    plan = PlannerRuntime(compiler=LLMPlanCompiler(model_client=client)).run(envelope)

    assert [step.step_id for step in plan.steps] == [
        "repo_discovery",
        "performance_context",
        "sdk_research",
        "integration_design",
        "async_integration_patch",
        "verify_integration",
        "finalize_summary",
    ]
    assert plan.steps[4].permissions.get("write_files") is True
    assert plan.steps[5].worker_type == "verify_worker"
    assert plan.steps[6].phase == "FINALIZE"
