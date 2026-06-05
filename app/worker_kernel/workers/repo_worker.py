"""Repository context worker."""

from __future__ import annotations

from app.schemas import Result, Task
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


REPO_LOCATOR_SYSTEM_PROMPT = """You are the repository locator instance.
Find likely target files, tests, and command/config clues using the fewest readonly
calls possible. Prefer one repo_snapshot call before primitive search. Use file_search
or text_search only when repo_snapshot does not expose enough evidence. Avoid reading
large files in this instance unless there is no reader instance left. Produce candidate
path artifacts with evidence from tool observations. Tool paths are relative to the
already-mounted repository root; use "." for whole-repo inventory. Use exact tool names:
read_file, read_many_files, file_search, text_search, repo_snapshot. Do not invent
synonyms such as file_read, batch_read, grep, or ls. If kernel_memory is
present, use it to avoid repeating failed discovery. In ANALYZE/observe_only mode,
produce expected artifacts from available evidence; do not ask for mutation permission.
Never mutate files. For file-management tasks, preserve exact user/test categories:
Markdown means .md/.markdown, logs mean named log files, JSON artifacts mean named
diagnostic/data JSON files only when prompt/tests/artifacts imply they should move,
and excluded files must be recorded with reasons. Capture exact manifest/report key
names from README, tests, prompt text, or existing files so downstream workers do not
invent synonyms."""

REPO_READER_SYSTEM_PROMPT = """You are the repository reader instance.
Read the highest-value candidate source and test files from earlier group artifacts and
tool observations. Prefer one read_many_files call over repeated read_file calls.
Extract exact functions, failing assertions, commands, and local contracts. Keep
evidence concise and path-specific. Tool paths are relative to the mounted repository
root, not parent workspace paths. If kernel_memory is present, read it before tools. If command evidence is needed, prefer
run_focused_tests or one allowlisted run_readonly_command at a time. Never use shell
chaining, semicolons, pipes, redirects, or arbitrary sh commands. If tools are not
available but input artifacts are sufficient, synthesize final artifacts. Never mutate files."""

REPO_SUMMARIZER_SYSTEM_PROMPT = """You are the repository discovery summarizer.
Use only group artifacts and tool observations to produce the exact expected output
artifacts. Every artifact must be an object with id and content. Content should be
structured, evidence-backed, and useful to downstream analyze/design workers. If the
observations are insufficient, return needs_replan with precise missing evidence. Do
not request tools; synthesize from artifacts and kernel_memory first. For file
management, emit exact source paths, intended destination paths when inferable, and
manifest/report key names as structured fields. Do not return generic completed
summaries; include the concrete files, categories, and evidence that justify each
expected artifact."""


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
    locator_tools = ("repo_snapshot", "file_search", "text_search", "git_status", "diff_summary")
    reader_tools = repo_tools + ("runtime_capabilities", "run_focused_tests", "run_readonly_command")
    return [
        WorkerInstanceTemplate(
            name="repo_locator",
            role="Locate target files, tests, and repo clues with cheap readonly discovery tools.",
            system_prompt=REPO_LOCATOR_SYSTEM_PROMPT,
            allowed_tools=locator_tools,
        ),
        WorkerInstanceTemplate(
            name="repo_reader",
            role="Read focused candidate files and extract path-specific source and test evidence.",
            system_prompt=REPO_READER_SYSTEM_PROMPT,
            allowed_tools=reader_tools,
        ),
        WorkerInstanceTemplate(
            name="repo_summarizer",
            role="Convert repository observations into the exact expected discovery artifacts.",
            system_prompt=REPO_SUMMARIZER_SYSTEM_PROMPT,
            allowed_tools=(),
        ),
    ]


class RepoWorker:
    worker_type = "repo_worker"

    def run(self, task: Task) -> Result:
        artifact_id = task.expected_outputs[0] if task.expected_outputs else "target_observation"
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary="Repository context collected.",
            artifacts=[{"id": artifact_id, "content": "repository scan summary"}],
            usage={
                "tool_calls": min(task.max_tool_calls, 2),
                "model_calls": min(task.max_model_calls, 1),
            },
            metadata={"worker_type": self.worker_type},
        )
