# OpenAI Codex Generation Capability Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the ChatGPT OAuth Codex Responses route from receiving unsupported sampling fields while preserving configured session values, truthful `/params` warnings, all non-Codex provider behavior, and the Agent context envelope.

**Architecture:** Split `openai_codex_responses` from the generic Responses capability policy and permit only documented Codex generation controls. Add a final wire-schema guard inside `CodexResponsesTransport` so unsupported sampling keys cannot re-enter through direct calls or request overrides. Keep immutable session settings intact; capability filtering affects only provider payload construction.

**Tech Stack:** Python 3, immutable `GenerationParams`, provider capability policy, Codex/OpenAI Responses transports, pytest, Context7 official `/openai/codex` documentation.

## Global Constraints

- Treat the repository root as the only active application tree; do not touch `appv231/` or `hermes-agent/`.
- Preserve all unrelated dirty State Signals files and hunks.
- Modify production code only in `travis/ai/providers/capabilities.py` and `travis/ai/providers/transports.py`.
- Do not modify `travis/agent/`, `travis/coding_agent/`, `travis/compaction/`, session JSONL ownership, model context windows, token estimation, or authentication.
- Preserve configured `GenerationParams`; unsupported Codex values are reported as dropped, never erased.
- Preserve generic `openai_responses`, `azure_openai_responses`, and every non-Codex provider byte-for-byte behaviorally.
- Add every regression before production changes and observe the intended failure.
- Do not add a reactive retry for unsupported parameters.
- Do not commit, push, merge, reset, or otherwise perform mutating git operations unless separately requested by the user.
- Before reporting completion, run Python tests, npm launcher tests, package builds, clean-wheel acceptance, parity verification, live installed-wheel TUI acceptance, and release-container smoke checks.

## Official contract

Context7 library `/openai/codex` resolves the official request type in [`codex-rs/codex-api/src/common.rs`](https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/common.rs). `ResponsesApiRequest` includes reasoning, text, tool, cache, service-tier, and streaming controls; it does not include `temperature`, `top_p`, or `max_output_tokens`.

---

## File responsibility map

- `tests/test_ai_provider_capabilities.py`: defines the exact Codex allowlist and protects generic Responses behavior.
- `tests/test_reference_runtime_contract.py`: proves the final Codex body cannot contain unsupported sampling keys even when injected directly.
- `tests/test_tui_runtime_compaction_and_models.py`: reproduces inherited CLI temperature plus session reset and proves warnings/state/context isolation.
- `travis/ai/providers/capabilities.py`: owns provider-aware filtering and `dropped` warnings.
- `travis/ai/providers/transports.py`: owns the final serialized Codex request schema.

---

### Task 1: Lock the provider defect and isolation requirements with failing tests

**Files:**
- Modify: `tests/test_ai_provider_capabilities.py:49-143`
- Modify: `tests/test_reference_runtime_contract.py:930-960`
- Modify: `tests/test_tui_runtime_compaction_and_models.py` beside the existing `/params` capability-warning tests

**Interfaces:**
- Consumes: `build_generation_payload()`, `CodexResponsesTransport.build_kwargs()`, `InteractiveMode._run_params_command()`.
- Produces: executable regression contracts for Tasks 2 and 3.

- [ ] **Step 1: Replace the incorrect Codex capability expectation**

Replace `test_codex_responses_payload_uses_response_native_fields()` with:

```python
def test_codex_responses_uses_only_documented_generation_fields() -> None:
    payload = build_generation_payload(
        provider="openai-codex",
        api_mode="openai_codex_responses",
        params=GenerationParams(
            temperature=0.1,
            top_p=0.95,
            max_tokens=6000,
            stop=("END",),
            frequency_penalty=0.2,
            presence_penalty=0.3,
            seed=7,
            provider_sort="latency",
            parallel_tool_calls=False,
            tool_choice="auto",
        ),
        tools_enabled=True,
    )

    assert payload.temperature is None
    assert payload.max_tokens is None
    assert payload.request_overrides == {
        "parallel_tool_calls": False,
        "tool_choice": "auto",
    }
    assert [(warning.param, warning.action) for warning in payload.warnings] == [
        ("temperature", "dropped"),
        ("top_p", "dropped"),
        ("max_tokens", "dropped"),
        ("stop", "dropped"),
        ("frequency_penalty", "dropped"),
        ("presence_penalty", "dropped"),
        ("seed", "dropped"),
        ("provider_sort", "dropped"),
    ]
```

- [ ] **Step 2: Protect generic OpenAI and Azure Responses behavior**

Add:

```python
@pytest.mark.parametrize("api_mode", ["openai_responses", "azure_openai_responses"])
def test_non_codex_responses_keep_existing_sampling_fields(api_mode: str) -> None:
    payload = build_generation_payload(
        provider="openai" if api_mode == "openai_responses" else "azure-openai-responses",
        api_mode=api_mode,
        params=GenerationParams(temperature=0.1, top_p=0.95, max_tokens=6000),
        tools_enabled=True,
    )

    assert payload.temperature == 0.1
    assert payload.max_tokens == 6000
    assert payload.request_overrides == {"top_p": 0.95}
    assert payload.warnings == []
```

In the existing Codex stop-warning test, remove `top_p=0.9` so the test remains scoped to `stop`. In the disabled-parallel-tools test, remove `top_p=0.9` and expect an empty `request_overrides` mapping plus only the `parallel_tool_calls` warning.

- [ ] **Step 3: Add the direct Codex wire-schema regression**

Extend `test_responses_request_shapes_are_not_conflated()` so its Codex call directly injects every disputed field:

```python
    codex = CodexResponsesTransport().build_kwargs(
        **common,
        request_overrides={"top_p": 0.9, "max_output_tokens": 123},
    )

    assert openai["temperature"] == 0
    assert azure["temperature"] == 0
    assert "temperature" not in codex
    assert "top_p" not in codex
    assert "max_output_tokens" not in codex
```

Keep all existing response-shape assertions.

- [ ] **Step 4: Add the exact TUI reset/model capability reproduction**

Add:

```python
def test_interactive_codex_params_preserve_state_but_drop_unsupported_sampling(
    tmp_path: Path,
) -> None:
    model = Model(
        id="gpt-5.3-codex-spark",
        name="GPT-5.3 Codex Spark",
        api="openai-codex-responses",
        provider="openai-codex",
        base_url="https://chatgpt.com/backend-api",
        reasoning=True,
        context_window=128_000,
        max_tokens=32_000,
    )
    app = CodingApp(
        cwd=str(tmp_path),
        model=model,
        terminal=FakeTerminal(columns=120),
        enable_tui=True,
        session_path=str(tmp_path / "codex-params.jsonl"),
    )
    app.session.set_thinking_level("high")
    mode = InteractiveMode(
        app,
        generation_params=GenerationParams(
            temperature=0.2,
            sources={"temperature": "cli"},
        ),
    )
    before_messages = list(app.messages)
    before_tokens = estimate_tokens(app.messages)

    mode._run_params_command("temperature 0.4")
    mode._run_params_command("reset")

    assert mode.generation_params.temperature == 0.2
    assert mode.generation_params.sources["temperature"] == "cli"
    assert [(warning.param, warning.action) for warning in mode.generation_param_warnings] == [
        ("temperature", "dropped")
    ]
    assert app.session.generation_param_overrides == GenerationParams()
    assert app.session.thinking_level == "high"
    assert app.messages == before_messages
    assert estimate_tokens(app.messages) == before_tokens
```

- [ ] **Step 5: Run the regressions and verify the intended RED state**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_ai_provider_capabilities.py \
  tests/test_reference_runtime_contract.py::test_responses_request_shapes_are_not_conflated \
  tests/test_tui_runtime_compaction_and_models.py::test_interactive_codex_params_preserve_state_but_drop_unsupported_sampling
```

Expected:

- Codex capability test fails because temperature, `top_p`, and `max_tokens` are still forwarded.
- Codex wire test fails because `temperature` and injected override keys remain in the body.
- TUI isolation test fails only on the absent `temperature dropped` warning; state/message/token assertions remain valid.
- Generic OpenAI/Azure invariant tests pass.

Do not modify production code until these failures are observed and recorded.

---

### Task 2: Split the Codex capability contract from generic Responses

**Files:**
- Modify: `travis/ai/providers/capabilities.py:31-124`
- Test: `tests/test_ai_provider_capabilities.py`
- Test: `tests/test_tui_runtime_compaction_and_models.py`

**Interfaces:**
- Consumes: immutable `GenerationParams`, `_copy_supported()`, `_warn_if_set()`, `_drop_parallel_tools_without_tools()`.
- Produces: `GenerationPayload` containing only documented Codex controls and warnings for every unsupported configured field.

- [ ] **Step 1: Define explicit Codex field tuples**

Add beside `_RESPONSES_COMMON`:

```python
_CODEX_RESPONSES_SUPPORTED = ("parallel_tool_calls", "tool_choice")
_CODEX_RESPONSES_UNSUPPORTED = (
    "temperature",
    "top_p",
    "max_tokens",
    "stop",
    "frequency_penalty",
    "presence_penalty",
    "seed",
    "provider_sort",
)
```

- [ ] **Step 2: Add the exact Codex capability branch**

Insert before the generic Responses branch:

```python
    if api_mode == "openai_codex_responses":
        _copy_supported(params, request_overrides, _CODEX_RESPONSES_SUPPORTED)
        _drop_parallel_tools_without_tools(
            params,
            request_overrides,
            warnings,
            tools_enabled=tools_enabled,
        )
        for name in _CODEX_RESPONSES_UNSUPPORTED:
            _warn_if_set(
                params,
                warnings,
                name,
                "dropped",
                f"Codex Responses does not accept {name}.",
            )
        return GenerationPayload(
            request_overrides=request_overrides,
            warnings=warnings,
        )
```

Narrow the existing generic branch from:

```python
    if api_mode in {"openai_responses", "azure_openai_responses", "openai_codex_responses"}:
```

to:

```python
    if api_mode in {"openai_responses", "azure_openai_responses"}:
```

Do not alter the generic branch body.

- [ ] **Step 3: Run capability and TUI tests to verify GREEN**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_ai_provider_capabilities.py \
  tests/test_tui_runtime_compaction_and_models.py::test_interactive_codex_params_preserve_state_but_drop_unsupported_sampling
```

Expected: all selected tests pass. The direct wire regression must still fail until Task 3, demonstrating that the two boundaries are independently tested.

---

### Task 3: Enforce the official Codex schema at final serialization

**Files:**
- Modify: `travis/ai/providers/transports.py:1999-2018`
- Test: `tests/test_reference_runtime_contract.py`

**Interfaces:**
- Consumes: `temperature`, `max_tokens`, and `request_overrides` accepted by the common transport interface.
- Produces: a Codex request body that cannot contain `temperature`, `top_p`, or `max_output_tokens`.

- [ ] **Step 1: Remove direct Codex temperature serialization**

Delete only:

```python
        if temperature is not None and profile.fixed_temperature is not OMIT_TEMPERATURE:
            body["temperature"] = profile.fixed_temperature if profile.fixed_temperature is not None else temperature
```

Keep the method signature unchanged for transport-interface compatibility.

- [ ] **Step 2: Add the post-override wire guard**

Immediately after applying `request_overrides`, add:

```python
        for unsupported_field in ("temperature", "top_p", "max_output_tokens"):
            body.pop(unsupported_field, None)
```

This code executes only in `CodexResponsesTransport.build_kwargs()`. `OpenAIResponsesTransport` and `AzureOpenAIResponsesTransport` use their own overriding implementations.

- [ ] **Step 3: Run the wire contract test to verify GREEN**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_reference_runtime_contract.py::test_responses_request_shapes_are_not_conflated \
  tests/test_reference_runtime_contract.py::test_provider_specific_options_reach_each_wire_payload
```

Expected: both tests pass; Codex omits unsupported fields while reasoning, service tier, text verbosity, and tool choice remain unchanged.

- [ ] **Step 4: Run all provider-focused tests**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_ai_provider_capabilities.py \
  tests/test_ai_generation_params.py \
  tests/test_reference_runtime_contract.py \
  tests/test_tui_runtime_compaction_and_models.py
```

Expected: all selected tests pass with no warnings or provider-shape regressions.

---

### Task 4: Verify microscopic scope and full repository health

**Files:**
- No production modifications
- Review only the five approved test/production files and the design/plan documents

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: evidence that provider correction does not affect sessions, context accounting, other providers, packaging, or release runtime.

- [ ] **Step 1: Prove the filesystem scope**

Run:

```bash
git diff --check
git diff --name-only
git diff -- travis/agent travis/coding_agent travis/compaction
```

Expected:

- `git diff --check` emits nothing.
- The final command emits nothing for this fix.
- Existing unrelated State Signals changes remain present but untouched.

- [ ] **Step 2: Run the complete Python suite**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests -q
```

Expected: the entire suite passes.

- [ ] **Step 3: Run launcher and package gates**

Run:

```bash
npm test
npm pack --dry-run
.venv/bin/python -m build --outdir /tmp/travis234-codex-capability-build
```

Expected: npm launcher tests pass, npm dry-run lists only intended package files, and wheel/sdist build successfully.

- [ ] **Step 4: Run clean-wheel and parity acceptance**

Create a fresh temporary virtual environment, install the new wheel, verify `travis234 --help`, and import the modified capability and transport modules. Then run:

```bash
.venv/bin/python scripts/verify_acceptance.py --parity-json
```

Expected: clean-wheel entrypoint/imports pass and parity reports zero invalid results.

- [ ] **Step 5: Run installed-wheel live TUI acceptance**

Follow the README user-side TUI protocol with `.env` and existing credential storage without printing credentials:

1. Start with `temperature=0.2` configured.
2. Select `openai-codex/gpt-5.3-codex-spark`.
3. Run `/params`; expect the value to remain visible with `temperature dropped`.
4. Send `Reply with exactly: CODEX-PARAMS-OK`; expect exact success and no unsupported-parameter error.
5. Run `/params reset`; expect inherited CLI temperature to remain visible and dropped.
6. Send `Reply with exactly: CODEX-RESET-OK`; expect exact success.
7. Switch to a compatible non-Codex provider; confirm the same inherited temperature has no Codex warning and a prompt succeeds.

- [ ] **Step 6: Build and smoke the release container**

Run:

```bash
docker build --no-cache -f Dockerfile.release -t travis234:codex-capability-smoke .
docker run --rm --entrypoint id travis234:codex-capability-smoke -un
docker run --rm travis234:codex-capability-smoke --help
docker run --rm --entrypoint python travis234:codex-capability-smoke -c "from travis.ai.providers.capabilities import build_generation_payload; from travis.ai.providers.transports import CodexResponsesTransport; print('codex-capability-import-ok')"
```

Expected: image builds, runtime user is `travis`, CLI help succeeds, and imports print `codex-capability-import-ok`.

- [ ] **Step 7: Stop before git operations**

Report the exact production/test files changed and verification evidence. Leave all changes uncommitted unless the user separately asks for commit or push.
