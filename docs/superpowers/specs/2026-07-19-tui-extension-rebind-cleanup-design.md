# TUI Extension Rebind Cleanup Design

## Problem

An interactive extension can register a raw terminal-input listener whose closure
contains a generation-scoped extension context. During session replacement,
Travis234 invalidates the old extension runner and binds the replacement runner,
but the TUI retains the old listener. The next keypress invokes the stale closure
and raises the intentional stale-context `RuntimeError`.

Normal `/reload` already avoids this failure by resetting extension UI before the
new runtime starts. Session replacement must provide the same ownership boundary
without changing the agent loop, session persistence, providers, compaction, or
context-envelope construction.

## Considered Approaches

### 1. TUI-only pre-rebind cleanup hook (selected)

Extend `ExtensionHostAdapter` with an optional callback that runs immediately
before a replacement session is bound. Interactive mode supplies its existing
`_reset_extension_ui` method. The adapter retains its established post-bind
`on_rebound` callback for history, footer, and session-subscription refresh.

This keeps lifecycle ordering in the host adapter, affects only hosts that opt in,
and reuses the same complete UI reset already exercised by `/reload`.

### 2. Application/runtime pre-invalidation subscription (rejected)

Adding another callback layer to `CodingApp` or `AgentSessionRuntime` could match
Pi's exact placement, but it expands the change into session-runtime composition
and conflicts with Travis234's existing single pre-invalidation owner. That scope
is unnecessary for this TUI-host regression.

### 3. AniFooter-only unsubscribe management (rejected as primary)

Storing and invoking AniFooter's unsubscribe handle is useful defense in depth,
but it does not uphold host ownership for other extensions. The runtime must not
retain callbacks owned by an invalidated session.

## Required Ordering

For initial startup, behavior remains unchanged: bind the initial session only.

For every replacement:

1. Run the optional TUI cleanup callback exactly once.
2. Bind the replacement session and emit its deferred `session_start` through the
   existing session binding path.
3. Run the existing post-bind rebound callback exactly once.

Cleanup must never run after replacement binding because that would delete the
new session's freshly registered listeners, statuses, widgets, and footer state.

## Error and Host Boundaries

The hook is optional. Print, JSON, RPC, and embedding hosts retain current
behavior unless they explicitly supply it. Cleanup exceptions propagate before
binding, preserving the existing fail-closed lifecycle rather than continuing
with partially reset UI state.

No stale-context guard is weakened and no extension exception is suppressed.

## Tests

- An adapter-ordering regression records `before`, `bind`, and `after` and proves
  the exact sequence for a replacement while proving startup does not call the
  replacement-only hook.
- A real interactive regression loads a project extension that registers a
  terminal listener during `session_start`, replaces the session, and proves the
  old listener is absent, exactly one fresh listener remains, and terminal input
  reaches only the fresh context without raising.
- Existing reload tests continue to prove `/reload` cleanup behavior.

## Scope Guard

Production changes are limited to:

- `travis/coding_agent/extension_host.py`
- `travis/tui/interactive_mode.py`

Regression changes are limited to:

- `tests/test_extension_host_runtime.py`

No Git commit, push, package publication, or unrelated refactor is part of this
implementation.
