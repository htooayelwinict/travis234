# Phase 127 - Session JSONL Export

## Goal

Port Pi's `AgentSession.exportToJsonl()` facade so appv22 can export the current session branch as a standalone JSONL file.

## Reference

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `appV2.2/appv22/coding_agent/session_store.py`
- `appV2.2/appv22/coding_agent/agent_session.py`

Key Pi behaviors covered in this slice:

- `exportToJsonl(outputPath?)` writes a session header followed by active-branch entries.
- Exported entries are re-chained into a linear parent sequence.
- Entries outside the active branch are not exported.
- The Pythonic alias `export_to_jsonl()` maps to the same behavior.

## Protected Compaction Note

No compaction implementation, threshold, timing, manual compression, or automatic compression logic changed in this phase. The work only serializes the existing active session branch.

## Regression

Added:

- `test_agent_session_export_to_jsonl_writes_active_branch_with_linear_parent_ids`

The test first failed with:

```text
AttributeError: 'AgentSession' object has no attribute 'export_to_jsonl'
```

## Implementation

- Added `SessionStore.export_to_jsonl()`/`exportToJsonl()`.
- Added `AgentSession.export_to_jsonl()`/`exportToJsonl()`.
- Export writes a fresh session header using the existing session ID and cwd.
- Export walks `get_branch()` and rewrites `parentId` values as a linear chain while preserving entry IDs and payloads.
- Explicit output paths create parent directories when needed.

## Verification

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_jsonl_writes_active_branch' -q
```

Result after implementation: `1 passed, 110 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'persists_and_reloads or branch_repoints or export_to_jsonl or get_user_messages_for_forking or navigate_tree' -q
```

Result: `7 passed, 104 deselected`.

```bash
PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests
```

Result: passed.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `109 passed, 2 deselected`.

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q
```

Result: `281 passed, 2 deselected`.

## Remaining Count

The full goal remains active. This phase closes Pi's JSONL session export facade. Remaining likely slices include HTML export, replaced-session context details, additional runtime/extension APIs, and live TUI rendering checks while preserving the current Hermes compaction behavior.
