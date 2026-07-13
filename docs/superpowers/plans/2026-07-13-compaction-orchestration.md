# Compaction Orchestration Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route manual, preflight, post-response, overflow, and failed-turn compaction through one public transaction coordinator without changing compaction algorithms or agent-loop behavior.

**Architecture:** Move persistence/message-application logic into `SessionCompactionAdapter`; expand the existing run-quiescence coordinator into `CompactionTransactionCoordinator`; leave `CompactionManager`, compressor, and timing algorithms intact. `CodingApp` and `AgentSession` become thin eligibility/delegation facades and never read private compaction state.

**Tech Stack:** Python 3.13, dataclasses, protocols/callback injection, pytest table-driven differential tests.

## Global Constraints

- `travis/agent/agent_loop.py`, `travis/compaction/compressor.py`, and `travis/compaction/timing.py` are red-zone algorithms.
- Baseline pre-rebrand SHA-256 fingerprints are `20c8a03d2fcb8bc22565f266b20aaf99a71dc69a246c794ae19a2a4f4180f8d1`, `5663eb046031ccf037a2a1fa5330c783cae8c22c7faa5f60ae511586ecb0f633`, and `bcc9cdc6930767a038692597cf7ea0989dcb41990458872b4c482bbc9cd42735` respectively.
- Rebranding/import edits are allowed in red-zone files; algorithmic control flow, summaries, timing gates, ordered results, and budgeting are not.
- Every compaction start event has exactly one end event with the same reason.
- Manual `prepare()` precedes start; deferred manual compaction emits no events.
- Preflight runs inside the active lease and never calls `prepare()`.
- Preflight preserves caller list identity when it applies persisted messages.
- Post-response resets overflow attempts on success, no-op, and exception.
- Overflow end event precedes `agent.continue_`; retry is true only when recovery succeeded.
- Prompt-guardrail failed-turn compaction uses `retain_source_suffix=False`.

---

### Task 1: Capture transaction parity before refactoring

**Files:**
- Create: `tests/compaction/test_transaction_parity.py`
- Create: `tests/compaction/transaction_harness.py`
- Modify: `tests/test_compaction_integration.py` only to share fixtures.

**Interfaces:**
- Produces `TransactionObservation(messages, list_identity_preserved, events, persisted_entries, compression_count, overflow_attempts, continuation_count, raised)`.
- Produces cases for manual, deferred, preflight, post-response, recovered/unrecovered overflow, ordinary/prompt-guardrail failure, no-op/cooldown, and exception.

- [ ] **Step 1: Implement the observation contract**

```python
@dataclass(frozen=True)
class TransactionObservation:
    messages: tuple[tuple[str, str], ...]
    list_identity_preserved: bool
    events: tuple[tuple[str, str, bool, bool, str | None], ...]
    persisted_entries: tuple[dict[str, object], ...]
    compression_count: int
    overflow_attempts: int
    continuation_count: int
    raised: str | None


def normalize_messages(messages) -> tuple[tuple[str, str], ...]:
    return tuple((str(getattr(message, "role", "")), message_text(message)) for message in messages)
```

- [ ] **Step 2: Add table-driven baseline cases**

```python
@pytest.mark.parametrize("case", transaction_cases(), ids=lambda case: case.name)
def test_transaction_behavior_matches_approved_baseline(case: TransactionCase) -> None:
    observation = run_current_transaction(case)
    assert observation == case.expected
```

`transaction_cases()` must contain these exact names:

```python
(
    "manual-compressed", "manual-deferred", "manual-cancelled",
    "preflight-compressed", "preflight-noop", "preflight-exception",
    "post-response-compressed", "post-response-noop", "post-response-exception",
    "overflow-recovered", "overflow-unrecovered", "overflow-exception",
    "failed-turn-threshold", "failed-turn-prompt-guardrail", "failed-turn-cooldown",
)
```

Each expected observation records normalized messages, exact start/end order and
fields, persisted compaction record fields, list identity, continuation count,
compression count, overflow attempts, and exact exception text.

- [ ] **Step 3: Run characterization tests green against the baseline**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/compaction/test_transaction_parity.py tests/test_compaction_integration.py tests/test_compaction_timing.py -q`

Expected: PASS before production changes. If a case cannot be made deterministic,
replace time with the existing fake clock rather than weakening assertions.

- [ ] **Step 4: Commit characterization coverage**

```bash
git add tests/compaction/test_transaction_parity.py tests/compaction/transaction_harness.py tests/test_compaction_integration.py
git commit -m "test: characterize every compaction transaction path"
```

### Task 2: Expose compaction results and session application publicly

**Files:**
- Modify: `travis/compaction/timing.py`
- Modify: `travis/coding_agent/compaction_adapter.py`
- Create: `tests/compaction/test_session_adapter.py`
- Modify: `tests/test_compaction_timing.py`

**Interfaces:**
- `CompactionManager.last_compression_result -> CompressionResult | None` read-only property.
- `SessionCompactionAdapter.begin/end/apply_manual_status/apply_result/replace_messages`.

- [ ] **Step 1: Write failing public-accessor test**

```python
def test_manager_exposes_last_result_read_only(manager) -> None:
    manager.maybe_compress_preflight(messages_over_threshold())
    result = manager.last_compression_result
    assert result is not None
    assert result.compressed is True
    with pytest.raises(AttributeError):
        manager.last_compression_result = None
```

- [ ] **Step 2: Write failing adapter persistence test**

```python
def test_adapter_applies_result_and_restores_persisted_context(session_store, process_context) -> None:
    state = FakeSessionState(messages=source_messages(), thinking_level="medium")
    events = []
    adapter = SessionCompactionAdapter(session_store=session_store, state=state, process_context=process_context, emit=events.append)
    result = compressed_result(summary="summary", first_kept_message_index=1)
    applied = adapter.apply_result(compacted_messages(), result, source_messages=state.messages)
    assert applied == session_store.build_context(default_thinking_level="medium").messages
    assert state.messages == applied
    assert session_store.get_branch()[-1]["type"] == "compaction"
```

- [ ] **Step 3: Run tests to verify red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/compaction/test_session_adapter.py tests/test_compaction_timing.py -k 'last_result or adapter_applies' -q`

Expected: FAIL because manager state is private and adapter lacks persistence ownership.

- [ ] **Step 4: Add the read-only accessor**

```python
@property
def last_compression_result(self) -> CompressionResult | None:
    return self._last_compression_result
```

No other `timing.py` behavior changes.

- [ ] **Step 5: Define adapter state and lifecycle protocols**

`CompactionSessionState` is a Protocol with mutable `messages`,
`thinking_level`, and `session_name` attributes. `SessionCompactionAdapter`
defines these concrete public methods: `begin(reason)`, `end(reason, result,
aborted, will_retry, error_message)`, `apply_manual_status(status,
source_messages)`, `apply_result(compacted_messages, result, source_messages,
retain_source_suffix=True)`, and `replace_messages(messages)`.

Move the current first-kept-entry, parent-entry, session-context-ID, process
detail merge, compaction append, and persisted-context restoration logic from
`AgentSession` into this adapter. `begin()`/`end()` own the running flag and exact
event objects; `end()` clears the flag in `finally`.

- [ ] **Step 6: Run adapter/timing tests green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/compaction/test_session_adapter.py tests/test_compaction_timing.py -q`

Expected: PASS.

- [ ] **Step 7: Commit public application boundary**

```bash
git add travis/compaction/timing.py travis/coding_agent/compaction_adapter.py tests/compaction/test_session_adapter.py tests/test_compaction_timing.py
git commit -m "refactor: expose compaction session application"
```

### Task 3: Implement one compaction transaction coordinator

**Files:**
- Rewrite: `travis/coding_agent/compaction_coordinator.py`
- Create: `tests/compaction/test_transaction_coordinator.py`

**Interfaces:**
- Produces `CompactionOutcome` and `CompactionTransactionCoordinator` with exact methods below.

- [ ] **Step 1: Write failing coordinator contract tests**

```python
def test_preflight_pairs_events_and_preserves_input_list_identity(harness) -> None:
    source = messages_over_threshold()
    identity = id(source)
    outcome = harness.coordinator.preflight(source)
    assert id(outcome.messages) == identity
    assert harness.events == [("start", "threshold"), ("end", "threshold", False, False, None)]


def test_post_response_resets_overflow_attempts_on_exception(harness) -> None:
    harness.manager.fail_post_response = RuntimeError("boom")
    harness.manager.overflow_attempts = 2
    with pytest.raises(RuntimeError, match="boom"):
        harness.coordinator.post_response(messages_with_usage(), prompt_tokens=100)
    assert harness.manager.overflow_attempts == 0
    assert harness.events[-1] == ("end", "threshold", False, False, "Auto-compaction failed: boom")


def test_recovered_overflow_ends_before_continue(harness) -> None:
    trace = harness.trace
    harness.continue_agent = lambda: trace.append("continue")
    outcome = harness.coordinator.recover_overflow(overflow_messages())
    assert outcome.recovered is True and outcome.will_retry is True
    assert trace.index("end:overflow") < trace.index("continue")
```

- [ ] **Step 2: Run coordinator tests red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/compaction/test_transaction_coordinator.py -q`

Expected: FAIL because the unified coordinator/API does not exist.

- [ ] **Step 3: Define result and constructor contracts**

```python
@dataclass(frozen=True)
class CompactionOutcome:
    messages: list[AgentMessage]
    compressed: bool
    recovered: bool = False
    result: object | None = None
    will_retry: bool = False
```

`CompactionTransactionCoordinator` takes keyword-only `manager`,
`run_coordinator`, `adapter`, and `continue_agent` dependencies. It exposes
`manual(focus=None, summarizer=None, deep=False)`, `preflight(messages)`,
`post_response(messages, prompt_tokens)`, `recover_overflow(messages)`, and
`compact_error_context(messages, force, retain_source_suffix=True)` with the
`CompactionOutcome` return contract above.

Keep the existing active-run `CompactionCoordinator.prepare()` helper or rename
it `CompactionRunCoordinator`; inject it instead of duplicating lease logic.

- [ ] **Step 4: Implement one transaction wrapper**

Every method calls this private helper:

```python
def _transaction(self, *, reason: str, operation: Callable[[], CompactionOutcome], failure_prefix: str) -> CompactionOutcome:
    self._adapter.begin(reason)
    try:
        outcome = operation()
    except Exception as error:
        self._adapter.end(reason=reason, result=None, aborted=str(error) == "Compaction cancelled", will_retry=False, error_message=None if str(error) == "Compaction cancelled" else f"{failure_prefix}: {error}")
        raise
    self._adapter.end(reason=reason, result=outcome.result if outcome.compressed else None, aborted=False, will_retry=outcome.will_retry)
    return outcome
```

Manual calls `prepare()` before `_transaction()`. Preflight does not. Each method
uses existing manager methods unchanged, adapts messages through
`to_compressor_messages()`, and applies via `SessionCompactionAdapter`. Post-response
wraps overflow reset in `finally`. Recovered overflow ends its transaction before
calling the injected continuation.

- [ ] **Step 5: Run coordinator tests green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/compaction/test_transaction_coordinator.py tests/compaction/test_session_adapter.py -q`

Expected: PASS.

- [ ] **Step 6: Commit unified coordinator**

```bash
git add travis/coding_agent/compaction_coordinator.py tests/compaction/test_transaction_coordinator.py
git commit -m "refactor: centralize compaction transactions"
```

### Task 4: Migrate all app/session entry paths

**Files:**
- Modify: `travis/app.py`
- Modify: `travis/coding_agent/agent_session.py`
- Modify: `travis/coding_agent/agent_session_services.py`
- Modify: `tests/compaction/test_transaction_parity.py`
- Modify: `tests/test_app_integration.py`
- Modify: `tests/test_coding_agent.py`

**Interfaces:**
- `CodingApp` consumes `CompactionTransactionCoordinator` only.
- `AgentSession.compact()` delegates to `coordinator.manual()`.
- `AgentSession` exposes no begin/end/apply private transaction methods.

- [ ] **Step 1: Add a failing private-access scan**

```python
def test_compaction_callers_use_only_public_coordinator() -> None:
    root = Path(__file__).parents[2] / "travis"
    forbidden = ("session._begin_compaction", "session._end_compaction", "compaction._last_compression_result", "_apply_compaction_boundary")
    failures = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        failures.extend(f"{path}: {token}" for token in forbidden if token in text)
    assert failures == []
```

- [ ] **Step 2: Run scan red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/architecture/test_compaction_boundary.py -q`

Expected: FAIL with all duplicated/private paths in `app.py` and `agent_session.py`.

- [ ] **Step 3: Replace application orchestration with thin wrappers**

`CodingApp._transform_context()` calls `coordinator.preflight(messages)` and
returns `outcome.messages`. `_compact_post_response()` computes eligibility and
prompt tokens, then calls `post_response()`. `_recover_context_overflow()` removes
the overflow error and calls `recover_overflow()`. `_compact_failed_turn_context()`
computes force/retain flags and calls `compact_error_context()`.

Delete `_apply_compaction_boundary()` and all begin/try/apply/end blocks.
`AgentSession.compact()` is:

```python
def compact(self, focus: str | None = None, summarizer=None, deep: bool = False):
    return self.compaction_transactions.manual(focus=focus, summarizer=summarizer, deep=deep)
```

Delete `apply_compaction_result`, `_first_kept_entry_id_for_status`,
`_first_kept_entry_id_for_compaction_result`, `_compaction_parent_entry_id`,
`_session_context_message_entry_ids`, `_begin_compaction`, and `_end_compaction`
from `AgentSession` after the adapter owns them.

- [ ] **Step 4: Run transaction parity green without changing expectations**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/compaction/test_transaction_parity.py tests/compaction/test_transaction_coordinator.py tests/test_compaction_integration.py tests/test_app_integration.py -q`

Expected: PASS with every baseline observation unchanged.

- [ ] **Step 5: Run private-boundary scan green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/architecture/test_compaction_boundary.py -q`

Expected: PASS.

- [ ] **Step 6: Commit entry-path migration**

```bash
git add travis/app.py travis/coding_agent/agent_session.py travis/coding_agent/agent_session_services.py travis/coding_agent/compaction_adapter.py tests/compaction tests/architecture/test_compaction_boundary.py tests/test_app_integration.py tests/test_coding_agent.py
git commit -m "fix: route all compaction through one transaction coordinator"
```

### Task 5: Prove red-zone behavior preservation

**Files:**
- Create: `tests/architecture/test_red_zone.py`
- Modify: `docs/verification/compaction-parity.md`

**Interfaces:**
- Produces normalized AST/hash and behavioral acceptance evidence for the three red-zone algorithm files.

- [ ] **Step 1: Add a red-zone structure test**

```python
def test_red_zone_changes_are_import_and_brand_only() -> None:
    baseline = load_baseline_normalized_asts()
    for relative in ("agent/agent_loop.py", "compaction/compressor.py", "compaction/timing.py"):
        current = normalize_imports_strings_and_comments(ast.parse((ROOT / "travis" / relative).read_text(encoding="utf-8")))
        assert ast.dump(current, include_attributes=False) == baseline[relative]
```

The committed baseline fixtures are generated once from commit `42360ff` and
normalize only import module names, application-brand string literals, comments,
and the new read-only `last_compression_result` property. Control-flow/function
body changes fail.

- [ ] **Step 2: Run complete red-zone suites**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_agent_loop.py tests/test_agent_runtime_hardening.py tests/test_compaction.py tests/test_compaction_timing.py tests/test_compaction_integration.py tests/compaction tests/architecture/test_red_zone.py -q`

Expected: PASS.

- [ ] **Step 3: Record evidence and commit**

`docs/verification/compaction-parity.md` records the exact command, collected
count, passed count, and hashes/normalized-AST result from the current tree.

```bash
git add tests/architecture/test_red_zone.py tests/fixtures/red_zone docs/verification/compaction-parity.md
git commit -m "test: prove red-zone compaction parity"
```
