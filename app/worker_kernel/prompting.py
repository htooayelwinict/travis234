"""Prompt assembly for agentic worker instances."""

from __future__ import annotations

import json
from typing import Any

from app.schemas import ArtifactPayload, Task
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


def build_agentic_prompt(
    *,
    worker_type: str,
    template: WorkerInstanceTemplate,
    task: Task,
    usage: dict[str, int],
    available_tools: list[dict[str, Any]],
    group_artifacts: list[ArtifactPayload],
    tool_observations: list[dict[str, Any]],
    expected_output_contract: list[dict[str, Any]],
) -> str:
    """Build the single JSON prompt contract shared by all worker instances."""

    remaining_tool_calls = max(0, task.max_tool_calls - usage["tool_calls"])
    instructions = _worker_instructions(task)
    payload = {
        "worker_type": worker_type,
        "instance": {
            "name": template.name,
            "role": template.role,
            "system_prompt": template.system_prompt,
        },
        "task": task.model_dump(mode="json"),
        "runtime_budget": {
            "tool_calls_used": usage["tool_calls"],
            "remaining_tool_calls": remaining_tool_calls,
            "model_calls_used_including_this_turn": usage["model_calls"],
            "remaining_model_calls_after_this_turn": max(0, task.max_model_calls - usage["model_calls"]),
        },
        "expected_output_contract": expected_output_contract,
        "final_result_example": {
            "final_result": {
                "status": "completed",
                "summary": "One concise sentence describing the completed worker output.",
                "artifacts": [
                    {
                        "id": artifact_id,
                        "content": contract["artifact_shape"]["content"],
                        "kind": "worker_output",
                    }
                    for artifact_id, contract in zip(
                        task.expected_outputs[:3],
                        expected_output_contract[:3],
                        strict=False,
                    )
                ],
            }
        },
        "available_tools": available_tools,
        "group_artifacts": [artifact.model_dump(mode="json") for artifact in group_artifacts],
        "tool_observations": tool_observations,
        "instructions": instructions,
    }
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _worker_instructions(task: Task) -> list[str]:
    instructions = [
        "Return JSON matching the schema.",
        "available_tools are OpenAI-style function tool specs; request only those names.",
        "For tool use, return {'tool_calls': [{'tool_name': '<name>', 'arguments': {...}}]}.",
        "OpenAI/OpenRouter function-call shape {'function': {'name': '<name>', 'arguments': '{...}'}} is also accepted.",
        "A response with tool_calls is an action turn; after observations, return a separate final_result turn.",
        "Use only listed tools.",
        "Do not invent synonym tool names: use read_file, not file_read; use read_many_files, not batch_read; use apply_file_operations, not move_files.",
        "Tool observations are the source of truth. Never claim files were moved, written, deleted, read, or tests run unless completed tool observations or kernel_memory prove it.",
        "If observations contain a repairable tool failure or denial and tool budget remains, return corrected tool_calls instead of final_result.",
        "For MUTATE/bounded_mutation, completed final_result requires completed write-tool evidence or kernel_memory proving successful writes.",
        "For apply_file_operations, prefer arguments {'operations':[{'action':'move','source':'...','destination':'...'}, {'action':'write','path':'...','content':'...'}, {'action':'create_directory','path':'...'}]}.",
        "If available_tools is empty or remaining_tool_calls is 0, return final_result from observations or needs_replan.",
        "For observe_only, plan_only, verify_only, and summarize_only tasks, synthesize from input/group artifacts when they are sufficient; do not return needs_replan solely because tools or write permissions are unavailable.",
        "needs_replan is only for semantic planner gaps such as missing required artifacts, wrong worker ordering, or user-intent drift.",
        "Return final_result only when expected artifacts can be produced.",
        "final_result.artifacts must be objects with id and non-null, non-empty content; never return bare artifact-name strings.",
        "Before final_result, compare each artifact against expected_output_contract.artifact_shape and include its required keys.",
        "Use the exact expected_output_contract ids in final_result.artifacts; do not use near-aliases such as manifest_update_result or moved_item_records.",
        "Issue issue_type must be exactly one of instance_failure, plan_failure, or kernel_failure; do not invent issue_type values.",
        "For file-management, reports, JSON manifests, and inventory outputs, copy exact key names, file categories, and path/basename wording from task instructions, tests, and input artifacts.",
        "For planner-level gaps, return {'final_result': {'status': 'needs_replan', 'summary': '<why>', 'issues': [...]}}.",
        "Use failed with an instance_failure issue for transient model/tool mistakes.",
    ]
    runtime_retry_instruction = task.metadata.get("runtime_retry_instruction")
    if runtime_retry_instruction:
        instructions.insert(0, str(runtime_retry_instruction))
    if task.metadata.get("kernel_memory"):
        instructions.insert(
            0,
            (
                "This task has kernel_memory from previous worker attempts. Use it as "
                "authoritative retry context: do not replay successful operations unless "
                "current state proves they are missing; finish remaining work and return "
                "the expected artifacts."
            ),
        )
    return instructions
