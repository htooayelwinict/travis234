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
from app.worker_kernel.group import SingleInstanceWorkerGroupRunner, WorkerGroupRunner
from app.worker_kernel.workers.base import BaseWorker


class WorkerRegistry:
    def __init__(self) -> None:
        self._groups: dict[str, WorkerGroupRunner] = {}

    def register(self, worker: BaseWorker) -> None:
        self.register_group(SingleInstanceWorkerGroupRunner(worker))

    def register_group(self, group: WorkerGroupRunner) -> None:
        self._groups[group.worker_type] = group

    def get(self, worker_type: str) -> WorkerGroupRunner:
        try:
            return self._groups[worker_type]
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
