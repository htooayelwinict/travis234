from __future__ import annotations

import json
import urllib.error
from dataclasses import replace

from travis.ai.providers import model_catalog
from travis.ai.types import Cost, Model


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
    assert model.cost.input == 0.75
    assert model.cost.output == 4.5
    assert model.cost.cache_read == 0.075
    metadata = model_catalog.get_live_openrouter_model_metadata(model)
    assert metadata["supported_parameters"] == [
        "include_reasoning",
        "max_completion_tokens",
        "max_tokens",
        "reasoning",
        "response_format",
        "seed",
        "structured_outputs",
        "tool_choice",
        "tools",
    ]
    assert metadata["pricing"]["prompt"] == "0.00000075"


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


def test_openrouter_live_catalog_item_caps_fallback_output_for_small_context() -> None:
    item = {
        "id": "small/context",
        "name": "Small Context",
        "context_length": 4096,
        "top_provider": {},
    }

    model = model_catalog.openrouter_live_catalog_item_to_model(item, _base_model())

    assert model is not None
    assert model.context_window == 4096
    assert model.max_tokens == 2048
    assert model.max_tokens < model.context_window


def test_openrouter_live_catalog_item_rejects_missing_id() -> None:
    assert model_catalog.openrouter_live_catalog_item_to_model({}, _base_model()) is None


def test_openrouter_live_catalog_item_does_not_inherit_base_reasoning_without_live_support() -> None:
    base_model = replace(_base_model(), reasoning=True)
    item = {
        "id": "plain/model",
        "name": "Plain Model",
        "supported_parameters": ["tools", "tool_choice"],
    }

    model = model_catalog.openrouter_live_catalog_item_to_model(item, base_model)

    assert model is not None
    assert model.reasoning is False


def test_openrouter_live_catalog_item_rejects_invalid_pricing_values() -> None:
    base_model = replace(_base_model(), cost=Cost(input=1.0, output=2.0, cache_read=3.0, cache_write=4.0))
    item = {
        "id": "bad/pricing",
        "name": "Bad Pricing",
        "pricing": {
            "prompt": "-0.000001",
            "completion": "nan",
            "input_cache_read": "inf",
            "input_cache_write": "0.0000015",
        },
    }

    model = model_catalog.openrouter_live_catalog_item_to_model(item, base_model)

    assert model is not None
    assert model.cost.input == 1.0
    assert model.cost.output == 2.0
    assert model.cost.cache_read == 3.0
    assert model.cost.cache_write == 1.5


def test_get_live_openrouter_models_fetches_and_caches(tmp_path, monkeypatch) -> None:
    model_catalog.reset_cache()
    monkeypatch.setenv("TRAVIS234_HOME", str(tmp_path))
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


def test_get_live_openrouter_models_reuses_payload_cache_with_current_base_model(tmp_path, monkeypatch) -> None:
    model_catalog.reset_cache()
    monkeypatch.setenv("TRAVIS234_HOME", str(tmp_path))
    payload = {
        "data": [
            {
                "id": "openai/gpt-5.4-mini",
                "name": "OpenAI: GPT-5.4 Mini",
                "context_length": 400000,
                "top_provider": {"max_completion_tokens": 128000},
            }
        ]
    }
    calls = {"count": 0}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(payload).encode()

    def fake_urlopen(request, timeout):
        calls["count"] += 1
        return FakeResponse()

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fake_urlopen)
    first_base = replace(_base_model(), api="openai-completions", base_url="https://first.example.test/api")
    second_base = replace(_base_model(), api="openai-responses", base_url="https://second.example.test/api")

    first_models = model_catalog.get_live_openrouter_models(base_model=first_base, force_refresh=True)
    second_models = model_catalog.get_live_openrouter_models(base_model=second_base)

    assert calls["count"] == 1
    assert first_models[0].api == "openai-completions"
    assert first_models[0].base_url == "https://first.example.test/api"
    assert second_models[0].api == "openai-responses"
    assert second_models[0].base_url == "https://second.example.test/api"


def test_get_live_openrouter_models_uses_stale_cache_when_fetch_fails(tmp_path, monkeypatch) -> None:
    model_catalog.reset_cache()
    monkeypatch.setenv("TRAVIS234_HOME", str(tmp_path))
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


def test_find_live_openrouter_model_uses_cache_by_default(tmp_path, monkeypatch) -> None:
    model_catalog.reset_cache()
    monkeypatch.setenv("TRAVIS234_HOME", str(tmp_path))
    payload = {
        "data": [
            {
                "id": "openai/gpt-5.4-mini",
                "name": "OpenAI: GPT-5.4 Mini",
                "context_length": 400000,
                "top_provider": {"max_completion_tokens": 128000},
            }
        ]
    }
    calls = {"count": 0}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(payload).encode()

    def fake_urlopen(request, timeout):
        calls["count"] += 1
        return FakeResponse()

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fake_urlopen)

    first = model_catalog.find_live_openrouter_model("openai/gpt-5.4-mini", base_model=_base_model())
    second = model_catalog.find_live_openrouter_model("openai/gpt-5.4-mini", base_model=_base_model())

    assert first is not None
    assert second is not None
    assert calls["count"] == 1


def test_get_live_openrouter_models_clears_removed_model_metadata_on_refresh(tmp_path, monkeypatch) -> None:
    model_catalog.reset_cache()
    monkeypatch.setenv("TRAVIS234_HOME", str(tmp_path))
    payloads = [
        {
            "data": [
                {
                    "id": "removed/model",
                    "name": "Removed Model",
                    "pricing": {"prompt": "0.000001"},
                    "supported_parameters": ["tools"],
                }
            ]
        },
        {
            "data": [
                {
                    "id": "new/model",
                    "name": "New Model",
                    "pricing": {"prompt": "0.000002"},
                    "supported_parameters": ["reasoning"],
                }
            ]
        },
    ]

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(self._payload).encode()

    def fake_urlopen(request, timeout):
        return FakeResponse(payloads.pop(0))

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fake_urlopen)

    first_models = model_catalog.get_live_openrouter_models(base_model=_base_model(), force_refresh=True)
    removed_model = first_models[0]
    assert model_catalog.get_live_openrouter_model_metadata(removed_model)["pricing"]["prompt"] == "0.000001"

    model_catalog.get_live_openrouter_models(base_model=_base_model(), force_refresh=True)

    assert model_catalog.get_live_openrouter_model_metadata(removed_model) == {}


def test_get_live_openrouter_models_uses_stale_cache_when_response_bytes_are_malformed(tmp_path, monkeypatch) -> None:
    model_catalog.reset_cache()
    monkeypatch.setenv("TRAVIS234_HOME", str(tmp_path))
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
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class BadBytesResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"\xff\xfe\xfa"

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", lambda request, timeout: BadBytesResponse())

    models = model_catalog.get_live_openrouter_models(base_model=_base_model(), force_refresh=True)

    assert [model.id for model in models] == ["openai/gpt-5.4-mini"]
    assert models[0].context_window == 400000


def test_get_live_openrouter_models_ignores_malformed_disk_cache_bytes(tmp_path, monkeypatch) -> None:
    model_catalog.reset_cache()
    monkeypatch.setenv("TRAVIS234_HOME", str(tmp_path))
    cache_path = model_catalog._openrouter_live_cache_path()
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(b"\xff\xfe\xfa")

    def fail_urlopen(request, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fail_urlopen)

    assert model_catalog.get_live_openrouter_models(base_model=_base_model(), force_refresh=True) == []


def test_get_live_openrouter_models_uses_fresh_disk_cache_before_network(tmp_path, monkeypatch) -> None:
    model_catalog.reset_cache()
    monkeypatch.setenv("TRAVIS234_HOME", str(tmp_path))
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
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    def fail_urlopen(request, timeout):
        raise AssertionError("fresh OpenRouter disk cache should be used before network")

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fail_urlopen)

    models = model_catalog.get_live_openrouter_models(base_model=_base_model())

    assert [model.id for model in models] == ["openai/gpt-5.4-mini"]
    assert models[0].context_window == 400000


def test_get_live_openrouter_models_records_fetch_error_when_no_cache(tmp_path, monkeypatch) -> None:
    model_catalog.reset_cache()
    monkeypatch.setenv("TRAVIS234_HOME", str(tmp_path))

    def fail_urlopen(request, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fail_urlopen)

    assert model_catalog.get_live_openrouter_models(base_model=_base_model(), force_refresh=True) == []
    assert isinstance(model_catalog.get_last_openrouter_live_catalog_error(), urllib.error.URLError)


def test_get_live_openrouter_models_ignores_startup_fetch_flag_for_direct_lookup(tmp_path, monkeypatch) -> None:
    model_catalog.reset_cache()
    monkeypatch.setenv("TRAVIS234_HOME", str(tmp_path))
    monkeypatch.setenv("TRAVIS234_MODEL_CATALOG_STARTUP_FETCH", "false")
    payload = {
        "data": [
            {
                "id": "openai/gpt-5.4-mini",
                "name": "OpenAI: GPT-5.4 Mini",
                "context_length": 400000,
                "top_provider": {"max_completion_tokens": 128000},
            }
        ]
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(payload).encode()

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", lambda request, timeout: FakeResponse())

    models = model_catalog.get_live_openrouter_models(base_model=_base_model())

    assert [model.id for model in models] == ["openai/gpt-5.4-mini"]


def test_get_live_openrouter_models_rereads_disk_cache_after_empty_fetch_without_network(tmp_path, monkeypatch) -> None:
    model_catalog.reset_cache()
    monkeypatch.setenv("TRAVIS234_HOME", str(tmp_path))
    calls = {"count": 0}

    def fail_urlopen(request, timeout):
        calls["count"] += 1
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fail_urlopen)

    assert model_catalog.get_live_openrouter_models(base_model=_base_model(), force_refresh=True) == []

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
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    models = model_catalog.get_live_openrouter_models(base_model=_base_model())

    assert [model.id for model in models] == ["openai/gpt-5.4-mini"]
    assert calls["count"] == 1


def test_get_live_openrouter_models_dedupes_duplicate_live_ids(tmp_path, monkeypatch) -> None:
    model_catalog.reset_cache()
    monkeypatch.setenv("TRAVIS234_HOME", str(tmp_path))
    payload = {
        "data": [
            {
                "id": "openai/gpt-5.4-mini",
                "name": "Old Metadata",
                "context_length": 128000,
                "top_provider": {"max_completion_tokens": 8192},
            },
            {
                "id": "openai/gpt-5.4-mini",
                "name": "OpenAI: GPT-5.4 Mini",
                "context_length": 400000,
                "top_provider": {"max_completion_tokens": 128000},
            },
        ]
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(payload).encode()

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", lambda request, timeout: FakeResponse())

    models = model_catalog.get_live_openrouter_models(base_model=_base_model(), force_refresh=True)

    assert [model.id for model in models] == ["openai/gpt-5.4-mini"]
    assert models[0].name == "OpenAI: GPT-5.4 Mini"
    assert models[0].context_window == 400000


def test_get_live_openrouter_models_skips_malformed_items_without_dropping_catalog(tmp_path, monkeypatch) -> None:
    model_catalog.reset_cache()
    monkeypatch.setenv("TRAVIS234_HOME", str(tmp_path))
    payload = {
        "data": [
            {"name": "Missing ID"},
            "not-a-model",
            {
                "id": "openai/gpt-5.4-mini",
                "name": "OpenAI: GPT-5.4 Mini",
                "context_length": 400000,
                "top_provider": {"max_completion_tokens": 128000},
            },
        ]
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(payload).encode()

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", lambda request, timeout: FakeResponse())

    models = model_catalog.get_live_openrouter_models(base_model=_base_model(), force_refresh=True)

    assert [model.id for model in models] == ["openai/gpt-5.4-mini"]
    assert models[0].context_window == 400000


def test_get_live_openrouter_models_retries_after_empty_cache_ttl(tmp_path, monkeypatch) -> None:
    model_catalog.reset_cache()
    monkeypatch.setenv("TRAVIS234_HOME", str(tmp_path))
    now = {"value": 0.0}
    monkeypatch.setattr(model_catalog.time, "time", lambda: now["value"])
    calls = {"count": 0}
    payload = {
        "data": [
            {
                "id": "openai/gpt-5.4-mini",
                "name": "OpenAI: GPT-5.4 Mini",
                "context_length": 400000,
                "top_provider": {"max_completion_tokens": 128000},
            }
        ]
    }

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(payload).encode()

    def flaky_urlopen(request, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            raise urllib.error.URLError("offline")
        return FakeResponse()

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", flaky_urlopen)

    assert model_catalog.get_live_openrouter_models(base_model=_base_model(), force_refresh=True) == []

    now["value"] = model_catalog.OPENROUTER_LIVE_MODEL_CACHE_TTL_SECONDS + 1
    models = model_catalog.get_live_openrouter_models(base_model=_base_model())

    assert [model.id for model in models] == ["openai/gpt-5.4-mini"]
    assert calls["count"] == 2
