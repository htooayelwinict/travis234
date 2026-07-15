# Canonical Context Envelope and Hermes Compaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Do not use subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate context-usage spikes and compaction poisoning by using one full-request estimator and porting current Hermes compaction lifecycle behavior into Travis's transaction boundary.

**Architecture:** Extend `travis.ai.context_estimate` into the sole request-envelope authority. Add a pure compaction-policy module, while keeping transcript transformation in `ContextCompressor`, scheduling in `CompactionManager`, and persistence in `CompactionCoordinator`.

**Tech Stack:** Python 3.13, dataclasses, existing provider-neutral `Context`/`Usage` types, pytest, faux provider, Pi JSONL v3 sessions.

## Global Constraints

- Hermes commit `af250d84948179834820a62bfd870c0df6f264a1` is the compaction source.
- Preserve Travis compaction transactions and Pi-format session entries.
- Preserve manual compaction, overflow recovery, model-switch recalibration, and deep compaction.
- Context pressure uses prompt/input tokens; billing statistics may use total tokens.
- Add a failing test before each correction.
- Do not perform state-changing Git operations.

---

### Task 1: Separate prompt pressure from total usage

**Files:**
- Modify: `travis/ai/context_estimate.py`
- Modify: `travis/ai/__init__.py`
- Create: `tests/test_context_estimate.py`

**Interfaces:**
- Produces: `calculate_prompt_tokens(usage: Usage) -> int`
- Produces: `calculate_total_tokens(usage: Usage) -> int`
- Produces: componentized `ContextUsageEstimate`

- [ ] **Step 1: Write failing prompt-only usage tests**

```python
def test_prompt_tokens_exclude_generated_output() -> None:
    usage = Usage(input=2_000, output=7_000, cache_read=11_000, cache_write=3_000, total_tokens=23_000)
    assert calculate_prompt_tokens(usage) == 16_000
    assert calculate_total_tokens(usage) == 23_000


def test_prompt_tokens_fall_back_to_input_cache_components() -> None:
    usage = Usage(input=400, output=900, cache_read=200, cache_write=100, total_tokens=0)
    assert calculate_prompt_tokens(usage) == 700
```

- [ ] **Step 2: Run the tests and confirm the functions are absent**

```bash
.venv/bin/python -m pytest -q tests/test_context_estimate.py
```

Expected: collection fails on missing exports.

- [ ] **Step 3: Implement explicit usage helpers**

```python
def calculate_prompt_tokens(usage: Usage) -> int:
    return max(0, int(usage.input or 0)) + max(0, int(usage.cache_read or 0)) + max(0, int(usage.cache_write or 0))


def calculate_total_tokens(usage: Usage) -> int:
    reported = max(0, int(usage.total_tokens or 0))
    if reported:
        return reported
    return calculate_prompt_tokens(usage) + max(0, int(usage.output or 0))
```

- [ ] **Step 4: Make the estimate componentized**

Replace `ContextUsageEstimate` with:

```python
@dataclass(frozen=True)
class ContextUsageEstimate:
    tokens: int
    usage_tokens: int
    trailing_tokens: int
    last_usage_index: int | None
    system_tokens: int = 0
    tool_tokens: int = 0
    message_tokens: int = 0
    confidence: str = "estimated_full_request"
```

Maintain the existing first four fields for callers. Set confidence to `provider_real` only when applicable prompt usage exists, `estimated_trailing` when real usage plus trailing estimates are combined, and `estimated_full_request` when the entire request is estimated.

- [ ] **Step 5: Run the focused estimator tests**

```bash
.venv/bin/python -m pytest -q tests/test_context_estimate.py
```

Expected: all usage-semantics tests pass.

### Task 2: Count the complete request and replay envelope

**Files:**
- Modify: `travis/ai/context_estimate.py`
- Modify: `travis/compaction/compressor.py`
- Test: `tests/test_context_estimate.py`
- Test: `tests/test_compaction.py`

**Interfaces:**
- Consumes: `Context(system_prompt, messages, tools)`
- Produces: complete `estimate_message_tokens(message)`
- Produces: `estimate_messages_tokens(messages: Sequence[Message]) -> int`

- [ ] **Step 1: Write failing tool/replay tests**

Construct assistant messages with text, thinking, tool calls, `reasoning_content`, `reasoning_details`, `codex_reasoning_items`, and `codex_message_items`. Assert every added serialized field increases the estimate. Add a tool-result image and assert it contributes `ESTIMATED_IMAGE_CHARS / CHARS_PER_TOKEN`.

```python
def test_full_request_includes_system_tools_and_replay_fields() -> None:
    assistant = assistant_with_replay_fields()
    context = Context(
        system_prompt="s" * 400,
        messages=[assistant],
        tools=[Tool(name="read", description="d" * 400, parameters={"type": "object"})],
    )
    estimate = estimate_context_tokens(context)
    assert estimate.system_tokens == 100
    assert estimate.tool_tokens > 100
    assert estimate.message_tokens > estimate_text_tokens("visible")
    assert estimate.tokens == estimate.system_tokens + estimate.tool_tokens + estimate.message_tokens
```

- [ ] **Step 2: Port Hermes replay-envelope field accounting**

Serialize each optional provider replay field with `_safe_json()` and add its character length before dividing by four. Count tool call ID, name, arguments, result tool-call ID/name/content, image blocks, and reasoning signatures. Keep sanitization separate from estimation; estimation must reflect transmitted size.

- [ ] **Step 3: Replace compactor text-only estimation**

Make `travis.compaction.compressor.estimate_tokens(messages)` delegate to `estimate_messages_tokens(messages)`. Do not import the compressor from `context_estimate.py`; dependency direction is AI types/estimator into compaction.

- [ ] **Step 4: Add appv231 cross-reference regression**

Create the same synthetic transcript used by `appv231/compaction/compressor.py` and assert the new estimator is strictly greater when reasoning/replay metadata is present. The test names `appv231` only in its explanation; it does not import that tree.

- [ ] **Step 5: Run estimator and compactor token tests**

```bash
.venv/bin/python -m pytest -q tests/test_context_estimate.py tests/test_compaction.py -k 'token or replay or tool or image'
```

Expected: all selected tests pass.

### Task 3: Use one authority in app, session, clamping, and telemetry

**Files:**
- Modify: `travis/app.py`
- Modify: `travis/ai/context_estimate.py`
- Modify: `travis/ai/providers/provider_request.py`
- Modify: `travis/coding_agent/session_persistence.py`
- Modify: `travis/coding_agent/session_turns.py`
- Modify: `travis/coding_agent/eval_trace.py`
- Modify: `travis/compaction/timing.py`
- Test: `tests/test_app_integration.py`
- Test: `tests/test_coding_persistence_and_compaction.py`
- Test: `tests/test_eval_trace.py`

**Interfaces:**
- Consumes: the exact `Context` sent to the provider
- Produces: one context-usage payload with component and confidence fields

- [ ] **Step 1: Write the post-compaction spike regression**

```python
def test_post_compaction_estimate_and_next_prompt_share_envelope(tmp_path: Path) -> None:
    app = make_app_with_large_system_and_tools(tmp_path)
    app.session.compact()
    before = app.session.get_context_usage()
    app.run_turn("ok")
    after = app.session.get_context_usage()

    assert before["confidence"] == "estimated_after_compaction_full_request"
    assert before["tokens"] >= before["systemTokens"] + before["toolTokens"]
    assert after["confidence"] == "provider_real"
    assert abs(after["tokens"] - before["tokens"]) < 5_000
```

- [ ] **Step 2: Replace `_assistant_prompt_tokens()`**

```python
def _assistant_prompt_tokens(message: AssistantMessage) -> int:
    return calculate_prompt_tokens(message.usage)
```

Use the same helper in `session_persistence.py`; delete its private total-token context calculator. Keep total usage only in aggregate statistics.

- [ ] **Step 3: Build one exact context per turn**

In `session_turns.py`, construct the system prompt, messages, and active tools once, run extension context hooks, then hand the same resulting `Context` to preflight compaction, output clamping, and provider request construction. Do not reconstruct tool schemas independently in the footer path.

- [ ] **Step 4: Publish full post-compaction estimates**

After a compaction transaction, estimate the complete current `Context` and store it as the rough baseline. Return component fields in `get_context_usage()` and mark it `estimated_after_compaction_full_request` until post-compaction provider usage exists.

- [ ] **Step 5: Remove duplicated estimator code**

Delete private message-only context functions in `session_persistence.py` after all callers use `travis.ai.context_estimate`. Ensure `clamp_max_tokens_to_context()` and `CompactionManager` consume the same estimate.

- [ ] **Step 6: Run integration tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_context_estimate.py \
  tests/test_app_integration.py -k 'context or compact' \
  tests/test_coding_persistence_and_compaction.py -k context \
  tests/test_eval_trace.py
```

Expected: all selected tests pass and the spike regression remains within its bounded small-follow-up delta.

### Task 4: Pure Hermes-aligned compaction policy

**Files:**
- Create: `travis/compaction/policy.py`
- Modify: `travis/compaction/__init__.py`
- Modify: `travis/compaction/compressor.py`
- Modify: `travis/app.py`
- Create: `tests/test_compaction_policy.py`

**Interfaces:**
- Produces: `CompactionPolicyInput`
- Produces: `CompactionBudget`
- Produces: `calculate_compaction_budget(input: CompactionPolicyInput) -> CompactionBudget`

- [ ] **Step 1: Write threshold table tests**

```python
@pytest.mark.parametrize(
    ("context_window", "max_output", "expected_ratio"),
    [(128_000, 8_192, 0.75), (256_000, 8_192, 0.75), (1_048_576, 8_192, 0.50)],
)
def test_hermes_threshold_bands(context_window: int, max_output: int, expected_ratio: float) -> None:
    budget = calculate_compaction_budget(
        CompactionPolicyInput(context_window=context_window, max_output_tokens=max_output)
    )
    effective = context_window - max_output
    assert budget.trigger_tokens == int(effective * expected_ratio)
    assert budget.tail_target_tokens == int(budget.trigger_tokens * 0.20)
    assert budget.tail_soft_ceiling_tokens == int(budget.tail_target_tokens * 1.5)
```

Add an explicit below-64K route test proving supported fallback behavior rather than rejection.

- [ ] **Step 2: Implement immutable policy types**

```python
@dataclass(frozen=True)
class CompactionPolicyInput:
    context_window: int
    max_output_tokens: int = 0
    model_id: str = ""
    summary_target_ratio: float = 0.20
    summarizer_context_window: int | None = None


@dataclass(frozen=True)
class CompactionBudget:
    effective_input_tokens: int
    trigger_tokens: int
    tail_target_tokens: int
    tail_soft_ceiling_tokens: int
    summary_max_tokens: int
    threshold_ratio: float
    reason: str
```

Implement the current Hermes 50% base, 75% sub-512K floor, small-window fallback, ratio clamp, and output reservation as a pure function. Put tested model-specific overrides in a constant mapping.

- [ ] **Step 3: Connect the policy to compressor construction and model switching**

Replace `_resolve_compaction_window()` returning a naked ratio with a budget calculation. `ContextCompressor` stores the active budget and recalculates it on model switch without discarding summaries or session history.

- [ ] **Step 4: Remove disconnected settings**

Either connect `enabled`, `summaryTargetRatio`, and protection settings to the policy/compressor or remove misleading `reserveTokens`/`keepRecentTokens` from active Travis settings documentation. Preserve parsing only when needed to read existing settings; report ignored legacy fields as diagnostics.

- [ ] **Step 5: Run policy and model-switch tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_compaction_policy.py \
  tests/test_compaction_integration.py -k model_switch \
  tests/test_tui_runtime_compaction_and_models.py -k context
```

Expected: all selected tests pass across small, normal, and 1M windows.

### Task 5: Reversible summaries and decaying protected head

**Files:**
- Modify: `travis/compaction/compressor.py`
- Modify: `travis/coding_agent/compaction_adapter.py`
- Test: `tests/test_compaction.py`
- Test: `tests/test_compaction_integration.py`

**Interfaces:**
- Consumes: previous compaction summary and role-aligned tail
- Produces: clean previous-summary body and retained tail

- [ ] **Step 1: Write the second-compaction contamination regression**

```python
def test_merged_summary_rehydrates_without_retained_tail() -> None:
    merged = f"{SUMMARY_PREFIX}\nSUMMARY BODY\n\n{SUMMARY_END_MARKER}\n\nLATEST USER TASK"
    index, body = ContextCompressor._find_previous_summary([_user(merged)])
    assert index == 0
    assert body == "SUMMARY BODY"
    assert "LATEST USER TASK" not in body
    assert SUMMARY_END_MARKER not in body
```

- [ ] **Step 2: Fix boundary extraction**

Find the first known end marker after the summary prefix, return only text before it as the previous summary, and leave text after it in the retained-tail message. Keep historical markers supported.

- [ ] **Step 3: Add a two-cycle persisted-session test**

Compact, persist, reload, append enough content, compact again, and capture the second summarizer input. Assert it contains prior summary facts once, latest tail only in the new middle/tail position, and no marker text.

- [ ] **Step 4: Port Hermes protected-head decay**

Add:

```python
def _effective_protect_first_n(self, messages: Sequence[Message]) -> int:
    return 0 if self._find_previous_summary(list(messages))[0] >= 0 else self.protect_first_n
```

Use the effective value in head-end calculation. The system prompt remains outside ordinary conversation messages in request construction.

- [ ] **Step 5: Prove obsolete early tasks can be summarized away**

After two compactions, assert the first raw user task is absent from retained raw messages but represented in a prior summary only when still relevant.

- [ ] **Step 6: Run compaction persistence tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_compaction.py \
  tests/test_compaction_integration.py -k 'merge or rehydrate or protect or second'
```

Expected: all selected tests pass.

### Task 6: One cooldown owner and safer fallback semantics

**Files:**
- Modify: `travis/compaction/compressor.py`
- Modify: `travis/compaction/timing.py`
- Modify: `travis/coding_agent/compaction_coordinator.py`
- Modify: `travis/coding_agent/session_store.py`
- Test: `tests/test_compaction_timing.py`
- Test: `tests/test_compaction_integration.py`

**Interfaces:**
- Produces: serializable cooldown/failure details on compaction entries
- Preserves: force/manual compaction clears cooldown explicitly

- [ ] **Step 1: Reverse the current cooldown expectation**

```python
def test_automatic_compaction_does_not_rewrite_during_summary_cooldown() -> None:
    manager, messages, clock = manager_with_failing_summarizer()
    first = manager.run_preflight(messages)
    second = manager.run_preflight(first.messages)
    assert first.summary_fallback is True
    assert second.compressed is False
    assert second.stop_reason == "cooldown"
```

- [ ] **Step 2: Move cooldown ownership into one serializable state**

Keep cooldown state on `ContextCompressor`; make `CompactionManager._in_cooldown()` delegate to it. `should_compress()` returns false during cooldown unless the caller passes a force flag through the transaction API.

- [ ] **Step 3: Persist cooldown details**

Store `summaryCooldownUntil`, `lastSummaryError`, and `summaryFallback` inside compaction `details`. Restore them when rehydrating the latest compaction entry, using wall-clock timestamps for persistence and monotonic time only for in-process durations.

- [ ] **Step 4: Replace repeated stale-task fallback**

The deterministic fallback contains one `Historical user ask` field and this exact warning:

```text
This ask is historical context and is not necessarily outstanding. Follow the newest retained user message.
```

Remove duplicate insertion into in-progress, pending, and remaining-work sections. Derive recent-user focus from the newest retained user messages.

- [ ] **Step 5: Run cooldown and fallback tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_compaction_timing.py -k 'cooldown or fallback' \
  tests/test_compaction_integration.py -k 'cooldown or resume or fallback'
```

Expected: automatic retries do not rewrite during cooldown; manual compaction remains available.

### Task 7: Auxiliary summarizer capacity calibration

**Files:**
- Modify: `travis/compaction/policy.py`
- Modify: `travis/app.py`
- Modify: `travis/coding_agent/model_registry.py`
- Modify: `travis/compaction/compressor.py`
- Test: `tests/test_compaction_policy.py`
- Test: `tests/test_compaction_integration.py`

**Interfaces:**
- Consumes: active and summarizer route capacities
- Produces: a trigger that guarantees the summarization middle can fit

- [ ] **Step 1: Write a smaller-summarizer regression**

```python
def test_smaller_aux_model_lowers_trigger_before_overflow() -> None:
    budget = calculate_compaction_budget(
        CompactionPolicyInput(
            context_window=1_048_576,
            max_output_tokens=8_192,
            summarizer_context_window=128_000,
        )
    )
    assert budget.trigger_tokens < 128_000
    assert budget.reason == "auxiliary_model_capacity"
```

- [ ] **Step 2: Resolve summarizer route capacity through `ModelRegistry`**

Pass the resolved compression model's context window and maximum output to policy construction. Do not infer capacity from the main model.

- [ ] **Step 3: Calibrate transcript middle and summary output**

Reserve the summarizer system prompt, summary instructions, previous summary, and summary output budget. Lower the live trigger so the maximum compacted middle fits the remaining summarizer input window.

- [ ] **Step 4: Emit actionable diagnostics**

Reject an unusable summarizer before a provider call with its model route, required input, and resolved capacity. Preserve the active coding model and allow configured fallback to the main summarizer.

- [ ] **Step 5: Run auxiliary-model tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_compaction_policy.py \
  tests/test_compaction_integration.py -k 'aux or summary_model or capacity'
```

Expected: all selected tests pass with no repeated overflow call.

### Task 8: Full Phase 2 verification

**Files:**
- Modify: `README.md`
- Modify: `docs/verification/acceptance-matrix.md`
- Modify: `evals/session_resume_smoke.py`
- Test: `tests/test_tui_runtime_compaction_and_models.py`

**Interfaces:**
- Consumes: complete Phase 2 behavior
- Produces: long-session context continuity evidence

- [ ] **Step 1: Extend the long-session smoke**

Record pre-compaction full estimate, post-compaction full estimate, and next real prompt usage. Assert the follow-up delta is bounded by the follow-up plus provider estimation tolerance rather than static prompt/tool size.

- [ ] **Step 2: Document the new envelope semantics**

Explain prompt pressure versus billing totals, Hermes threshold bands, small-window support, and why existing sessions need no migration.

- [ ] **Step 3: Run the Phase 2 gate**

```bash
.venv/bin/python -m pytest -q \
  tests/test_context_estimate.py \
  tests/test_compaction_policy.py \
  tests/test_compaction.py \
  tests/test_compaction_timing.py \
  tests/test_compaction_integration.py \
  tests/test_app_integration.py \
  tests/test_tui_runtime_compaction_and_models.py
.venv/bin/python -m evals.session_resume_smoke
```

Expected: all commands exit zero and the smoke reports bounded post-compaction context continuity.

- [ ] **Step 4: Review checkpoint without Git operations**

Run `git diff --check` and inspect `git status --short` read-only. Record evidence without staging or committing.
