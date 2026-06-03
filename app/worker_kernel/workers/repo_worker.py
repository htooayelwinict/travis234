"""Repository context worker."""

from __future__ import annotations

from app.schemas import Result, Task
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


REPO_LOCATOR_SYSTEM_PROMPT = """You are the repository locator instance.
Find likely target files, tests, and command/config clues using cheap readonly search.
Prefer file_search, text_search, git_status, and shallow list_dir calls. Avoid reading
large files in this instance unless there is no reader instance left. Produce candidate
path artifacts with evidence from tool observations. Never mutate files."""

REPO_READER_SYSTEM_PROMPT = """You are the repository reader instance.
Read the highest-value candidate source and test files from earlier group artifacts and
tool observations. Extract exact functions, failing assertions, commands, and local
contracts. Keep evidence concise and path-specific. Never mutate files."""

REPO_SUMMARIZER_SYSTEM_PROMPT = """You are the repository discovery summarizer.
Use only group artifacts and tool observations to produce the exact expected output
artifacts. Every artifact must be an object with id and content. Content should be
structured, evidence-backed, and useful to downstream analyze/design workers. If the
observations are insufficient, return needs_replan with precise missing evidence."""


def agentic_templates() -> list[WorkerInstanceTemplate]:
    repo_tools = ("list_dir", "read_file", "file_search", "text_search", "json_query", "git_status", "git_diff")
    locator_tools = ("list_dir", "file_search", "text_search", "git_status", "git_diff")
    reader_tools = repo_tools + ("run_readonly_command",)
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
