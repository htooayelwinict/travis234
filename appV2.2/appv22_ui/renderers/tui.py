from __future__ import annotations

from appv22_ui.events import events_from_result, result_summary


class TuiRenderer:
    def render(self, result: dict | None) -> str:
        summary = result_summary(result)
        events = events_from_result(result)
        panels = [
            _panel(
                "APPV22 SESSION",
                [
                    f"id       {summary['session_id'] or '-'}",
                    f"status   {summary['status']}",
                    f"reason   {summary['reason'] or '-'}",
                    f"refs     {summary['world_ref_count']}",
                ],
            )
        ]
        usage = summary.get("usage")
        if usage:
            panels.append(_panel("MODEL / TOOL METRICS", [str(usage)]))
        if isinstance(result, dict) and result.get("assistant_message"):
            panels.append(_panel("ASSISTANT", [str(result["assistant_message"])]))
        context_summary = summary.get("context_summary")
        if isinstance(context_summary, dict) and context_summary:
            panels.append(_panel("HERMES CONTEXT", _context_lines(context_summary)))
        if events:
            panels.append(_panel("PI-STYLE AGENT LOOP", _event_lines(events)))
        return "\n\n".join(panels)


def _panel(title: str, lines: list[str]) -> str:
    width = max([len(title) + 4, *(len(line) + 4 for line in lines), 48])
    top = "+" + "-" * (width - 2) + "+"
    heading = f"| {title.ljust(width - 4)} |"
    separator = "+" + "-" * (width - 2) + "+"
    body = [f"| {line[: width - 4].ljust(width - 4)} |" for line in lines]
    bottom = "+" + "-" * (width - 2) + "+"
    return "\n".join([top, heading, separator, *body, bottom])


def _context_lines(context_summary: dict) -> list[str]:
    lines: list[str] = []
    progress = context_summary.get("progress")
    if progress:
        lines.append(f"progress   {progress}")
    open_risks = context_summary.get("open_risks")
    if isinstance(open_risks, list) and open_risks:
        lines.append(f"open risks {len(open_risks)}")
        for risk in open_risks[-4:]:
            lines.append(f"- {risk}")
    if not lines:
        lines.append("summary available")
    return lines


def _event_lines(events) -> list[str]:
    lines: list[str] = []
    for index, event in enumerate(events[-30:], start=max(1, len(events) - 29)):
        detail = f" :: {event.detail}" if event.detail else ""
        lines.append(f"{index:02d} {event.title}{detail}")
    return lines
