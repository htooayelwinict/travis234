# Sub-project 6: ui-rendering Design

Date: 2026-06-19
Status: Implemented
Parent: `2026-06-19-appv22-pi-hermes-parity-decomposition.md`
Reference: `pi/packages/tui/src` + `pi/packages/coding-agent/src/modes/interactive`

## Goal

Port pi's `tui` rendering model and the coding-agent interactive components into
`appV2.2/appv22/tui/` by **actual porting** (no import of pi/hermes modules).

## Scope (mirrors pi/packages/tui/src)

| appv22 file | pi source | Contents |
|---|---|---|
| `tui/utils.py` | `utils.ts` | `strip_ansi`, `visible_width`, `truncate_to_width`, `wrap_text`. |
| `tui/component.py` | `tui.ts` (Component/Container) + `components/*` | `Component`, `Container`, `Text` (line cache), `Box`, `Spacer`, `CURSOR_MARKER`. |
| `tui/terminal.py` | `terminal.ts` | `Terminal` protocol, `FakeTerminal` (records writes, for tests), `ProcessTerminal`. |
| `tui/tui.py` | `tui.ts` (TUI core) | `TUI` differential renderer: render → diff `first_changed`/`last_changed` → rewrite only changed lines; full redraw on first render / width change. Emits minimal ANSI. |
| `tui/interactive.py` | `modes/interactive` components | `AssistantMessageComponent.update_content`, `ToolExecutionComponent.update_result`, `InteractiveRenderer.handle_event` (AgentEvent → components → `request_render`). |

## Parity notes

- `Component.render(width) -> list[str]` (one string per visual line), matching pi.
- Differential rendering matches pi's behavior: compute the new frame, find the
  first/last changed line vs the previous frame, and rewrite only that range
  (`\x1b[{n};1H` + `\x1b[2K` + line); full clear + redraw on first render or width
  change. `request_render()` returns a `RenderInfo` for testing/inspection.
- `Text` caches rendered lines keyed by `(text, width)` and busts on `set_text`/
  `invalidate`, like pi.
- `InteractiveRenderer` maps the `agent` package's `AgentEvent`s to components
  (assistant streaming via `message_update`, tool calls via `tool_execution_*`),
  the same bridge pi's interactive mode uses.

## Out of scope (YAGNI)

Kitty/iTerm2 image protocols, the full `Editor`, markdown rendering, overlays,
keybinding manager, autocomplete — none are needed for engine/render parity and
can be ported later if a concrete need arises.

## Tests (`tests/test_tui.py`, 9)

width utils; `Text` wrap + cache; `Container` concat; `TUI` full-then-diff (only
changed line rewritten); no-change empty diff; `InteractiveRenderer` renders a
streamed assistant message + a tool execution + its result.
