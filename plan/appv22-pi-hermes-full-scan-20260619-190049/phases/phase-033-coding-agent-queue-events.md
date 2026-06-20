# Phase 033: Coding-Agent Queue Events

## Scope

Port the first session-event sub-slice from Pi `AgentSession`: session-level subscriptions and queue update events for steering/follow-up queues.

## Reference

- `pi/packages/coding-agent/src/core/agent-session.ts`

## Changes

- Added `test_agent_session_emits_queue_update_events_before_delivered_user_message`.
- Added `QueueUpdateEvent` with visible steering/follow-up queue snapshots.
- Added `AgentSession.subscribe()` and internal session event forwarding from the underlying `Agent`.
- Added session-level `steer()`, `follow_up()`, `continue_()`, `clear_queue()`, `pending_message_count`, `get_steering_messages()`, and `get_follow_up_messages()`.
- Matched Pi queue-removal ordering: when a queued user message is delivered, the session removes it from visible queue state and emits `queue_update` before forwarding that message's `message_start`.

## Red/Green Evidence

Red:

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_emits_queue_update_events_before_delivered_user_message -q
```

Result:

- Failed with `AttributeError: 'AgentSession' object has no attribute 'subscribe'`.

Green:

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_emits_queue_update_events_before_delivered_user_message -q
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Results:

- focused queue-event regression: `1 passed`
- `tests/test_coding_agent.py`: `20 passed`

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
git diff --check
```

Results:

- `tests`: `130 passed`
- `py_compile`: exit 0
- `git diff --check`: exit 0

## Remaining Work

- Port remaining Phase 3 session events: compaction events, retry events, thinking-level/model changes, and session-info updates.
- Port prompt preflight behavior for streaming: steer vs follow-up.
- Port session persistence/branching hooks after the core event/session API is stable.
