# appv231 Live Startup Model Catalog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fetch provider model catalogs during appv231 startup so live-known models such as `openai/gpt-5.4-mini` resolve with accurate context, output, tool, and pricing metadata instead of falling through to generic custom-model defaults.

**Architecture:** Keep model resolution pure and keep network I/O in the yellow provider/CLI layer. Add a reusable OpenRouter live-catalog hydrator in `ai/providers/model_catalog.py`, call it from CLI startup before `resolve_cli_model()`, and let failures degrade to the current custom-model path. Do not modify red-zone agent loop, provider streaming, compaction, session store, tool schemas, or `ai/types.py`.

**Tech Stack:** Python dataclasses/functions, existing `urllib.request` catalog style, existing `Model` dataclass, pytest with monkeypatched network, existing CLI/TUI model-picker tests.

---

## Source Basis

- `rules.md` marks provider reliability, model catalog updates, and TUI usability as yellow-zone work.
- OpenRouter `/api/v1/models` exposes model filters, input modalities, context filtering, and server-side sorting.
- OpenRouter model responses include architecture, pricing, top-provider limits, and supported parameter metadata.
- OpenRouter provider routing accepts a `provider` request object with fields such as `order`, `allow_fallbacks`, `require_parameters`, `data_collection`, `only`, `ignore`, `quantizations`, and `sort`.
- OpenRouter API parameters must be checked against each model/provider because support varies.

References:

- https://openrouter.ai/docs/guides/overview/models
- https://openrouter.ai/docs/api/api-reference/models/get-models
- https://openrouter.ai/docs/guides/routing/provider-selection
- https://openrouter.ai/docs/api/reference/parameters

## Scope Boundaries

Allowed files:

- Modify: `appV2.3.1/appv231/ai/providers/model_catalog.py`
- Modify: `appV2.3.1/appv231/cli.py`
- Modify: `appV2.3.1/appv231/tui/interactive_mode.py`
- Modify if a TUI probe exposes reasoning leakage: `appV2.3.1/appv231/tui/interactive.py`
- Modify: `appV2.3.1/tests/test_ai_appv2_env_provider.py`
- Modify: `appV2.3.1/tests/test_cli.py`
- Modify: `appV2.3.1/tests/test_tui.py`
- Create if cleaner: `appV2.3.1/tests/test_ai_live_model_catalog.py`

Explicitly out of scope:

- Do not edit `appV2.3.1/appv231/agent/agent.py`.
- Do not edit `appV2.3.1/appv231/agent/agent_loop.py`.
- Do not edit `appV2.3.1/appv231/ai/types.py`.
- Do not edit `appV2.3.1/appv231/ai/stream.py`.
- Do not edit `appV2.3.1/appv231/ai/validation.py`.
- Do not edit `appV2.3.1/appv231/compaction/`.
- Do not edit `appV2.3.1/appv231/coding_agent/session_store.py`.
- Do not change tool schemas.
- Do not add live-network tests to the default suite.
- Do not publish npm or build GHCR.

If execution discovers a required red-zone change, stop and ask Lewis for explicit `kernel change` approval with the exact reason, rollback path, and regression tests.

## File Responsibilities

- `appV2.3.1/appv231/ai/providers/model_catalog.py`: reusable live OpenRouter model fetch, validation, conversion to `Model`, disk cache reuse, and reset hooks for tests.
- `appV2.3.1/appv231/cli.py`: startup catalog hydration before model resolution, live-backed `--list-models`, and fail-open behavior when the live catalog is unavailable.
- `appV2.3.1/appv231/tui/interactive_mode.py`: reuse the provider-layer OpenRouter item-to-model conversion instead of carrying a duplicate converter.
- `appV2.3.1/appv231/tui/interactive.py`: only TUI-renderer regression fixes discovered during model-probe testing, such as hiding reasoning blocks by default. Do not change layout or unrelated interaction behavior here.
- `appV2.3.1/tests/test_ai_live_model_catalog.py`: isolated parser/cache tests for live OpenRouter catalog behavior.
- `appV2.3.1/tests/test_cli.py`: startup behavior tests proving live-known models do not emit custom warnings and receive accurate metadata.
- `appV2.3.1/tests/test_tui.py`: focused compatibility tests for the model picker after shared converter extraction.

## Behavioral Contract

Startup model resolution order:

```text
registered models/models.json
  -> startup live provider catalog for requested provider/model
  -> stale live-catalog disk cache when network fails
  -> existing custom known-provider fallback
  -> existing unknown-provider error
```

Required behavior:

- `appv231 --provider openrouter --model openai/gpt-5.4-mini ...` attempts a live OpenRouter catalog fetch during startup.
- If the live catalog contains the model, startup selects a real `Model` with live metadata and does not print `Using custom model id`.
- If the live fetch fails but a valid stale cache contains the model, startup uses the stale metadata without blocking startup.
- If neither live nor stale catalog contains the model, startup preserves the existing custom-model warning and fallback.
- Live catalog failures must never prevent app startup by themselves.
- Default pytest must use mocked network only.

## Task 1: Live OpenRouter Catalog Parser

**Files:**

- Modify: `appV2.3.1/appv231/ai/providers/model_catalog.py`
- Create: `appV2.3.1/tests/test_ai_live_model_catalog.py`

- [ ] **Step 1: Write failing parser tests**

Create `appV2.3.1/tests/test_ai_live_model_catalog.py` with:

```python
from __future__ import annotations

from appv231.ai.providers import model_catalog
from appv231.ai.types import Model


def _base_model() -> Model:
    return Model(
        id="moonshotai/kimi-k2.6",
        name="moonshotai/kimi-k2.6",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )


def test_openrouter_live_catalog_item_to_model_preserves_runtime_metadata() -> None:
    item = {
        "id": "openai/gpt-5.4-mini",
        "name": "OpenAI: GPT-5.4 Mini",
        "context_length": 400000,
        "architecture": {"input_modalities": ["text", "image", "file"]},
        "pricing": {
            "prompt": "0.00000075",
            "completion": "0.0000045",
            "input_cache_read": "0.000000075",
        },
        "top_provider": {
            "context_length": 400000,
            "max_completion_tokens": 128000,
            "is_moderated": True,
        },
        "supported_parameters": [
            "include_reasoning",
            "max_completion_tokens",
            "max_tokens",
            "reasoning",
            "response_format",
            "seed",
            "structured_outputs",
            "tool_choice",
            "tools",
        ],
    }

    model = model_catalog.openrouter_live_catalog_item_to_model(item, _base_model())

    assert model is not None
    assert model.provider == "openrouter"
    assert model.id == "openai/gpt-5.4-mini"
    assert model.name == "OpenAI: GPT-5.4 Mini"
    assert model.context_window == 400000
    assert model.max_tokens == 128000
    assert model.reasoning is True
    assert model.input == ["text", "image"]


def test_openrouter_live_catalog_item_caps_output_below_context_window() -> None:
    item = {
        "id": "huge/output",
        "name": "Huge Output",
        "context_length": 262144,
        "top_provider": {"max_completion_tokens": 262144},
        "supported_parameters": ["tools"],
    }

    model = model_catalog.openrouter_live_catalog_item_to_model(item, _base_model())

    assert model is not None
    assert model.context_window == 262144
    assert model.max_tokens == 16384
    assert model.max_tokens < model.context_window


def test_openrouter_live_catalog_item_rejects_missing_id() -> None:
    assert model_catalog.openrouter_live_catalog_item_to_model({}, _base_model()) is None
```

- [ ] **Step 2: Run parser tests and confirm failure**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_ai_live_model_catalog.py -q
```

Expected:

```text
AttributeError: module 'appv231.ai.providers.model_catalog' has no attribute 'openrouter_live_catalog_item_to_model'
```

- [ ] **Step 3: Add parser constants and converter**

Append to `appV2.3.1/appv231/ai/providers/model_catalog.py`:

```python
from dataclasses import replace
from appv231.ai.types import Model

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_STARTUP_FETCH_TIMEOUT_SECONDS = 3.0
OPENROUTER_MODEL_PICKER_MAX_COMPLETION_TOKENS = 16384
OPENROUTER_MODEL_PICKER_CONTEXT_RESERVE_TOKENS = 4096


def openrouter_live_catalog_item_to_model(item: dict[str, Any], base_model: Model) -> Model | None:
    model_id = str(item.get("id") or "").strip()
    if not model_id:
        return None

    architecture = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
    top_provider = item.get("top_provider") if isinstance(item.get("top_provider"), dict) else {}
    modalities = architecture.get("input_modalities", architecture.get("inputModalities", []))
    input_types = ["text"]
    if isinstance(modalities, list) and any(str(value).lower() == "image" for value in modalities):
        input_types.append("image")

    supported_parameters = item.get("supported_parameters", item.get("supportedParameters", []))
    reasoning = bool(getattr(base_model, "reasoning", False) or item.get("reasoning"))
    if isinstance(supported_parameters, list) and any("reason" in str(value).lower() for value in supported_parameters):
        reasoning = True

    context_window = _positive_int_or(item.get("context_length", item.get("contextLength")), base_model.context_window)
    max_completion_tokens = _safe_openrouter_completion_tokens(
        top_provider.get("max_completion_tokens", top_provider.get("maxCompletionTokens")),
        context_window,
        base_model.max_tokens,
    )

    return replace(
        base_model,
        id=model_id,
        name=str(item.get("name") or model_id),
        provider="openrouter",
        context_window=context_window,
        max_tokens=max_completion_tokens,
        reasoning=reasoning,
        input=input_types,
    )


def _safe_openrouter_completion_tokens(value: object, context_window: int, fallback: int) -> int:
    parsed = _positive_int_or(value, fallback)
    if parsed <= 0:
        return fallback
    capped = min(parsed, OPENROUTER_MODEL_PICKER_MAX_COMPLETION_TOKENS)
    if context_window > OPENROUTER_MODEL_PICKER_CONTEXT_RESERVE_TOKENS:
        capped = min(capped, context_window - OPENROUTER_MODEL_PICKER_CONTEXT_RESERVE_TOKENS)
    elif context_window > 0:
        capped = min(capped, max(1, context_window // 2))
    return max(1, capped)


def _positive_int_or(value: object, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback
```

If these helper names conflict with existing private names, keep the existing implementation and expose only `openrouter_live_catalog_item_to_model`.

- [ ] **Step 4: Run parser tests**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_ai_live_model_catalog.py -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit parser unit**

```bash
git add appV2.3.1/appv231/ai/providers/model_catalog.py appV2.3.1/tests/test_ai_live_model_catalog.py
git commit -m "test: cover live openrouter model catalog parsing"
```

## Task 2: Live Fetch With Disk Cache

**Files:**

- Modify: `appV2.3.1/appv231/ai/providers/model_catalog.py`
- Modify: `appV2.3.1/tests/test_ai_live_model_catalog.py`

- [ ] **Step 1: Add failing fetch/cache tests**

Append to `appV2.3.1/tests/test_ai_live_model_catalog.py`:

```python
import json
import urllib.error


def test_get_live_openrouter_models_fetches_and_caches(tmp_path, monkeypatch) -> None:
    model_catalog.reset_cache()
    monkeypatch.setenv("APPV231_HOME", str(tmp_path))

    payload = {
        "data": [
            {
                "id": "openai/gpt-5.4-mini",
                "name": "OpenAI: GPT-5.4 Mini",
                "context_length": 400000,
                "top_provider": {"max_completion_tokens": 128000},
                "supported_parameters": ["tools", "tool_choice", "reasoning"],
            }
        ]
    }
    calls: list[str] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(payload).encode()

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fake_urlopen)

    models = model_catalog.get_live_openrouter_models(base_model=_base_model(), force_refresh=True)

    assert [model.id for model in models] == ["openai/gpt-5.4-mini"]
    assert models[0].context_window == 400000
    assert models[0].max_tokens == 128000
    assert calls == ["https://openrouter.ai/api/v1/models"]


def test_get_live_openrouter_models_uses_stale_cache_when_fetch_fails(tmp_path, monkeypatch) -> None:
    model_catalog.reset_cache()
    monkeypatch.setenv("APPV231_HOME", str(tmp_path))
    cache_path = model_catalog._openrouter_live_cache_path()
    cache_path.parent.mkdir(parents=True)
    cache_path.write_text(
        json.dumps(
            {
                "data": [
                    {
                        "id": "openai/gpt-5.4-mini",
                        "name": "OpenAI: GPT-5.4 Mini",
                        "context_length": 400000,
                        "top_provider": {"max_completion_tokens": 128000},
                        "supported_parameters": ["tools"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fail_urlopen(request, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fail_urlopen)

    models = model_catalog.get_live_openrouter_models(base_model=_base_model(), force_refresh=True)

    assert [model.id for model in models] == ["openai/gpt-5.4-mini"]
    assert models[0].context_window == 400000
```

- [ ] **Step 2: Run fetch/cache tests and confirm failure**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_ai_live_model_catalog.py -q
```

Expected:

```text
AttributeError: module 'appv231.ai.providers.model_catalog' has no attribute 'get_live_openrouter_models'
```

- [ ] **Step 3: Add live fetch/cache functions**

Append to `appV2.3.1/appv231/ai/providers/model_catalog.py`:

```python
_openrouter_live_model_cache: tuple[float, list[Model]] | None = None


def _openrouter_live_cache_path() -> Path:
    return _appv231_home() / "cache" / "openrouter_models.json"


def get_live_openrouter_models(
    *,
    base_model: Model,
    force_refresh: bool = False,
    timeout: float = OPENROUTER_STARTUP_FETCH_TIMEOUT_SECONDS,
) -> list[Model]:
    global _openrouter_live_model_cache

    if not _live_catalog_enabled():
        return []

    if not force_refresh and _openrouter_live_model_cache is not None:
        return list(_openrouter_live_model_cache[1])

    payload = _fetch_openrouter_live_payload(timeout=timeout)
    if payload is not None:
        _write_openrouter_live_cache(payload)
    else:
        payload = _read_openrouter_live_cache()

    models = _openrouter_live_payload_to_models(payload, base_model) if payload is not None else []
    _openrouter_live_model_cache = (time.time(), models)
    return list(models)


def find_live_openrouter_model(model_id: str, *, base_model: Model, force_refresh: bool = True) -> Model | None:
    wanted = model_id.strip().lower()
    for model in get_live_openrouter_models(base_model=base_model, force_refresh=force_refresh):
        if model.id.lower() == wanted:
            return model
    return None


def _live_catalog_enabled() -> bool:
    enabled_raw = os.environ.get("APPV231_MODEL_CATALOG_ENABLED", "true").strip().lower()
    startup_raw = os.environ.get("APPV231_MODEL_CATALOG_STARTUP_FETCH", "true").strip().lower()
    return enabled_raw not in _FALSE_VALUES and startup_raw not in _FALSE_VALUES


def _fetch_openrouter_live_payload(*, timeout: float) -> dict[str, Any] | None:
    try:
        request = urllib.request.Request(
            OPENROUTER_MODELS_URL,
            headers={
                "Accept": "application/json",
                "User-Agent": _USER_AGENT,
            },
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.info("openrouter live model catalog fetch failed: %s", exc)
        return None
    return payload if _validate_openrouter_live_payload(payload) else None


def _validate_openrouter_live_payload(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    data = payload.get("data")
    if not isinstance(data, list):
        return False
    return all(isinstance(item, dict) and isinstance(item.get("id"), str) and item.get("id").strip() for item in data)


def _openrouter_live_payload_to_models(payload: dict[str, Any], base_model: Model) -> list[Model]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    models: list[Model] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model = openrouter_live_catalog_item_to_model(item, base_model)
        if model is not None:
            models.append(model)
    return models


def _read_openrouter_live_cache() -> dict[str, Any] | None:
    try:
        payload = json.loads(_openrouter_live_cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if _validate_openrouter_live_payload(payload) else None


def _write_openrouter_live_cache(payload: dict[str, Any]) -> None:
    path = _openrouter_live_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.info("openrouter live model catalog cache write failed: %s", exc)
```

Also update `reset_cache()` in the same file so tests can reset live state:

```python
def reset_cache() -> None:
    global _catalog_cache, _catalog_cache_source_mtime, _openrouter_live_model_cache
    _catalog_cache = None
    _catalog_cache_source_mtime = 0.0
    _openrouter_live_model_cache = None
```

- [ ] **Step 4: Run fetch/cache tests**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_ai_live_model_catalog.py -q
```

Expected:

```text
5 passed
```

- [ ] **Step 5: Commit live fetch/cache unit**

```bash
git add appV2.3.1/appv231/ai/providers/model_catalog.py appV2.3.1/tests/test_ai_live_model_catalog.py
git commit -m "feat: add live openrouter model catalog cache"
```

## Task 3: Hydrate CLI Startup Before Model Resolution

**Files:**

- Modify: `appV2.3.1/appv231/cli.py`
- Modify: `appV2.3.1/tests/test_cli.py`

- [ ] **Step 1: Add failing CLI startup test**

Append to `appV2.3.1/tests/test_cli.py`:

```python
def test_cli_startup_hydrates_live_openrouter_model_before_custom_fallback(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.cwd = cwd
            self.model = model
            self.enable_tui = enable_tui
            self.thinking_level = thinking_level
            self.scoped_models = scoped_models
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
        reasoning=True,
    )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "_load_live_startup_models", lambda env_model, **kwargs: [live_model])

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--provider",
            "openrouter",
            "--model",
            "openai/gpt-5.4-mini",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert code == 0
    assert app.model is live_model
    assert app.model.context_window == 400000
    assert app.model.max_tokens == 128000
    assert "Using custom model id" not in captured.err
```

- [ ] **Step 2: Run targeted CLI test and confirm failure**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_cli.py::test_cli_startup_hydrates_live_openrouter_model_before_custom_fallback -q
```

Expected:

```text
AttributeError: module 'appv231.cli' has no attribute '_load_live_startup_models'
```

- [ ] **Step 3: Import live catalog and add startup loader**

Modify imports in `appV2.3.1/appv231/cli.py`:

```python
from appv231.ai.providers.model_catalog import get_live_openrouter_models
```

Add below `_registered_models_with_env_fallback()`:

```python
def _load_live_startup_models(
    env_model: Model,
    *,
    cli_provider: str | None,
    cli_model: str | None,
    cli_models: list[str] | None,
    list_models: bool = False,
) -> list[Model]:
    provider = (cli_provider or getattr(env_model, "provider", "") or "").strip().lower()
    model_hint = (cli_model or "").strip()
    patterns = cli_models or []

    if not provider and model_hint.startswith("openrouter/"):
        provider = "openrouter"
    if provider != "openrouter" and getattr(env_model, "provider", "") != "openrouter":
        return []
    if not list_models and not model_hint and not patterns:
        return []

    return get_live_openrouter_models(base_model=env_model, force_refresh=True)
```

Modify `_startup_model_from_env()` so the registry includes live models before resolution:

```python
    live_models = _load_live_startup_models(
        env_model,
        cli_provider=cli_provider,
        cli_model=cli_model,
        cli_models=cli_models,
    )
    registry = _CliModelRegistry(_dedupe_startup_models([*_registered_models_with_env_fallback(env_model), *live_models]))
```

Add the helper:

```python
def _dedupe_startup_models(models: list[Model]) -> list[Model]:
    by_key: dict[tuple[str, str], Model] = {}
    for model in models:
        key = (model.provider, model.id)
        by_key[key] = model
    return list(by_key.values())
```

The live model must be placed after registered/env models only if local config should win. If live metadata should win for the exact same provider/id, place `live_models` after registered models as shown.

- [ ] **Step 4: Run targeted CLI test**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_cli.py::test_cli_startup_hydrates_live_openrouter_model_before_custom_fallback -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Commit startup hydration**

```bash
git add appV2.3.1/appv231/cli.py appV2.3.1/tests/test_cli.py
git commit -m "feat: hydrate startup model metadata from live catalog"
```

## Task 4: Keep Custom Fallback When Live Catalog Misses

**Files:**

- Modify: `appV2.3.1/tests/test_cli.py`
- Modify if needed: `appV2.3.1/appv231/cli.py`

- [ ] **Step 1: Add regression test for fallback behavior**

Append to `appV2.3.1/tests/test_cli.py`:

```python
def test_cli_startup_preserves_custom_model_fallback_when_live_catalog_misses(monkeypatch, tmp_path, capsys) -> None:
    created: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, cwd, model, enable_tui, thinking_level, scoped_models, **kwargs):
            self.model = model
            self.messages = []
            created["app"] = self

        def run_turn(self, prompt):
            created["prompt"] = prompt

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "CodingApp", FakeApp)
    monkeypatch.setattr(cli, "_load_live_startup_models", lambda env_model, **kwargs: [])

    code = cli.main(
        [
            "--cwd",
            str(tmp_path),
            "--dotenv",
            str(tmp_path / "missing.env"),
            "--provider",
            "openrouter",
            "--model",
            "unknown/vendor-model",
            "--plain",
            "inspect",
        ]
    )

    captured = capsys.readouterr()
    app = created["app"]
    assert code == 0
    assert app.model.provider == "openrouter"
    assert app.model.id == "unknown/vendor-model"
    assert 'Model "unknown/vendor-model" not found for provider "openrouter". Using custom model id.' in captured.err
```

- [ ] **Step 2: Run regression test**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_cli.py::test_cli_startup_preserves_custom_model_fallback_when_live_catalog_misses -q
```

Expected:

```text
1 passed
```

- [ ] **Step 3: Commit fallback regression**

```bash
git add appV2.3.1/tests/test_cli.py appV2.3.1/appv231/cli.py
git commit -m "test: preserve custom model fallback on catalog miss"
```

## Task 5: Live-Backed `--list-models`

**Files:**

- Modify: `appV2.3.1/appv231/cli.py`
- Modify: `appV2.3.1/tests/test_cli.py`

- [ ] **Step 1: Add failing list-models test**

Append to `appV2.3.1/tests/test_cli.py`:

```python
def test_cli_list_models_includes_live_openrouter_catalog(monkeypatch, tmp_path, capsys) -> None:
    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
    )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "_load_live_startup_models", lambda env_model, **kwargs: [live_model])
    monkeypatch.setattr(
        cli,
        "CodingApp",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("app must not start")),
    )

    code = cli.main(["--cwd", str(tmp_path), "--provider", "openrouter", "--list-models"])

    captured = capsys.readouterr()
    assert code == 0
    assert "openrouter/openai/gpt-5.4-mini" in captured.out
```

- [ ] **Step 2: Run list-models test and confirm failure**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_cli.py::test_cli_list_models_includes_live_openrouter_catalog -q
```

Expected:

```text
AssertionError: assert 'openrouter/openai/gpt-5.4-mini' in ...
```

- [ ] **Step 3: Hydrate list-models path**

Modify `main()` before `if args.list_models:`:

```python
    if args.list_models:
        startup_seed = _startup_model_from_env(
            dotenv_path,
            config=config,
            cli_provider=args.provider,
            cli_model=args.model,
            cli_thinking=args.thinking,
            cli_models=_split_models_arg(args.models),
            list_models=True,
        )
        for model in startup_seed.scoped_models:
            register_model(model.model)
        register_model(startup_seed.model)
        _print_model_list()
        return 0
```

Update `_startup_model_from_env()` signature:

```python
def _startup_model_from_env(
    dotenv_path: str | Path,
    *,
    config: ModelConfig | None = None,
    cli_provider: str | None = None,
    cli_model: str | None = None,
    cli_thinking: str | None = None,
    cli_models: list[str] | None = None,
    list_models: bool = False,
) -> _StartupModelSelection:
```

Pass `list_models=list_models` into `_load_live_startup_models()`.

If registering only `startup_seed.model` drops non-selected live models, replace this with an explicit `_hydrate_live_models_for_list()` helper:

```python
def _hydrate_live_models_for_list(env_model: Model, *, cli_provider: str | None) -> None:
    for model in _load_live_startup_models(
        env_model,
        cli_provider=cli_provider,
        cli_model=None,
        cli_models=None,
        list_models=True,
    ):
        register_model(model)
```

Then call that helper before `_print_model_list()`.

- [ ] **Step 4: Run list-models test**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_cli.py::test_cli_list_models_includes_live_openrouter_catalog -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Commit list-models hydration**

```bash
git add appV2.3.1/appv231/cli.py appV2.3.1/tests/test_cli.py
git commit -m "feat: include live catalog models in list-models"
```

## Task 6: Reuse Live Catalog Converter in TUI

**Files:**

- Modify: `appV2.3.1/appv231/tui/interactive_mode.py`
- Modify: `appV2.3.1/tests/test_tui.py`

- [ ] **Step 1: Add compatibility test for TUI converter behavior**

The existing TUI tests already cover OpenRouter catalog conversion around `/model`. Keep them, but add this focused assertion if no exact equivalent exists:

```python
def test_interactive_mode_openrouter_catalog_uses_shared_model_metadata_converter(tmp_path, monkeypatch) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "model should not run")))
    terminal = FakeTerminal(columns=140)
    active = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=128000,
        max_tokens=8192,
    )

    def fake_fetch(base_model):
        return [
            {
                "id": "openai/gpt-5.4-mini",
                "name": "OpenAI: GPT-5.4 Mini",
                "context_length": 400000,
                "top_provider": {"max_completion_tokens": 128000},
                "supported_parameters": ["tools", "tool_choice", "reasoning"],
            }
        ]

    monkeypatch.setattr(interactive_mode, "_fetch_openrouter_model_catalog", fake_fetch)
    app = CodingApp(cwd=str(tmp_path), model=active, terminal=terminal, enable_tui=True)
    inputs = iter(["/model", "1", "/exit"])
    mode = InteractiveMode(app, input_fn=lambda prompt: next(inputs))

    mode.run()

    assert app.session.model.id == "openai/gpt-5.4-mini"
    assert app.session.model.context_window == 400000
    assert app.session.model.max_tokens == 128000
    assert app.session.model.reasoning is True
```

- [ ] **Step 2: Run focused TUI model tests**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest \
  appV2.3.1/tests/test_tui.py::test_interactive_mode_model_command_fetches_openrouter_catalog \
  appV2.3.1/tests/test_tui.py::test_interactive_mode_model_command_caps_openrouter_full_context_output_limit \
  appV2.3.1/tests/test_tui.py::test_interactive_mode_openrouter_catalog_uses_shared_model_metadata_converter \
  -q
```

Expected before refactor:

```text
passed
```

- [ ] **Step 3: Replace duplicate TUI converter**

Modify `appV2.3.1/appv231/tui/interactive_mode.py` imports:

```python
from appv231.ai.providers.model_catalog import openrouter_live_catalog_item_to_model
```

Change `_openrouter_catalog_item_to_model()` to delegate:

```python
def _openrouter_catalog_item_to_model(item: dict, base_model):
    return openrouter_live_catalog_item_to_model(item, base_model)
```

Remove duplicated `_safe_openrouter_completion_tokens()` and `_positive_int_or()` from `interactive_mode.py` only if no other local code uses them.

- [ ] **Step 4: Run focused TUI tests**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest \
  appV2.3.1/tests/test_tui.py::test_interactive_mode_model_command_fetches_openrouter_catalog \
  appV2.3.1/tests/test_tui.py::test_interactive_mode_model_command_caps_openrouter_full_context_output_limit \
  appV2.3.1/tests/test_tui.py::test_interactive_mode_openrouter_catalog_uses_shared_model_metadata_converter \
  -q
```

Expected:

```text
3 passed
```

- [ ] **Step 5: Commit TUI converter reuse**

```bash
git add appV2.3.1/appv231/tui/interactive_mode.py appV2.3.1/tests/test_tui.py
git commit -m "refactor: share openrouter catalog conversion"
```

## Task 7: Provider Ergonomics Display

**Files:**

- Modify: `appV2.3.1/appv231/cli.py`
- Modify: `appV2.3.1/tests/test_cli.py`

- [ ] **Step 1: Add test for metadata-rich list output behind explicit flag**

Add a new CLI flag instead of changing default `--list-models` output. Append to `appV2.3.1/tests/test_cli.py`:

```python
def test_cli_list_models_verbose_shows_live_metadata(monkeypatch, tmp_path, capsys) -> None:
    live_model = Model(
        id="openai/gpt-5.4-mini",
        name="OpenAI: GPT-5.4 Mini",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        context_window=400000,
        max_tokens=128000,
        reasoning=True,
        input=["text", "image"],
    )

    monkeypatch.setattr(cli, "register_builtin_providers", lambda dotenv_path, config=None: None)
    monkeypatch.setattr(cli, "_load_live_startup_models", lambda env_model, **kwargs: [live_model])

    code = cli.main(["--cwd", str(tmp_path), "--provider", "openrouter", "--list-models", "--verbose-models"])

    captured = capsys.readouterr()
    assert code == 0
    assert "openrouter/openai/gpt-5.4-mini" in captured.out
    assert "context=400000" in captured.out
    assert "max_tokens=128000" in captured.out
    assert "reasoning=true" in captured.out
    assert "input=text,image" in captured.out
```

- [ ] **Step 2: Run verbose list test and confirm failure**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_cli.py::test_cli_list_models_verbose_shows_live_metadata -q
```

Expected:

```text
SystemExit: 2
```

- [ ] **Step 3: Add verbose flag and printer**

Modify parser setup in `appV2.3.1/appv231/cli.py`:

```python
parser.add_argument("--verbose-models", action="store_true", help="Show model metadata with --list-models")
```

Replace `_print_model_list()` with:

```python
def _print_model_list(*, verbose: bool = False) -> None:
    for provider in sorted(get_providers()):
        for model in sorted(get_models(provider), key=lambda item: item.id):
            if not verbose:
                print(f"{provider}/{model.id}")
                continue
            input_types = ",".join(getattr(model, "input", []) or [])
            reasoning = "true" if getattr(model, "reasoning", False) else "false"
            print(
                f"{provider}/{model.id} "
                f"context={getattr(model, 'context_window', 0)} "
                f"max_tokens={getattr(model, 'max_tokens', 0)} "
                f"reasoning={reasoning} "
                f"input={input_types or 'text'}"
            )
```

Call:

```python
_print_model_list(verbose=args.verbose_models)
```

- [ ] **Step 4: Run verbose list test**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests/test_cli.py::test_cli_list_models_verbose_shows_live_metadata -q
```

Expected:

```text
1 passed
```

- [ ] **Step 5: Commit display ergonomics**

```bash
git add appV2.3.1/appv231/cli.py appV2.3.1/tests/test_cli.py
git commit -m "feat: show verbose model catalog metadata"
```

## Task 8: Verification

**Files:**

- No code changes.

- [ ] **Step 1: Run focused model/provider tests**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest \
  appV2.3.1/tests/test_ai_live_model_catalog.py \
  appV2.3.1/tests/test_ai_appv2_env_provider.py \
  appV2.3.1/tests/test_ai_model_resolver.py \
  appV2.3.1/tests/test_cli.py \
  appV2.3.1/tests/test_tui.py \
  -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 2: Run full appv231 Python suite**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests
```

Expected:

```text
all tests pass
```

- [ ] **Step 3: Manually verify no custom warning for live-known model**

Use a temp cwd and real `.env` only if Lewis has provided credentials:

```bash
tmpdir="$(mktemp -d)"
PYTHONPATH=appV2.3.1 .venv/bin/appv231 \
  --dotenv .env \
  --cwd "$tmpdir" \
  --provider openrouter \
  --model openai/gpt-5.4-mini \
  --plain \
  "Reply with exactly: catalog-ok"
```

Expected:

```text
No stderr line containing: Using custom model id
Assistant reply contains: catalog-ok
```

- [ ] **Step 4: Manually verify verbose model list**

Run:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/appv231 \
  --dotenv .env \
  --cwd . \
  --provider openrouter \
  --list-models \
  --verbose-models | rg 'openrouter/openai/gpt-5.4-mini|context=|max_tokens='
```

Expected:

```text
openrouter/openai/gpt-5.4-mini context=400000 max_tokens=128000 ...
```

- [ ] **Step 5: Confirm red-zone untouched**

Run:

```bash
git diff --name-only HEAD
```

Expected changed paths only under:

```text
appV2.3.1/appv231/ai/providers/model_catalog.py
appV2.3.1/appv231/cli.py
appV2.3.1/appv231/tui/interactive_mode.py
appV2.3.1/tests/
docs/
```

If any red-zone file appears, stop and do not commit until Lewis explicitly approves a `kernel change`.

## Rollback

Rollback should be one or more normal commits, not destructive git:

```bash
git revert <commit-that-added-live-startup-hydration>
```

Runtime disable switch:

```bash
APPV231_MODEL_CATALOG_STARTUP_FETCH=false appv231 --provider openrouter --model openai/gpt-5.4-mini
```

This must preserve the old custom-model fallback behavior.

## Self-Review

- Spec coverage: live startup fetch, cache fallback, custom fallback preservation, model metadata display, TUI converter reuse, and red-zone avoidance are all covered.
- Placeholder scan: no placeholder markers or intentionally incomplete implementation steps remain.
- Type consistency: all planned code uses the existing `Model` dataclass without changing `ai/types.py`.
- Scope check: this plan is one focused yellow-zone provider ergonomics change, not the whole provider control plane.
