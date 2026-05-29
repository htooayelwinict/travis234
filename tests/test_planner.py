import json
from typing import Any

from app.decompressor.runtime import DecompressorRuntime
from app.planner.runtime import PlannerRuntime


class FakePromptChainClient:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        return json.dumps(self.responses[stage])


def _plan_for(text: str):
    envelope = DecompressorRuntime(model_client=FakePromptChainClient(_llm_responses_for(text))).run(text)
    return PlannerRuntime().run(envelope)


def _llm_responses_for(text: str) -> dict[str, Any]:
    if text == "what is docker":
        return {
            "decompress_request": {
                "normalized_input": "what is docker",
                "user_goal": "Answer the user's question.",
                "input_type": "question",
                "intents": ["question.answer"],
                "domains": ["infra"],
                "risks": [],
                "artifacts": [],
                "context_needed": [],
                "constraints": [],
                "complexity_hint": "low",
                "confidence": 0.9,
                "ambiguity": [],
                "assumptions": [],
            },
        }
    if text == "fix the app":
        responses = _llm_code_fix_responses(intents=["code.fix"], domains=["code"])
        responses["decompress_request"].update({
            "input_type": "ambiguous_request",
            "intents": ["code.fix"],
            "domains": ["code"],
            "risks": ["ambiguous_scope", "ambiguous_mutation"],
            "artifacts": [],
            "context_needed": ["repo_tree", "scope_clarification"],
            "constraints": ["target_scope_must_be_identified_before_mutation"],
            "ambiguity": ["The request does not identify a concrete target or failure."],
            "complexity_hint": "medium",
            "confidence": 0.61,
        })
        return responses
    if text == "fix terraform apply error":
        return _llm_code_fix_responses(intents=["infra.debug"], domains=["infra"])
    return _llm_code_fix_responses()


def _llm_code_fix_responses(*, intents: list[str] | None = None, domains: list[str] | None = None) -> dict[str, Any]:
    return {
        "decompress_request": {
            "normalized_input": "fix service.py",
            "user_goal": "Repair the service.",
            "input_type": "mutation_request",
            "intents": intents or ["code.fix"],
            "domains": domains or ["code"],
            "risks": ["mutation_requested", "file_mutation", "needs_verification"],
            "artifacts": [{"type": "file_hint", "path": "service.py", "language_hint": "python"}],
            "context_needed": ["repo_tree", "target_file"],
            "constraints": ["target_locations_must_be_identified_before_mutation", "mutation_requires_verification"],
            "ambiguity": [],
            "assumptions": [],
            "complexity_hint": "medium",
            "confidence": 0.9,
        },
    }


def test_planner_selects_direct_for_question() -> None:
    plan = _plan_for("what is docker")

    assert plan.planner == "direct"
    assert len(plan.steps) == 1
    assert plan.steps[0].worker_type == "direct_worker"
    assert plan.strategy == "direct_answer"


def test_planner_observes_for_pronoun_only_request() -> None:
    responses = _llm_responses_for("what is docker")
    responses["decompress_request"] = {
        "normalized_input": "it",
        "user_goal": "Answer the user's question.",
        "input_type": "question",
        "intents": ["question.answer"],
        "domains": ["general"],
        "risks": [],
        "artifacts": [],
        "context_needed": [],
        "constraints": [],
        "complexity_hint": "low",
        "confidence": 0.95,
        "ambiguity": [],
        "assumptions": [],
    }
    envelope = DecompressorRuntime(model_client=FakePromptChainClient(responses)).run("it")

    plan = PlannerRuntime().run(envelope)

    assert plan.planner == "fallback"
    assert plan.strategy == "observe_first"
    assert plan.steps[0].worker_type == "repo_worker"


def test_planner_selects_code_for_file_fix() -> None:
    plan = _plan_for("fix network_sniffer.py")

    assert plan.planner == "code"
    assert plan.strategy == "observe_then_patch"
    assert len(plan.steps) == 3
    assert [step.step_id for step in plan.steps] == ["observe_target", "patch_target", "verify_patch"]
    assert [step.worker_type for step in plan.steps] == ["repo_worker", "code_worker", "verify_worker"]
    assert plan.budget["max_tool_calls"] >= sum(step.max_tool_calls for step in plan.steps)


def test_planner_handles_vague_fix_with_observe_first() -> None:
    plan = _plan_for("fix the app")

    assert plan.planner == "fallback"
    assert plan.strategy == "observe_first"
    assert len(plan.steps) == 1
    assert plan.steps[0].worker_type == "repo_worker"
    assert plan.steps[0].permissions.get("write_files") is False


def test_planner_selects_infra_from_semantic_signals() -> None:
    envelope = DecompressorRuntime(
        model_client=FakePromptChainClient(_llm_responses_for("fix terraform apply error"))
    ).run("fix terraform apply error")

    plan = PlannerRuntime().run(envelope)

    assert plan.planner == "infra"


def test_planner_selects_code_from_llm_semantic_envelope() -> None:
    runtime = DecompressorRuntime(model_client=FakePromptChainClient(_llm_code_fix_responses()))
    envelope = runtime.run("fix service.py")

    plan = PlannerRuntime().run(envelope)

    assert plan.planner == "code"


def test_planner_selects_infra_from_llm_semantic_envelope() -> None:
    runtime = DecompressorRuntime(
        model_client=FakePromptChainClient(
            _llm_code_fix_responses(intents=["infra.debug"], domains=["infra"])
        )
    )
    envelope = runtime.run("fix service.py")

    plan = PlannerRuntime().run(envelope)

    assert plan.planner == "infra"


def test_planner_prefers_descriptive_ambiguity_over_code_semantics() -> None:
    runtime = DecompressorRuntime(
        model_client=FakePromptChainClient(
            {
                **_llm_code_fix_responses(intents=["code.fix"], domains=["code"]),
                "decompress_request": {
                    **_llm_code_fix_responses(intents=["code.fix"], domains=["code"])["decompress_request"],
                    "input_type": "ambiguous_request",
                    "intents": ["code.fix"],
                    "domains": ["code"],
                    "risks": ["ambiguous_scope", "ambiguous_mutation"],
                    "context_needed": ["repo_tree", "scope_clarification"],
                    "constraints": ["target_scope_must_be_identified_before_mutation"],
                    "ambiguity": ["The request does not identify a concrete target or failure."],
                    "complexity_hint": "medium",
                    "confidence": 0.61,
                },
            }
        )
    )
    envelope = runtime.run("fix service.py")

    plan = PlannerRuntime().run(envelope)

    assert plan.planner == "fallback"
