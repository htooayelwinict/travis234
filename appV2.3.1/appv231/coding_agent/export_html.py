"""HTML session export for appv231 coding-agent.

Python port of pi's export-html data contract: embed a standalone session-data
payload and render a readable transcript without depending on the source module.
"""

from __future__ import annotations

import base64
import html
import json
import re
from pathlib import Path
from typing import Any

from appv231.ai.types import ImageContent, TextContent, ThinkingContent, ToolCall
from appv231.coding_agent.session_store import SessionStore

_TEMPLATE_RENDERED_TOOLS = {"bash", "read", "write", "edit", "ls"}


def export_session_to_html(
    session_store: SessionStore,
    state,
    options: str | dict[str, Any] | None = None,
    *,
    tool_renderer: object | None = None,
    theme_name: str | None = None,
) -> str:
    opts = _normalize_export_options(options)
    if tool_renderer is None:
        tool_renderer = opts.get("toolRenderer") or opts.get("tool_renderer")
    if theme_name is None:
        theme_name = opts.get("themeName") or opts.get("theme_name")
    del theme_name

    target_path = _resolve_output_path(session_store, opts.get("outputPath"))
    target_path.parent.mkdir(parents=True, exist_ok=True)

    entries = session_store.entries
    session_data = {
        "header": session_store.header,
        "entries": entries,
        "leafId": session_store.get_leaf_id(),
        "systemPrompt": getattr(state, "system_prompt", None),
        "tools": [
            {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
            for tool in (getattr(state, "tools", None) or [])
        ],
    }
    rendered_tools = _pre_render_custom_tools(entries, tool_renderer) if tool_renderer is not None else {}
    if rendered_tools:
        session_data["renderedTools"] = rendered_tools
    encoded = base64.b64encode(json.dumps(session_data, separators=(",", ":")).encode("utf-8")).decode("ascii")
    target_path.write_text(_render_html(session_data, encoded), encoding="utf-8")
    return str(target_path)


def export_from_file(input_path: str, options: str | dict[str, Any] | None = None) -> str:
    opts = _normalize_export_options(options)
    session_path = Path(input_path).expanduser()
    if not session_path.is_absolute():
        session_path = Path.cwd() / session_path
    session_path = session_path.resolve()
    if not session_path.exists():
        raise FileNotFoundError(f"File not found: {session_path}")

    session_store = SessionStore(str(session_path), cwd=str(session_path.parent))
    target_path = _resolve_export_file_output_path(session_path, opts.get("outputPath"))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    session_data = {
        "header": session_store.header,
        "entries": session_store.entries,
        "leafId": session_store.get_leaf_id(),
    }
    encoded = base64.b64encode(json.dumps(session_data, separators=(",", ":")).encode("utf-8")).decode("ascii")
    target_path.write_text(_render_html(session_data, encoded), encoding="utf-8")
    return str(target_path)


exportFromFile = export_from_file


def _normalize_export_options(options: str | dict[str, Any] | None) -> dict[str, Any]:
    if options is None:
        return {}
    if isinstance(options, str):
        return {"outputPath": options}
    if isinstance(options, dict):
        return dict(options)
    raise TypeError("HTML export options must be a path string, dict, or None")


def _resolve_output_path(session_store: SessionStore, output_path: str | None) -> Path:
    if output_path:
        return _resolve_path_for_write(output_path)
    return Path.cwd() / f"pi-session-{session_store.path.stem}.html"


def _resolve_export_file_output_path(input_path: Path, output_path: str | None) -> Path:
    if output_path:
        return _resolve_path_for_write(output_path)
    return Path.cwd() / f"pi-session-{input_path.stem}.html"


def _resolve_path_for_write(path_value: str) -> Path:
    path = Path(path_value).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def _pre_render_custom_tools(entries: list[dict[str, Any]], tool_renderer: object) -> dict[str, dict[str, str]]:
    rendered_tools: dict[str, dict[str, str]] = {}
    for entry in entries:
        if entry.get("type") != "message":
            continue
        message = entry.get("message") or {}
        if message.get("role") == "assistant" and isinstance(message.get("content"), list):
            for block in message["content"]:
                if not isinstance(block, dict) or block.get("type") != "toolCall":
                    continue
                tool_name = str(block.get("name") or "")
                tool_call_id = str(block.get("id") or "")
                if not tool_call_id or tool_name in _TEMPLATE_RENDERED_TOOLS:
                    continue
                call_html = _normalize_rendered_html(
                    _call_tool_renderer(
                        tool_renderer,
                        ("render_call", "renderCall"),
                        tool_call_id,
                        tool_name,
                        block.get("arguments", {}),
                    )
                )
                if call_html:
                    rendered_tools[tool_call_id] = {"callHtml": call_html}

        if message.get("role") == "toolResult":
            tool_call_id = str(message.get("toolCallId") or "")
            tool_name = str(message.get("toolName") or "")
            existing = rendered_tools.get(tool_call_id)
            if not tool_call_id or (existing is None and tool_name in _TEMPLATE_RENDERED_TOOLS):
                continue
            result = _call_tool_renderer(
                tool_renderer,
                ("render_result", "renderResult"),
                tool_call_id,
                tool_name,
                message.get("content") or [],
                message.get("details"),
                bool(message.get("isError", False)),
            )
            rendered = _normalize_rendered_result(result)
            if rendered:
                rendered_tools[tool_call_id] = {**(existing or {}), **rendered}
    return rendered_tools


def _call_tool_renderer(tool_renderer: object, method_names: tuple[str, ...], *args):
    for method_name in method_names:
        method = getattr(tool_renderer, method_name, None)
        if callable(method):
            return method(*args)
    return None


def _normalize_rendered_result(result: object) -> dict[str, str]:
    if result is None:
        return {}
    if isinstance(result, dict):
        collapsed = result.get("collapsed")
        expanded = result.get("expanded")
    else:
        collapsed = getattr(result, "collapsed", None)
        expanded = getattr(result, "expanded", None)
    rendered: dict[str, str] = {}
    collapsed_html = _normalize_rendered_html(collapsed)
    expanded_html = _normalize_rendered_html(expanded)
    if collapsed_html:
        rendered["resultHtmlCollapsed"] = collapsed_html
    if expanded_html:
        rendered["resultHtmlExpanded"] = expanded_html
    return rendered


_ANSI_COLORS = [
    "#000000",
    "#800000",
    "#008000",
    "#808000",
    "#000080",
    "#800080",
    "#008080",
    "#c0c0c0",
    "#808080",
    "#ff0000",
    "#00ff00",
    "#ffff00",
    "#0000ff",
    "#ff00ff",
    "#00ffff",
    "#ffffff",
]
_ANSI_REGEX = re.compile(r"\x1b\[([\d;]*)m")


def _normalize_rendered_html(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return _ansi_lines_to_html(value)
    render = getattr(value, "render", None)
    if callable(render):
        try:
            lines = render(100)
        except TypeError:
            lines = render()
        if isinstance(lines, str):
            lines = lines.splitlines()
        if isinstance(lines, (list, tuple)):
            return _ansi_lines_to_html(lines)
    return None


def _ansi_lines_to_html(lines: list[object] | tuple[object, ...]) -> str:
    return "".join(f'<div class="ansi-line">{_ansi_to_html(str(line)) or "&nbsp;"}</div>' for line in lines)


def _ansi_to_html(text: str) -> str:
    style = _empty_ansi_style()
    result: list[str] = []
    last_index = 0
    in_span = False
    for match in _ANSI_REGEX.finditer(text):
        before_text = text[last_index : match.start()]
        if before_text:
            result.append(_escape_html(before_text))
        if in_span:
            result.append("</span>")
            in_span = False
        param_str = match.group(1)
        params = [int(part) if part.isdigit() else 0 for part in param_str.split(";")] if param_str else [0]
        _apply_sgr_code(params, style)
        if _has_ansi_style(style):
            result.append(f'<span style="{_style_to_inline_css(style)}">')
            in_span = True
        last_index = match.end()
    remaining_text = text[last_index:]
    if remaining_text:
        result.append(_escape_html(remaining_text))
    if in_span:
        result.append("</span>")
    return "".join(result)


def _escape_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#039;")
    )


def _empty_ansi_style() -> dict[str, object]:
    return {"fg": None, "bg": None, "bold": False, "dim": False, "italic": False, "underline": False}


def _style_to_inline_css(style: dict[str, object]) -> str:
    parts: list[str] = []
    if style["fg"]:
        parts.append(f"color:{style['fg']}")
    if style["bg"]:
        parts.append(f"background-color:{style['bg']}")
    if style["bold"]:
        parts.append("font-weight:bold")
    if style["dim"]:
        parts.append("opacity:0.6")
    if style["italic"]:
        parts.append("font-style:italic")
    if style["underline"]:
        parts.append("text-decoration:underline")
    return ";".join(parts)


def _has_ansi_style(style: dict[str, object]) -> bool:
    return bool(style["fg"] or style["bg"] or style["bold"] or style["dim"] or style["italic"] or style["underline"])


def _color256_to_hex(index: int) -> str:
    if index < 16:
        return _ANSI_COLORS[index]
    if index < 232:
        cube_index = index - 16
        red = cube_index // 36
        green = (cube_index % 36) // 6
        blue = cube_index % 6

        def to_component(value: int) -> int:
            return 0 if value == 0 else 55 + value * 40

        return f"#{to_component(red):02x}{to_component(green):02x}{to_component(blue):02x}"
    gray = 8 + (index - 232) * 10
    return f"#{gray:02x}{gray:02x}{gray:02x}"


def _apply_sgr_code(params: list[int], style: dict[str, object]) -> None:
    index = 0
    while index < len(params):
        code = params[index]
        if code == 0:
            style.update(_empty_ansi_style())
        elif code == 1:
            style["bold"] = True
        elif code == 2:
            style["dim"] = True
        elif code == 3:
            style["italic"] = True
        elif code == 4:
            style["underline"] = True
        elif code == 22:
            style["bold"] = False
            style["dim"] = False
        elif code == 23:
            style["italic"] = False
        elif code == 24:
            style["underline"] = False
        elif 30 <= code <= 37:
            style["fg"] = _ANSI_COLORS[code - 30]
        elif code == 39:
            style["fg"] = None
        elif 40 <= code <= 47:
            style["bg"] = _ANSI_COLORS[code - 40]
        elif code == 49:
            style["bg"] = None
        elif 90 <= code <= 97:
            style["fg"] = _ANSI_COLORS[code - 90 + 8]
        elif 100 <= code <= 107:
            style["bg"] = _ANSI_COLORS[code - 100 + 8]
        elif code in (38, 48) and index + 2 < len(params):
            target = "fg" if code == 38 else "bg"
            mode = params[index + 1]
            if mode == 5:
                style[target] = _color256_to_hex(params[index + 2])
                index += 2
            elif mode == 2 and index + 4 < len(params):
                red, green, blue = params[index + 2], params[index + 3], params[index + 4]
                style[target] = f"#{red:02x}{green:02x}{blue:02x}"
                index += 4
        index += 1


def _render_html(session_data: dict[str, Any], encoded_session_data: str) -> str:
    return (
        _EXPORT_TEMPLATE.replace("{{CSS}}", _EXPORT_CSS)
        .replace("{{JS}}", _EXPORT_JS)
        .replace("{{SESSION_DATA}}", encoded_session_data)
        .replace("{{MARKED_JS}}", _load_export_asset("vendor", "marked.min.js"))
        .replace("{{HIGHLIGHT_JS}}", _load_export_asset("vendor", "highlight.min.js"))
    )


_EXPORT_ASSET_DIR = Path(__file__).with_name("export_html_assets")


def _load_export_asset(*parts: str) -> str:
    return (_EXPORT_ASSET_DIR.joinpath(*parts)).read_text(encoding="utf-8")


_EXPORT_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Session Export</title>
  <style>
{{CSS}}
  </style>
</head>
<body>
  <button id="hamburger" title="Open sidebar"><svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none"><circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="12" r="2.5"/><rect x="5" y="6" width="2" height="12"/><path d="M6 12h10c1 0 2 0 2-2V8"/></svg></button>
  <div id="sidebar-overlay"></div>
  <div id="app">
    <aside id="sidebar">
      <div class="sidebar-header">
        <div class="sidebar-controls">
          <input type="text" class="sidebar-search" id="tree-search" placeholder="Search...">
        </div>
        <div class="sidebar-filters">
          <button class="filter-btn active" data-filter="default" title="Hide settings entries">Default</button>
          <button class="filter-btn" data-filter="no-tools" title="Default minus tool results">No-tools</button>
          <button class="filter-btn" data-filter="user-only" title="Only user messages">User</button>
          <button class="filter-btn" data-filter="labeled-only" title="Only labeled entries">Labeled</button>
          <button class="filter-btn" data-filter="all" title="Show everything">All</button>
          <button class="sidebar-close" id="sidebar-close" title="Close">x</button>
        </div>
      </div>
      <div class="tree-container" id="tree-container"></div>
      <div class="tree-status" id="tree-status"></div>
    </aside>
    <div id="sidebar-resizer" role="separator" aria-orientation="vertical" aria-label="Resize session tree sidebar"></div>
    <main id="content">
      <div id="header-container"></div>
      <div id="messages"></div>
    </main>
    <div id="image-modal" class="image-modal">
      <img id="modal-image" src="" alt="">
    </div>
  </div>

  <script id="session-data" type="application/json">{{SESSION_DATA}}</script>

  <!-- Vendored libraries -->
  <script>{{MARKED_JS}}</script>

  <!-- highlight.js -->
  <script>{{HIGHLIGHT_JS}}</script>

  <!-- Main application code -->
  <script>
{{JS}}
  </script>
</body>
</html>
"""


_EXPORT_CSS = """
    :root {
      --body-bg: #18181e;
      --container-bg: #202026;
      --text: #f4f4f5;
      --muted: #a1a1aa;
      --accent: #7dd3fc;
      --border: #3f3f46;
      --dim: #71717a;
      --warning: #fbbf24;
      --success: #86efac;
      --error: #fca5a5;
      --hover: #2a2a31;
      --selectedBg: rgba(125, 211, 252, 0.10);
      --borderAccent: var(--accent);
      --user-bg: #1f2937;
      --assistant-bg: #27272a;
      --tool-bg: #1e293b;
      --info-bg: #3b3320;
      --userMessageBg: var(--user-bg);
      --userMessageText: var(--text);
      --customMessageBg: var(--info-bg);
      --customMessageText: var(--text);
      --customMessageLabel: var(--accent);
      --thinkingText: var(--muted);
      --mdHeading: var(--accent);
      --mdLink: var(--accent);
      --mdCode: var(--text);
      --mdQuote: var(--muted);
      --mdQuoteBorder: var(--dim);
      --mdListBullet: var(--accent);
      --mdHr: var(--dim);
      --mdCodeBlockBorder: var(--dim);
      --toolPendingBg: var(--tool-bg);
      --toolSuccessBg: rgba(34, 197, 94, 0.12);
      --toolErrorBg: rgba(248, 113, 113, 0.14);
      --toolOutput: var(--muted);
      --toolDiffAdded: #86efac;
      --toolDiffRemoved: #fca5a5;
      --toolDiffContext: var(--muted);
      --syntaxComment: #71717a;
      --syntaxKeyword: #c084fc;
      --syntaxNumber: #fbbf24;
      --syntaxString: #86efac;
      --syntaxFunction: #7dd3fc;
      --syntaxType: #67e8f9;
      --syntaxVariable: #f9a8d4;
      --syntaxOperator: #f4f4f5;
      --syntaxPunctuation: #a1a1aa;
      --line-height: 18px;
      --sidebar-width: 400px;
      --sidebar-min-width: 240px;
      --sidebar-max-width: 840px;
      --sidebar-resizer-width: 6px;
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    body {
      font-family: ui-monospace, 'Cascadia Code', 'Source Code Pro', Menlo, Consolas, 'DejaVu Sans Mono', monospace;
      font-size: 12px;
      line-height: var(--line-height);
      color: var(--text);
      background: var(--body-bg);
    }
    body.sidebar-resizing {
      cursor: col-resize;
      user-select: none;
    }
    #hamburger {
      display: none;
      position: fixed;
      top: 10px;
      left: 10px;
      z-index: 40;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--container-bg);
      color: var(--text);
      padding: 8px;
    }
    #sidebar-overlay { display: none; }
    #app { display: flex; min-height: 100vh; }
    #sidebar {
      width: var(--sidebar-width);
      min-width: var(--sidebar-width);
      max-width: var(--sidebar-width);
      background: var(--container-bg);
      flex-shrink: 0;
      display: flex;
      flex-direction: column;
      position: sticky;
      top: 0;
      height: 100vh;
      border-right: 1px solid var(--dim);
    }
    .sidebar-header {
      padding: 8px 12px;
      background: var(--container-bg);
      flex-shrink: 0;
    }
    .sidebar-controls {
      padding: 8px 8px 4px 8px;
    }
    .sidebar-search {
      width: 100%;
      box-sizing: border-box;
      padding: 4px 8px;
      font-size: 11px;
      font-family: inherit;
      border: 1px solid var(--dim);
      border-radius: 3px;
      background: var(--body-bg);
      color: var(--text);
    }
    .sidebar-search:focus {
      outline: none;
      border-color: var(--accent);
    }
    .sidebar-search::placeholder {
      color: var(--muted);
    }
    .sidebar-filters {
      display: flex;
      padding: 4px 8px 8px 8px;
      flex-wrap: wrap;
      gap: 4px;
      align-items: center;
    }
    .filter-btn, .sidebar-close {
      border: 1px solid var(--dim);
      border-radius: 3px;
      background: transparent;
      color: var(--muted);
      padding: 3px 8px;
      font-size: 10px;
      font: inherit;
      cursor: pointer;
    }
    .filter-btn:hover, .sidebar-close:hover {
      color: var(--text);
      border-color: var(--text);
    }
    .filter-btn.active {
      background: var(--accent);
      color: var(--body-bg);
      border-color: var(--accent);
    }
    .sidebar-close {
      display: none;
      margin-left: auto;
    }
    .tree-container {
      flex: 1;
      overflow: auto;
      padding: 4px 0;
    }
    .tree-node {
      display: flex;
      align-items: baseline;
      border: 0;
      background: transparent;
      color: var(--muted);
      text-align: left;
      font-size: 11px;
      line-height: 13px;
      padding: 0 8px;
      cursor: pointer;
      white-space: nowrap;
    }
    .tree-node:hover {
      background: var(--selectedBg);
    }
    .tree-node.active {
      background: var(--selectedBg);
    }
    .tree-node.active .tree-content {
      font-weight: bold;
    }
    .tree-node.in-path {
      background: color-mix(in srgb, var(--accent) 10%, transparent);
    }
    .tree-node:not(.in-path) {
      opacity: 0.5;
    }
    .tree-node:not(.in-path):hover {
      opacity: 1;
    }
    .tree-prefix {
      color: var(--muted);
      flex-shrink: 0;
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
      white-space: pre;
    }
    .tree-marker {
      color: var(--accent);
      flex-shrink: 0;
    }
    .tree-content {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      color: var(--text);
    }
    .tree-label, .tree-role-user { color: var(--accent); }
    .tree-role-skill, .tree-custom, .tree-custom-message { color: #f0abfc; }
    .tree-role-assistant { color: #86efac; }
    .tree-role-tool, .tree-muted { color: var(--muted); }
    .tree-error { color: #fca5a5; }
    .tree-compaction { color: #a78bfa; }
    .tree-branch-summary { color: #fbbf24; }
    .tree-status { color: var(--muted); padding: 8px 12px 14px; }
    .message-timestamp {
      font-size: 11px;
      color: var(--muted);
      opacity: 0.8;
      margin-bottom: 4px;
    }
    .user-message {
      background: var(--user-bg);
      color: var(--text);
      padding: 12px;
      border-radius: 6px;
      position: relative;
      overflow-wrap: anywhere;
    }
    .assistant-message {
      background: var(--assistant-bg);
      border-radius: 6px;
      padding: 0;
      position: relative;
      overflow-wrap: anywhere;
    }
    .assistant-message > .message-timestamp {
      padding: 12px 12px 0;
    }
    .assistant-text {
      padding: 12px;
      padding-bottom: 0;
    }
    .thinking-block + .assistant-text,
    .assistant-text + .tool-execution {
      margin-top: 12px;
    }
    #sidebar-resizer {
      width: var(--sidebar-resizer-width);
      flex-shrink: 0;
      position: sticky;
      top: 0;
      height: 100vh;
      cursor: col-resize;
      touch-action: none;
      background: transparent;
      border-right: 1px solid transparent;
    }
    #sidebar-resizer:hover,
    body.sidebar-resizing #sidebar-resizer {
      background: var(--selectedBg);
      border-right-color: var(--dim);
    }
    #content {
      flex: 1;
      min-width: 0;
      overflow-y: auto;
      padding: var(--line-height) calc(var(--line-height) * 2);
      display: flex;
      flex-direction: column;
      align-items: center;
    }
    #content > * {
      width: 100%;
      max-width: 800px;
    }
    #messages {
      display: flex;
      flex-direction: column;
      gap: var(--line-height);
    }
    #header-container {
      margin: 0 0 18px;
      color: var(--muted);
      overflow-wrap: anywhere;
    }
    .header {
      background: var(--container-bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px 14px;
      margin-bottom: 14px;
    }
    .header h1 {
      margin: 0 0 10px;
      color: var(--accent);
      font-size: 14px;
    }
    .help-bar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 10px;
      margin-bottom: 10px;
      color: var(--muted);
      font-size: 12px;
    }
    .help-hint {
      flex: 1 1 240px;
    }
    .help-actions {
      display: flex;
      align-items: center;
      flex-wrap: wrap;
      gap: 8px;
    }
    .header-toggle-btn, .download-json-btn {
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--body-bg);
      color: var(--text);
      padding: 4px 8px;
      font: inherit;
      cursor: pointer;
    }
    .header-toggle-btn:hover, .download-json-btn:hover {
      border-color: var(--accent);
      color: var(--accent);
    }
    .header-info {
      display: flex;
      flex-direction: column;
      gap: 2px;
      font-size: 12px;
    }
    .info-item {
      display: flex;
      align-items: baseline;
      color: var(--muted);
    }
    .info-label {
      min-width: 92px;
      margin-right: 8px;
      color: var(--text);
      font-weight: 700;
    }
    .info-value {
      color: var(--text);
      flex: 1;
    }
    .system-prompt, .tools-list {
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--container-bg);
      padding: 12px 14px;
      margin-bottom: 14px;
    }
    .system-prompt-header, .tools-header {
      color: var(--accent);
      font-weight: 700;
      margin-bottom: 8px;
    }
    .system-prompt-preview,
    .system-prompt-full,
    .tools-content {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .system-prompt-full { display: none; }
    .system-prompt.expanded .system-prompt-preview,
    .system-prompt.expanded .system-prompt-expand-hint { display: none; }
    .system-prompt.expanded .system-prompt-full { display: block; }
    .system-prompt.provider-prompt {
      border-left: 3px solid var(--warning);
    }
    .system-prompt-note {
      font-size: 10px;
      font-style: italic;
      color: var(--muted);
      margin-top: 4px;
    }
    .tool-item { margin: 0 0 6px; }
    .tool-item-name { color: var(--text); font-weight: 700; }
    .tool-item-desc, .tool-param-desc, .system-prompt-expand-hint { color: var(--muted); }
    .tool-params-hint {
      color: var(--muted);
      font-style: italic;
    }
    .tool-item:has(.tool-params-hint) {
      cursor: pointer;
    }
    .tool-params-hint::after {
      content: '[click to show parameters]';
    }
    .tool-item.params-expanded .tool-params-hint::after {
      content: '[hide parameters]';
    }
    .tool-params-content { display: none; margin: 6px 0 0 12px; }
    .tool-item.params-expanded .tool-params-content { display: block; }
    .tool-param { margin: 4px 0; }
    .tool-param-name { color: var(--accent); }
    .tool-param-type, .tool-param-required, .tool-param-optional { color: var(--muted); }
    .message {
      margin: 0 0 14px;
      padding: 12px 14px;
      border: 1px solid var(--border);
      border-radius: 6px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .role {
      color: var(--accent);
      font-weight: 700;
      margin-bottom: 6px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }
    .copy-link-btn {
      border: 1px solid var(--border);
      border-radius: 6px;
      background: transparent;
      color: var(--muted);
      cursor: pointer;
      padding: 2px 5px;
      line-height: 0;
    }
    .copy-link-btn:hover, .copy-link-btn.copied {
      color: var(--accent);
      border-color: var(--accent);
    }
    .message.highlight {
      outline: 2px solid var(--accent);
    }
    .user-message.highlight,
    .assistant-message.highlight,
    .tool-execution.highlight,
    .skill-user-entry.highlight,
    .branch-summary.highlight,
    .compaction.highlight,
    .hook-message.highlight {
      outline: 2px solid var(--accent);
    }
    .user-message:hover .copy-link-btn,
    .assistant-message:hover .copy-link-btn,
    .skill-user-entry:hover .copy-link-btn {
      color: var(--accent);
      border-color: var(--accent);
    }
    .skill-user-entry {
      position: relative;
    }
    .skill-invocation {
      background: var(--info-bg);
      border-radius: 6px;
      padding: 12px;
      cursor: pointer;
    }
    .skill-invocation-label {
      color: var(--accent);
      font-weight: 700;
    }
    .skill-invocation-collapsed {
      color: var(--text);
    }
    .skill-invocation-content {
      display: none;
      color: var(--text);
      margin-top: 12px;
    }
    .skill-invocation.expanded .skill-invocation-collapsed {
      display: none;
    }
    .skill-invocation.expanded .skill-invocation-content {
      display: block;
    }
    .skill-invocation + .user-message {
      margin-top: 12px;
    }
    .model-change {
      padding: 0 12px;
      color: var(--muted);
      font-size: 12px;
    }
    .model-name {
      color: var(--accent);
      font-weight: 700;
    }
    .hook-message {
      background: var(--info-bg);
      color: var(--text);
      padding: 12px;
      border-radius: 6px;
      overflow-wrap: anywhere;
    }
    .hook-type {
      color: var(--accent);
      font-weight: 700;
    }
    .branch-summary {
      background: var(--info-bg);
      padding: 12px;
      border-radius: 6px;
      overflow-wrap: anywhere;
    }
    .branch-summary-header {
      font-weight: 700;
      color: var(--accent);
    }
    .error-text {
      color: #fca5a5;
      padding: 0 12px 12px;
    }
    .tool-execution {
      margin: 10px 0 0;
      padding: 10px 12px;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--tool-bg);
    }
    .tool-execution.success { border-color: rgba(34, 197, 94, 0.45); }
    .tool-execution.error { border-color: rgba(248, 113, 113, 0.55); }
    .tool-execution.pending { border-color: rgba(161, 161, 170, 0.45); }
    .tool-command, .tool-header {
      color: var(--accent);
      font-weight: 700;
      margin-bottom: 6px;
    }
    .tool-name { color: var(--accent); }
    .tool-path, .line-count, .line-numbers, .expand-hint { color: var(--muted); }
    .tool-output {
      margin-top: 6px;
      color: var(--text);
      overflow-wrap: anywhere;
    }
    .tool-output.expandable {
      cursor: pointer;
    }
    .tool-output.expandable .output-full {
      display: none;
    }
    .tool-output.expandable.expanded .output-preview {
      display: none;
    }
    .tool-output.expandable.expanded .output-full {
      display: block;
    }
    .tool-error, .diff-removed { color: #fca5a5; }
    .diff-added { color: #86efac; }
    .diff-context { color: var(--muted); }
    .tool-images {
    }
    .tool-image {
      max-width: 100%;
      max-height: 500px;
      border-radius: 4px;
      margin: 12px 0;
    }
    .tool-diff {
      margin-top: 6px;
      white-space: pre-wrap;
    }
    .ansi-rendered { overflow-wrap: anywhere; }
    .ansi-line {
      min-height: var(--line-height);
      white-space: pre-wrap;
    }
    .user { background: var(--user-bg); }
    .assistant { background: var(--assistant-bg); }
    .toolResult, .bashExecution { background: var(--tool-bg); }
    .compaction, .branchSummary, .custom { background: var(--info-bg); }
    .compaction {
      border-radius: 6px;
      padding: 12px;
      cursor: pointer;
      overflow-wrap: anywhere;
    }
    .compaction-label {
      color: var(--accent);
      font-weight: 700;
    }
    .compaction-collapsed {
      color: var(--text);
    }
    .compaction-content {
      display: none;
      color: var(--text);
      white-space: pre-wrap;
      margin-top: 12px;
    }
    .compaction.expanded .compaction-collapsed {
      display: none;
    }
    .compaction.expanded .compaction-content {
      display: block;
    }
    .message-images {
      margin: 0 0 12px;
    }
    .message-image {
      display: block;
      max-width: 100%;
      max-height: 400px;
      border-radius: 6px;
      margin: 8px 0;
      cursor: zoom-in;
    }
    .image-modal {
      display: none;
      position: fixed;
      inset: 0;
      z-index: 100;
      align-items: center;
      justify-content: center;
      padding: 24px;
      background: rgba(0, 0, 0, 0.84);
      cursor: zoom-out;
    }
    .image-modal.open {
      display: flex;
    }
    .image-modal img {
      max-width: 96vw;
      max-height: 92vh;
      border-radius: 6px;
      box-shadow: 0 12px 40px rgba(0, 0, 0, 0.45);
    }
    .markdown-content h1,
    .markdown-content h2,
    .markdown-content h3,
    .markdown-content h4,
    .markdown-content h5,
    .markdown-content h6 {
      color: var(--mdHeading);
      margin: var(--line-height) 0 0 0;
      font-weight: bold;
    }
    .markdown-content h1 { font-size: 1em; }
    .markdown-content h2 { font-size: 1em; }
    .markdown-content h3 { font-size: 1em; }
    .markdown-content h4 { font-size: 1em; }
    .markdown-content h5 { font-size: 1em; }
    .markdown-content h6 { font-size: 1em; }
    .markdown-content p { margin: 0; }
    .markdown-content p + p { margin-top: var(--line-height); }
    .markdown-content a {
      color: var(--mdLink);
      text-decoration: underline;
    }
    .markdown-content code {
      background: rgba(128, 128, 128, 0.2);
      color: var(--mdCode);
      padding: 0 4px;
      border-radius: 3px;
      font-family: inherit;
    }
    .markdown-content pre {
      background: transparent;
      margin: var(--line-height) 0;
      overflow-x: auto;
    }
    .markdown-content pre code {
      display: block;
      background: none;
      color: var(--text);
    }
    .markdown-content blockquote {
      border-left: 3px solid var(--mdQuoteBorder);
      padding-left: var(--line-height);
      margin: var(--line-height) 0;
      color: var(--mdQuote);
      font-style: italic;
    }
    .markdown-content ul,
    .markdown-content ol {
      margin: var(--line-height) 0;
      padding-left: calc(var(--line-height) * 2);
    }
    .markdown-content li { margin: 0; }
    .markdown-content li::marker { color: var(--mdListBullet); }
    .markdown-content hr {
      border: none;
      border-top: 1px solid var(--mdHr);
      margin: var(--line-height) 0;
    }
    .markdown-content table {
      border-collapse: collapse;
      margin: 0.5em 0;
      width: 100%;
    }
    .markdown-content th,
    .markdown-content td {
      border: 1px solid var(--mdCodeBlockBorder);
      padding: 6px 10px;
      text-align: left;
    }
    .markdown-content th {
      background: rgba(128, 128, 128, 0.1);
      font-weight: bold;
    }
    .markdown-content img {
      max-width: 100%;
      border-radius: 4px;
    }
    .hljs { background: transparent; color: var(--text); }
    .hljs-comment, .hljs-quote { color: var(--syntaxComment); }
    .hljs-keyword, .hljs-selector-tag { color: var(--syntaxKeyword); }
    .hljs-number, .hljs-literal { color: var(--syntaxNumber); }
    .hljs-string, .hljs-doctag { color: var(--syntaxString); }
    .hljs-function, .hljs-title, .hljs-title.function_, .hljs-section, .hljs-name { color: var(--syntaxFunction); }
    .hljs-type, .hljs-class, .hljs-title.class_, .hljs-built_in { color: var(--syntaxType); }
    .hljs-attr, .hljs-variable, .hljs-variable.language_, .hljs-params, .hljs-property { color: var(--syntaxVariable); }
    .hljs-meta, .hljs-meta .hljs-keyword, .hljs-meta .hljs-string { color: var(--syntaxKeyword); }
    .hljs-operator { color: var(--syntaxOperator); }
    .hljs-punctuation { color: var(--syntaxPunctuation); }
    .hljs-subst { color: var(--text); }
    .footer {
      margin-top: 48px;
      padding: 20px;
      text-align: center;
      color: var(--dim);
      font-size: 10px;
    }
    @media (max-width: 900px) {
      #hamburger {
        display: block;
      }
      #sidebar {
        position: fixed;
        left: 0;
        width: min(var(--sidebar-width), 100vw);
        min-width: min(var(--sidebar-width), 100vw);
        max-width: min(var(--sidebar-width), 100vw);
        z-index: 99;
        transform: translateX(-100%);
        transition: transform 0.3s;
      }
      #sidebar.open {
        transform: translateX(0);
      }
      #sidebar-resizer {
        display: none;
      }
      #sidebar-overlay.open {
        display: block;
        position: fixed;
        inset: 0;
        background: rgba(0, 0, 0, 0.5);
        z-index: 98;
      }
      .sidebar-close {
        display: inline-block;
      }
      #content {
        max-width: none;
        width: 100%;
        padding: 56px 16px 24px;
      }
    }
    @media print {
      #sidebar, #sidebar-resizer, #sidebar-toggle { display: none !important; }
      body { background: white; color: black; }
      #content { max-width: none; }
    }
    code, pre { white-space: pre-wrap; }
"""


_EXPORT_JS = r"""
    (function() {
      'use strict';

      // ============================================================
      // DATA LOADING
      // ============================================================

      const base64 = document.getElementById('session-data').textContent;
      const binary = atob(base64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
      }
      const data = JSON.parse(new TextDecoder('utf-8').decode(bytes));
      const { header, entries, leafId: defaultLeafId, systemPrompt, tools, renderedTools } = data;

      // ============================================================
      // URL PARAMETER HANDLING
      // ============================================================

      const injectedParams = document.querySelector('meta[name="pi-url-params"]');
      const searchString = injectedParams ? injectedParams.content : window.location.search.substring(1);
      const urlParams = new URLSearchParams(searchString);
      const urlLeafId = urlParams.get('leafId');
      const urlTargetId = urlParams.get('targetId');
      const leafId = urlLeafId || defaultLeafId;

      // ============================================================
      // DATA STRUCTURES
      // ============================================================

      const byId = new Map();
      for (const entry of entries) {
        byId.set(entry.id, entry);
      }

      const toolCallMap = new Map();
      for (const entry of entries) {
        if (entry.type === 'message' && entry.message && entry.message.role === 'assistant') {
          const content = entry.message.content;
          if (Array.isArray(content)) {
            for (const block of content) {
              if (block && block.type === 'toolCall') {
                toolCallMap.set(block.id, { name: block.name, arguments: block.arguments });
              }
            }
          }
        }
      }

      const labelMap = new Map();
      for (const entry of entries) {
        if (entry.type === 'label' && entry.targetId && entry.label) {
          labelMap.set(entry.targetId, entry.label);
        }
      }

      // ============================================================
      // TREE DATA PREPARATION
      // ============================================================

      function buildTree() {
        const nodeMap = new Map();
        const roots = [];
        for (const entry of entries) {
          nodeMap.set(entry.id, { entry, children: [], label: labelMap.get(entry.id) });
        }
        for (const entry of entries) {
          const node = nodeMap.get(entry.id);
          if (!node) continue;
          if (entry.parentId === null || entry.parentId === undefined || entry.parentId === entry.id) {
            roots.push(node);
          } else {
            const parent = nodeMap.get(entry.parentId);
            if (parent) {
              parent.children.push(node);
            } else {
              roots.push(node);
            }
          }
        }
        function sortChildren(node) {
          node.children.sort((a, b) => new Date(a.entry.timestamp).getTime() - new Date(b.entry.timestamp).getTime());
          node.children.forEach(sortChildren);
        }
        roots.forEach(sortChildren);
        return roots;
      }

      function buildActivePathIds(targetId) {
        const ids = new Set();
        let current = byId.get(targetId);
        while (current) {
          ids.add(current.id);
          if (!current.parentId || current.parentId === current.id) break;
          current = byId.get(current.parentId);
        }
        return ids;
      }

      function getPath(targetId) {
        const path = [];
        let current = byId.get(targetId);
        while (current) {
          path.unshift(current);
          if (!current.parentId || current.parentId === current.id) break;
          current = byId.get(current.parentId);
        }
        return path;
      }

      let treeNodeMap = null;

      function findNewestLeaf(nodeId) {
        if (!treeNodeMap) {
          treeNodeMap = new Map();
          const tree = buildTree();
          function mapNodes(node) {
            treeNodeMap.set(node.entry.id, node);
            node.children.forEach(mapNodes);
          }
          tree.forEach(mapNodes);
        }

        const node = treeNodeMap.get(nodeId);
        if (!node) return nodeId;

        let current = node;
        while (current.children.length > 0) {
          current = current.children[current.children.length - 1];
        }
        return current.entry.id;
      }

      function escapeHtml(value) {
        return String(value ?? '').replace(/[&<>"']/g, (ch) => ({
          '&': '&amp;',
          '<': '&lt;',
          '>': '&gt;',
          '"': '&quot;',
          "'": '&#39;',
        })[ch]);
      }

      function formatTimestamp(timestamp) {
        if (!timestamp) return '';
        try {
          return new Date(timestamp).toLocaleString();
        } catch (error) {
          return '';
        }
      }

      function sanitizeMarkdownUrl(value) {
        const href = String(value || '').trim().replace(/[\x00-\x1f\x7f]/g, '');
        if (!href) return href;

        const scheme = href.match(/^([A-Za-z][A-Za-z0-9+.-]*):/);
        if (scheme && !/^(https?|mailto|tel|ftp)$/i.test(scheme[1])) {
          return null;
        }

        return href;
      }

      function contentToText(content) {
        if (typeof content === 'string') return content;
        if (!Array.isArray(content)) return content == null ? '' : String(content);
        return content.map((block) => {
          if (!block || typeof block !== 'object') return String(block ?? '');
          if (block.type === 'text') return block.text || '';
          if (block.type === 'thinking') return block.thinking || block.text || '';
          if (block.type === 'toolCall') return `-> ${block.name || 'tool'}(${JSON.stringify(block.arguments || {})})`;
          if (block.type === 'image') return `[image: ${block.mimeType || block.mime_type || 'image'}]`;
          return JSON.stringify(block);
        }).filter(Boolean).join('\n');
      }

      function contentTextOnly(content) {
        if (typeof content === 'string') return content;
        if (!Array.isArray(content)) return content == null ? '' : String(content);
        return content
          .filter(block => block && block.type === 'text' && block.text)
          .map(block => block.text)
          .join('\n');
      }

      function messageText(message) {
        if (!message) return '';
        if (message.role === 'bashExecution') {
          return `$ ${message.command || ''}\n${message.output || ''}`.trim();
        }
        return contentToText(message.content);
      }

      function renderMessageImages(content) {
        if (!Array.isArray(content)) return '';
        const images = content.filter(block => block && block.type === 'image');
        if (images.length === 0) return '';
        return `<div class="message-images">${images.map(img => `<img src="data:${escapeHtml(img.mimeType || img.mime_type || 'image/png')};base64,${escapeHtml(img.data || '')}" class="message-image" alt="">`).join('')}</div>`;
      }

      const imageModal = document.getElementById('image-modal');
      const modalImage = document.getElementById('modal-image');

      function openImageModal(src) {
        if (!imageModal || !modalImage) return;
        modalImage.src = src;
        imageModal.classList.add('open');
      }

      function closeImageModal() {
        if (!imageModal || !modalImage) return;
        imageModal.classList.remove('open');
        modalImage.src = '';
      }

      if (imageModal) {
        imageModal.addEventListener('click', closeImageModal);
      }

      function formatTokens(count) {
        if (count < 1000) return count.toString();
        if (count < 10000) return (count / 1000).toFixed(1) + 'k';
        if (count < 1000000) return Math.round(count / 1000) + 'k';
        return (count / 1000000).toFixed(1) + 'M';
      }

      function replaceTabs(text) {
        return String(text || '').replace(/\t/g, '   ');
      }

      function str(value) {
        if (typeof value === 'string') return value;
        if (value == null) return '';
        return null;
      }

      function shortenPath(path) {
        const value = String(path || '');
        const home = header?.home || '';
        if (home && value.startsWith(home)) {
          return `~${value.slice(home.length)}`;
        }
        const cwd = header?.cwd || '';
        if (cwd && value.startsWith(cwd)) {
          return value.slice(cwd.length).replace(/^\//, '') || '.';
        }
        return value;
      }

      function getLanguageFromPath(filePath) {
        const ext = String(filePath || '').split('.').pop()?.toLowerCase();
        const extToLang = {
          py: 'python', js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript',
          sh: 'bash', bash: 'bash', zsh: 'bash', json: 'json', yaml: 'yaml', yml: 'yaml',
          html: 'html', css: 'css', md: 'markdown', xml: 'xml', sql: 'sql',
          go: 'go', rs: 'rust', rb: 'ruby', java: 'java', c: 'c', cpp: 'cpp'
        };
        return extToLang[ext];
      }

      function findToolResult(toolCallId) {
        for (const entry of entries) {
          if (entry.type === 'message' && entry.message && entry.message.role === 'toolResult') {
            if (entry.message.toolCallId === toolCallId) {
              return entry.message;
            }
          }
        }
        return null;
      }

      function formatExpandableOutput(text, maxLines, lang) {
        text = replaceTabs(text);
        const lines = text.split('\n');
        const displayLines = lines.slice(0, maxLines);
        const remaining = lines.length - maxLines;

        if (lang) {
          let highlighted;
          try {
            highlighted = hljs.highlight(text, { language: lang }).value;
          } catch {
            highlighted = escapeHtml(text);
          }

          if (remaining > 0) {
            const previewCode = displayLines.join('\n');
            let previewHighlighted;
            try {
              previewHighlighted = hljs.highlight(previewCode, { language: lang }).value;
            } catch {
              previewHighlighted = escapeHtml(previewCode);
            }

            return `<div class="tool-output expandable" onclick="if(window.getSelection().toString())return;this.classList.toggle('expanded')">
              <div class="output-preview"><pre><code class="hljs">${previewHighlighted}</code></pre><div class="expand-hint">... (${remaining} more lines)</div></div>
              <div class="output-full"><pre><code class="hljs">${highlighted}</code></pre></div>
            </div>`;
          }

          return `<div class="tool-output"><pre><code class="hljs">${highlighted}</code></pre></div>`;
        }

        if (remaining > 0) {
          let out = "<div class=\"tool-output expandable\" onclick=\"if(window.getSelection().toString())return;this.classList.toggle('expanded')\">";
          out += '<div class="output-preview">';
          for (const line of displayLines) {
            out += `<div>${escapeHtml(line)}</div>`;
          }
          out += `<div class="expand-hint">... (${remaining} more lines)</div></div>`;
          out += '<div class="output-full">';
          for (const line of lines) {
            out += `<div>${escapeHtml(line)}</div>`;
          }
          out += '</div></div>';
          return out;
        }

        return `<div class="tool-output">${displayLines.map(line => `<div>${escapeHtml(line)}</div>`).join('')}</div>`;
      }

      function renderToolCall(call) {
        const result = findToolResult(call.id);
        const isError = result?.isError || false;
        const statusClass = result ? (isError ? 'error' : 'success') : 'pending';
        const args = call.arguments || {};
        const name = call.name || 'tool';
        const toolDomId = `tool-call-${escapeHtml(call.id)}`;
        const invalidArg = '<span class="tool-error">[invalid arg]</span>';
        let html = `<div class="tool-execution ${statusClass}" id="${toolDomId}">`;

        const getResultText = () => {
          if (!result || !Array.isArray(result.content)) return '';
          return result.content
            .filter(block => block && block.type === 'text')
            .map(block => block.text || '')
            .join('\n');
        };

        const getResultImages = () => {
          if (!result || !Array.isArray(result.content)) return [];
          return result.content.filter(block => block && block.type === 'image');
        };

        function renderResultImages() {
          const images = getResultImages();
          if (images.length === 0) return '';
          return '<div class="tool-images">' +
            images.map(img => `<img src="data:${escapeHtml(img.mimeType || 'image/png')};base64,${escapeHtml(img.data || '')}" class="tool-image" />`).join('') +
            '</div>';
        }

        switch (name) {
          case 'bash': {
            const command = str(args.command);
            html += `<div class="tool-command">$ ${command === null ? invalidArg : escapeHtml(command || '...')}</div>`;
            if (result) {
              const output = getResultText().trim();
              if (output) html += formatExpandableOutput(output, 5);
            }
            break;
          }
          case 'read': {
            const filePath = str(args.file_path ?? args.path);
            const offset = args.offset;
            const limit = args.limit;
            let pathHtml = filePath === null ? invalidArg : escapeHtml(shortenPath(filePath || ''));
            if (filePath !== null && (offset !== undefined || limit !== undefined)) {
              const startLine = offset ?? 1;
              const endLine = limit !== undefined ? startLine + limit - 1 : '';
              pathHtml += `<span class="line-numbers">:${startLine}${endLine ? '-' + endLine : ''}</span>`;
            }
            html += `<div class="tool-header"><span class="tool-name">read</span> <span class="tool-path">${pathHtml}</span></div>`;
            if (result) {
              html += renderResultImages();
              const output = getResultText();
              const lang = filePath ? getLanguageFromPath(filePath) : null;
              if (output) html += formatExpandableOutput(output, 10, lang);
            }
            break;
          }
          case 'write': {
            const filePath = str(args.file_path ?? args.path);
            const content = str(args.content);
            html += `<div class="tool-header"><span class="tool-name">write</span> <span class="tool-path">${filePath === null ? invalidArg : escapeHtml(shortenPath(filePath || ''))}</span>`;
            if (content !== null && content) {
              const lines = content.split('\n');
              if (lines.length > 10) html += ` <span class="line-count">(${lines.length} lines)</span>`;
            }
            html += '</div>';
            if (content === null) {
              html += '<div class="tool-error">[invalid content arg - expected string]</div>';
            } else if (content) {
              html += formatExpandableOutput(content, 10, filePath ? getLanguageFromPath(filePath) : null);
            }
            if (result) {
              const output = getResultText().trim();
              if (output) html += `<div class="tool-output"><div>${escapeHtml(output)}</div></div>`;
            }
            break;
          }
          case 'edit': {
            const filePath = str(args.file_path ?? args.path);
            html += `<div class="tool-header"><span class="tool-name">edit</span> <span class="tool-path">${filePath === null ? invalidArg : escapeHtml(shortenPath(filePath || ''))}</span></div>`;
            if (result?.details?.diff) {
              const diffLines = result.details.diff.split('\n');
              html += '<div class="tool-diff">';
              for (const line of diffLines) {
                const cls = line.match(/^\+/) ? 'diff-added' : line.match(/^-/) ? 'diff-removed' : 'diff-context';
                html += `<div class="${cls}">${escapeHtml(replaceTabs(line))}</div>`;
              }
              html += '</div>';
            } else if (result) {
              const output = getResultText().trim();
              if (output) html += `<div class="tool-output"><pre>${escapeHtml(output)}</pre></div>`;
            }
            break;
          }
          case 'ls': {
            const dirPath = str(args.path);
            const limit = args.limit;
            let pathHtml = dirPath === null ? invalidArg : escapeHtml(shortenPath(dirPath || '.'));
            if (limit !== undefined) {
              pathHtml += ` <span class="line-count">(limit ${escapeHtml(String(limit))})</span>`;
            }
            html += `<div class="tool-header"><span class="tool-name">ls</span> <span class="tool-path">${pathHtml}</span></div>`;
            if (result) {
              const output = getResultText().trim();
              if (output) html += formatExpandableOutput(output, 20);
            }
            break;
          }
          default: {
            const rendered = renderedTools?.[call.id];
            if (rendered?.callHtml || rendered?.resultHtmlCollapsed || rendered?.resultHtmlExpanded) {
              html += rendered.callHtml
                ? `<div class="tool-header ansi-rendered">${rendered.callHtml}</div>`
                : `<div class="tool-header"><span class="tool-name">${escapeHtml(name)}</span></div>`;
              if (rendered.resultHtmlCollapsed && rendered.resultHtmlExpanded && rendered.resultHtmlCollapsed !== rendered.resultHtmlExpanded) {
                html += `<div class="tool-output expandable ansi-rendered" onclick="if(window.getSelection().toString())return;this.classList.toggle('expanded')"><div class="output-preview">${rendered.resultHtmlCollapsed}</div><div class="output-full">${rendered.resultHtmlExpanded}</div></div>`;
              } else if (rendered.resultHtmlExpanded) {
                html += `<div class="tool-output ansi-rendered">${rendered.resultHtmlExpanded}</div>`;
              }
            } else {
              html += `<div class="tool-header"><span class="tool-name">${escapeHtml(name)}</span></div>`;
              html += `<div class="tool-output"><pre>${escapeHtml(JSON.stringify(args, null, 2))}</pre></div>`;
            }
            if (result) {
              const output = getResultText();
              if (output) html += formatExpandableOutput(output, 10);
            }
          }
        }

        html += '</div>';
        return html;
      }

      function buildShareUrl(entryId) {
        const baseUrlMeta = document.querySelector('meta[name="pi-share-base-url"]');
        const baseUrl = baseUrlMeta ? baseUrlMeta.content : window.location.href.split('?')[0];
        const url = new URL(window.location.href);
        const gistId = Array.from(url.searchParams.keys()).find(key => !url.searchParams.get(key));
        const params = new URLSearchParams();
        params.set('leafId', currentLeafId);
        params.set('targetId', entryId);

        if (baseUrlMeta) {
          return `${baseUrl}&${params.toString()}`;
        }

        url.search = gistId ? `?${gistId}&${params.toString()}` : `?${params.toString()}`;
        return url.toString();
      }

      async function copyToClipboard(text, button) {
        let success = false;
        try {
          if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(text);
            success = true;
          }
        } catch (error) {
          // Try fallback below.
        }

        if (!success) {
          try {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.opacity = '0';
            document.body.appendChild(textarea);
            textarea.select();
            success = document.execCommand('copy');
            document.body.removeChild(textarea);
          } catch (error) {
            console.error('Failed to copy:', error);
          }
        }

        if (success && button) {
          const originalHtml = button.innerHTML;
          button.innerHTML = 'ok';
          button.classList.add('copied');
          setTimeout(() => {
            button.innerHTML = originalHtml;
            button.classList.remove('copied');
          }, 1500);
        }
      }

      function renderCopyLinkButton(entryId) {
        return `<button class="copy-link-btn" data-entry-id="${escapeHtml(entryId)}" title="Copy link to this message">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
            <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
          </svg>
        </button>`;
      }

      function downloadSessionJson() {
        const lines = [];
        if (header) {
          lines.push(JSON.stringify({ type: 'header', ...header }));
        }
        for (const entry of entries) {
          lines.push(JSON.stringify(entry));
        }
        const jsonlContent = lines.join('\n');

        const blob = new Blob([jsonlContent], { type: 'application/x-ndjson' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${header?.id || 'session'}.jsonl`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
      }
      window.downloadSessionJson = downloadSessionJson;

      function renderEntry(entry) {
        const type = entry.type || 'entry';
        const entryDomId = `entry-${escapeHtml(entry.id)}`;
        const copyBtnHtml = renderCopyLinkButton(entry.id);
        const ts = formatTimestamp(entry.timestamp);
        const tsHtml = ts ? `<div class="message-timestamp">${escapeHtml(ts)}</div>` : '';
        if (type === 'message') {
          const message = entry.message || {};
          const role = message.role || 'message';
          if (role === 'toolResult') return '';
          const text = messageText(message);
          if (role === 'user') {
            const content = message.content;
            const text = typeof content === 'string' ? content :
              (Array.isArray(content) ? content.filter(block => block && block.type === 'text' && block.text).map(block => block.text).join('\n') : '');
            const skillBlock = parseSkillBlock(text);
            if (skillBlock) {
              const images = Array.isArray(content) ? content.filter(block => block && block.type === 'image') : [];
              const hasUserContent = skillBlock.userMessage || images.length > 0;
              let html = `<div class="skill-user-entry" id="${entryDomId}">${copyBtnHtml}${tsHtml}`;
              html += `<div class="skill-invocation" onclick="if(window.getSelection().toString())return;this.classList.toggle('expanded')">
                <div class="skill-invocation-label">[skill] ${escapeHtml(skillBlock.name)}</div>
                <div class="skill-invocation-collapsed">${escapeHtml(skillBlock.name)} (click to expand)</div>
                <div class="skill-invocation-content markdown-content">${safeMarkedParse(skillBlock.content)}</div>
              </div>`;
              if (hasUserContent) {
                html += '<div class="user-message">';
                if (images.length > 0) {
                  html += `<div class="message-images">${images.map(img => `<img src="data:${escapeHtml(img.mimeType || img.mime_type || 'image/png')};base64,${escapeHtml(img.data || '')}" class="message-image" alt="">`).join('')}</div>`;
                }
                if (skillBlock.userMessage) {
                  html += `<div class="markdown-content">${safeMarkedParse(skillBlock.userMessage)}</div>`;
                }
                html += '</div>';
              }
              html += '</div>';
              return html;
            }

            let html = `<div class="user-message" id="${entryDomId}">${copyBtnHtml}${tsHtml}`;
            html += renderMessageImages(content);
            if (text.trim()) {
              html += `<div class="markdown-content">${safeMarkedParse(text)}</div>`;
            }
            html += '</div>';
            return html;
          }

          if (role === 'assistant') {
            let html = `<div class="assistant-message" id="${entryDomId}">${copyBtnHtml}${tsHtml}`;
            for (const block of message.content || []) {
              if (block.type === 'text' && block.text && block.text.trim()) {
                html += `<div class="assistant-text markdown-content">${safeMarkedParse(block.text)}</div>`;
              } else if (block.type === 'thinking' && block.thinking && block.thinking.trim()) {
                html += `<div class="thinking-block"><div class="thinking-text">${escapeHtml(block.thinking)}</div><div class="thinking-collapsed">Thinking ...</div></div>`;
              }
            }
            for (const block of message.content || []) {
              if (block.type === 'toolCall') {
                html += renderToolCall(block);
              }
            }
            if (!html.includes('assistant-text') && !html.includes('thinking-block') && messageText(message).trim()) {
              html += `<div class="assistant-text markdown-content">${safeMarkedParse(messageText(message))}</div>`;
            }
            if (message.stopReason === 'aborted') {
              html += '<div class="error-text">Aborted</div>';
            } else if (message.stopReason === 'error') {
              html += `<div class="error-text">Error: ${escapeHtml(message.errorMessage || 'Unknown error')}</div>`;
            }
            html += '</div>';
            return html;
          }

          if (role === 'bashExecution') {
            const isError = message.cancelled || (message.exitCode !== 0 && message.exitCode !== null);
            let html = `<div class="tool-execution ${isError ? 'error' : 'success'}" id="${entryDomId}">${tsHtml}`;
            html += `<div class="tool-command">$ ${escapeHtml(message.command || '')}</div>`;
            if (message.output) html += formatExpandableOutput(message.output, 10);
            if (message.cancelled) {
              html += '<div class="error-text">(cancelled)</div>';
            } else if (message.exitCode !== 0 && message.exitCode !== null) {
              html += `<div class="error-text">(exit ${message.exitCode})</div>`;
            }
            html += '</div>';
            return html;
          }
        }

        if (entry.type === 'model_change') {
          return `<div class="model-change" id="${entryDomId}">${tsHtml}Switched to model: <span class="model-name">${escapeHtml(entry.provider || '')}/${escapeHtml(entry.modelId || '')}</span></div>`;
        }

        if (entry.type === 'compaction') {
          return `<div class="compaction" id="${entryDomId}" onclick="if(window.getSelection().toString())return;this.classList.toggle('expanded')">
            <div class="compaction-label">[compaction]</div>
            <div class="compaction-collapsed">Compacted from ${entry.tokensBefore.toLocaleString()} tokens</div>
            <div class="compaction-content"><strong>Compacted from ${entry.tokensBefore.toLocaleString()} tokens</strong>\n\n${escapeHtml(entry.summary || '')}</div>
          </div>`;
        }

        if (entry.type === 'branch_summary') {
          return `<div class="branch-summary" id="${entryDomId}">${tsHtml}
            <div class="branch-summary-header">Branch Summary</div>
            <div class="markdown-content">${safeMarkedParse(entry.summary || '')}</div>
          </div>`;
        }

        if (entry.type === 'custom_message' && entry.display) {
          const content = typeof entry.content === 'string' ? entry.content : JSON.stringify(entry.content);
          return `<div class="hook-message" id="${entryDomId}">${tsHtml}
            <div class="hook-type">[${escapeHtml(entry.customType)}]</div>
            <div class="markdown-content">${safeMarkedParse(content || '')}</div>
          </div>`;
        }

        return '';
      }

      function entryPreview(entry) {
        if (entry.type === 'message') {
          const msg = entry.message || {};
          const text = messageText(msg).replace(/\s+/g, ' ').trim();
          return `${msg.role || 'message'}: ${text || entry.id}`;
        }
        if (entry.type === 'label') return `label: ${entry.label || entry.targetId || entry.id}`;
        return `${entry.type || 'entry'}: ${entry.id}`;
      }

      // ============================================================
      // FILTERING
      // ============================================================

      let filterMode = 'default';
      let searchQuery = '';
      let currentLeafId = leafId;
      let currentTargetId = urlTargetId || leafId;

      function hasTextContent(content) {
        if (typeof content === 'string') return content.trim().length > 0;
        if (Array.isArray(content)) {
          for (const item of content) {
            if (item && item.type === 'text' && item.text && item.text.trim().length > 0) return true;
          }
        }
        return false;
      }

      function extractContent(content) {
        if (typeof content === 'string') return content;
        if (Array.isArray(content)) {
          return content
            .filter(item => item && item.type === 'text' && item.text)
            .map(item => item.text)
            .join('');
        }
        return '';
      }

      function parseSkillBlock(text) {
        const match = text.match(/^<skill name="([^"]+)" location="([^"]+)">\n([\s\S]*?)\n<\/skill>(?:\n\n([\s\S]+))?$/);
        if (!match) return null;
        return {
          name: match[1],
          location: match[2],
          content: match[3],
          userMessage: match[4]?.trim() || undefined,
        };
      }

      function getSearchableText(entry, label) {
        const parts = [];
        if (label) parts.push(label);

        if (entry.type === 'message') {
          const message = entry.message || {};
          parts.push(message.role || '');
          if (message.content) parts.push(extractContent(message.content));
          if (message.role === 'bashExecution' && message.command) parts.push(message.command);
        } else if (entry.type === 'custom_message') {
          parts.push(entry.customType || '');
          parts.push(typeof entry.content === 'string' ? entry.content : extractContent(entry.content));
        } else if (entry.type === 'compaction') {
          parts.push('compaction');
        } else if (entry.type === 'branch_summary') {
          parts.push('branch summary', entry.summary || '');
        } else if (entry.type === 'model_change') {
          parts.push('model', entry.modelId || '');
        } else if (entry.type === 'thinking_level_change') {
          parts.push('thinking', entry.thinkingLevel || '');
        } else if (entry.type === 'label') {
          parts.push('label', entry.label || '');
        }

        return parts.join(' ').toLowerCase();
      }

      function filterNodes(flatNodes, currentLeafId) {
        const searchTokens = searchQuery.toLowerCase().split(/\s+/).filter(Boolean);

        return flatNodes.filter(({ node }) => {
          const entry = node.entry;
          const label = node.label;
          const isCurrentLeaf = entry.id === currentLeafId;

          if (isCurrentLeaf) return true;

          if (entry.type === 'message' && entry.message && entry.message.role === 'assistant') {
            const message = entry.message;
            const hasText = hasTextContent(message.content);
            const isErrorOrAborted = message.stopReason && message.stopReason !== 'stop' && message.stopReason !== 'toolUse';
            if (!hasText && !isErrorOrAborted) return false;
          }

          const isSettingsEntry = ['label', 'custom', 'model_change', 'thinking_level_change'].includes(entry.type);
          let passesFilter = true;

          switch (filterMode) {
            case 'user-only':
              passesFilter = entry.type === 'message' && entry.message && entry.message.role === 'user';
              break;
            case 'no-tools':
              passesFilter = !isSettingsEntry && !(entry.type === 'message' && entry.message && entry.message.role === 'toolResult');
              break;
            case 'labeled-only':
              passesFilter = label !== undefined;
              break;
            case 'all':
              passesFilter = true;
              break;
            default:
              passesFilter = !isSettingsEntry;
              break;
          }

          if (!passesFilter) return false;

          if (searchTokens.length > 0) {
            const nodeText = getSearchableText(entry, label);
            if (!searchTokens.every(token => nodeText.includes(token))) return false;
          }

          return true;
        });

        recalculateVisualStructure(filtered, flatNodes);

        return filtered;
      }

      function recalculateVisualStructure(filteredNodes, allFlatNodes) {
        if (filteredNodes.length === 0) return;

        const visibleIds = new Set(filteredNodes.map(row => row.node.entry.id));
        const entryMap = new Map();
        for (const flatNode of allFlatNodes) {
          entryMap.set(flatNode.node.entry.id, flatNode);
        }

        function findVisibleAncestor(nodeId) {
          let currentId = entryMap.get(nodeId)?.node.entry.parentId;
          while (currentId != null) {
            if (visibleIds.has(currentId)) return currentId;
            currentId = entryMap.get(currentId)?.node.entry.parentId;
          }
          return null;
        }

        const visibleChildren = new Map();
        visibleChildren.set(null, []);
        for (const flatNode of filteredNodes) {
          const nodeId = flatNode.node.entry.id;
          const ancestorId = findVisibleAncestor(nodeId);
          if (!visibleChildren.has(ancestorId)) visibleChildren.set(ancestorId, []);
          visibleChildren.get(ancestorId).push(nodeId);
        }

        const visibleRootIds = visibleChildren.get(null);
        const multipleRoots = visibleRootIds.length > 1;
        const filteredNodeMap = new Map();
        for (const flatNode of filteredNodes) {
          filteredNodeMap.set(flatNode.node.entry.id, flatNode);
        }

        const stack = [];
        for (let i = visibleRootIds.length - 1; i >= 0; i--) {
          const isLast = i === visibleRootIds.length - 1;
          stack.push([visibleRootIds[i], multipleRoots ? 1 : 0, multipleRoots, multipleRoots, isLast, [], multipleRoots]);
        }

        while (stack.length > 0) {
          const [nodeId, indent, justBranched, showConnector, isLast, gutters, isVirtualRootChild] = stack.pop();
          const flatNode = filteredNodeMap.get(nodeId);
          if (!flatNode) continue;

          flatNode.indent = indent;
          flatNode.showConnector = showConnector;
          flatNode.isLast = isLast;
          flatNode.gutters = gutters;
          flatNode.isVirtualRootChild = isVirtualRootChild;
          flatNode.multipleRoots = multipleRoots;

          const children = visibleChildren.get(nodeId) || [];
          const multipleChildren = children.length > 1;
          const childIndent = multipleChildren ? indent + 1 : (justBranched && indent > 0 ? indent + 1 : indent);
          const connectorDisplayed = showConnector && !isVirtualRootChild;
          const currentDisplayIndent = multipleRoots ? Math.max(0, indent - 1) : indent;
          const connectorPosition = Math.max(0, currentDisplayIndent - 1);
          const childGutters = connectorDisplayed ? [...gutters, { position: connectorPosition, show: !isLast }] : gutters;

          for (let i = children.length - 1; i >= 0; i--) {
            const childIsLast = i === children.length - 1;
            stack.push([children[i], childIndent, multipleChildren, multipleChildren, childIsLast, childGutters, false]);
          }
        }
      }

      function flattenTree(roots, activePathIds) {
        const result = [];
        const multipleRoots = roots.length > 1;
        const containsActive = new Map();

        function markActive(node) {
          let has = activePathIds.has(node.entry.id);
          for (const child of node.children) {
            if (markActive(child)) has = true;
          }
          containsActive.set(node, has);
          return has;
        }
        roots.forEach(markActive);

        const stack = [];
        const orderedRoots = [...roots].sort((a, b) => Number(containsActive.get(b)) - Number(containsActive.get(a)));
        for (let i = orderedRoots.length - 1; i >= 0; i--) {
          const isLast = i === orderedRoots.length - 1;
          stack.push([orderedRoots[i], multipleRoots ? 1 : 0, multipleRoots, multipleRoots, isLast, [], multipleRoots]);
        }

        while (stack.length > 0) {
          const [node, indent, justBranched, showConnector, isLast, gutters, isVirtualRootChild] = stack.pop();
          result.push({ node, indent, showConnector, isLast, gutters, isVirtualRootChild, multipleRoots });

          const children = node.children;
          const multipleChildren = children.length > 1;
          const orderedChildren = [...children].sort((a, b) => Number(containsActive.get(b)) - Number(containsActive.get(a)));
          const childIndent = multipleChildren ? indent + 1 : (justBranched && indent > 0 ? indent + 1 : indent);
          const connectorDisplayed = showConnector && !isVirtualRootChild;
          const currentDisplayIndent = multipleRoots ? Math.max(0, indent - 1) : indent;
          const connectorPosition = Math.max(0, currentDisplayIndent - 1);
          const childGutters = connectorDisplayed ? [...gutters, { position: connectorPosition, show: !isLast }] : gutters;

          for (let i = orderedChildren.length - 1; i >= 0; i--) {
            const childIsLast = i === orderedChildren.length - 1;
            stack.push([orderedChildren[i], childIndent, multipleChildren, multipleChildren, childIsLast, childGutters, false]);
          }
        }

        return result;
      }

      function buildTreePrefix(flatNode) {
        const { indent, showConnector, isLast, gutters, isVirtualRootChild, multipleRoots } = flatNode;
        const displayIndent = multipleRoots ? Math.max(0, indent - 1) : indent;
        const connector = showConnector && !isVirtualRootChild ? (isLast ? '└─ ' : '├─ ') : '';
        const connectorPosition = connector ? displayIndent - 1 : -1;

        const totalChars = displayIndent * 3;
        const prefixChars = [];
        for (let i = 0; i < totalChars; i++) {
          const level = Math.floor(i / 3);
          const posInLevel = i % 3;
          const gutter = gutters.find(item => item.position === level);
          if (gutter) {
            prefixChars.push(posInLevel === 0 ? (gutter.show ? '│' : ' ') : ' ');
          } else if (connector && level === connectorPosition) {
            if (posInLevel === 0) {
              prefixChars.push(isLast ? '└' : '├');
            } else if (posInLevel === 1) {
              prefixChars.push('─');
            } else {
              prefixChars.push(' ');
            }
          } else {
            prefixChars.push(' ');
          }
        }
        return prefixChars.join('');
      }

      function truncate(s, maxLen = 100) {
        if (s.length <= maxLen) return s;
        return s.slice(0, maxLen) + '...';
      }

      function formatToolCall(name, args) {
        switch (name) {
          case 'read': {
            const path = shortenPath(String(args.path || args.file_path || ''));
            const offset = args.offset;
            const limit = args.limit;
            let display = path;
            if (offset !== undefined || limit !== undefined) {
              const start = offset ?? 1;
              const end = limit !== undefined ? start + limit - 1 : '';
              display += `:${start}${end ? `-${end}` : ''}`;
            }
            return `[read: ${display}]`;
          }
          case 'write':
            return `[write: ${shortenPath(String(args.path || args.file_path || ''))}]`;
          case 'edit':
            return `[edit: ${shortenPath(String(args.path || args.file_path || ''))}]`;
          case 'bash': {
            const rawCmd = String(args.command || '');
            const cmd = rawCmd.replace(/[\n\t]/g, ' ').trim().slice(0, 50);
            return `[bash: ${cmd}${rawCmd.length > 50 ? '...' : ''}]`;
          }
          case 'grep':
            return `[grep: /${args.pattern || ''}/ in ${shortenPath(String(args.path || '.'))}]`;
          case 'find':
            return `[find: ${args.pattern || ''} in ${shortenPath(String(args.path || '.'))}]`;
          case 'ls':
            return `[ls: ${shortenPath(String(args.path || '.'))}]`;
          default: {
            const rawArgs = JSON.stringify(args);
            const argsStr = rawArgs.slice(0, 40);
            return `[${name}: ${argsStr}${rawArgs.length > 40 ? '...' : ''}]`;
          }
        }
      }

      function getTreeNodeDisplayHtml(entry, label) {
        const normalize = value => String(value || '').replace(/[\n\t]/g, ' ').trim();
        const labelHtml = label ? `<span class="tree-label">[${escapeHtml(label)}]</span> ` : '';

        if (entry.type === 'message') {
          const message = entry.message || {};
          if (message.role === 'user') {
            const rawContent = extractContent(message.content);
            const skillBlock = parseSkillBlock(rawContent);
            if (skillBlock) {
              let treeHtml = labelHtml + `<span class="tree-role-skill">skill:</span> ${escapeHtml(skillBlock.name)}`;
              if (skillBlock.userMessage) {
                treeHtml += ` · <span class="tree-role-user">user:</span> ${escapeHtml(truncate(normalize(skillBlock.userMessage)))}`;
              }
              return treeHtml;
            }
            return labelHtml + `<span class="tree-role-user">user:</span> ${escapeHtml(truncate(normalize(rawContent)))}`;
          }
          if (message.role === 'assistant') {
            const textContent = truncate(normalize(extractContent(message.content)));
            if (textContent) return labelHtml + `<span class="tree-role-assistant">assistant:</span> ${escapeHtml(textContent)}`;
            if (message.stopReason === 'aborted') return labelHtml + '<span class="tree-role-assistant">assistant:</span> <span class="tree-muted">(aborted)</span>';
            if (message.errorMessage) return labelHtml + `<span class="tree-role-assistant">assistant:</span> <span class="tree-error">${escapeHtml(truncate(message.errorMessage))}</span>`;
            return labelHtml + '<span class="tree-role-assistant">assistant:</span> <span class="tree-muted">(no text)</span>';
          }
          if (message.role === 'toolResult') {
            const toolCall = message.toolCallId ? toolCallMap.get(message.toolCallId) : null;
            if (toolCall) {
              return labelHtml + `<span class="tree-role-tool">${escapeHtml(formatToolCall(toolCall.name, toolCall.arguments))}</span>`;
            }
            return labelHtml + `<span class="tree-role-tool">[${escapeHtml(message.toolName || 'tool')}]</span>`;
          }
          if (message.role === 'bashExecution') {
            return labelHtml + `<span class="tree-role-tool">[bash]:</span> ${escapeHtml(truncate(normalize(message.command || '')))}`;
          }
          return labelHtml + `<span class="tree-muted">[${escapeHtml(message.role || 'message')}]</span>`;
        }
        if (entry.type === 'compaction') {
          const tokens = Number(entry.tokensBefore || 0);
          return labelHtml + `<span class="tree-compaction">[compaction: ${Math.round(tokens / 1000)}k tokens]</span>`;
        }
        if (entry.type === 'branch_summary') {
          return labelHtml + `<span class="tree-branch-summary">[branch summary]:</span> ${escapeHtml(truncate(normalize(entry.summary || '')))}`;
        }
        if (entry.type === 'custom_message') {
          const content = typeof entry.content === 'string' ? entry.content : extractContent(entry.content);
          return labelHtml + `<span class="tree-custom">[${escapeHtml(entry.customType || 'custom')}]:</span> ${escapeHtml(truncate(normalize(content)))}`;
        }
        if (entry.type === 'model_change') {
          return labelHtml + `<span class="tree-muted">[model: ${escapeHtml(entry.modelId || '')}]</span>`;
        }
        if (entry.type === 'thinking_level_change') {
          return labelHtml + `<span class="tree-muted">[thinking: ${escapeHtml(entry.thinkingLevel || '')}]</span>`;
        }
        return labelHtml + `<span class="tree-muted">[${escapeHtml(entry.type || 'entry')}]</span>`;
      }

      let treeRendered = false;

      function renderTree(activeId) {
        currentLeafId = activeId || currentLeafId;
        const activePathIds = buildActivePathIds(currentLeafId);
        const rows = flattenTree(buildTree(), activePathIds);
        const filtered = filterNodes(rows, currentLeafId);
        const tree = document.getElementById('tree-container');
        if (!treeRendered) {
          tree.innerHTML = '';
          for (const flatNode of filtered) {
            const entry = flatNode.node.entry;
            const isOnPath = activePathIds.has(entry.id);
            const isTarget = entry.id === currentTargetId;

            const div = document.createElement('div');
            div.className = 'tree-node';
            if (isOnPath) div.classList.add('in-path');
            if (isTarget) div.classList.add('active');
            div.dataset.id = entry.id;

            const prefix = buildTreePrefix(flatNode);
            const prefixSpan = document.createElement('span');
            prefixSpan.className = 'tree-prefix';
            prefixSpan.textContent = prefix;

            const marker = document.createElement('span');
            marker.className = 'tree-marker';
            marker.textContent = isOnPath ? '•' : ' ';

            const content = document.createElement('span');
            content.className = 'tree-content';
            content.innerHTML = getTreeNodeDisplayHtml(entry, flatNode.node.label);

            div.appendChild(prefixSpan);
            div.appendChild(marker);
            div.appendChild(content);
            div.addEventListener('click', () => {
              if (window.getSelection().toString()) return;
              const leafId = findNewestLeaf(entry.id);
              navigateTo(leafId, 'target', entry.id);
            });

            tree.appendChild(div);
          }
          treeRendered = true;
        } else {
          const nodes = tree.querySelectorAll('.tree-node');
          for (const node of nodes) {
            const id = node.dataset.id;
            const isOnPath = activePathIds.has(id);
            const isTarget = id === currentTargetId;
            node.classList.toggle('in-path', isOnPath);
            node.classList.toggle('active', isTarget);
            const marker = node.querySelector('.tree-marker');
            if (marker) {
              marker.textContent = isOnPath ? '•' : ' ';
            }
          }
        }
        document.getElementById('tree-status').textContent = `${filtered.length} / ${rows.length} entries`;
        setTimeout(() => {
          const activeNode = tree.querySelector('.tree-node.active');
          if (activeNode) activeNode.scrollIntoView({ block: 'nearest' });
        }, 0);
      }

      function forceTreeRerender() {
        treeRendered = false;
        renderTree(currentLeafId);
      }

      // ============================================================
      // MARKDOWN RENDERING
      // ============================================================

      const strictStrikethroughRegex = /^(~~)(?=[^\s~])((?:\\.|[^\\])*?(?:\\.|[^\s~\\]))\1(?=[^~]|$)/;

      marked.use({
        breaks: true,
        gfm: true,
        tokenizer: {
          html() {
            return undefined;
          },
          tag() {
            return undefined;
          },
          del(src) {
            const match = strictStrikethroughRegex.exec(src);
            if (!match) return undefined;
            return {
              type: 'del',
              raw: match[0],
              text: match[2],
              tokens: this.lexer.inlineTokens(match[2])
            };
          }
        },
        renderer: {
          link(token) {
            const href = sanitizeMarkdownUrl(token.href);
            if (href === null) {
              return this.parser.parseInline(token.tokens);
            }
            let out = '<a href="' + escapeHtml(href) + '"';
            if (token.title) {
              out += ' title="' + escapeHtml(token.title) + '"';
            }
            out += '>' + this.parser.parseInline(token.tokens) + '</a>';
            return out;
          },
          image(token) {
            const href = sanitizeMarkdownUrl(token.href);
            if (href === null) {
              return escapeHtml(token.text || '');
            }
            let out = '<img src="' + escapeHtml(href) + '" alt="' + escapeHtml(token.text || '') + '"';
            if (token.title) {
              out += ' title="' + escapeHtml(token.title) + '"';
            }
            out += '>';
            return out;
          },
          code(token) {
            const code = token.text;
            const lang = token.lang;
            let highlighted;
            if (lang && hljs.getLanguage(lang)) {
              try {
                highlighted = hljs.highlight(code, { language: lang }).value;
              } catch {
                highlighted = escapeHtml(code);
              }
            } else {
              try {
                highlighted = hljs.highlightAuto(code).value;
              } catch {
                highlighted = escapeHtml(code);
              }
            }
            return `<pre><code class="hljs">${highlighted}</code></pre>`;
          },
          codespan(token) {
            return `<code>${escapeHtml(token.text)}</code>`;
          }
        }
      });

      function safeMarkedParse(text) {
        return marked.parse(text);
      }

      const searchInput = document.getElementById('tree-search');
      searchInput.addEventListener('input', (event) => {
        searchQuery = event.target.value;
        forceTreeRerender();
      });

      document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.addEventListener('click', () => {
          document.querySelectorAll('.filter-btn').forEach(button => button.classList.remove('active'));
          btn.classList.add('active');
          filterMode = btn.dataset.filter;
          forceTreeRerender();
        });
      });

      const sidebar = document.getElementById('sidebar');
      const overlay = document.getElementById('sidebar-overlay');
      const hamburger = document.getElementById('hamburger');
      const sidebarResizer = document.getElementById('sidebar-resizer');
      const SIDEBAR_WIDTH_STORAGE_KEY = 'pi-share:v1:sidebar-width';
      const MIN_CONTENT_WIDTH = 320;

      function isMobileLayout() {
        return window.matchMedia('(max-width: 900px)').matches;
      }

      function getSidebarBounds() {
        const rootStyles = getComputedStyle(document.documentElement);
        const minWidth = parseFloat(rootStyles.getPropertyValue('--sidebar-min-width')) || 240;
        const maxWidth = parseFloat(rootStyles.getPropertyValue('--sidebar-max-width')) || 720;
        const viewportMaxWidth = window.innerWidth - MIN_CONTENT_WIDTH;
        return {
          minWidth,
          maxWidth: Math.max(minWidth, Math.min(maxWidth, viewportMaxWidth))
        };
      }

      function clampSidebarWidth(width) {
        const { minWidth, maxWidth } = getSidebarBounds();
        return Math.max(minWidth, Math.min(maxWidth, width));
      }

      function applySidebarWidth(width) {
        document.documentElement.style.setProperty('--sidebar-width', `${Math.round(clampSidebarWidth(width))}px`);
      }

      function loadSidebarWidth() {
        try {
          const raw = localStorage.getItem(SIDEBAR_WIDTH_STORAGE_KEY);
          if (raw === null) return null;
          const width = Number(raw);
          return Number.isFinite(width) ? width : null;
        } catch {
          return null;
        }
      }

      function saveSidebarWidth(width) {
        try {
          localStorage.setItem(SIDEBAR_WIDTH_STORAGE_KEY, String(Math.round(clampSidebarWidth(width))));
        } catch {
          // Ignore storage failures.
        }
      }

      function setupSidebarResize() {
        const savedWidth = loadSidebarWidth();
        if (savedWidth !== null) {
          applySidebarWidth(savedWidth);
        }

        if (!sidebarResizer || !sidebar) return;

        let cleanupDrag = null;

        const stopDrag = (pointerId) => {
          if (cleanupDrag) {
            cleanupDrag(pointerId);
            cleanupDrag = null;
          }
        };

        sidebarResizer.addEventListener('pointerdown', (event) => {
          if (isMobileLayout()) return;

          event.preventDefault();
          const startX = event.clientX;
          const startWidth = sidebar.getBoundingClientRect().width;
          document.body.classList.add('sidebar-resizing');
          sidebarResizer.setPointerCapture?.(event.pointerId);

          const onPointerMove = (event) => {
            applySidebarWidth(startWidth + (event.clientX - startX));
          };

          cleanupDrag = (pointerIdToRelease) => {
            document.body.classList.remove('sidebar-resizing');
            sidebarResizer.releasePointerCapture?.(pointerIdToRelease);
            window.removeEventListener('pointermove', onPointerMove);
            window.removeEventListener('pointerup', onPointerUp);
            window.removeEventListener('pointercancel', onPointerCancel);
            saveSidebarWidth(sidebar.getBoundingClientRect().width);
          };

          const onPointerUp = (event) => stopDrag(event.pointerId);
          const onPointerCancel = (event) => stopDrag(event.pointerId);

          window.addEventListener('pointermove', onPointerMove);
          window.addEventListener('pointerup', onPointerUp);
          window.addEventListener('pointercancel', onPointerCancel);
        });

        sidebarResizer.addEventListener('dblclick', () => {
          if (isMobileLayout()) return;
          applySidebarWidth(400);
          saveSidebarWidth(400);
        });

        window.addEventListener('resize', () => {
          if (isMobileLayout()) return;
          applySidebarWidth(sidebar.getBoundingClientRect().width);
        });
      }

      setupSidebarResize();

      if (hamburger && sidebar && overlay) {
        hamburger.addEventListener('click', () => {
          sidebar.classList.add('open');
          overlay.classList.add('open');
          hamburger.style.display = 'none';
        });
      }

      const closeSidebar = () => {
        if (!sidebar || !overlay || !hamburger) return;
        sidebar.classList.remove('open');
        overlay.classList.remove('open');
        hamburger.style.display = '';
      };

      if (overlay) overlay.addEventListener('click', closeSidebar);
      document.getElementById('sidebar-close')?.addEventListener('click', closeSidebar);

      function computeStats(entryList) {
        let userMessages = 0;
        let assistantMessages = 0;
        let toolResults = 0;
        let customMessages = 0;
        let compactions = 0;
        let branchSummaries = 0;
        let toolCalls = 0;
        const tokens = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 };
        const cost = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 };
        const models = new Set();

        for (const entry of entryList) {
          if (entry.type === 'message') {
            const msg = entry.message || {};
            if (msg.role === 'user') userMessages++;
            if (msg.role === 'assistant') {
              assistantMessages++;
              if (msg.model) models.add(msg.provider ? `${msg.provider}/${msg.model}` : msg.model);
              if (msg.usage) {
                tokens.input += msg.usage.input || 0;
                tokens.output += msg.usage.output || 0;
                tokens.cacheRead += msg.usage.cacheRead || 0;
                tokens.cacheWrite += msg.usage.cacheWrite || 0;
                if (msg.usage.cost) {
                  cost.input += msg.usage.cost.input || 0;
                  cost.output += msg.usage.cost.output || 0;
                  cost.cacheRead += msg.usage.cost.cacheRead || 0;
                  cost.cacheWrite += msg.usage.cost.cacheWrite || 0;
                }
              }
              toolCalls += Array.isArray(msg.content) ? msg.content.filter(c => c.type === 'toolCall').length : 0;
            }
            if (msg.role === 'toolResult') toolResults++;
          } else if (entry.type === 'compaction') {
            compactions++;
          } else if (entry.type === 'branch_summary') {
            branchSummaries++;
          } else if (entry.type === 'custom_message') {
            customMessages++;
          }
        }

        return { userMessages, assistantMessages, toolResults, customMessages, compactions, branchSummaries, toolCalls, tokens, cost, models: Array.from(models) };
      }

      const globalStats = computeStats(entries);

      let thinkingExpanded = true;
      let toolOutputsExpanded = false;

      function toggleThinking() {
        thinkingExpanded = !thinkingExpanded;
        document.querySelectorAll('.thinking-text').forEach(el => {
          el.style.display = thinkingExpanded ? '' : 'none';
        });
        document.querySelectorAll('.thinking-collapsed').forEach(el => {
          el.style.display = thinkingExpanded ? 'none' : 'block';
        });
      }

      function toggleToolOutputs() {
        toolOutputsExpanded = !toolOutputsExpanded;
        document.querySelectorAll('.tool-output.expandable').forEach(el => {
          el.classList.toggle('expanded', toolOutputsExpanded);
        });
        document.querySelectorAll('.compaction').forEach(el => {
          el.classList.toggle('expanded', toolOutputsExpanded);
        });
        document.querySelectorAll('.skill-invocation').forEach(el => {
          el.classList.toggle('expanded', toolOutputsExpanded);
        });
      }

      function attachHeaderHandlers() {
        document.querySelector('[data-action="toggle-thinking"]')?.addEventListener('click', toggleThinking);
        document.querySelector('[data-action="toggle-tools"]')?.addEventListener('click', toggleToolOutputs);
      }

      function isEditableTarget(element) {
        if (!element) return false;
        const tagName = element.tagName;
        if (tagName === 'INPUT' || tagName === 'TEXTAREA' || tagName === 'SELECT' || tagName === 'BUTTON') {
          return true;
        }
        return element.isContentEditable || Boolean(element.closest?.('[contenteditable="true"]'));
      }

      document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          searchInput.value = '';
          searchQuery = '';
          closeSidebar();
          navigateTo(leafId, 'bottom');
          return;
        }

        if (isEditableTarget(document.activeElement)) {
          return;
        }

        const key = event.key.toLowerCase();
        if (key === 't') {
          event.preventDefault();
          toggleThinking();
        } else if (key === 'o') {
          event.preventDefault();
          toggleToolOutputs();
        }
      });

      function renderHeader() {
        const totalCost = globalStats.cost.input + globalStats.cost.output + globalStats.cost.cacheRead + globalStats.cost.cacheWrite;

        const tokenParts = [];
        if (globalStats.tokens.input) tokenParts.push(`I${formatTokens(globalStats.tokens.input)}`);
        if (globalStats.tokens.output) tokenParts.push(`O${formatTokens(globalStats.tokens.output)}`);
        if (globalStats.tokens.cacheRead) tokenParts.push(`R${formatTokens(globalStats.tokens.cacheRead)}`);
        if (globalStats.tokens.cacheWrite) tokenParts.push(`W${formatTokens(globalStats.tokens.cacheWrite)}`);

        const msgParts = [];
        if (globalStats.userMessages) msgParts.push(`${globalStats.userMessages} user`);
        if (globalStats.assistantMessages) msgParts.push(`${globalStats.assistantMessages} assistant`);
        if (globalStats.toolResults) msgParts.push(`${globalStats.toolResults} tool results`);
        if (globalStats.customMessages) msgParts.push(`${globalStats.customMessages} custom`);
        if (globalStats.compactions) msgParts.push(`${globalStats.compactions} compactions`);
        if (globalStats.branchSummaries) msgParts.push(`${globalStats.branchSummaries} branch summaries`);

        let html = `
          <div class="header">
            <h1>Session: ${escapeHtml(header?.id || 'unknown')}</h1>
            <div class="help-bar">
              <span class="help-hint">T toggle thinking - O toggle tools</span>
              <div class="help-actions">
                <button type="button" class="header-toggle-btn" data-action="toggle-thinking" title="Toggle thinking (T)">Toggle thinking</button>
                <button type="button" class="header-toggle-btn" data-action="toggle-tools" title="Toggle tools (O)">Toggle tools</button>
                <button type="button" class="download-json-btn" onclick="downloadSessionJson()" title="Download session as JSONL">JSONL</button>
              </div>
            </div>
            <div class="header-info">
              <div class="info-item"><span class="info-label">Date:</span><span class="info-value">${header?.timestamp ? new Date(header.timestamp).toLocaleString() : 'unknown'}</span></div>
              <div class="info-item"><span class="info-label">Models:</span><span class="info-value">${escapeHtml(globalStats.models.join(', ') || 'unknown')}</span></div>
              <div class="info-item"><span class="info-label">Messages:</span><span class="info-value">${msgParts.join(', ') || '0'}</span></div>
              <div class="info-item"><span class="info-label">Tool Calls:</span><span class="info-value">${globalStats.toolCalls}</span></div>
              <div class="info-item"><span class="info-label">Tokens:</span><span class="info-value">${tokenParts.join(' ') || '0'}</span></div>
              <div class="info-item"><span class="info-label">Cost:</span><span class="info-value">$${totalCost.toFixed(3)}</span></div>
            </div>
          </div>`;

        if (systemPrompt) {
          const lines = systemPrompt.split('\n');
          const previewLines = 10;
          if (lines.length > previewLines) {
            const preview = lines.slice(0, previewLines).join('\n');
            const remaining = lines.length - previewLines;
            html += `<div class="system-prompt expandable" onclick="if(window.getSelection().toString())return;this.classList.toggle('expanded')">
              <div class="system-prompt-header">System Prompt</div>
              <div class="system-prompt-preview">${escapeHtml(preview)}</div>
              <div class="system-prompt-expand-hint">... (${remaining} more lines, click to expand)</div>
              <div class="system-prompt-full">${escapeHtml(systemPrompt)}</div>
            </div>`;
          } else {
            html += `<div class="system-prompt">
              <div class="system-prompt-header">System Prompt</div>
              <div class="system-prompt-full" style="display: block">${escapeHtml(systemPrompt)}</div>
            </div>`;
          }
        }

        if (tools && tools.length > 0) {
          html += `<div class="tools-list">
            <div class="tools-header">Available Tools</div>
            <div class="tools-content">
              ${tools.map(t => {
                const hasParams = t.parameters && typeof t.parameters === 'object' && t.parameters.properties && Object.keys(t.parameters.properties).length > 0;
                if (!hasParams) {
                  return `<div class="tool-item"><span class="tool-item-name">${escapeHtml(t.name)}</span> - <span class="tool-item-desc">${escapeHtml(t.description)}</span></div>`;
                }
                const params = t.parameters;
                const properties = params.properties;
                const required = params.required || [];
                let paramsHtml = '';
                for (const [name, prop] of Object.entries(properties)) {
                  const isRequired = required.includes(name);
                  const typeStr = prop.type || 'any';
                  const reqLabel = isRequired ? '<span class="tool-param-required">required</span>' : '<span class="tool-param-optional">optional</span>';
                  paramsHtml += `<div class="tool-param"><span class="tool-param-name">${escapeHtml(name)}</span> <span class="tool-param-type">${escapeHtml(typeStr)}</span> ${reqLabel}`;
                  if (prop.description) {
                    paramsHtml += `<div class="tool-param-desc">${escapeHtml(prop.description)}</div>`;
                  }
                  paramsHtml += '</div>';
                }
                return `<div class="tool-item" onclick="if(window.getSelection().toString())return;this.classList.toggle('params-expanded')"><span class="tool-item-name">${escapeHtml(t.name)}</span> - <span class="tool-item-desc">${escapeHtml(t.description)}</span> <span class="tool-params-hint"></span><div class="tool-params-content">${paramsHtml}</div></div>`;
              }).join('')}
            </div>
          </div>`;
        }

        return html;
      }

      const entryCache = new Map();

      function getScrollTargetElementId(entryId) {
        const entry = byId.get(entryId);
        if (entry?.type === 'message' && entry.message.role === 'toolResult' && entry.message.toolCallId) {
          return `tool-call-${entry.message.toolCallId}`;
        }
        return `entry-${entryId}`;
      }

      function renderEntryToNode(entry) {
        if (entryCache.has(entry.id)) {
          return entryCache.get(entry.id).cloneNode(true);
        }

        const html = renderEntry(entry);
        if (!html) return null;

        const template = document.createElement('template');
        template.innerHTML = html;
        const node = template.content.firstElementChild;

        if (node) {
          entryCache.set(entry.id, node.cloneNode(true));
        }
        return node;
      }

      function navigateTo(targetId, scrollMode = 'target', scrollToEntryId = null) {
        const target = targetId || leafId || (entries.length ? entries[entries.length - 1].id : null);
        currentLeafId = target;
        currentTargetId = scrollToEntryId || target;
        const path = target ? getPath(target) : entries;
        renderTree(target);
        document.getElementById('header-container').innerHTML = renderHeader();
        attachHeaderHandlers();

        const messagesEl = document.getElementById('messages');
        const fragment = document.createDocumentFragment();
        for (const entry of path) {
          const node = renderEntryToNode(entry);
          if (node) {
            fragment.appendChild(node);
          }
        }
        messagesEl.innerHTML = '';
        messagesEl.appendChild(fragment);
        messagesEl.querySelectorAll('.copy-link-btn').forEach(btn => {
          btn.addEventListener('click', (event) => {
            event.stopPropagation();
            const entryId = btn.dataset.entryId;
            const shareUrl = buildShareUrl(entryId);
            copyToClipboard(shareUrl, btn);
          });
        });
        messagesEl.querySelectorAll('.message-image').forEach(img => {
          img.addEventListener('click', () => openImageModal(img.src));
        });

        setTimeout(() => {
          const content = document.getElementById('content');
          if (scrollMode === 'bottom') {
            content.scrollTop = content.scrollHeight;
          } else if (scrollMode === 'target') {
            const scrollTargetId = scrollToEntryId || target;
            const targetEl = document.getElementById(getScrollTargetElementId(scrollTargetId)) ||
              document.getElementById(`entry-${scrollTargetId}`);
            if (targetEl) {
              targetEl.scrollIntoView({ block: 'center' });
              if (scrollToEntryId) {
                targetEl.classList.add('highlight');
                setTimeout(() => targetEl.classList.remove('highlight'), 2000);
              }
            }
          }
        }, 0);
      }

      function renderSession(targetId) {
        navigateTo(targetId, 'target');
      }

      if (leafId) {
        if (urlTargetId && byId.has(urlTargetId)) {
          navigateTo(leafId, 'target', urlTargetId);
        } else {
          navigateTo(leafId, 'none');
        }
      } else if (entries.length > 0) {
        navigateTo(entries[entries.length - 1].id, 'none');
      }
    })();
"""


def _render_entry(entry: dict[str, Any]) -> str:
    entry_type = str(entry.get("type", "entry"))
    if entry_type == "message":
        message = entry.get("message") or {}
        role = str(message.get("role", "message"))
        return _message_block(role, _message_text(message), role)
    if entry_type == "custom_message":
        return _message_block("custom", _content_text(entry.get("content")), "custom")
    if entry_type == "compaction":
        return _message_block("compaction", str(entry.get("summary") or ""), "compaction")
    if entry_type == "branch_summary":
        return _message_block("branchSummary", str(entry.get("summary") or ""), "branchSummary")
    return _message_block(entry_type, json.dumps(entry, indent=2, sort_keys=True), "custom")


def _message_block(role: str, text: str, css_class: str) -> str:
    return (
        f'        <section class="message {html.escape(css_class)}">'
        f'<div class="role">{html.escape(role)}</div>'
        f"{html.escape(text)}</section>"
    )


def _message_text(message: dict[str, Any]) -> str:
    role = message.get("role")
    if role == "bashExecution":
        return f"$ {message.get('command', '')}\n{message.get('output', '')}".rstrip()
    if role == "toolResult":
        return _content_text(message.get("content"))
    return _content_text(message.get("content"))


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            block_type = block.get("type")
            if block_type in ("text", "thinking"):
                parts.append(str(block.get("text") or block.get("thinking") or ""))
            elif block_type == "image":
                parts.append(f"[image: {block.get('mimeType') or block.get('mime_type') or 'image'}]")
            elif block_type == "toolCall":
                parts.append(f"-> {block.get('name')}({json.dumps(block.get('arguments', {}), sort_keys=True)})")
            else:
                parts.append(json.dumps(block, sort_keys=True))
        elif isinstance(block, TextContent):
            parts.append(block.text)
        elif isinstance(block, ThinkingContent):
            parts.append(block.thinking)
        elif isinstance(block, ImageContent):
            parts.append(f"[image: {block.mime_type}]")
        elif isinstance(block, ToolCall):
            parts.append(f"-> {block.name}({json.dumps(block.arguments, sort_keys=True)})")
        else:
            parts.append(str(block))
    return "\n".join(part for part in parts if part)
