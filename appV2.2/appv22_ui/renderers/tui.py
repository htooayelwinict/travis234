from __future__ import annotations

from appv22_ui.events import events_from_result, result_summary


class TuiRenderer:
    def render(self, result: dict | None) -> str:
        summary = result_summary(result)
        events = events_from_result(result)
        lines = [
            _status_line(summary),
            _context_line(summary),
        ]
        usage = summary.get("usage")
        if usage:
            lines.append(f"usage {usage}")
        if isinstance(result, dict) and result.get("assistant_message"):
            lines.extend(["", str(result["assistant_message"])])
        context_summary = summary.get("context_summary")
        if isinstance(context_summary, dict) and context_summary:
            lines.extend(["", "[compaction]", *_context_lines(context_summary)])
        if events:
            lines.extend(["", *_event_lines(events)])
        return "\n".join(lines)


def _status_line(summary: dict) -> str:
    return (
        f"status {summary['status']}"
        f"  session {summary['session_id'] or '-'}"
        f"  reason {summary['reason'] or '-'}"
    )


def _context_line(summary: dict) -> str:
    return f"context refs {summary['world_ref_count']}"


def _context_lines(context_summary: dict) -> list[str]:
    lines: list[str] = []
    progress = context_summary.get("progress")
    if progress:
        lines.append(f"progress {progress}")
    blockers = context_summary.get("blockers")
    if isinstance(blockers, list) and blockers:
        lines.append(f"blockers {len(blockers)}")
        for blocker in blockers[-4:]:
            lines.append(f"- {blocker}")
    if not lines:
        lines.append("summary available")
    return lines


def _event_lines(events) -> list[str]:
    lines: list[str] = []
    for index, event in enumerate(events[-30:], start=max(1, len(events) - 29)):
        detail = f" :: {event.detail}" if event.detail else ""
        lines.append(f"{index:02d} {event.title}{detail}")
    return lines
