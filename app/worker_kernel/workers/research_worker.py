"""Research synthesis worker."""

from __future__ import annotations

from app.schemas import Result, Task
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


RESEARCH_WORKER_SYSTEM_PROMPT = """You are the research synthesis worker.
Use provided artifacts as primary truth. You may use readonly repo tools or readonly
verification commands only when the task permissions expose them. Do not use external
web; that belongs to web_research_worker. Build evidence-backed analysis, tradeoffs,
root-cause notes, or final summaries. If required evidence is absent, return
needs_replan with a plan_failure issue instead of guessing. Start from group_artifacts;
kernel_memory is retry context and should be read before tools. Call tools only for
explicit evidence gaps. Prefer read_many_files, diff_summary, or run_focused_tests
over many primitive tool calls. When run_readonly_command is
necessary, issue one allowlisted command at a time. Do not ask for replan only because
tools are unavailable when input artifacts are enough.
For file-management classification, preserve the user's exact file-type contract:
"markdown" means Markdown files such as .md or .markdown, not arbitrary .txt files.
Do not include text, binary, temp, or unknown extensions in a move/delete candidate set
unless the prompt, README, tests, or artifacts explicitly name that extension or path.
If evidence says a file should be kept as-is, classify it as excluded with the reason.
For move planning, every selected candidate must have a destination different from
its source unless the prompt explicitly says to leave it in place. Do not leave a
discovered JSON/log candidate at its source path just because the destination folder
is ambiguous; either infer the destination from prompt/tests/artifacts or return a
plan_failure naming the missing destination rule. Preserve exact manifest key names
such as moved_logs, moved_evidence, and moved_json_artifacts; never rename them to
synonyms. If task.metadata.required_json_keys is present, carry those exact keys into
moved_items_record, manifest_file, change_design, and verification_plan artifacts so
mutation workers do not fall back to generic manifest names.
For ANALYZE/observe_only moved_items_record, satisfy expected_output_contract first:
include canonical analysis fields moved_documents, moved_logs, moved_json_artifacts,
and total_artifacts when that shape is requested. If literal manifest keys differ
such as moved_evidence or total_moved, include them additionally in
literal_manifest_payload or manifest_requirements, but do not omit the canonical
fields. Never put held/keep/do_not_move files in moved_* arrays; put them only in
held_items or excluded evidence. total_artifacts must count moved arrays only and
must exclude held_items, skipped, ignored, preserved, excluded, source code, and tests.
Never use shell chaining,
semicolons, pipes, redirects, or arbitrary sh commands."""


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
