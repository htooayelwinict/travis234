"""Research synthesis worker."""

from __future__ import annotations

from app.schemas import Result, Task
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


RESEARCH_WORKER_SYSTEM_PROMPT = """You are the research synthesis worker.
Use provided artifacts as primary truth. You may use readonly repo tools or readonly
verification commands only when the task permissions expose them. Do not use external
web; that belongs to web_research_worker. Build evidence-backed analysis, tradeoffs,
root-cause notes, or final summaries. If required evidence is absent, return
needs_replan with a plan_failure issue instead of guessing."""


def agentic_templates() -> list[WorkerInstanceTemplate]:
    repo_tools = ("list_dir", "read_file", "file_search", "text_search", "json_query", "git_status", "git_diff")
    command_tools = repo_tools + ("run_readonly_command",)
    return [
        WorkerInstanceTemplate(
            name="context_synthesizer",
            role="Synthesize input artifacts and optional readonly observations into evidence-backed worker artifacts.",
            system_prompt=RESEARCH_WORKER_SYSTEM_PROMPT,
            allowed_tools=command_tools,
        )
    ]


class ResearchWorker:
    worker_type = "research_worker"

    def run(self, task: Task) -> Result:
        artifact_id = task.expected_outputs[0] if task.expected_outputs else "research_notes"
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary="Research synthesis produced.",
            artifacts=[{"id": artifact_id, "content": "research summary"}],
            usage={
                "tool_calls": min(task.max_tool_calls, 3),
                "model_calls": min(task.max_model_calls, 1),
            },
            metadata={"worker_type": self.worker_type},
        )
