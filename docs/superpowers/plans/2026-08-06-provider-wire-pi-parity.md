# Provider Wire Pi-Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the two confirmed provider-wire defects by sending Z.AI output limits with the documented field and preserving OpenAI Responses incomplete reasons.

**Architecture:** Keep provider behavior at the existing compatibility and stream-decoder boundaries. Extend the existing Z.AI predicate by one compatibility fact, then pass terminal Responses metadata into the existing pure stop-reason mapper; do not change request orchestration, public message types, or the agent loop.

**Tech Stack:** Python 3.13, pytest, Travis234 provider transports and SSE decoders.

## Global Constraints

- Product and CLI remain `Travis234` and `travis234`; Python imports remain under `travis`.
- Treat the repository root as the only active application tree.
- Preserve user data under `~/.travis234`; introduce no alternate state path or migration alias.
- Keep credentials out of tracked files and command output.
- Add a failing regression before each bug fix.
- Do not modify agent-loop ordering, iteration budgeting, bounded parallel execution, compaction, session persistence, TUI state, or tool scheduling.
- Do not add runtime model-catalog discovery or network-dependent tests.
- Official provider documentation wins over Pi when evidence differs.
- Preserve both documented OpenAI token-limit reason spellings: `max_output_tokens` and the REST-reference spelling `max_tokens`.
- Do not commit, push, publish, or stage either user-owned untracked document.
- Before completion, run focused Python tests, the full Python suite, npm launcher tests, package builds/checks, and the relevant release-container smoke.

---

## File structure

- Modify `travis/ai/providers/openai_compat.py`: add Z.AI to the existing output-token field decision. Do not add model-ID branching.
- Modify `travis/ai/providers/responses_stream.py`: accept and classify `incomplete_details.reason` at the terminal Responses event boundary.
- Modify `tests/test_reference_runtime_contract.py`: final-body and full-stream regressions using existing transport/decoder test patterns.

No new runtime modules, public dataclasses, dependencies, fixtures, or persisted fields are needed.

## Execution preflight

- [ ] **Record the immutable comparison base before Task 1**

```bash
git rev-parse HEAD > /tmp/travis234-provider-wire-plan-base
git status --short --branch
```

Expected: the base file contains the current 40-character commit, and status shows only the two pre-existing user-owned untracked documents.

### Task 1: Correct direct Z.AI output-token fields

**Files:**

- Modify: `tests/test_reference_runtime_contract.py:604-630`
- Modify: `travis/ai/providers/openai_compat.py:75-124`

**Interfaces:**

- Consumes: `ChatCompletionsTransport.build_kwargs(...) -> dict[str, Any]` and `resolve_openai_compat(model: Model) -> OpenAICompat`.
- Produces: detected `OpenAICompat.max_tokens_field == "max_tokens"` for `zai` and `zai-coding-cn`, while explicit `model.compat["maxTokensField"]` continues to win.

- [ ] **Step 1: Add a failing final-body regression for both direct Z.AI routes**

Add the following near the existing output-cap transport tests in `tests/test_reference_runtime_contract.py`:

```python
@pytest.mark.parametrize(
    ("provider", "base_url"),
    [
        ("zai", "https://api.z.ai/api/coding/paas/v4"),
        ("zai-coding-cn", "https://open.bigmodel.cn/api/coding/paas/v4"),
    ],
)
def test_direct_zai_routes_send_output_limit_as_max_tokens(
    provider: str,
    base_url: str,
) -> None:
    body = ChatCompletionsTransport().build_kwargs(
        model="glm-5.2",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        profile=ProviderProfile(
            name=provider,
            base_url=base_url,
            default_max_tokens=8_192,
        ),
        stream=True,
        temperature=None,
        max_tokens=4_096,
        base_url=base_url,
        model_reasoning=True,
    )

    assert body["max_tokens"] == 4_096
    assert "max_completion_tokens" not in body
```

- [ ] **Step 2: Run the regression and confirm the current field is wrong**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_reference_runtime_contract.py::test_direct_zai_routes_send_output_limit_as_max_tokens \
  -q
```

Expected: both parameter cases fail because the body contains `max_completion_tokens` instead of `max_tokens`.

- [ ] **Step 3: Make the minimal compatibility correction**

In `_detect_openai_compat()` change only the existing `use_max_tokens` tuple:

```python
use_max_tokens = any(
    (
        "chutes.ai" in base_url,
        is_moonshot,
        is_cloudflare_gateway,
        is_together,
        is_nvidia,
        is_ant_ling,
        is_zai,
    )
)
```

Do not change `is_zai`, `resolve_openai_compat()`, or catalog metadata. The existing field-by-field explicit compatibility override remains the override mechanism.

- [ ] **Step 4: Add a control regression for the explicit override boundary**

Append this test beside the new Z.AI test:

```python
def test_direct_zai_explicit_max_tokens_field_override_still_wins() -> None:
    base_url = "https://api.z.ai/api/coding/paas/v4"
    body = ChatCompletionsTransport().build_kwargs(
        model="custom-zai-model",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        profile=ProviderProfile(
            name="zai",
            base_url=base_url,
            default_max_tokens=8_192,
        ),
        stream=True,
        temperature=None,
        max_tokens=2_048,
        base_url=base_url,
        model_compat={"maxTokensField": "max_completion_tokens"},
    )

    assert body["max_completion_tokens"] == 2_048
    assert "max_tokens" not in body
```

- [ ] **Step 5: Run the focused compatibility tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_reference_runtime_contract.py::test_direct_zai_routes_send_output_limit_as_max_tokens \
  tests/test_reference_runtime_contract.py::test_direct_zai_explicit_max_tokens_field_override_still_wins \
  -q
```

Expected: 3 parameter cases pass.

- [ ] **Step 6: Commit the isolated Z.AI fix**

```bash
git add \
  tests/test_reference_runtime_contract.py \
  travis/ai/providers/openai_compat.py
git commit -m "fix: send documented ZAI output token field"
```

### Task 2: Preserve OpenAI Responses incomplete reasons

**Files:**

- Modify: `tests/test_reference_runtime_contract.py:2361-2397`
- Modify: `travis/ai/providers/responses_stream.py:44-51`
- Modify: `travis/ai/providers/responses_stream.py:349-367`

**Interfaces:**

- Consumes: terminal Responses payloads with `status` and optional `incomplete_details.reason`.
- Produces: `_map_responses_status(status: str | None, incomplete_reason: str | None = None) -> tuple[str, str | None]`.
- Preserves: `decode_responses_stream(lines, model)` event types, `AssistantMessage.stop_reason`, and existing `ErrorEvent`/`DoneEvent` ownership.

- [ ] **Step 1: Add failing full-stream regressions for incomplete reasons**

Add this helper and parametrized test after `test_responses_stream_preserves_text_signature_and_exact_usage_split`:

```python
def _responses_terminal_message(events):
    terminal = events[-1]
    return terminal.error if hasattr(terminal, "error") else terminal.message


@pytest.mark.parametrize(
    ("incomplete_reason", "expected_stop", "expected_error"),
    [
        ("max_output_tokens", "length", None),
        ("max_tokens", "length", None),
        ("content_filter", "error", "Response incomplete: content_filter"),
        ("future_provider_reason", "error", "Response incomplete: future_provider_reason"),
        (None, "error", "Response incomplete without a provider reason"),
    ],
)
def test_responses_stream_classifies_incomplete_provider_reason(
    incomplete_reason: str | None,
    expected_stop: str,
    expected_error: str | None,
) -> None:
    model = Model(
        id="gpt-5.4",
        name="GPT-5.4",
        api="openai-responses",
        provider="openai",
        base_url="https://api.openai.com/v1",
    )
    details = {} if incomplete_reason is None else {"reason": incomplete_reason}
    payload = {
        "type": "response.incomplete",
        "response": {
            "id": "resp_incomplete",
            "status": "incomplete",
            "incomplete_details": details,
            "output": [],
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "total_tokens": 2,
            },
        },
    }

    message = _responses_terminal_message(
        list(decode_responses_stream([f"data: {json.dumps(payload)}"], model))
    )

    assert message.stop_reason == expected_stop
    assert message.error_message == expected_error
```

- [ ] **Step 2: Run the new regression and confirm unsafe reasons are misclassified**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_reference_runtime_contract.py::test_responses_stream_classifies_incomplete_provider_reason \
  -q
```

Expected: the content-filter, unknown, and missing-reason cases fail because current code reports `length` with no error.

- [ ] **Step 3: Extend the pure status mapper**

Replace `_map_responses_status()` with:

```python
def _map_responses_status(
    status: str | None,
    incomplete_reason: str | None = None,
) -> tuple[str, str | None]:
    if status in (None, "completed", "in_progress", "queued"):
        return "stop", None
    if status == "incomplete":
        if incomplete_reason in {"max_output_tokens", "max_tokens"}:
            return "length", None
        if incomplete_reason:
            return "error", f"Response incomplete: {incomplete_reason}"
        return "error", "Response incomplete without a provider reason"
    if status in ("failed", "cancelled"):
        return "error", f"Provider response status: {status}"
    return "error", f"Provider response status: {status}"
```

Do not add a raw stop-reason field to `AssistantMessage`.

- [ ] **Step 4: Extract the terminal incomplete reason at the decoder boundary**

Immediately before calling `_map_responses_status()` in the terminal response branch, use:

```python
incomplete_details = response.get("incomplete_details")
incomplete_reason = (
    incomplete_details.get("reason")
    if isinstance(incomplete_details, dict)
    and isinstance(incomplete_details.get("reason"), str)
    else None
)
reason, error_message = _map_responses_status(
    response.get("status"),
    incomplete_reason,
)
```

Replace only the existing one-argument mapper call. Do not change usage merging, response IDs, tool-call promotion, or event ordering.

- [ ] **Step 5: Pin the completed-response control**

Add this assertion to `test_responses_stream_preserves_text_signature_and_exact_usage_split` immediately after resolving `message`:

```python
assert message.stop_reason == "stop"
assert message.error_message is None
```

- [ ] **Step 6: Run focused Responses decoder tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_reference_runtime_contract.py::test_responses_stream_preserves_text_signature_and_exact_usage_split \
  tests/test_reference_runtime_contract.py::test_responses_stream_classifies_incomplete_provider_reason \
  -q
```

Expected: 6 parameter/control cases pass.

- [ ] **Step 7: Run the complete provider contract module**

Run:

```bash
.venv/bin/python -m pytest tests/test_reference_runtime_contract.py -q
```

Expected: all reference-runtime provider contracts pass with no changed event ordering.

- [ ] **Step 8: Commit the isolated Responses fix**

```bash
git add \
  tests/test_reference_runtime_contract.py \
  travis/ai/providers/responses_stream.py
git commit -m "fix: preserve Responses incomplete reasons"
```

### Task 3: Qualify the runtime wire changes

**Files:**

- Verify only; modify no runtime file unless a newly failing regression demonstrates a defect in this plan's scope.
- Optionally modify: `docs/verification/full-suite.md` only to record exact observed results after all checks complete.

**Interfaces:**

- Consumes: the commits from Tasks 1 and 2.
- Produces: repository-level evidence that provider-only changes did not affect packaging, launcher behavior, or the release container.

- [ ] **Step 1: Run the focused provider suites**

```bash
.venv/bin/python -m pytest \
  tests/test_ai_provider_capabilities.py \
  tests/ai/providers \
  tests/test_reference_runtime_contract.py \
  -q
```

Expected: all focused provider tests pass.

- [ ] **Step 2: Run the complete Python suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: all Python tests pass with zero failures.

- [ ] **Step 3: Run launcher tests and npm dry-run packaging**

```bash
npm run test:launcher
npm run pack:launcher
```

Expected: launcher tests and npm package dry-run pass.

- [ ] **Step 4: Build and check Python distributions**

```bash
uv build --clear
uv run twine check dist/*
```

Expected: one valid wheel and one valid source distribution are produced for the current version.

- [ ] **Step 5: Build and smoke-test the release container**

```bash
docker build --no-cache -f Dockerfile.release -t travis234:provider-wire-pi-parity .
.venv/bin/python evals/container_smoke.py --image travis234:provider-wire-pi-parity
```

Expected: the image build and unprivileged installed-container smoke both pass without forwarding provider credentials.

- [ ] **Step 6: Confirm the protected source boundaries and worktree state**

Run against the base recorded during execution preflight:

```bash
WIRE_PLAN_BASE="$(cat /tmp/travis234-provider-wire-plan-base)"
git diff --check "$WIRE_PLAN_BASE"..HEAD
git diff --exit-code "$WIRE_PLAN_BASE"..HEAD -- \
  travis/agent \
  travis/compaction \
  travis/coding_agent/session_store.py
git status --short
```

Expected: no whitespace errors, no protected-path changes, and only the two pre-existing user-owned untracked documents remain.

- [ ] **Step 7: Record verification only if requested or required for release evidence**

If updating `docs/verification/full-suite.md`, record exact test counts, build artifact names, and container outcome. Do not claim authenticated provider calls unless they were actually run, and do not include request headers, tokens, or credential paths.

- [ ] **Step 8: Commit verification documentation if it changed**

```bash
git add docs/verification/full-suite.md
git commit -m "docs: record provider wire parity verification"
```

Skip this commit when the verification document is unchanged. Do not push or publish without separate user authorization.

## Stop conditions

Stop and return to design review if implementation appears to require:

- a new public `AssistantMessage` field;
- a change to `provider_request.py` or transport selection;
- a retry, fallback, or compaction behavior change;
- a runtime catalog fetch;
- an agent-loop, session, TUI, tool-ordering, iteration-budget, or bounded-parallel change; or
- support for an additional Pi adapter capability.
