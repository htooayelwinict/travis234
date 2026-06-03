"""External web research worker."""

from __future__ import annotations

from app.schemas import Result, Task
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


WEB_SOURCE_DISCOVERY_PROMPT = """You are the web source discovery worker.
Find authoritative, current, and relevant sources for the task. Use web_search only
when web_research permission is present. Return source candidate artifacts with urls,
titles, relevance, and why each source should or should not be trusted. If search is
unavailable or insufficient, return needs_replan with a plan_failure issue."""

WEB_SOURCE_EXTRACTION_PROMPT = """You are the web source extraction worker.
Fetch only selected source urls from earlier artifacts, extract concise evidence, and
preserve source provenance. Do not overquote. If required sources cannot be fetched,
return needs_replan with source-specific failure metadata."""

WEB_CITATION_SYNTHESIS_PROMPT = """You are the web citation synthesis worker.
Turn collected source excerpts into cited research artifacts. Separate source-backed
facts from inference. Do not add uncited claims."""


def agentic_templates() -> list[WorkerInstanceTemplate]:
    return [
        WorkerInstanceTemplate(
            name="source_discovery",
            role="Discover candidate external sources and rank them for usefulness and trust.",
            system_prompt=WEB_SOURCE_DISCOVERY_PROMPT,
            allowed_tools=("web_search",),
        ),
        WorkerInstanceTemplate(
            name="source_extraction",
            role="Fetch selected sources and extract relevant evidence with provenance.",
            system_prompt=WEB_SOURCE_EXTRACTION_PROMPT,
            allowed_tools=("web_fetch",),
        ),
        WorkerInstanceTemplate(
            name="citation_formatter",
            role="Synthesize extracted source artifacts into cited research output.",
            system_prompt=WEB_CITATION_SYNTHESIS_PROMPT,
            allowed_tools=(),
        ),
    ]


class WebResearchWorker:
    worker_type = "web_research_worker"

    def run(self, task: Task) -> Result:
        artifact_id = task.expected_outputs[0] if task.expected_outputs else "web_research_notes"
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary="Web research synthesis produced.",
            artifacts=[
                {
                    "id": artifact_id,
                    "content": "web research summary",
                    "sources": [],
                }
            ],
            usage={
                "tool_calls": min(task.max_tool_calls, 4),
                "model_calls": min(task.max_model_calls, 1),
            },
            metadata={"worker_type": self.worker_type},
        )
