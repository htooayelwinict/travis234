"""Coordinates manual compaction with the active agent run."""

from __future__ import annotations

from typing import Literal

from appv231.agent.agent import Agent


class CompactionDeferredError(RuntimeError):
    pass


class CompactionCoordinator:
    def __init__(self, agent: Agent) -> None:
        self._agent = agent

    def prepare(self, timeout: float | None = 30.0) -> Literal["ready", "deferred"]:
        lease = self._agent.run_lease
        if not lease.active:
            return "ready"
        if lease.owned_by_current_thread:
            return "deferred"
        self._agent.abort()
        if not lease.wait(timeout):
            raise TimeoutError("Timed out waiting for the active run before compaction")
        return "ready"


__all__ = ["CompactionCoordinator", "CompactionDeferredError"]
