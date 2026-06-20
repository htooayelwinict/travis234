# Phase 034: Coding-Agent Prompt Streaming Behavior

## Scope

Port the Phase 3 `AgentSession.prompt()` streaming preflight behavior from Pi: concurrent prompts must be explicitly routed to steering or follow-up queues instead of falling through to the core agent active-run rejection.

## Reference

- `pi/packages/coding-agent/src/core/agent-session.ts`

## Changes

- Added `test_agent_session_prompt_queues_during_streaming_by_behavior`.
- Added `AgentSession.is_streaming`.
- Added `streaming_behavior`, `preflight_result`, and optional image handling to `AgentSession.prompt()`.
- While streaming, `streaming_behavior="steer"` routes through `AgentSession.steer()`.
- While streaming, `streaming_behavior="followUp"` or `"follow_up"` routes through `AgentSession.follow_up()`.
- A streaming prompt without behavior raises a Pi-shaped error and reports `preflight_result(False)`.
- Successful streaming queue routing reports `preflight_result(True)` and returns without entering `Agent.prompt()`.

## Red/Green Evidence

Red:

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_prompt_queues_during_streaming_by_behavior -q
```

Result:

- Failed with `AttributeError: 'AgentSession' object has no attribute 'is_streaming'`.

Green:

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_prompt_queues_during_streaming_by_behavior -q
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Results:

- focused streaming-behavior regression: `1 passed`
- `tests/test_coding_agent.py`: `21 passed`

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
git diff --check
```

Results:

- `tests`: `131 passed`
- `py_compile`: exit 0
- `git diff --check`: exit 0

## Remaining Work

- Port remaining Phase 3 session events: compaction events, retry events, thinking-level/model changes, and session-info updates.
- Port session persistence/branching hooks after the core event/session API is stable.
- Continue Phase 4 tool parity and Phase 6 TUI/rendering parity.
