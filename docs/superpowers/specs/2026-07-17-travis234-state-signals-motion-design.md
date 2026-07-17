# Travis234 State Signals Motion Design

**Status:** Approved for implementation

**Date:** 2026-07-17

**Scope:** TUI presentation behavior only

## Goal

Add restrained animation to Travis234 without turning the coding interface into a dashboard and without changing Agent behavior, context construction, compaction, provider requests, tools, session persistence, or extension execution.

Motion must answer one question: **what is Travis doing now?** It must not exist merely as decoration.

## Approved product direction

1. Preserve the current compact, transcript-first layout.
2. Do not add frames, sidebars, dashboards, permanent gauges, or core system-monitor widgets.
3. Use the **State Signals** motion direction.
4. Idle is completely static.
5. At most one glyph may animate across the entire TUI.
6. The existing footer remains the only telemetry surface.
7. Provider, model, context, cache, cost, compaction, branch, and session data remain as they are.
8. CPU and memory are not sampled by Travis core. Extensions may publish compact static values through the existing footer status surface.
9. Extensions publish text and semantic working state; Travis owns animation frames, timing, color, priority, and cleanup.
10. Subtle motion is enabled by default, with a session-local off switch and static terminal fallbacks.

## Hard boundary

### Allowed production scope

- `travis/tui/dispatcher.py`
- `travis/tui/components/footer.py`
- `travis/tui/interactive_mode.py`
- Focused lifecycle wiring in `travis/tui/interactive_*.py`
- A new focused module under `travis/tui/`
- TUI exports, tests, and README usage documentation

### Forbidden behavior

- Changes under `travis/agent/**`
- Changes to provider payload construction or response handling
- Changes to compaction algorithms, thresholds, summaries, or context envelopes
- Changes to message, tool, session, or JSONL schemas
- Changes to agent-loop ordering, iteration budgets, or parallel execution
- Animation state entering prompts, messages, events persisted to sessions, or extension context
- A new system-monitor dependency or background resource sampler
- Animation of historical transcript rows
- Multiple component-owned timers
- Forced full-screen redraws for animation

The implementation may consume existing TUI-visible runtime state and events as read-only inputs.

## Visual behavior

The existing `StatusLine` remains the single motion surface. A semantic state selects a small built-in frame sequence displayed after the existing status text. Motion never shifts the label horizontally.

### State priority

From highest to lowest:

1. error, abort, exit, or retry
2. compaction, reload, or package operation
3. active tool execution
4. provider streaming or thinking
5. extension-reported working state
6. idle

Only the winning state is visible. Parallel tools never create parallel spinners.

### Motion profiles

- **Thinking/streaming:** the activity row reads `Thinking...`; three fixed suffix dots remain visible while one highlighted dot travels across them at four frames per second. The sequence is never rendered as a growing `.`/`..`/`...` prefix.
- **Tool activity:** one fixed-width suffix spinner cell at four frames per second.
- **Maintenance:** one fixed-width suffix pulse beside the dedicated operation label.
- **Retry:** a once-per-second suffix countdown supplied by the existing retry state.
- **Success or error:** one short suffix transition, followed by a static terminal glyph.
- **Idle:** a static label with no scheduled callback.

Theme semantic roles determine color. Motion does not introduce a separate palette. In `NO_COLOR`, `TERM=dumb`, disabled-motion mode, or any unsupported terminal condition, the same state remains readable through static text and glyphs.

## Architecture

### `UiDispatcher` scheduling

Add a small owner-thread scheduled-callback facility to the existing dispatcher:

- `call_later(delay, callback)` returns a cancellable handle.
- Scheduled work is ordered by deadline and insertion order.
- `time_until_next_work()` includes the nearest scheduled deadline.
- `drain()` executes due callbacks on the TUI owner thread before rendering.
- Cancellation is idempotent.
- Exceptions cannot leave dispatcher bookkeeping or the render request state corrupted.

This avoids animation threads, repeated `threading.Timer` creation, cross-thread component mutation, and render storms.

### `MotionController`

Create `travis/tui/motion.py` with:

- a closed set of semantic motion states
- priority metadata and immutable built-in frame profiles
- enabled/static-terminal policy
- current frame and generation state
- activation, replacement, cancellation, and shutdown lifecycle
- an injected clock/scheduler for deterministic tests

The controller schedules only its next required frame. Changing to an equivalent state does not restart the sequence. Changing to idle or disabled mode cancels outstanding work immediately.

### `StatusLine`

`StatusLine` keeps ownership of its existing message and semantic color. It receives the controller-selected indicator through a narrow presentation interface. A frame change invalidates only the status component and requests an ordinary differential render.

Historical tool and transcript rows settle to static text. They never retain animation ownership.

### Lifecycle wiring

TUI lifecycle handlers map existing runtime-visible events to semantic states explicitly. They do not infer state by parsing display strings.

The main mappings are:

- turn/provider active → working
- pending tool activity → tool
- auto retry → retry
- manual or automatic compaction → compacting
- reload/package operations → maintenance
- abort/shutdown → terminating
- settled turn → idle

The controller is stopped before terminal restoration and is rebound safely when a session changes.

## Extensions and telemetry

No new dashboard or widget registry is required.

- Existing `ui.setStatus(key, text)` remains the static footer telemetry path.
- A system-monitor extension may publish a compact value such as `CPU 42% · RAM 61%`.
- Extension status values remain subject to the footer's existing width-aware truncation.
- Frequent telemetry changes use normal dispatcher coalescing; extension authors should update system values no more than once per second.
- Existing working-message and working-indicator calls map into the shared semantic working state.
- Extension-provided frame arrays remain accepted for compatibility but cannot start independent animation loops. Travis selects the animated profile; a supplied first frame may remain the static fallback.
- Core states always outrank extension working state.

## Controls

- Motion is enabled by default.
- `/motion on` enables it for the current TUI process.
- `/motion off` disables it for the current TUI process and immediately cancels scheduled frames.
- `/motion` reports the current state and usage without invoking a model turn.
- `TRAVIS234_MOTION=0` starts the TUI with motion disabled.
- `NO_COLOR=1` and `TERM=dumb` select static presentation automatically.

The control is intentionally session-local. No settings-manager or user-state schema change is required.

## Performance and safety invariants

- No scheduled animation work exists while idle.
- Active motion is capped at four frames per second; retry countdowns are capped at one update per second.
- There is never more than one outstanding motion callback.
- Animation requests never use forced rendering.
- The existing dispatcher coalesces animation with streaming, input, and tool renders.
- Frame changes do not invalidate Markdown, history, editor, or footer caches unnecessarily.
- Resize, overlay, theme preview, session rebound, abort, and shutdown cannot leak scheduled work.
- Motion cannot affect token counts, context estimates, compaction baselines, or provider usage data.

## Error handling

- Invalid extension indicator values fall back to the built-in static glyph.
- An unavailable color capability changes presentation to static; it does not fail startup.
- Disabling or stopping the controller is idempotent.
- A late scheduled callback checks controller generation and becomes a no-op after cancellation or state replacement.
- Callback failures are isolated to the presentation path and cannot terminate the agent turn.

## Testing strategy

Regression tests are written before production changes.

### Dispatcher tests

- deadline and insertion ordering
- cancellation and idempotent cancellation
- `time_until_next_work()` deadline calculation
- owner-thread execution
- scheduled callbacks requesting coalesced renders
- reentrant scheduling without render recursion

### Motion tests

- idle schedules nothing
- active state advances at four frames per second
- retry uses one-second cadence
- equivalent state does not restart
- priority replacement and stale-callback suppression
- disable, static terminal, and shutdown cancellation
- one outstanding callback invariant

### TUI integration tests

- one animated glyph maximum
- lifecycle state mappings
- extension working state cannot outrank core activity
- extension footer telemetry remains static
- `/motion` is local and never starts an agent turn
- `NO_COLOR`, `TERM=dumb`, and `TRAVIS234_MOTION=0`
- native scrollback remains intact under repeated animation renders
- themes color the signal through semantic roles
- motion leaves messages, context usage, and compaction state unchanged
- imports in motion core do not reach agent, provider, or compaction modules

### Repository verification

Before completion, run focused TUI tests, the complete Python suite, npm launcher tests, Python and npm package builds, installed-wheel acceptance, and the relevant release-container smoke test described in the repository README.

## Non-goals

- A JARVIS-style dashboard
- Persistent panels or alternate-screen composition
- Animated banners, borders, backgrounds, Markdown, tool history, or footer metrics
- Core CPU, memory, disk, network, temperature, or process monitoring
- Extension-defined frame rates or animation engines
- Persisted motion settings
- Agent or context-envelope changes
