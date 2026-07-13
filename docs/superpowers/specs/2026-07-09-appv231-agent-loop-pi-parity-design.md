# appv231 Agent Loop Pi Parity Design

Date: 2026-07-09
Status: Approved direction for planning

## Goal

Restore `appV2.3.1/appv231/agent/*` as a strict Python port of the local Pi agent package while keeping appv231 product safety behavior explicit at the composition boundary. The authoritative parity reference is the local `pi/packages/agent/src` tree, especially:

- `pi/packages/agent/src/agent-loop.ts`
- `pi/packages/agent/src/agent.ts`
- `pi/packages/agent/src/types.ts`

The redzone is `appV2.3.1/appv231/compaction/`. This design requires no edits there.

## Non-Negotiable Constraints

- `appv231/agent` must not drift from Pi agent-loop behavior unless a Python runtime difference makes an exact structural port impossible.
- appv231-specific safety policy must not be hidden in the Pi-port core.
- Do not edit `appV2.3.1/appv231/compaction/`.
- Fixes must be covered by focused regression tests for the proven faults.
- Existing tests that encode non-Pi core behavior may be changed, moved, or replaced when they contradict Pi parity.
- Do not commit, push, open PRs, branch, or perform any gitops unless Lewis explicitly asks for that later.

## Proven Faults

The following faults were reproduced against the current worktree:

1. Final-message tool-call dedupe drops duplicate tool calls by name and args while streamed TUI components for dropped calls remain unfinished.
2. `agent_loop_continue()` can strand consumers when the worker thread raises before emitting `agent_end`.
3. Parallel `tool_execution_end` events are emitted from Python worker threads into unsynchronized Agent/TUI listener state.
4. Bash mutation detection misses attached redirects and absolute mutators such as `echo hi >file` and `/bin/rm file`.
5. `prepare_next_turn` has a split contract: low-level loop receives turn context, high-level `Agent` receives abort signal. Pi exposes both contracts explicitly.

## Architecture

### Pi Parity Core

`appV2.3.1/appv231/agent/*` is the Pi parity layer. It should own only the generic agent runtime:

- lifecycle events
- assistant streaming
- tool-call execution
- steering and follow-up queues
- callback contracts that match Pi
- state updates equivalent to Pi's `Agent`

This layer should not implement appv231 product policy such as duplicate bash suppression, workspace guardrails, package-manager consent, process-limit control, or model/tool catalog policy.

### Appv231 Policy Layer

`appV2.3.1/appv231/coding_agent/agent_session.py`, tool definitions, and guardrail modules own appv231 behavior:

- tool-loop guardrails
- active tool refresh after a turn
- model and thinking-level refresh after a turn
- package-manager consent checks
- workspace scope checks
- process-limit controlled halt responses
- any optional duplicate-suppression policy that appv231 still needs

`appV2.3.1/appv231/tui/*` should render the events emitted by the parity core and policy layer. It should not rely on hidden core dedupe to clean up streamed tool rows.

## Design Decisions

### Remove Core Tool-Call Dedupe

`agent_loop.py` should not deduplicate assistant tool calls. Pi's `streamAssistantResponse()` does not remove duplicate tool calls from the final assistant message. If a provider emits two distinct calls with different ids, the core loop should treat them as distinct calls.

If appv231 needs duplicate bash-call suppression, it must live above the parity core as explicit appv231 policy. That policy can block a call through `before_tool_call`, transform a provider stream before it reaches the core, or mark a tool definition sequential when ordering matters.

### Port Pi Callback Shape

Add Pi's explicit high-level callback split:

- `prepare_next_turn(signal)` for signal-only compatibility.
- `prepare_next_turn_with_context(context, signal)` for context-aware behavior.

The low-level loop config remains context-based, matching Pi's `AgentLoopConfig.prepareNextTurn(context)`. The high-level `Agent` adapter decides whether to call `prepare_next_turn_with_context(context, signal)` or `prepare_next_turn(signal)`.

`AgentSession._prepare_next_turn()` should move to the context-aware callback path because it refreshes runtime context, model, tools, and thinking state after tool execution.

### Restore Pi Tool Dispatch Semantics

Pi chooses sequential execution when:

- `toolExecution` is `"sequential"`, or
- any requested tool has `executionMode` set to `"sequential"`.

The current appv231 `should_parallelize_tool_batch()` safety filter is not Pi core behavior. Strict parity means it should not be called from `agent_loop.py`. Risky appv231 tools should instead set `execution_mode="sequential"` at definition time, or the appv231 policy layer should decide how to expose tools.

### Preserve Ordered, Single-Threaded Event Delivery

Pi runs parallel tools through promises in one event loop. Python uses thread pools, so event sinks can be called from worker threads unless the port compensates.

The Python port should avoid calling Agent/TUI listeners from worker threads. Parallel workers may execute tools concurrently, but finalized outcomes should be collected and emitted back through the loop thread. Tool-result messages must remain in assistant source order, matching Pi.

### Keep Guardrails Outside Core

`tool_guardrails.py` remains an appv231/Hermes policy module. It should be invoked from `AgentSession.before_tool_call` and `AgentSession.after_tool_call`, not embedded as a core loop behavior.

The bash mutation classifier fix belongs in `tool_guardrails.py`, not `agent_loop.py`.

## Data Flow

1. The TUI or CLI sends a prompt to `CodingApp`.
2. `CodingApp` delegates to `AgentSession`.
3. `AgentSession` constructs appv231 policy hooks and tool definitions, then calls `Agent`.
4. `Agent` adapts high-level callbacks to Pi-compatible `AgentLoopConfig`.
5. `agent_loop.py` performs Pi-compatible streaming and tool execution.
6. Events flow through `Agent._process_event`, `AgentSession._handle_agent_event`, and TUI renderers.
7. After each turn, `AgentSession.prepare_next_turn_with_context` refreshes appv231 session state for the next provider request.

No compaction module participates in this repair.

## Error Handling

- `Agent.prompt()` and `Agent.continue_()` should keep Pi-style lifecycle handling: runtime exceptions become assistant error messages through the high-level `Agent` failure path.
- The stream wrapper functions should not leave consumers waiting forever if the worker fails.
- Low-level callback contracts should remain documented as "must not throw" where Pi says so; high-level Agent handling remains the safety net for unexpected exceptions.

## Testing Plan

Focused regression tests should prove these behaviors:

- Duplicate same-turn tool calls with distinct ids both execute in the parity core.
- Streaming duplicate tool calls do not leave stale TUI rows because core no longer drops them after stream updates.
- `agent_loop_continue()` worker failures end or fail the stream instead of hanging.
- `prepare_next_turn_with_context` receives the Pi turn context and active abort signal.
- Signal-only `prepare_next_turn` remains supported for compatibility.
- Parallel tool execution does not call Agent/TUI event sinks from worker threads.
- Parallel tool-result message artifacts remain in assistant source order.
- Bash mutation classifier catches attached redirects and absolute mutator commands.
- No files under `appV2.3.1/appv231/compaction/` change.

Existing tests that intentionally assert non-Pi core behavior, such as core-level duplicate tool-call suppression, should be rewritten to assert the new boundary:

- Pi core executes duplicates.
- appv231 policy, if kept, suppresses or blocks duplicates outside the core.

## Verification Gate

Implementation is not complete until these commands or equivalent project-local test commands provide fresh evidence:

- focused agent-loop regression tests pass
- focused coding-agent policy tests pass
- focused TUI regression tests pass
- `git diff -- appV2.3.1/appv231/compaction` is empty
- a parity review confirms touched `appv231/agent` behavior matches the corresponding Pi source behavior

## Out of Scope

- Any changes under `appV2.3.1/appv231/compaction/`
- Provider control-plane changes
- Docker or GHCR release changes
- npm package metadata changes
- broad TUI redesign
- session-store restructuring
