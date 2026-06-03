"""Registry of planner-visible worker groups to agentic instance templates."""

from __future__ import annotations

from app.worker_kernel.workers import (
    code_worker,
    direct_worker,
    filesystem_worker,
    infra_worker,
    repo_worker,
    research_worker,
    verify_worker,
    web_research_worker,
)
from app.worker_kernel.workers.templates import WorkerInstanceTemplate


def get_agentic_worker_templates() -> dict[str, list[WorkerInstanceTemplate]]:
    return {
        "direct_worker": direct_worker.agentic_templates(),
        "repo_worker": repo_worker.agentic_templates(),
        "research_worker": research_worker.agentic_templates(),
        "web_research_worker": web_research_worker.agentic_templates(),
        "code_worker": code_worker.agentic_templates(),
        "filesystem_worker": filesystem_worker.agentic_templates(),
        "infra_worker": infra_worker.agentic_templates(),
        "verify_worker": verify_worker.agentic_templates(),
    }
