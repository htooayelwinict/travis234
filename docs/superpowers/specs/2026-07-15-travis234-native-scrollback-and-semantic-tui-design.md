# Travis234 Native Scrollback and Semantic TUI Design

**Status:** Implemented and verified

**Date:** 2026-07-15

**Scope:** TUI presentation and terminal behavior only

**Implementation authorization:** granted after the written-spec review gate
**Git operations:** not authorized

**Verification:** 1,612 Python tests and 20 npm launcher tests passed; parity checks, Python/npm package builds, installed-wheel PTY acceptance, and the release-container smoke check passed. The optional tmux variant was unavailable because tmux is not installed in the test environment.

## Goal

Replace Travis234's nonfunctional real-world transcript viewport with Pi-style native terminal scrollback and native text selection, then deliver a staged semantic-theme and UX refactor without changing the Agent, runtime, session, compaction, context, tool, or extension layers.

## Approved product choices

1. Native terminal scrollback is the default and only production transcript mode.
2. Normal terminal text selection, URL interaction, tmux scrollback, and touchpad/wheel behavior take priority over a pinned editor.
3. Agent impact is zero. No read-only Agent adapter is needed.
4. The work covers the full staged TUI improvement: scroll/selection, terminal compatibility, semantic themes, transcript presentation, Markdown, multiline input, and responsive chrome.
5. Fixed-editor/alternate-screen composition is not part of this implementation.
6. The initial theme pack is bold, original, complete, and externally Pi-compatible.

## Correction to the research draft

`/tmp/TRAVIS234_TUI_PI_ORACLE_RESEARCH_DRAFT.md` described Travis's explicit internal viewport as a strength. That conclusion is superseded by production behavior and a real PTY reproduction.

The code has internal scroll-offset methods and synthetic tests, but the user-facing interaction is contradictory:

- Outside sandbox mode, mouse tracking is disabled. Wheel/touchpad events remain terminal-owned and never reach the internal viewport.
- Inside sandbox mode, SGR mouse tracking is enabled. Wheel events may reach the application, but ordinary drag selection, URL click-through, and tmux/native scrollback are captured.
- The renderer emits only a terminal-height slice of logical history, so terminal-native scrollback does not contain a faithful transcript.
- The scroll handler recognizes only a narrow set of raw PageUp/PageDown/End sequences.
- Forced repaint, resize, and shutdown paths may emit `CSI 3J`, deleting terminal scrollback.

A real installed-entry-point PTY run confirmed that exact legacy `ESC [ 5 ~` and `ESC [ 6 ~` injections move the internal viewport. That proves the unit path exists; it does not satisfy ordinary wheel/touchpad or selection behavior.

The two reported symptoms—cannot scroll and cannot select text—are therefore one renderer/input ownership defect.

## Hard boundary

### Allowed production files

- `travis/tui/tui.py`
- `travis/tui/terminal.py`
- `travis/tui/keys.py`
- `travis/tui/stdin_buffer.py`
- `travis/tui/terminal_colors.py`
- `travis/tui/utils.py`
- `travis/tui/components/**`
- Presentation-only component classes in `travis/tui/interactive.py`
- Narrow theme construction and presentation wiring in `travis/tui/interactive_mode.py` and `travis/tui/interactive_view.py`
- New focused modules under `travis/tui/**`
- TUI-focused tests under `tests/test_tui*.py`
- TUI documentation and README usage notes

### Forbidden production files and behavior

- `travis/agent/**`
- `travis/ai/**`
- `travis/compaction/**`
- `travis/coding_agent/**`
- `travis/runtime_facade.py`
- `travis/app.py`
- `travis/tui/interactive_session_commands.py`
- `travis/tui/interactive_extensions.py`
- `travis/tui/interactive_turn_controller.py`
- Agent-loop ordering or iteration budgets
- Provider request construction or tool schemas
- Session entries, JSONL formats, compaction inputs, thresholds, or summaries
- Extension API shape, hooks, reload behavior, or context injection

Existing theme records, settings reads, selected registry state, message/tool view data, and extension-rendered component values may be consumed through their existing interfaces. They are read-only inputs to the presentation layer.

## Architecture decision

### Selected: converge the terminal renderer with current Pi behavior

Travis will render the complete logical component tree into the terminal's normal screen buffer. Appended lines advance the physical terminal and become real scrollback. Differential updates rewrite only the mutable visible tail.

The design does not copy the current Pi renderer blindly. It adopts its native-buffer ownership while preserving Travis-specific dispatcher, overlays, image handling, OSC 133 zones, ANSI/OSC width handling, and Python component model.

### Rejected: repair the internal viewport

Keeping a terminal-height slice cannot provide both ordinary wheel scrolling and ordinary native selection. Mouse tracking must be enabled for one and disabled for the other.

### Rejected: alternate-screen fixed editor

An alternate-screen compositor would require application-owned scrolling, selection, clipboard integration, URL handling, tmux behavior, crash cleanup, and resize recovery. It is a separate experimental product, not a production-safe correction.

## 1. Native scrollback renderer

### Render model

`TUI` retains the entire logical line list in `previous_lines`; it no longer stores only the visible terminal-height slice.

On first render:

1. Render every mounted component to logical lines.
2. Composite visible overlays.
3. Apply line-reset safety.
4. Write all lines without clearing the screen or scrollback.
5. Track logical cursor row, hardware cursor row, viewport top, width, and height.

On append-only growth:

1. Find the unchanged prefix.
2. Move from the current physical row to the append boundary.
3. Emit new lines with CRLF so the terminal records them in native history.
4. Rewrite only mutable trailing lines such as streaming assistant text, tool progress, editor, status, and footer.

On ordinary differential changes:

1. Reject an unsafe diff when its first changed logical line is above the physically addressable viewport.
2. Fall back to a screen repaint without `CSI 3J`.
3. Preserve already-recorded native scrollback even if historical wrapping reflects the old terminal width.

On resize:

- Width changes repaint the active screen but do not clear scrollback.
- Height changes recompute the active viewport and repaint only the screen.
- Termux height churn keeps its non-destructive special case.

On stop:

- Move below the last rendered content.
- Restore cursor, keyboard, bracketed-paste, and any explicitly enabled terminal modes.
- Never clear native scrollback.

### Removed viewport ownership

The following internal transcript behaviors are removed from the production path:

- `_scroll_offset_from_bottom`
- `scroll_by()` and `scroll_to_bottom()` transcript ownership
- global PageUp/PageDown/End interception for transcript history
- the `history - PageDown/End to latest` footer hint
- viewport slicing in `_do_render()`
- default or sandbox-implied mouse tracking

Compatibility methods may remain temporarily only if an existing public test or extension surface requires them. They must be no-ops or explicitly deprecated and cannot intercept ordinary input.

## 2. Terminal input and selection contract

`ProcessTerminal` never enables SGR/X10 mouse tracking by default, including when `TRAVIS234_SANDBOX=1`.

An explicit diagnostic-only opt-in may remain through `TRAVIS234_TUI_MOUSE=1`, but it is unsupported for ordinary use and must display/document the selection tradeoff. No theme or UX feature depends on it.

The terminal owns:

- wheel and touchpad scrolling
- drag selection
- clipboard copy of selected terminal text
- URL click-through
- terminal/tmux history navigation
- Shift+PageUp/PageDown or emulator-specific scrollback shortcuts

The application owns PageUp/PageDown only inside a focused multiline editor or list whose own content exceeds its visible bounds.

Travis broadens key parsing to current Pi-compatible functional sequences, including legacy double-bracket, modified CSI, and Kitty functional PageUp/PageDown/Home/End forms. Those keys are delivered to focused components, not hijacked for transcript scrolling.

## 3. Semantic theme system

### Modules

`travis/tui/theme.py` owns:

- the 51 required Pi semantic roles and optional `thinkingMax`
- immutable `ResolvedTheme`
- variable/color validation
- complete fallback layering
- truecolor and xterm-256 ANSI generation
- independent foreground/background resets
- `NO_COLOR` and `TERM=dumb` behavior

`travis/tui/builtin_themes.py` owns six complete original palettes as Python data so packaging needs no non-Python resource change.

`travis/tui/theme_controller.py` owns:

- startup resolution from the existing registry and persisted settings read
- active registry observation
- preview, restore, and commit-visible state
- last-valid active-file hot reload
- diagnostics
- monotonic generation for component-cache invalidation

No theme data is added to messages, prompts, sessions, tools, extensions, or provider requests.

### Color values

Supported theme values match practical Pi compatibility:

- strict `#RRGGBB`
- xterm index `0..255`
- empty string for terminal default
- variable references through `vars`

Missing variables, cycles, malformed hex, invalid index values, and wrong value types produce local diagnostics. A bad supplied role falls back to the complete base palette; rendering never crashes.

Partial legacy Travis themes remain valid by inheriting missing roles from Signal Glass.

### Built-in themes

1. **Signal Glass** — black, cybernetic mint, ice cyan, amber warnings
2. **Black Ice** — midnight navy, electric cyan, cold white, blue-violet depth
3. **Neon Oni** — near-black, ultraviolet, magenta, cyan, hot red
4. **Blood Circuit** — graphite, crimson, ember orange, steel highlights
5. **Reactor Gold** — carbon black, industrial gold, radioactive green
6. **Polar Ghost** — light surfaces, graphite text, cobalt/cyan accents

All palettes must distinguish success, warning, error, selection, user surface, tool states, diffs, and thinking levels. Color is reinforced with text/icon state.

The palettes are original. MIT/Apache oracle material may be adapted later only with attribution. GPL and unclear-license sources remain clean-room visual inspiration.

## 4. Theme lifecycle and preview

The existing `/theme`, extension `setTheme`, and `/reload` owners remain untouched.

The TUI observes `ThemeRegistry.active_name` and the active record/source fingerprint lazily. Existing selection paths therefore update the resolved theme on their next render without a command-layer modification.

The existing select overlay receives presentation-only behavior when `kind="theme"`:

1. Opening captures the original resolved theme.
2. Moving the selection previews a candidate without persistence.
3. Cancel restores the exact original generation and palette.
4. Confirm restores before returning; the untouched `/theme` handler remains the sole commit/persistence owner.
5. Preview never appends a session entry or invokes an Agent turn.

The preview renders representative real components, not swatches alone: user/assistant messages, thinking, tools, Markdown/code, diffs, editor, status, and footer.

## 5. Transcript visual language

The default density is compact and copy-friendly.

- User messages use a semantic surface or one optional accent rail, not an ASCII rectangle.
- Assistant prose remains low-chrome.
- Thinking is visually subordinate and uses ordered thinking roles.
- Tool pending, success, and error states combine semantic color with a stable label/icon.
- Bash, diffs, compaction, branch, skill, and custom messages are visually distinct without changing their data or behavior.
- Decorative glyphs disappear before important text at narrow widths.
- Unicode borders have an ASCII fallback.
- OSC 133 zones remain intact.
- Control characters are sanitized in one-line labels and metadata.

## 6. Markdown and code

Markdown improves incrementally without a new runtime dependency:

1. headings, emphasis, inline code, fenced code, quotes, bullets, ordered lists, and rules
2. OSC 8 links with visible URL fallback
3. stable partial-fence behavior during streaming
4. narrow-width tables with a plain stacked fallback
5. semantic diff lines

Code is plain-but-themed initially. A syntax lexer is not added unless a later dependency/design decision explicitly authorizes it.

ANSI state must not leak across lines or nested foreground/background roles.

## 7. Multiline editor

The current `Input` remains for search, passwords, small dialogs, and selectors.

A new main-prompt `Editor` is introduced only after scrollback, themes, and Markdown are stable. It preserves every current `Input` behavior before adding:

- newline-preserving paste
- visual-line wrapping
- vertical motion with sticky column
- multiline Home/End and page motion
- atomic bracketed-paste undo
- current history, word motion, kill/yank/yank-pop, and autocomplete behavior

Enter submits. Shift+Enter inserts a newline when the terminal can distinguish it; otherwise the documented fallback is Alt+Enter. No submission, queue, cancellation, or Agent-turn policy changes.

## 8. Responsive status and footer

Every existing Travis datum remains available: cwd, provider/model, thinking level, context estimate, cache values, cost/subscription marker, compaction count/state, branch, session name, process state, extension statuses, and warnings.

Segments have explicit priorities:

1. active error/warning and context pressure
2. working/idle state
3. model/provider and thinking
4. cwd/branch/session
5. cache/cost/compaction detail
6. decorative identity

Widths 20, 40, 80, 120, and 200 are tested. Critical state never disappears behind decoration. No native-scrollback hint is rendered.

## 9. Data flow and invariants

### Startup

1. Existing resource loading and theme discovery finish unchanged.
2. TUI registers its built-in presentation palettes locally.
3. TUI reads the existing persisted theme name.
4. `ThemeController` resolves the active record over Signal Glass.
5. Components receive a presentation-only theme provider.
6. `TUI` starts in the normal terminal buffer without mouse tracking.

### Render

1. Existing runtime data is mapped into existing TUI component state.
2. Components render strings using the active `ResolvedTheme`.
3. The renderer diffs complete logical lines.
4. Append-only lines enter native terminal scrollback.
5. Mutable tail changes are updated in place.

### Theme change

1. Existing command/extension path changes the registry.
2. Theme controller detects the registry/source fingerprint.
3. Controller resolves a new immutable theme and increments generation.
4. Theme-aware caches invalidate.
5. A TUI render updates presentation only.

### Zero-envelope invariant

Before and after any TUI/theme operation:

- serialized provider request is byte-for-byte identical
- app/session message count is identical
- session JSONL contains no theme or preview entry
- system prompt and tool schemas are identical
- compaction inputs and thresholds are identical
- extension hooks are not invoked by preview or hot reload

## 10. Error handling

- Invalid themes retain the last valid screen and show a local diagnostic.
- Unsupported terminal color capability degrades deterministically to 256-color or no-color.
- Renderer uncertainty falls back to a non-destructive screen repaint, never scrollback deletion.
- A component that emits an over-width line is truncated safely and diagnosed in tests; the TUI must not corrupt terminal state.
- Resize, suspend/resume, crash cleanup, tmux, SSH, Kitty, iTerm2, Ghostty, Apple Terminal, Windows Terminal, and Termux receive focused coverage proportional to available CI/local capability.
- Extension custom components continue rendering even if they do not consume the theme.

## 11. Delivery sequence

Each slice begins with a failing regression and ends in a releasable state.

1. **Native scrollback and selection recovery**
2. **Terminal key and resize compatibility**
3. **Semantic theme resolver and six built-ins**
4. **Persisted activation, cache invalidation, and preview**
5. **Transcript/tool/footer theming**
6. **Markdown and links**
7. **Multiline main editor**
8. **Responsive polish and release qualification**

Later slices do not block release of the scroll/selection correction.

## 12. Acceptance criteria

### Scrollback and selection

- A real installed-entry-point PTY creates more than one screen of output.
- Ordinary wheel/touchpad scroll reveals the complete earlier transcript.
- Ordinary drag selects visible text without Shift.
- Selected copied text contains content rather than mandatory box chrome.
- URLs remain clickable.
- tmux/native scrollback is not captured by Travis.
- Resize, force render, shutdown, and restart do not emit `CSI 3J` or erase prior terminal history.

### Themes

- All six built-ins visibly differ across every wired semantic role.
- Persisted theme is active on restart.
- Partial external theme inherits safely.
- Invalid value/reference/cycle does not crash.
- Preview cancel restores exactly; confirm persists exactly once through the existing owner.
- Truecolor, 256-color, no-color, and dumb-terminal snapshots are deterministic.

### Components

- User, assistant, thinking, error, abort, tool states, bash, diff, compaction, branch, skill, custom, picker, editor, status, and footer have semantic render coverage.
- Markdown streaming does not leak styles or flicker across partial fences.
- Multiline editor preserves all former single-line submission/history/editing behavior.
- Width and Unicode matrices pass.

### Boundary

- No forbidden production file changes.
- No Agent/session/context/compaction/extension behavior changes.
- Focused and full Python tests pass.
- npm launcher tests and package builds pass.
- Relevant container smoke and real user-side PTY scenarios pass.

## 13. Manual user-side TUI scenarios

1. Run the installed entry point in a normal terminal, emit `/help`, scroll with wheel/touchpad, select/copy text, and open a visible URL.
2. Repeat under the release container/sandbox environment and confirm mouse tracking is not enabled.
3. Generate a streaming answer with tools, scroll upward during streaming, select stable earlier text, and return using terminal-native controls.
4. Preview Signal Glass, Neon Oni, Blood Circuit, and Polar Ghost; cancel once, commit once, restart, and verify persistence.
5. Exercise Markdown, a multiline paste, autocomplete, resize, tmux, 256-color, and `NO_COLOR` while confirming the session/context envelope is unchanged.

## 14. Explicit non-goals

- No fixed editor
- No alternate screen
- No application-owned text selection or clipboard
- No Agent/runtime/session/context/compaction/tool/extension changes
- No theme prompt injection
- No browser Studio workspace
- No workflow/todo/review features from oracle repositories
- No syntax-highlighting dependency in this implementation
- No automatic modification of user theme files
- No Git operation without a separate explicit user request

## Recommendation

Implement the eight delivery slices in order. Release the native-scrollback correction as soon as its focused and repository-level gates pass, then build the semantic visual system on the corrected renderer. The expected model-token and session-envelope effect is exactly zero.
