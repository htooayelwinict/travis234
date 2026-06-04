"""Worker-facing artifact contracts and quality checks."""

from __future__ import annotations

from typing import Any

from app.schemas import ArtifactPayload


CORE_ARTIFACT_SHAPES: dict[str, dict[str, Any]] = {
    "mutation_scope": {
        "target_paths": ["repo/relative/path"],
        "create_paths": [],
        "update_paths": [],
        "delete_paths": [],
        "move_pairs": [{"source": "old/path", "destination": "new/path"}],
        "test_paths": [],
        "forbidden_paths": [],
        "forbidden_globs": [],
        "reason": "why this is the full write boundary",
        "max_files": 5,
    },
    "rollback_plan": {
        "strategy": "how to reverse or abandon the proposed mutation safely",
        "preimage_required": True,
        "affected_paths": [],
    },
    "verification_plan": {
        "checks": [],
        "commands": [],
        "expected_outcome": "what proves the work",
    },
    "change_design": {
        "steps": [],
        "target_behavior": "intended behavior after mutation",
        "scope_notes": "why this design is bounded",
    },
    "fix_design": {
        "steps": [],
        "root_cause": "specific behavior to fix",
        "target_behavior": "intended behavior after mutation",
    },
    "change_summary": {
        "changed_paths": [],
        "summary": "what changed",
        "risk_notes": [],
    },
    "patch_diff": {
        "paths": [],
        "diff": "unified diff or bounded diff summary",
    },
    "rollback_patch": {
        "changed_paths": [],
        "diff": "reverse patch or rollback instructions",
    },
    "verification_results": {
        "status": "passed|failed",
        "commands": [],
        "scope_audit": {},
    },
    "test_results": {
        "status": "passed|failed",
        "commands": [],
        "failed_commands": [],
    },
    "final_report": {
        "summary": "human-facing report summary",
        "findings": [],
        "path": "optional report path",
    },
    "selected_move_candidates": {
        "candidates": [
            {
                "source": "repo/relative/source",
                "destination": "repo/relative/destination",
                "category": "markdown|log|json_artifact|other",
                "reason": "evidence-backed reason for the move",
            }
        ],
        "excluded_from_moves": [
            {
                "path": "repo/relative/path",
                "reason": "why this path must stay untouched",
            }
        ],
        "manifest_schema": {
            "moved_documents": ["basename.md"],
            "moved_logs": ["old_build.log"],
            "moved_json_artifacts": ["error_dump.json"],
            "total_artifacts": 0,
        },
    },
    "allowed_write_paths": {
        "allowed_write_files": ["repo/relative/source-or-destination"],
        "allowed_write_destinations": ["repo/relative/destination"],
        "excluded_paths": ["repo/relative/path"],
        "evidence": [],
    },
    "moved_items_record": {
        "moved_documents": ["basename.md"],
        "moved_logs": ["old_build.log"],
        "moved_json_artifacts": ["error_dump.json"],
        "total_artifacts": 0,
    },
    "moved_items_evidence": {
        "move_pairs": [{"source": "old/path", "destination": "new/path"}],
        "total_moved": 0,
        "manifest_path": "repo/relative/manifest.json",
    },
    "manifest_file": {
        "manifest_path": "repo/relative/manifest.json",
        "payload": {
            "moved_documents": ["basename.md"],
            "moved_logs": ["old_build.log"],
            "moved_json_artifacts": ["error_dump.json"],
            "total_artifacts": 0,
        },
    },
    "manifest_validation": {
        "manifest_exists": True,
        "fields_present": ["moved_documents", "moved_logs", "moved_json_artifacts", "total_artifacts"],
        "counts_match": True,
        "total_artifacts": 0,
    },
    "scope_verification": {
        "scope_status": "passed|failed",
        "changed_files": [],
        "forbidden_changes": [],
        "protected_paths_intact": [],
    },
}

CORE_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "mutation_scope": ("target_paths",),
    "rollback_plan": ("strategy",),
    "verification_plan": ("checks",),
    "change_design": ("steps",),
    "fix_design": ("steps",),
    "change_summary": ("summary",),
    "patch_diff": ("diff",),
    "rollback_patch": ("diff",),
    "verification_results": ("status",),
    "test_results": ("status",),
    "selected_move_candidates": ("candidates",),
    "allowed_write_paths": ("allowed_write_files",),
    "moved_items_record": ("total_artifacts",),
    "moved_items_evidence": ("move_pairs",),
    "manifest_file": ("manifest_path",),
    "manifest_validation": ("manifest_exists", "counts_match"),
    "scope_verification": ("scope_status",),
}


def artifact_contract(artifact_id: str) -> dict[str, Any]:
    return {
        "id": artifact_id,
        "required": True,
        "artifact_shape": {
            "id": artifact_id,
            "content": CORE_ARTIFACT_SHAPES.get(artifact_id, "structured evidence or result payload"),
            "kind": "worker_output",
        },
    }


def artifact_content_empty(content: Any) -> bool:
    if content is None:
        return True
    if isinstance(content, str):
        return not content.strip()
    if isinstance(content, (list, tuple, set, dict)):
        return len(content) == 0
    return False


def evaluate_artifact_quality(
    *,
    expected_outputs: list[str],
    artifacts: list[ArtifactPayload],
) -> dict[str, Any]:
    by_id = {
        artifact.id: artifact
        for artifact in artifacts
        if not artifact.metadata.get("worker_returned_bare_artifact_id")
    }
    missing = [artifact_id for artifact_id in expected_outputs if artifact_id not in by_id]
    empty = [
        artifact_id
        for artifact_id in expected_outputs
        if artifact_id in by_id and artifact_content_empty(by_id[artifact_id].content)
    ]
    synthesized = [
        artifact_id
        for artifact_id in expected_outputs
        if artifact_id in by_id and by_id[artifact_id].metadata.get("synthesized_after_model_budget_exhaustion")
    ]
    invalid = [
        invalid_payload
        for artifact_id in expected_outputs
        if artifact_id in by_id and artifact_id not in empty
        for invalid_payload in _contract_errors(artifact_id=artifact_id, content=by_id[artifact_id].content)
    ]
    return {
        "expected_count": len(expected_outputs),
        "missing_count": len(missing),
        "empty_count": len(empty),
        "invalid_count": len(invalid),
        "synthesized_count": len(synthesized),
        "missing_artifacts": missing,
        "empty_artifacts": empty,
        "invalid_artifacts": invalid,
        "synthesized_artifacts": synthesized,
    }


def _contract_errors(*, artifact_id: str, content: Any) -> list[dict[str, Any]]:
    required = CORE_REQUIRED_KEYS.get(artifact_id)
    if not required:
        return []

    if artifact_id == "final_report" and isinstance(content, str) and content.strip():
        return []
    if not isinstance(content, dict):
        return [
            {
                "artifact_id": artifact_id,
                "code": "artifact_content_type_invalid",
                "message": f"{artifact_id} content should be an object matching its contract",
            }
        ]

    errors: list[dict[str, Any]] = []
    for key in required:
        if artifact_content_empty(content.get(key)):
            errors.append(
                {
                    "artifact_id": artifact_id,
                    "code": "artifact_required_field_empty",
                    "field": key,
                    "message": f"{artifact_id}.{key} is required and must be non-empty",
                }
            )
    return errors
