import json
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.2"))

from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.extensions.file_management.mutation_executor import FileMutationExecutor
from appv22.extensions.file_management.mutation_policy import FileMutationPolicy
from appv22.extensions.file_management.planner import ModelAuthoredFilePlanner
from appv22.extensions.file_management.verifier import WorkspaceManifestVerifier
from appv22.extensions.registry import ExtensionRegistry
from appv22.runtime.capabilities import CapabilityRegistry
from appv22.state.models import AgentState, RequestEnvelope
from appv22.tools.registry import ToolRegistry


def test_file_management_extension_registers_all_capabilities():
    extension = FileManagementExtension()
    registry = ExtensionRegistry()
    capabilities = CapabilityRegistry()
    registry.register(extension)
    extension.register_capabilities(capabilities)
    state = AgentState("sess", "run", RequestEnvelope("req", "tidy this workspace mess", "."))

    resolved = registry.resolve_active(state)

    assert resolved.extension_ids == ("file_management",)
    assert resolved.planner_ids == ("file_management.model_authored_file_planner",)
    assert resolved.mutation_policy_ids == ("file_management.safe_file_mutations",)
    assert resolved.mutation_executor_ids == ("file_management.file_mutation_executor",)
    assert resolved.verifier_ids == ("file_management.manifest_verifier",)
    assert resolved.tool_ids == ("file_management.read_file", "file_management.repo_snapshot")
    assert resolved.artifact_schema_ids == ()
    assert capabilities.planner("file_management.model_authored_file_planner")
    assert capabilities.mutation_policy("file_management.safe_file_mutations")
    assert capabilities.mutation_executor("file_management.file_mutation_executor")
    assert capabilities.verifier("file_management.manifest_verifier")


def test_file_management_skill_activation_handles_vague_prompts():
    extension = FileManagementExtension()
    state = AgentState("sess", "run", RequestEnvelope("req", "leave a useful record for the next person", "."))

    assert extension.skill_cards()[0].activates_for(state) is True


def test_file_management_skill_activation_handles_creation_and_documentation_prompts():
    extension = FileManagementExtension()
    prompts = [
        "leave something useful for the next person",
        "capture the decision in a small durable file",
        "make a practical checklist from my notes",
        "create the smallest sensible runbook stub",
    ]

    assert [
        extension.skill_cards()[0].activates_for(
            AgentState("sess", "run", RequestEnvelope("req", prompt, "."))
        )
        for prompt in prompts
    ] == [True, True, True, True]


def test_file_management_extension_registers_snapshot_and_read_tools(tmp_path):
    (tmp_path / "notes.md").write_text("hello", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested/data.json").write_text("{}", encoding="utf-8")
    registry = ToolRegistry()
    FileManagementExtension().register_tools(registry)

    snapshot = registry.handler("file_management.repo_snapshot")({}, {"root_path": tmp_path})
    read = registry.handler("file_management.read_file")({"path": "notes.md"}, {"root_path": tmp_path})

    assert registry.definition("file_management.repo_snapshot").tool_id == "file_management.repo_snapshot"
    assert registry.definition("file_management.read_file").tool_id == "file_management.read_file"
    assert snapshot["status"] == "completed"
    assert snapshot["files"] == ["nested/data.json", "notes.md"]
    assert snapshot["directories"] == ["nested"]
    assert snapshot["text_previews"] == {"nested/data.json": "{}", "notes.md": "hello"}
    assert read == {"status": "completed", "path": "notes.md", "content": "hello"}


def test_policy_rejects_root_escape_and_protected_paths(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/existing.md").write_text("existing", encoding="utf-8")
    operations = [
        {"action": "move", "source": "../outside.md", "destination": "docs/outside.md"},
        {"action": "move", "source": "README.md", "destination": "docs/readme.md"},
        {"action": "move", "source": "draft.md", "destination": "docs/existing.md"},
        {"action": "write", "path": "secrets/generated.txt", "content": "{}"},
    ]

    errors = FileMutationPolicy().validate(operations, root_path=tmp_path)

    assert "path_outside_root:../outside.md->docs/outside.md" in errors
    assert "protected_source_path:README.md" in errors
    assert "destination_exists:docs/existing.md" in errors
    assert "protected_write_path:secrets/generated.txt" in errors


def test_policy_allows_model_authored_safe_file_creation(tmp_path):
    errors = FileMutationPolicy().validate(
        [
            {
                "action": "write",
                "path": "docs/NEXT_STEPS.md",
                "content": "# Next Steps\n\nHandoff record.\n",
            }
        ],
        root_path=tmp_path,
    )

    assert errors == []


def test_verifier_accepts_created_files_intent(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/NEXT_STEPS.md").write_text("# Next Steps\n\nHandoff record.\n", encoding="utf-8")

    result = WorkspaceManifestVerifier().verify(
        root_path=tmp_path,
        verification_intent={
            "created_files": [
                {
                    "path": "docs/NEXT_STEPS.md",
                    "content": "# Next Steps\n\nHandoff record.\n",
                }
            ]
        },
    )

    assert result["status"] == "passed"
    assert {"name": "created_file_exists:docs/NEXT_STEPS.md", "passed": True} in result["checks"]


def test_policy_rejects_duplicate_move_destinations_and_absolute_paths(tmp_path):
    (tmp_path / "draft.md").write_text("draft", encoding="utf-8")
    (tmp_path / "notes.md").write_text("notes", encoding="utf-8")

    errors = FileMutationPolicy().validate(
        [
            {"action": "move", "source": "draft.md", "destination": "docs/shared.md"},
            {"action": "move", "source": "notes.md", "destination": "docs/shared.md"},
            {"action": "move", "source": "/tmp/source.md", "destination": "docs/absolute-source.md"},
            {"action": "move", "source": "draft.md", "destination": "/tmp/destination.md"},
            {"action": "write", "path": "/tmp/manifest.json", "content": "{}"},
        ],
        root_path=tmp_path,
    )

    assert "duplicate_destination:docs/shared.md" in errors
    assert "absolute_path:source:/tmp/source.md" in errors
    assert "absolute_path:destination:/tmp/destination.md" in errors
    assert "absolute_path:path:/tmp/manifest.json" in errors


def test_policy_preflights_move_sources_before_execution(tmp_path):
    (tmp_path / "folder_source").mkdir()

    errors = FileMutationPolicy().validate(
        [
            {"action": "move", "source": "missing.md", "destination": "docs/missing.md"},
            {"action": "move", "source": "folder_source", "destination": "docs/folder_source"},
        ],
        root_path=tmp_path,
    )

    assert "missing_source:missing.md" in errors
    assert "non_file_source:folder_source" in errors


def test_read_file_denies_protected_paths_with_schema_compatible_payload(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/config").write_text("private", encoding="utf-8")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets/token.txt").write_text("secret", encoding="utf-8")
    registry = ToolRegistry()
    FileManagementExtension().register_tools(registry)

    git_read = registry.handler("file_management.read_file")({"path": ".git/config"}, {"root_path": tmp_path})
    secret_read = registry.handler("file_management.read_file")({"path": "secrets/token.txt"}, {"root_path": tmp_path})
    missing_read = registry.handler("file_management.read_file")({"path": "missing.txt"}, {"root_path": tmp_path})

    assert git_read == {"status": "denied", "path": ".git/config", "content": "", "errors": ["protected_path:.git/config"]}
    assert secret_read == {
        "status": "denied",
        "path": "secrets/token.txt",
        "content": "",
        "errors": ["protected_path:secrets/token.txt"],
    }
    assert missing_read == {"status": "failed", "path": "missing.txt", "content": "", "errors": ["missing_file:missing.txt"]}
    schema = registry.definition("file_management.read_file").result_schema
    assert "errors" in schema["properties"]
    assert set(schema["required"]) == {"status", "path", "content"}


def test_read_file_denies_normalized_protected_path_bypass(tmp_path):
    (tmp_path / "safe").mkdir()
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets/token.txt").write_text("secret", encoding="utf-8")
    registry = ToolRegistry()
    FileManagementExtension().register_tools(registry)

    result = registry.handler("file_management.read_file")(
        {"path": "safe/../secrets/token.txt"},
        {"root_path": tmp_path},
    )

    assert result == {
        "status": "denied",
        "path": "secrets/token.txt",
        "content": "",
        "errors": ["protected_path:secrets/token.txt"],
    }


def test_read_file_allows_case_variant_readme_rehydration(tmp_path):
    (tmp_path / "readme.md").write_text("private", encoding="utf-8")
    registry = ToolRegistry()
    FileManagementExtension().register_tools(registry)

    result = registry.handler("file_management.read_file")({"path": "readme.md"}, {"root_path": tmp_path})

    assert result == {"status": "completed", "path": "readme.md", "content": "private"}


def test_read_file_allows_docs_rehydration_even_when_write_policy_protects_sensitive_paths(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/context.md").write_text("office evidence", encoding="utf-8")
    registry = ToolRegistry()
    FileManagementExtension().register_tools(registry)

    result = registry.handler("file_management.read_file")({"path": "docs/context.md"}, {"root_path": tmp_path})

    assert result == {"status": "completed", "path": "docs/context.md", "content": "office evidence"}


def test_policy_rejects_case_variant_protected_sources_and_destinations(tmp_path):
    (tmp_path / "draft.md").write_text("draft", encoding="utf-8")
    (tmp_path / "notes.md").write_text("notes", encoding="utf-8")

    errors = FileMutationPolicy().validate(
        [
            {"action": "move", "source": "readme.md", "destination": "out/readme.md"},
            {"action": "move", "source": "README.md", "destination": "out/README.md"},
            {"action": "move", "source": "Tests/foo.py", "destination": "out/foo.py"},
            {"action": "move", "source": "draft.md", "destination": "Secrets/out.txt"},
            {"action": "move", "source": "notes.md", "destination": ".GIT/config"},
        ],
        root_path=tmp_path,
    )

    assert "protected_source_path:readme.md" in errors
    assert "protected_source_path:README.md" in errors
    assert "protected_source_path:Tests/foo.py" in errors
    assert "protected_destination_path:Secrets/out.txt" in errors
    assert "protected_destination_path:.GIT/config" in errors


def test_policy_rejects_normalized_protected_source_and_destination(tmp_path):
    (tmp_path / "safe").mkdir()
    (tmp_path / "safe/draft.md").write_text("draft", encoding="utf-8")
    (tmp_path / "secrets").mkdir()
    (tmp_path / "secrets/token.txt").write_text("secret", encoding="utf-8")

    errors = FileMutationPolicy().validate(
        [
            {"action": "move", "source": "safe/../secrets/token.txt", "destination": "moved/token.txt"},
            {"action": "move", "source": "safe/draft.md", "destination": "safe/../secrets/draft.md"},
        ],
        root_path=tmp_path,
    )

    assert "protected_source_path:secrets/token.txt" in errors
    assert "protected_destination_path:secrets/draft.md" in errors


def test_policy_rejects_canonical_duplicate_move_destinations(tmp_path):
    (tmp_path / "one.md").write_text("one", encoding="utf-8")
    (tmp_path / "two.md").write_text("two", encoding="utf-8")

    errors = FileMutationPolicy().validate(
        [
            {"action": "move", "source": "one.md", "destination": "docs/file.md"},
            {"action": "move", "source": "two.md", "destination": "docs/../docs/file.md"},
        ],
        root_path=tmp_path,
    )

    assert "duplicate_destination:docs/file.md" in errors


def test_policy_rejects_casefolded_duplicate_move_sources(tmp_path):
    (tmp_path / "safe").mkdir()
    (tmp_path / "safe/file.md").write_text("file", encoding="utf-8")

    operations = [
        {"action": "move", "source": "safe/file.md", "destination": "moved/one.md"},
        {"action": "move", "source": "SAFE/FILE.md", "destination": "moved/two.md"},
    ]

    errors = FileMutationPolicy().validate(operations, root_path=tmp_path)
    result = FileMutationExecutor().apply(operations, root_path=tmp_path)

    assert "duplicate_source:SAFE/FILE.md" in errors
    assert result["status"] == "denied"
    assert "duplicate_source:SAFE/FILE.md" in result["errors"]
    assert (tmp_path / "safe/file.md").read_text(encoding="utf-8") == "file"
    assert not (tmp_path / "moved/one.md").exists()
    assert not (tmp_path / "moved/two.md").exists()


def test_policy_rejects_casefolded_duplicate_move_destinations(tmp_path):
    (tmp_path / "one.md").write_text("one", encoding="utf-8")
    (tmp_path / "two.md").write_text("two", encoding="utf-8")

    errors = FileMutationPolicy().validate(
        [
            {"action": "move", "source": "one.md", "destination": "out/File.md"},
            {"action": "move", "source": "two.md", "destination": "out/file.md"},
        ],
        root_path=tmp_path,
    )

    assert "duplicate_destination:out/file.md" in errors


def test_policy_rejects_casefolded_source_destination_cross_collision(tmp_path):
    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "b.md").write_text("b", encoding="utf-8")

    operations = [
        {"action": "move", "source": "a.md", "destination": "B.md"},
        {"action": "move", "source": "b.md", "destination": "c.md"},
    ]

    errors = FileMutationPolicy().validate(operations, root_path=tmp_path)
    result = FileMutationExecutor().apply(operations, root_path=tmp_path)

    assert "source_destination_collision:B.md" in errors
    assert result["status"] == "denied"
    assert "source_destination_collision:B.md" in result["errors"]
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "a"
    assert (tmp_path / "b.md").read_text(encoding="utf-8") == "b"
    assert not (tmp_path / "c.md").exists()


def test_policy_rejects_casefolded_existing_destination_collision(tmp_path):
    (tmp_path / "draft.md").write_text("draft", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/file.md").write_text("existing", encoding="utf-8")

    operations = [{"action": "move", "source": "draft.md", "destination": "docs/File.md"}]

    errors = FileMutationPolicy().validate(operations, root_path=tmp_path)
    result = FileMutationExecutor().apply(operations, root_path=tmp_path)

    assert "destination_exists:docs/File.md" in errors
    assert result["status"] == "denied"
    assert "destination_exists:docs/File.md" in result["errors"]
    assert (tmp_path / "draft.md").read_text(encoding="utf-8") == "draft"
    assert (tmp_path / "docs/file.md").read_text(encoding="utf-8") == "existing"


def test_executor_preflight_rejects_casefolded_collisions_without_partial_mutation(tmp_path, monkeypatch):
    (tmp_path / "a.md").write_text("a", encoding="utf-8")
    (tmp_path / "b.md").write_text("b", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/file.md").write_text("existing", encoding="utf-8")
    operations = [
        {"action": "move", "source": "a.md", "destination": "B.md"},
        {"action": "move", "source": "b.md", "destination": "c.md"},
        {"action": "move", "source": "a.md", "destination": "docs/File.md"},
    ]
    monkeypatch.setattr(FileMutationPolicy, "validate", lambda self, operations, *, root_path: [])

    result = FileMutationExecutor().apply(operations, root_path=tmp_path)

    assert result["status"] == "denied"
    assert result["touched_paths"] == []
    assert "source_destination_collision:B.md" in result["errors"]
    assert "destination_exists:docs/File.md" in result["errors"]
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "a"
    assert (tmp_path / "b.md").read_text(encoding="utf-8") == "b"
    assert (tmp_path / "docs/file.md").read_text(encoding="utf-8") == "existing"
    assert not (tmp_path / "c.md").exists()


def test_policy_rejects_duplicate_canonical_sources_without_partial_mutation(tmp_path):
    (tmp_path / "safe").mkdir()
    (tmp_path / "safe/first.md").write_text("first", encoding="utf-8")

    operations = [
        {"action": "move", "source": "safe/first.md", "destination": "moved/first.md"},
        {"action": "move", "source": "safe/../safe/first.md", "destination": "moved/second.md"},
    ]

    errors = FileMutationPolicy().validate(operations, root_path=tmp_path)
    result = FileMutationExecutor().apply(operations, root_path=tmp_path)

    assert "duplicate_source:safe/first.md" in errors
    assert result == {"status": "denied", "touched_paths": [], "errors": ["duplicate_source:safe/first.md"]}
    assert (tmp_path / "safe/first.md").read_text(encoding="utf-8") == "first"
    assert not (tmp_path / "moved/first.md").exists()
    assert not (tmp_path / "moved/second.md").exists()


def test_planner_rejects_non_executable_model_plan_instead_of_cleanup_fallback():
    state = AgentState("sess", "run", RequestEnvelope("req", "cleanup", "/workspace"))
    state.world_refs["world://repo_snapshot/latest"] = {
        "payload": {"files": ["draft.md", "docs/draft.md", "logs/run.json", "run.json"]}
    }

    with pytest.raises(ValueError, match="model_authored_plan_required"):
        ModelAuthoredFilePlanner().plan(state, decision_payload={"plan_steps": [{"path": "docs/handoff.md"}]})


def test_planner_accepts_model_authored_proposed_artifact():
    state = AgentState("sess", "run", RequestEnvelope("req", "create record", "/workspace"))

    plan = ModelAuthoredFilePlanner().plan(
        state,
        decision_payload={
            "proposed_artifact": {
                "relative_path": "docs/handoff.md",
                "content": "# Handoff\n\nUse the observed context.\n",
            }
        },
    )

    assert plan["planner_id"] == "file_management.model_authored_file_planner"
    assert plan["mutation_intent"] == {
        "operation_batch_id": "model_authored_file_creation",
        "operations": [
            {
                "action": "write",
                "path": "docs/handoff.md",
                "content": "# Handoff\n\nUse the observed context.\n",
            }
        ],
    }


def test_planner_prefers_proposed_artifact_over_non_runtime_mutation_shape():
    state = AgentState("sess", "run", RequestEnvelope("req", "create record", "/workspace"))

    plan = ModelAuthoredFilePlanner().plan(
        state,
        decision_payload={
            "mutation_intent": {
                "operations": [
                    {
                        "kind": "create_file",
                        "path": "docs/checklist.md",
                    }
                ]
            },
            "proposed_artifact": {
                "path": "docs/checklist.md",
                "content": "# Checklist\n\n- [ ] Ship it.\n",
            },
        },
    )

    assert plan["mutation_intent"]["operations"] == [
        {
            "action": "write",
            "path": "docs/checklist.md",
            "content": "# Checklist\n\n- [ ] Ship it.\n",
        }
    ]


def test_executor_applies_validated_moves_and_manifest(tmp_path):
    (tmp_path / "draft.md").write_text("draft", encoding="utf-8")
    operations = [
        {"action": "move", "source": "draft.md", "destination": "docs/draft.md"},
        {
            "action": "write",
            "path": "docs/workspace_manifest.json",
            "content": {"generated_by": "appv22", "moves": [], "held": [], "collisions": []},
        },
    ]
    assert FileMutationPolicy().validate(operations, root_path=tmp_path) == []

    result = FileMutationExecutor().apply(operations, root_path=tmp_path)

    assert result == {
        "status": "applied",
        "touched_paths": ["docs/draft.md", "docs/workspace_manifest.json", "draft.md"],
        "errors": [],
    }
    assert not (tmp_path / "draft.md").exists()
    assert (tmp_path / "docs/draft.md").read_text(encoding="utf-8") == "draft"
    assert json.loads((tmp_path / "docs/workspace_manifest.json").read_text(encoding="utf-8"))["generated_by"] == "appv22"


def test_executor_preflight_denies_batch_without_partial_mutation(tmp_path):
    (tmp_path / "first.md").write_text("first", encoding="utf-8")
    operations = [
        {"action": "move", "source": "first.md", "destination": "docs/first.md"},
        {"action": "move", "source": "missing.md", "destination": "docs/missing.md"},
    ]

    result = FileMutationExecutor().apply(operations, root_path=tmp_path)

    assert result == {"status": "denied", "touched_paths": [], "errors": ["missing_source:missing.md"]}
    assert (tmp_path / "first.md").read_text(encoding="utf-8") == "first"
    assert not (tmp_path / "docs/first.md").exists()


def test_executor_preflights_manifest_write_parent_before_moves(tmp_path):
    (tmp_path / "draft.md").write_text("draft", encoding="utf-8")
    (tmp_path / "docs").write_text("not a directory", encoding="utf-8")
    operations = [
        {"action": "move", "source": "draft.md", "destination": "archive/draft.md"},
        {
            "action": "write",
            "path": "docs/workspace_manifest.json",
            "content": {"generated_by": "appv22", "moves": [], "held": [], "collisions": []},
        },
    ]

    result = FileMutationExecutor().apply(operations, root_path=tmp_path)

    assert result == {
        "status": "failed",
        "touched_paths": [],
        "errors": ["blocked_write_parent:docs/workspace_manifest.json"],
    }
    assert (tmp_path / "draft.md").read_text(encoding="utf-8") == "draft"
    assert not (tmp_path / "archive/draft.md").exists()


def test_manifest_verifier_checks_required_manifest_fields(tmp_path):
    manifest_path = tmp_path / "docs/workspace_manifest.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        json.dumps({"generated_by": "appv22", "moves": [], "held": [], "collisions": []}),
        encoding="utf-8",
    )

    result = WorkspaceManifestVerifier().verify(
        root_path=tmp_path,
        verification_intent={"manifest_path": "docs/workspace_manifest.json"},
    )

    assert result["status"] == "passed"
    assert all(check["passed"] for check in result["checks"])
    assert result["manifest"]["generated_by"] == "appv22"


def test_manifest_verifier_rejects_type_mismatches_and_intent_mismatches(tmp_path):
    manifest_path = tmp_path / "docs/workspace_manifest.json"
    manifest_path.parent.mkdir()
    manifest_path.write_text(
        json.dumps({"generated_by": "appv22", "moves": {}, "held": [], "collisions": []}),
        encoding="utf-8",
    )

    result = WorkspaceManifestVerifier().verify(
        root_path=tmp_path,
        verification_intent={"manifest_path": "docs/workspace_manifest.json", "moves": []},
    )

    assert result["status"] == "failed"
    assert {"name": "manifest_type_moves", "passed": False} in result["checks"]
    assert {"name": "verification_moves_match", "passed": False} in result["checks"]


def test_manifest_verifier_checks_intended_moves_on_disk(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/draft.md").write_text("draft", encoding="utf-8")
    manifest_path = tmp_path / "docs/workspace_manifest.json"
    move = {"source": "draft.md", "destination": "docs/draft.md"}
    manifest_path.write_text(
        json.dumps({"generated_by": "appv22", "moves": [move], "held": ["README.md"], "collisions": []}),
        encoding="utf-8",
    )

    result = WorkspaceManifestVerifier().verify(
        root_path=tmp_path,
        verification_intent={
            "manifest_path": "docs/workspace_manifest.json",
            "moves": [move],
            "held": ["README.md"],
            "collisions": [],
        },
    )

    assert result["status"] == "passed"
    assert {"name": "verification_moves_match", "passed": True} in result["checks"]
    assert {"name": "verification_held_match", "passed": True} in result["checks"]
    assert {"name": "verification_collisions_match", "passed": True} in result["checks"]
    assert {"name": "move_destination_exists:docs/draft.md", "passed": True} in result["checks"]
    assert {"name": "move_source_absent:draft.md", "passed": True} in result["checks"]
