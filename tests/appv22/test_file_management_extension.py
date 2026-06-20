from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.2"))

from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.extensions.registry import ExtensionRegistry
from appv22.runtime.services import create_appv22_services
from appv22.state.models import AgentState, RequestEnvelope


class NullProvider:
    provider_id = "null"


def _services(tmp_path):
    return create_appv22_services(
        root_path=tmp_path,
        provider=NullProvider(),
        extensions=[FileManagementExtension()],
    )


def test_file_management_extension_resolves_skill_and_tools(tmp_path):
    registry = ExtensionRegistry()
    registry.register(FileManagementExtension())
    state = AgentState("sess", "run", RequestEnvelope("req", "create a handoff file", str(tmp_path)))

    resolved = registry.resolve_active(state)

    assert resolved.extension_ids == ("file_management",)
    assert "file_management.file_mutation" in [card.skill_id for card in resolved.skill_cards]
    assert set(resolved.tool_ids) == {
        "file_management.copy_file",
        "file_management.delete_file",
        "file_management.edit_file",
        "file_management.mkdir",
        "file_management.move_file",
        "file_management.read_file",
        "file_management.repo_snapshot",
        "file_management.write_file",
    }
    mutation_card = next(card for card in resolved.skill_cards if card.skill_id == "file_management.file_mutation")
    assert mutation_card.observation_contract.preferred_tool_id == "file_management.repo_snapshot"


def test_repo_snapshot_lists_files_and_safe_text_previews(tmp_path):
    (tmp_path / "README.md").write_text("# Workspace\n", encoding="utf-8")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "brief.md").write_text("brief content", encoding="utf-8")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "prod.env").write_text("TOKEN=secret", encoding="utf-8")
    services = _services(tmp_path)

    result = services.broker.execute(
        "file_management.repo_snapshot",
        {},
        active_tool_ids=("file_management.repo_snapshot",),
    )

    assert result["status"] == "completed"
    assert result["payload_ref"] == "world://file_management.repo_snapshot/latest"
    assert "README.md" in result["payload"]["files"]
    assert "notes/brief.md" in result["payload"]["files"]
    assert result["payload"]["text_previews"]["notes/brief.md"] == "brief content"
    assert "secrets/prod.env" not in result["payload"]["text_previews"]


def test_read_file_denies_absolute_outside_and_protected_paths(tmp_path):
    (tmp_path / "notes.md").write_text("hello", encoding="utf-8")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets" / "prod.env").write_text("TOKEN=secret", encoding="utf-8")
    services = _services(tmp_path)
    active = ("file_management.read_file",)

    ok = services.broker.execute("file_management.read_file", {"path": "notes.md"}, active_tool_ids=active)
    absolute = services.broker.execute("file_management.read_file", {"path": str(tmp_path / "notes.md")}, active_tool_ids=active)
    outside = services.broker.execute("file_management.read_file", {"path": "../outside.md"}, active_tool_ids=active)
    protected = services.broker.execute("file_management.read_file", {"path": "secrets/prod.env"}, active_tool_ids=active)

    assert ok["status"] == "completed"
    assert ok["payload"]["content"] == "hello"
    assert absolute["status"] == "denied"
    assert outside["status"] == "denied"
    assert protected["status"] == "denied"


def test_write_file_is_explicit_tool_action_and_creates_parent_dirs(tmp_path):
    services = _services(tmp_path)

    result = services.broker.execute(
        "file_management.write_file",
        {"path": "docs/handoff.md", "content": "handoff notes\n"},
        active_tool_ids=("file_management.write_file",),
    )

    assert result["status"] == "completed"
    assert result["payload"]["path"] == "docs/handoff.md"
    assert result["payload"]["overwritten"] is False
    assert (tmp_path / "docs" / "handoff.md").read_text(encoding="utf-8") == "handoff notes\n"


def test_write_file_denies_existing_file_without_explicit_overwrite(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "existing.md").write_text("original\n", encoding="utf-8")
    services = _services(tmp_path)

    denied = services.broker.execute(
        "file_management.write_file",
        {"path": "docs/existing.md", "content": "changed\n"},
        active_tool_ids=("file_management.write_file",),
    )
    overwritten = services.broker.execute(
        "file_management.write_file",
        {"path": "docs/existing.md", "content": "changed\n", "overwrite": True},
        active_tool_ids=("file_management.write_file",),
    )

    assert denied["status"] == "denied"
    assert denied["payload"]["errors"] == ["existing_file_requires_overwrite:docs/existing.md"]
    assert denied["payload"]["suggested_path"] == "docs/existing-1.md"
    assert overwritten["status"] == "completed"
    assert overwritten["payload"]["overwritten"] is True
    assert (tmp_path / "docs" / "existing.md").read_text(encoding="utf-8") == "changed\n"


def test_edit_file_applies_unique_targeted_replacement_to_existing_file(tmp_path):
    (tmp_path / "docs").mkdir()
    target = tmp_path / "docs" / "existing.md"
    target.write_text("owner: old\nstatus: pending\n", encoding="utf-8")
    services = _services(tmp_path)

    result = services.broker.execute(
        "file_management.edit_file",
        {
            "path": "docs/existing.md",
            "edits": [{"oldText": "owner: old", "newText": "owner: new"}],
        },
        active_tool_ids=("file_management.edit_file",),
    )

    assert result["status"] == "completed"
    assert result["payload"]["path"] == "docs/existing.md"
    assert result["payload"]["edits_applied"] == 1
    assert result["payload"]["first_changed_line"] == 1
    assert "-owner: old" in result["payload"]["diff"]
    assert "+owner: new" in result["payload"]["diff"]
    assert target.read_text(encoding="utf-8") == "owner: new\nstatus: pending\n"


def test_edit_file_schema_matches_pi_strict_replacement_contract(tmp_path):
    services = _services(tmp_path)

    definition = services.tool_registry.definition("file_management.edit_file")
    assert definition is not None
    schema = definition.argument_schema

    assert schema["additionalProperties"] is False
    edits = schema["properties"]["edits"]
    assert edits["minItems"] == 1
    assert edits["items"]["required"] == ("oldText", "newText")
    assert edits["items"]["additionalProperties"] is False
    assert tuple(edits["items"]["properties"].keys()) == ("oldText", "newText")


def test_edit_file_accepts_pi_legacy_top_level_old_new_text(tmp_path):
    (tmp_path / "docs").mkdir()
    target = tmp_path / "docs" / "existing.md"
    target.write_text("owner: old\nstatus: pending\n", encoding="utf-8")
    services = _services(tmp_path)

    result = services.broker.execute(
        "file_management.edit_file",
        {
            "path": "docs/existing.md",
            "oldText": "owner: old",
            "newText": "owner: new",
        },
        active_tool_ids=("file_management.edit_file",),
    )

    assert result["status"] == "completed"
    assert result["payload"]["edits_applied"] == 1
    assert target.read_text(encoding="utf-8") == "owner: new\nstatus: pending\n"


def test_edit_file_prepare_arguments_accepts_flat_key_value_edit_array_from_model(tmp_path):
    (tmp_path / "notes").mkdir()
    target = tmp_path / "notes" / "alpha.txt"
    target.write_text(
        "Pi hooks allow intercepting agent decisions.\n"
        "They enable custom validation and logging.\n"
        "Hooks run seamlessly within the Pi-style loop.\n",
        encoding="utf-8",
    )
    services = _services(tmp_path)

    result = services.broker.execute(
        "file_management.edit_file",
        {
            "path": "notes/alpha.txt",
            "edits": [
                "oldText",
                "They enable custom validation and logging.",
                "newText",
                "They enable extension lifecycle checks.",
            ],
        },
        active_tool_ids=("file_management.edit_file",),
    )

    assert result["status"] == "completed"
    assert result["payload"]["edits_applied"] == 1
    assert "They enable extension lifecycle checks." in target.read_text(encoding="utf-8")


def test_file_mutation_skill_guides_line_changes_without_placeholder_edits():
    registry = ExtensionRegistry()
    registry.register(FileManagementExtension())
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope("req", "change the second line in notes/alpha.txt", "."),
    )

    resolved = registry.resolve_active(state)

    mutation_card = next(card for card in resolved.skill_cards if card.skill_id == "file_management.file_mutation")
    instructions = "\n".join(mutation_card.instructions)
    assert "line-based" in instructions
    assert '{"oldText":"exact line or block from read_file"' in instructions
    assert "Do not send placeholder strings" in instructions


def test_edit_file_denies_non_unique_old_text_without_writing(tmp_path):
    (tmp_path / "docs").mkdir()
    target = tmp_path / "docs" / "existing.md"
    target.write_text("todo\nkeep\ntodo\n", encoding="utf-8")
    services = _services(tmp_path)

    result = services.broker.execute(
        "file_management.edit_file",
        {"path": "docs/existing.md", "edits": [{"oldText": "todo", "newText": "done"}]},
        active_tool_ids=("file_management.edit_file",),
    )

    assert result["status"] == "denied"
    assert result["payload"]["errors"] == ["old_text_not_unique:0"]
    assert target.read_text(encoding="utf-8") == "todo\nkeep\ntodo\n"


def test_file_management_extension_provides_denial_recovery_guidance():
    extension = FileManagementExtension()
    guidance = extension.tool_result_guidance(
        {
            "tool_id": "file_management.write_file",
            "status": "denied",
            "payload": {
                "path": "docs/existing.md",
                "suggested_path": "docs/existing-1.md",
                "errors": ["existing_file_requires_overwrite:docs/existing.md"],
            },
        }
    )

    assert "docs/existing-1.md" in guidance
    assert "file_management.write_file" in guidance


def test_file_management_finalize_guidance_requires_write_after_source_reads_for_file_creation_goal():
    extension = FileManagementExtension()
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope(
            "req",
            "Make one concise handoff file for the next operator. Preserve exact codes, owners, dates, and the escalation rule.",
            ".",
        ),
    )
    state.tool_results["read_operator"] = {
        "tool_id": "file_management.read_file",
        "status": "completed",
        "payload": {"path": "docs/operator-brief.md", "content": "Bridge code ORCHID-77-BRIDGE belongs to owner Mira Chen."},
    }
    state.tool_results["read_ferry"] = {
        "tool_id": "file_management.read_file",
        "status": "completed",
        "payload": {"path": "notes/ferry-window.txt", "content": "Ferry code NEON-42-FERRY belongs to owner Pavel Ortiz."},
    }

    guidance = extension.finalize_guidance(state)

    assert "the next decision must be a tool_call to file_management.write_file" in guidance
    assert "docs/handoff.md" in guidance
    assert "docs/operator-brief.md" in guidance
    assert "notes/ferry-window.txt" in guidance


def test_file_management_finalize_guidance_does_not_hardcode_handoff_for_manifest_write():
    extension = FileManagementExtension()
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope(
            "req",
            "write docs/workspace_manifest.json recording recovery_notes.md as source and recovery_notes_archive.md as archive",
            ".",
        ),
    )
    state.tool_results["read_source"] = {
        "tool_id": "file_management.read_file",
        "status": "completed",
        "payload": {"path": "docs/recovery_notes.md", "content": "# Recovery Notes\n"},
    }

    guidance = extension.finalize_guidance(state)

    assert "file_management.write_file" in guidance
    assert "docs/handoff.md" not in guidance


def test_file_management_guides_malformed_edit_arguments_without_cleanup_domain_prompt():
    result = {
        "tool_id": "file_management.edit_file",
        "status": "denied",
        "arguments": {"path": "src/agents/planner.py", "edits": ["oldText", "newText"]},
        "payload": {
            "path": "src/agents/planner.py",
            "errors": ["invalid_edit:0", "invalid_edit:1"],
        },
    }

    guidance = FileManagementExtension().tool_result_guidance(result)

    assert "file_management.edit_file" in guidance
    assert "edits" in guidance
    assert "array of objects" in guidance
    assert "oldText" in guidance
    assert "newText" in guidance
    assert "corrected arguments" in guidance
    assert "docs/workspace_manifest.json" not in guidance


def test_file_management_denies_excessive_same_file_read_range_for_read_only_request():
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope(
            "req",
            "what functions in src/agents/planner.py connect to qdrant?",
            ".",
            active_user_request="what functions in src/agents/planner.py connect to qdrant?",
        ),
    )
    state.tool_results = {
        f"toolres_{index}": {
            "tool_result_id": f"toolres_{index}",
            "tool_id": "file_management.read_range",
            "status": "completed",
            "arguments": {"path": "src/agents/planner.py", "start_line": 100 + index, "end_line": 130 + index},
            "payload": {"path": "src/agents/planner.py", "content": "evidence"},
        }
        for index in range(3)
    }

    result = FileManagementExtension().before_tool_call(
        state,
        "file_management.read_range",
        {"path": "src/agents/planner.py", "start_line": 120, "end_line": 150},
    )

    assert result is not None
    assert result["reason"] == "redundant_read_range_budget"
    guidance = FileManagementExtension().tool_result_guidance(
        {
            "tool_id": "file_management.read_range",
            "status": "denied",
            "payload": result["payload"] | {"errors": result["errors"]},
        }
    )
    assert "finalize from existing evidence_refs" in guidance


def test_file_management_allows_repeated_read_range_for_mutation_request():
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope(
            "req",
            "edit src/agents/planner.py to add logging",
            ".",
            active_user_request="edit src/agents/planner.py to add logging",
        ),
    )
    state.tool_results = {
        f"toolres_{index}": {
            "tool_result_id": f"toolres_{index}",
            "tool_id": "file_management.read_range",
            "status": "completed",
            "arguments": {"path": "src/agents/planner.py", "start_line": 100 + index, "end_line": 130 + index},
            "payload": {"path": "src/agents/planner.py", "content": "evidence"},
        }
        for index in range(3)
    }

    result = FileManagementExtension().before_tool_call(
        state,
        "file_management.read_range",
        {"path": "src/agents/planner.py", "start_line": 120, "end_line": 150},
    )

    assert result is None


def test_file_management_finalize_guidance_prefers_edit_after_source_read_for_existing_file_fix():
    extension = FileManagementExtension()
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope(
            "req",
            "Fix the status line in docs/existing.md from pending to done.",
            ".",
        ),
    )
    state.tool_results["read_existing"] = {
        "tool_id": "file_management.read_file",
        "status": "completed",
        "payload": {"path": "docs/existing.md", "content": "owner: new\nstatus: pending\n"},
    }

    guidance = extension.finalize_guidance(state)

    assert "the next decision must be a tool_call to file_management.edit_file" in guidance
    assert "docs/existing.md" in guidance
    assert "file_management.write_file" not in guidance


def test_file_management_finalize_guidance_accepts_completed_edit_for_existing_file_fix():
    extension = FileManagementExtension()
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope(
            "req",
            "Fix the status line in docs/existing.md from pending to done.",
            ".",
        ),
    )
    state.tool_results["edit_existing"] = {
        "tool_id": "file_management.edit_file",
        "status": "completed",
        "payload": {"path": "docs/existing.md", "edits_applied": 1, "errors": []},
    }

    guidance = extension.finalize_guidance(state)

    assert guidance == ""


def test_file_management_finalize_guidance_does_not_treat_no_sibling_file_as_creation_request():
    extension = FileManagementExtension()
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope(
            "req",
            "Change priority from high to urgent in the same notes file. Use the existing file and do not create a sibling file.",
            ".",
        ),
    )
    state.tool_results["read_existing"] = {
        "tool_id": "file_management.read_file",
        "status": "completed",
        "payload": {"path": "plan/appv22-live-scope/notes.md", "content": "**Priority**: high"},
    }
    state.tool_results["edit_existing"] = {
        "tool_id": "file_management.edit_file",
        "status": "completed",
        "payload": {"path": "plan/appv22-live-scope/notes.md", "edits_applied": 1, "errors": []},
    }

    guidance = extension.finalize_guidance(state)

    assert guidance == ""


def test_file_management_finalize_guidance_does_not_treat_retrospective_added_question_as_mutation():
    extension = FileManagementExtension()
    state = AgentState(
        "sess",
        "run",
        RequestEnvelope(
            "req",
            "Still no edits. Which helper was added most recently, and which file proves its tests exist?",
            ".",
        ),
    )
    state.tool_results["read_tests"] = {
        "tool_id": "file_management.read_file",
        "status": "completed",
        "payload": {"path": "tests/test_math_utils.py", "content": "def test_sign_label():\n    pass\n"},
    }

    guidance = extension.finalize_guidance(state)

    assert guidance == ""


def test_write_file_removes_obsolete_identifier_lines(tmp_path):
    services = _services(tmp_path)

    result = services.broker.execute(
        "file_management.write_file",
        {
            "path": "docs/handoff.md",
            "content": "Current code ORCHID-77-BRIDGE.\nObsolete codes excluded: ORCHID-17-BRIDGE\n",
        },
        active_tool_ids=("file_management.write_file",),
    )

    assert result["status"] == "completed"
    written = (tmp_path / "docs" / "handoff.md").read_text(encoding="utf-8")
    assert "ORCHID-77-BRIDGE" in written
    assert "ORCHID-17-BRIDGE" not in written


def test_write_file_removes_obsolete_identifier_section_bullets(tmp_path):
    services = _services(tmp_path)

    result = services.broker.execute(
        "file_management.write_file",
        {
            "path": "docs/handoff.md",
            "content": (
                "Current code ORCHID-77-BRIDGE.\n\n"
                "## Obsolete Codes (Do Not Use)\n"
                "- ORCHID-17-BRIDGE\n"
                "- NEON-24-FERRY\n"
            ),
        },
        active_tool_ids=("file_management.write_file",),
    )

    assert result["status"] == "completed"
    written = (tmp_path / "docs" / "handoff.md").read_text(encoding="utf-8")
    assert "ORCHID-77-BRIDGE" in written
    assert "ORCHID-17-BRIDGE" not in written
    assert "NEON-24-FERRY" not in written


def test_write_file_denies_paths_outside_workspace(tmp_path):
    services = _services(tmp_path)

    result = services.broker.execute(
        "file_management.write_file",
        {"path": "../escape.md", "content": "no"},
        active_tool_ids=("file_management.write_file",),
    )

    assert result["status"] == "denied"
    assert not (tmp_path.parent / "escape.md").exists()


def test_move_copy_delete_and_mkdir_are_explicit_file_tools(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "standup.md").write_text("standup\n", encoding="utf-8")
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / "spec.md").write_text("spec\n", encoding="utf-8")
    (tmp_path / "tmp").mkdir()
    (tmp_path / "tmp" / "old.log").write_text("old\n", encoding="utf-8")
    services = _services(tmp_path)
    active = (
        "file_management.move_file",
        "file_management.copy_file",
        "file_management.delete_file",
        "file_management.mkdir",
    )

    made = services.broker.execute("file_management.mkdir", {"path": "artifacts/logs"}, active_tool_ids=active)
    moved = services.broker.execute(
        "file_management.move_file",
        {"source": "notes/standup.md", "destination": "docs/standup.md"},
        active_tool_ids=active,
    )
    copied = services.broker.execute(
        "file_management.copy_file",
        {"source": "projects/spec.md", "destination": "docs/spec.md", "preserve_source": True},
        active_tool_ids=active,
    )
    deleted = services.broker.execute("file_management.delete_file", {"path": "tmp/old.log"}, active_tool_ids=active)

    assert made["status"] == "completed"
    assert moved["status"] == "completed"
    assert copied["status"] == "completed"
    assert deleted["status"] == "completed"
    assert not (tmp_path / "notes" / "standup.md").exists()
    assert (tmp_path / "docs" / "standup.md").read_text(encoding="utf-8") == "standup\n"
    assert (tmp_path / "projects" / "spec.md").read_text(encoding="utf-8") == "spec\n"
    assert (tmp_path / "docs" / "spec.md").read_text(encoding="utf-8") == "spec\n"
    assert not (tmp_path / "tmp" / "old.log").exists()


def test_file_tools_deny_unsafe_paths_and_existing_destinations(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.md").write_text("a\n", encoding="utf-8")
    (tmp_path / "docs" / "existing.md").write_text("existing\n", encoding="utf-8")
    services = _services(tmp_path)

    move_existing = services.broker.execute(
        "file_management.move_file",
        {"source": "docs/a.md", "destination": "docs/existing.md"},
        active_tool_ids=("file_management.move_file",),
    )
    copy_outside = services.broker.execute(
        "file_management.copy_file",
        {"source": "docs/a.md", "destination": "../escape.md", "preserve_source": True},
        active_tool_ids=("file_management.copy_file",),
    )
    delete_protected = services.broker.execute(
        "file_management.delete_file",
        {"path": "secrets/prod.env"},
        active_tool_ids=("file_management.delete_file",),
    )

    assert move_existing["status"] == "denied"
    assert move_existing["payload"]["suggested_path"] == "docs/existing-1.md"
    assert copy_outside["status"] == "denied"
    assert delete_protected["status"] == "denied"
    assert (tmp_path / "docs" / "a.md").is_file()
    assert not (tmp_path.parent / "escape.md").exists()


def test_copy_file_requires_explicit_source_preservation(tmp_path):
    (tmp_path / "projects").mkdir()
    (tmp_path / "projects" / "spec.md").write_text("spec\n", encoding="utf-8")
    services = _services(tmp_path)

    result = services.broker.execute(
        "file_management.copy_file",
        {"source": "projects/spec.md", "destination": "docs/spec.md"},
        active_tool_ids=("file_management.copy_file",),
    )

    assert result["status"] == "denied"
    assert result["payload"]["errors"] == ["copy_requires_preserve_source:true"]
    assert not (tmp_path / "docs" / "spec.md").exists()


def test_copy_file_schema_exposes_preserve_source_argument(tmp_path):
    services = _services(tmp_path)

    definition = services.tool_registry.definition("file_management.copy_file")

    assert definition is not None
    assert "preserve_source" in definition.argument_schema["properties"]
