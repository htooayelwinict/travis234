"""Coding-session service, auth, provider, and tool wiring contracts."""


from __future__ import annotations


from tests._support_coding_agent import *  # noqa: F403


from travis.coding_agent.resource_loader import DefaultResourceLoader


def test_auth_storage_create_persists_api_key_runtime_and_fallback(tmp_path: Path, monkeypatch) -> None:
    from travis.coding_agent import AuthStorage

    auth_path = tmp_path / "auth.json"
    auth = AuthStorage.create(str(auth_path))
    auth.set("stored", {"type": "api_key", "key": "stored-key"})
    auth.set_runtime_api_key("runtime", "runtime-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-key")

    reloaded = AuthStorage.create(str(auth_path))

    assert reloaded.get("stored") == {"type": "api_key", "key": "stored-key"}
    assert reloaded.list() == ["stored"]
    assert reloaded.get_api_key("stored") == "stored-key"
    assert auth.get_api_key("runtime") == "runtime-key"
    assert reloaded.get_api_key("openrouter") == "env-key"
    assert reloaded.get_api_key("openrouter", {"includeFallback": False}) is None
    assert reloaded.get_auth_status("stored") == {"configured": True, "source": "stored"}

    reloaded.remove("stored")
    assert AuthStorage.create(str(auth_path)).get("stored") is None


def test_model_registry_create_loads_models_json_and_resolves_travis234_request_auth(tmp_path: Path) -> None:
    from travis.coding_agent import AuthStorage, ModelRegistry

    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    auth = AuthStorage.create(str(tmp_path / "auth.json"))
    auth.set("proxy", {"type": "api_key", "key": "stored-proxy-key"})
    models_path = tmp_path / "models.json"
    models_path.write_text(
        json.dumps(
            {
                "providers": {
                    "proxy": {
                        "name": "Proxy Provider",
                        "api": "faux",
                        "baseUrl": "https://proxy.example.test/v1",
                        "apiKey": "models-key",
                        "headers": {"X-Provider": "provider"},
                        "authHeader": True,
                        "models": [
                            {
                                "id": "fast",
                                "name": "Fast",
                                "headers": {"X-Model": "model"},
                                "cost": {
                                    "input": 1,
                                    "output": 2,
                                    "cacheRead": 0.1,
                                    "cacheWrite": 1.25,
                                    "tiers": [
                                        {
                                            "inputTokensAbove": 100000,
                                            "input": 2,
                                            "output": 4,
                                            "cacheRead": 0.2,
                                            "cacheWrite": 2.5,
                                        }
                                    ],
                                },
                                "contextWindow": 64000,
                                "maxTokens": 4096,
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    registry = ModelRegistry.create(auth, str(models_path))
    model = registry.find("proxy", "fast")

    assert model is not None
    assert model.name == "Fast"
    assert model.api == "faux"
    assert model.base_url == "https://proxy.example.test/v1"
    assert model.context_window == 64000
    assert [(tier.input_tokens_above, tier.input) for tier in model.cost.tiers] == [(100_000, 2.0)]
    assert registry.get_provider_display_name("proxy") == "Proxy Provider"
    assert registry.has_configured_auth(model) is True
    assert registry.get_available() == [model]
    assert registry.get_api_key_for_provider("proxy") == "stored-proxy-key"
    assert registry.get_api_key_and_headers(model) == {
        "ok": True,
        "apiKey": "stored-proxy-key",
        "headers": {
            "X-Provider": "provider",
            "X-Model": "model",
            "Authorization": "Bearer stored-proxy-key",
        },
    }


def test_create_agent_session_services_defaults_travis234_auth_storage_and_model_registry(tmp_path: Path) -> None:
    from travis.coding_agent import AuthStorage, ModelRegistry, create_agent_session_services

    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    agent_dir.mkdir()
    project.mkdir()
    (agent_dir / "auth.json").write_text(json.dumps({"proxy": {"type": "api_key", "key": "service-key"}}), encoding="utf-8")
    (agent_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "proxy": {
                        "api": "faux",
                        "baseUrl": "https://proxy.example.test/v1",
                        "apiKey": "models-key",
                        "models": [{"id": "service", "name": "Service"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    services = create_agent_session_services({"cwd": str(project), "agentDir": str(agent_dir)})

    assert isinstance(services["authStorage"], AuthStorage)
    assert isinstance(services["modelRegistry"], ModelRegistry)
    assert services["authStorage"].get_api_key("proxy") == "service-key"
    model = services["modelRegistry"].find("proxy", "service")
    assert model is not None
    assert services["modelRegistry"].get_api_key_and_headers(model)["apiKey"] == "service-key"


def test_create_agent_session_from_services_resolves_travis234_default_model(tmp_path: Path) -> None:
    from travis.coding_agent import SettingsManager, create_agent_session_from_services, create_agent_session_services

    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    agent_dir.mkdir()
    project.mkdir()
    (agent_dir / "auth.json").write_text(json.dumps({"proxy": {"type": "api_key", "key": "service-key"}}), encoding="utf-8")
    (agent_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "proxy": {
                        "api": "faux",
                        "baseUrl": "https://proxy.example.test/v1",
                        "apiKey": "models-key",
                        "models": [{"id": "service", "name": "Service"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    settings = SettingsManager.in_memory({"defaultProvider": "proxy", "defaultModel": "service"})
    services = create_agent_session_services(
        {
            "cwd": str(project),
            "agentDir": str(agent_dir),
            "settingsManager": settings,
        }
    )

    result = create_agent_session_from_services({"services": services})

    assert result.session.model.provider == "proxy"
    assert result.session.model.id == "service"
    assert result.model_fallback_message is None


def test_create_agent_session_streams_with_travis234_model_registry_auth_and_retry_settings(tmp_path: Path) -> None:
    from travis.ai.event_stream import create_assistant_message_event_stream
    from tests._provider_runtime import ApiProvider
    from travis.coding_agent import SettingsManager, create_agent_session

    captured: dict[str, object] = {}

    def stream(model, context, options=None):
        captured["api_key"] = getattr(options, "api_key", None)
        captured["headers"] = dict(getattr(options, "headers", {}) or {})
        captured["timeout_ms"] = getattr(options, "timeout_ms", None)
        captured["websocket_connect_timeout_ms"] = getattr(options, "websocket_connect_timeout_ms", None)
        captured["max_retries"] = getattr(options, "max_retries", None)
        captured["max_retry_delay_ms"] = getattr(options, "max_retry_delay_ms", None)
        s = create_assistant_message_event_stream()
        for event in text_response_events(model, "ok"):
            s.push(event)
        return s

    register_api_provider(ApiProvider(api="svc-faux", stream=stream, stream_simple=stream))
    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    agent_dir.mkdir()
    project.mkdir()
    (agent_dir / "auth.json").write_text(
        json.dumps({"proxy": {"type": "api_key", "key": "service-key"}}),
        encoding="utf-8",
    )
    (agent_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "proxy": {
                        "api": "svc-faux",
                        "baseUrl": "https://proxy.example.test/v1",
                        "apiKey": "models-key",
                        "headers": {"X-Provider": "provider"},
                        "authHeader": True,
                        "models": [{"id": "service", "name": "Service", "headers": {"X-Model": "model"}}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = create_agent_session(
        {
            "cwd": str(project),
            "agentDir": str(agent_dir),
            "settingsManager": SettingsManager.in_memory(
                {
                    "defaultProvider": "proxy",
                    "defaultModel": "service",
                    "httpIdleTimeoutMs": 0,
                    "websocketConnectTimeoutMs": 4321,
                    "retry": {"provider": {"maxRetries": 7, "maxRetryDelayMs": 4567}},
                }
            ),
        }
    )

    result.session.prompt("hi")

    assert captured == {
        "api_key": "service-key",
        "headers": {
            "X-Provider": "provider",
            "X-Model": "model",
            "Authorization": "Bearer service-key",
        },
        "timeout_ms": 2147483647,
        "websocket_connect_timeout_ms": 4321,
        "max_retries": 7,
        "max_retry_delay_ms": 4567,
    }


def test_create_agent_session_defaults_travis234_session_file_and_stream_session_id(tmp_path: Path) -> None:
    from travis.ai.event_stream import create_assistant_message_event_stream
    from tests._provider_runtime import ApiProvider
    from travis.coding_agent import SettingsManager, create_agent_session

    captured: dict[str, object] = {}

    def stream(model, context, options=None):
        captured["session_id"] = getattr(options, "session_id", None)
        captured["headers"] = dict(getattr(options, "headers", {}) or {})
        s = create_assistant_message_event_stream()
        for event in text_response_events(model, "ok"):
            s.push(event)
        return s

    register_api_provider(ApiProvider(api="svc-opencode", stream=stream, stream_simple=stream))
    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    agent_dir.mkdir()
    project.mkdir()
    (agent_dir / "auth.json").write_text(
        json.dumps({"opencode": {"type": "api_key", "key": "service-key"}}),
        encoding="utf-8",
    )
    (agent_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "opencode": {
                        "api": "svc-opencode",
                        "baseUrl": "https://opencode.ai/zen/v1",
                        "apiKey": "models-key",
                        "authHeader": True,
                        "models": [{"id": "service", "name": "Service"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = create_agent_session(
        {
            "cwd": str(project),
            "agentDir": str(agent_dir),
            "settingsManager": SettingsManager.in_memory(
                {"defaultProvider": "opencode", "defaultModel": "service", "defaultThinkingLevel": "low"}
            ),
        }
    )

    session_path = Path(result.session.session_path or "")
    safe_cwd = f"--{str(project.resolve()).lstrip(os.sep).replace(os.sep, '-').replace(':', '-')}--"
    assert session_path.parent == agent_dir / "sessions" / safe_cwd
    assert session_path.name.endswith(f"_{result.session.session_id}.jsonl")
    entries = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    assert entries[0]["id"] == result.session.session_id
    assert entries[0]["cwd"] == str(project.resolve())
    assert [entry["type"] for entry in entries[1:]] == ["model_change", "thinking_level_change"]
    assert entries[1]["provider"] == "opencode"
    assert entries[1]["modelId"] == "service"
    assert entries[2]["thinkingLevel"] == "low"

    result.session.prompt("hi")

    assert captured["session_id"] == result.session.session_id
    assert captured["headers"]["x-opencode-session"] == result.session.session_id
    assert captured["headers"]["x-opencode-client"] == "travis"
    assert captured["headers"]["Authorization"] == "Bearer service-key"


def test_create_agent_session_restores_existing_travis234_session_model_before_settings_default(tmp_path: Path) -> None:
    from travis.coding_agent import SettingsManager, create_agent_session

    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    agent_dir.mkdir()
    project.mkdir()
    (agent_dir / "auth.json").write_text(
        json.dumps(
            {
                "default": {"type": "api_key", "key": "default-key"},
                "saved": {"type": "api_key", "key": "saved-key"},
            }
        ),
        encoding="utf-8",
    )
    (agent_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "default": {
                        "api": "faux",
                        "baseUrl": "https://default.example.test/v1",
                        "apiKey": "default-key",
                        "models": [{"id": "service", "name": "Default"}],
                    },
                    "saved": {
                        "api": "faux",
                        "baseUrl": "https://saved.example.test/v1",
                        "apiKey": "saved-key",
                        "models": [{"id": "session", "name": "Saved"}],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    session_path = tmp_path / "existing.jsonl"
    store = SessionStore(str(session_path), cwd=str(project.resolve()))
    store.append_model_change("saved", "session")
    store.append_thinking_level_change("medium")
    store.append_message(UserMessage(content=[TextContent(text="previous")], timestamp=now_ms()))

    result = create_agent_session(
        {
            "cwd": str(project),
            "agentDir": str(agent_dir),
            "sessionPath": str(session_path),
            "settingsManager": SettingsManager.in_memory(
                {"defaultProvider": "default", "defaultModel": "service", "defaultThinkingLevel": "off"}
            ),
        }
    )

    assert result.session.model.provider == "saved"
    assert result.session.model.id == "session"
    assert result.session.thinking_level == "medium"
    assert result.model_fallback_message is None


def test_create_agent_session_ports_travis234_settings_request_options_to_agent_loop(tmp_path: Path) -> None:
    from travis.ai.event_stream import create_assistant_message_event_stream
    from travis.coding_agent import SettingsManager, create_agent_session

    captured: dict[str, object] = {}

    def stream(model, context, options=None):
        captured["transport"] = getattr(options, "transport", None)
        captured["thinking_budgets"] = getattr(options, "thinking_budgets", None)
        captured["max_retry_delay_ms"] = getattr(options, "max_retry_delay_ms", None)
        s = create_assistant_message_event_stream()
        for event in text_response_events(model, "ok"):
            s.push(event)
        return s

    result = create_agent_session(
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / "agent"),
            "model": faux_model(),
            "settingsManager": SettingsManager.in_memory(
                {
                    "transport": "websocket",
                    "thinkingBudgets": {"low": 1024, "medium": 2048},
                    "retry": {"provider": {"maxRetryDelayMs": 12345}},
                }
            ),
        }
    )

    result.session.prompt("hi", stream_fn=stream)

    assert captured == {
        "transport": "websocket",
        "thinking_budgets": {"low": 1024, "medium": 2048},
        "max_retry_delay_ms": 12345,
    }


def test_travis_provider_attribution_headers_match_travis_precedence(monkeypatch) -> None:
    from travis.coding_agent.agent_session_services import merge_provider_attribution_headers

    settings = SettingsManager.in_memory()
    openrouter = Model(
        id="m",
        name="m",
        api="faux",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
    )

    headers = merge_provider_attribution_headers(
        openrouter,
        settings,
        None,
        {"HTTP-Referer": "https://provider.example", "X-OpenRouter-Categories": "provider-category"},
        {"X-OpenRouter-Title": "request-title"},
    )

    assert headers == {
        "HTTP-Referer": "https://provider.example",
        "X-OpenRouter-Title": "request-title",
        "X-OpenRouter-Categories": "provider-category",
    }

    settings.set_enable_install_telemetry(False)
    assert merge_provider_attribution_headers(openrouter, settings, None) is None
    monkeypatch.setenv("TRAVIS234_TELEMETRY", "YES")
    assert merge_provider_attribution_headers(openrouter, settings, None)["X-OpenRouter-Title"] == "travis"

    nvidia = Model(id="m", name="m", api="faux", provider="nvidia", base_url="https://example.test/v1")
    assert merge_provider_attribution_headers(nvidia, settings, None)["X-BILLING-INVOKE-ORIGIN"] == "travis"

    opencode = Model(id="m", name="m", api="faux", provider="opencode", base_url="https://opencode.ai/zen/v1")
    assert merge_provider_attribution_headers(opencode, settings, "session-1") == {
        "x-opencode-session": "session-1",
        "x-opencode-client": "travis",
    }


def test_exported_create_agent_session_matches_travis234_sdk_result_factory(tmp_path: Path) -> None:
    from travis.coding_agent import (
        CreateAgentSessionResult,
        SettingsManager,
        create_agent_session,
    )

    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    agent_dir = tmp_path / "agent"
    project = tmp_path / "repo"
    agent_dir.mkdir()
    project.mkdir()
    (agent_dir / "auth.json").write_text(json.dumps({"proxy": {"type": "api_key", "key": "service-key"}}), encoding="utf-8")
    (agent_dir / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "proxy": {
                        "api": "faux",
                        "baseUrl": "https://proxy.example.test/v1",
                        "apiKey": "models-key",
                        "models": [{"id": "service", "name": "Service"}],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = create_agent_session(
        {
            "cwd": str(project),
            "agentDir": str(agent_dir),
            "settingsManager": SettingsManager.in_memory(
                {"defaultProvider": "proxy", "defaultModel": "service"}
            ),
        }
    )

    assert isinstance(result, CreateAgentSessionResult)
    assert result.session.model.provider == "proxy"
    assert result.session.model.id == "service"


def test_create_agent_session_ports_travis234_no_tools_option(tmp_path: Path) -> None:
    from travis.coding_agent import create_agent_session

    result = create_agent_session(
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / "agent"),
            "model": faux_model(),
            "noTools": True,
        }
    )

    assert result.session.get_active_tool_names() == []


def test_create_agent_session_ports_travis234_custom_tools_without_replacing_builtins(tmp_path: Path) -> None:
    from travis.coding_agent import create_agent_session
    from travis.coding_agent.tools.types import ToolDefinition

    definition = ToolDefinition(
        name="custom",
        label="custom",
        description="Custom SDK tool",
        parameters={"type": "object", "properties": {}},
        execute=lambda tool_call_id, args, signal=None, on_update=None, ctx=None: AgentToolResult(
            content=[TextContent(text="ok")],
            details={},
        ),
        prompt_snippet="Run custom SDK tool",
    )

    result = create_agent_session(
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / "agent"),
            "model": faux_model(),
            "customTools": [definition],
        }
    )

    tool_names = {tool["name"] for tool in result.session.get_all_tools()}
    assert {"read", "bash", "edit", "write", "custom"} <= tool_names
    assert "append" not in tool_names
    assert result.session.get_active_tool_names() == ["read", "bash", "edit", "write"]


def test_create_agent_session_wraps_convert_to_llm_with_travis234_block_images_setting(tmp_path: Path) -> None:
    from travis.coding_agent import create_agent_session

    settings = SettingsManager.in_memory({"images": {"blockImages": True}})
    result = create_agent_session(
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / "agent"),
            "model": faux_model(),
            "settingsManager": settings,
        }
    )

    converted = result.session._convert_to_llm(
        [
            UserMessage(
                content=[
                    TextContent(text="before"),
                    ImageContent(data="aW1hZ2Ux", mime_type="image/png"),
                    ImageContent(data="aW1hZ2Uy", mime_type="image/jpeg"),
                    TextContent(text="after"),
                ]
            ),
            ToolResultMessage(
                tool_call_id="call-1",
                tool_name="read",
                content=[
                    ImageContent(data="aW1hZ2Uz", mime_type="image/png"),
                    TextContent(text="tool text"),
                ],
                is_error=False,
            ),
        ]
    )

    assert converted[0].content == [
        TextContent(text="before"),
        TextContent(text="Image reading is disabled."),
        TextContent(text="after"),
    ]
    assert converted[1].content == [
        TextContent(text="Image reading is disabled."),
        TextContent(text="tool text"),
    ]


def test_default_resource_loader_ports_travis234_inline_extension_factories(tmp_path: Path) -> None:
    from travis.coding_agent import DefaultResourceLoader, ExtensionRunner

    def extension_factory(travis: ExtensionRunner) -> None:
        travis.register_flag("mode", {"type": "string", "default": "safe"})
        travis.register_provider(
            "proxy",
            {
                "api": "faux",
                "baseUrl": "https://proxy.example.test/v1",
                "apiKey": "factory-key",
                "models": [{"id": "factory", "name": "Factory"}],
            },
        )

    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        extension_factories=[extension_factory],
    )
    loader.reload()

    extensions = loader.get_extensions()
    runtime = extensions["runtime"]

    assert isinstance(runtime, ExtensionRunner)
    assert runtime.get_flags()["mode"].default == "safe"
    assert runtime.get_flag("mode") == "safe"
    assert runtime.pending_provider_registrations == [
        (
            "proxy",
            {
                "api": "faux",
                "baseUrl": "https://proxy.example.test/v1",
                "apiKey": "factory-key",
                "models": [{"id": "factory", "name": "Factory"}],
            },
            "<inline:1>",
        )
    ]


def test_staged_resource_reload_reuses_pretrust_runtime_without_reexecuting_factories(
    tmp_path: Path,
) -> None:
    from travis.coding_agent import DefaultResourceLoader, ExtensionRunner

    calls: list[str] = []

    def extension_factory(runner: ExtensionRunner) -> None:
        calls.append("factory")
        runner.register_flag("profile", {"type": "string"})

    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        extension_factories=[extension_factory],
    )

    pretrust = loader.load_project_trust_extensions()
    pretrust_runtime = pretrust["runtime"]
    loader.complete_reload(
        {"projectTrustOverride": False},
        pretrust_extensions=pretrust,
    )

    assert calls == ["factory"]
    assert loader.get_extensions()["runtime"] is pretrust_runtime
    assert "profile" in pretrust_runtime.get_flags()

    ordinary_calls: list[str] = []

    def ordinary_factory(runner: ExtensionRunner) -> None:
        ordinary_calls.append("factory")
        runner.register_flag("verbose", {"type": "boolean"})

    ordinary_loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "ordinary-agent"),
        extension_factories=[ordinary_factory],
    )
    ordinary_loader.reload({"projectTrustOverride": False})
    ordinary_runtime = ordinary_loader.get_extensions()["runtime"]

    assert ordinary_calls == ["factory"]
    assert "verbose" in ordinary_runtime.get_flags()


def test_create_agent_session_services_ports_travis234_provider_and_flag_diagnostics(tmp_path: Path) -> None:
    from travis.coding_agent import ExtensionRunner, create_agent_session_services

    def extension_factory(travis: ExtensionRunner) -> None:
        travis.register_flag("verbose", {"type": "boolean"})
        travis.register_flag("profile", {"type": "string"})
        travis.register_provider(
            "proxy",
            {
                "api": "faux",
                "baseUrl": "https://proxy.example.test/v1",
                "apiKey": "factory-key",
                "models": [{"id": "factory", "name": "Factory"}],
            },
        )

    services = create_agent_session_services(
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / "agent"),
            "resourceLoaderOptions": {"extension_factories": [extension_factory]},
            "extensionFlagValues": {"verbose": False, "profile": "debug", "missing": True},
        }
    )
    runtime = services["resourceLoader"].get_extensions()["runtime"]
    model = services["modelRegistry"].find("proxy", "factory")

    assert model is not None
    assert services["modelRegistry"].get_api_key_and_headers(model)["apiKey"] == "factory-key"
    assert runtime.get_flag("verbose") is True
    assert runtime.get_flag("profile") == "debug"
    assert runtime.pending_provider_registrations == []
    assert services["diagnostics"] == [{"type": "error", "message": "Unknown option: --missing"}]
    assert type(services) is dict
    assert set(services) == {
        "cwd",
        "agentDir",
        "settingsManager",
        "resourceLoader",
        "authStorage",
        "modelRegistry",
        "sessionCatalog",
        "sessionPath",
        "sessionId",
        "operationRuntime",
        "diagnostics",
    }


def test_create_agent_session_from_services_uses_loaded_extension_runtime(tmp_path: Path) -> None:
    from travis.coding_agent import ExtensionRunner, create_agent_session_from_services, create_agent_session_services

    def extension_factory(travis: ExtensionRunner) -> None:
        travis.register_command(
            "service-hello",
            {
                "description": "Service hello",
                "handler": lambda args, ctx: ctx.send_message(
                    {
                        "customType": "service-hello",
                        "content": "hello from service extension",
                    }
                ),
            },
        )

    services = create_agent_session_services(
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / "agent"),
            "resourceLoaderOptions": {"extension_factories": [extension_factory]},
        }
    )
    result = create_agent_session_from_services({"services": services, "model": faux_model()})

    result.session.prompt("/service-hello")

    assert result.session.extension_runner is services["resourceLoader"].get_extensions()["runtime"]
    assert any(
        getattr(message, "role", None) == "custom" and getattr(message, "custom_type", None) == "service-hello"
        for message in result.session.messages
    )


def test_agent_session_runs_read_tool_call(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("file body here", encoding="utf-8")
    model = faux_model()
    calls = {"n": 0}

    def script(m, c):
        calls["n"] += 1
        if calls["n"] == 1:
            return tool_call_response_events(m, "read", {"path": "hello.txt"})
        return text_response_events(m, "The file says: file body here")

    register_api_provider(create_faux_provider(script))
    session = AgentSession(cwd=str(tmp_path), model=model)
    session.prompt("read hello.txt")
    roles = [getattr(msg, "role", None) for msg in session.messages]
    assert "toolResult" in roles
    tool_results = [m for m in session.messages if getattr(m, "role", None) == "toolResult"]
    assert "file body here" in tool_results[0].content[0].text
    assert calls["n"] == 2


def test_internal_subagent_exposes_only_canonical_bash_tool(tmp_path: Path) -> None:
    model = faux_model()
    session = AgentSession(cwd=str(tmp_path), model=model)
    child_with_bash = AgentSession(
        cwd=str(tmp_path),
        model=model,
        active_tool_names=["read", "bash"],
        allowed_tool_names=["read", "bash"],
    )
    try:
        assert not hasattr(session, "_install_subagent_tool_aliases")
        assert child_with_bash.get_active_tool_names() == ["read", "bash"]
        assert child_with_bash.get_tool_definition("bash") is not None
        assert child_with_bash.get_tool_definition("run") is None
    finally:
        child_with_bash.shutdown()
        session.shutdown()


def test_agent_session_allows_repeated_same_path_write_batch_then_recovers_with_read_edit(tmp_path: Path) -> None:
    model = faux_model()
    provider_calls = {"n": 0}
    executions: list[str] = []

    def multi_write_events(model):
        calls = [
            ToolCall(id="call_1", name="write", arguments={"path": "LOCAL_REVIEW.md", "content": "first"}),
            ToolCall(id="call_2", name="write", arguments={"path": "./LOCAL_REVIEW.md", "content": "second"}),
        ]
        partial = AssistantMessage(
            content=list(calls),
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="toolUse",
            timestamp=now_ms(),
        )
        final = AssistantMessage(
            content=list(calls),
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="toolUse",
            timestamp=now_ms(),
        )
        events = [StartEvent(partial=partial)]
        for index, tool_call in enumerate(calls):
            events.append(ToolcallStartEvent(content_index=index, partial=partial))
            events.append(ToolcallEndEvent(content_index=index, tool_call=tool_call, partial=partial))
        events.append(DoneEvent(reason="toolUse", message=final))
        return events

    def script(m, c):
        provider_calls["n"] += 1
        if provider_calls["n"] == 1:
            return multi_write_events(m)
        if provider_calls["n"] == 2:
            return tool_call_response_events(
                m,
                "read",
                {"path": "LOCAL_REVIEW.md"},
                call_id="call_read",
            )
        if provider_calls["n"] == 3:
            return tool_call_response_events(
                m,
                "edit",
                {
                    "path": "LOCAL_REVIEW.md",
                    "old": "second",
                    "new": "second\n\n## Boundary check\n- one\n- two\n- three\n",
                },
                call_id="call_edit",
            )
        return text_response_events(m, "recovered after read and edit")

    def write_execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append(args["content"])
        (tmp_path / args["path"]).write_text(args["content"], encoding="utf-8")
        return AgentToolResult(content=[TextContent(text=f"wrote:{args['content']}")], details={})

    def read_execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append("read")
        return AgentToolResult(content=[TextContent(text=(tmp_path / args["path"]).read_text(encoding="utf-8"))], details={})

    def edit_execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append("edit")
        target = tmp_path / args["path"]
        content = target.read_text(encoding="utf-8")
        target.write_text(content.replace(args["old"], args["new"], 1), encoding="utf-8")
        return AgentToolResult(content=[TextContent(text="edited")], details={})

    register_api_provider(create_faux_provider(script))
    write_definition = ToolDefinition(
        name="write",
        label="Write",
        description="write",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        execute=write_execute,
    )
    read_definition = ToolDefinition(
        name="read",
        label="Read",
        description="read",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        execute=read_execute,
    )
    edit_definition = ToolDefinition(
        name="edit",
        label="Edit",
        description="edit",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
        },
        execute=edit_execute,
    )
    session = AgentSession(cwd=str(tmp_path), model=model, tool_definitions=[write_definition, read_definition, edit_definition])

    session.prompt("write twice then recover")

    tool_result_text = "\n".join(
        _content_text(message.content)
        for message in session.messages
        if getattr(message, "role", None) == "toolResult"
    )
    user_message_text = "\n".join(
        _content_text(message.content)
        for message in session.messages
        if getattr(message, "role", None) == "user"
    )
    assert executions == ["first", "second", "read", "edit"]
    assert provider_calls["n"] == 4
    assert "repeated_file_mutation_block" not in tool_result_text
    assert "repeated_file_mutation_warning" not in tool_result_text
    assert "repeated_file_mutation_warning" not in user_message_text
    assert session.messages[-1].role == "assistant"
    assert session.messages[-1].content[0].text == "recovered after read and edit"
