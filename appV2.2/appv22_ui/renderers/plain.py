from __future__ import annotations

from appv22_ui.events import events_from_result, result_summary


class PlainRenderer:
    def render(self, result: dict | None) -> str:
        summary = result_summary(result)
        lines = [
            f"session: {summary['session_id'] or '-'}",
            f"status: {summary['status']}",
            f"reason: {summary['reason'] or '-'}",
            f"world_refs: {summary['world_ref_count']}",
        ]
        if isinstance(result, dict) and result.get("assistant_message"):
            lines.append(f"assistant: {result['assistant_message']}")
        usage = summary.get("usage")
        if usage:
            lines.append(f"usage: {usage}")
        context_summary = summary.get("context_summary")
        if isinstance(context_summary, dict) and context_summary:
            blockers = context_summary.get("blockers")
            progress = context_summary.get("progress")
            if progress:
                lines.append(f"progress: {progress}")
            if blockers:
                lines.append(f"blockers: {blockers}")
        events = events_from_result(result)
        if events:
            lines.append("")
            lines.append("events:")
            for index, event in enumerate(events, start=1):
                detail = f" - {event.detail}" if event.detail else ""
                lines.append(f"{index:02d}. {event.title}{detail}")
        return "\n".join(lines)
