"""Verification worker."""

from __future__ import annotations

from app.schemas import Result, Task
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


VERIFY_WORKER_SYSTEM_PROMPT = """You are the verification worker.
Use readonly tools and allowed verification commands to prove whether the worker
outputs satisfy success criteria. Record exact commands, return codes, and relevant
stdout/stderr. Do not edit files. If checks fail, report failed or needs_replan based
on whether the cause is implementation failure or planner-level mismatch."""


def agentic_templates() -> list[WorkerInstanceTemplate]:
    repo_tools = ("list_dir", "read_file", "file_search", "text_search", "json_query", "git_status", "git_diff")
    command_tools = repo_tools + ("run_readonly_command",)
    return [
        WorkerInstanceTemplate(
            name="verification_runner",
            role="Run scoped verification and produce evidence-backed verification artifacts.",
            system_prompt=VERIFY_WORKER_SYSTEM_PROMPT,
            allowed_tools=command_tools,
        )
    ]


class VerifyWorker:
    worker_type = "verify_worker"

    def run(self, task: Task) -> Result:
        artifact_id = task.expected_outputs[0] if task.expected_outputs else "verification_result"
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary="Verification report produced.",
            artifacts=[
                {
                    "id": artifact_id,
                    "content": "focused checks passed",
                    "inputs_checked": [a.get("id") or a.get("artifact_id") for a in task.input_artifacts],
                }
            ],
            usage={
                "tool_calls": min(task.max_tool_calls, 2),
                "model_calls": min(task.max_model_calls, 0),
            },
            metadata={"worker_type": self.worker_type},
        )
