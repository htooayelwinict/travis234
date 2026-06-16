from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "appV2.2"))

from appv22.context.prompt_builder import PromptBuilder
from appv22.context.selector import ContextSelector
from appv22.extensions.base import SkillCard
from appv22.extensions.registry import ResolvedExtensions
from appv22.state.models import AgentState, RequestEnvelope


def _card(skill_id: str, modes: tuple[str, ...], tool_ids: tuple[str, ...]) -> SkillCard:
    return SkillCard(
        skill_id=skill_id,
        extension_id="demo",
        triggers=("clean",),
        modes=modes,
        summary=f"{skill_id} summary",
        planner_id=f"{skill_id}.planner",
        mutation_policy_id=f"{skill_id}.policy",
        mutation_executor_id=f"{skill_id}.executor",
        verifier_id=f"{skill_id}.verifier",
        tool_ids=tool_ids,
        artifact_schema_ids=(f"{skill_id}.schema",),
    )


def _resolved() -> ResolvedExtensions:
    plan_card = _card("demo.plan_skill", ("PLAN",), ("demo.plan_only",))
    observe_card = _card("demo.observe_skill", ("START", "THINK", "OBSERVE", "VERIFY"), ("demo.inspect",))
    return ResolvedExtensions(
        ("demo",),
        (plan_card, observe_card),
        ("demo.inspect", "demo.plan_only"),
        ("demo.planner",),
        ("demo.policy",),
        ("demo.executor",),
        ("demo.verifier",),
        ("demo.schema",),
    )


def test_prompt_uses_pre_turn_mode_and_hides_tools_in_plan():
    state = AgentState("sess", "run", RequestEnvelope("req", "clean this", "."), mode="PLAN")
    selected = ContextSelector().select(state, _resolved(), pre_turn_mode="PLAN")
    prompt = PromptBuilder().build(state, selected)

    assert prompt["system"]["identity"] == "AppV2.2 Pi-Hermes coding agent"
    assert any("observe -> plan -> act -> verify" in rule for rule in prompt["system"]["agent_loop_contract"])
    assert any("context_summary.evidence_refs" in rule for rule in prompt["system"]["dual_context_contract"])
    assert any("selected_tools" in rule for rule in prompt["system"]["tool_contract"])
    assert any("observation evidence" in rule for rule in prompt["agent"]["mode_contract"])
    assert prompt["agent"]["mode"] == "PLAN"
    assert prompt["selection"]["selected_tools"] == []
    assert prompt["selection"]["available_tools"] == []
    assert prompt["tools"] == []
    assert selected["skills"][0]["tool_ids"] == ()
    assert prompt["skills"][0]["tool_ids"] == ()


def test_observe_mode_exposes_only_tools_from_selected_skill_cards():
    state = AgentState("sess", "run", RequestEnvelope("req", "clean this", "."), mode="OBSERVE")
    selected = ContextSelector().select(state, _resolved(), pre_turn_mode="OBSERVE")
    prompt = PromptBuilder().build(state, selected)

    assert selected["selection"]["selected_tools"] == ["demo.inspect"]
    assert selected["selection"]["available_tools"] == ["demo.inspect"]
    assert selected["tools"] == ["demo.inspect"]
    assert prompt["selection"]["available_tools"] == ["demo.inspect"]
    assert selected["skills"][0]["tool_ids"] == ("demo.inspect",)
    assert prompt["skills"][0]["tool_ids"] == ("demo.inspect",)


def test_read_mode_tool_order_is_deterministic_and_scoped_to_selected_skill_cards():
    observe_first = _card("demo.observe_first", ("OBSERVE",), ("demo.inspect_b", "demo.inspect_a"))
    plan_card = _card("demo.plan_skill", ("PLAN",), ("demo.plan_only",))
    observe_second = _card("demo.observe_second", ("OBSERVE",), ("demo.inspect_c",))
    resolved = ResolvedExtensions(
        ("demo",),
        (observe_first, plan_card, observe_second),
        ("demo.inspect_c", "demo.plan_only", "demo.inspect_a", "demo.inspect_b"),
        ("demo.planner",),
        ("demo.policy",),
        ("demo.executor",),
        ("demo.verifier",),
        ("demo.schema",),
    )
    state = AgentState("sess", "run", RequestEnvelope("req", "clean this", "."), mode="OBSERVE")

    selected = ContextSelector().select(state, resolved, pre_turn_mode="OBSERVE")

    assert selected["selection"]["selected_skills"] == ["demo.observe_first", "demo.observe_second"]
    assert selected["selection"]["selected_tools"] == [
        "demo.inspect_b",
        "demo.inspect_a",
        "demo.inspect_c",
    ]
    assert selected["tools"] == ["demo.inspect_b", "demo.inspect_a", "demo.inspect_c"]


def test_skill_selection_filters_by_pre_turn_mode():
    state = AgentState("sess", "run", RequestEnvelope("req", "clean this", "."), mode="OBSERVE")
    selected = ContextSelector().select(state, _resolved(), pre_turn_mode="OBSERVE")

    assert selected["selection"]["selected_skills"] == ["demo.observe_skill"]
    assert [skill["skill_id"] for skill in selected["skills"]] == ["demo.observe_skill"]


def test_pre_turn_mode_controls_prompt_when_state_mode_has_changed():
    state = AgentState("sess", "run", RequestEnvelope("req", "clean this", "."), mode="ACT")
    selected = ContextSelector().select(state, _resolved(), pre_turn_mode="OBSERVE")
    prompt = PromptBuilder().build(state, selected)

    assert prompt["agent"]["mode"] == "OBSERVE"
    assert prompt["state"]["mode"] == "OBSERVE"
    assert prompt["selection"]["selected_tools"] == ["demo.inspect"]


def test_prompt_includes_state_receipts_world_refs_and_metadata_without_mutability_leaks():
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope("req", "clean this", ".", constraints=["stay safe"]),
        mode="VERIFY",
    )
    state.runtime_plan["step"] = {"id": "plan_1"}
    state.mutation_receipts["mut_1"] = {"status": "applied"}
    state.verification_receipts["verify_1"] = {"status": "passed"}
    state.world_refs["world://repo_snapshot/latest"] = {"summary": "snapshot"}
    state.context_summary["evidence_refs"] = ["world://repo_snapshot/latest"]
    state.context_summary["progress"] = ["repo snapshot evidence is available"]

    selected = ContextSelector().select(state, _resolved(), pre_turn_mode="VERIFY")
    prompt = PromptBuilder().build(state, selected)
    state.runtime_plan["step"]["id"] = "mutated"
    state.world_refs["world://repo_snapshot/latest"]["summary"] = "mutated"
    selected["state"]["mutation_receipts"]["mut_1"]["status"] = "mutated"
    selected["state"]["context_summary"]["progress"].append("mutated")

    assert prompt["agent"]["constraints"] == ["stay safe"]
    assert prompt["state"]["runtime_plan"]["step"]["id"] == "plan_1"
    assert prompt["state"]["mutation_receipts"]["mut_1"]["status"] == "applied"
    assert prompt["state"]["verification_receipts"]["verify_1"]["status"] == "passed"
    assert prompt["state"]["context_summary"]["evidence_refs"] == ["world://repo_snapshot/latest"]
    assert prompt["state"]["context_summary"]["progress"] == ["repo snapshot evidence is available"]
    assert prompt["world"]["world_refs"]["world://repo_snapshot/latest"]["summary"] == "snapshot"
    assert prompt["selection"]["active_extensions"] == ["demo"]
    assert prompt["selection"]["available_tools"] == ["demo.inspect"]


def test_skill_prompt_instructions_are_selected_and_prompt_visible():
    card = SkillCard(
        skill_id="demo.web_research",
        extension_id="demo",
        triggers=("research",),
        modes=("OBSERVE",),
        summary="Research public sources.",
        planner_id="demo.research.planner",
        mutation_policy_id="demo.research.policy",
        mutation_executor_id="demo.research.executor",
        verifier_id="demo.research.verifier",
        tool_ids=("demo.search",),
        artifact_schema_ids=("demo.research_report",),
        instructions=(
            "Use the skill prompt as the domain adapter, not as a replacement for the agent loop.",
            "Rehydrate exact evidence before citing or writing final claims.",
        ),
    )
    resolved = ResolvedExtensions(
        ("demo",),
        (card,),
        ("demo.search",),
        ("demo.research.planner",),
        ("demo.research.policy",),
        ("demo.research.executor",),
        ("demo.research.verifier",),
        ("demo.research_report",),
    )
    state = AgentState("sess", "run", RequestEnvelope("req", "research this", "."), mode="OBSERVE")

    prompt = PromptBuilder().build(
        state,
        ContextSelector().select(state, resolved, pre_turn_mode="OBSERVE"),
    )

    assert prompt["skills"][0]["instructions"] == (
        "Use the skill prompt as the domain adapter, not as a replacement for the agent loop.",
        "Rehydrate exact evidence before citing or writing final claims.",
    )
