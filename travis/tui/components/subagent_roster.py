"""Bounded native-TUI rendering for supervisor snapshots."""

from __future__ import annotations

import time
from collections.abc import Callable

from travis.coding_agent.subagent_supervision import SupervisorSnapshot
from travis.tui.components.base import Component


class SubagentRoster(Component):
    def __init__(
        self,
        snapshot: SupervisorSnapshot,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.snapshot = snapshot
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))

    def render(self, width: int) -> list[str]:
        safe_width = max(1, int(width))
        lines = [_fit(f"Agents: {self.snapshot.active_count}/{self.snapshot.capacity} active", safe_width)]
        if not self.snapshot.tasks:
            lines.append(_fit("  No subagents have been spawned in this session.", safe_width))
            return lines
        now = self._clock_ms()
        for task in self.snapshot.tasks[:32]:
            end = task.ended_at_ms or now
            elapsed = max(0, end - task.started_at_ms) // 1000
            controls = "steer,cancel" if task.controllable else "inspect"
            line = f"  {task.task_id[:12]} {task.role[:12]} {task.status} {elapsed}s [{controls}]"
            if task.summary_preview:
                line += f" | {task.summary_preview}"
            lines.append(_fit(line, safe_width))
        if len(self.snapshot.tasks) > 32:
            lines.append(_fit(f"  ... {len(self.snapshot.tasks) - 32} more agents", safe_width))
        return lines


def _fit(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[: max(0, width - 1)] + "…"


__all__ = ["SubagentRoster"]
