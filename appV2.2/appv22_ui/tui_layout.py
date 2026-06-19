from __future__ import annotations

import shutil
import textwrap

from appv22_ui.tui_state import ConversationLine, TuiState


MIN_WIDTH = 72


def render_tui(state: TuiState, *, prompt: str = "appv22> ") -> str:
    width, height = shutil.get_terminal_size((110, 32))
    width = max(MIN_WIDTH, width)
    body_height = max(8, height - 2)
    rows = [
        _truncate(f"appv22  {state.workspace}", width),
        _truncate(_session_status_line(state), width),
        _truncate(_context_status_line(state), width),
        "",
    ]
    rows.extend(_compaction_stream(state, width))
    rows.extend(_conversation_stream(state, width))
    rows.extend(_event_stream(state, width))
    rows.extend(_context_stream(state, width))
    visible_rows = _tail(rows, body_height)
    visible_rows.append(_truncate(state.notice, width))
    visible_rows.append(prompt)
    return "\x1b[2J\x1b[H" + "\n".join(visible_rows)


def _conversation_stream(state: TuiState, width: int) -> list[str]:
    if not state.conversation:
        return ["Type a message to start the session."]
    lines: list[str] = []
    for item in state.conversation[-16:]:
        if lines:
            lines.append("")
        lines.extend(_conversation_line(item, width))
    return lines


def _conversation_line(item: ConversationLine, width: int) -> list[str]:
    role = item.role.lower()
    text = str(item.text)
    if role == "user":
        return _prefixed_block("> ", text, width)
    if role == "assistant":
        return _wrap(text, width)
    return _prefixed_block(f"[{role}] ", text, width)


def _event_stream(state: TuiState, width: int) -> list[str]:
    if not state.events:
        return []
    lines: list[str] = []
    for index, event in enumerate(state.events[-12:], start=max(1, len(state.events) - 11)):
        detail = f" :: {event.detail}" if event.detail else ""
        lines.extend(_wrap(f"{index:02d} {event.title}{detail}", width))
    return ["", *lines]


def _context_stream(state: TuiState, width: int) -> list[str]:
    summary = state.context_summary
    lines = []
    progress = summary.get("progress")
    if isinstance(progress, list) and progress:
        lines.append(f"progress: {len(progress)}")
        lines.extend(f"- {item}" for item in progress[-4:])
    blockers = summary.get("blockers")
    display_blockers = _display_risks(blockers, progress)
    if display_blockers:
        lines.append(f"blockers: {len(display_blockers)}")
        lines.extend(f"- {item}" for item in display_blockers[-4:])
    if state.world_refs:
        lines.append("refs:")
        lines.extend(f"- {ref}" for ref in state.world_refs[-6:])
    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(_wrap(line, width))
    return ["", *wrapped] if wrapped else []


def _compaction_stream(state: TuiState, width: int) -> list[str]:
    if not state.conversation_summary.strip():
        return []
    metrics = state.ui_context_metrics
    tokens_before = int(metrics.get("tokens_before") or 0) if isinstance(metrics, dict) else 0
    token_text = f"{tokens_before:,}" if tokens_before else "unknown"
    lines = ["[compaction]", f"compacted from {token_text} tokens"]
    lines.extend(_wrap(state.conversation_summary.strip(), width))
    return [*lines, ""]


def _session_status_line(state: TuiState) -> str:
    return (
        f"status {state.status or 'unknown'}"
        f"  mode {state.mode or 'IDLE'}"
        f"  session {state.session_id or '-'}"
        f"  reason {state.reason or '-'}"
    )


def _context_status_line(state: TuiState) -> str:
    metrics = state.ui_context_metrics
    compactions = metrics.get("compaction_count", 0) if isinstance(metrics, dict) else 0
    source = metrics.get("summary_source", "none") if isinstance(metrics, dict) else "none"
    hot_lines = metrics.get("hot_lines", 0) if isinstance(metrics, dict) else 0
    return f"context refs {state.world_ref_count} compact {compactions} source {source} hot {hot_lines}"


def _prefixed_block(prefix: str, text: str, width: int) -> list[str]:
    lines: list[str] = []
    continuation = " " * len(prefix)
    for index, raw_line in enumerate(str(text).splitlines() or [""]):
        line_prefix = prefix if index == 0 else continuation
        wrapped = _wrap(f"{line_prefix}{raw_line}", width)
        lines.extend(wrapped)
    return lines


def _wrap(text: str, width: int) -> list[str]:
    lines: list[str] = []
    for raw_line in str(text).splitlines() or [""]:
        wrapped = textwrap.wrap(raw_line, width=max(8, width), replace_whitespace=False)
        lines.extend(wrapped or [""])
    return lines or [""]


def _tail(lines: list[str], height: int) -> list[str]:
    clipped = lines[-height:] if len(lines) > height else list(lines)
    while len(clipped) < height:
        clipped.append("")
    return clipped[:height]


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
    has_progress = bool(progress_lines)
    if not has_progress:
        return risk_lines
    stale_markers = ("inactive_tool:list_dir", "list_dir request was denied", "list_dir reported error")
    return [risk for risk in risk_lines if not any(marker in risk for marker in stale_markers)]
