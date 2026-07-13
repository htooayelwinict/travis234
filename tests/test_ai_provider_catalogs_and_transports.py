from __future__ import annotations

from tests._support_ai_travis_env_provider import *  # noqa: F403


def test_travis_style_provider_catalog_exposes_openrouter_profile() -> None:
    from travis.ai.providers.catalog import get_provider_profile, list_provider_profiles

    profile = get_provider_profile("or")

    assert profile is not None
    assert profile.name == "openrouter"
    assert profile.api_mode == "chat_completions"
    assert profile.base_url == "https://openrouter.ai/api/v1"
    assert "OPENROUTER_API_KEY" in profile.env_vars
    assert profile in list_provider_profiles()

def test_travis_style_provider_catalog_descriptors_share_one_provider_universe() -> None:
    from travis.ai.providers.catalog import provider_catalog, provider_catalog_by_slug

    catalog = provider_catalog()
    by_slug = provider_catalog_by_slug()

    assert catalog
    assert by_slug["openrouter"].label == "OpenRouter"
    assert by_slug["openrouter"].tab == "keys"
    assert by_slug["openrouter"].api_key_env_vars == ("OPENROUTER_API_KEY",)
    assert by_slug["openai-codex"].tab == "accounts"
    assert by_slug["openai-codex"].auth_type == "oauth_external"
    assert by_slug["qwen-oauth"].tab == "accounts"
    assert by_slug["qwen-oauth"].base_url_env_var == "TRAVIS_QWEN_BASE_URL"
    assert [entry.order for entry in catalog] == list(range(len(catalog)))

def test_travis_style_model_catalog_fetches_fallback_and_keeps_stale_disk_cache(tmp_path, monkeypatch) -> None:
    import travis.ai.providers.model_catalog as model_catalog

    model_catalog.reset_cache()
    monkeypatch.setenv("TRAVIS234_HOME", str(tmp_path))
    monkeypatch.setenv("TRAVIS234_MODEL_CATALOG_ENABLED", "true")
    monkeypatch.setattr(model_catalog, "DEFAULT_CATALOG_URL", "https://primary.example/catalog.json")
    monkeypatch.setattr(model_catalog, "DEFAULT_CATALOG_FALLBACK_URLS", ("https://fallback.example/catalog.json",))

    manifest = {
        "version": 1,
        "providers": {
            "openrouter": {
                "models": [
                    {"id": "qwen/qwen3-coder-next", "description": "coding"},
                    {"id": "moonshotai/kimi-k2.6"},
                ]
            }
        },
    }
    calls: list[str] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(manifest).encode()

    def fake_urlopen(request, timeout):
        url = request.full_url
        calls.append(url)
        if "primary" in url:
            raise urllib.error.URLError("blocked")
        return FakeResponse()

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fake_urlopen)

    assert model_catalog.get_curated_openrouter_models(force_refresh=True) == [
        ("qwen/qwen3-coder-next", "coding"),
        ("moonshotai/kimi-k2.6", ""),
    ]
    assert calls == ["https://primary.example/catalog.json", "https://fallback.example/catalog.json"]

    def always_fail(_request, timeout):
        raise urllib.error.URLError("offline")

    model_catalog.reset_cache()
    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", always_fail)

    assert model_catalog.get_curated_openrouter_models(force_refresh=True) == [
        ("qwen/qwen3-coder-next", "coding"),
        ("moonshotai/kimi-k2.6", ""),
    ]

def test_travis_style_model_catalog_respects_provider_override_url(tmp_path, monkeypatch) -> None:
    import travis.ai.providers.model_catalog as model_catalog

    model_catalog.reset_cache()
    monkeypatch.setenv("TRAVIS234_HOME", str(tmp_path))
    monkeypatch.setattr(
        model_catalog,
        "_load_catalog_config",
        lambda: {
            "enabled": True,
            "url": "https://primary.example/catalog.json",
            "ttl_hours": 1,
            "providers": {"openrouter": {"url": "https://override.example/catalog.json"}},
        },
    )

    manifest = {
        "version": 1,
        "providers": {
            "openrouter": {
                "models": [
                    {"id": "qwen/qwen3-coder-next", "description": "override"},
                ]
            }
        },
    }
    calls: list[str] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(manifest).encode()

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fake_urlopen)

    assert model_catalog.get_curated_openrouter_models(force_refresh=True) == [
        ("qwen/qwen3-coder-next", "override"),
    ]
    assert calls == ["https://override.example/catalog.json"]

def test_travis_style_model_catalog_can_seed_cache_from_checkout(tmp_path, monkeypatch) -> None:
    import travis.ai.providers.model_catalog as model_catalog

    model_catalog.reset_cache()
    monkeypatch.setenv("TRAVIS234_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TRAVIS234_MODEL_CATALOG_ENABLED", "true")
    checkout = tmp_path / "checkout"
    manifest_path = checkout / "website" / "static" / "api" / "model-catalog.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "providers": {
                    "nous": {
                        "models": [
                            {"id": "test-model-405b"},
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert model_catalog.seed_cache_from_checkout(checkout) is True

    def fail_urlopen(_request, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fail_urlopen)
    assert model_catalog.get_curated_nous_models(force_refresh=True) == ["test-model-405b"]

def test_travis_style_chat_completions_transport_builds_openrouter_payload() -> None:
    from travis.ai.providers.catalog import get_provider_profile
    from travis.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=200,
        provider_preferences={"sort": "latency", "allow_fallbacks": True},
    )

    assert body["model"] == "qwen/qwen3-coder-next"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["tools"][0]["function"]["name"] == "read"
    assert body["stream"] is True
    assert body["temperature"] == 0
    assert body["max_tokens"] == 200
    assert body["provider"] == {"sort": "latency", "allow_fallbacks": True}

def test_travis_style_openrouter_qwen_tools_omit_unsupported_parallel_tool_calls() -> None:
    from travis.ai.providers.catalog import get_provider_profile
    from travis.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=[{"role": "user", "content": "write protocol fixture"}],
        tools=[{"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    assert "parallel_tool_calls" not in body

def test_travis_style_openrouter_glm_tools_omit_parallel_tool_calls_by_default() -> None:
    from travis.ai.providers.catalog import get_provider_profile
    from travis.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="z-ai/glm-5.2",
        messages=[{"role": "user", "content": "write protocol fixture"}],
        tools=[{"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    assert "parallel_tool_calls" not in body

def test_openrouter_qwen_protocol_literal_user_text_is_not_rewritten_like_travis234_provider() -> None:
    from travis.ai.providers.catalog import get_provider_profile
    from travis.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=[{"role": "user", "content": "Write literal <function=write> and </function> into a file."}],
        tools=[{"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    assert body["messages"] == [
        {"role": "user", "content": "Write literal <function=write> and </function> into a file."}
    ]
    user_content = body["messages"][0]["content"]
    assert "<function=write>" in user_content
    assert "\\u003cfunction=write\\u003e" not in user_content

def test_openrouter_non_qwen_protocol_literal_user_text_is_not_escaped() -> None:
    from travis.ai.providers.catalog import get_provider_profile
    from travis.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="anthropic/claude-sonnet-4.6",
        messages=[{"role": "user", "content": "Write literal <function=write> and </function> into a file."}],
        tools=[{"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    user_content = next(message["content"] for message in body["messages"] if message["role"] == "user")
    assert "<function=write>" in user_content
    assert "\\u003cfunction=write\\u003e" not in user_content

def test_travis_style_transport_exposes_convert_tools_boundary() -> None:
    from travis.ai.providers.catalog import get_provider_profile
    from travis.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)
    tools = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]

    assert transport.convert_tools(tools) == tools

def test_travis_style_transport_normalizes_chat_completion_response_provider_data() -> None:
    from travis.ai.providers.catalog import get_provider_profile
    from travis.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content="",
                    reasoning_content="think",
                    reasoning_details=[{"type": "reasoning.encrypted", "id": "call_1", "data": "sig"}],
                    tool_calls=[
                        SimpleNamespace(
                            id="call_1",
                            extra_content={"google": {"thought_signature": "sig"}},
                            function=SimpleNamespace(name="read", arguments='{"path":"README.md"}'),
                        )
                    ],
                ),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10),
    )

    normalized = transport.normalize_response(response)

    assert normalized.finish_reason == "tool_calls"
    assert normalized.reasoning == "think"
    assert normalized.usage.total_tokens == 10
    assert normalized.tool_calls[0].id == "call_1"
    assert normalized.tool_calls[0].name == "read"
    assert normalized.tool_calls[0].arguments == '{"path":"README.md"}'
    assert normalized.tool_calls[0].provider_data == {"extra_content": {"google": {"thought_signature": "sig"}}}
    assert normalized.provider_data == {"reasoning_content": "think", "reasoning_details": [{"type": "reasoning.encrypted", "id": "call_1", "data": "sig"}]}

def test_travis_style_openrouter_transport_does_not_force_parameter_support_for_tools() -> None:
    from travis.ai.providers.catalog import get_provider_profile
    from travis.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    assert "provider" not in body

def test_travis_style_openrouter_mandatory_anthropic_uses_verbosity_not_reasoning() -> None:
    from travis.ai.providers.catalog import get_provider_profile
    from travis.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="anthropic/claude-sonnet-4.6",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
        reasoning_config={"enabled": True, "effort": "high"},
    )

    assert "reasoning" not in body
    assert body["verbosity"] == "high"

def test_travis_style_chat_transport_strips_internal_replay_fields() -> None:
    from travis.ai.providers.catalog import get_provider_profile
    from travis.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)
    messages = [
        {
            "role": "assistant",
            "content": "",
            "timestamp": 123,
            "_empty_terminal_sentinel": True,
            "codex_reasoning_items": [{"id": "r1"}],
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "call_id": "codex-call",
                    "response_item_id": "item-1",
                    "extra_content": {"thought_signature": "provider-only"},
                    "function": {"name": "read", "arguments": "{\"path\":\"a.txt\"}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "read",
            "tool_name": "read",
            "timestamp": 456,
            "content": "ok",
        },
    ]

    body = transport.build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=messages,
        tools=None,
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    assistant = body["messages"][0]
    tool = body["messages"][1]
    tool_call = assistant["tool_calls"][0]
    assert "timestamp" not in assistant
    assert "_empty_terminal_sentinel" not in assistant
    assert "codex_reasoning_items" not in assistant
    assert "call_id" not in tool_call
    assert "response_item_id" not in tool_call
    assert "extra_content" not in tool_call
    assert "tool_name" not in tool
    assert "timestamp" not in tool

def test_travis_style_chat_transport_accepts_plain_valid_tool_call_arguments() -> None:
    from travis.ai.providers.catalog import get_provider_profile
    from travis.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{\"command\":\"mkdir -p docs\"}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "bash",
            "content": "(no output)",
        },
    ]

    body = transport.build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=messages,
        tools=None,
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    assert body["messages"] == messages

def test_travis_style_transport_merges_request_overrides_after_profile_body() -> None:
    from travis.ai.providers.catalog import get_provider_profile
    from travis.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
        provider_preferences={"sort": "latency"},
        request_overrides={
            "top_p": 0.9,
            "extra_body": {
                "provider": {"order": ["anthropic"]},
                "custom_marker": True,
            },
        },
    )

    assert body["top_p"] == 0.9
    assert body["custom_marker"] is True
    assert body["provider"] == {
        "sort": "latency",
        "order": ["anthropic"],
    }

def test_travis_style_transport_uses_provider_profile_hooks() -> None:
    from travis.ai.providers.base import OMIT_TEMPERATURE, ProviderProfile
    from travis.ai.providers.transports import get_transport

    class HookedProfile(ProviderProfile):
        def prepare_messages(self, messages):
            prepared = list(messages)
            prepared.append({"role": "system", "content": "prepared-by-profile"})
            return prepared

        def build_extra_body(self, *, session_id=None, **context):
            return {"extra_body_marker": context["model"], "session_id": session_id}

        def build_api_kwargs_extras(self, *, reasoning_config=None, **context):
            return (
                {"reasoning": dict(reasoning_config or {})},
                {"extra_headers": {"x-profile": context["model"]}},
            )

    profile = HookedProfile(name="hooked", fixed_temperature=OMIT_TEMPERATURE)
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="hooked-model",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
        session_id="session-1",
        reasoning_config={"enabled": True, "effort": "medium"},
    )

    assert body["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "prepared-by-profile"},
    ]
    assert "temperature" not in body
    assert body["extra_body_marker"] == "hooked-model"
    assert body["session_id"] == "session-1"
    assert body["reasoning"] == {"enabled": True, "effort": "medium"}
    assert body["extra_headers"] == {"x-profile": "hooked-model"}

def test_travis_style_transport_registry_covers_catalog_api_modes() -> None:
    from travis.ai.providers.catalog import list_provider_profiles
    from travis.ai.providers.transports import get_transport

    api_modes = sorted({profile.api_mode for profile in list_provider_profiles()})

    for api_mode in api_modes:
        transport = get_transport(api_mode)

        assert transport.api_mode == api_mode
        assert isinstance(transport.endpoint_path, str)
        assert transport.endpoint_path.startswith("/")

def test_travis_style_anthropic_transport_builds_messages_payload() -> None:
    from travis.ai.providers.catalog import get_provider_profile
    from travis.ai.providers.transports import get_transport

    profile = get_provider_profile("anthropic")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="claude-opus-4-8",
        messages=[
            {"role": "system", "content": "system contract"},
            {"role": "user", "content": "read it"},
            {
                "role": "assistant",
                "content": "using tool",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read", "arguments": "{\"path\":\"README.md\"}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "read",
                "content": "README content",
            },
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                },
            }
        ],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=200,
    )

    assert transport.endpoint_path == "/v1/messages"
    assert body["model"] == "claude-opus-4-8"
    assert body["stream"] is True
    assert body["max_tokens"] == 200
    assert body["system"] == [{"type": "text", "text": "system contract"}]
    assert body["tools"] == [
        {
            "name": "read",
            "description": "Read a file",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        }
    ]
    assert body["messages"][0] == {"role": "user", "content": "read it"}
    assert body["messages"][1]["role"] == "assistant"
    assert body["messages"][1]["content"] == [
        {"type": "text", "text": "using tool"},
        {"type": "tool_use", "id": "call_1", "name": "read", "input": {"path": "README.md"}},
    ]
    assert body["messages"][2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "README content", "is_error": False}],
    }

def test_travis_style_codex_responses_transport_builds_responses_payload() -> None:
    from travis.ai.providers.catalog import get_provider_profile
    from travis.ai.providers.transports import get_transport

    profile = get_provider_profile("openai-api")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="gpt-5.4",
        messages=[
            {"role": "system", "content": "system contract"},
            {"role": "user", "content": "read it"},
            {
                "role": "assistant",
                "content": "using tool",
                "tool_calls": [
                    {
                        "id": "call_1|fc_1",
                        "type": "function",
                        "function": {"name": "read", "arguments": "{\"path\":\"README.md\"}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1|fc_1",
                "name": "read",
                "content": "README content",
            },
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                },
            }
        ],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=200,
        session_id="session-1",
        reasoning_config={"enabled": True, "effort": "medium"},
    )

    assert transport.endpoint_path == "/responses"
    assert body["model"] == "gpt-5.4"
    assert body["stream"] is True
    assert body["store"] is False
    assert body["instructions"] == "system contract"
    assert body["prompt_cache_key"] == "session-1"
    assert body["tool_choice"] == "auto"
    assert body["parallel_tool_calls"] is True
    assert body["max_output_tokens"] == 200
    assert body["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert body["tools"] == [
        {
            "type": "function",
            "name": "read",
            "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            "strict": None,
        }
    ]
    assert body["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "read it"}]},
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "using tool", "annotations": []}],
            "status": "completed",
        },
        {"type": "function_call", "call_id": "call_1", "id": "fc_1", "name": "read", "arguments": "{\"path\":\"README.md\"}"},
        {"type": "function_call_output", "call_id": "call_1", "output": "README content"},
    ]

def test_travis_env_provider_uses_transport_endpoint_path(monkeypatch) -> None:
    from travis.ai.providers.base import ProviderProfile
    from travis.ai.providers.catalog import ResolvedProviderRuntime

    captured: dict[str, object] = {}

    runtime_profile = ProviderProfile(
        name="active-provider",
        api_mode="codex_responses",
        base_url="https://active.example/v1",
    )

    def fake_runtime(*args, **kwargs):
        return ResolvedProviderRuntime(
            provider="active-provider",
            requested_provider="active-provider",
            profile=runtime_profile,
            api_mode="codex_responses",
            transport="codex_responses",
            endpoint_path="/responses",
            base_url="https://active.example/v1",
            api_key_env_vars=("ACTIVE_API_KEY",),
            auth_type="api_key",
            source="test-runtime",
        )

    class FakeTransport:
        api_mode = "codex_responses"
        endpoint_path = "/responses"

        def build_kwargs(self, **kwargs):
            return {"model": kwargs["model"], "input": [], "stream": True}

    def fake_get_transport(api_mode):
        captured["api_mode"] = api_mode
        return FakeTransport()

    class FakeStream:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
                yield 'data: {"type":"response.completed","response":{"id":"resp_1","status":"completed"}}'
                yield "data: [DONE]"

    class FakeClient:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            return FakeStream()

    monkeypatch.setattr(travis_env, "resolve_provider_runtime", fake_runtime)
    monkeypatch.setattr(travis_env, "get_transport", fake_get_transport)
    monkeypatch.setattr(httpx, "Client", FakeClient)

    provider = _openrouter_provider()
    model = Model(
        id="gpt-5.4",
        name="GPT",
        api="openai-completions",
        provider="active-provider",
        base_url="",
    )

    events = list(provider.stream(model, Context(messages=[UserMessage("hi", timestamp=now_ms())])))

    assert events[-1].type == "done"
    assert captured["api_mode"] == "codex_responses"
    assert captured["url"] == "https://active.example/v1/responses"

def test_travis_env_provider_uses_travis_runtime_base_url_env_var(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai-proxy.example/v1")
    captured: dict[str, object] = {}

    class FakeStream:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield 'data: {"type":"response.completed","response":{"id":"resp_1","status":"completed"}}'

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return FakeStream()

    monkeypatch.setattr(travis_env.httpx, "Client", FakeClient)

    provider = _openrouter_provider()
    model = Model(id="gpt-5.4", name="GPT", api="openai-completions", provider="openai-api", base_url="")
    provider.stream(model, Context(messages=[UserMessage(content="hi")])).result_sync()

    assert captured["method"] == "POST"
    assert captured["url"] == "https://openai-proxy.example/v1/responses"

def test_travis_env_provider_resolves_transport_from_runtime_profile(monkeypatch) -> None:
    from travis.ai.providers.base import ProviderProfile
    from travis.ai.providers.catalog import ResolvedProviderRuntime

    captured: dict[str, object] = {}

    runtime_profile = ProviderProfile(
        name="active-provider",
        api_mode="chat_completions",
        base_url="https://active.example/v1",
    )

    def fake_runtime(*args, **kwargs):
        return ResolvedProviderRuntime(
            provider="active-provider",
            requested_provider="active-provider",
            profile=runtime_profile,
            api_mode="chat_completions",
            transport="openai_chat",
            endpoint_path="/active",
            base_url="https://active.example/v1",
            api_key_env_vars=("ACTIVE_API_KEY",),
            auth_type="api_key",
            source="test-runtime",
        )

    class FakeTransport:
        api_mode = "chat_completions"

        def build_kwargs(self, **kwargs):
            captured["profile"] = kwargs["profile"]
            captured["model"] = kwargs["model"]
            return {"model": kwargs["model"], "messages": kwargs["messages"], "stream": True, "from_active_transport": True}

    def fake_get_transport(api_mode):
        captured["api_mode"] = api_mode
        return FakeTransport()

    class FakeStream:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, json, headers):
            captured["json"] = json
            return FakeStream()

    monkeypatch.setattr(travis_env, "resolve_provider_runtime", fake_runtime)
    monkeypatch.setattr(travis_env, "get_transport", fake_get_transport)
    monkeypatch.setattr(httpx, "Client", FakeClient)

    provider = _openrouter_provider()
    model = Model(
        id="active/model",
        name="Active",
        api="openai-completions",
        provider="active-provider",
        base_url="",
    )

    events = list(provider.stream(model, Context(messages=[UserMessage("hi", timestamp=now_ms())])))

    assert events[-1].type == "done"
    assert captured["api_mode"] == "chat_completions"
    assert captured["profile"] is runtime_profile
    assert captured["model"] == "active/model"
    assert captured["json"]["from_active_transport"] is True

def test_travis_env_provider_delegates_payload_construction_to_transport(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeTransport:
        api_mode = "chat_completions"

        def build_kwargs(
            self,
            *,
            model,
            messages,
            tools,
            profile,
            stream,
            temperature,
            max_tokens,
            provider_preferences,
            request_overrides,
        ):
            captured["transport_model"] = model
            captured["transport_messages"] = messages
            captured["transport_tools"] = tools
            captured["transport_profile"] = profile
            captured["transport_stream"] = stream
            captured["transport_temperature"] = temperature
            captured["transport_max_tokens"] = max_tokens
            captured["transport_provider_preferences"] = provider_preferences
            captured["transport_request_overrides"] = request_overrides
            return {"model": model, "messages": messages, "stream": stream, "metadata": {"from_transport": True}}

    class FakeStream:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, json, headers):
            captured["json"] = json
            return FakeStream()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    provider = _openrouter_provider()
    provider.transport = FakeTransport()

    events = list(
        provider.stream(
            _model(),
            Context(messages=[UserMessage("hello", timestamp=now_ms())]),
            SimpleStreamOptions(max_tokens=99),
        )
    )

    assert events[-1].type == "done"
    assert captured["transport_model"] == "acme/x"
    assert captured["transport_profile"].name == "openrouter"
    assert captured["transport_stream"] is True
    assert captured["transport_temperature"] is None
    assert captured["transport_max_tokens"] == 99
    assert captured["transport_provider_preferences"] is None
    assert captured["transport_request_overrides"] == {}
    assert captured["json"]["metadata"] == {"from_transport": True}

def test_convert_messages_sanitizes_unpaired_surrogates_for_provider_payload() -> None:
    emoji = chr(0x1F648)
    high_surrogate = chr(0xD83D)
    low_surrogate = chr(0xDE48)
    ctx = Context(
        system_prompt=f"sys {high_surrogate}{emoji}",
        messages=[
            UserMessage(content=f"hello {high_surrogate}{emoji}{low_surrogate}", timestamp=now_ms()),
            UserMessage(content=[TextContent(text=f"part {low_surrogate}{emoji}")], timestamp=now_ms()),
            AssistantMessage(
                content=[
                    ThinkingContent(thinking=f"think {high_surrogate}{emoji}", thinking_signature="reasoning_content"),
                    TextContent(text=f"answer {emoji}{low_surrogate}"),
                ],
                api="openai-completions",
                provider="openrouter",
                model="acme/x",
                usage=empty_usage(),
                stop_reason="stop",
                timestamp=now_ms(),
            ),
            ToolResultMessage(
                tool_call_id="c1",
                tool_name="read",
                content=[TextContent(text=f"tool {high_surrogate}{emoji}")],
                is_error=False,
                timestamp=now_ms(),
            ),
        ],
    )

    messages, _tools = convert_messages(ctx, _model())

    assert messages[0]["content"] == f"sys {emoji}"
    assert messages[1]["content"] == f"hello {emoji}"
    assert messages[2]["content"][0]["text"] == f"part {emoji}"
    assert messages[3]["content"] == f"answer {emoji}"
    assert messages[3]["reasoning_content"] == f"think {emoji}"
    assert messages[4]["content"] == f"tool {emoji}"
    json.dumps(messages, ensure_ascii=False).encode("utf-8")

def test_convert_messages_preserves_user_image_content_parts() -> None:
    ctx = Context(
        messages=[
            UserMessage(
                content=[
                    TextContent(text="look"),
                    ImageContent(data="aW1n", mime_type="image/png"),
                ],
                timestamp=now_ms(),
            )
        ]
    )

    messages, _tools = convert_messages(ctx)

    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1n"}},
            ],
        }
    ]

def test_convert_messages_preserves_assistant_thinking_signature() -> None:
    model = Model(id="gpt-oss", name="GPT OSS", api="openai-completions", provider="opencode-go", base_url="")
    ctx = Context(
        messages=[
            AssistantMessage(
                content=[
                    ThinkingContent(thinking="first", thinking_signature="reasoning"),
                    ThinkingContent(thinking="second", thinking_signature="reasoning"),
                    TextContent(text="Visible"),
                ],
                api="openai-completions",
                provider="opencode-go",
                model="gpt-oss",
                usage=empty_usage(),
                stop_reason="stop",
                timestamp=now_ms(),
            )
        ]
    )

    messages, _tools = convert_messages(ctx, model)

    assert messages == [
        {
            "role": "assistant",
            "content": "Visible",
            "reasoning_content": "first\nsecond",
        }
    ]

def test_convert_messages_bridges_tool_result_images_for_image_models() -> None:
    model = Model(
        id="vision",
        name="Vision",
        api="openai-completions",
        provider="openrouter",
        base_url="",
        input=["text", "image"],
    )
    ctx = Context(
        messages=[
            ToolResultMessage(
                tool_call_id="c1",
                tool_name="read",
                content=[
                    TextContent(text="first text"),
                    ImageContent(data="Zmlyc3Q=", mime_type="image/png"),
                ],
                is_error=False,
                timestamp=now_ms(),
            ),
            ToolResultMessage(
                tool_call_id="c2",
                tool_name="read",
                content=[ImageContent(data="c2Vjb25k", mime_type="image/jpeg")],
                is_error=False,
                timestamp=now_ms(),
            ),
        ]
    )

    messages, _tools = convert_messages(ctx, model)

    assert messages == [
        {"role": "tool", "tool_call_id": "c1", "name": "read", "content": "first text"},
        {"role": "tool", "tool_call_id": "c2", "name": "read", "content": "(see attached image)"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Attached image(s) from tool result:"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,Zmlyc3Q="}},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,c2Vjb25k"}},
            ],
        },
    ]

def test_convert_messages_normalizes_cross_model_tool_call_ids_and_matching_results() -> None:
    raw_id = "call.bad+id-" + ("x" * 50) + "|openai-response-item"
    expected_id = ("call_bad_id-" + ("x" * 50))[:40]
    ctx = Context(
        messages=[
            AssistantMessage(
                content=[
                    TextContent(text=""),
                    ToolCall(id=raw_id, name="read", arguments={"path": "README.md"}),
                ],
                api="openai-responses",
                provider="openai",
                model="gpt-4.1",
                usage=empty_usage(),
                stop_reason="toolUse",
                timestamp=now_ms(),
            ),
            ToolResultMessage(
                tool_call_id=raw_id,
                tool_name="read",
                content=[TextContent(text="contents")],
                is_error=False,
                timestamp=now_ms(),
            ),
        ]
    )

    messages, _tools = convert_messages(ctx, _model())

    assert messages[0]["tool_calls"][0]["id"] == expected_id
    assert messages[1]["tool_call_id"] == expected_id

def test_convert_messages_inserts_travis234_synthetic_result_for_orphaned_tool_call() -> None:
    ctx = Context(
        messages=[
            AssistantMessage(
                content=[ToolCall(id="call_missing", name="read", arguments={"path": "README.md"})],
                api="openai-completions",
                provider="openrouter",
                model="acme/x",
                usage=empty_usage(),
                stop_reason="toolUse",
                timestamp=now_ms(),
            ),
            UserMessage(content=[TextContent(text="continue")], timestamp=now_ms()),
        ]
    )

    messages, _tools = convert_messages(ctx, _model())

    assert messages[1] == {
        "role": "tool",
        "tool_call_id": "call_missing",
        "name": "read",
        "content": "No result provided",
    }
    assert messages[2]["role"] == "user"

def test_convert_messages_skips_error_and_aborted_assistant_replay() -> None:
    ctx = Context(
        messages=[
            UserMessage(content=[TextContent(text="before")], timestamp=now_ms()),
            AssistantMessage(
                content=[TextContent(text="failed partial")],
                api="openai-completions",
                provider="openrouter",
                model="acme/x",
                usage=empty_usage(),
                stop_reason="error",
                error_message="provider failed",
                timestamp=now_ms(),
            ),
            AssistantMessage(
                content=[TextContent(text="aborted partial")],
                api="openai-completions",
                provider="openrouter",
                model="acme/x",
                usage=empty_usage(),
                stop_reason="aborted",
                timestamp=now_ms(),
            ),
            UserMessage(content=[TextContent(text="after")], timestamp=now_ms()),
        ]
    )

    messages, _tools = convert_messages(ctx, _model())

    assert [message["role"] for message in messages] == ["user", "user"]
    assert messages[0]["content"][0]["text"] == "before"
    assert messages[1]["content"][0]["text"] == "after"

def test_convert_messages_downgrades_images_for_non_vision_model() -> None:
    model = Model(id="text-only", name="Text", api="openai-completions", provider="openrouter", base_url="")
    ctx = Context(
        messages=[
            UserMessage(
                content=[
                    TextContent(text="look"),
                    ImageContent(data="aW1n", mime_type="image/png"),
                    ImageContent(data="aW1nMg==", mime_type="image/png"),
                ],
                timestamp=now_ms(),
            ),
            ToolResultMessage(
                tool_call_id="c1",
                tool_name="read",
                content=[
                    ImageContent(data="dG9vbA==", mime_type="image/png"),
                    TextContent(text="tool text"),
                ],
                is_error=False,
                timestamp=now_ms(),
            ),
        ]
    )

    messages, _tools = convert_messages(ctx, model)

    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "text", "text": "(image omitted: model does not support images)"},
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "name": "read",
            "content": "(tool image omitted: model does not support images)tool text",
        },
    ]
