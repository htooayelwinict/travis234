# Phase 129 - Replaced Session Context

## Goal

Port Pi's `AgentSession.createReplacedSessionContext()` facade so replacement flows can obtain a fresh command context bound to the current appv22 session.

## Reference

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `appV2.2/appv22/coding_agent/agent_session.py`
- `appV2.2/appv22/coding_agent/extensions.py`

Key Pi behaviors covered in this slice:

- `createReplacedSessionContext()` returns a command-context-like object.
- `sendMessage` and `sendUserMessage` are bound to the current session.
- The context exposes current session/tool metadata.
- The Pythonic alias `create_replaced_session_context()` maps to the same behavior.

## Protected Compaction Note

No compaction implementation, threshold, timing, manual compression, or automatic compression logic changed in this phase. This phase only exposes an existing extension command context through Pi's replacement-context facade.

## Regression

Added:

- `test_agent_session_create_replaced_session_context_rebinds_message_senders`

The test first failed with:

```text
AttributeError: 'AgentSession' object has no attribute 'create_replaced_session_context'
```

## Implementation

- Added `AgentSession.create_replaced_session_context()`/`createReplacedSessionContext()`.
- Reused appv22's existing `_extension_command_context()` so replacement contexts share the same send, append, tool, command, thinking, exec, wait, and compact actions as extension command handlers.

## Verification

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'create_replaced_session_context' -q
```

Result after implementation: `1 passed, 112 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'create_replaced_session_context or (extension_command_context and not extension_command_context_exec_runs_without_session_message)' -q
```

Result: `9 passed, 104 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'runtime or create_replaced_session_context or extension_command_context and not extension_command_context_exec_runs_without_session_message' -q
```

Result: `13 passed, 100 deselected`.

```bash
PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests
```

Result: passed.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `111 passed, 2 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `283 passed, 2 deselected`.

## Remaining Count

The full goal remains active. This phase closes Pi's replaced-session context facade. Remaining likely slices include richer HTML tree/template behavior, bind/reload extension lifecycle details, additional model/settings registry parity, and live TUI rendering checks while preserving the current Hermes compaction behavior.
