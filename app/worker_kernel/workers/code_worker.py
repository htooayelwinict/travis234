"""Code action worker."""

from __future__ import annotations

from app.schemas import Result, Task
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


CODE_WORKER_SYSTEM_PROMPT = """You are the bounded code worker.
Treat the task instruction, mutation_scope artifacts, permissions, and expected output
ids as hard contracts. Use readonly tools for analysis. Use write tools only when
write_files is true and the target path is inside approved write scope. Prefer exact
minimal edits with replace_in_file before full rewrites. Never write outside scope.
If write scope is missing, target files drift, or evidence contradicts the plan,
return needs_replan with a plan_failure issue."""


def agentic_templates() -> list[WorkerInstanceTemplate]:
    repo_tools = ("list_dir", "read_file", "file_search", "text_search", "json_query", "git_status", "git_diff")
    write_tools = repo_tools + ("write_file", "replace_in_file")
    return [
        WorkerInstanceTemplate(
            name="code_agent",
            role="Analyze code, apply bounded scoped mutations when permitted, and produce change artifacts.",
            system_prompt=CODE_WORKER_SYSTEM_PROMPT,
            allowed_tools=write_tools + ("run_readonly_command",),
        )
    ]


class CodeWorker:
    worker_type = "code_worker"

    def run(self, task: Task) -> Result:
        artifact_id = task.expected_outputs[0] if task.expected_outputs else "patch_result"
        action = "applied" if task.permissions.get("write_files") else "proposed"
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary=f"Code fix {action}.",
            artifacts=[
                {
                    "id": artifact_id,
                    "content": f"code change {action}",
                    "write_files": bool(task.permissions.get("write_files")),
                }
            ],
            usage={
                "tool_calls": min(task.max_tool_calls, 3),
                "model_calls": min(task.max_model_calls, 1),
            },
            metadata={"worker_type": self.worker_type},
        )
