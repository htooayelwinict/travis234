from __future__ import annotations

import shutil
import textwrap

from appv22_ui.tui_state import ConversationLine, TuiState


MIN_WIDTH = 72


def render_tui(state: TuiState, *, prompt: str = "appv22> ") -> str:
    width, height = shutil.get_terminal_size((110, 32))
    width = max(MIN_WIDTH, width)
    body_height = max(20, height - 3)
    left_width = max(40, int(width * 0.56))
    right_width = width - left_width - 3
    conversation_lines = _panel_lines("CONVERSATION", _conversation_body(state), left_width)
    loop_lines = _panel_lines("PI AGENT LOOP", _loop_body(state), right_width)
    context_lines = _panel_lines("HERMES CONTEXT", _context_body(state), right_width)
    status_lines = _panel_lines("SESSION", _session_body(state), right_width)

    right_stack = _stack([status_lines, loop_lines, context_lines], body_height)
    left = _fit(conversation_lines, body_height)
    right = _fit(right_stack, body_height)

    rows = []
    for index in range(body_height):
        rows.append(f"{left[index]} | {right[index]}")
    rows.append(_truncate(state.notice, width))
    rows.append(prompt)
    return "\x1b[2J\x1b[H" + "\n".join(rows)


def _conversation_body(state: TuiState) -> list[str]:
    if not state.conversation:
        return ["No messages yet."]
    lines: list[str] = []
    for item in state.conversation[-16:]:
        prefix = "you" if item.role == "user" else item.role
        for index, raw_line in enumerate(str(item.text).splitlines() or [""]):
            line_prefix = f"{prefix}: " if index == 0 else " " * (len(prefix) + 2)
            lines.extend(_wrap(f"{line_prefix}{raw_line}", 1_000))
    return lines


def _session_body(state: TuiState) -> list[str]:
    return [
        f"workspace: {state.workspace}",
        f"session: {state.session_id or '-'}",
        f"status: {state.status}",
        f"mode: {state.mode}",
        f"reason: {state.reason or '-'}",
        f"world refs: {state.world_ref_count}",
    ]


def _loop_body(state: TuiState) -> list[str]:
    if not state.events:
        return ["Waiting for agent events."]
    lines: list[str] = []
    for index, event in enumerate(state.events[-14:], start=max(1, len(state.events) - 13)):
        detail = f" :: {event.detail}" if event.detail else ""
        lines.append(f"{index:02d} {event.title}{detail}")
    return lines


def _context_body(state: TuiState) -> list[str]:
    summary = state.context_summary
    lines = []
    if state.conversation_summary:
        lines.append(f"ui summary chars: {len(state.conversation_summary)}")
    if state.ui_context_metrics:
        compactions = state.ui_context_metrics.get("compaction_count", 0)
        hot_lines = state.ui_context_metrics.get("hot_lines", 0)
        source = state.ui_context_metrics.get("summary_source", "none")
        lines.append(f"ui compactions: {compactions} hot lines: {hot_lines} source: {source}")
    progress = summary.get("progress")
    if isinstance(progress, list) and progress:
        lines.append(f"progress: {len(progress)}")
        lines.extend(f"- {item}" for item in progress[-4:])
    risks = summary.get("open_risks")
    display_risks = _display_risks(risks, progress)
    if display_risks:
        lines.append(f"open risks: {len(display_risks)}")
        lines.extend(f"- {item}" for item in display_risks[-4:])
    if state.world_refs:
        lines.append("refs:")
        lines.extend(f"- {ref}" for ref in state.world_refs[-6:])
    return lines or ["Compact context available after the first observed run."]


def _panel_lines(title: str, body: list[str], width: int) -> list[str]:
    width = max(24, width)
    top = "+" + "-" * (width - 2) + "+"
    heading = f"| {title[: width - 4].ljust(width - 4)} |"
    sep = "+" + "-" * (width - 2) + "+"
    lines = [top, heading, sep]
    for item in body:
        for wrapped in _wrap(item, width - 4):
            lines.append(f"| {wrapped.ljust(width - 4)} |")
    lines.append(top)
    return lines


def _stack(panels: list[list[str]], height: int) -> list[str]:
    lines: list[str] = []
    for panel in panels:
        if lines:
            lines.append("")
        lines.extend(panel)
    return _fit(lines, height)


def _fit(lines: list[str], height: int) -> list[str]:
    width = max((len(line) for line in lines), default=1)
    clipped = lines[-height:] if len(lines) > height else list(lines)
    while len(clipped) < height:
        clipped.append(" " * width)
    return [line.ljust(width) for line in clipped[:height]]


def _wrap(text: str, width: int) -> list[str]:
    lines: list[str] = []
    for raw_line in str(text).splitlines() or [""]:
        wrapped = textwrap.wrap(raw_line, width=max(8, width), replace_whitespace=False)
        lines.extend(wrapped or [""])
    return lines or [""]


def _truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[: max(0, width - 15)] + "...<truncated>"


def _display_risks(risks, progress) -> list[str]:
    if not isinstance(risks, list):
        return []
    risk_lines = [str(risk) for risk in risks if risk]
    if not risk_lines:
        return []
    progress_lines = [str(item) for item in progress] if isinstance(progress, list) else []
    has_successful_observation = any("file_management.repo_snapshot" in item for item in progress_lines)
    if not has_successful_observation:
        return risk_lines
    stale_markers = ("inactive_tool:list_dir", "list_dir request was denied", "list_dir reported error")
    return [risk for risk in risk_lines if not any(marker in risk for marker in stale_markers)]
