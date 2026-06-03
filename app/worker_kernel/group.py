"""Worker-group runners used by the kernel dispatcher."""

from __future__ import annotations

from typing import Protocol

from app.schemas import Result, Task
from app.worker_kernel.workers.base import BaseWorker


class WorkerGroupRunner(Protocol):
    worker_type: str

    def run(self, task: Task) -> Result:
        """Run a worker group for one compiled task."""


class SingleInstanceWorkerGroupRunner:
    """Compatibility wrapper that treats an existing worker as a one-instance group."""

    def __init__(self, worker: BaseWorker) -> None:
        self._worker = worker
        self.worker_type = worker.worker_type

    def run(self, task: Task) -> Result:
        return self._worker.run(task)


class SequentialWorkerGroupRunner:
    """Runs several internal worker instances under one planner-visible worker type."""

    def __init__(self, *, worker_type: str, workers: list[BaseWorker]) -> None:
        if not workers:
            raise ValueError("SequentialWorkerGroupRunner requires at least one worker.")
        self.worker_type = worker_type
        self._workers = workers

    def run(self, task: Task) -> Result:
        group_results: list[dict] = []
        artifacts = []
        usage = {"tool_calls": 0, "model_calls": 0}
        last_result: Result | None = None

        for index, worker in enumerate(self._workers, start=1):
            instance_task = task.model_copy(
                update={
                    "metadata": {
                        **task.metadata,
                        "worker_group": self.worker_type,
                        "instance_index": str(index),
                    }
                }
            )
            result = worker.run(instance_task)
            last_result = result
            group_results.append(result.model_dump(mode="json"))
            artifacts.extend(result.artifacts)
            usage["tool_calls"] += int(result.usage.get("tool_calls", 0) or 0)
            usage["model_calls"] += int(result.usage.get("model_calls", 0) or 0)

            if result.status != "completed":
                metadata = dict(result.metadata)
                metadata["worker_group_results"] = group_results
                return result.model_copy(
                    update={
                        "producer": self.worker_type,
                        "artifacts": artifacts,
                        "usage": usage,
                        "metadata": metadata,
                    }
                )

        summary = last_result.summary if last_result is not None else "Worker group completed."
        return Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary=summary,
            artifacts=artifacts,
            usage=usage,
            metadata={"worker_group_results": group_results},
        )
