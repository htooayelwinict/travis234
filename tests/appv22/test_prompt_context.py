from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "appV2.2"))

from appv22.context.prompt_builder import PromptBuilder
from appv22.context.selector import ContextSelector
from appv22.extensions.base import SkillCard
from appv22.extensions.registry import ResolvedExtensions
from appv22.state.models import AgentState, RequestEnvelope
from appv22.tools.definitions import ToolDefinition
from appv22.tools.registry import ToolRegistry


def _card(skill_id: str, modes: tuple[str, ...], tool_ids: tuple[str, ...]) -> SkillCard:
    return SkillCard(
        skill_id=skill_id,
        extension_id="demo",
        triggers=("clean",),
        modes=modes,
        summary=f"{skill_id} summary",
        tool_ids=tool_ids,
    )


def _resolved() -> ResolvedExtensions:
    think_card = _card("demo.think_skill", ("THINK",), ("demo.think_tool",))
    observe_card = _card("demo.observe_skill", ("START", "THINK", "OBSERVE", "VERIFY"), ("demo.inspect",))
    return ResolvedExtensions(
        ("demo",),
        (think_card, observe_card),
        ("demo.inspect", "demo.think_tool"),
    )


def test_prompt_uses_pre_turn_mode_and_exposes_selected_skill_tools_in_think():
    state = AgentState("sess", "run", RequestEnvelope("req", "clean this", "."), mode="THINK")
    selected = ContextSelector().select(state, _resolved(), pre_turn_mode="THINK")
    prompt = PromptBuilder().build(state, selected)

    assert prompt["system"]["identity"] == "AppV2.2 Pi-Hermes coding agent"
    assert any("Pi-style coding-agent harness" in rule for rule in prompt["system"]["agent_loop_contract"])
    assert any("obsolete, fake, stale, or do-not-use" in rule for rule in prompt["system"]["agent_loop_contract"])
    assert any("context_summary.evidence_refs" in rule for rule in prompt["system"]["dual_context_contract"])
    assert any("exact world_ref" in rule for rule in prompt["system"]["dual_context_contract"])
    assert any("structured evidence_refs array" in rule for rule in prompt["system"]["dual_context_contract"])
    assert any("Do not repeat broad observation" in rule for rule in prompt["system"]["dual_context_contract"])
    assert any("selected_tools" in rule for rule in prompt["system"]["tool_contract"])
    assert any("do not emit compact" in rule and "selected tool" in rule for rule in prompt["system"]["tool_contract"])
    assert any("supersedes earlier user or skill instructions" in rule for rule in prompt["system"]["tool_contract"])
    assert any("finalize, pause, or compact is invalid" in rule for rule in prompt["system"]["tool_contract"])
    assert any("pause is invalid" in rule and "read-only" in rule for rule in prompt["system"]["tool_contract"])
    assert any("planning or requesting a tool" in rule and "tool_call" in rule for rule in prompt["system"]["tool_contract"])
    assert any("Repeated read-only tool calls" in rule and "selected action tool" in rule for rule in prompt["system"]["tool_contract"])
    assert any("required argument" in rule and "schema" in rule for rule in prompt["system"]["tool_contract"])
    assert any("tool_call for actions" in rule for rule in prompt["agent"]["mode_contract"])
    assert prompt["agent"]["mode"] == "THINK"
    assert prompt["selection"]["selected_tools"] == ["demo.think_tool", "demo.inspect"]
    assert prompt["selection"]["available_tools"] == ["demo.think_tool", "demo.inspect"]
    assert prompt["tools"] == ["demo.think_tool", "demo.inspect"]


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


def test_repeated_observe_results_do_not_shrink_selected_tool_surface():
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            "demo.inspect",
            "observe",
            "low",
            {"type": "object", "properties": {}},
            {"type": "object", "properties": {}},
            "test",
            "test",
        ),
        lambda _args, _context: {"status": "completed"},
    )
    state = AgentState("sess", "run", RequestEnvelope("req", "inspect again", "."), mode="OBSERVE")
    state.tool_results = {
        "toolres_1": {
            "tool_result_id": "toolres_1",
            "tool_id": "demo.inspect",
            "status": "completed",
            "arguments": {"path": "src"},
        },
        "toolres_2": {
            "tool_result_id": "toolres_2",
            "tool_id": "demo.inspect",
            "status": "completed",
            "arguments": {"path": "src"},
        },
    }

    selected = ContextSelector(tool_registry=registry).select(state, _resolved(), pre_turn_mode="OBSERVE")

    assert selected["selection"]["selected_tools"] == ["demo.inspect"]
    assert selected["selection"]["available_tools"] == ["demo.inspect"]
    assert selected["tools"] == ["demo.inspect"]


def test_read_mode_tool_order_is_deterministic_and_scoped_to_selected_skill_cards():
    observe_first = _card("demo.observe_first", ("OBSERVE",), ("demo.inspect_b", "demo.inspect_a"))
    think_card = _card("demo.think_skill", ("THINK",), ("demo.think_tool",))
    observe_second = _card("demo.observe_second", ("OBSERVE",), ("demo.inspect_c",))
    resolved = ResolvedExtensions(
        ("demo",),
        (observe_first, think_card, observe_second),
        ("demo.inspect_c", "demo.think_tool", "demo.inspect_a", "demo.inspect_b"),
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


def test_prompt_includes_state_world_refs_and_metadata_without_mutability_leaks():
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope("req", "clean this", ".", constraints=["stay safe"]),
        mode="VERIFY",
    )
    state.world_refs["world://repo_snapshot/latest"] = {"summary": "snapshot"}
    state.context_summary["evidence_refs"] = ["world://repo_snapshot/latest"]
    state.context_summary["progress"] = ["repo snapshot evidence is available"]

    selected = ContextSelector().select(state, _resolved(), pre_turn_mode="VERIFY")
    prompt = PromptBuilder().build(state, selected)
    state.world_refs["world://repo_snapshot/latest"]["summary"] = "mutated"
    selected["state"]["context_summary"]["progress"].append("mutated")

    assert prompt["agent"]["constraints"] == ["stay safe"]
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
        tool_ids=("demo.search",),
        instructions=(
            "Use the skill prompt as the domain adapter, not as a replacement for the agent loop.",
            "Rehydrate exact evidence before citing or writing final claims.",
        ),
    )
    resolved = ResolvedExtensions(
        ("demo",),
        (card,),
        ("demo.search",),
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


def test_file_management_skill_prompt_guides_vague_organization_without_planner():
    from appv22.extensions.file_management.skills import FILE_MANAGEMENT_SKILL

    instruction_text = "\n".join(FILE_MANAGEMENT_SKILL.instructions)

    assert "file-content cues" in instruction_text
    assert "colliding basename" in instruction_text
    assert "first clear colliding source claims the common destination" in instruction_text
    assert "later colliding sources" in instruction_text
    assert "docs/workspace_manifest.json" in instruction_text
    assert "human-authored artifacts" in instruction_text
    assert "machine/session traces" in instruction_text
    assert "prefer move_file" in instruction_text
    assert "remove obvious junk" in instruction_text
    assert "file_management.delete_file" in instruction_text
    assert "deletions" in instruction_text
    assert "Do not emit finalize" in instruction_text
    assert "deterministic" not in instruction_text.lower()
