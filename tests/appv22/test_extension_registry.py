import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.2"))

from appv22.extensions.base import RuntimeExtension, SkillCard
from appv22.extensions.file_management.extension import FileManagementExtension
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


class ShutdownRecordingExtension(DemoExtension):
    extension_id = "shutdown_recording"

    def __init__(self):
        self.events = []

    def session_shutdown(self, event):
        self.events.append(event)


class ShutdownRaisingExtension(DemoExtension):
    extension_id = "shutdown_raising"

    def session_shutdown(self, _event):
        raise RuntimeError("shutdown hook failed")


class StartRecordingExtension(DemoExtension):
    extension_id = "start_recording"

    def __init__(self):
        self.events = []

    def session_start(self, event):
        self.events.append(event)


class StartRaisingExtension(DemoExtension):
    extension_id = "start_raising"

    def session_start(self, _event):
        raise RuntimeError("session start hook failed")


class ResourcesRecordingExtension(DemoExtension):
    extension_id = "resources_recording"

    def __init__(self):
        self.events = []

    def resources_discover(self, event):
        self.events.append(event)
        return {
            "skillPaths": ["skills/runtime"],
            "promptPaths": ["prompts/runtime"],
            "themePaths": ["themes/runtime"],
        }


class ResourcesRaisingExtension(DemoExtension):
    extension_id = "resources_raising"

    def resources_discover(self, _event):
        raise RuntimeError("resources hook failed")


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


def test_file_management_extension_does_not_activate_tools_for_plain_greeting():
    registry = ExtensionRegistry()
    registry.register(FileManagementExtension())
    state = AgentState("sess", "run", RequestEnvelope("req", "hi", ".", active_user_request="hi"))

    resolved = registry.resolve_active(state)

    assert resolved.extension_ids == ()
    assert resolved.skill_cards == ()
    assert resolved.tool_ids == ()


def test_file_management_extension_does_not_activate_from_ui_history_for_greeting():
    registry = ExtensionRegistry()
    registry.register(FileManagementExtension())
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope(
            "req",
            (
                "[COMPACTED UI CONTEXT]\n"
                "User previously analyzed src/agents and repository files.\n\n"
                "[RECENT UI TURNS]\n"
                "user: list src agents\n"
                "assistant: planner.py, reflection.py, facebook_surfer.py\n\n"
                "[CURRENT USER REQUEST]\n"
                "hi"
            ),
            ".",
            active_user_request="hi",
            ui_context={
                "conversation_summary": "Earlier session analyzed repo, src, codebase, files, and planner.py.",
                "metrics": {"compaction_count": 1, "hot_lines": 6},
            },
        ),
    )

    resolved = registry.resolve_active(state)

    assert resolved.extension_ids == ()
    assert resolved.skill_cards == ()
    assert resolved.tool_ids == ()


def test_file_management_extension_activates_code_tools_for_src_analysis_request():
    registry = ExtensionRegistry()
    registry.register(FileManagementExtension())
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope("req", "analyze src", ".", active_user_request="analyze src"),
    )

    resolved = registry.resolve_active(state)

    skill_ids = [card.skill_id for card in resolved.skill_cards]
    assert "file_management.code_search" in skill_ids
    assert "file_management.workspace_navigation" in skill_ids
    assert "file_management.read_file" in resolved.tool_ids
    assert "file_management.tree" in resolved.tool_ids


def test_file_management_extension_narrows_single_file_analysis_tool_surface():
    registry = ExtensionRegistry()
    registry.register(FileManagementExtension())
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope(
            "req",
            "analyze src/agents/planner.py design patterns",
            ".",
            active_user_request="analyze src/agents/planner.py design patterns",
        ),
    )

    resolved = registry.resolve_active(state)

    assert resolved.tool_ids == (
        "file_management.find_files",
        "file_management.read_file",
        "file_management.read_range",
    )
    assert "file_management.grep" not in resolved.tool_ids
    assert "file_management.read_many" not in resolved.tool_ids
    assert "file_management.repo_snapshot" not in resolved.tool_ids


def test_file_management_extension_activates_code_followup_from_prior_code_evidence():
    registry = ExtensionRegistry()
    registry.register(FileManagementExtension())
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope("req", "how many lines in that", ".", active_user_request="how many lines in that"),
    )
    state.world_refs["world://file_management.read_file/current"] = {
        "kind": "file_management.read_file",
        "arguments": {"path": "src/agents/facebook_surfer.py"},
        "summary": "file_management.read_file result",
    }

    resolved = registry.resolve_active(state)

    assert "file_management.code_search" in [card.skill_id for card in resolved.skill_cards]
    assert "file_management.read_file" in resolved.tool_ids


def test_file_management_extension_activates_navigation_for_current_source_shape_summary():
    registry = ExtensionRegistry()
    registry.register(FileManagementExtension())
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope(
            "req",
            "summarize current src shape in two bullets",
            ".",
            active_user_request="summarize current src shape in two bullets",
        ),
    )

    resolved = registry.resolve_active(state)

    assert "file_management.tree" in resolved.tool_ids
    assert "file_management.repo_snapshot" in resolved.tool_ids


def test_general_file_mutation_does_not_load_cleanup_record_skill_prompt():
    registry = ExtensionRegistry()
    registry.register(FileManagementExtension())
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope(
            "req",
            "fix appV2.2/appv22/context/prompt_builder.py",
            ".",
            active_user_request="fix appV2.2/appv22/context/prompt_builder.py",
        ),
    )

    resolved = registry.resolve_active(state)

    mutation_card = next(card for card in resolved.skill_cards if card.skill_id == "file_management.file_mutation")
    instruction_text = "\n".join(mutation_card.instructions)
    assert "docs/workspace_manifest.json" not in instruction_text
    assert "colliding basename" not in instruction_text
    assert "human-authored artifacts" not in instruction_text
    assert "machine/session traces" not in instruction_text


def test_cleanup_record_skill_prompt_is_selected_only_for_cleanup_record_requests():
    registry = ExtensionRegistry()
    registry.register(FileManagementExtension())
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope(
            "req",
            "clean the workspace and keep a manifest record of moves and deletions",
            ".",
            active_user_request="clean the workspace and keep a manifest record of moves and deletions",
        ),
    )

    resolved = registry.resolve_active(state)

    skill_ids = [card.skill_id for card in resolved.skill_cards]
    assert "file_management.file_mutation" in skill_ids
    assert "file_management.cleanup_record" in skill_ids
    cleanup_card = next(card for card in resolved.skill_cards if card.skill_id == "file_management.cleanup_record")
    instruction_text = "\n".join(cleanup_card.instructions)
    assert "docs/workspace_manifest.json" in instruction_text
    assert "colliding basename" in instruction_text


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


def test_extension_registry_dispatches_pi_style_session_shutdown_safely():
    registry = ExtensionRegistry()
    recording = ShutdownRecordingExtension()
    registry.register(ShutdownRaisingExtension())
    registry.register(recording)

    registry.session_shutdown(
        ("shutdown_raising", "shutdown_recording"),
        {"type": "session_shutdown", "reason": "quit", "targetSessionFile": "next.jsonl"},
    )

    assert recording.events == [
        {"type": "session_shutdown", "reason": "quit", "targetSessionFile": "next.jsonl"}
    ]


def test_extension_registry_dispatches_pi_style_session_start_safely():
    registry = ExtensionRegistry()
    recording = StartRecordingExtension()
    registry.register(StartRaisingExtension())
    registry.register(recording)

    registry.session_start(
        ("start_raising", "start_recording"),
        {"type": "session_start", "reason": "startup", "previousSessionFile": "old.jsonl"},
    )

    assert recording.events == [
        {"type": "session_start", "reason": "startup", "previousSessionFile": "old.jsonl"}
    ]


def test_extension_registry_discovers_pi_style_resources_safely():
    registry = ExtensionRegistry()
    recording = ResourcesRecordingExtension()
    registry.register(ResourcesRaisingExtension())
    registry.register(recording)

    result = registry.resources_discover(
        {"type": "resources_discover", "cwd": "/workspace", "reason": "startup"}
    )

    assert recording.events == [{"type": "resources_discover", "cwd": "/workspace", "reason": "startup"}]
    assert result == {
        "skillPaths": [{"path": "skills/runtime", "extensionId": "resources_recording"}],
        "promptPaths": [{"path": "prompts/runtime", "extensionId": "resources_recording"}],
        "themePaths": [{"path": "themes/runtime", "extensionId": "resources_recording"}],
    }
