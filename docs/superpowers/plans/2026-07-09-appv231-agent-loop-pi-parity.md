# appv231 Agent Loop Pi Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port `appV2.3.1/appv231/agent` to Pi agent-loop semantics without touching `appV2.3.1/appv231/compaction/`, while keeping appv231-specific safety behavior outside the core agent loop.

**Architecture:** Keep the core loop in `appV2.3.1/appv231/agent/agent_loop.py` as the Python port of `pi/packages/agent/src/agent-loop.ts`. Put appv231 policy in higher layers such as `appV2.3.1/appv231/coding_agent/agent_session.py` and `appV2.3.1/appv231/agent/tool_guardrails.py`. Preserve the mature compaction package as a redzone.

**Tech Stack:** Python appv231 runtime, pytest test suite under `appV2.3.1/tests`, local Pi TypeScript reference under `pi/packages/agent/src`.

---

## Non-Negotiables

- [ ] Do not edit anything under `appV2.3.1/appv231/compaction/`.
- [ ] Do not commit, push, open PRs, branch, tag, or publish. Lewis explicitly said no gitops.
- [ ] Do not weaken tests to preserve current appv231 drift from Pi.
- [ ] Do not silently drop tool calls in the core agent loop.
- [ ] Do not introduce web research, subagents, or broad parent-directory traversal.
- [ ] Subagent-driven execution requires a new explicit Lewis request; default execution for this repo is inline.

---

## Reference Files

- `pi/packages/agent/src/agent-loop.ts`
- `pi/packages/agent/src/agent.ts`
- `pi/packages/agent/src/types.ts`
- `appV2.3.1/appv231/agent/agent_loop.py`
- `appV2.3.1/appv231/agent/agent.py`
- `appV2.3.1/appv231/agent/types.py`
- `appV2.3.1/appv231/agent/tool_guardrails.py`
- `appV2.3.1/appv231/coding_agent/agent_session.py`
- `appV2.3.1/tests/test_agent_loop.py`
- `appV2.3.1/tests/test_coding_agent.py`
- `appV2.3.1/tests/test_tui.py`

---

## Implementation Steps

### Task 1: Establish The Test Runner And Current Baseline

**Files:**
- Read: `appV2.3.1/tests/test_agent_loop.py`
- Read: `appV2.3.1/tests/test_coding_agent.py`
- Read: `appV2.3.1/tests/test_tui.py`
- Modify: none

- [ ] Run the narrow existing suite that covers the target surface:

```bash
uv run --project appV2.3.1 python -m pytest -p no:cacheprovider \
  appV2.3.1/tests/test_agent_loop.py \
  appV2.3.1/tests/test_coding_agent.py \
  appV2.3.1/tests/test_tui.py
```

- [ ] If `uv` is unavailable, run the same module list with the project Python that already has pytest installed. Do not install packages during this step.
- [ ] Record which tests fail before implementation. Existing failures are baseline; new failures after edits are regressions unless the updated test encodes Pi parity.

Expected result after implementation: the same command exits `0`.

### Task 2: Convert Duplicate Tool-Call Behavior To Pi-Parity Tests

**Files:**
- Modify: `appV2.3.1/tests/test_agent_loop.py`
- Read: `appV2.3.1/appv231/agent/agent_loop.py`

Pi does not deduplicate final assistant tool calls in `streamAssistantResponse`. The current appv231 core test that expects one execution for identical tool calls must be rewritten to prove both calls execute.

- [ ] In `appV2.3.1/tests/test_agent_loop.py`, replace the current core duplicate test named `test_duplicate_tool_calls_in_same_assistant_turn_execute_once`.
- [ ] New expected behavior:
  - a streamed assistant message contains two tool calls with the same `name` and `args` but different ids
  - both tool calls execute
  - both tool result messages are emitted
  - the final assistant message preserves both tool calls

Test shape:

```python
def test_duplicate_tool_calls_in_same_assistant_turn_execute_like_pi():
    executed_ids: list[str] = []

    def tool(args):
        executed_ids.append(args["call_id"])
        return {"ok": True, "call_id": args["call_id"]}

    # Model stream emits call_1 and call_2 with identical semantic arguments.
    # The assertion must prove both ids are present in executed_ids and final messages.
```

- [ ] Keep any appv231 product-level duplicate suppression test out of `agent_loop.py`; core loop must not own that policy.

### Task 3: Add A TUI Regression For Stale Duplicate Tool Components

**Files:**
- Modify: `appV2.3.1/tests/test_tui.py`
- Read: `appV2.3.1/appv231/agent/agent_loop.py`

The observed TUI fault is a mismatch between streamed `message_update` tool components and the deduped final assistant message. Once core dedupe is removed, the duplicate streamed components should complete normally.

- [ ] Add or update a test in `appV2.3.1/tests/test_tui.py` that feeds a streamed assistant response with duplicate tool calls.
- [ ] Assert that the renderer-visible tool call ids all have matching terminal tool-result state.
- [ ] The test should fail against current code by proving an unexecuted component remains for the dropped duplicate id.
- [ ] The test should pass after removing core dedupe and executing both calls.

Minimal assertion target:

```python
assert renderer_component_ids == {"call_1", "call_2", "call_3"}
assert completed_tool_result_ids == {"call_1", "call_2", "call_3"}
assert stale_component_ids == set()
```

### Task 4: Port Pi Truncated Tool-Call Failure Semantics

**Files:**
- Modify: `appV2.3.1/tests/test_agent_loop.py`
- Modify: `appV2.3.1/appv231/agent/agent_loop.py`
- Reference: `pi/packages/agent/src/agent-loop.ts`

Pi fails tool calls found in a truncated assistant message when `stopReason === "length"`. appv231 currently lacks this path.

- [ ] Add a failing test in `appV2.3.1/tests/test_agent_loop.py`.
- [ ] Simulate a final assistant message with `stop_reason == "length"` and one or more tool calls.
- [ ] Assert that the loop emits tool-result messages marking those tool calls failed instead of attempting execution or silently dropping them.

Implementation target in `appV2.3.1/appv231/agent/agent_loop.py`:

- [ ] Add a helper equivalent to Pi `failToolCallsFromTruncatedMessage`.
- [ ] Call it immediately after assistant streaming completes when the assistant message has `stop_reason == "length"` and includes tool calls.
- [ ] Ensure normal non-truncated tool calls still execute.

Expected behavior:

```python
assert failed_tool_result.tool_call_id == "call_truncated"
assert failed_tool_result.is_error is True
assert "truncated" in failed_tool_result.content.lower()
```

### Task 5: Remove Core Final-Message Tool-Call Deduplication

**Files:**
- Modify: `appV2.3.1/appv231/agent/agent_loop.py`
- Modify: `appV2.3.1/tests/test_agent_loop.py`
- Modify: `appV2.3.1/tests/test_tui.py`

Core dedupe is the direct drift from Pi and the root of the stale TUI component fault.

- [ ] In `appV2.3.1/appv231/agent/agent_loop.py`, remove calls to `_deduplicate_tool_calls(final_message)` from `_stream_assistant_response`.
- [ ] Delete `_deduplicate_tool_calls`.
- [ ] Delete `_canonical_tool_call_arguments` if no remaining code uses it.
- [ ] Remove the now-unused `json` import if it becomes unused.
- [ ] Run:

```bash
uv run --project appV2.3.1 python -m pytest -p no:cacheprovider \
  appV2.3.1/tests/test_agent_loop.py::test_duplicate_tool_calls_in_same_assistant_turn_execute_like_pi \
  appV2.3.1/tests/test_tui.py
```

Expected result: duplicate ids are preserved through streamed state, final message state, execution, and TUI completion state.

### Task 6: Split Prepare-Next-Turn Callback Contracts Like Pi

**Files:**
- Modify: `appV2.3.1/appv231/agent/types.py`
- Modify: `appV2.3.1/appv231/agent/agent.py`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py`
- Modify: `appV2.3.1/tests/test_agent_loop.py`
- Reference: `pi/packages/agent/src/agent.ts`
- Reference: `pi/packages/agent/src/types.ts`

Pi exposes both callback shapes:

- `prepareNextTurn(signal)` for high-level simple callers
- `prepareNextTurnWithContext(context, signal)` for loop-aware callers

Current appv231 has the low-level context callback, but high-level `Agent` adapts the callback by discarding context. The product `AgentSession` currently passes a signal-shaped method into `prepare_next_turn`, creating a split-contract drift.

- [ ] In `appV2.3.1/appv231/agent/types.py`, introduce a named alias:

```python
PrepareNextTurnContext = ShouldStopAfterTurnContext
```

- [ ] Extend high-level agent options in `appV2.3.1/appv231/agent/agent.py`:

```python
prepare_next_turn: Callable[[AbortSignal | None], AgentLoopTurnUpdate | None] | None = None
prepare_next_turn_with_context: Callable[
    [PrepareNextTurnContext, AbortSignal | None],
    AgentLoopTurnUpdate | None,
] | None = None
```

- [ ] Update `Agent._build_config()` so it follows Pi ordering:
  - if `prepare_next_turn_with_context` exists, call it with `(context, self._signal)`
  - otherwise, if `prepare_next_turn` exists, call it with `(self._signal)`
  - otherwise, pass `None`
- [ ] Update `appV2.3.1/appv231/coding_agent/agent_session.py` to use `prepare_next_turn_with_context` for session refresh behavior.
- [ ] Keep the existing signal-only high-level test:

```python
test_agent_prepare_next_turn_receives_active_abort_signal
```

- [ ] Add or update a context-aware high-level test proving the context object is passed through to `prepare_next_turn_with_context`.

Expected result:

```python
assert seen_context.turn_count == 1
assert seen_signal is agent.signal
```

### Task 7: Add Exception Parity To `agent_loop_continue`

**Files:**
- Modify: `appV2.3.1/appv231/agent/agent_loop.py`
- Modify: `appV2.3.1/tests/test_agent_loop.py`

`agent_loop` catches unexpected stream/runtime exceptions and emits an assistant error path. `agent_loop_continue` currently lacks the same wrapper and can leave consumers without a terminal event.

- [ ] Add a failing test in `appV2.3.1/tests/test_agent_loop.py` where `stream_assistant_response_fn` raises during `agent_loop_continue`.
- [ ] Assert that an `AgentEndEvent` is emitted and the output stream reaches a terminal state.
- [ ] Mirror the existing wrapper behavior from `agent_loop()` in `agent_loop_continue()`.
- [ ] Keep the emitted message list consistent with existing `agent_loop()` error behavior.

Expected result:

```python
assert any(event.type == "agent_end" for event in events)
assert output_stream.done is True
```

### Task 8: Restore Pi Tool Dispatch Semantics

**Files:**
- Modify: `appV2.3.1/appv231/agent/agent_loop.py`
- Modify: `appV2.3.1/tests/test_agent_loop.py`
- Reference: `pi/packages/agent/src/agent-loop.ts`

Pi dispatch rule:

- if global `tool_execution == "sequential"`, run sequential
- otherwise, if any requested tool has `execution_mode == "sequential"`, run sequential
- otherwise, run parallel

Current appv231 uses `should_parallelize_tool_batch()`, which is not Pi core behavior.

- [ ] In `appV2.3.1/tests/test_agent_loop.py`, add tests for:
  - global sequential forces sequential
  - one tool with `execution_mode == "sequential"` forces sequential
  - default global parallel with parallel-safe tools runs parallel
  - appv231 guardrail classification does not control core dispatch
- [ ] In `appV2.3.1/appv231/agent/agent_loop.py`, replace:

```python
if config.tool_execution != "sequential" and should_parallelize_tool_batch(tool_calls, tools):
    return _execute_parallel(...)
return _execute_sequential(...)
```

with the Pi-equivalent rule:

```python
if config.tool_execution == "sequential":
    return _execute_sequential(...)

has_sequential_tool_call = any(
    tools.get(call.name) is not None
    and tools[call.name].execution_mode == "sequential"
    for call in tool_calls
)
if has_sequential_tool_call:
    return _execute_sequential(...)

return _execute_parallel(...)
```

- [ ] Remove the `should_parallelize_tool_batch` import from `agent_loop.py` if it becomes unused there.

Expected result: core dispatch decisions match Pi. Safety-specific parallel restrictions belong outside the core loop.

### Task 9: Emit Parallel Tool-End Events From The Loop Thread

**Files:**
- Modify: `appV2.3.1/appv231/agent/agent_loop.py`
- Modify: `appV2.3.1/tests/test_agent_loop.py`

Current appv231 emits `tool_execution_end` inside worker threads. Pi's JavaScript event model avoids cross-thread event delivery. The Python port should return finalized tool results from workers and emit events on the loop thread.

- [ ] Add a regression test in `appV2.3.1/tests/test_agent_loop.py`.
- [ ] Capture `threading.current_thread().name` inside the event listener for `tool_execution_end`.
- [ ] Run two parallel tool calls.
- [ ] Assert every `tool_execution_end` listener invocation happens on `MainThread`.
- [ ] Also assert emitted tool-result messages remain in source tool-call order.

Implementation target in `_execute_parallel()`:

- [ ] Worker function returns `(index, finalized_result)` only.
- [ ] Use `concurrent.futures.as_completed()` to collect completion order.
- [ ] Emit `_emit_tool_end(finalized, emit)` on the caller thread as each future completes.
- [ ] Store finalized results by original source index.
- [ ] Append tool result messages in original source order.

Expected behavior:

```python
assert all(name == "MainThread" for name in end_event_threads)
assert [msg.tool_call_id for msg in tool_result_messages] == ["call_1", "call_2"]
```

### Task 10: Move Appv231 Duplicate Bash Suppression Out Of Core

**Files:**
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py`
- Modify: `appV2.3.1/tests/test_coding_agent.py`
- Read: `appV2.3.1/appv231/agent/agent_loop.py`

If appv231 still needs duplicate bash suppression for product safety, it must live above the Pi core. It must not mutate the final assistant message or make streamed components disappear.

- [ ] Update `appV2.3.1/tests/test_coding_agent.py::test_agent_session_deduplicates_duplicate_bash_calls_in_same_turn`.
- [ ] New expectation:
  - core loop sees all tool calls
  - duplicate bash policy returns an explicit blocked/synthetic tool result for the duplicate call id
  - final assistant message still contains all tool call ids
  - TUI has a terminal state for every streamed tool component
- [ ] Implement the policy in `appV2.3.1/appv231/coding_agent/agent_session.py`, not in `agent_loop.py`.
- [ ] Store per-turn bash signatures in the session.
- [ ] Reset the per-turn signature set when a new assistant turn starts.
- [ ] Use `AgentSession`'s `before_tool_call` path to return an explicit duplicate-block result.

Expected behavior:

```python
assert executed_bash_call_ids == ["call_1", "call_3"]
assert blocked_duplicate_call_ids == ["call_2"]
assert final_assistant_tool_ids == ["call_1", "call_2", "call_3"]
```

### Task 11: Fix Bash Mutation Classification In Guardrails

**Files:**
- Modify: `appV2.3.1/appv231/agent/tool_guardrails.py`
- Modify: `appV2.3.1/tests/test_coding_agent.py`

The classifier currently misses attached redirects and absolute mutator commands. This belongs in appv231 safety policy, not in Pi core dispatch.

- [ ] Add tests in `appV2.3.1/tests/test_coding_agent.py` for:

```python
assert _bash_command_may_change_state("echo hi > file") is True
assert _bash_command_may_change_state("echo hi >file") is True
assert _bash_command_may_change_state("cat <<EOF >out.txt\nx\nEOF") is True
assert _bash_command_may_change_state("/bin/rm file") is True
assert _bash_command_may_change_state("/usr/bin/touch file") is True
```

- [ ] In `appV2.3.1/appv231/agent/tool_guardrails.py`, update `_bash_command_may_change_state()` to:
  - parse tokens with `shlex` as it already does
  - treat `_is_redirection_token(token)` as state-changing, including attached redirects
  - normalize command names with `os.path.basename()` before checking mutator command names
  - preserve existing positive detections such as `echo hi > file`
- [ ] Keep this classifier out of `agent_loop.py`.

Expected result: all listed commands classify as mutating.

### Task 12: Run Focused Verification

**Files:**
- Read: `appV2.3.1/tests/test_agent_loop.py`
- Read: `appV2.3.1/tests/test_coding_agent.py`
- Read: `appV2.3.1/tests/test_tui.py`
- Read: `appV2.3.1/tests/test_ai_appv2_env_provider.py`
- Verify unchanged: `appV2.3.1/appv231/compaction/`

- [ ] Run the target suite:

```bash
uv run --project appV2.3.1 python -m pytest -p no:cacheprovider \
  appV2.3.1/tests/test_agent_loop.py \
  appV2.3.1/tests/test_coding_agent.py \
  appV2.3.1/tests/test_tui.py
```

- [ ] Run any additional existing tests that directly import touched modules:

```bash
uv run --project appV2.3.1 python -m pytest -p no:cacheprovider \
  appV2.3.1/tests/test_ai_appv2_env_provider.py
```

- [ ] Run a compaction redzone check:

```bash
git diff -- appV2.3.1/appv231/compaction
```

Expected output:

```text

```

- [ ] Run a no-gitops status check:

```bash
git status --short
```

Expected result: modified implementation and test files only, plus the already untracked local artifacts unless Lewis asks to clean them. No commits, pushes, PRs, branches, tags, or publishes.

### Task 13: Final Review Checklist

**Files:**
- Read: `git status --short`
- Read: `git diff -- appV2.3.1/appv231/compaction`

- [ ] Core duplicate tool calls execute like Pi.
- [ ] TUI duplicate tool components all reach terminal state.
- [ ] Truncated assistant tool calls fail explicitly like Pi.
- [ ] `agent_loop_continue()` emits terminal end events on runtime exceptions.
- [ ] High-level `Agent` supports both signal-only and context-aware prepare-next-turn callbacks.
- [ ] `AgentSession` uses the context-aware callback for appv231 session refresh.
- [ ] Core dispatch matches Pi and does not call appv231 mutation classifiers.
- [ ] Parallel tool-end events are emitted from the loop thread.
- [ ] Appv231 duplicate bash policy is explicit and outside the core loop.
- [ ] Bash mutation guardrails detect attached redirects and absolute mutator paths.
- [ ] No files under `appV2.3.1/appv231/compaction/` changed.
- [ ] No gitops were performed.
