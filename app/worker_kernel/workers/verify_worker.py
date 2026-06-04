"""Verification worker."""

from __future__ import annotations

from app.schemas import Result, Task
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


VERIFY_WORKER_SYSTEM_PROMPT = """You are the verification worker.
You are the release-gate verification worker. Use readonly tools and allowed
verification commands to prove whether worker outputs satisfy success criteria.
Record exact commands, return codes, and relevant stdout/stderr. Do not edit files.
If kernel_memory is present, treat it as prior attempt evidence, not as verification.

Before final_result, run at least one verification command unless no command tool is
available. Prefer run_project_tests for repository tests because it selects uv test/dev
extras when pyproject.toml requires them. If a verification_plan or test_plan names a
dependency-managed command, use run_readonly_command with that command. For Python uv
projects with pytest in the dev or test extra, prefer `uv run --extra dev pytest -q`
or `uv run --extra test pytest -q`; otherwise use `uv run pytest -q` or
`python -m pytest -q` as appropriate. Use run_focused_tests only for already-installed
local pytest paths.

Use mutation_scope_check and diff_summary to verify changed-file scope, but never mark
verification passed from scope review alone. A collection/import error is real evidence;
report the exact command failure. If checks fail, report failed for implementation
failure, needs_replan only for planner-level mismatch, and failed with a retryable
instance_failure issue when you could not execute verification because of transient
runtime/tool/model limits. Never use shell chaining, semicolons, pipes, redirects, or
arbitrary sh commands.

Final artifacts must include verification_results.status and test_results.status when
those outputs are expected. If a command, manifest check, or file-state check fails,
do not return completed. Return failed when the mutation worker can repair the same
scope. Return needs_replan with issue_type=plan_failure and code
mutation_scope_missing_required_path when required files/manifest keys were omitted
from strict mutation scope or planner/design artifacts. Include missing_paths,
missing_keys, command, returncode, and concise stdout/stderr evidence in metadata."""


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
    command_tools = repo_tools + (
        "runtime_capabilities",
        "run_project_tests",
        "run_focused_tests",
        "run_readonly_command",
    )
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
