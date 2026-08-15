"""Native-TUI ownership for supervised child-agent observation and control."""

from __future__ import annotations

import re

from travis.coding_agent.subagent_supervision import SupervisorSnapshot
from travis.tui.components import StatusLine, Text
from travis.tui.components.subagent_roster import SubagentRoster


class InteractiveSubagents:
    def _bind_subagent_supervisor(self) -> None:
        self._shutdown_subagent_ui()
        supervisor = getattr(self.app.session, "subagents", None)
        subscribe = getattr(supervisor, "subscribe", None)
        if not callable(subscribe):
            return
        self._unsubscribe_subagents = subscribe(
            lambda snapshot: self.tui.dispatcher.post(
                lambda: self._apply_subagent_snapshot(snapshot)
            )
        )

    def _apply_subagent_snapshot(self, snapshot: SupervisorSnapshot) -> None:
        if snapshot.revision <= self._subagent_snapshot.revision:
            return
        self._subagent_snapshot = snapshot
        self.tui.request_render()

    def _rebind_subagent_supervisor(self) -> None:
        supervisor = getattr(self.app.session, "subagents", None)
        snapshot = getattr(supervisor, "snapshot", None)
        if callable(snapshot):
            self._subagent_snapshot = snapshot()
        self._bind_subagent_supervisor()

    def _shutdown_subagent_ui(self) -> None:
        if self._unsubscribe_subagents is not None:
            self._unsubscribe_subagents()
            self._unsubscribe_subagents = None

    def _run_agents_command(self, command: tuple[str, tuple[str, ...]]) -> None:
        action, args = command
        supervisor = getattr(self.app.session, "subagents", None)
        if supervisor is None:
            self.history.add(StatusLine("Subagent supervisor is unavailable.", kind="error"))
        elif action == "status":
            snapshot = supervisor.snapshot()
            self._subagent_snapshot = snapshot
            self.history.add(SubagentRoster(snapshot))
        elif action == "inspect":
            self._inspect_subagent(supervisor, args[0])
        elif action == "steer":
            result = supervisor.steer(args[0], args[1])
            kind = "info" if result.accepted else "error"
            self.history.add(StatusLine(f"Agent {args[0]}: {result.code}", kind=kind))
        elif action == "cancel":
            if not self.prompt_extension_confirm(
                "Cancel delegated agent", f"Cancel {args[0]}?"
            ):
                self.history.add(StatusLine("Agent cancel skipped.", kind="info"))
            else:
                try:
                    result = supervisor.cancel(args[0], "Cancelled from native TUI.")
                    self.history.add(
                        StatusLine(f"Agent {args[0]}: {result.status}", kind="warning")
                    )
                except KeyError:
                    self.history.add(StatusLine(f"Unknown agent: {args[0]}", kind="error"))
        else:
            self.history.add(
                StatusLine(
                    "Usage: /agents [status|inspect <id>|steer <id> <message>|cancel <id>]",
                    kind="error",
                )
            )
        self.status.set_message("Idle")
        self._refresh_footer()
        self.tui.request_render()

    def _inspect_subagent(self, supervisor: object, task_id: str) -> None:
        getter = getattr(supervisor, "get_result", None)
        result = getter(task_id) if callable(getter) else None
        if result is None:
            task = next(
                (item for item in self._subagent_snapshot.tasks if item.task_id == task_id),
                None,
            )
            if task is None:
                self.history.add(StatusLine(f"Unknown agent: {task_id}", kind="error"))
            else:
                self.history.add(
                    Text(f"Agent {task.task_id}\nrole: {task.role}\nstatus: {task.status}")
                )
            return
        prepare = getattr(self.app.session, "_prepare_public_subagent_result", None)
        if callable(prepare):
            result = prepare(result)
        lines = [
            f"Agent {result.task_id}",
            f"role: {result.role}",
            f"backend: {result.backend}",
            f"status: {result.status}",
            f"summary: {_safe_inspection_text(result.summary)[:1000]}",
        ]
        if result.artifacts:
            lines.append("artifacts: " + ", ".join(result.artifacts[:16]))
        if result.validation_errors:
            lines.append("validation: " + "; ".join(result.validation_errors[:8]))
        self.history.add(Text("\n".join(lines)))


def _safe_inspection_text(value: str) -> str:
    text = " ".join(str(value).split())
    return re.sub(r"(?<![A-Za-z0-9_.-])(?:/[A-Za-z0-9_.-]+){2,}", "[path]", text)


__all__ = ["InteractiveSubagents"]
