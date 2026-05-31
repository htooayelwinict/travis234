"""Worker registry and defaults."""

from __future__ import annotations

from app.worker_kernel.workers import (
    CodeWorker,
    DirectWorker,
    InfraWorker,
    RepoWorker,
    ResearchWorker,
    VerifyWorker,
    WebResearchWorker,
)
from app.worker_kernel.workers.base import BaseWorker


class WorkerRegistry:
    def __init__(self) -> None:
        self._workers: dict[str, BaseWorker] = {}

    def register(self, worker: BaseWorker) -> None:
        self._workers[worker.worker_type] = worker

    def get(self, worker_type: str) -> BaseWorker:
        try:
            return self._workers[worker_type]
        except KeyError as exc:
            raise ValueError(f"Unknown worker_type: {worker_type}") from exc


def build_default_registry() -> WorkerRegistry:
    registry = WorkerRegistry()
    registry.register(DirectWorker())
    registry.register(RepoWorker())
    registry.register(CodeWorker())
    registry.register(ResearchWorker())
    registry.register(WebResearchWorker())
    registry.register(InfraWorker())
    registry.register(VerifyWorker())
    return registry
