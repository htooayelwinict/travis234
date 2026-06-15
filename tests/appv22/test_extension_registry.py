import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.2"))

from appv22.extensions.base import RuntimeExtension, SkillCard
from appv22.extensions.registry import ExtensionRegistry
from appv22.runtime.capabilities import CapabilityRegistry
from appv22.state.models import AgentState, RequestEnvelope


class DemoPlanner:
    capability_id = "demo.planner"


class DemoVerifier:
    capability_id = "demo.verifier"


class DemoMutationPolicy:
    capability_id = "demo.policy"


class DemoMutationExecutor:
    capability_id = "demo.executor"


class DemoExtension(RuntimeExtension):
    extension_id = "demo"

    def skill_cards(self):
        return [
            SkillCard(
                "demo.cleanup",
                "demo",
                ("clean",),
                ("START", "PLAN", "ACT"),
                "Demo",
                "demo.planner",
                "demo.policy",
                "demo.executor",
                "demo.verifier",
                ("demo.inspect",),
                ("demo.schema",),
            )
        ]

    def register_capabilities(self, capabilities: CapabilityRegistry):
        capabilities.register_planner("demo.planner", DemoPlanner())
        capabilities.register_mutation_policy("demo.policy", DemoMutationPolicy())
        capabilities.register_mutation_executor("demo.executor", DemoMutationExecutor())
        capabilities.register_verifier("demo.verifier", DemoVerifier())
        capabilities.register_artifact_schema("demo.schema", {"type": "object"})


class OtherExtension(RuntimeExtension):
    extension_id = "other"

    def skill_cards(self):
        return [
            SkillCard(
                "zeta",
                "other",
                ("clean",),
                ("START",),
                "Other Zeta",
                "other.planner",
                "other.policy",
                "other.executor",
                "other.verifier",
                (),
                (),
            ),
            SkillCard(
                "alpha",
                "other",
                ("clean",),
                ("START",),
                "Other Alpha",
                "other.planner",
                "other.policy",
                "other.executor",
                "other.verifier",
                (),
                (),
            ),
        ]


class MismatchedSkillExtension(RuntimeExtension):
    extension_id = "registered"

    def skill_cards(self):
        return [
            SkillCard(
                "cleanup",
                "reported",
                ("clean",),
                ("START",),
                "Mismatched",
                "registered.planner",
                "registered.policy",
                "registered.executor",
                "registered.verifier",
                (),
                (),
            )
        ]


class DuplicateSkillExtension(RuntimeExtension):
    extension_id = "duplicate"

    def skill_cards(self):
        return [
            SkillCard(
                "cleanup",
                "duplicate",
                ("clean",),
                ("START",),
                "Duplicate A",
                "duplicate.planner",
                "duplicate.policy",
                "duplicate.executor",
                "duplicate.verifier",
                (),
                (),
            ),
            SkillCard(
                "cleanup",
                "duplicate",
                ("clean",),
                ("START",),
                "Duplicate B",
                "duplicate.planner",
                "duplicate.policy",
                "duplicate.executor",
                "duplicate.verifier",
                (),
                (),
            ),
        ]


def test_extension_resolution_links_skill_to_capabilities():
    registry = ExtensionRegistry()
    capabilities = CapabilityRegistry()
    extension = DemoExtension()
    registry.register(extension)
    extension.register_capabilities(capabilities)
    state = AgentState("sess", "run", RequestEnvelope("req", "please clean", "."))

    resolved = registry.resolve_active(state)

    assert resolved.extension_ids == ("demo",)
    assert resolved.planner_ids == ("demo.planner",)
    assert resolved.mutation_policy_ids == ("demo.policy",)
    assert resolved.mutation_executor_ids == ("demo.executor",)
    assert resolved.verifier_ids == ("demo.verifier",)
    assert resolved.artifact_schema_ids == ("demo.schema",)
    assert capabilities.planner("demo.planner").capability_id == "demo.planner"
    assert capabilities.mutation_policy("demo.policy").capability_id == "demo.policy"
    assert capabilities.mutation_executor("demo.executor").capability_id == "demo.executor"
    assert capabilities.verifier("demo.verifier").capability_id == "demo.verifier"
    assert capabilities.artifact_schema("demo.schema") == {"type": "object"}


def test_extension_resolution_ignores_inactive_skills():
    registry = ExtensionRegistry()
    registry.register(DemoExtension())
    state = AgentState("sess", "run", RequestEnvelope("req", "leave it alone", "."))

    resolved = registry.resolve_active(state)

    assert resolved.extension_ids == ()
    assert resolved.skill_cards == ()
    assert resolved.planner_ids == ()


def test_resolved_extensions_are_immutable_tuples():
    registry = ExtensionRegistry()
    registry.register(DemoExtension())
    state = AgentState("sess", "run", RequestEnvelope("req", "please clean", "."))

    resolved = registry.resolve_active(state)

    assert isinstance(resolved.extension_ids, tuple)
    assert isinstance(resolved.skill_cards, tuple)
    assert isinstance(resolved.tool_ids, tuple)
    assert isinstance(resolved.planner_ids, tuple)
    assert isinstance(resolved.mutation_policy_ids, tuple)
    assert isinstance(resolved.mutation_executor_ids, tuple)
    assert isinstance(resolved.verifier_ids, tuple)
    assert isinstance(resolved.artifact_schema_ids, tuple)


def test_skill_card_normalizes_collection_fields_to_immutable_tuples():
    triggers = ["clean"]
    modes = ["START"]
    tool_ids = ["demo.inspect"]
    artifact_schema_ids = ["demo.schema"]

    card = SkillCard(
        "demo.cleanup",
        "demo",
        triggers,
        modes,
        "Demo",
        "demo.planner",
        "demo.policy",
        "demo.executor",
        "demo.verifier",
        tool_ids,
        artifact_schema_ids,
    )
    triggers.append("mutated")
    modes.append("ACT")
    tool_ids.append("demo.mutate")
    artifact_schema_ids.append("demo.changed")

    assert card.triggers == ("clean",)
    assert card.modes == ("START",)
    assert card.tool_ids == ("demo.inspect",)
    assert card.artifact_schema_ids == ("demo.schema",)


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


def test_capability_registry_rejects_duplicate_capability_ids():
    capabilities = CapabilityRegistry()
    capabilities.register_planner("cap", object())
    capabilities.register_mutation_policy("cap", object())
    capabilities.register_mutation_executor("cap", object())
    capabilities.register_verifier("cap", object())
    capabilities.register_artifact_schema("cap", {"type": "object"})

    with pytest.raises(ValueError, match="duplicate capability_id: cap"):
        capabilities.register_planner("cap", object())
    with pytest.raises(ValueError, match="duplicate capability_id: cap"):
        capabilities.register_mutation_policy("cap", object())
    with pytest.raises(ValueError, match="duplicate capability_id: cap"):
        capabilities.register_mutation_executor("cap", object())
    with pytest.raises(ValueError, match="duplicate capability_id: cap"):
        capabilities.register_verifier("cap", object())
    with pytest.raises(ValueError, match="duplicate capability_id: cap"):
        capabilities.register_artifact_schema("cap", {"type": "object"})


def test_artifact_schemas_are_copied_on_register_and_return():
    capabilities = CapabilityRegistry()
    schema = {"type": "object", "properties": {"name": {"type": "string"}}}
    capabilities.register_artifact_schema("schema", schema)

    schema["properties"]["name"]["type"] = "integer"
    returned = capabilities.artifact_schema("schema")
    returned["properties"]["name"]["type"] = "boolean"

    assert capabilities.artifact_schema("schema") == {
        "type": "object",
        "properties": {"name": {"type": "string"}},
    }
