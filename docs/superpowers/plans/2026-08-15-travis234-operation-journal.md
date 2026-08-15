# Travis234 Durable Operation Journal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Repository guidance prohibits subagents unless the user explicitly requests them, so implementation and review are inline by default.

**Goal:** Make provider and tool-effect uncertainty visible after a crash through a separate durable intent/effect/settlement journal, without replacing JSONL history or automatically replaying anything.

**Architecture:** Add a session-owned `OperationCoordinator` backed by SQLite at the existing agent state root. A turn operation records a durable program counter and bounded registers. Provider and tool boundaries commit an effect intent before execution and settle it after execution; conversation-visible JSONL continues through its existing owner and source order. Startup marks committed-but-unsettled effects uncertain and exposes them read-only. Any journal failure degrades journaling, never the coding turn.

**Tech Stack:** Python 3.13, stdlib `sqlite3` in WAL mode, existing agent-directory resolution and SQLite helpers, session turn/tool hooks, immutable snapshots, native TUI read-only command, pytest subprocess fault injection, npm, and wheel/sdist builds.

## Global Constraints

- Start from the verified Phase 3 commit on branch `codex/operation-journal`.
- Store only at `~/.travis234/agent/operations.sqlite3` as resolved by the existing agent-directory owner. Do not add an alternate path or migration alias.
- JSONL remains authoritative conversation history. Do not move messages, branches, compaction, or session metadata into SQLite.
- Default `operations.mode` to `observe`; support only `disabled|observe` in this release.
- Only global/user settings can disable observation or raise its 1 GiB storage cap; trusted project settings may lower the cap but cannot weaken the user-owned journal.
- Commit effect intent before provider/tool execution and settlement after it. Do not reorder generic tool scheduling, result persistence, or provider events.
- Every effect replay policy is `never` in this release. Persisting the enum does not authorize replay.
- Never record prompts, completions, tool arguments/results, environment values, file contents, credentials, steering text, or subagent goals. Store stable fingerprints and bounded codes only.
- An intent without settlement becomes `uncertain`; it is never inferred as success/failure and never retried.
- Journal initialization/write/corruption failure emits one sanitized diagnostic, disables further journal writes for that session, and cannot block JSONL persistence.
- Multiple Travis234 processes may share the journal. Recovery must not mark effects owned by a live runtime uncertain.
- Retention is explicit. Do not automatically prune journal rows during startup or shutdown.
- Keep focused operation modules at or below 750 lines and independent of TUI, `AgentSession`, `CodingApp`, and provider implementations.
- Do not build or smoke a container in this phase.

---

### Task 1: Define operation/effect state machines and strict settings

**Files:**
- Create: `travis/coding_agent/operations/__init__.py`
- Create: `travis/coding_agent/operations/types.py`
- Modify: `travis/coding_agent/settings_manager.py`
- Test: `tests/test_operation_types.py`
- Test: `tests/test_operation_settings.py`

**Interfaces:**
- Add `OperationMode = Literal["disabled", "observe"]` and `ReplayPolicy = Literal["never"]`.
- Add immutable `RuntimeLease`, `OperationRecord`, `OperationRegister`, `EffectRecord`, `UsageLedgerEntry`, and `OperationSnapshot`.
- Operation states: `running`, `settled`, `failed`, `cancelled`, `uncertain`.
- Effect states: `intent`, `settled`, `failed`, `cancelled`, `uncertain`.
- ID formats: `op_` and `effect_` plus 32 lowercase hexadecimal characters.
- Add `SettingsManager.get_operation_settings()` with default `{mode: observe, maxBytes: 1 GiB}`.

- [ ] **Step 1: Write failing state/validation/default tests**

```python
def test_operations_default_to_observe_only():
    assert SettingsManager.in_memory().get_operation_settings() == {
        "mode": "observe", "maxBytes": 1024 * 1024 * 1024,
    }

def test_replay_policy_has_no_safe_value_in_first_release():
    assert get_args(ReplayPolicy) == ("never",)
```

Cover invalid IDs, timestamps, negative counters/usage, invalid transitions, unknown modes, project attempts to disable/raise capacity, canonical state serialization, and defensive copying of bounded register values.

- [ ] **Step 2: Run the tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_operation_types.py tests/test_operation_settings.py -q
```

- [ ] **Step 3: Implement the contracts without execution behavior**

Registers accept only JSON scalar/list/object values after recursive redaction and a 16 KiB serialized limit, with at most 128 register keys per operation. Cap effects at 10,000 per operation. Fingerprints are lowercase SHA-256. Error/outcome codes are bounded identifiers, not exception strings.

- [ ] **Step 4: Verify and commit**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_operation_types.py tests/test_operation_settings.py -q
wc -l travis/coding_agent/operations/types.py
git add travis/coding_agent/operations travis/coding_agent/settings_manager.py \
  tests/test_operation_types.py tests/test_operation_settings.py
git commit -m "feat(operations): define observe-only journal contracts"
```

### Task 2: Implement the durable SQLite store and explicit retention

**Files:**
- Create: `travis/coding_agent/operations/store.py`
- Modify: `travis/coding_agent/sqlite_utils.py`
- Test: `tests/test_operation_store.py`

**Interfaces:**
- Add `OperationStore(path)` with `open_runtime`, `heartbeat_runtime`, `close_runtime`, `create_operation`, `advance`, `set_register`, `begin_effect`, `settle_effect`, `record_usage`, `settle_operation`, `snapshot`, `list_uncertain`, and `prune_settled_before`.
- Tables: `store_meta`, `runtime_leases`, `operations`, `registers`, `effects`, and `usage_ledger` with foreign keys and indexed session/state/timestamp columns.
- Set schema version 1, `PRAGMA journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout`, and restrictive file permissions.
- Refuse new writes with stable `journal_capacity` when the configured database/WAL footprint reaches the cap; do not delete old rows automatically.
- `prune_settled_before` never removes running/uncertain operations and returns exact row counts.

- [ ] **Step 1: Write failing schema and durability tests**

Test fresh schema, reopen, concurrent writers, runtime lease identity/heartbeat/close, monotonic program counter, register/effect caps, database-cap refusal, effect ordinal uniqueness, exactly-once settlement, duplicate usage source key, transaction rollback, database/WAL/SHM file mode `0600`, WAL cleanup on close, explicit pruning, and refusal to prune uncertain rows.

- [ ] **Step 2: Run store tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_operation_store.py -q
```

- [ ] **Step 3: Implement short, locked transactions**

Use a connection per operation or the repository's synchronized SQLite helper; never hold a transaction while a provider or tool executes. Commit intent in its own transaction. Settlement compares current state so duplicates are idempotent but conflicting settlement raises a typed store error. Fsync behavior follows SQLite durability, not a second ad hoc journal.

- [ ] **Step 4: Verify corruption is detected, not repaired destructively**

Add tests for random bytes, incompatible schema version, read-only directory, disk-full injection, and locked database timeout. Opening a corrupt/incompatible database raises a sanitized `OperationStoreUnavailable`; it does not delete, rename, truncate, or rebuild the file.

- [ ] **Step 5: Verify and commit**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_operation_store.py tests/test_process_completions.py tests/test_session_index.py -q
wc -l travis/coding_agent/operations/store.py
git add travis/coding_agent/operations/store.py travis/coding_agent/sqlite_utils.py \
  tests/test_operation_store.py
git commit -m "feat(operations): persist intent and settlement state"
```

Use the actual existing SQLite-owner test names if repository discovery shows these behaviors live in consolidated suites; do not duplicate their coverage under guessed filenames.

### Task 3: Add a fail-open session operation coordinator

**Files:**
- Create: `travis/coding_agent/operations/coordinator.py`
- Modify: `travis/coding_agent/agent_session_services.py`
- Modify: `travis/coding_agent/agent_session.py`
- Modify: `travis/app.py`
- Test: `tests/test_operation_coordinator.py`

**Interfaces:**
- Add app-owned `OperationRuntime` that owns the shared store connection factory, one process lease/heartbeat, and `for_session(session_id) -> OperationCoordinator`.
- Add `OperationCoordinator.start(kind, session_id)`, `advance(phase, registers) -> int`, `begin_effect(kind, name, fingerprint)`, `settle_effect(handle, outcome_code)`, `record_usage(...)`, `complete(outcome_code)`, and `disable(reason_code)`; the store increments the counter transactionally rather than trusting a caller-supplied number.
- Add `EffectHandle(operation_id, effect_id)`; all effects use replay policy `never`.
- Add `NullOperationCoordinator` for disabled mode or unavailable storage.
- Register one runtime lease with random instance ID, PID, process creation time, and bounded heartbeat; close it during normal app shutdown.
- Emit one sanitized diagnostic through the existing session event path after degradation.

- [ ] **Step 1: Write failing coordinator/degradation tests**

Cover normal sequence, nested provider/tool effects, internal child-session coordinator creation, invalid ordering, cancellation, lease heartbeat/normal close, heartbeat task shutdown, store failure at each method, one diagnostic only, continued no-op behavior after disable, session ID fingerprinting when absent, and no raw values in emitted diagnostics.

- [ ] **Step 2: Run coordinator tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_operation_coordinator.py -q
```

- [ ] **Step 3: Implement fail-open orchestration**

The runtime owns the lease and closes it after all session coordinators during app shutdown. Each coordinator owns current operation/effect handles but not messages or execution. Any store exception is caught at this boundary, mapped to `journal_unavailable`, and swaps future calls to no-ops. The diagnostic includes only operation kind and code.

- [ ] **Step 4: Verify composition and architecture limits**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_operation_coordinator.py tests/coding_agent/test_agent_session_characterization.py \
  tests/architecture/test_facade_boundaries.py -q
wc -l travis/coding_agent/operations/coordinator.py
```

- [ ] **Step 5: Commit the coordinator**

```bash
git add travis/coding_agent/operations/coordinator.py \
  travis/coding_agent/agent_session_services.py travis/coding_agent/agent_session.py \
  travis/app.py \
  tests/test_operation_coordinator.py
git commit -m "feat(operations): compose fail-open session journal"
```

### Task 4: Journal tool effects around the existing session hooks

**Files:**
- Modify: `travis/coding_agent/session_policy_controller.py`
- Modify: `travis/coding_agent/session_events.py`
- Test: `tests/test_operation_tool_effects.py`
- Test: `tests/test_tool_policy_integration.py`

**Interfaces:**
- In `_before_tool_call`: extension mutation/denial, then Phase 1D authorization, then committed effect intent.
- In `_after_tool_call`: settle the tool effect before extension result mutation and before generic-loop result persistence.
- Tool intent records tool name, sorted effect classes, and fingerprint of final arguments; settlement records only `ok`, `tool_error`, or `cancelled`.
- Journal failure never blocks or rewrites a tool result.

- [ ] **Step 1: Add a failing exact-order test**

```python
assert events == [
    "extension_before", "policy_allow", "journal_intent", "tool_execute",
    "journal_settlement", "extension_after", "result_persisted",
]
```

Also test extension denial and policy denial create no effect, argument mutation changes the fingerprint, parallel calls keep independent handles, source-ordered result persistence is unchanged, tool exception settles failed, cancellation settles cancelled when observed, and settlement write failure does not change returned content.

- [ ] **Step 2: Run tool-effect tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_operation_tool_effects.py tests/test_tool_policy_integration.py -q
```

- [ ] **Step 3: Carry handles by tool-call ID, not a single mutable slot**

Parallel execution requires a synchronized map keyed by operation/tool-call ID. Remove handles in a `finally` path after settlement attempt. Do not serialize the tool batch and do not import operation code into the generic agent loop.

- [ ] **Step 4: Verify generic-loop parity and commit**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_operation_tool_effects.py tests/test_tool_policy_integration.py \
  tests/test_agent_loop.py tests/test_agent_loop_compatibility.py -q
git diff --exit-code HEAD -- travis/agent/agent_loop.py
git add travis/coding_agent/session_policy_controller.py \
  travis/coding_agent/session_events.py tests/test_operation_tool_effects.py \
  tests/test_tool_policy_integration.py
git commit -m "feat(operations): journal coding tool effects"
```

### Task 5: Journal turns, provider effects, program counters, and usage

**Files:**
- Modify: `travis/coding_agent/session_turns.py`
- Modify: `travis/coding_agent/session_persistence.py`
- Modify: `travis/coding_agent/agent_session_runtime.py`
- Test: `tests/test_operation_provider_effects.py`
- Test: `tests/test_operation_usage.py`

**Interfaces:**
- Start one operation for each mutating prompt/continue turn, including internal typed child turns; register only branch/session/task IDs as hashes, bounded role name, and current turn sequence—never the child goal/context.
- The durable program counter is a strictly increasing integer. Store repeatable symbolic phase register values `turn_started`, `provider_intent`, `provider_settled`, `tools_settled`, `conversation_persisted`, and `turn_settled`; provider/tool continuation cycles may repeat phase names without moving the counter backward.
- Wrap the session-selected stream function immediately before invocation; do not modify provider classes.
- Usage ledger records provider/model identifiers and numeric usage/cost fields from settled assistant messages with a deterministic source key.

- [ ] **Step 1: Write failing provider/usage sequence tests**

Cover successful stream, provider error, cancellation, each session-level retry invocation as a distinct effect, tool continuation, no-tool turn, compaction continuation, zero/estimated usage, duplicate persistence callbacks, journal failure, and JSONL failure after effect settlement. Assert prompt/completion content and provider credentials are absent from every database text column.

- [ ] **Step 2: Run provider tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_operation_provider_effects.py tests/test_operation_usage.py -q
```

- [ ] **Step 3: Implement at the session-selected stream boundary**

Fingerprint the normalized provider request shape without message bodies. Settle when the stream future/result settles, including its shaped error class. A session auto-retry that invokes the routed stream again is a new effect; transport-internal retries remain one provider effect because this phase does not modify provider ownership. Advance `conversation_persisted` only from the existing persistence success edge. A JSONL failure can leave a settled external effect with a failed operation; preserve both facts.

- [ ] **Step 4: Verify provider ownership and retry semantics**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_operation_provider_effects.py tests/test_operation_usage.py \
  tests/test_provider_ownership_architecture.py tests/test_agent_loop_compatibility.py -q
```

- [ ] **Step 5: Commit turn/provider journaling**

```bash
git add travis/coding_agent/session_turns.py travis/coding_agent/session_persistence.py \
  travis/coding_agent/agent_session_runtime.py tests/test_operation_provider_effects.py \
  tests/test_operation_usage.py
git commit -m "feat(operations): journal provider effects and usage"
```

### Task 6: Recover uncertain effects without replay

**Files:**
- Create: `travis/coding_agent/operations/recovery.py`
- Modify: `travis/coding_agent/agent_session_services.py`
- Test: `tests/test_operation_recovery.py`

**Interfaces:**
- Add `OperationRecovery.inspect(store) -> RecoveryReport`.
- On startup, atomically transition every `intent` effect owned by a provably dead/stale runtime and its running operation to `uncertain`; leave live-runtime effects untouched.
- `RecoveryReport` contains counts and bounded metadata only.
- Export no `replay`, `resume`, or effect-execution method.

- [ ] **Step 1: Write failing startup recovery tests**

Cover no rows, settled rows, one/many intents, already uncertain state, a second live Travis process, dead PID, reused PID with mismatched process creation time, expired heartbeat when process liveness is unavailable, two simultaneous inspectors, corrupt database, JSONL session absent/present, all replay policies `never`, and repeated startup idempotence. Assert no tool/provider mock is called.

- [ ] **Step 2: Run recovery tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_operation_recovery.py -q
```

- [ ] **Step 3: Implement classification only**

Check local process liveness with the existing `psutil` dependency using PID plus process creation time; fall back to a 60-second heartbeat lease when liveness cannot be determined. Use one short SQLite transaction to claim only dead/stale intents. Do not derive a result from JSONL and do not append a synthetic conversation message. Surface the report through a sanitized startup/session diagnostic.

- [ ] **Step 4: Prove the API cannot replay and commit**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_operation_recovery.py tests/test_session_store_recovery.py -q
rg -n "def (replay|resume_effect)|ReplayPolicy.*safe" travis/coding_agent/operations
```

Expected: no executable replay API and no `safe` replay policy.

```bash
git add travis/coding_agent/operations/recovery.py \
  travis/coding_agent/agent_session_services.py tests/test_operation_recovery.py
git commit -m "feat(operations): report uncertain effects without replay"
```

### Task 7: Add read-only TUI inspection, fault injection, and documentation

**Files:**
- Create: `travis/tui/interactive_operations.py`
- Modify: `travis/tui/interactive_mode.py`
- Modify: `travis/tui/interactive_command_dispatcher.py`
- Modify: `travis/tui/user_commands.py`
- Create: `tests/fixtures/operation_crash_worker.py`
- Test: `tests/test_operation_fault_injection.py`
- Test: `tests/tui/test_interactive_operations.py`
- Modify: `docs/architecture/contract-parity.md`
- Modify: `docs/settings.md`
- Modify: `README.md`
- Modify: `scripts/verify_acceptance.py`
- Test: `tests/architecture/test_acceptance_matrix.py`

**Interfaces:**
- Add read-only `/operations` summary and `/operations <op_id>` detail, authorized to the current session's hashed identity only.
- Display IDs, kinds, counters, states, effect names/replay policy, and timestamps only. An operation from another session is reported as unknown even when its opaque ID is supplied.
- The fault worker exits abruptly at named checkpoints: before intent, after intent, during effect, after effect before settlement, after settlement, and after JSONL persistence.
- Acceptance reports mode/schema version/counts only.

- [ ] **Step 1: Write failing crash-window and TUI tests**

Spawn the fixture in a separate process per checkpoint, wait for its PID to be absent, reopen both stores, and assert exact JSONL/journal combinations. `after intent` and `during/after effect before settlement` must become uncertain and never execute again on startup. A simultaneously running second fixture remains live. `after settlement` remains settled even if conversation output is missing. Corrupt journal must leave JSONL readable.

- [ ] **Step 2: Run fault/TUI tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_operation_fault_injection.py tests/tui/test_interactive_operations.py -q
```

- [ ] **Step 3: Implement inspection and document dual persistence**

Explain the uncertainty window, observe-only behavior, explicit retention API, corruption degradation, and why exactly-once is not claimed. Keep `/operations` read-only; explicit pruning remains a programmatic/admin action and must never be coupled to inspection.

- [ ] **Step 4: Run architecture and acceptance tests**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_operation_fault_injection.py tests/tui/test_interactive_operations.py \
  tests/architecture/test_acceptance_matrix.py tests/architecture/test_facade_boundaries.py -q
```

- [ ] **Step 5: Commit recovery UX and evidence**

```bash
git add travis/tui/interactive_operations.py travis/tui/interactive_mode.py \
  travis/tui/interactive_command_dispatcher.py travis/tui/user_commands.py \
  tests/fixtures/operation_crash_worker.py tests/test_operation_fault_injection.py \
  tests/tui/test_interactive_operations.py docs/architecture/contract-parity.md \
  docs/settings.md README.md scripts/verify_acceptance.py \
  tests/architecture/test_acceptance_matrix.py
git commit -m "docs: expose observe-only operation recovery"
```

### Task 8: Phase 4 repository and installed-wheel qualification

**Files:**
- No source changes expected.

**Interfaces:**
- Consumes: complete Phase 4 branch.
- Produces: fresh non-container evidence and the exact base commit for Phase 5.

- [ ] **Step 1: Run operation and invariant slices**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_operation_types.py tests/test_operation_settings.py \
  tests/test_operation_store.py tests/test_operation_coordinator.py \
  tests/test_operation_tool_effects.py tests/test_operation_provider_effects.py \
  tests/test_operation_usage.py tests/test_operation_recovery.py \
  tests/test_operation_fault_injection.py tests/tui/test_interactive_operations.py \
  tests/test_session_store_recovery.py tests/test_agent_loop.py -q
```

- [ ] **Step 2: Run complete non-container qualification**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest tests -q
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
phase4_dist=$(mktemp -d /tmp/travis234-phase4.XXXXXX)
uv build --clear --out-dir "$phase4_dist" .
/Users/htooayelwin/orca/travis234/.venv/bin/python scripts/verify_acceptance.py --parity-json
```

- [ ] **Step 3: Run installed-wheel crash-awareness scenarios**

Install the exact wheel in an isolated Python 3.13 environment and use only the documented `--dotenv` boundary. Run one settled provider/tool turn, inspect it through `/operations`, then terminate fixture turns after intent and after settlement. Restart the wheel and prove the former is uncertain, the latter settled, neither is replayed, JSONL remains independently resumable, and disabling operations creates no database writes. Do not print dotenv values or raw prompts/arguments.

- [ ] **Step 4: Audit privacy, scope, and phase gate**

Search a seeded secret and seeded prompt/tool strings across the SQLite file and rendered diagnostics; none may appear. Then run:

```bash
git diff --check
git diff --exit-code codex/typed-coordination...HEAD -- \
  travis/agent/agent_loop.py travis/ai/providers \
  packages/travis234-cli packages/travis234-mcp-adapter
git status --short --branch
```

Do not build or smoke a container. Record exact evidence and use verified `HEAD` as the only Phase 5 base.
