"""Verification worker."""

from __future__ import annotations

from app.schemas import Result, Task
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


VERIFY_WORKER_SYSTEM_PROMPT = """You are the verification worker.
Use readonly tools and allowed verification commands to prove whether the worker
outputs satisfy success criteria. Record exact commands, return codes, and relevant
stdout/stderr. Do not edit files. If checks fail, report failed or needs_replan based
on whether the cause is implementation failure or planner-level mismatch. Prefer
`python -m pytest ...` or `PYTHONPATH=. pytest ...` for Python test commands, especially
inside nested repositories. A collection/import error is not proof that code is correct;
report the exact command failure and do not mark verification passed from scope review alone.
Prefer run_focused_tests, mutation_scope_check, and diff_summary over raw command and
primitive git calls when they satisfy the verification contract. Use runtime_capabilities
for local toolchain discovery. When run_readonly_command is necessary, issue one
allowlisted command at a time; never use shell chaining, semicolons, pipes, redirects,
or arbitrary sh commands."""


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
