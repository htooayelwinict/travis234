# Travis234 Native Scrollback and Semantic TUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Do not use subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Implemented and verified

**Goal:** Restore native terminal scrollback and text selection, then deliver a complete semantic theme system and staged Pi-quality TUI UX without changing any Agent-facing behavior.

**Architecture:** Replace the terminal-height internal viewport with a complete logical-line renderer that appends into the normal terminal buffer and differentially rewrites only the mutable tail. Layer a TUI-owned 51-role theme resolver/controller over existing discovered theme records, then migrate presentation components, Markdown, the main editor, and responsive chrome in independently testable slices.

**Tech Stack:** Python 3.13, pytest, ANSI/OSC terminal sequences, existing Travis TUI component framework and dispatcher, installed-entry-point PTY acceptance, npm launcher tests, Python package builds, Docker release smoke.

## Global Constraints

- Do not run any Git command or Git-backed tool operation. This includes status, diff, add, commit, push, checkout, reset, switch, merge, rebase, stash, and worktree.
- Do not spawn subagents.
- The repository root is the only active application tree.
- Product and CLI remain `Travis234` and `travis234`; Python imports remain `travis`.
- State remains under `~/.travis234`; add no alternate state path or migration alias.
- Modify no production file outside the explicit TUI allowlist in the approved design.
- Do not modify Agent, runtime, session, context, compaction, tool, provider, or extension behavior.
- Do not modify `travis/tui/interactive_session_commands.py`, `travis/tui/interactive_extensions.py`, or `travis/tui/interactive_turn_controller.py`.
- Preserve core loop ordering, iteration budgeting, source-ordered result persistence, and bounded parallel execution.
- Add a failing regression before each behavioral correction.
- Keep mouse tracking disabled by default, including release-container/sandbox execution.
- Keep fixed-editor and alternate-screen behavior out of scope.
- Before reporting completion, run focused and full Python tests, npm launcher tests, Python/npm package builds, parity checks, relevant container smoke, and real installed-entry-point PTY scenarios.
- Replace every normal commit step with a read/test/review checkpoint.

---

## File Responsibility Map

### New focused owners

- `travis/tui/theme.py`: semantic roles, color resolution, ANSI styling, capabilities, `ThemeContext`
- `travis/tui/builtin_themes.py`: six complete original built-in palette definitions
- `travis/tui/theme_controller.py`: registry observation, persisted startup, preview/restore, last-valid reload, generation
- `travis/tui/components/transcript.py`: presentation-only user/assistant/tool/summary panels when extraction from `interactive.py` is required
- `tests/test_tui_themes.py`: resolver, palette, controller, preview, and envelope-boundary tests

### Existing owners to converge

- `travis/tui/tui.py`: complete logical-line/native-scrollback renderer; overlay and image integration
- `travis/tui/terminal.py`: terminal-mode lifecycle; no implicit mouse tracking
- `travis/tui/keys.py`: Pi-compatible functional key decoding
- `travis/tui/components/base.py`: theme-aware, copy-friendly panel primitives
- `travis/tui/components/footer.py`: semantic status/footer and responsive segments
- `travis/tui/components/pickers.py`: semantic picker styling and selection-change callback
- `travis/tui/components/markdown.py`: streaming-safe semantic Markdown
- `travis/tui/components/editor.py`: preserve `Input`; add multiline `Editor`
- `travis/tui/components/__init__.py` and `travis/tui/__init__.py`: public TUI exports only
- `travis/tui/interactive.py`: presentation component classes only; no renderer event-order changes
- `travis/tui/interactive_mode.py`: built-in registration, persisted startup read, theme construction
- `travis/tui/interactive_view.py`: theme-kind preview overlay, theme propagation, removal of history-scroll hint
- `travis/tui/interactive_command_dispatcher.py`: switch only the main prompt from `Input` to `Editor`
- `tests/test_tui_terminal_and_input.py`: renderer/input/PTY-facing regressions
- `tests/test_tui_rendering_and_components.py`: component/Markdown/editor snapshots and behavior
- `README.md`: terminal interaction, themes, multiline keys, manual acceptance

---

### Task 1: Replace the sliced viewport with native terminal scrollback

**Files:**
- Modify: `tests/test_tui_terminal_and_input.py`
- Modify: `travis/tui/tui.py`
- Modify: `travis/tui/interactive_view.py:84-91,389-392`

**Interfaces:**
- Preserves: `TUI.request_render(force=False) -> RenderInfo | None`
- Preserves: `RenderInfo.lines` as the complete logical frame after this task
- Produces: complete `previous_lines`, logical `cursor_row`, `hardware_cursor_row`, and `previous_viewport_top`
- Removes from transcript ownership: `scroll_by`, `scroll_to_bottom`, `is_scrolled`, and global PageUp/PageDown/End consumption

- [x] **Step 1: Write failing complete-history and non-destructive-render tests**

Add tests with these exact contracts:

```python
def test_tui_native_scrollback_keeps_complete_logical_history() -> None:
    terminal = FakeTerminal(columns=40, rows=4)
    tui = TUI(terminal)
    for index in range(10):
        tui.add(Text(f"line {index}"))

    info = tui.request_render()

    assert info is not None
    assert info.lines == [f"line {index}" for index in range(10)]
    assert "\r\n".join(f"line {index}" for index in range(10)) in strip_ansi(terminal.output)


def test_tui_force_render_never_erases_native_scrollback() -> None:
    terminal = FakeTerminal(columns=40, rows=4)
    tui = TUI(terminal)
    tui.add(Text("history"))
    tui.request_render()
    terminal.writes.clear()

    tui.request_render(force=True)

    assert "\x1b[3J" not in terminal.output


def test_tui_append_only_growth_enters_terminal_history_with_crlf() -> None:
    terminal = FakeTerminal(columns=40, rows=4)
    tui = TUI(terminal)
    history = Container([Text("one"), Text("two"), Text("three"), Text("four")])
    tui.add(history)
    tui.request_render()
    terminal.writes.clear()

    history.add(Text("five"))
    tui.request_render()

    assert "\r\nfive" in strip_ansi(terminal.output)
    assert tui.previous_lines == ["one", "two", "three", "four", "five"]
```

Replace the former synthetic internal-scroll tests with native-history contracts rather than deleting coverage without replacement.

- [x] **Step 2: Run the red tests**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_tui_terminal_and_input.py::test_tui_native_scrollback_keeps_complete_logical_history \
  tests/test_tui_terminal_and_input.py::test_tui_force_render_never_erases_native_scrollback \
  tests/test_tui_terminal_and_input.py::test_tui_append_only_growth_enters_terminal_history_with_crlf
```

Expected: failures because the current renderer returns only the last four lines, force rendering includes `CSI 3J`, and append rendering uses viewport-addressed rewrites.

- [x] **Step 3: Port complete logical-line renderer state**

Replace viewport-offset state in `TUI.__init__()` with:

```python
self._cursor_row = 0
self._hardware_cursor_row = 0
self._previous_viewport_top = 0
self._last_height: int | None = None
```

In `_do_render()`, render and retain the full logical list:

```python
width = self.terminal.columns
height = max(1, self.terminal.rows)
new_lines = super().render(width)
if self._overlay_stack:
    new_lines = self._composite_overlays(new_lines, width, height)
new_lines = [
    line if is_image_line(line) else truncate_to_width(line, width)
    for line in new_lines
]
cursor_position = self._extract_cursor_position(new_lines, max(0, len(new_lines) - height))
```

Adapt the current Pi native-buffer algorithm into Python with these invariants:

- first render writes all lines without clearing;
- append-only growth reaches the append boundary and emits CRLF;
- diffs above the addressable viewport fall back to a screen-only repaint;
- screen-only repaint uses `CSI 2J` and `CSI H`, never `CSI 3J`;
- `previous_lines` always stores all logical lines;
- width/height changes update viewport bookkeeping without clearing scrollback;
- image deletion/placement and synchronized output wrappers remain intact.

Define separate constants:

```python
_CLEAR_VIEWPORT = "\x1b[2J\x1b[H"
_SYNC_BEGIN = "\x1b[?2026h"
_SYNC_END = "\x1b[?2026l"
```

Delete `_CLEAR_SCREEN` use from ordinary render and shutdown paths.

- [x] **Step 4: Remove transcript input interception and footer hint**

Remove `_handle_scroll_input()` from the global input path. Input listeners and the focused component receive PageUp/PageDown/Home/End normally.

Remove scroll-listener subscription and `_refresh_footer_history_hint()` calls from `InteractiveView`. Keep `FooterComponent.history_hint` temporarily only for extension/backward compatibility; normal Travis rendering leaves it `None`.

- [x] **Step 5: Run renderer and overlay/image regressions**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_tui_terminal_and_input.py -k \
  'render or scroll or viewport or overlay or image or cursor or resize or shrink'
```

Expected: all selected tests pass after obsolete internal-scroll expectations are replaced with native-buffer expectations.

- [x] **Step 6: Review checkpoint**

Read the complete `_do_render()`, full-render fallback, diff path, image changed-range path, and stop lifecycle. Confirm no `CSI 3J` remains in a normal TUI lifecycle and no internal transcript scroll offset remains authoritative.

---

### Task 2: Restore native selection and broaden terminal key compatibility

**Files:**
- Modify: `tests/test_tui_terminal_and_input.py`
- Modify: `travis/tui/terminal.py`
- Modify: `travis/tui/keys.py`
- Modify: `README.md`

**Interfaces:**
- Produces: `ProcessTerminal.mouse_tracking_enabled -> bool`
- Produces: functional-key parsing for legacy, modified CSI, double-bracket, and Kitty functional sequences
- Preserves: bracketed paste, raw input, signal handling, stdin buffering, and focused-component dispatch

- [x] **Step 1: Write failing sandbox-selection and key-variant tests**

```python
def test_process_terminal_never_enables_mouse_tracking_implicitly_in_sandbox(monkeypatch) -> None:
    class RecordingTerminal(ProcessTerminal):
        def __init__(self) -> None:
            self.writes: list[str] = []
            super().__init__(progress_keepalive_seconds=10)

        def write(self, data: str) -> None:
            self.writes.append(data)

    monkeypatch.setenv("TRAVIS234_SANDBOX", "1")
    monkeypatch.delenv("TRAVIS234_TUI_MOUSE", raising=False)
    terminal = RecordingTerminal()
    terminal.start(lambda _data: None, lambda: None)
    terminal.stop()

    assert "\x1b[?1000h\x1b[?1006h" not in terminal.writes


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        ("\x1b[[5~", "pageUp"),
        ("\x1b[[6~", "pageDown"),
        ("\x1b[57421u", "pageUp"),
        ("\x1b[57422u", "pageDown"),
        ("\x1b[5$", "shift+pageUp"),
        ("\x1b[6^", "ctrl+pageDown"),
    ],
)
def test_parse_key_supports_terminal_functional_variants(sequence: str, expected: str) -> None:
    assert parse_key(sequence) == expected
```

- [x] **Step 2: Run the red tests**

Expected: sandbox mouse tracking is currently enabled and several key variants return `None`.

- [x] **Step 3: Make mouse tracking explicit-only**

Replace `_mouse_tracking_enabled()` with:

```python
def _mouse_tracking_enabled() -> bool:
    return _env_flag_enabled("TRAVIS234_TUI_MOUSE")
```

Expose a read-only property for tests/diagnostics:

```python
@property
def mouse_tracking_enabled(self) -> bool:
    return self._mouse_tracking_enabled
```

Do not remove explicit opt-in cleanup.

- [x] **Step 4: Port focused functional-key mappings**

Add only the current Pi mappings needed for PageUp/PageDown/Home/End and modifier parsing. Do not replace the complete key module in one step. Normalize all supported sequences through `parse_key()` and keep `matches_key()` as the single comparison authority.

- [x] **Step 5: Run focused input tests**

```bash
.venv/bin/python -m pytest -q tests/test_tui_terminal_and_input.py -k \
  'mouse_tracking or parse_key or key_release or stdin_buffer or terminal_input'
```

- [x] **Step 6: Document native terminal ownership**

Document that wheel/touchpad, drag selection, URLs, and terminal/tmux history are terminal-owned; Shift+PageUp/PageDown follows terminal configuration; `TRAVIS234_TUI_MOUSE=1` is diagnostic and disables normal selection in many terminals.

---

### Task 3: Add the semantic resolver and six built-in themes

**Files:**
- Create: `travis/tui/theme.py`
- Create: `travis/tui/builtin_themes.py`
- Create: `tests/test_tui_themes.py`
- Modify: `travis/tui/__init__.py`

**Interfaces:**
- Produces: `REQUIRED_THEME_ROLES: tuple[str, ...]`
- Produces: `ResolvedTheme`
- Produces: `ThemeDiagnostic`
- Produces: `ThemeContext`
- Produces: `resolve_theme(name, colors, variables, *, color_mode, fallback) -> tuple[ResolvedTheme, tuple[ThemeDiagnostic, ...]]`
- Produces: `BUILTIN_THEMES: Mapping[str, Mapping[str, object]]`

- [x] **Step 1: Write failing resolver contracts**

Cover exact required roles, missing-role inheritance, variable chains, missing references, cycles, strict hex, xterm bounds, truecolor/256/no-color output, independent resets, immutable maps, and all six complete palettes.

Representative tests:

```python
def test_all_builtin_themes_resolve_every_semantic_role() -> None:
    for name, definition in BUILTIN_THEMES.items():
        theme, diagnostics = resolve_theme(
            name,
            definition["colors"],
            definition.get("vars", {}),
            color_mode="truecolor",
            fallback=None,
        )
        assert diagnostics == ()
        assert set(REQUIRED_THEME_ROLES) <= set(theme.colors)


def test_partial_theme_inherits_and_invalid_role_falls_back() -> None:
    fallback, _ = resolve_builtin_theme("Signal Glass", color_mode="truecolor")
    theme, diagnostics = resolve_theme(
        "partial",
        {"accent": "#ff00ff", "error": "not-a-color"},
        {},
        color_mode="truecolor",
        fallback=fallback,
    )
    assert theme.colors["accent"] != fallback.colors["accent"]
    assert theme.colors["error"] == fallback.colors["error"]
    assert any(item.role == "error" for item in diagnostics)


def test_theme_foreground_and_background_reset_independently() -> None:
    theme, _ = resolve_builtin_theme("Signal Glass", color_mode="truecolor")
    assert theme.fg("accent", "x").endswith("\x1b[39m")
    assert theme.bg("userMessage", "x").endswith("\x1b[49m")
```

- [x] **Step 2: Run tests and verify missing modules fail collection**

- [x] **Step 3: Implement resolver types and color conversion**

Use frozen dataclasses and immutable proxy maps. `ResolvedTheme` exposes:

```python
@dataclass(frozen=True)
class ResolvedTheme:
    name: str
    colors: Mapping[str, str]
    foreground_ansi: Mapping[str, str]
    background_ansi: Mapping[str, str]
    color_mode: str

    def fg(self, role: str, text: str) -> str: ...
    def bg(self, role: str, text: str) -> str: ...
    def bold(self, text: str) -> str: ...
    def italic(self, text: str) -> str: ...
    def underline(self, text: str) -> str: ...
    def strikethrough(self, text: str) -> str: ...
```

Resolve variables recursively with an active-stack cycle check. Six-digit hex maps to `38;2;r;g;b` and `48;2;r;g;b`; xterm indices map to `38;5;n` and `48;5;n`. No-color returns plain text.

- [x] **Step 4: Add six complete original palettes**

Define Signal Glass, Black Ice, Neon Oni, Blood Circuit, Reactor Gold, and Polar Ghost with every required role. Use shared variables inside each definition but no cross-theme inheritance.

- [x] **Step 5: Run theme tests**

```bash
.venv/bin/python -m pytest -q tests/test_tui_themes.py
```

- [x] **Step 6: Review checkpoint**

Verify role count, all palettes, no external-license code, no filesystem writes, and no imports from Agent/session/context/compaction modules.

---

### Task 4: Activate persisted themes and add transactional preview

**Files:**
- Create: `travis/tui/theme_controller.py`
- Modify: `travis/tui/interactive_mode.py:90-180`
- Modify: `travis/tui/interactive_view.py:573-647`
- Modify: `travis/tui/components/pickers.py:235-end`
- Modify: `tests/test_tui_themes.py`
- Modify: `tests/test_tui_terminal_and_input.py`

**Interfaces:**
- Produces: `ThemeController(registry, settings_reader, context, request_render)`
- Produces: `ThemeController.sync()`, `preview(name)`, `restore_preview()`, `commit_preview_result()`
- Produces: `SelectList.on_selection_change`
- Preserves: existing `/theme`, extension `setTheme`, and `/reload` owners unchanged

- [x] **Step 1: Write failing persisted-startup and preview tests**

Tests must prove:

- persisted name is selected after built-ins/discovered themes register;
- missing persisted name falls back locally without writing settings;
- registry changes from existing paths are observed on `sync()`;
- preview changes `ThemeContext` but not registry/settings/messages;
- cancel restores exact original theme/generation;
- confirm returns the selected name after restoring, leaving persistence to the existing handler;
- source-file invalid content retains last valid theme.

- [x] **Step 2: Run red tests**

- [x] **Step 3: Implement `ThemeContext` generation and controller sync**

Controller fingerprint includes active name, source path, file stat signature when available, colors, and vars. `sync()` resolves only when the fingerprint changes. Diagnostics are presentation data, not transcript/session entries.

- [x] **Step 4: Register built-ins and apply persisted setting in TUI construction**

Construct synthetic `Theme` records inside `interactive_mode.py` using existing `Theme` and `SourceInfo` types. Register Signal Glass first, then other built-ins, then discovered themes. Read `settings_manager.get_theme()` and select when valid before creating themed components.

Do not call `set_theme()` during startup.

- [x] **Step 5: Add real theme-kind overlay preview**

Add `on_selection_change` to `SelectList`. In `prompt_extension_select()`, use a `SelectList` overlay for `kind="theme"`. Preview on movement; always restore before returning; let the untouched caller select/persist the returned name.

- [x] **Step 6: Run theme lifecycle and existing command/extension tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_tui_themes.py \
  tests/test_tui_commands_and_extensions.py -k 'theme or reload' \
  tests/test_tui_terminal_and_input.py -k 'select or overlay'
```

---

### Task 5: Theme transcript, tools, pickers, status, and footer

**Files:**
- Modify: `travis/tui/components/base.py`
- Modify: `travis/tui/components/footer.py`
- Modify: `travis/tui/components/pickers.py`
- Modify: presentation classes in `travis/tui/interactive.py`
- Optionally create: `travis/tui/components/transcript.py`
- Modify: `travis/tui/interactive_view.py`
- Modify: `tests/test_tui_rendering_and_components.py`
- Modify: `tests/test_tui_themes.py`

**Interfaces:**
- Produces: theme-aware `Panel` or extended `Box` with ASCII fallback
- Produces: theme-generation-aware component cache keys
- Preserves: existing component constructor defaults and extension custom components

- [x] **Step 1: Write failing opposite-theme snapshots**

For Signal Glass and Blood Circuit, assert different ANSI output for user, assistant, thinking, error, abort, tool pending/success/error, bash, diff, compaction, branch, skill, custom message, picker, editor border, status, and footer.

Add plain-text assertions after `strip_ansi()` to prove semantic theming never changes content.

- [x] **Step 2: Run red snapshots**

- [x] **Step 3: Add copy-friendly semantic panel primitives**

Add optional border role, background role, title role, padding, Unicode/ASCII style, and accent-rail mode. Defaults preserve existing callers until migrated.

- [x] **Step 4: Remove hard-coded Signal Glass ANSI from footer**

Map status kinds and responsive footer segments to semantic roles. Preserve every existing value and calculation. Remove ordinary `history_hint` output.

- [x] **Step 5: Migrate presentation components only**

Theme existing states without changing event mapping, tool renderer invocation, data mutation, message storage, or ordering. If `interactive.py` becomes unsafe to edit, extract only presentation classes into `components/transcript.py` and leave `InteractiveRenderer` logic untouched.

- [x] **Step 6: Run component, runtime-boundary, and ANSI-reset tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_tui_themes.py \
  tests/test_tui_rendering_and_components.py \
  tests/test_tui_runtime_compaction_and_models.py
```

---

### Task 6: Replace marker stripping with streaming-safe semantic Markdown

**Files:**
- Modify: `travis/tui/components/markdown.py`
- Modify: `tests/test_tui_rendering_and_components.py`
- Modify: `tests/test_tui_themes.py`

**Interfaces:**
- Preserves: `Markdown(text="").render(width) -> list[str]`
- Adds optional: `theme_context`
- Produces: stable block parser for headings, emphasis, inline/fenced code, quotes, lists, rules, links, and narrow tables

- [x] **Step 1: Write failing block, streaming, width, link, and style-leak tests**

Include empty/partial fences, nested emphasis, ordered/unordered lists, blockquotes, long URLs, OSC 8 enabled/disabled, 20-column tables, CJK/emoji, and independent line resets.

- [x] **Step 2: Run red Markdown tests**

- [x] **Step 3: Implement a bounded block parser without a dependency**

Parse blocks in one pass, render inline tokens with semantic roles, and keep incomplete streaming fences stable. Tables fall back to stacked key/value rows when columns cannot fit.

- [x] **Step 4: Run focused and transcript rendering tests**

```bash
.venv/bin/python -m pytest -q tests/test_tui_rendering_and_components.py -k 'markdown or assistant or user'
```

---

### Task 7: Add a multiline main editor without changing turn policy

**Files:**
- Modify: `travis/tui/components/editor.py`
- Modify: `travis/tui/components/__init__.py`
- Modify: `travis/tui/__init__.py`
- Modify: `travis/tui/interactive_command_dispatcher.py:150-190`
- Modify: `travis/tui/interactive_view.py:620-647` only if dialog input typing requires it
- Modify: `tests/test_tui_rendering_and_components.py`
- Modify: `tests/test_tui_terminal_and_input.py`

**Interfaces:**
- Preserves: `Input` for dialogs and selectors
- Produces: `Editor(value="", prompt="", on_submit=None, max_visible_lines=8, theme_context=None)`
- Preserves: main prompt submit callback and queue/turn policy

- [x] **Step 1: Write a side-by-side parity suite**

Run identical sequences through `Input` and `Editor` for typing, word motion, history, kill/yank/yank-pop, undo, autocomplete, mask-independent behavior, Ctrl-C/Escape handling, submit, and wide graphemes.

Add multiline-only tests for newline paste, Shift+Enter/Alt+Enter, sticky vertical column, visual wrapping, Home/End, page motion, atomic paste undo, and cursor marker placement.

- [x] **Step 2: Run red editor tests**

- [x] **Step 3: Add `Editor` by composing existing editing owners**

Reuse current kill ring, undo stack, word navigation, autocomplete, and cursor utilities. Do not fork those behaviors. Store logical lines plus cursor line/column; derive visual rows only during render.

- [x] **Step 4: Switch only the main prompt**

Change the main interactive prompt construction to `Editor`. Keep `_prompt_tui_value()` and picker/search/password inputs on `Input`.

- [x] **Step 5: Run editor, terminal input, command, and cancellation suites**

```bash
.venv/bin/python -m pytest -q \
  tests/test_tui_rendering_and_components.py -k 'input or editor or autocomplete' \
  tests/test_tui_terminal_and_input.py -k 'input or submit or ctrl_c or paste' \
  tests/test_tui_commands_and_extensions.py
```

---

### Task 8: Responsive polish, boundary proof, documentation, and release verification

**Files:**
- Modify: `travis/tui/components/footer.py`
- Modify: `tests/test_tui_rendering_and_components.py`
- Modify: `tests/test_tui_themes.py`
- Modify: `README.md`
- Modify only if an existing smoke owner supports it: `evals/container_smoke.py`
- Modify only if required by that smoke: `tests/test_release_workflow.py`

**Interfaces:**
- Produces: deterministic segment-priority layout at 20/40/80/120/200 columns
- Produces: executable zero-envelope and forbidden-import/file-boundary evidence

- [x] **Step 1: Write failing width-priority and boundary tests**

Assert critical warning/context state survives narrow widths, every line fits, decoration drops first, and theme operations do not change fake serialized request data, message count, session entries, compaction values, or extension-hook counters.

- [x] **Step 2: Implement deterministic responsive segment omission**

Build footer segments as `(priority, minimum_width, text, role)` records. Add in priority order and omit low-priority segments before truncating critical ones.

- [x] **Step 3: Update README**

Document native scrollback/selection, terminal ownership, theme list and JSON compatibility, `/theme` preview/commit behavior, multiline keys, no-color behavior, and an exact five-scenario installed-entry-point TUI acceptance protocol.

- [x] **Step 4: Run focused TUI suite**

```bash
.venv/bin/python -m pytest -q \
  tests/test_tui_themes.py \
  tests/test_tui_terminal_and_input.py \
  tests/test_tui_rendering_and_components.py \
  tests/test_tui_commands_and_extensions.py \
  tests/test_tui_dispatcher.py \
  tests/test_tui_runtime_compaction_and_models.py \
  tests/test_tui_user_commands.py
```

- [x] **Step 5: Run repository-level verification**

```bash
.venv/bin/python -m pytest -q
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
.venv/bin/python -m build
.venv/bin/python scripts/verify_acceptance.py --parity-json
```

Expected: all commands exit zero.

- [x] **Step 6: Run relevant container smoke**

Use the repository's documented no-cache release-image build/smoke command. Confirm the installed `travis234` entry point starts with no implicit mouse tracking and exits cleanly. Do not expose `.env` or credentials in output.

- [x] **Step 7: Run five real user-side PTY scenarios**

Use the installed console entry point in an attached PTY, isolated state under `/tmp`, and the README protocol. Verify native wheel/touchpad scroll and drag selection manually, themes, streaming/tool output, multiline input, resize, tmux/256/no-color variants, and clean exit.

- [x] **Step 8: Final review checkpoint**

Inspect every touched production path directly without Git. Confirm the forbidden file list was not modified, no credential/state path changed, no Agent/session/context/compaction/extension behavior was added, no placeholder remains, and all verification evidence is fresh.

---

## Execution order and stop conditions

- Execute tasks strictly in order.
- If Task 1 cannot preserve overlays/images while delivering native history after three evidence-based attempts, stop and revisit the renderer architecture before proceeding.
- A later task may not weaken native selection or re-enable default mouse tracking.
- A failing repository-level test must be diagnosed before proceeding; do not classify it as unrelated without source evidence.
- If full theme/component/editor work cannot be completed safely in one release, the native-scrollback slice remains independently releasable and later tasks remain tracked in this plan.
