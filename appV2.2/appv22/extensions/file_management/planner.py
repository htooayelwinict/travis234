from __future__ import annotations

import json


class ModelAuthoredFilePlanner:
    capability_id = "file_management.model_authored_file_planner"

    def plan(self, state, decision_payload: dict | None = None) -> dict:
        model_plan = _model_authored_plan(decision_payload or {})
        if model_plan is not None:
            return model_plan
        raise ValueError("model_authored_plan_required")


def _model_authored_plan(payload: dict) -> dict | None:
    if not isinstance(payload, dict):
        return None

    artifact_plan = _artifact_plan(payload)
    if artifact_plan is not None:
        return artifact_plan

    mutation_intent = payload.get("mutation_intent")
    if isinstance(mutation_intent, dict) and isinstance(mutation_intent.get("operations"), list):
        return _with_runtime_capabilities(
            {
                "planner_id": "file_management.model_authored_file_planner",
                "mutation_intent": mutation_intent,
                "verification_intent": payload.get("verification_intent", {}),
            }
        )

    return None


def _artifact_plan(payload: dict) -> dict | None:
    artifact = payload.get("proposed_artifact")
    if not isinstance(artifact, dict):
        return None
    path = artifact.get("path", artifact.get("relative_path"))
    content = artifact.get("content", "")
    if not isinstance(path, str) or not path:
        return None
    if not isinstance(content, str):
        content = json.dumps(content, indent=2, sort_keys=True)

    return _with_runtime_capabilities(
        {
            "planner_id": "file_management.model_authored_file_planner",
            "mutation_intent": {
                "operation_batch_id": "model_authored_file_creation",
                "operations": [
                    {
                        "action": "write",
                        "path": path,
                        "content": content,
                    }
                ],
            },
            "verification_intent": {
                "created_files": [
                    {
                        "path": path,
                        "content": content,
                    }
                ]
            },
        }
    )


def _with_runtime_capabilities(plan: dict) -> dict:
    plan.setdefault("planner_id", "file_management.model_authored_file_planner")
    plan.setdefault("mutation_policy_id", "file_management.safe_file_mutations")
    plan.setdefault("mutation_executor_id", "file_management.file_mutation_executor")
    plan.setdefault("verifier_id", "file_management.manifest_verifier")
    plan.setdefault("mutation_intent", {"operation_batch_id": "model_authored", "operations": []})
    plan["mutation_intent"].setdefault("operation_batch_id", "model_authored")
    plan.setdefault("verification_intent", {})
    return plan
