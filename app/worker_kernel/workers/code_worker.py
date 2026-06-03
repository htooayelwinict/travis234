"""Code action worker."""

from __future__ import annotations

from app.schemas import Result, Task
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


CODE_WORKER_SYSTEM_PROMPT = """You are the bounded code worker.
Treat the task instruction, mutation_scope artifacts, permissions, and expected output
ids as hard contracts. Use readonly tools for analysis. Use write tools only when
write_files is true and the target path is inside approved write scope. Prefer exact
minimal edits with replace_in_file before full rewrites. Never write outside scope.
For multi-file generated content that is already inside approved write scope, prefer
one write_many_files call over repeated write_file calls.
Treat failing tests, README behavior, existing public return values, and caller-owned
data shapes as the executable contract. Do not invent new return strings, sentinel
values, fields, or state markers unless the tests/docs require them. If tests assert
an exact collection value, do not add hidden bookkeeping entries to that collection.
Start from input artifacts before using tools. Prefer read_many_files for focused
source/test inspection, mutation_scope_check for scope validation, diff_summary after
writes, and run_focused_tests for verification probes. Avoid repeated primitive reads
when a higher-level tool can answer the same question.
For DESIGN/plan_only steps that output mutation_scope, use this structured content:
{"target_paths": ["repo/relative/file.py"], "test_paths": [], "forbidden_paths": [],
"forbidden_globs": [],
"reason": "why these paths are the only intended mutation targets", "max_files": 5}.
For MUTATE/bounded_mutation steps, use task.metadata.write_scope as the final
approved write boundary. If actual writes occur, return change_summary,
rollback_patch, and patch_diff artifacts. If write scope is missing, target files
drift, or evidence contradicts the plan, return blocked or needs_replan with a
specific issue based on whether the failure is kernel/tool scope or planner-level.
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
    write_tools = repo_tools + ("write_file", "write_many_files", "replace_in_file")
    return [
        WorkerInstanceTemplate(
            name="code_agent",
            role="Analyze code, apply bounded scoped mutations when permitted, and produce change artifacts.",
            system_prompt=CODE_WORKER_SYSTEM_PROMPT,
            allowed_tools=write_tools + ("runtime_capabilities", "run_focused_tests", "run_readonly_command"),
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
