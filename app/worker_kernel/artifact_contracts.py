"""Worker-facing artifact contracts and quality checks."""

from __future__ import annotations

from typing import Any

from app.artifact_aliases import canonical_artifact_id, canonicalize_artifact_ids
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
    "manifest_update_record": {
        "manifest_path": "repo/relative/manifest.json",
        "payload": {
            "moved_documents": ["basename.md"],
            "moved_logs": ["old_build.log"],
            "moved_json_artifacts": ["error_dump.json"],
            "total_artifacts": 0,
        },
        "fields_present": ["moved_documents", "moved_logs", "moved_json_artifacts", "total_artifacts"],
        "missing_fields": [],
        "counts_match": True,
        "total_artifacts": 0,
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
    "moved_items_record": ("moved_documents", "moved_logs", "moved_json_artifacts", "total_artifacts"),
    "moved_items_evidence": ("move_pairs",),
    "manifest_file": ("manifest_path",),
    "manifest_update_record": ("manifest_path", "payload", "fields_present", "counts_match"),
    "manifest_validation": ("manifest_exists", "counts_match"),
    "scope_verification": ("scope_status",),
}


def artifact_contract(artifact_id: str, *, contract_context: dict[str, Any] | None = None) -> dict[str, Any]:
    canonical_id = canonical_artifact_id(artifact_id)
    content_shape = CORE_ARTIFACT_SHAPES.get(canonical_id, "structured evidence or result payload")
    required_json_keys = _manifest_required_json_keys(
        artifact_id=canonical_id,
        contract_context=contract_context,
    )
    if required_json_keys and canonical_id in {
        "manifest_file",
        "manifest_update_record",
        "manifest_validation",
        "moved_items_record",
    }:
        payload_shape = _manifest_payload_shape(required_json_keys)
        if canonical_id == "moved_items_record":
            content_shape = payload_shape
        elif canonical_id in {"manifest_file", "manifest_update_record"}:
            base_shape = dict(content_shape) if isinstance(content_shape, dict) else {}
            content_shape = {
                **base_shape,
                "manifest_path": "repo/relative/manifest.json",
                "payload": payload_shape,
            }
        elif canonical_id == "manifest_validation":
            base_shape = dict(content_shape) if isinstance(content_shape, dict) else {}
            content_shape = {
                **base_shape,
                "fields_present": required_json_keys,
            }
    return {
        "id": canonical_id,
        "required": True,
        "artifact_shape": {
            "id": canonical_id,
            "content": content_shape,
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
    contract_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    canonical_expected, expected_alias_changes = canonicalize_artifact_ids(expected_outputs)
    by_id = {
        canonical_artifact_id(artifact.id): artifact
        for artifact in artifacts
        if not artifact.metadata.get("worker_returned_bare_artifact_id")
    }
    produced_alias_changes = [
        {"original": artifact.id, "canonical": canonical_artifact_id(artifact.id)}
        for artifact in artifacts
        if canonical_artifact_id(artifact.id) != artifact.id
    ]
    missing = [artifact_id for artifact_id in canonical_expected if artifact_id not in by_id]
    empty = [
        artifact_id
        for artifact_id in canonical_expected
        if artifact_id in by_id and artifact_content_empty(by_id[artifact_id].content)
    ]
    synthesized = [
        artifact_id
        for artifact_id in canonical_expected
        if artifact_id in by_id and by_id[artifact_id].metadata.get("synthesized_after_model_budget_exhaustion")
    ]
    invalid = [
        invalid_payload
        for artifact_id in canonical_expected
        if artifact_id in by_id and artifact_id not in empty
        for invalid_payload in _contract_errors(
            artifact_id=artifact_id,
            content=by_id[artifact_id].content,
            contract_context=contract_context,
        )
    ]
    return {
        "expected_count": len(canonical_expected),
        "missing_count": len(missing),
        "empty_count": len(empty),
        "invalid_count": len(invalid),
        "synthesized_count": len(synthesized),
        "missing_artifacts": missing,
        "empty_artifacts": empty,
        "invalid_artifacts": invalid,
        "synthesized_artifacts": synthesized,
        "canonical_aliases": {
            "expected_outputs": expected_alias_changes,
            "produced_artifacts": produced_alias_changes,
        },
    }


def _contract_errors(
    *,
    artifact_id: str,
    content: Any,
    contract_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    artifact_id = canonical_artifact_id(artifact_id)
    dynamic_required = _dynamic_required_keys(artifact_id=artifact_id, contract_context=contract_context)
    required = dynamic_required or CORE_REQUIRED_KEYS.get(artifact_id)
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
        if dynamic_required and artifact_id == "moved_items_record":
            errors.extend(_required_json_key_errors(artifact_id=artifact_id, content=content, key=key))
            continue
        if artifact_id == "moved_items_record" and key in {
            "moved_documents",
            "moved_logs",
            "moved_json_artifacts",
        }:
            if key not in content:
                errors.append(
                    {
                        "artifact_id": artifact_id,
                        "code": "artifact_required_field_missing",
                        "field": key,
                        "message": f"{artifact_id}.{key} is required",
                    }
                )
            elif not isinstance(content.get(key), list):
                errors.append(
                    {
                        "artifact_id": artifact_id,
                        "code": "artifact_field_type_invalid",
                        "field": key,
                        "message": f"{artifact_id}.{key} must be a list",
                    }
                )
            continue
        if artifact_content_empty(content.get(key)):
            errors.append(
                {
                    "artifact_id": artifact_id,
                    "code": "artifact_required_field_empty",
                    "field": key,
                    "message": f"{artifact_id}.{key} is required and must be non-empty",
                }
            )
    errors.extend(
        _semantic_contract_errors(
            artifact_id=artifact_id,
            content=content,
            contract_context=contract_context,
        )
    )
    return errors


def _semantic_contract_errors(
    *,
    artifact_id: str,
    content: dict[str, Any],
    contract_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if artifact_id == "moved_items_record":
        return _moved_items_record_errors(
            artifact_id=artifact_id,
            content=content,
            contract_context=contract_context,
        )
    if artifact_id in {"manifest_update_record", "manifest_file", "manifest_validation"}:
        return _manifest_artifact_errors(
            artifact_id=artifact_id,
            content=content,
            contract_context=contract_context,
        )
    return []


def _moved_items_record_errors(
    *,
    artifact_id: str,
    content: dict[str, Any],
    contract_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    required_json_keys = _manifest_required_json_keys(
        artifact_id=artifact_id,
        contract_context=contract_context,
    )
    total_key, category_keys = _count_contract(required_json_keys=required_json_keys, payload=content)
    if not total_key:
        total_key = "total_artifacts"
    if not category_keys:
        category_keys = ("moved_documents", "moved_logs", "moved_json_artifacts")
    if not all(isinstance(content.get(key), list) for key in category_keys):
        return []
    total = content.get(total_key)
    counted = sum(len(content.get(key) or []) for key in category_keys)
    if total != counted:
        return [
            {
                "artifact_id": artifact_id,
                "code": "artifact_total_mismatch",
                "field": total_key,
                "message": f"{artifact_id}.{total_key} must equal moved item count {counted}",
            }
        ]
    return []


def _manifest_artifact_errors(
    *,
    artifact_id: str,
    content: dict[str, Any],
    contract_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    payload = content.get("payload")
    required_json_keys = _manifest_required_json_keys(
        artifact_id=artifact_id,
        contract_context=contract_context,
    )
    if artifact_id in {"manifest_update_record", "manifest_file"}:
        if not isinstance(payload, dict):
            errors.append(
                {
                    "artifact_id": artifact_id,
                    "code": "artifact_field_type_invalid",
                    "field": "payload",
                    "message": f"{artifact_id}.payload must be an object",
                }
            )
            return errors
        for key in required_json_keys:
            errors.extend(_required_json_key_errors(artifact_id=artifact_id, content=payload, key=key))
        if required_json_keys or {"moved_documents", "moved_logs", "moved_json_artifacts", "total_artifacts"} <= set(payload):
            errors.extend(
                _moved_items_record_errors(
                    artifact_id=artifact_id,
                    content=payload,
                    contract_context=contract_context,
                )
            )
    if artifact_id == "manifest_validation" and required_json_keys:
        fields_present = content.get("fields_present")
        if isinstance(fields_present, list):
            missing = [key for key in required_json_keys if key not in fields_present]
            if missing:
                errors.append(
                    {
                        "artifact_id": artifact_id,
                        "code": "artifact_manifest_missing_fields",
                        "field": "fields_present",
                        "message": f"{artifact_id}.fields_present is missing exact keys: {', '.join(missing)}",
                    }
                )
    if artifact_id == "manifest_update_record":
        missing_fields = content.get("missing_fields")
        if missing_fields:
            errors.append(
                {
                    "artifact_id": artifact_id,
                    "code": "artifact_manifest_missing_fields",
                    "field": "missing_fields",
                    "message": f"{artifact_id}.missing_fields must be empty after a successful manifest update",
                }
            )
        if content.get("counts_match") is not True:
            errors.append(
                {
                    "artifact_id": artifact_id,
                    "code": "artifact_manifest_counts_mismatch",
                    "field": "counts_match",
                    "message": f"{artifact_id}.counts_match must be true",
                }
            )
    return errors


def _required_json_keys(contract_context: dict[str, Any] | None) -> list[str]:
    if not isinstance(contract_context, dict):
        return []
    return [
        str(key)
        for key in contract_context.get("required_json_keys") or []
        if isinstance(key, str) and key.strip()
    ]


def _manifest_required_json_keys(
    *,
    artifact_id: str,
    contract_context: dict[str, Any] | None,
) -> list[str]:
    required_json_keys = _required_json_keys(contract_context)
    if not required_json_keys:
        return []
    if _strict_manifest_contract_enabled(artifact_id=artifact_id, contract_context=contract_context):
        return required_json_keys
    return []


def _strict_manifest_contract_enabled(
    *,
    artifact_id: str,
    contract_context: dict[str, Any] | None,
) -> bool:
    if artifact_id not in {
        "manifest_file",
        "manifest_update_record",
        "manifest_validation",
        "moved_items_record",
    }:
        return False
    if not isinstance(contract_context, dict):
        return True

    phase = str(contract_context.get("phase") or "").upper()
    mode = str(contract_context.get("mode") or "")
    worker_type = str(contract_context.get("worker_type") or "")
    if not phase and not mode and not worker_type:
        return True
    if phase in {"MUTATE", "VERIFY", "FINALIZE"}:
        return True
    if mode in {"bounded_mutation", "verify_only"}:
        return True
    if worker_type in {"code_worker", "filesystem_worker", "verify_worker"}:
        return True
    return False


def _dynamic_required_keys(*, artifact_id: str, contract_context: dict[str, Any] | None) -> tuple[str, ...] | None:
    required_json_keys = _manifest_required_json_keys(
        artifact_id=artifact_id,
        contract_context=contract_context,
    )
    if not required_json_keys:
        return None
    if artifact_id == "moved_items_record":
        return tuple(required_json_keys)
    if artifact_id in {"manifest_file", "manifest_update_record"}:
        return CORE_REQUIRED_KEYS.get(artifact_id)
    return None


def _manifest_payload_shape(required_json_keys: list[str]) -> dict[str, Any]:
    total_key, count_keys = _count_contract(required_json_keys=required_json_keys, payload=None)
    shape: dict[str, Any] = {}
    for key in required_json_keys:
        if key == total_key or _looks_like_total_key(key):
            shape[key] = 0
        elif key in count_keys or _looks_like_list_json_key(key):
            shape[key] = ["basename-or-value"]
        else:
            shape[key] = "exact value"
    return shape


def _required_json_key_errors(*, artifact_id: str, content: dict[str, Any], key: str) -> list[dict[str, Any]]:
    if key not in content:
        return [
            {
                "artifact_id": artifact_id,
                "code": "artifact_required_field_missing",
                "field": key,
                "message": f"{artifact_id}.{key} is required by the literal JSON contract",
            }
        ]
    value = content.get(key)
    if _looks_like_total_key(key) and not isinstance(value, int):
        return [
            {
                "artifact_id": artifact_id,
                "code": "artifact_field_type_invalid",
                "field": key,
                "message": f"{artifact_id}.{key} must be an integer total",
            }
        ]
    if _looks_like_list_json_key(key) and not isinstance(value, list):
        return [
            {
                "artifact_id": artifact_id,
                "code": "artifact_field_type_invalid",
                "field": key,
                "message": f"{artifact_id}.{key} must be a list",
            }
        ]
    return []


def _count_contract(*, required_json_keys: list[str], payload: dict[str, Any] | None) -> tuple[str | None, tuple[str, ...]]:
    keys = list(required_json_keys)
    if not keys and isinstance(payload, dict):
        keys = [str(key) for key in payload]
    total_key = _infer_total_key(keys=keys, payload=payload)
    count_keys = [
        key
        for key in keys
        if key != total_key
        and not _is_excluded_count_key(key)
        and (
            key.startswith("moved_")
            or _looks_like_counted_key(key)
            or (isinstance(payload, dict) and isinstance(payload.get(key), list) and not required_json_keys)
        )
    ]
    return total_key, tuple(count_keys)


def _infer_total_key(*, keys: list[str], payload: dict[str, Any] | None) -> str | None:
    candidates = [key for key in keys if _looks_like_total_key(key)]
    if candidates:
        return candidates[0]
    if isinstance(payload, dict):
        payload_candidates = [str(key) for key in payload if _looks_like_total_key(str(key))]
        if payload_candidates:
            return payload_candidates[0]
    return None


def _looks_like_total_key(key: str) -> bool:
    normalized = key.lower()
    return normalized == "total" or normalized.startswith("total_") or normalized.endswith("_total")


def _looks_like_list_json_key(key: str) -> bool:
    return _looks_like_counted_key(key) or _is_excluded_count_key(key)


def _looks_like_counted_key(key: str) -> bool:
    normalized = key.lower()
    return normalized.startswith("moved_") or normalized.endswith(
        (
            "_items",
            "_files",
            "_documents",
            "_logs",
            "_exports",
            "_evidence",
            "_artifacts",
            "_records",
        )
    )


def _is_excluded_count_key(key: str) -> bool:
    normalized = key.lower()
    return any(token in normalized for token in ("held", "hold", "exclude", "excluded", "skipped", "ignored", "preserved"))
