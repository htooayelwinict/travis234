# appv231 Provider Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first-class provider control plane for appv231 so model/provider selection, model catalogs, generation parameters, and provider-specific request shaping are explicit, testable, and visible in CLI/TUI.

**Architecture:** Add provider-control primitives in the yellow provider layer, wire them through env/CLI startup, and expose read-only TUI diagnostics before editable TUI controls. Do not change the red-zone agent loop, compaction, stream types, session store, or tool schemas.

**Tech Stack:** Python dataclasses, pytest, existing appv231 provider transports, existing CLI argparse, existing TUI command handling.

---

## Source Basis

Local rules:

- `rules.md` defines provider reliability, model catalogs, and TUI usability as yellow-zone work.
- `rules.md` freezes `appV2.3.1/appv231/ai/types.py`, `appV2.3.1/appv231/ai/stream.py`, `appV2.3.1/appv231/app.py`, agent loop, compaction, and session store. This plan avoids those files.

External references used for design:

- OpenRouter provider routing supports provider ordering, fallback controls, required-parameter routing, sorting, and provider preferences: https://openrouter.ai/docs/guides/routing/provider-selection
- LiteLLM exposes provider-supported OpenAI params and makes unsupported-param behavior explicit through errors or configured dropping: https://docs.litellm.ai/docs/completion/input and https://docs.litellm.ai/docs/completion/drop_params
- Anthropic Messages API has native parameter names such as `max_tokens`, `stop_sequences`, `temperature`, `thinking`, `tools`, and `tool_choice`: https://docs.anthropic.com/en/api/messages
- OpenAI Responses API uses response-native fields such as `max_output_tokens`, `tool_choice`, `tools`, `parallel_tool_calls`, `temperature`, and `top_p`: https://platform.openai.com/docs/api-reference/responses/create

## Scope Boundaries

Allowed files:

- Create: `appV2.3.1/appv231/ai/providers/params.py`
- Create: `appV2.3.1/appv231/ai/providers/capabilities.py`
- Create: `appV2.3.1/tests/test_ai_generation_params.py`
- Create: `appV2.3.1/tests/test_ai_provider_capabilities.py`
- Modify: `appV2.3.1/appv231/ai/env_config.py`
- Modify: `appV2.3.1/appv231/ai/register_builtins.py`
- Modify: `appV2.3.1/appv231/ai/model_resolver.py`
- Modify: `appV2.3.1/appv231/ai/providers/appv2_env.py`
- Modify: `appV2.3.1/appv231/cli.py`
- Modify: `appV2.3.1/appv231/tui/interactive_mode.py`
- Modify: focused existing tests under `appV2.3.1/tests/`
- Create: `docs/architecture/provider-control-plane.md`

Explicitly out of scope:

- Do not edit `appV2.3.1/appv231/agent/agent_loop.py`.
- Do not edit `appV2.3.1/appv231/ai/types.py`.
- Do not edit `appV2.3.1/appv231/ai/stream.py`.
- Do not edit `appV2.3.1/appv231/compaction/`.
- Do not edit `appV2.3.1/appv231/coding_agent/session_store.py`.
- Do not change tool schemas.
- Do not add live provider network tests to the default test suite.
- Do not publish npm or build GHCR as part of this work.

If execution discovers a required change in a red-zone file, stop and ask Lewis for explicit `kernel change` approval with the exact reason and regression tests.

## File Responsibilities

- `appV2.3.1/appv231/ai/providers/params.py`: typed generation parameter model, env/CLI parsing helpers, merge order, display formatting.
- `appV2.3.1/appv231/ai/providers/capabilities.py`: provider/api-mode capability matrix and request-shaping policy.
- `appV2.3.1/appv231/ai/env_config.py`: keep existing env compatibility and expose parsed generation params from dotenv/env.
- `appV2.3.1/appv231/ai/register_builtins.py`: allow provider registration with an already-resolved `ModelConfig`.
- `appV2.3.1/appv231/ai/model_resolver.py`: resolve known provider custom model IDs even when no static model is registered.
- `appV2.3.1/appv231/ai/providers/appv2_env.py`: build provider payloads from `GenerationParams` and `ProviderCapabilities`.
- `appV2.3.1/appv231/cli.py`: add real list commands and generation-param CLI overrides.
- `appV2.3.1/appv231/tui/interactive_mode.py`: add read-only `/params` and provider-aware `/models` display.

## Merge Order Contract

Generation params resolve in this order:

```text
provider defaults < .env < process env < profile defaults < CLI flags < TUI session override
```

For this implementation pass:

```text
provider defaults < .env < process env < CLI flags
```

Profile and editable TUI overrides stay in the interface shape but are not used until the profile system lands.

## Task 1: GenerationParams Schema

**Files:**

- Create: `appV2.3.1/appv231/ai/providers/params.py`
- Create: `appV2.3.1/tests/test_ai_generation_params.py`

- [ ] **Step 1: Write failing tests for parsing, merge order, and display**

Create `appV2.3.1/tests/test_ai_generation_params.py`:

```python
import pytest

from appv231.ai.providers.params import (
    GenerationParams,
    compact_generation_params_display,
    merge_generation_params,
    params_from_mapping,
)


def test_params_from_mapping_parses_common_values() -> None:
    params = params_from_mapping(
        {
            "temperature": "0.25",
            "top_p": "0.9",
            "max_tokens": "4096",
            "timeout_seconds": "75",
            "frequency_penalty": "0.1",
            "presence_penalty": "-0.2",
            "seed": "42",
            "stop": '["END", "STOP"]',
            "provider_sort": "throughput",
        },
        source="cli",
    )

    assert params.temperature == 0.25
    assert params.top_p == 0.9
    assert params.max_tokens == 4096
    assert params.timeout_seconds == 75
    assert params.frequency_penalty == 0.1
    assert params.presence_penalty == -0.2
    assert params.seed == 42
    assert params.stop == ("END", "STOP")
    assert params.provider_sort == "throughput"
    assert params.sources["temperature"] == "cli"


def test_params_from_mapping_treats_blank_and_null_as_unset() -> None:
    params = params_from_mapping(
        {
            "temperature": "",
            "top_p": "null",
            "max_tokens": "none",
            "stop": "",
        },
        source="cli",
    )

    assert params.temperature is None
    assert params.top_p is None
    assert params.max_tokens is None
    assert params.stop == ()
    assert params.sources == {}


def test_params_from_mapping_accepts_comma_stop_list() -> None:
    params = params_from_mapping({"stop": "END, STOP ,DONE"}, source="env")

    assert params.stop == ("END", "STOP", "DONE")
    assert params.sources["stop"] == "env"


def test_params_from_mapping_rejects_invalid_ranges() -> None:
    with pytest.raises(ValueError, match="temperature must be between 0 and 2"):
        params_from_mapping({"temperature": "3"}, source="cli")

    with pytest.raises(ValueError, match="top_p must be between 0 and 1"):
        params_from_mapping({"top_p": "1.5"}, source="cli")

    with pytest.raises(ValueError, match="max_tokens must be positive"):
        params_from_mapping({"max_tokens": "0"}, source="cli")


def test_merge_generation_params_prefers_later_sources() -> None:
    env = GenerationParams(temperature=0.1, top_p=0.8, sources={"temperature": "env", "top_p": "env"})
    cli = GenerationParams(temperature=0.3, max_tokens=8192, sources={"temperature": "cli", "max_tokens": "cli"})

    merged = merge_generation_params(env, cli)

    assert merged.temperature == 0.3
    assert merged.top_p == 0.8
    assert merged.max_tokens == 8192
    assert merged.sources == {"temperature": "cli", "top_p": "env", "max_tokens": "cli"}


def test_compact_generation_params_display_is_secret_free() -> None:
    params = GenerationParams(
        temperature=0.2,
        top_p=0.95,
        max_tokens=4096,
        stop=("END",),
        provider_sort="latency",
        sources={"temperature": "cli", "top_p": "env"},
    )

    assert compact_generation_params_display(params) == (
        "temperature=0.2 (cli), top_p=0.95 (env), max_tokens=4096, "
        "stop=1 sequence, provider_sort=latency"
    )
```

- [ ] **Step 2: Run the focused failing test**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_ai_generation_params.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv231.ai.providers.params'
```

- [ ] **Step 3: Add the minimal params module**

Create `appV2.3.1/appv231/ai/providers/params.py`:

```python
"""Generation parameter parsing and display helpers for provider requests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Mapping


@dataclass(frozen=True)
class GenerationParams:
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    timeout_seconds: float | None = None
    frequency_penalty: float | None = None
    presence_penalty: float | None = None
    seed: int | None = None
    stop: tuple[str, ...] = ()
    provider_sort: str | None = None
    provider_preferences: dict[str, Any] | None = None
    parallel_tool_calls: bool | None = None
    tool_choice: str | None = None
    sources: dict[str, str] = field(default_factory=dict)


_FIELD_NAMES = (
    "temperature",
    "top_p",
    "max_tokens",
    "timeout_seconds",
    "frequency_penalty",
    "presence_penalty",
    "seed",
    "stop",
    "provider_sort",
    "parallel_tool_calls",
    "tool_choice",
)


def params_from_mapping(values: Mapping[str, object], *, source: str) -> GenerationParams:
    parsed: dict[str, object] = {}
    sources: dict[str, str] = {}

    for key in _FIELD_NAMES:
        raw = values.get(key)
        if _is_unset(raw):
            continue
        if key == "temperature":
            parsed[key] = _bounded_float(raw, key, minimum=0, maximum=2)
        elif key == "top_p":
            parsed[key] = _bounded_float(raw, key, minimum=0, maximum=1)
        elif key in {"frequency_penalty", "presence_penalty"}:
            parsed[key] = _bounded_float(raw, key, minimum=-2, maximum=2)
        elif key == "max_tokens":
            parsed[key] = _positive_int(raw, key)
        elif key == "timeout_seconds":
            parsed[key] = _positive_float(raw, key)
        elif key == "seed":
            parsed[key] = int(str(raw))
        elif key == "stop":
            parsed[key] = _stop_tuple(raw)
        elif key == "parallel_tool_calls":
            parsed[key] = _bool_value(raw, key)
        else:
            parsed[key] = str(raw).strip()
        if key == "stop" and parsed[key] == ():
            continue
        sources[key] = source

    return GenerationParams(**parsed, sources=sources)


def merge_generation_params(*items: GenerationParams | None) -> GenerationParams:
    result = GenerationParams()
    for item in items:
        if item is None:
            continue
        updates: dict[str, object] = {}
        sources = dict(result.sources)
        for key in _FIELD_NAMES:
            value = getattr(item, key)
            if value is None:
                continue
            if key == "stop" and value == ():
                continue
            updates[key] = value
            if key in item.sources:
                sources[key] = item.sources[key]
        if item.provider_preferences:
            updates["provider_preferences"] = dict(item.provider_preferences)
        result = replace(result, **updates, sources=sources)
    return result


def compact_generation_params_display(params: GenerationParams) -> str:
    parts: list[str] = []
    for key in (
        "temperature",
        "top_p",
        "max_tokens",
        "timeout_seconds",
        "frequency_penalty",
        "presence_penalty",
        "seed",
        "parallel_tool_calls",
        "tool_choice",
    ):
        value = getattr(params, key)
        if value is None:
            continue
        source = params.sources.get(key)
        suffix = f" ({source})" if source else ""
        parts.append(f"{key}={value}{suffix}")
    if params.stop:
        label = "sequence" if len(params.stop) == 1 else "sequences"
        parts.append(f"stop={len(params.stop)} {label}")
    if params.provider_sort is not None:
        source = params.sources.get("provider_sort")
        suffix = f" ({source})" if source else ""
        parts.append(f"provider_sort={params.provider_sort}{suffix}")
    return ", ".join(parts) if parts else "default generation parameters"


def _is_unset(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"", "none", "null"}
    return False


def _bounded_float(value: object, name: str, *, minimum: float, maximum: float) -> float:
    parsed = float(str(value))
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{name} must be between {minimum:g} and {maximum:g}.")
    return parsed


def _positive_float(value: object, name: str) -> float:
    parsed = float(str(value))
    if parsed <= 0:
        raise ValueError(f"{name} must be positive.")
    return parsed


def _positive_int(value: object, name: str) -> int:
    parsed = int(str(value))
    if parsed <= 0:
        raise ValueError(f"{name} must be positive.")
    return parsed


def _bool_value(value: object, name: str) -> bool:
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false.")


def _stop_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item))
    stripped = str(value).strip()
    if not stripped:
        return ()
    if stripped.startswith("["):
        parsed = json.loads(stripped)
        if not isinstance(parsed, list):
            raise ValueError("stop must be a JSON array or comma-separated list.")
        return tuple(str(item) for item in parsed if str(item))
    return tuple(item.strip() for item in stripped.split(",") if item.strip())
```

- [ ] **Step 4: Run the focused test**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_ai_generation_params.py -q
```

Expected:

```text
6 passed
```

- [ ] **Step 5: Commit Task 1**

Run:

```bash
git add appV2.3.1/appv231/ai/providers/params.py appV2.3.1/tests/test_ai_generation_params.py
git commit -m "feat(appv231): add generation parameter model"
```

## Task 2: Provider Capability Matrix

**Files:**

- Create: `appV2.3.1/appv231/ai/providers/capabilities.py`
- Create: `appV2.3.1/tests/test_ai_provider_capabilities.py`

- [ ] **Step 1: Write failing capability tests**

Create `appV2.3.1/tests/test_ai_provider_capabilities.py`:

```python
from appv231.ai.providers.capabilities import build_generation_payload
from appv231.ai.providers.params import GenerationParams


def test_openrouter_payload_preserves_routing_preferences() -> None:
    payload = build_generation_payload(
        provider="openrouter",
        api_mode="chat_completions",
        params=GenerationParams(
            temperature=0.2,
            top_p=0.9,
            max_tokens=4096,
            provider_sort="throughput",
        ),
        tools_enabled=True,
    )

    assert payload.temperature == 0.2
    assert payload.max_tokens == 4096
    assert payload.provider_preferences == {"sort": "throughput", "allow_fallbacks": True}
    assert payload.request_overrides == {"top_p": 0.9}
    assert payload.warnings == []


def test_anthropic_translates_stop_and_drops_unsupported_penalties() -> None:
    payload = build_generation_payload(
        provider="anthropic",
        api_mode="anthropic_messages",
        params=GenerationParams(
            temperature=0.4,
            top_p=0.8,
            max_tokens=2000,
            stop=("END",),
            frequency_penalty=0.3,
            seed=123,
        ),
        tools_enabled=True,
    )

    assert payload.temperature == 0.4
    assert payload.max_tokens == 2000
    assert payload.request_overrides == {"top_p": 0.8, "stop_sequences": ["END"]}
    assert [warning.param for warning in payload.warnings] == ["frequency_penalty", "seed"]
    assert all(warning.action == "dropped" for warning in payload.warnings)


def test_codex_responses_payload_uses_response_native_fields() -> None:
    payload = build_generation_payload(
        provider="openai-codex",
        api_mode="codex_responses",
        params=GenerationParams(
            temperature=0.1,
            top_p=0.95,
            max_tokens=6000,
            parallel_tool_calls=False,
            tool_choice="auto",
        ),
        tools_enabled=True,
    )

    assert payload.temperature == 0.1
    assert payload.max_tokens == 6000
    assert payload.request_overrides == {
        "top_p": 0.95,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
    }
    assert payload.warnings == []


def test_stepfun_uses_conservative_openai_compatible_policy() -> None:
    payload = build_generation_payload(
        provider="stepfun",
        api_mode="chat_completions",
        params=GenerationParams(
            temperature=0.2,
            top_p=0.9,
            max_tokens=8192,
            presence_penalty=0.1,
        ),
        tools_enabled=True,
    )

    assert payload.temperature == 0.2
    assert payload.max_tokens == 8192
    assert payload.request_overrides == {"top_p": 0.9, "presence_penalty": 0.1}
    assert payload.warnings == []
```

- [ ] **Step 2: Run failing capability tests**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_ai_provider_capabilities.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv231.ai.providers.capabilities'
```

- [ ] **Step 3: Add capability matrix and payload builder**

Create `appV2.3.1/appv231/ai/providers/capabilities.py`:

```python
"""Provider generation-parameter capability policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from appv231.ai.providers.params import GenerationParams


@dataclass(frozen=True)
class ProviderParamWarning:
    param: str
    action: str
    reason: str


@dataclass(frozen=True)
class GenerationPayload:
    temperature: float | None = None
    max_tokens: int | None = None
    provider_preferences: dict[str, Any] | None = None
    request_overrides: dict[str, Any] = field(default_factory=dict)
    warnings: list[ProviderParamWarning] = field(default_factory=list)


_CHAT_COMMON = {
    "top_p",
    "frequency_penalty",
    "presence_penalty",
    "seed",
    "stop",
    "parallel_tool_calls",
    "tool_choice",
}
_ANTHROPIC_DIRECT = {"top_p", "stop"}
_RESPONSES_COMMON = {"top_p", "parallel_tool_calls", "tool_choice"}


def build_generation_payload(
    *,
    provider: str,
    api_mode: str,
    params: GenerationParams,
    tools_enabled: bool,
) -> GenerationPayload:
    provider_id = provider.lower()
    request_overrides: dict[str, Any] = {}
    warnings: list[ProviderParamWarning] = []

    if api_mode == "anthropic_messages":
        _copy_supported(params, request_overrides, _ANTHROPIC_DIRECT)
        if params.stop:
            request_overrides["stop_sequences"] = list(params.stop)
        _warn_if_set(params, warnings, "frequency_penalty", "dropped", "Anthropic Messages does not support frequency_penalty.")
        _warn_if_set(params, warnings, "presence_penalty", "dropped", "Anthropic Messages does not support presence_penalty.")
        _warn_if_set(params, warnings, "seed", "dropped", "Anthropic Messages does not support seed.")
        _warn_if_set(params, warnings, "parallel_tool_calls", "dropped", "Anthropic parallel tool control uses tool_choice.disable_parallel_tool_use.")
        if params.tool_choice is not None:
            request_overrides["tool_choice"] = {"type": params.tool_choice}
        return GenerationPayload(
            temperature=params.temperature,
            max_tokens=params.max_tokens,
            request_overrides=request_overrides,
            warnings=warnings,
        )

    if api_mode == "codex_responses":
        _copy_supported(params, request_overrides, _RESPONSES_COMMON)
        if params.stop:
            _warn_if_set(params, warnings, "stop", "dropped", "Responses transport does not expose stop in appv231 yet.")
        _warn_if_set(params, warnings, "frequency_penalty", "dropped", "Responses transport does not expose frequency_penalty in appv231 yet.")
        _warn_if_set(params, warnings, "presence_penalty", "dropped", "Responses transport does not expose presence_penalty in appv231 yet.")
        _warn_if_set(params, warnings, "seed", "dropped", "Responses transport does not expose seed in appv231 yet.")
        return GenerationPayload(
            temperature=params.temperature,
            max_tokens=params.max_tokens,
            request_overrides=request_overrides,
            warnings=warnings,
        )

    _copy_supported(params, request_overrides, _CHAT_COMMON)
    provider_preferences = None
    if provider_id == "openrouter" and params.provider_sort:
        provider_preferences = {"sort": params.provider_sort, "allow_fallbacks": True}
    if params.stop:
        request_overrides["stop"] = list(params.stop)
    if params.parallel_tool_calls is not None and not tools_enabled:
        warnings.append(
            ProviderParamWarning(
                param="parallel_tool_calls",
                action="dropped",
                reason="parallel_tool_calls has no effect when tools are disabled.",
            )
        )
        request_overrides.pop("parallel_tool_calls", None)
    return GenerationPayload(
        temperature=params.temperature,
        max_tokens=params.max_tokens,
        provider_preferences=provider_preferences,
        request_overrides=request_overrides,
        warnings=warnings,
    )


def _copy_supported(params: GenerationParams, target: dict[str, Any], names: set[str]) -> None:
    for name in names:
        value = getattr(params, name)
        if value is None:
            continue
        if name == "stop":
            continue
        target[name] = value


def _warn_if_set(
    params: GenerationParams,
    warnings: list[ProviderParamWarning],
    name: str,
    action: str,
    reason: str,
) -> None:
    value = getattr(params, name)
    if value is None:
        return
    if name == "stop" and value == ():
        return
    warnings.append(ProviderParamWarning(param=name, action=action, reason=reason))
```

- [ ] **Step 4: Run capability tests**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_ai_provider_capabilities.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 5: Commit Task 2**

Run:

```bash
git add appV2.3.1/appv231/ai/providers/capabilities.py appV2.3.1/tests/test_ai_provider_capabilities.py
git commit -m "feat(appv231): add provider generation capabilities"
```

## Task 3: Env Config Integration

**Files:**

- Modify: `appV2.3.1/appv231/ai/env_config.py`
- Modify: `appV2.3.1/tests/test_ai_env_config.py`

- [ ] **Step 1: Add failing tests for StepFun env metadata and params export**

Update the import from `appv231.ai.env_config` in `appV2.3.1/tests/test_ai_env_config.py` to include `find_env_keys`, then append the new tests:

```python
from appv231.ai.providers.params import GenerationParams


def test_stepfun_env_metadata_is_registered() -> None:
    assert get_default_model_for_provider("stepfun") == "step-3.7-flash"
    assert find_env_keys("stepfun") is None


def test_model_config_exposes_generation_params(tmp_path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "APPV2_WORKER_LLM_ENABLED=true",
                "APPV2_WORKER_LLM_API_KEY=test-key",
                "APPV2_WORKER_LLM_PROVIDER_SORT=throughput",
                "APPV2_WORKER_LLM_TEMPERATURE=0.2",
                "APPV2_WORKER_LLM_TOP_P=0.9",
                "APPV2_WORKER_LLM_MAX_TOKENS=4096",
                "APPV2_WORKER_LLM_STOP=END,STOP",
            ]
        ),
        encoding="utf-8",
    )

    config = load_model_config("APPV2_WORKER_LLM", dotenv)

    assert config.generation_params == GenerationParams(
        temperature=0.2,
        top_p=0.9,
        max_tokens=4096,
        stop=("END", "STOP"),
        provider_sort="throughput",
        sources={
            "temperature": "env",
            "top_p": "env",
            "max_tokens": "env",
            "stop": "env",
            "provider_sort": "env",
        },
    )
```

- [ ] **Step 2: Run focused failing tests**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_ai_env_config.py -q
```

Expected:

```text
FAILED ... AttributeError: 'ModelConfig' object has no attribute 'generation_params'
```

- [ ] **Step 3: Modify env config without breaking existing fields**

In `appV2.3.1/appv231/ai/env_config.py`, add the import:

```python
from appv231.ai.providers.params import GenerationParams, params_from_mapping
```

Add StepFun metadata:

```python
DEFAULT_MODEL_PER_PROVIDER = {
    # existing entries stay unchanged
    "stepfun": "step-3.7-flash",
}
```

Add StepFun env key:

```python
PROVIDER_API_KEY_ENV = {
    # existing entries stay unchanged
    "stepfun": ("STEPFUN_API_KEY",),
}
```

Extend `ModelConfig` by adding this field at the end so existing constructor usage remains valid:

```python
    generation_params: GenerationParams = field(default_factory=GenerationParams)
```

Inside `load_model_config`, build generation params from the already-read config:

```python
    generation_params = params_from_mapping(
        {
            "temperature": config.get(f"{prefix}_TEMPERATURE"),
            "top_p": config.get(f"{prefix}_TOP_P"),
            "frequency_penalty": config.get(f"{prefix}_FREQUENCY_PENALTY"),
            "presence_penalty": config.get(f"{prefix}_PRESENCE_PENALTY"),
            "seed": config.get(f"{prefix}_SEED"),
            "stop": config.get(f"{prefix}_STOP"),
            "provider_sort": config.get(f"{prefix}_PROVIDER_SORT") or config.get("OPENROUTER_PROVIDER_SORT"),
            "max_tokens": config.get(f"{prefix}_MAX_TOKENS"),
            "timeout_seconds": config.get(f"{prefix}_TIMEOUT_SECONDS"),
        },
        source="env",
    )
```

Return it in `ModelConfig(...)`:

```python
        generation_params=generation_params,
```

- [ ] **Step 4: Run env config tests**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_ai_env_config.py -q
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit Task 3**

Run:

```bash
git add appV2.3.1/appv231/ai/env_config.py appV2.3.1/tests/test_ai_env_config.py
git commit -m "feat(appv231): expose env generation params"
```

## Task 4: Provider Payload Wiring

**Files:**

- Modify: `appV2.3.1/appv231/ai/providers/appv2_env.py`
- Modify: `appV2.3.1/tests/test_ai_appv2_env_provider.py`

- [ ] **Step 1: Write failing test for env params reaching provider payload**

Append to `appV2.3.1/tests/test_ai_appv2_env_provider.py`:

```python
from appv231.ai.providers.params import GenerationParams


def test_appv2_env_provider_applies_generation_params_to_payload(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            return iter(
                [
                    'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}',
                    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
                    "data: [DONE]",
                ]
            )

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, json, headers):
            captured["method"] = method
            captured["url"] = url
            captured["body"] = json
            captured["headers"] = headers
            return FakeResponse()

    class FakeStream:
        def __init__(self):
            self.events = []

        def push(self, event):
            self.events.append(event)

        def close(self):
            self.closed = True

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)
    config = appv2_env.ModelConfig(
        enabled=True,
        api_key="test-key",
        model="acme/x",
        base_url="https://openrouter.ai/api/v1",
        timeout_seconds=55,
        temperature=0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        stop=[],
        provider_sort="latency",
        max_tokens=None,
        generation_params=GenerationParams(
            temperature=0.2,
            top_p=0.9,
            max_tokens=4096,
            stop=("END",),
            provider_sort="throughput",
        ),
    )

    provider = appv2_env.AppV2EnvProvider(config)
    stream = FakeStream()
    provider._run(stream, _model(), Context(messages=[UserMessage(content="hi")]), None)

    body = captured["body"]
    assert body["temperature"] == 0.2
    assert body["top_p"] == 0.9
    assert body["max_tokens"] == 4096
    assert body["stop"] == ["END"]
    assert body["provider"] == {"sort": "throughput", "allow_fallbacks": True}
```

- [ ] **Step 2: Run focused failing provider test**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_ai_appv2_env_provider.py::test_appv2_env_provider_applies_generation_params_to_payload -q
```

Expected:

```text
FAILED ... KeyError: 'top_p'
```

- [ ] **Step 3: Wire capability payload in AppV2EnvProvider**

In `appV2.3.1/appv231/ai/providers/appv2_env.py`, add imports:

```python
from appv231.ai.providers.capabilities import build_generation_payload
from appv231.ai.providers.params import GenerationParams, merge_generation_params
```

Inside `AppV2EnvProvider._run`, replace the direct `max_tokens`, `provider_preferences`, and `temperature` setup with:

```python
            option_params = getattr(options, "generation_params", None) if options is not None else None
            if option_params is not None and not isinstance(option_params, GenerationParams):
                option_params = None
            generation_params = merge_generation_params(self.config.generation_params, option_params)
            max_tokens = getattr(options, "max_tokens", None) if options is not None else None
            if max_tokens is not None:
                generation_params = merge_generation_params(
                    generation_params,
                    GenerationParams(max_tokens=max_tokens, sources={"max_tokens": "runtime_options"}),
                )
```

After `api_mode` is resolved, build payload:

```python
            generation_payload = build_generation_payload(
                provider=runtime.provider,
                api_mode=api_mode,
                params=generation_params,
                tools_enabled=bool(tools),
            )
```

Update `transport_kwargs` to use the payload:

```python
            transport_kwargs = {
                "model": model.id or self.config.model,
                "messages": messages,
                "tools": tools,
                "profile": profile,
                "stream": True,
                "temperature": generation_payload.temperature,
                "max_tokens": generation_payload.max_tokens,
                "provider_preferences": generation_payload.provider_preferences,
                "request_overrides": generation_payload.request_overrides,
            }
```

Do not change `appV2.3.1/appv231/ai/types.py`; runtime options stay dynamically accessed as they are today.

- [ ] **Step 4: Run provider payload tests**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_ai_appv2_env_provider.py::test_appv2_env_provider_applies_generation_params_to_payload -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Run provider regression subset**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_ai_appv2_env_provider.py appV2.3.1/tests/test_ai_provider_capabilities.py -q
```

Expected:

```text
passed
```

- [ ] **Step 6: Commit Task 4**

Run:

```bash
git add appV2.3.1/appv231/ai/providers/appv2_env.py appV2.3.1/tests/test_ai_appv2_env_provider.py
git commit -m "feat(appv231): apply generation params to provider payloads"
```

## Task 5: StepFun and Known-Provider Custom Model Resolution

**Files:**

- Modify: `appV2.3.1/appv231/ai/model_resolver.py`
- Modify: `appV2.3.1/tests/test_ai_model_resolver.py`

- [ ] **Step 1: Write failing model resolver test**

Append to `appV2.3.1/tests/test_ai_model_resolver.py`:

```python
def test_resolve_cli_model_builds_known_provider_custom_model_without_registered_models() -> None:
    registry = Registry([])

    result = resolve_cli_model(
        cli_provider="stepfun",
        cli_model="step-3.7-flash",
        model_registry=registry,
    )

    assert result.error is None
    assert result.warning == 'Model "step-3.7-flash" not found for provider "stepfun". Using custom model id.'
    assert result.model is not None
    assert result.model.provider == "stepfun"
    assert result.model.id == "step-3.7-flash"
    assert result.model.api == "openai-completions"
    assert result.model.base_url == "https://api.stepfun.ai/step_plan/v1"


def test_resolve_cli_model_infers_known_provider_from_slash_reference_without_registered_models() -> None:
    registry = Registry([])

    result = resolve_cli_model(
        cli_model="stepfun/step-3.7-flash",
        model_registry=registry,
    )

    assert result.error is None
    assert result.warning == 'Model "step-3.7-flash" not found for provider "stepfun". Using custom model id.'
    assert result.model is not None
    assert result.model.provider == "stepfun"
    assert result.model.id == "step-3.7-flash"
```

- [ ] **Step 2: Run failing resolver test**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest \
  appV2.3.1/tests/test_ai_model_resolver.py::test_resolve_cli_model_builds_known_provider_custom_model_without_registered_models \
  appV2.3.1/tests/test_ai_model_resolver.py::test_resolve_cli_model_infers_known_provider_from_slash_reference_without_registered_models \
  -q
```

Expected:

```text
FAILED ... assert 'Unknown provider "stepfun"' is None
```

- [ ] **Step 3: Build fallback models from provider profiles**

In `appV2.3.1/appv231/ai/model_resolver.py`, add imports:

```python
from appv231.ai.providers.catalog import get_provider_profile, list_provider_profiles, normalize_provider
```

Change provider map construction in `resolve_cli_model`:

```python
    provider_map = {model.provider.lower(): model.provider for model in available_models}
    known_provider_profiles = {
        profile.name.lower(): profile.name
        for profile in list_provider_profiles()
    }
    provider_map.update({key: value for key, value in known_provider_profiles.items() if key not in provider_map})
    if cli_provider:
        normalized_cli_provider = normalize_provider(cli_provider)
        if get_provider_profile(normalized_cli_provider):
            provider_map.setdefault(cli_provider.lower(), normalized_cli_provider)
            provider_map.setdefault(normalized_cli_provider.lower(), normalized_cli_provider)
```

Change `_build_fallback_model`:

```python
def _build_fallback_model(provider: str, model_id: str, available_models: list[Model]) -> Model | None:
    provider_models = [model for model in available_models if model.provider == provider]
    if provider_models:
        default_id = DEFAULT_MODEL_PER_PROVIDER.get(provider)
        base_model = next((model for model in provider_models if model.id == default_id), provider_models[0])
        return replace(base_model, id=model_id, name=model_id)

    profile = get_provider_profile(provider)
    if profile is None:
        return None
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider=provider,
        base_url=profile.base_url or "",
        reasoning=False,
        input=["text"],
        context_window=128000,
        max_tokens=profile.default_max_tokens or 8192,
    )
```

- [ ] **Step 4: Run resolver tests**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_ai_model_resolver.py -q
```

Expected:

```text
passed
```

- [ ] **Step 5: Commit Task 5**

Run:

```bash
git add appV2.3.1/appv231/ai/model_resolver.py appV2.3.1/tests/test_ai_model_resolver.py
git commit -m "fix(appv231): resolve known provider custom models"
```

## Task 6: CLI Listing and Generation Overrides

**Files:**

- Modify: `appV2.3.1/appv231/ai/register_builtins.py`
- Modify: `appV2.3.1/appv231/cli.py`
- Modify: `appV2.3.1/tests/test_cli.py`

- [ ] **Step 1: Write failing CLI tests**

Append to `appV2.3.1/tests/test_cli.py`:

```python
def test_cli_list_models_exits_without_starting_app(monkeypatch, tmp_path, capsys) -> None:
    register_model(Model(id="step-3.7-flash", name="Step 3.7 Flash", api="openai-completions", provider="stepfun", base_url=""))
    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("app must not start")))

    code = cli.main(["--cwd", str(tmp_path), "--list-models"])

    assert code == 0
    assert "stepfun/step-3.7-flash" in capsys.readouterr().out


def test_cli_provider_stepfun_model_uses_custom_known_provider(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            observed.update(kwargs)
            self.messages = []

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--provider",
            "stepfun",
            "--model",
            "step-3.7-flash",
            "--plain",
            "noop",
        ]
    )

    assert code == 0
    model = observed["model"]
    assert model.provider == "stepfun"
    assert model.id == "step-3.7-flash"


def test_cli_generation_flags_are_passed_to_registered_provider(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}

    def record_registration(dotenv_path, config=None):
        observed["config"] = config

    class FakeApp:
        def __init__(self, **kwargs):
            self.messages = []

    monkeypatch.setattr(cli, "register_builtin_providers", record_registration)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--model",
            "stepfun/step-3.7-flash",
            "--temperature",
            "0.2",
            "--top-p",
            "0.9",
            "--max-tokens",
            "4096",
            "--provider-sort",
            "throughput",
            "--stop",
            "END,STOP",
            "--plain",
            "noop",
        ]
    )

    assert code == 0
    params = observed["config"].generation_params
    assert params.temperature == 0.2
    assert params.top_p == 0.9
    assert params.max_tokens == 4096
    assert params.provider_sort == "throughput"
    assert params.stop == ("END", "STOP")
```

- [ ] **Step 2: Run failing CLI tests**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest \
  appV2.3.1/tests/test_cli.py::test_cli_list_models_exits_without_starting_app \
  appV2.3.1/tests/test_cli.py::test_cli_provider_stepfun_model_uses_custom_known_provider \
  appV2.3.1/tests/test_cli.py::test_cli_generation_flags_are_passed_to_registered_provider \
  -q
```

Expected:

```text
FAILED ... unrecognized arguments: --list-models
```

- [ ] **Step 3: Allow explicit config for provider registration**

Modify `appV2.3.1/appv231/ai/register_builtins.py`:

```python
"""Register built-in api providers. Port of providers/register-builtins.ts."""

from __future__ import annotations

from appv231.ai.env_config import ModelConfig
from appv231.ai.providers.appv2_env import create_appv2_env_provider
from appv231.ai.stream import register_api_provider


def register_builtin_providers(
    prefix: str = "APPV2_WORKER_LLM",
    dotenv_path: str = ".env",
    *,
    config: ModelConfig | None = None,
) -> None:
    register_api_provider(create_appv2_env_provider(prefix, dotenv_path, config=config))
```

Update `create_appv2_env_provider` in `appV2.3.1/appv231/ai/providers/appv2_env.py`:

```python
def create_appv2_env_provider(
    prefix: str = "APPV2_WORKER_LLM",
    dotenv_path: str = ".env",
    *,
    config: ModelConfig | None = None,
) -> ApiProvider:
    config = config or load_model_config(prefix, dotenv_path)
    impl = AppV2EnvProvider(config) if config.enabled else RuntimeAuthProvider(config)
    return ApiProvider(api=PROVIDER_API, stream=impl.stream, stream_simple=impl.stream_simple)
```

- [ ] **Step 4: Add CLI flags and config merge**

In `appV2.3.1/appv231/cli.py`, update imports:

```python
from dataclasses import dataclass, field, replace
from appv231.ai.env_config import ModelConfig, get_default_model_for_provider, load_model_config
from appv231.ai.providers.params import GenerationParams, merge_generation_params, params_from_mapping
```

Add parser flags:

```python
    parser.add_argument("--list-models", action="store_true", help="List available provider/model IDs and exit")
    parser.add_argument("--list-providers", action="store_true", help="List available providers and exit")
    parser.add_argument("--temperature", help="Override generation temperature")
    parser.add_argument("--top-p", help="Override nucleus sampling top_p")
    parser.add_argument("--max-tokens", type=_positive_int_arg, help="Override generation max tokens")
    parser.add_argument("--timeout-seconds", help="Override provider request timeout")
    parser.add_argument("--provider-sort", help="Override provider routing sort preference where supported")
    parser.add_argument("--stop", help="Comma-separated or JSON-array stop sequences")
```

Add helpers near `_split_models_arg`:

```python
def _generation_params_from_args(args: argparse.Namespace) -> GenerationParams:
    values = {
        "temperature": args.temperature,
        "top_p": args.top_p,
        "max_tokens": args.max_tokens,
        "timeout_seconds": args.timeout_seconds,
        "provider_sort": args.provider_sort,
        "stop": args.stop,
    }
    return params_from_mapping(values, source="cli")


def _config_with_cli_generation_params(config: ModelConfig, args: argparse.Namespace) -> ModelConfig:
    cli_params = _generation_params_from_args(args)
    merged = merge_generation_params(config.generation_params, cli_params)
    return replace(
        config,
        temperature=merged.temperature if merged.temperature is not None else config.temperature,
        top_p=merged.top_p,
        max_tokens=merged.max_tokens,
        timeout_seconds=merged.timeout_seconds if merged.timeout_seconds is not None else config.timeout_seconds,
        provider_sort=merged.provider_sort,
        stop=list(merged.stop),
        generation_params=merged,
    )


def _print_provider_list() -> None:
    providers = sorted(set(get_providers()))
    for provider in providers:
        print(provider)


def _print_model_list() -> None:
    for provider in sorted(get_providers()):
        for model in sorted(get_models(provider), key=lambda item: item.id):
            print(f"{provider}/{model.id}")
```

In `main`, load config once before registration:

```python
    dotenv_path = _resolve_dotenv_path(args.dotenv, search_start=cwd_path)
    config = _config_with_cli_generation_params(load_model_config("APPV2_WORKER_LLM", dotenv_path), args)
    register_builtin_providers(dotenv_path=dotenv_path, config=config)
```

Handle listing before app startup:

```python
    if args.list_providers:
        _print_provider_list()
        return 0
    if args.list_models:
        _print_model_list()
        return 0
```

Pass `config` into `_startup_model_from_env` by extending its signature:

```python
def _startup_model_from_env(
    dotenv_path: str | Path,
    *,
    config: ModelConfig | None = None,
    cli_provider: str | None = None,
    cli_model: str | None = None,
    cli_thinking: str | None = None,
    cli_models: list[str] | None = None,
) -> _StartupModelSelection:
    config = config or load_model_config("APPV2_WORKER_LLM", dotenv_path)
```

- [ ] **Step 5: Run CLI tests**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_cli.py appV2.3.1/tests/test_ai_register_builtins.py -q
```

Expected:

```text
passed
```

- [ ] **Step 6: Commit Task 6**

Run:

```bash
git add \
  appV2.3.1/appv231/ai/register_builtins.py \
  appV2.3.1/appv231/ai/providers/appv2_env.py \
  appV2.3.1/appv231/cli.py \
  appV2.3.1/tests/test_cli.py \
  appV2.3.1/tests/test_ai_register_builtins.py
git commit -m "feat(appv231): add provider listing and generation CLI flags"
```

## Task 7: TUI Read-Only Provider Diagnostics

**Files:**

- Modify: `appV2.3.1/appv231/cli.py`
- Modify: `appV2.3.1/appv231/tui/interactive_mode.py`
- Modify: `appV2.3.1/tests/test_cli.py`
- Modify: `appV2.3.1/tests/test_tui.py`

- [ ] **Step 1: Write failing TUI command classification tests**

Append to `appV2.3.1/tests/test_tui.py`:

```python
from appv231.ai.providers.params import GenerationParams


def test_interactive_mode_parses_params_command() -> None:
    from appv231.tui.interactive_mode import _parse_params_command

    assert _parse_params_command("/params") == ""


def test_interactive_mode_parses_params_filter() -> None:
    from appv231.tui.interactive_mode import _parse_params_command

    assert _parse_params_command("/params temperature") == "temperature"


def test_interactive_mode_params_command_displays_constructor_params(monkeypatch) -> None:
    class FakeSession:
        model = Model(id="step-3.7-flash", name="Step", api="openai-completions", provider="stepfun", base_url="")
        thinking_level = "off"
        session_name = "test"

        def subscribe(self, callback):
            return lambda: None

    class FakeApp:
        cwd = "."
        tui = TUI(FakeTerminal())
        session = FakeSession()
        messages = []

    mode = InteractiveMode(
        FakeApp(),
        generation_params=GenerationParams(
            temperature=0.2,
            max_tokens=4096,
            sources={"temperature": "cli", "max_tokens": "cli"},
        ),
    )
    shown: dict[str, str] = {}
    monkeypatch.setattr(mode, "_show_status", lambda message, kind="info": shown.update(message=message, kind=kind))

    mode._run_params_command("")

    assert shown["kind"] == "model"
    assert shown["message"] == "stepfun/step-3.7-flash: temperature=0.2 (cli), max_tokens=4096 (cli)"
```

Append to `appV2.3.1/tests/test_cli.py`:

```python
def test_cli_passes_generation_params_to_interactive_mode(monkeypatch, tmp_path) -> None:
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, **kwargs):
            self.messages = []
            self.cwd = kwargs["cwd"]
            self.session = type(
                "FakeSession",
                (),
                {
                    "model": kwargs["model"],
                    "thinking_level": kwargs["thinking_level"],
                    "session_name": "test",
                    "subscribe": lambda self, callback: (lambda: None),
                },
            )()
            self.tui = None

    class FakeInteractiveMode:
        def __init__(self, app, *, generation_params=None, **kwargs):
            observed["generation_params"] = generation_params

        def run(self):
            return 0

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "InteractiveMode", FakeInteractiveMode)

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--model",
            "stepfun/step-3.7-flash",
            "--temperature",
            "0.2",
            "--max-tokens",
            "4096",
            "--tui",
        ]
    )

    assert code == 0
    params = observed["generation_params"]
    assert params.temperature == 0.2
    assert params.max_tokens == 4096
```

- [ ] **Step 2: Run failing TUI tests**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest \
  appV2.3.1/tests/test_tui.py::test_interactive_mode_parses_params_command \
  appV2.3.1/tests/test_tui.py::test_interactive_mode_parses_params_filter \
  appV2.3.1/tests/test_tui.py::test_interactive_mode_params_command_displays_constructor_params \
  appV2.3.1/tests/test_cli.py::test_cli_passes_generation_params_to_interactive_mode \
  -q
```

Expected:

```text
FAILED ... ImportError: cannot import name '_parse_params_command'
```

- [ ] **Step 3: Add `/params` classification and help text**

In `appV2.3.1/appv231/tui/interactive_mode.py`, add the command to `create_base_autocomplete_provider` command list:

```python
            {"name": "params", "description": "Show active provider generation parameters"},
```

Add to help text near `/models`:

```python
            "/params - Show active provider generation parameters.",
```

Add a real params-command parser near `_parse_model_command`:

```python
def _parse_params_command(prompt: str) -> str | None:
    if prompt == "/params":
        return ""
    if prompt.startswith("/params "):
        return prompt[len("/params ") :].strip()
    return None
```

- [ ] **Step 4: Add read-only params status handler**

Import display helper:

```python
from appv231.ai.providers.params import compact_generation_params_display
from appv231.ai.providers.params import GenerationParams
```

Extend `InteractiveMode.__init__` with an optional read-only params argument:

```python
    def __init__(
        self,
        app,
        *,
        input_fn: InputFn | None = None,
        prompt_label: str = "appv231> ",
        generation_params: GenerationParams | None = None,
    ) -> None:
        self.app = app
        self.generation_params = generation_params
```

Keep the existing constructor body intact after assigning `self.generation_params`.

In the main TUI command dispatch, immediately after `model_command` handling and before extension/unknown command handling, add:

```python
                params_query = _parse_params_command(prompt)
                if params_query is not None:
                    self._run_params_command(params_query)
                    continue
```

Add method on `InteractiveMode`:

```python
    def _run_params_command(self, query: str | None = None) -> None:
        provider = getattr(self.app.session.model, "provider", "")
        model_id = getattr(self.app.session.model, "id", "")
        params = self.generation_params
        if params is None:
            self._show_status(f"{provider}/{model_id}: default generation parameters", kind="model")
            return
        display = compact_generation_params_display(params)
        if query:
            normalized = query.strip().lower()
            pieces = [part for part in display.split(", ") if normalized in part.lower()]
            display = ", ".join(pieces) if pieces else f"no generation parameter matching {query}"
        self._show_status(f"{provider}/{model_id}: {display}", kind="model")
```

In `appV2.3.1/appv231/cli.py`, pass the already-merged params when starting the TUI:

```python
        return InteractiveMode(app, generation_params=config.generation_params).run()
```

Do not add provider config state to `CodingApp`, `AgentSession`, session store, or stream types.

Update existing CLI test fakes whose `InteractiveMode.__init__` currently accepts only `app` so they accept `**kwargs`; otherwise unrelated tests will fail on the new `generation_params=` argument.

- [ ] **Step 5: Run TUI tests**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_tui.py appV2.3.1/tests/test_cli.py -q
```

Expected:

```text
passed
```

- [ ] **Step 6: Commit Task 7**

Run:

```bash
git add appV2.3.1/appv231/cli.py appV2.3.1/appv231/tui/interactive_mode.py appV2.3.1/tests/test_cli.py appV2.3.1/tests/test_tui.py
git commit -m "feat(appv231): show provider params in tui"
```

## Task 8: Provider Control Plane Docs

**Files:**

- Create: `docs/architecture/provider-control-plane.md`

- [ ] **Step 1: Write architecture docs**

Create `docs/architecture/provider-control-plane.md`:

````markdown
# appv231 Provider Control Plane

appv231 provider behavior is controlled outside the agent loop.

## Layers

```text
CLI/env/profile input
  -> GenerationParams
  -> ProviderCapabilities
  -> transport payload
  -> provider response normalization
```

## Red-Zone Rule

Provider ergonomics must not require changes to:

- `appV2.3.1/appv231/agent/agent_loop.py`
- `appV2.3.1/appv231/ai/types.py`
- `appV2.3.1/appv231/ai/stream.py`
- `appV2.3.1/appv231/compaction/`
- `appV2.3.1/appv231/coding_agent/session_store.py`

If a provider feature needs those files, it is a kernel change and needs explicit approval.

## Parameter Policy

Direct providers use explicit capability policy. Unsupported user parameters are dropped only with warnings.

Routing aggregators such as OpenRouter can receive provider-routing preferences through the provider payload object.

## Merge Order

```text
provider defaults < .env < process env < profile defaults < CLI flags < TUI session override
```

The current implementation supports:

```text
provider defaults < .env < process env < CLI flags
```

## Testing Rule

Default tests use fake providers, payload snapshots, and monkeypatched HTTP clients. Live provider calls are manual verification only.
```
````

- [ ] **Step 2: Commit docs**

Run:

```bash
git add docs/architecture/provider-control-plane.md
git commit -m "docs(appv231): document provider control plane"
```

## Task 9: Full Verification

**Files:**

- No source edits.

- [ ] **Step 1: Run focused provider suite**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest \
  appV2.3.1/tests/test_ai_generation_params.py \
  appV2.3.1/tests/test_ai_provider_capabilities.py \
  appV2.3.1/tests/test_ai_env_config.py \
  appV2.3.1/tests/test_ai_appv2_env_provider.py \
  appV2.3.1/tests/test_ai_model_resolver.py \
  appV2.3.1/tests/test_cli.py \
  appV2.3.1/tests/test_tui.py \
  -q
```

Expected:

```text
passed
```

- [ ] **Step 2: Run full appv231 pytest suite**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests
```

Expected:

```text
passed
```

- [ ] **Step 3: Run npm CLI QA if package files were touched**

Run only if implementation touched `packages/` or CLI package metadata:

```bash
npm run qa:appv23-cli
```

Expected:

```text
node tests passed
npm pack --dry-run OK
```

- [ ] **Step 4: Manual budget-model TUI smoke matrix and 21-turn coding run**

Run only after Lewis confirms `.env` has valid credentials for at least one budget provider.

Budget-model rule:

```text
Do not use premium/expensive flagship models for the manual TUI probe.
Prefer configured budget coding models.
The candidate set must include StepFun when available and at least one other currently configured budget model.
Run short probes first, choose the best successful budget model for the 21-turn coding conversation, and record the evidence.
```

Candidate examples, subject to current credentials/catalog availability:

```text
stepfun/step-3.7-flash
openrouter/zai/glm-4.5-air:free
openrouter/qwen/qwen3-coder:free
zai/glm-4.5-flash
opencode-go/glm-5
```

Use only candidates that are currently available and configured. If a listed model is unavailable, skip it and document the reason.

Example StepFun probe:

```bash
APPV2_WORKER_LLM_MODEL=step-3.7-flash \
PYTHONPATH=appV2.3.1 \
.venv/bin/appv231 \
  --dotenv .env \
  --cwd tmp/appv231_provider_smoke \
  --provider stepfun \
  --model step-3.7-flash \
  --temperature 0.2 \
  --max-tokens 4096 \
  --tui
```

Expected:

```text
TUI starts with model stepfun/step-3.7-flash and /params shows temperature=0.2 and max_tokens=4096.
```

Then run the required 21-turn TUI coding scenario in a temp probe folder with the best successful budget candidate:

```bash
mkdir -p tmp/appv231_provider_tui_21_turn_probe
```

The scenario must exercise file read/write/edit/bash behavior through normal TUI prompts, end with tests passing inside the temp folder, and record which budget model produced the best result.

## Rollback Plan

Rollback can happen task by task:

```bash
git revert <task-commit-sha>
```

If provider payload wiring causes live regressions, revert Task 4 first. The params schema and tests can remain because they do not alter runtime behavior until wired.

## Self-Review

Spec coverage:

- Provider/model resolution is covered by Tasks 5 and 6.
- Model catalog/listing ergonomics are covered by Task 6 and existing model registry APIs.
- Generation params are covered by Tasks 1, 3, 4, and 6.
- Provider capability policy is covered by Task 2.
- TUI visibility is covered by Task 7.
- Rules.md red/yellow/green constraints are covered in Scope Boundaries and Task 9.

Placeholder scan:

- The plan contains concrete paths, test code, implementation snippets, commands, and expected results.
- The plan has no intentionally broad rewrite step.

Type consistency:

- `GenerationParams`, `GenerationPayload`, and `ProviderParamWarning` names are introduced before use.
- `generation_params` is added to `ModelConfig` with a default factory to preserve existing constructor calls.
- Runtime options are accessed dynamically, preserving the existing no-red-zone `options` pattern.

Risk notes:

- Task 7 must pass generation params into `InteractiveMode` directly. Do not add provider config state to `CodingApp`, `AgentSession`, session store, or stream types.
- Task 6 changes `register_builtin_providers` signature. All tests that monkeypatch it must accept `config=None`.
- OpenRouter raw model IDs such as `openai/gpt-4o` must stay OpenRouter IDs unless Lewis explicitly passes `--provider openai`.
