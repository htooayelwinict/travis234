# Phase 027 - Hermes Session Lineage Persistence

## Scope

Port enough of Hermes' compression session split persistence for appv22 lineage to survive restarts instead of existing only in memory.

## Reference

- `hermes-agent/agent/conversation_compression.py`
  - ends the old session with `end_reason="compression"`
  - creates the child session with `parent_session_id=old_session_id`
- `hermes-agent/hermes_state.py`
  - `sessions` table
  - `parent_session_id`
  - `ended_at`
  - `end_reason`
- `hermes-agent/tests/gateway/test_compression_session_id_persistence.py`
  - persists post-compression session-id updates so restart resumes the child session
- `hermes-agent/tests/agent/test_compression_concurrent_fork.py`
  - verifies children are counted through `parent_session_id`

## Appv22 Changes

- Added `SessionLineageStore`, a small SQLite-backed persistence helper with a Hermes-style `sessions` table:
  - `id`
  - `parent_session_id`
  - `started_at`
  - `ended_at`
  - `end_reason`
- Made `SessionLineage` accept an optional store.
- Made `SessionLineage.rotate(reason="compression")` write the old session end reason and insert the child session with a parent link.
- Added `SessionLineage.load(store, current_id=...)` to reconstruct the parent chain from persisted rows after restart.
- Exported `SessionLineageStore` from `appv22.compaction`.

## Regression

- Added `test_session_lineage_persists_parent_chain_across_reload`.
  - Red: failed with `ImportError: cannot import name 'SessionLineageStore' from 'appv22.compaction'`.
  - Green: passed after adding the store, wiring rotation persistence, and exporting the type.

## Verification

- `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction_timing.py::test_session_lineage_persists_parent_chain_across_reload -q`
  - `1 passed`
- `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction_timing.py::test_session_lineage_rotation -q`
  - `1 passed`
- `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py tests/test_compaction_timing.py -q`
  - `34 passed`
- `cd appV2.2 && PYTHONPATH=. uv run pytest tests -q`
  - `118 passed`
- `cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')`
  - Passed

## Remaining Gap

Manual compression feedback/status still needs a user-facing result path matching Hermes `/compress` status semantics.
