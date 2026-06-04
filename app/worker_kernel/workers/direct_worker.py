"""Direct response worker."""

from __future__ import annotations

from app.schemas import Result, Task
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


DIRECT_WORKER_SYSTEM_PROMPT = """You are the direct guidance worker.
Answer only from the scoped task context, input artifacts, and envelope summary.
If task.metadata.kernel_memory exists, use it as retry context.
Do not invent missing facts. If the task cannot be answered from context, return a
needs_replan final_result with a plan_failure issue that names the missing context.
Produce artifacts whose ids exactly match expected_outputs when possible. Artifact
content must match expected_output_contract; for final_report use summary, findings,
and optional path instead of plain text when a structured report is expected."""


def agentic_templates() -> list[WorkerInstanceTemplate]:
    return [
        WorkerInstanceTemplate(
            name="direct_responder",
            role="Produce a direct user-facing answer from scoped context without tools.",
            system_prompt=DIRECT_WORKER_SYSTEM_PROMPT,
            allowed_tools=(),
        )
    ]


class DirectWorker:
    worker_type = "direct_worker"

    def run(self, task: Task) -> Result:
        artifact_id = task.expected_outputs[0] if task.expected_outputs else "direct_answer"
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary="Direct response generated.",
            artifacts=[{"id": artifact_id, "content": task.instruction}],
            usage={
                "tool_calls": min(task.max_tool_calls, 0),
                "model_calls": min(task.max_model_calls, 1),
            },
            metadata={"worker_type": self.worker_type},
        )
