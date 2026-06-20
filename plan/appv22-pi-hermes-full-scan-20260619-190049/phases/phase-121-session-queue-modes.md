# Phase 121 - Session Queue Modes

## Goal

Port Pi's `AgentSession` queue-mode facade so appv22 exposes steering and follow-up queue modes at the session layer, not only on the lower-level agent.

## Reference

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/test/suite/agent-session-queue.test.ts`

Key Pi behaviors covered in this slice:

- `AgentSession.steeringMode` reflects `agent.steeringMode`.
- `AgentSession.followUpMode` reflects `agent.followUpMode`.
- `setSteeringMode("all")` switches steering delivery from one-at-a-time to batched delivery.
- `setFollowUpMode("all")` switches follow-up delivery from one-at-a-time to batched delivery.
- `followUp()` is available as the Pi camelCase alias for `follow_up()`.

## Protected Compaction Note

No compaction implementation was changed in this phase. The existing Hermes dual-pass/timing compaction layer remains protected; future compaction edits should be limited to direct Hermes/Pi parity fixes with regression tests.

## Regression

Added `test_agent_session_queue_modes_batch_messages_in_all_mode`.

The test first failed with:

```text
AttributeError: 'AgentSession' object has no attribute 'steering_mode'
```

After adding the mode facade, it also exposed the missing Pi alias:

```text
AttributeError: 'AgentSession' object has no attribute 'followUp'
```

## Implementation

- Added `steering_mode` and `follow_up_mode` constructor options and forwarded them into `Agent`.
- Added `steering_mode`/`steeringMode` and `follow_up_mode`/`followUpMode` properties.
- Added `set_steering_mode()`/`setSteeringMode()` and `set_follow_up_mode()`/`setFollowUpMode()` methods.
- Added `followUp` as the camelCase alias for `follow_up()`.

## Verification

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_agent_session_queue_modes_batch_messages_in_all_mode -q
```

Result: `1 passed`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'queue or follow_up or followUp or steering or streaming_behavior' -q
```

Result: `7 passed, 96 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_agent_loop.py -k 'queue or steering or follow_up or modes' -q
```

Result: `3 passed, 20 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `101 passed, 2 deselected`.

```bash
PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests
```

Result: passed.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `273 passed, 2 deselected`.

## Remaining Count

The full goal remains active. This phase closes the session-level queue-mode facade. Remaining likely slices include remaining provider/extension parity, runtime-host details, and live TUI usability/rendering confidence checks while preserving the current Hermes compaction behavior.
