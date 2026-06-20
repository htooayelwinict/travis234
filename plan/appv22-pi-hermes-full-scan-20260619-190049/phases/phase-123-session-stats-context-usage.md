# Phase 123 - Session Stats Context Usage

## Goal

Port Pi's `AgentSession` stats/context-usage facade so appv22 exposes session metrics directly from the session object instead of only through ad hoc TUI callbacks.

## Reference

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/src/core/compaction/compaction.ts`
- `pi/packages/coding-agent/test/agent-session-stats.test.ts`
- `pi/packages/coding-agent/test/rpc.test.ts`

Key Pi behaviors covered in this slice:

- `getSessionStats()` counts user, assistant, tool-call, and tool-result messages.
- Assistant usage totals are accumulated into input, output, cache-read, cache-write, and total token counters.
- Assistant cost totals are accumulated.
- `getContextUsage()` returns `undefined`/`None` when the model has no context window.
- Context usage uses the latest reliable assistant usage plus estimated trailing message tokens.
- After a compaction entry, context usage is unknown until a non-error, non-aborted assistant usage exists after the compaction boundary.

## Protected Compaction Note

No compaction implementation was changed in this phase. The code only reads existing session compaction entries to match Pi's context-usage reporting semantics. The Hermes dual-pass/timing compaction behavior remains protected.

## Regression

Added:

- `test_agent_session_stats_and_context_usage_from_messages`
- `test_agent_session_context_usage_unknown_after_compaction_until_post_compaction_assistant`

The tests first failed with:

```text
AttributeError: 'AgentSession' object has no attribute 'get_session_stats'
AttributeError: 'AgentSession' object has no attribute 'get_context_usage'
```

## Implementation

- Added `session_id`/`sessionId` using the persisted session header ID.
- Added `get_session_stats()`/`getSessionStats()`.
- Added `get_context_usage()`/`getContextUsage()`.
- Added helper functions for latest compaction lookup, assistant usage filtering, context-token calculation, and latest-usage-plus-trailing-token estimation.
- Reused appv22's existing session message deserializer and token estimator instead of importing Pi/Hermes source modules.

## Verification

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'session_stats or context_usage' -q
```

Result: `2 passed, 105 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'session_stats or context_usage or get_last_assistant_text or get_user_messages_for_forking or branch or navigate_tree or persists_and_reloads' -q
```

Result: `10 passed, 97 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_tui.py -k 'context or status or footer' -q
```

Result: `9 passed, 41 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `105 passed, 2 deselected`.

```bash
PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests
```

Result: passed.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `277 passed, 2 deselected`.

## Remaining Count

The full goal remains active. This phase closes the session stats/context-usage facade. Remaining likely slices include more provider/extension parity, runtime-host details, and live TUI usability/rendering confidence checks while preserving the current Hermes compaction behavior.
