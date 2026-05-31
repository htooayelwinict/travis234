"""External web research worker."""

from __future__ import annotations

from app.schemas import Result, Task


class WebResearchWorker:
    worker_type = "web_research_worker"

    def run(self, task: Task) -> Result:
        artifact_id = task.expected_outputs[0] if task.expected_outputs else "web_research_notes"
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary="Web research synthesis produced.",
            artifacts=[
                {
                    "id": artifact_id,
                    "content": "web research summary",
                    "sources": [],
                }
            ],
            usage={
                "tool_calls": min(task.max_tool_calls, 4),
                "model_calls": min(task.max_model_calls, 1),
            },
            metadata={"worker_type": self.worker_type},
        )
