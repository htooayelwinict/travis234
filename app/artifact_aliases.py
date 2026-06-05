"""Deterministic artifact id canonicalization.

Planner and worker prompts are allowed to be natural-language friendly, but the
runtime boundary needs exact artifact ids. Keep this registry intentionally small
and exact; fuzzy matching would be unsafe because unrelated artifacts can look
similar after pluralization or wording changes.
"""

from __future__ import annotations

from typing import Any

from app.schemas import Plan, PlanStep


CANONICAL_ARTIFACT_ALIASES: dict[str, str] = {
    "manifest_result": "manifest_update_record",
    "manifest_results": "manifest_update_record",
    "manifest_update_result": "manifest_update_record",
    "manifest_update_results": "manifest_update_record",
    "manifest_update_record_result": "manifest_update_record",
    "moved_item_record": "moved_items_record",
    "moved_item_records": "moved_items_record",
    "moved_items_records": "moved_items_record",
    "moved_files_record": "moved_items_record",
    "moved_files_records": "moved_items_record",
}

CANONICAL_ARTIFACT_IDS: tuple[str, ...] = (
    "allowed_write_paths",
    "change_design",
    "change_summary",
    "final_report",
    "fix_design",
    "manifest_file",
    "manifest_update_record",
    "manifest_validation",
    "moved_items_evidence",
    "moved_items_record",
    "mutation_scope",
    "patch_diff",
    "rollback_patch",
    "rollback_plan",
    "scope_verification",
    "selected_move_candidates",
    "test_results",
    "verification_plan",
    "verification_results",
)


def canonical_artifact_id(artifact_id: str) -> str:
    normalized = str(artifact_id or "").strip()
    return CANONICAL_ARTIFACT_ALIASES.get(normalized, normalized)


def canonicalize_artifact_ids(artifact_ids: list[str]) -> tuple[list[str], list[dict[str, str]]]:
    normalized: list[str] = []
    changes: list[dict[str, str]] = []
    seen: set[str] = set()
    for artifact_id in artifact_ids:
        canonical = canonical_artifact_id(artifact_id)
        if canonical != artifact_id:
            changes.append({"original": artifact_id, "canonical": canonical})
        if canonical not in seen:
            normalized.append(canonical)
            seen.add(canonical)
    return normalized, changes


def canonicalize_plan_artifact_ids(plan: Plan) -> tuple[Plan, list[dict[str, Any]]]:
    steps: list[PlanStep] = []
    changes: list[dict[str, Any]] = []

    for step in plan.steps:
        input_artifacts, input_changes = canonicalize_artifact_ids(step.input_artifacts)
        output_artifacts, output_changes = canonicalize_artifact_ids(step.output_artifacts)
        write_scope_refs, write_scope_changes = canonicalize_artifact_ids(
            list(step.permissions.write_paths_from_artifacts)
        )
        permissions = step.permissions
        if write_scope_changes:
            permissions = permissions.model_copy(update={"write_paths_from_artifacts": write_scope_refs})

        if input_changes:
            changes.append({"step_id": step.step_id, "field": "input_artifacts", "changes": input_changes})
        if output_changes:
            changes.append({"step_id": step.step_id, "field": "output_artifacts", "changes": output_changes})
        if write_scope_changes:
            changes.append(
                {
                    "step_id": step.step_id,
                    "field": "permissions.write_paths_from_artifacts",
                    "changes": write_scope_changes,
                }
            )

        steps.append(
            step.model_copy(
                update={
                    "input_artifacts": input_artifacts,
                    "output_artifacts": output_artifacts,
                    "permissions": permissions,
                }
            )
        )

    if not changes:
        return plan, []

    metadata = dict(plan.metadata)
    metadata["canonical_artifact_aliases"] = [
        *list(metadata.get("canonical_artifact_aliases") or []),
        *changes,
    ]
    return plan.model_copy(update={"steps": steps, "metadata": metadata}), changes


def canonical_artifact_catalog() -> dict[str, Any]:
    return {
        "canonical_ids": list(CANONICAL_ARTIFACT_IDS),
        "exact_aliases": dict(sorted(CANONICAL_ARTIFACT_ALIASES.items())),
        "policy": "Use canonical ids in new plans and worker final artifacts; aliases are normalized only for compatibility.",
    }
