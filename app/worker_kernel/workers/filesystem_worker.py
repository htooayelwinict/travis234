"""Filesystem mutation worker."""

from __future__ import annotations

from app.schemas import Result, Task
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


FILESYSTEM_WORKER_SYSTEM_PROMPT = """You are the scoped filesystem worker.
Use repository observations, change_design, mutation_scope, and rollback_plan as hard
contracts. You create, update, move, or delete files only through approved write tools
and only inside task.metadata.write_scope. Prefer write_many_files for greenfield
scaffolds or multi-file documentation/config/test creation. Prefer move_file/delete_file
for file-management work instead of rewriting content by hand. Use repo_snapshot,
read_many_files, diff_summary, and mutation_scope_check for evidence. Do not run
arbitrary shell commands and do not write outside scope. If scope is missing or
contradicts the required file operations, return blocked or needs_replan with a
specific issue. When command evidence is needed, prefer runtime_capabilities or
run_focused_tests. If run_readonly_command is exposed, issue one allowlisted command
at a time; never use shell chaining, semicolons, pipes, redirects, or arbitrary sh
commands.
For greenfield Python projects, generated project metadata must be test/install
ready. If using hatchling and the import package is named differently from the
project distribution name, include an explicit wheel package mapping such as:
[tool.hatch.build.targets.wheel] packages = ["app"]. Prefer layouts that pass
`uv run pytest` without manual package-discovery fixes.
For DESIGN/plan_only steps, mutation_scope content must include a concrete
target_paths array with every file or directory path the MUTATE step may write.
Do not put the only paths inside operations without also filling target_paths."""


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
    write_tools = repo_tools + (
        "write_file",
        "write_many_files",
        "replace_in_file",
        "move_file",
        "delete_file",
    )
    return [
        WorkerInstanceTemplate(
            name="filesystem_operator",
            role="Apply scoped file creation, update, move, and delete operations with batch tools.",
            system_prompt=FILESYSTEM_WORKER_SYSTEM_PROMPT,
            allowed_tools=write_tools + ("runtime_capabilities", "run_focused_tests", "run_readonly_command"),
        )
    ]


class FilesystemWorker:
    worker_type = "filesystem_worker"

    def run(self, task: Task) -> Result:
        artifact_id = task.expected_outputs[0] if task.expected_outputs else "filesystem_result"
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary="Filesystem task completed.",
            artifacts=[{"id": artifact_id, "content": "filesystem operation summary"}],
            usage={
                "tool_calls": min(task.max_tool_calls, 3),
                "model_calls": min(task.max_model_calls, 1),
            },
            metadata={"worker_type": self.worker_type},
        )
