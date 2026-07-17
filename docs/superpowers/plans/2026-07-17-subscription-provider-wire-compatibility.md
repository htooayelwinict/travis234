# Subscription Provider Wire Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Do not dispatch subagents unless the user explicitly authorizes them.

**Goal:** Repair confirmed Codex, Claude Code, and Copilot-Claude wire requests without modifying Travis234's agent loop, session state, context envelopes, or compaction behavior.

**Architecture:** Keep static model restrictions in Pi-style catalog compatibility flags, translate provider vocabulary in the existing capability builder, and apply state-dependent invariants at the final provider wire body. Codex receives its system prompt directly through the Responses `instructions` field; Anthropic request guards run after request overrides so unsupported fields cannot be reintroduced.

**Tech Stack:** Python 3.13, dataclasses, pytest, Travis234 provider transports, pinned JSON model catalog, uv/build, npm launcher tests, Docker release smoke.

## Global Constraints

- Runtime edits are restricted to `travis/ai/providers/capabilities.py`, `travis/ai/providers/transports.py`, and `travis/ai/builtin_models.json`.
- Do not modify the agent loop, `AgentSession`, session persistence, system-prompt construction, context estimation, context envelopes, token accounting, compaction, iteration budgeting, bounded parallelism, TUI lifecycle, or `/params` persistence.
- Do not modify `travis/ai/providers/provider_request.py`; needing to do so is a stop condition requiring design review.
- Keep Copilot GPT Responses and OpenAI Completions routes unchanged.
- Keep `github-copilot/claude-fable-5` unchanged because it uses the undocumented `openai-completions` route.
- Preserve configured generation parameters in session state; provider guards may only translate or omit fields in the outgoing request.
- Add a failing regression test before each bug fix.
- Do not print credentials, authorization headers, or dotenv values.
- Use the pre-design runtime baseline `e69b370` for rollback.

## File responsibility map

- `travis/ai/providers/capabilities.py`: provider/API-mode translation and early warnings for invalid generic values.
- `travis/ai/providers/transports.py`: final Anthropic and Codex wire-body construction; model/thinking combination invariants.
- `travis/ai/builtin_models.json`: pinned per-model compatibility facts only.
- `tests/test_ai_provider_capabilities.py`: unit contracts for generation-parameter translation and warnings.
- `tests/test_reference_runtime_contract.py`: final request-body contracts for Anthropic, Claude Code, Copilot, and Codex.
- `tests/test_catalog_generation.py`: exact catalog compatibility assertions and refresh-drift protection.
- `docs/superpowers/specs/2026-07-17-subscription-provider-wire-compatibility-design.md`: approved architecture and scope.
- `docs/verification/full-suite.md`: final evidence only after all implementation and release gates pass.

---

### Task 1: Normalize Anthropic capability vocabulary

**Files:**

- Modify: `tests/test_ai_provider_capabilities.py`
- Modify: `travis/ai/providers/capabilities.py:63-96`

**Interfaces:**

- Consumes: `GenerationParams.tool_choice: str | None`, `GenerationParams.temperature: float | None`, provider ID, and API mode.
- Produces: `_anthropic_tool_choice(value: str | None, warnings: list[ProviderParamWarning]) -> dict[str, str] | None` and an Anthropic `GenerationPayload` whose session source object is unchanged.

- [ ] **Step 1: Add failing tool-choice translation tests**

Add these focused tests to `tests/test_ai_provider_capabilities.py`:

```python
@pytest.mark.parametrize(
    ("requested", "expected"),
    [
        ("auto", {"type": "auto"}),
        ("any", {"type": "any"}),
        ("none", {"type": "none"}),
    ],
)
def test_anthropic_accepts_native_tool_choice_values(requested: str, expected: dict[str, str]) -> None:
    payload = build_generation_payload(
        provider="anthropic",
        api_mode="anthropic_messages",
        params=GenerationParams(tool_choice=requested),
        tools_enabled=True,
    )

    assert payload.request_overrides["tool_choice"] == expected
    assert payload.warnings == []


def test_anthropic_translates_required_tool_choice_to_any() -> None:
    payload = build_generation_payload(
        provider="anthropic",
        api_mode="anthropic_messages",
        params=GenerationParams(tool_choice="required"),
        tools_enabled=True,
    )

    assert payload.request_overrides["tool_choice"] == {"type": "any"}
    assert [(warning.param, warning.action) for warning in payload.warnings] == [
        ("tool_choice", "translated")
    ]


def test_anthropic_drops_unknown_tool_choice_instead_of_sending_invalid_type() -> None:
    payload = build_generation_payload(
        provider="anthropic",
        api_mode="anthropic_messages",
        params=GenerationParams(tool_choice="read"),
        tools_enabled=True,
    )

    assert "tool_choice" not in payload.request_overrides
    assert [(warning.param, warning.action) for warning in payload.warnings] == [
        ("tool_choice", "dropped")
    ]
```

- [ ] **Step 2: Run the new tests and verify the existing invalid mapping fails**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_ai_provider_capabilities.py::test_anthropic_accepts_native_tool_choice_values \
  tests/test_ai_provider_capabilities.py::test_anthropic_translates_required_tool_choice_to_any \
  tests/test_ai_provider_capabilities.py::test_anthropic_drops_unknown_tool_choice_instead_of_sending_invalid_type
```

Expected: the native test may pass, while `required` and unknown values fail because the current code emits `{"type": <raw string>}`.

- [ ] **Step 3: Implement the smallest vocabulary helper**

Add to `travis/ai/providers/capabilities.py` near the other private helpers:

```python
_ANTHROPIC_TOOL_CHOICES = {"auto", "any", "none"}


def _anthropic_tool_choice(
    value: str | None,
    warnings: list[ProviderParamWarning],
) -> dict[str, str] | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized == "required":
        warnings.append(
            ProviderParamWarning(
                param="tool_choice",
                action="translated",
                reason="Anthropic names required tool use 'any'.",
            )
        )
        return {"type": "any"}
    if normalized in _ANTHROPIC_TOOL_CHOICES:
        return {"type": normalized}
    warnings.append(
        ProviderParamWarning(
            param="tool_choice",
            action="dropped",
            reason="Anthropic tool_choice must be auto, any, none, or a structured named-tool choice.",
        )
    )
    return None
```

Replace the raw Anthropic mapping with:

```python
        anthropic_tool_choice = _anthropic_tool_choice(params.tool_choice, warnings)
        if anthropic_tool_choice is not None:
            request_overrides["tool_choice"] = anthropic_tool_choice
```

- [ ] **Step 4: Add failing Anthropic temperature-range tests**

Add:

```python
@pytest.mark.parametrize("provider", ["anthropic", "github-copilot"])
def test_subscription_anthropic_route_drops_temperature_above_one(provider: str) -> None:
    params = GenerationParams(temperature=1.5)
    payload = build_generation_payload(
        provider=provider,
        api_mode="anthropic_messages",
        params=params,
        tools_enabled=True,
    )

    assert params.temperature == 1.5
    assert payload.temperature is None
    assert [(warning.param, warning.action) for warning in payload.warnings] == [
        ("temperature", "dropped")
    ]


def test_non_subscription_anthropic_compatible_route_keeps_existing_temperature_policy() -> None:
    payload = build_generation_payload(
        provider="vercel-ai-gateway",
        api_mode="anthropic_messages",
        params=GenerationParams(temperature=1.5),
        tools_enabled=True,
    )

    assert payload.temperature == 1.5
```

- [ ] **Step 5: Run the range tests and verify they fail only on the intended assertion**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_ai_provider_capabilities.py::test_subscription_anthropic_route_drops_temperature_above_one \
  tests/test_ai_provider_capabilities.py::test_non_subscription_anthropic_compatible_route_keeps_existing_temperature_policy
```

Expected: subscription cases fail because `temperature` is still 1.5; the non-subscription control passes.

- [ ] **Step 6: Add provider-scoped temperature normalization**

Inside the `anthropic_messages` branch, compute the wire temperature without changing `params`:

```python
        temperature = params.temperature
        if (
            provider_id in {"anthropic", "github-copilot"}
            and temperature is not None
            and not 0.0 <= temperature <= 1.0
        ):
            warnings.append(
                ProviderParamWarning(
                    param="temperature",
                    action="dropped",
                    reason="Anthropic Messages temperature must be between 0 and 1.",
                )
            )
            temperature = None
```

Return `temperature=temperature` instead of `params.temperature`.

- [ ] **Step 7: Run the complete capability module**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_ai_provider_capabilities.py
```

Expected: all tests pass, including existing Codex and OpenRouter controls.

- [ ] **Step 8: Commit Task 1**

```bash
git add travis/ai/providers/capabilities.py tests/test_ai_provider_capabilities.py
git commit -m "fix: normalize Anthropic generation parameters"
```

---

### Task 2: Pin subscription Claude sampling compatibility

**Files:**

- Modify: `tests/test_catalog_generation.py`
- Modify: `travis/ai/builtin_models.json`

**Interfaces:**

- Consumes: existing `Model.compat` dictionaries loaded from `builtin_models.json`.
- Produces: `supportsTemperature: false` and `supportsTopP: false` only on the documented direct Anthropic and Copilot Anthropic-message model entries.

- [ ] **Step 1: Add a failing exact catalog test**

Add to `tests/test_catalog_generation.py`:

```python
def test_subscription_claude_sampling_flags_are_pinned_to_anthropic_routes() -> None:
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads((root / "travis/ai/builtin_models.json").read_text(encoding="utf-8"))

    restricted = {
        "anthropic": [
            "claude-fable-5",
            "claude-opus-4-7",
            "claude-opus-4-8",
            "claude-sonnet-5",
        ],
        "github-copilot": [
            "claude-opus-4.7",
            "claude-opus-4.8",
            "claude-sonnet-5",
        ],
    }
    for provider, model_ids in restricted.items():
        for model_id in model_ids:
            record = catalog[provider][model_id]
            assert record["api"] == "anthropic-messages"
            assert record["compat"]["supportsTemperature"] is False
            assert record["compat"]["supportsTopP"] is False

    copilot_fable = catalog["github-copilot"]["claude-fable-5"]
    assert copilot_fable["api"] == "openai-completions"
    assert "supportsTemperature" not in copilot_fable["compat"]
    assert "supportsTopP" not in copilot_fable["compat"]
```

- [ ] **Step 2: Run the catalog test and verify missing flags fail**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_catalog_generation.py::test_subscription_claude_sampling_flags_are_pinned_to_anthropic_routes
```

Expected: FAIL on Fable 5/Sonnet 5 missing `supportsTemperature` and all listed models missing `supportsTopP`.

- [ ] **Step 3: Update only the seven catalog entries**

Mechanically rewrite the one-line JSON while preserving compact formatting and the terminating newline. For each listed entry, merge these fields into `compat`:

```json
{
  "supportsTemperature": false,
  "supportsTopP": false
}
```

Do not replace existing fields such as `forceAdaptiveThinking`. Do not update any model outside the exact table in the test. Do not update Copilot Fable 5.

- [ ] **Step 4: Prove the catalog diff is exact**

Run:

```bash
git diff --word-diff=porcelain -- travis/ai/builtin_models.json | \
  rg 'supportsTemperature|supportsTopP|forceAdaptiveThinking|claude-'
```

Expected: additions only for the seven intended entries; no deletion of existing compatibility fields.

- [ ] **Step 5: Run catalog and model-loading tests**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_catalog_generation.py \
  tests/test_reference_runtime_contract.py::test_every_static_model_api_has_a_concrete_transport
```

Expected: all tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add travis/ai/builtin_models.json tests/test_catalog_generation.py
git commit -m "fix: pin Claude subscription sampling capabilities"
```

---

### Task 3: Enforce final Anthropic wire invariants

**Files:**

- Modify: `tests/test_reference_runtime_contract.py`
- Modify: `travis/ai/providers/transports.py:1693-1816`

**Interfaces:**

- Consumes: final Anthropic request body, `model_compat`, `reasoning_config`, and `target_model.thinking_level_map`.
- Produces: `_anthropic_allows_disabled_thinking(target_model: Any) -> bool` and `_apply_anthropic_wire_compatibility(body: dict[str, Any], *, compat: dict[str, Any], thinking_enabled: bool) -> None`.

- [ ] **Step 1: Add failing latest-model sampling wire tests**

Add a small local helper in `tests/test_reference_runtime_contract.py` that selects a built-in model and calls `AnthropicMessagesTransport.build_kwargs`. Then add:

```python
@pytest.mark.parametrize(
    ("provider", "model_id"),
    [
        ("anthropic", "claude-fable-5"),
        ("anthropic", "claude-opus-4-7"),
        ("anthropic", "claude-opus-4-8"),
        ("anthropic", "claude-sonnet-5"),
        ("github-copilot", "claude-opus-4.7"),
        ("github-copilot", "claude-opus-4.8"),
        ("github-copilot", "claude-sonnet-5"),
    ],
)
def test_subscription_claude_wire_drops_unsupported_sampling(
    provider: str,
    model_id: str,
) -> None:
    model = next(
        item for item in load_builtin_models()
        if item.provider == provider and item.id == model_id
    )
    body = AnthropicMessagesTransport().build_kwargs(
        model=model.id,
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        profile=ProviderProfile(name=provider),
        stream=True,
        temperature=0.2,
        max_tokens=4096,
        request_overrides={"top_p": 0.8},
        context=Context(messages=[UserMessage(content="hello")]),
        target_model=model,
        model_compat=model.compat,
    )

    assert "temperature" not in body
    assert "top_p" not in body
```

- [ ] **Step 2: Run the sampling test and verify `top_p` still leaks**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_reference_runtime_contract.py::test_subscription_claude_wire_drops_unsupported_sampling
```

Expected: FAIL because `request_overrides` currently writes `top_p` after the earlier temperature guard.

- [ ] **Step 3: Add the final wire compatibility helper**

Add near the Anthropic transport helpers:

```python
def _apply_anthropic_wire_compatibility(
    body: dict[str, Any],
    *,
    compat: dict[str, Any],
    thinking_enabled: bool,
) -> None:
    if compat.get("supportsTemperature") is False:
        body.pop("temperature", None)
    if compat.get("supportsTopP") is False:
        body.pop("top_p", None)
    elif thinking_enabled:
        top_p = body.get("top_p")
        if isinstance(top_p, (int, float)) and not 0.95 <= float(top_p) <= 1.0:
            body.pop("top_p", None)

    if not thinking_enabled:
        return
    tool_choice = body.get("tool_choice")
    choice_type = tool_choice.get("type") if isinstance(tool_choice, dict) else tool_choice
    if choice_type in {"any", "tool", "required"}:
        body["tool_choice"] = {"type": "auto"}
```

Call it after `body.update(request_overrides)` and before returning:

```python
        _apply_anthropic_wire_compatibility(
            body,
            compat=compat,
            thinking_enabled=thinking_enabled,
        )
```

- [ ] **Step 4: Add failing thinking/off and combination tests**

Add:

```python
def test_fable_five_off_request_omits_unsupported_disabled_thinking() -> None:
    model = next(
        item for item in load_builtin_models()
        if item.provider == "anthropic" and item.id == "claude-fable-5"
    )
    body = AnthropicMessagesTransport().build_kwargs(
        model=model.id,
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        profile=ProviderProfile(name="anthropic"),
        stream=True,
        temperature=None,
        max_tokens=4096,
        reasoning_config={"enabled": False, "effort": "off"},
        context=Context(messages=[UserMessage(content="hello")]),
        target_model=model,
        model_compat=model.compat,
    )

    assert "thinking" not in body


def test_manual_thinking_drops_invalid_top_p_and_relaxes_forced_tool_choice() -> None:
    model = next(
        item for item in load_builtin_models()
        if item.provider == "anthropic" and item.id == "claude-haiku-4-5"
    )
    body = AnthropicMessagesTransport().build_kwargs(
        model=model.id,
        messages=[{"role": "user", "content": "hello"}],
        tools=[{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
        profile=ProviderProfile(name="anthropic"),
        stream=True,
        temperature=None,
        max_tokens=4096,
        reasoning_config={"enabled": True, "effort": "medium"},
        request_overrides={"top_p": 0.8, "tool_choice": {"type": "any"}},
        context=Context(messages=[UserMessage(content="hello")]),
        target_model=model,
        model_compat=model.compat,
    )

    assert "top_p" not in body
    assert body["tool_choice"] == {"type": "auto"}


def test_manual_thinking_preserves_valid_top_p_and_none_tool_choice() -> None:
    model = next(
        item for item in load_builtin_models()
        if item.provider == "anthropic" and item.id == "claude-haiku-4-5"
    )
    body = AnthropicMessagesTransport().build_kwargs(
        model=model.id,
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        profile=ProviderProfile(name="anthropic"),
        stream=True,
        temperature=None,
        max_tokens=4096,
        reasoning_config={"enabled": True, "effort": "medium"},
        request_overrides={"top_p": 0.95, "tool_choice": {"type": "none"}},
        context=Context(messages=[UserMessage(content="hello")]),
        target_model=model,
        model_compat=model.compat,
    )

    assert body["top_p"] == 0.95
    assert body["tool_choice"] == {"type": "none"}
```

- [ ] **Step 5: Run the new tests and confirm only Fable off remains failing after the helper**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_reference_runtime_contract.py::test_fable_five_off_request_omits_unsupported_disabled_thinking \
  tests/test_reference_runtime_contract.py::test_manual_thinking_drops_invalid_top_p_and_relaxes_forced_tool_choice \
  tests/test_reference_runtime_contract.py::test_manual_thinking_preserves_valid_top_p_and_none_tool_choice
```

Expected: Fable off fails because the current branch emits `thinking: disabled`; combination tests pass after Step 3.

- [ ] **Step 6: Implement Pi-style off-map handling**

Add:

```python
def _anthropic_allows_disabled_thinking(target_model: Any) -> bool:
    mapping = getattr(target_model, "thinking_level_map", None)
    return not (
        isinstance(mapping, dict)
        and "off" in mapping
        and mapping["off"] is None
    )
```

Change only the disabled branch:

```python
            if not thinking_enabled:
                if _anthropic_allows_disabled_thinking(native_model):
                    body["thinking"] = {"type": "disabled"}
```

Do not alter adaptive or manual thinking selection.

- [ ] **Step 7: Add the failing minimum-budget regression**

Add:

```python
def test_manual_thinking_rejects_output_cap_too_small_for_valid_budget() -> None:
    model = next(
        item for item in load_builtin_models()
        if item.provider == "anthropic" and item.id == "claude-haiku-4-5"
    )

    with pytest.raises(
        ValueError,
        match="manual thinking requires max_tokens >= 2048",
    ):
        AnthropicMessagesTransport().build_kwargs(
            model=model.id,
            messages=[{"role": "user", "content": "hello"}],
            tools=[],
            profile=ProviderProfile(name="anthropic"),
            stream=True,
            temperature=None,
            max_tokens=1500,
            reasoning_config={"enabled": True, "effort": "medium"},
            context=Context(messages=[UserMessage(content="hello")]),
            target_model=model,
            model_compat=model.compat,
        )
```

- [ ] **Step 8: Run the budget test and verify the invalid 476-token body causes failure**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_reference_runtime_contract.py::test_manual_thinking_rejects_output_cap_too_small_for_valid_budget
```

Expected: FAIL because no exception is raised and the current body contains `budget_tokens: 476`.

- [ ] **Step 9: Add a local pre-network budget invariant**

Before constructing a manual `thinking` object, add:

```python
                if int(body["max_tokens"]) < 2048:
                    raise ValueError(
                        "Anthropic manual thinking requires max_tokens >= 2048 "
                        "to preserve the 1024-token minimum thinking budget and response reserve."
                    )
```

Keep the existing budget map and `max_tokens - 1024` clamp unchanged for valid caps.

- [ ] **Step 10: Protect Claude Code system identity and prompt composition**

Add or extend a regression using a non-secret fake OAuth-shaped token:

```python
def test_claude_code_wire_guard_preserves_identity_and_travis_system_prompt() -> None:
    model = next(
        item for item in load_builtin_models()
        if item.provider == "anthropic" and item.id == "claude-sonnet-5"
    )
    body = AnthropicMessagesTransport().build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=ProviderProfile(name="anthropic"),
        stream=True,
        temperature=0.2,
        max_tokens=4096,
        request_overrides={"top_p": 0.8},
        context=Context(messages=[UserMessage(content="hello")], system_prompt="SYSTEM_SENTINEL"),
        target_model=model,
        model_compat=model.compat,
        api_key="sk-ant-oat-test-placeholder",
    )

    assert [block["text"] for block in body["system"]] == [
        "You are Claude Code, Anthropic's official CLI for Claude.",
        "SYSTEM_SENTINEL",
    ]
    assert "temperature" not in body
    assert "top_p" not in body
```

Run it once before making any further code change. Expected: it passes after the wire guard, proving the fix did not damage the Claude Code system prompt.

- [ ] **Step 11: Run the focused Anthropic/Copilot contract group**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_ai_provider_capabilities.py \
  tests/test_catalog_generation.py \
  tests/test_reference_runtime_contract.py -k 'anthropic or claude or copilot'
```

Expected: all selected tests pass.

- [ ] **Step 12: Commit Task 3**

```bash
git add travis/ai/providers/transports.py tests/test_reference_runtime_contract.py
git commit -m "fix: enforce Claude subscription wire invariants"
```

---

### Task 4: Restore Codex system instructions

**Files:**

- Modify: `tests/test_reference_runtime_contract.py`
- Modify: `travis/ai/providers/transports.py:1940-1971`

**Interfaces:**

- Consumes: `Context.system_prompt` or compatibility messages with `system`/`developer` roles.
- Produces: `_codex_instructions(context: Context | None, messages: list[dict[str, Any]]) -> str` and a Codex body containing the prompt exactly once.

- [ ] **Step 1: Add the failing native-context sentinel test**

Add:

```python
def test_codex_request_uses_native_context_system_prompt_as_instructions() -> None:
    model = next(
        item for item in load_builtin_models()
        if item.provider == "openai-codex" and item.id == "gpt-5.4"
    )
    context = Context(
        messages=[UserMessage(content="hello")],
        system_prompt="SYSTEM_SENTINEL",
    )
    body = CodexResponsesTransport().build_kwargs(
        model=model.id,
        messages=[
            {"role": "developer", "content": "SYSTEM_SENTINEL"},
            {"role": "user", "content": "hello"},
        ],
        tools=[],
        profile=ProviderProfile(name="openai-codex"),
        stream=True,
        temperature=None,
        max_tokens=None,
        context=context,
        target_model=model,
        model_compat=model.compat,
    )

    assert body["instructions"] == "SYSTEM_SENTINEL"
    assert "SYSTEM_SENTINEL" not in str(body["input"])
```

- [ ] **Step 2: Run the sentinel test and verify the confirmed root cause**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_reference_runtime_contract.py::test_codex_request_uses_native_context_system_prompt_as_instructions
```

Expected: FAIL with `body["instructions"] == "You are a helpful assistant."`.

- [ ] **Step 3: Add context-free fallback tests before implementation**

Add:

```python
def test_codex_request_accepts_developer_instruction_without_native_context() -> None:
    body = CodexResponsesTransport().build_kwargs(
        model="gpt-test",
        messages=[
            {"role": "developer", "content": "DEVELOPER_SENTINEL"},
            {"role": "user", "content": "hello"},
        ],
        tools=[],
        profile=ProviderProfile(name="openai-codex"),
        stream=True,
        temperature=None,
        max_tokens=None,
    )

    assert body["instructions"] == "DEVELOPER_SENTINEL"


def test_codex_request_uses_default_only_without_any_instruction() -> None:
    body = CodexResponsesTransport().build_kwargs(
        model="gpt-test",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        profile=ProviderProfile(name="openai-codex"),
        stream=True,
        temperature=None,
        max_tokens=None,
    )

    assert body["instructions"] == "You are a helpful assistant."
```

- [ ] **Step 4: Run both fallback tests**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_reference_runtime_contract.py::test_codex_request_accepts_developer_instruction_without_native_context \
  tests/test_reference_runtime_contract.py::test_codex_request_uses_default_only_without_any_instruction
```

Expected: developer fallback fails; empty fallback passes.

- [ ] **Step 5: Implement direct Codex instruction resolution**

Add near the Codex transport:

```python
def _codex_instructions(
    context: Context | None,
    messages: list[dict[str, Any]],
) -> str:
    if context is not None and isinstance(context.system_prompt, str) and context.system_prompt.strip():
        return context.system_prompt
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "developer"}:
            continue
        content = _content_to_text(message.get("content"))
        if content.strip():
            return content
    return "You are a helpful assistant."
```

Replace the current `next(...)` scan with:

```python
        instructions = _codex_instructions(context, messages)
```

Do not change `include_system_prompt=False`.

- [ ] **Step 6: Run Codex request and WebSocket contract tests**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_reference_runtime_contract.py -k 'codex and (request or websocket or sse)'
```

Expected: all selected tests pass; no change to continuation, cache key, input delta, or transport selection.

- [ ] **Step 7: Re-run the existing Codex generation-capability tests**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_ai_provider_capabilities.py -k codex \
  tests/test_reference_runtime_contract.py -k 'codex and unsupported'
```

Expected: all tests pass; temperature, `top_p`, and output-token overrides remain absent from the subscription wire body.

- [ ] **Step 8: Commit Task 4**

```bash
git add travis/ai/providers/transports.py tests/test_reference_runtime_contract.py
git commit -m "fix: preserve Travis instructions in Codex requests"
```

---

### Task 5: Prove Copilot containment and provider non-regression

**Files:**

- Modify: `tests/test_reference_runtime_contract.py`

**Interfaces:**

- Consumes: built-in Copilot routes and existing transport selection.
- Produces: regression evidence that only Copilot's explicit Anthropic-message entries changed.

- [ ] **Step 1: Add Copilot GPT Responses preservation test**

Add a final-body test using a built-in Copilot `openai-responses` model:

```python
def test_copilot_gpt_responses_sampling_behavior_is_unchanged() -> None:
    model = next(
        item for item in load_builtin_models()
        if item.provider == "github-copilot" and item.api == "openai-responses"
    )
    body = OpenAIResponsesTransport().build_kwargs(
        model=model.id,
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        profile=ProviderProfile(name="github-copilot"),
        stream=True,
        temperature=0.2,
        max_tokens=2048,
        request_overrides={"top_p": 0.8},
        context=Context(messages=[UserMessage(content="hello")]),
        target_model=model,
        model_compat=model.compat,
    )

    assert body["temperature"] == 0.2
    assert body["top_p"] == 0.8
```

If the existing OpenAI Responses transport intentionally names the output limit differently, assert its current field without changing transport behavior.

- [ ] **Step 2: Add Copilot Fable Completions preservation test**

Add:

```python
def test_copilot_fable_completions_route_is_outside_anthropic_guard() -> None:
    model = next(
        item for item in load_builtin_models()
        if item.provider == "github-copilot" and item.id == "claude-fable-5"
    )

    assert model.api == "openai-completions"
    assert model.compat.get("supportsTemperature") is None
    assert model.compat.get("supportsTopP") is None
```

- [ ] **Step 3: Run the two containment tests before any production edit**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_reference_runtime_contract.py::test_copilot_gpt_responses_sampling_behavior_is_unchanged \
  tests/test_reference_runtime_contract.py::test_copilot_fable_completions_route_is_outside_anthropic_guard
```

Expected: both pass. If either fails, inspect the test setup; do not change production code to force the expected result without a new design review.

- [ ] **Step 4: Run the complete provider contract group**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_ai_provider_capabilities.py \
  tests/test_catalog_generation.py \
  tests/test_reference_runtime_contract.py
```

Expected: all tests pass.

- [ ] **Step 5: Commit Task 5 if it added new regression coverage**

```bash
git add tests/test_reference_runtime_contract.py
git commit -m "test: contain subscription provider compatibility guards"
```

---

### Task 6: Full qualification and documentation

**Files:**

- Modify only after successful verification: `docs/verification/full-suite.md`
- Modify only if user-facing behavior needs explanation: `README.md`

**Interfaces:**

- Consumes: all provider changes and regression tests from Tasks 1-5.
- Produces: release-level evidence with no credentials and no unsupported success claims.

- [ ] **Step 1: Verify the surgical diff boundary**

Run:

```bash
git diff --name-only e69b370..HEAD
```

Expected runtime/test paths are limited to:

```text
travis/ai/providers/capabilities.py
travis/ai/providers/transports.py
travis/ai/builtin_models.json
tests/test_ai_provider_capabilities.py
tests/test_reference_runtime_contract.py
tests/test_catalog_generation.py
docs/superpowers/specs/2026-07-17-subscription-provider-wire-compatibility-design.md
docs/superpowers/plans/2026-07-17-subscription-provider-wire-compatibility.md
```

The final verification record or README may also appear if updated in this task. Any agent/session/context/compaction path is a hard failure requiring rollback and review.

- [ ] **Step 2: Run whitespace and repository hygiene checks**

Run:

```bash
git diff --check
PYTHONPATH=. .venv/bin/python scripts/check_repository_hygiene.py
```

Expected: both exit zero; no forbidden compatibility shim or reference-coupling regression.

- [ ] **Step 3: Run the full Python suite**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests -q
```

Expected: all repository tests pass. Record the exact count and duration; do not reuse an earlier count.

- [ ] **Step 4: Run npm launcher qualification**

Run:

```bash
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Expected: launcher tests pass and the dry-run package contains only the declared release files.

- [ ] **Step 5: Compile and build Python distributions**

Run:

```bash
.venv/bin/python -m compileall -q travis
.venv/bin/python -m build
```

Expected: compile succeeds and `dist/` contains one wheel and one source distribution for the current version.

- [ ] **Step 6: Run the acceptance verifier**

Run:

```bash
.venv/bin/python scripts/verify_acceptance.py --parity-json
```

Expected: exit zero. Confirm provider parity checks did not report agent-loop, context, or compaction drift.

- [ ] **Step 7: Build and smoke the release container**

Run:

```bash
docker build --no-cache -f Dockerfile.release -t travis234:provider-wire-smoke .
.venv/bin/python evals/container_smoke.py --image travis234:provider-wire-smoke
```

Expected: both commands pass; container runs as the existing unprivileged user with isolated Travis state.

- [ ] **Step 8: Perform optional authenticated provider smokes without logging secrets**

Only when the relevant subscription credentials are already configured through supported login flows:

1. Start the installed TUI using the README entry protocol and `.env` only where already supported.
2. Select one Codex model and ask it to identify a harmless sentinel instruction that is present only in the Travis system prompt.
3. Select one Claude Code model and send a greeting with non-default session sampling fields; confirm no 400 and no loss of tools/system prompt.
4. Select Copilot Sonnet 5 and send a greeting with the same fields; confirm no 400.
5. Do not dump payload headers, tokens, `.env`, or credential files.

If credentials are absent, record live verification as not run—not passed.

- [ ] **Step 9: Update the verification record with exact evidence**

Add a dated provider-wire section to `docs/verification/full-suite.md` containing:

- focused and full test counts
- npm launcher and pack outcomes
- wheel/sdist names
- container image smoke outcome
- live provider smoke outcome or explicit not-run status
- statement that agent/session/context/compaction paths were unchanged

Do not edit README unless a user-visible `/params` warning or error message needs documentation.

- [ ] **Step 10: Commit qualification documentation**

```bash
git add docs/verification/full-suite.md README.md
git commit -m "docs: record subscription provider wire verification"
```

If README is unchanged, omit it from `git add`. Do not push or publish without a separate user instruction.

## Stop conditions

Stop implementation and request a new design review if any task appears to require:

- a change to `provider_request.py`
- a change to agent/session/context/compaction code
- dynamic capability ingestion from Copilot's `/models` endpoint
- hard-coded model-name checks in a transport
- changing Copilot GPT/Completions request behavior
- mutating persisted `/params` to suit the selected provider
- automatic model fallback or retry-policy changes
- a catalog-wide regeneration that alters unrelated providers

## Plan self-review

- Spec coverage: Codex instructions, Claude sampling, Fable off semantics, thinking/top-p, forced tools, minimum budget, Claude Code prompt identity, and Copilot containment each map to an explicit test-first task.
- Placeholder scan: no `TBD`, `TODO`, “implement later,” or unspecified test/error-handling steps remain.
- Type consistency: helper names and signatures are consistent across producing and consuming tasks.
- Scope consistency: runtime file allowlist matches the design; `provider_request.py` and all core agent/session/context paths are explicit stop conditions.
- Execution policy: the plan requires inline `executing-plans`; repository rules prohibit unrequested subagents.
