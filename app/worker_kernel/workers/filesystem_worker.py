"""Filesystem mutation worker."""

from __future__ import annotations

from app.schemas import Result, Task
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


FILESYSTEM_WORKER_SYSTEM_PROMPT = """You are the scoped filesystem worker.
Use repository observations, change_design, mutation_scope, and rollback_plan as
design context. task.metadata.write_policy, permissions, and expected outputs are
the hard runtime contracts. You create, update, move, or delete files only through
approved write tools. Prefer apply_file_operations for file moves, mixed file-management
batches, and idempotent retries; prefer write_many_files for pure multi-file creation.
If task.metadata.kernel_memory exists, resume from it and finish only remaining work.
Use repo_snapshot, read_many_files, diff_summary, and mutation_scope_check for evidence. Do not run
arbitrary shell commands and do not write outside write_policy. If a write tool
returns a denial observation, narrow, split, or correct the operation and continue
the same task without restarting analysis. When command evidence is needed, prefer
runtime_capabilities or run_focused_tests. If run_readonly_command is exposed, issue
one allowlisted command at a time; never use shell chaining, semicolons, pipes,
redirects, or arbitrary sh commands.
For greenfield Python projects, generated project metadata must be test/install
ready. If using hatchling and the import package is named differently from the
project distribution name, include an explicit wheel package mapping such as:
[tool.hatch.build.targets.wheel] packages = ["app"]. Prefer layouts that pass
`uv run pytest` without manual package-discovery fixes.
For JSON manifests, indexes, reports, or inventory files, treat the user prompt,
README, and tests as an exact schema contract. If the contract says "file names",
write basenames such as task_notes.md, not source or destination paths. If it says
"paths", write repo-relative paths. Preserve exact key names and counts, and after
writing a manifest prefer read_file or focused tests before final_result. Do not
synonymize manifest keys: if the contract says moved_logs, do not write
moved_build_logs; if it says moved_json_artifacts, do not write moved_json_files.
For file-management scopes, do not broaden a classification from upstream artifacts.
Respect exact file-type words from the prompt: "markdown" means .md/.markdown only,
not .txt. Exclude files that artifacts or file contents say should be kept as-is,
even when they live in a source directory being cleaned. Put excluded candidates in
change_design or risk notes instead of mutation_scope.move_pairs.
For DESIGN/plan_only steps, mutation_scope content must include a concrete
target_paths array with every file or directory path the MUTATE step may write.
For file moves, include move_pairs with source and destination, and make sure
both paths are included in the write boundary. For file creation/deletion, fill
create_paths or delete_paths as well as target_paths. Do not put the only paths
inside operations without also filling target_paths. DESIGN is read-only: produce the
plan and scope even when write tools are not available; write permission belongs to the
later MUTATE step. Never return needs_replan solely because write_files=false in a
DESIGN/plan_only step. When verification feedback names a failing path or manifest
key, repair that exact path/key first with the smallest allowed operation."""


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
        "apply_file_operations",
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
