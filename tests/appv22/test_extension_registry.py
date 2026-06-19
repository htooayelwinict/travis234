import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.2"))

from appv22.extensions.base import RuntimeExtension, SkillCard
from appv22.extensions.file_management.skills import FILE_MANAGEMENT_SKILL
from appv22.extensions.registry import ExtensionRegistry
from appv22.state.models import AgentState, RequestEnvelope


class DemoExtension(RuntimeExtension):
    extension_id = "demo"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="demo.cleanup",
                extension_id="demo",
                triggers=("clean",),
                modes=("START", "THINK", "ACT"),
                summary="Demo",
                tool_ids=("demo.inspect",),
            )
        ]


class OtherExtension(RuntimeExtension):
    extension_id = "other"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="zeta",
                extension_id="other",
                triggers=("clean",),
                modes=("START",),
                summary="Other Zeta",
                tool_ids=(),
            ),
            SkillCard(
                skill_id="alpha",
                extension_id="other",
                triggers=("clean",),
                modes=("START",),
                summary="Other Alpha",
                tool_ids=(),
            ),
        ]


class MismatchedSkillExtension(RuntimeExtension):
    extension_id = "registered"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="cleanup",
                extension_id="reported",
                triggers=("clean",),
                modes=("START",),
                summary="Mismatched",
                tool_ids=(),
            )
        ]


class DuplicateSkillExtension(RuntimeExtension):
    extension_id = "duplicate"

    def skill_cards(self):
        return [
            SkillCard(
                skill_id="cleanup",
                extension_id="duplicate",
                triggers=("clean",),
                modes=("START",),
                summary="Duplicate A",
                tool_ids=(),
            ),
            SkillCard(
                skill_id="cleanup",
                extension_id="duplicate",
                triggers=("clean",),
                modes=("START",),
                summary="Duplicate B",
                tool_ids=(),
            ),
        ]


def test_extension_resolution_links_skill_to_prompt_visible_tools():
    registry = ExtensionRegistry()
    extension = DemoExtension()
    registry.register(extension)
    state = AgentState("sess", "run", RequestEnvelope("req", "please clean", "."))

    resolved = registry.resolve_active(state)

    assert resolved.extension_ids == ("demo",)
    assert resolved.tool_ids == ("demo.inspect",)


def test_extension_resolution_ignores_inactive_skills():
    registry = ExtensionRegistry()
    registry.register(DemoExtension())
    state = AgentState("sess", "run", RequestEnvelope("req", "leave it alone", "."))

    resolved = registry.resolve_active(state)

    assert resolved.extension_ids == ()
    assert resolved.skill_cards == ()


def test_extension_activation_uses_bounded_reference_context_without_replacing_latest_request():
    registry = ExtensionRegistry()
    registry.register(DemoExtension())
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope(
            "req",
            "[RECENT UI TURNS]\nuser: please clean src\nassistant: listed src files\n\n[CURRENT USER REQUEST]\nthat one",
            ".",
            active_user_request="that one",
            ui_context={"conversation_summary": "User is working on cleanup context.", "metrics": {"hot_lines": 2}},
        ),
    )

    resolved = registry.resolve_active(state)

    assert resolved.extension_ids == ("demo",)
    assert resolved.tool_ids == ("demo.inspect",)


def test_resolved_extensions_are_immutable_tuples():
    registry = ExtensionRegistry()
    registry.register(DemoExtension())
    state = AgentState("sess", "run", RequestEnvelope("req", "please clean", "."))

    resolved = registry.resolve_active(state)

    assert isinstance(resolved.extension_ids, tuple)
    assert isinstance(resolved.skill_cards, tuple)
    assert isinstance(resolved.tool_ids, tuple)


def test_skill_card_normalizes_collection_fields_to_immutable_tuples():
    triggers = ["clean"]
    modes = ["START"]
    tool_ids = ["demo.inspect"]

    card = SkillCard(
        skill_id="demo.cleanup",
        extension_id="demo",
        triggers=triggers,
        modes=modes,
        summary="Demo",
        tool_ids=tool_ids,
    )
    triggers.append("mutated")
    modes.append("ACT")
    tool_ids.append("demo.mutate")

    assert card.triggers == ("clean",)
    assert card.modes == ("START",)
    assert card.tool_ids == ("demo.inspect",)


def test_file_management_skill_declares_observation_contract() -> None:
    contract = FILE_MANAGEMENT_SKILL.observation_contract

    assert contract is not None
    assert contract.evidence_refs == ("world://file_management.repo_snapshot/latest",)
    assert contract.evidence_kinds == ("file_management.repo_snapshot",)
    assert contract.preferred_tool_id == "file_management.repo_snapshot"


def test_extension_registry_rejects_duplicate_extension_ids():
    registry = ExtensionRegistry()
    registry.register(DemoExtension())

    with pytest.raises(ValueError, match="duplicate extension_id: demo"):
        registry.register(DemoExtension())


def test_skill_cards_resolve_in_deterministic_order():
    registry = ExtensionRegistry()
    registry.register(OtherExtension())
    registry.register(DemoExtension())
    state = AgentState("sess", "run", RequestEnvelope("req", "please clean", "."))

    resolved = registry.resolve_active(state)

    assert [(card.extension_id, card.skill_id) for card in resolved.skill_cards] == [
        ("demo", "demo.cleanup"),
        ("other", "alpha"),
        ("other", "zeta"),
    ]


def test_extension_registry_rejects_mismatched_active_skill_extension_id():
    registry = ExtensionRegistry()
    registry.register(MismatchedSkillExtension())
    state = AgentState("sess", "run", RequestEnvelope("req", "please clean", "."))

    with pytest.raises(
        ValueError,
        match="skill card extension_id mismatch: registered extension registered returned reported",
    ):
        registry.resolve_active(state)


def test_extension_registry_rejects_duplicate_active_skill_cards():
    registry = ExtensionRegistry()
    registry.register(DuplicateSkillExtension())
    state = AgentState("sess", "run", RequestEnvelope("req", "please clean", "."))

    with pytest.raises(ValueError, match="duplicate active skill card: duplicate/cleanup"):
        registry.resolve_active(state)
