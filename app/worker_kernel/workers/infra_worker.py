"""Infrastructure guidance worker."""

from __future__ import annotations

from app.schemas import Result, Task
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


INFRA_WORKER_SYSTEM_PROMPT = """You are the infrastructure diagnosis worker.
Inspect configs, logs, scripts, environment examples, and readonly command evidence.
Do not mutate infrastructure or secrets. Separate confirmed findings from operational
recommendations. If required infra artifacts or permissions are missing, return
needs_replan or blocked with a structured issue. Prefer repo_snapshot, read_many_files,
diff_summary, runtime_capabilities, and focused readonly commands over repeated
primitive scans. Use runtime_capabilities for local toolchain/version discovery.
When run_readonly_command is necessary, issue one allowlisted command at a time;
never use shell chaining, semicolons, pipes, redirects, or arbitrary sh commands."""


def agentic_templates() -> list[WorkerInstanceTemplate]:
    repo_tools = (
        "repo_snapshot",
        "list_dir",
        "read_file",
        "read_many_files",
        "file_search",
        "text_search",
        "json_query",
        "git_status",
        "git_diff",
        "diff_summary",
        "mutation_scope_check",
    )
    command_tools = repo_tools + ("runtime_capabilities", "run_focused_tests", "run_readonly_command")
    return [
        WorkerInstanceTemplate(
            name="infra_diagnoser",
            role="Diagnose infrastructure, configuration, and operational issues using readonly evidence.",
            system_prompt=INFRA_WORKER_SYSTEM_PROMPT,
            allowed_tools=command_tools,
        )
    ]


class InfraWorker:
    worker_type = "infra_worker"

    def run(self, task: Task) -> Result:
        artifact_id = task.expected_outputs[0] if task.expected_outputs else "infra_plan"
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary="Infrastructure guidance generated.",
            artifacts=[{"id": artifact_id, "content": "infra recommendations"}],
            usage={
                "tool_calls": min(task.max_tool_calls, 2),
                "model_calls": min(task.max_model_calls, 1),
            },
            metadata={"worker_type": self.worker_type},
        )
