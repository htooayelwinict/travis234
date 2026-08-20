from __future__ import annotations

from tests._support_coding_agent import *  # noqa: F403
from travis.ai.providers.params import (
    GenerationParams,
    generation_params_to_mapping,
    params_from_mapping,
)


def test_generation_param_snapshots_restore_latest_valid_active_branch(tmp_path: Path) -> None:
    store = SessionStore(str(tmp_path / "session.jsonl"), cwd=str(tmp_path))
    first = store.append_generation_params_change(
        params_from_mapping({"temperature": "0.2"}, source="session")
    )
    store.append_generation_params_change(
        params_from_mapping(
            {"temperature": "0.4", "max_tokens": "4096"},
            source="session",
        )
    )

    assert generation_params_to_mapping(store.build_context().generation_params) == {
        "temperature": 0.4,
        "max_tokens": 4096,
    }

    store.branch(first)

    assert generation_params_to_mapping(store.build_context().generation_params) == {
        "temperature": 0.2
    }


def test_generation_param_empty_snapshot_resets_and_invalid_snapshot_keeps_last_valid(
    tmp_path: Path,
) -> None:
    store = SessionStore(str(tmp_path / "session.jsonl"), cwd=str(tmp_path))
    store.append_generation_params_change(
        params_from_mapping({"temperature": "0.2"}, source="session")
    )
    store._append_entry(  # noqa: SLF001 - exercise defensive replay of malformed JSONL state.
        {
            "type": "generation_params_change",
            "params": {"api_key": "sk-secret"},
        },
        durable=True,
    )

    assert store.build_context().generation_params.temperature == 0.2

    store.append_generation_params_change(GenerationParams())

    assert generation_params_to_mapping(store.build_context().generation_params) == {}


def test_generation_param_snapshot_is_non_message_state(tmp_path: Path) -> None:
    store = SessionStore(str(tmp_path / "session.jsonl"), cwd=str(tmp_path))
    store.append_generation_params_change(
        params_from_mapping({"temperature": "0.2"}, source="session")
    )
    store.append_message(UserMessage("hello"))

    snapshot = store.build_context()

    assert len(snapshot.messages) == 1
    assert isinstance(snapshot.messages[0], UserMessage)
    assert snapshot.messages[0].content == "hello"
    assert generation_params_to_mapping(snapshot.generation_params) == {"temperature": 0.2}


def test_generation_param_snapshot_survives_branch_copy_and_export(tmp_path: Path) -> None:
    store = SessionStore(str(tmp_path / "source.jsonl"), cwd=str(tmp_path))
    store.append_generation_params_change(
        params_from_mapping({"temperature": "0.2"}, source="session")
    )
    leaf_id = store.append_message(UserMessage("hello"))

    branch_path = store.create_branched_session(
        leaf_id,
        path=str(tmp_path / "branch.jsonl"),
    )
    export_path = store.export_to_jsonl(str(tmp_path / "export.jsonl"))

    branched = SessionStore(branch_path, cwd=str(tmp_path))
    exported = SessionStore(export_path, cwd=str(tmp_path))
    assert generation_params_to_mapping(branched.build_context().generation_params) == {
        "temperature": 0.2
    }
    assert generation_params_to_mapping(exported.build_context().generation_params) == {
        "temperature": 0.2
    }


def test_agent_session_generation_overrides_resume_and_follow_active_branch(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(session_path),
    )
    branch_point = session._session_store.append_message(UserMessage("before params"))  # noqa: SLF001

    session.set_generation_param_override("temperature", "0.2")
    changed_leaf = session.get_session_leaf_id()

    assert session.generation_param_overrides.temperature == 0.2
    assert dict(session.generation_param_overrides.sources) == {"temperature": "session"}

    resumed = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(session_path),
    )
    assert resumed.generation_param_overrides.temperature == 0.2

    resumed.branch(branch_point)
    assert resumed.generation_param_overrides == GenerationParams()

    assert changed_leaf is not None
    resumed.branch(changed_leaf)
    assert resumed.generation_param_overrides.temperature == 0.2


def test_agent_session_generation_override_resets_are_idempotent(tmp_path: Path) -> None:
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(tmp_path / "session.jsonl"),
    )

    original_count = len(session.session_entries)
    session.reset_generation_param_override("temperature")
    session.reset_generation_param_overrides()
    assert len(session.session_entries) == original_count

    session.set_generation_param_override("temperature", "0.2")
    changed_count = len(session.session_entries)
    session.set_generation_param_override("temperature", "0.2")
    assert len(session.session_entries) == changed_count

    session.reset_generation_param_override("temperature")
    assert session.generation_param_overrides == GenerationParams()
    reset_count = len(session.session_entries)
    session.reset_generation_param_overrides()
    assert len(session.session_entries) == reset_count


def test_agent_session_generation_override_persistence_failure_is_atomic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(tmp_path / "session.jsonl"),
    )
    session.set_generation_param_override("temperature", "0.2")

    def fail_append(_params: GenerationParams) -> str:
        raise OSError("disk full")

    monkeypatch.setattr(
        session._session_store,  # noqa: SLF001 - verify append-before-publish atomicity.
        "append_generation_params_change",
        fail_append,
    )

    with pytest.raises(OSError, match="disk full"):
        session.set_generation_param_override("temperature", "0.4")

    assert session.generation_param_overrides.temperature == 0.2


def test_agent_session_tree_navigation_restores_generation_overrides(tmp_path: Path) -> None:
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(tmp_path / "session.jsonl"),
    )
    session._session_store.append_message(UserMessage("root"))  # noqa: SLF001
    session.set_generation_param_override("temperature", "0.2")
    params_leaf = session.get_session_leaf_id()
    session._session_store.append_message(UserMessage("after params"))  # noqa: SLF001
    session.set_generation_param_override("temperature", "0.4")

    assert params_leaf is not None
    result = session.navigate_tree(params_leaf)

    assert result == {"cancelled": False}
    assert session.generation_param_overrides.temperature == 0.2


def test_agent_session_extension_command_can_register_provider_override_without_reload(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    seen_base_urls: list[str] = []

    def provider(model, context):
        seen_base_urls.append(model.base_url)
        return text_response_events(model, "using override")

    register_api_provider(create_faux_provider(provider))

    def handler(args, ctx):
        runner.register_provider("faux", {"baseUrl": "http://localhost:8080/command"})

    runner.register_command("use-proxy", {"description": "Use proxy", "handler": handler})
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)

    command_result = session.prompt("/use-proxy")
    prompt_result = session.prompt("hello")

    assert command_result == []
    assert session.model.base_url == "http://localhost:8080/command"
    assert seen_base_urls == ["http://localhost:8080/command"]
    assert prompt_result[-1].content[0].text == "using override"

def test_agent_session_extension_command_can_unregister_provider_override(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    seen_base_urls: list[str] = []

    def provider(model, context):
        seen_base_urls.append(model.base_url)
        return text_response_events(model, "using current provider")

    register_api_provider(create_faux_provider(provider))

    def use_proxy(args, ctx):
        runner.register_provider("faux", {"baseUrl": "http://localhost:8080/command"})

    def clear_proxy(args, ctx):
        runner.unregister_provider("faux")

    runner.register_command("use-proxy", {"description": "Use proxy", "handler": use_proxy})
    runner.register_command("clear-proxy", {"description": "Clear proxy", "handler": clear_proxy})
    model = faux_model()
    model.base_url = "https://original.example.test"
    session = AgentSession(cwd=str(tmp_path), model=model, extension_runner=runner)

    session.prompt("/use-proxy")
    assert session.model.base_url == "http://localhost:8080/command"
    session.prompt("/clear-proxy")
    prompt_result = session.prompt("hello")

    assert session.model.base_url == "https://original.example.test"
    assert seen_base_urls == ["https://original.example.test"]
    assert prompt_result[-1].content[0].text == "using current provider"

def test_agent_session_extension_unregister_provider_removes_extension_models(tmp_path: Path) -> None:
    runner = ExtensionRunner()

    def add_provider(args, ctx):
        runner.register_provider(
            "proxy",
            {
                "baseUrl": "https://proxy.example.test",
                "apiKey": "test-key",
                "api": "faux",
                "models": [
                    {
                        "id": "proxy-model",
                        "name": "Proxy Model",
                        "reasoning": False,
                        "input": ["text"],
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        "contextWindow": 32000,
                        "maxTokens": 4096,
                    }
                ],
            },
        )

    def remove_provider(args, ctx):
        runner.unregister_provider("proxy")

    runner.register_command("add-proxy", {"description": "Add proxy", "handler": add_provider})
    runner.register_command("remove-proxy", {"description": "Remove proxy", "handler": remove_provider})
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)

    session.prompt("/add-proxy")
    assert session.model_registry.find("proxy", "proxy-model") is not None

    session.prompt("/remove-proxy")

    assert session.model_registry.find("proxy", "proxy-model") is None

def test_agent_session_extension_unregister_provider_restores_existing_models(tmp_path: Path) -> None:
    original = faux_model()
    original.provider = "proxy"
    original.id = "original-model"
    original.name = "Original Model"
    original.base_url = "https://original.example.test"
    register_model(original)
    runner = ExtensionRunner()

    def replace_provider(args, ctx):
        runner.register_provider(
            "proxy",
            {
                "baseUrl": "https://override.example.test",
                "apiKey": "test-key",
                "api": "faux",
                "models": [
                    {
                        "id": "override-model",
                        "name": "Override Model",
                        "reasoning": False,
                        "input": ["text"],
                        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                        "contextWindow": 32000,
                        "maxTokens": 4096,
                    }
                ],
            },
        )

    def remove_provider(args, ctx):
        runner.unregister_provider("proxy")

    runner.register_command("replace-proxy", {"description": "Replace proxy", "handler": replace_provider})
    runner.register_command("remove-proxy", {"description": "Remove proxy", "handler": remove_provider})
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)

    session.prompt("/replace-proxy")
    assert [model.id for model in session.model_registry.get_all() if model.provider == "proxy"] == ["override-model"]

    session.prompt("/remove-proxy")

    assert session.model_registry.find("proxy", "override-model") is None
    assert [model for model in session.model_registry.get_all() if model.provider == "proxy"] == [original]

def test_agent_session_extension_register_provider_validates_model_auth_config(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)
    model_config = {
        "id": "proxy-model",
        "name": "Proxy Model",
        "reasoning": False,
        "input": ["text"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": 32000,
        "maxTokens": 4096,
    }

    try:
        runner.register_provider("proxy", {"api": "faux", "apiKey": "test-key", "models": [model_config]})
        assert False, "expected missing baseUrl to be rejected"
    except RuntimeError as error:
        assert str(error) == 'Provider proxy: "baseUrl" is required when defining models.'

    try:
        runner.register_provider("proxy", {"baseUrl": "https://proxy.example.test", "api": "faux", "models": [model_config]})
        assert False, "expected missing apiKey/oauth to be rejected"
    except RuntimeError as error:
        assert str(error) == 'Provider proxy: "apiKey" or "oauth" is required when defining models.'

    try:
        runner.register_provider(
            "proxy",
            {"baseUrl": "https://proxy.example.test", "apiKey": "test-key", "models": [model_config]},
        )
        assert False, "expected missing api to be rejected"
    except RuntimeError as error:
        assert str(error) == 'Provider proxy, model proxy-model: no "api" specified.'

    try:
        runner.register_provider("proxy", {"streamSimple": lambda model, context, options=None: []})
        assert False, "expected streamSimple without api to be rejected"
    except RuntimeError as error:
        assert str(error) == 'Provider proxy: "api" is required when registering streamSimple.'

    runner.register_provider(
        "proxy",
        {
            "baseUrl": "https://proxy.example.test",
            "api": "faux",
            "oauth": {
                "name": "Proxy OAuth",
                "login": lambda callbacks: {"access": "token", "expires": 4_102_444_800_000},
                "refreshToken": lambda credentials: credentials,
                "getApiKey": lambda credentials: credentials["access"],
            },
            "models": [model_config],
        },
    )
    assert session.model_registry.find("proxy", "proxy-model") is not None

def test_agent_session_extension_provider_auth_status_tracks_api_key_and_oauth(tmp_path: Path, monkeypatch) -> None:
    runner = ExtensionRunner()
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)
    model_config = {
        "id": "proxy-model",
        "name": "Proxy Model",
        "reasoning": False,
        "input": ["text"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": 32000,
        "maxTokens": 4096,
    }
    monkeypatch.setenv("PROXY_API_KEY", "proxy-secret")

    runner.register_provider(
        "proxy",
        {
            "baseUrl": "https://proxy.example.test",
            "api": "faux",
            "apiKey": "$PROXY_API_KEY",
            "models": [model_config],
        },
    )
    model_registry = session.model_registry
    proxy_model = model_registry.find("proxy", "proxy-model")

    assert proxy_model is not None
    assert model_registry.has_configured_auth(proxy_model) is True
    assert model_registry.get_provider_auth_status("proxy") == {
        "configured": True,
        "source": "environment",
        "label": "PROXY_API_KEY",
    }
    assert model_registry.get_api_key_for_provider("proxy") == "proxy-secret"

    runner.unregister_provider("proxy")

    assert model_registry.get_provider_auth_status("proxy") == {"configured": False}
    assert model_registry.get_api_key_for_provider("proxy") is None

    runner.register_provider(
        "sso",
        {
            "baseUrl": "https://sso.example.test",
            "api": "faux",
            "oauth": {
                "name": "Corporate SSO",
                "login": lambda callbacks: {"access": "sso-token", "expires": 4_102_444_800_000},
                "refreshToken": lambda credentials: credentials,
                "getApiKey": lambda credentials: credentials["access"],
            },
            "models": [{**model_config, "id": "sso-model", "name": "SSO Model"}],
        },
    )
    model_registry.set_auth_credential(
        "sso",
        {"type": "oauth", "access": "sso-token", "refresh": "refresh-token", "expires": 4_102_444_800_000},
    )
    sso_model = model_registry.find("sso", "sso-model")

    assert sso_model is not None
    assert model_registry.has_configured_auth(sso_model) is True
    assert model_registry.get_provider_auth_status("sso") == {"configured": True, "source": "stored"}
    assert model_registry.get_api_key_for_provider("sso") == "sso-token"
    assert {"id": "sso", "name": "Corporate SSO"} in model_registry.get_oauth_providers()

    runner.unregister_provider("sso")

    assert all(provider["id"] != "sso" for provider in model_registry.get_oauth_providers())

def test_agent_session_extension_provider_oauth_login_logout_and_refresh(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)
    model_config = {
        "id": "oauth-model",
        "name": "OAuth Model",
        "reasoning": False,
        "input": ["text"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": 32000,
        "maxTokens": 4096,
    }
    calls: list[object] = []

    def login(callbacks):
        calls.append(("login", callbacks))
        return {"access": "login-token", "refresh": "login-refresh", "expires": 1}

    def refresh_token(credentials):
        calls.append(("refresh", credentials["refresh"]))
        return {"access": "fresh-token", "refresh": "fresh-refresh", "expires": 4_102_444_800_000}

    runner.register_provider(
        "sso",
        {
            "baseUrl": "https://sso.example.test",
            "api": "faux",
            "oauth": {
                "name": "Corporate SSO",
                "login": login,
                "refreshToken": refresh_token,
                "getApiKey": lambda credentials: credentials["access"],
            },
            "models": [model_config],
        },
    )
    model_registry = session.model_registry

    callbacks = {"onAuth": lambda info: None, "onDeviceCode": lambda info: None}
    model_registry.login_oauth_provider("sso", callbacks)

    assert calls[0] == ("login", callbacks)
    assert model_registry.get_provider_auth_status("sso") == {"configured": True, "source": "stored"}
    assert model_registry.get_api_key_for_provider("sso") == "fresh-token"
    assert calls[1] == ("refresh", "login-refresh")

    model_registry.logout_provider("sso")

    assert model_registry.get_provider_auth_status("sso") == {"configured": False}
    assert model_registry.get_api_key_for_provider("sso") is None
    assert {"id": "sso", "name": "Corporate SSO"} in model_registry.get_oauth_providers()

    try:
        model_registry.login_oauth_provider("missing", callbacks)
        assert False, "expected unknown provider to be rejected"
    except RuntimeError as error:
        assert str(error) == "Unknown OAuth provider: missing"

def test_agent_session_rejects_queued_extension_commands(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    runner.register_command("testcmd", {"description": "Test command", "handler": lambda args, ctx=None: None})
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)

    try:
        session.steer("/testcmd queued")
        assert False, "expected steering extension command to be rejected"
    except RuntimeError as error:
        assert str(error) == (
            'Extension command "/testcmd" cannot be queued. Use prompt() or execute the command when not streaming.'
        )

    try:
        session.follow_up("/testcmd queued")
        assert False, "expected follow-up extension command to be rejected"
    except RuntimeError as error:
        assert str(error) == (
            'Extension command "/testcmd" cannot be queued. Use prompt() or execute the command when not streaming.'
        )

    assert session.pending_message_count == 0
    assert session.get_steering_messages() == []
    assert session.get_follow_up_messages() == []

def test_agent_session_emits_session_info_and_thinking_events(tmp_path: Path) -> None:
    model = faux_model()
    model.reasoning = True
    session = AgentSession(cwd=str(tmp_path), model=model, thinking_level="off")
    events: list[object] = []
    session.subscribe(events.append)

    session.set_session_name("hello world")
    session.set_thinking_level("high")
    session.set_thinking_level("high")

    assert session.session_name == "hello world"
    assert session.thinking_level == "high"
    session_events = [event for event in events if event.type in {"session_info_changed", "thinking_level_changed"}]
    assert [(event.type, getattr(event, "name", None), getattr(event, "level", None)) for event in session_events] == [
        ("session_info_changed", "hello world", None),
        ("thinking_level_changed", None, "high"),
    ]

def test_agent_session_cycles_scoped_models_with_thinking_levels(tmp_path: Path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    first = Model(id="first", name="First", api="faux", provider="faux", base_url="http://localhost", reasoning=True)
    second = Model(id="second", name="Second", api="faux", provider="faux", base_url="http://localhost", reasoning=True)
    session = AgentSession(
        cwd=str(tmp_path),
        model=first,
        thinking_level="low",
        scoped_models=[ScopedModel(model=first, thinking_level="low"), ScopedModel(model=second, thinking_level="high")],
    )
    events: list[object] = []
    session.subscribe(events.append)

    result = session.cycle_model()

    assert result is not None
    assert result.model is second
    assert result.thinking_level == "high"
    assert result.is_scoped is True
    assert session.model is second
    assert session.thinking_level == "high"
    assert any(event.type == "thinking_level_changed" and event.level == "high" for event in events)

def test_agent_session_cycles_registered_models_without_scoped_models(tmp_path: Path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    first = Model(id="first", name="First", api="faux", provider="faux", base_url="http://localhost", reasoning=True)
    second = Model(id="second", name="Second", api="faux", provider="faux", base_url="http://localhost", reasoning=True)
    register_model(first)
    register_model(second)
    session = AgentSession(cwd=str(tmp_path), model=first, thinking_level="high")

    result = session.cycle_model()

    assert result is not None
    assert result.model is second
    assert result.thinking_level == "high"
    assert result.is_scoped is False
    assert session.model is second
    assert session.thinking_level == "high"

def test_agent_session_cycle_includes_active_model_when_registry_does_not(tmp_path: Path) -> None:
    from travis.coding_agent import AuthStorage, ModelRegistry

    active = Model(id="env-model", name="Env", api="faux", provider="openrouter", base_url="http://localhost", reasoning=True)
    alternate = Model(id="registered-model", name="Registered", api="faux", provider="openrouter", base_url="http://localhost", reasoning=True)
    registry = ModelRegistry.in_memory(AuthStorage.in_memory())
    registry.runtime.clear_providers()
    registry.runtime.set_provider(
        create_faux_provider(
            lambda model, context: text_response_events(model, "unused"),
            provider_id="openrouter",
            models=[alternate],
        )
    )
    session = AgentSession(
        cwd=str(tmp_path),
        model=active,
        thinking_level="high",
        model_registry=registry,
    )

    result = session.cycle_model()

    assert result is not None
    assert result.model is alternate
    assert result.thinking_level == "high"
    assert result.is_scoped is False
    assert session.model is alternate

def test_agent_session_extension_model_registry_includes_active_model_without_registered_model(tmp_path: Path) -> None:
    from travis.coding_agent import AuthStorage, ModelRegistry

    runner = ExtensionRunner()
    active = Model(id="env-model", name="Env", api="faux", provider="openrouter", base_url="http://localhost", reasoning=True)
    model_registry = ModelRegistry.in_memory(AuthStorage.in_memory())
    model_registry.runtime.clear_providers()
    session = AgentSession(
        cwd=str(tmp_path),
        model=active,
        extension_runner=runner,
        model_registry=model_registry,
    )

    registry = runner.create_context().model_registry

    assert registry is not None
    assert registry.find("openrouter", "env-model") is active
    assert registry.get_all() == [active]
    assert registry.get_available() == [active]
    assert registry.has_configured_auth(active) is True
    assert session.extension_runner is runner

def test_agent_session_thinking_level_helpers_follow_model_capabilities(tmp_path: Path) -> None:
    model = Model(
        id="restricted",
        name="Restricted",
        api="faux",
        provider="faux",
        base_url="",
        reasoning=True,
        thinking_level_map={"off": None, "minimal": None, "low": None, "xhigh": "max"},
    )
    session = AgentSession(cwd=str(tmp_path), model=model, thinking_level="medium")
    events: list[object] = []
    session.subscribe(events.append)

    assert session.supports_thinking() is True
    assert session.get_available_thinking_levels() == ["medium", "high", "xhigh"]

    session.set_thinking_level("off")
    assert session.thinking_level == "medium"

    assert session.cycle_thinking_level() == "high"
    assert session.thinking_level == "high"
    assert session.cycle_thinking_level() == "xhigh"
    assert session.thinking_level == "xhigh"
    assert [event.level for event in events if event.type == "thinking_level_changed"] == ["high", "xhigh"]

def test_agent_session_thinking_level_helpers_disable_non_reasoning_cycle(tmp_path: Path) -> None:
    model = Model(id="plain", name="Plain", api="faux", provider="faux", base_url="", reasoning=False)
    session = AgentSession(cwd=str(tmp_path), model=model, thinking_level="high")
    events: list[object] = []
    session.subscribe(events.append)

    assert session.supports_thinking() is False
    assert session.get_available_thinking_levels() == ["off"]
    assert session.cycle_thinking_level() is None

    session.set_thinking_level("high")

    assert session.thinking_level == "off"
    assert [event.level for event in events if event.type == "thinking_level_changed"] == ["off"]

def test_agent_session_set_model_updates_state_without_non_travis234_listener_event(tmp_path: Path) -> None:
    first = faux_model()
    second = faux_model()
    second.id = "second-model"
    second.name = "Second"
    session = AgentSession(cwd=str(tmp_path), model=first)
    events: list[object] = []
    session.subscribe(events.append)

    session.set_model(second)

    assert session.model is second
    assert not any(event.type in {"model_changed", "model_select"} for event in events)

def test_agent_session_manual_compaction_emits_start_and_end(tmp_path: Path) -> None:
    from travis.compaction import CompactionManager, ContextCompressor

    model = faux_model()
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        compaction_manager=CompactionManager(
            ContextCompressor(context_length=40, protect_last_n=1, protect_first_n=1),
            summarizer=lambda prompt: "summary",
        ),
    )
    session.agent.state.messages = [
        UserMessage(content=f"message {index} " + ("x" * 80), timestamp=now_ms() + index)
        for index in range(6)
    ]
    events: list[object] = []
    session.subscribe(events.append)

    status = session.compact()

    compaction_events = [event for event in events if event.type in {"compaction_start", "compaction_end"}]
    assert compaction_events[0].type == "compaction_start"
    assert compaction_events[0].reason == "manual"
    assert compaction_events[1].type == "compaction_end"
    assert compaction_events[1].reason == "manual"
    assert compaction_events[1].aborted is False
    assert compaction_events[1].will_retry is False
    assert compaction_events[1].error_message is None
    assert compaction_events[1].result is status
    assert session.messages == status.messages

def test_agent_session_manual_compaction_persists_travis234_first_kept_boundary(tmp_path: Path) -> None:
    from travis.compaction import CompactionManager, ContextCompressor, estimate_tokens

    session_path = tmp_path / "session.jsonl"
    compressor = ContextCompressor(context_length=40, protect_first_n=1, protect_last_n=1)
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(session_path),
        compaction_manager=CompactionManager(
            compressor,
            summarizer=lambda prompt: "## Goal\nPi boundary summary.",
        ),
    )
    messages = [
        UserMessage(content=f"message {index} " + ("x" * 80), timestamp=now_ms() + index)
        for index in range(12)
    ]
    session.agent.state.messages = list(messages)
    entry_ids = [session._session_store.append_message(message) for message in messages]
    before_tokens = estimate_tokens(messages)
    expected_cut = compressor._find_tail_start(messages, compressor._protect_head_size(messages))

    status = session.compact()

    persisted = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    compaction_entry = next(entry for entry in persisted if entry["type"] == "compaction")
    assert status.first_kept_message_index == expected_cut
    assert status.first_kept_entry_id == entry_ids[expected_cut]
    assert compaction_entry["firstKeptEntryId"] == entry_ids[expected_cut]
    assert compaction_entry["tokensBefore"] == before_tokens
    assert compaction_entry["summary"] == "## Goal\nPi boundary summary."
    assert getattr(session.messages[0], "role", None) == "compactionSummary"
    assert _user_text(session.messages[1]) == _user_text(messages[expected_cut])


def test_persistent_compaction_entry_ids_include_retained_older_compaction_summaries(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "nested-compaction-boundaries.jsonl"
    store = SessionStore(str(session_path), cwd=str(tmp_path))
    user_id = store.append_message(UserMessage(content="retained goal", timestamp=now_ms()))
    old_call_id = store.append_message(
        AssistantMessage(
            content=[ToolCall(id="old-call", name="read", arguments={"path": "old.txt"})],
            api="faux",
            provider="faux",
            model="m",
            usage=empty_usage(),
            stop_reason="toolUse",
            timestamp=now_ms(),
        )
    )
    older_compaction_id = store.append_compaction("older summary", user_id, 1_000)
    latest_compaction_id = store.append_compaction("latest summary", user_id, 900)
    latest_call_id = store.append_message(
        AssistantMessage(
            content=[ToolCall(id="latest-call", name="write", arguments={"path": "new.txt"})],
            api="faux",
            provider="faux",
            model="m",
            usage=empty_usage(),
            stop_reason="toolUse",
            timestamp=now_ms(),
        )
    )
    latest_result_id = store.append_message(
        ToolResultMessage(
            tool_call_id="latest-call",
            tool_name="write",
            content=[TextContent(text="written")],
            is_error=False,
            timestamp=now_ms(),
        )
    )

    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(session_path),
    )

    assert session._compaction_adapter.context_message_entry_ids() == [  # noqa: SLF001
        latest_compaction_id,
        user_id,
        old_call_id,
        older_compaction_id,
        latest_call_id,
        latest_result_id,
    ]
    assert len(session._compaction_adapter.context_message_entry_ids()) == len(session.messages)  # noqa: SLF001


def test_persisted_compaction_context_omits_orphaned_tool_results(tmp_path: Path) -> None:
    session_path = tmp_path / "orphaned-tool-result.jsonl"
    store = SessionStore(str(session_path), cwd=str(tmp_path))
    store.append_message(
        AssistantMessage(
            content=[ToolCall(id="lost-call", name="write", arguments={"path": "lost.txt"})],
            api="faux",
            provider="faux",
            model="m",
            usage=empty_usage(),
            stop_reason="toolUse",
            timestamp=now_ms(),
        )
    )
    orphaned_result_id = store.append_message(
        ToolResultMessage(
            tool_call_id="lost-call",
            tool_name="write",
            content=[TextContent(text="written")],
            is_error=False,
            timestamp=now_ms(),
        )
    )
    compaction_id = store.append_compaction(
        "summary with a bad historical cut", orphaned_result_id, 1_000
    )

    snapshot = store.build_context()
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(session_path),
    )

    assert [getattr(message, "role", None) for message in snapshot.messages] == [
        "compactionSummary"
    ]
    assert session._compaction_adapter.context_message_entry_ids() == [compaction_id]  # noqa: SLF001

def test_agent_session_manual_compaction_persists_travis234_file_operation_details(tmp_path: Path) -> None:
    from travis.compaction import CompactionManager, ContextCompressor

    session_path = tmp_path / "manual-compaction-file-details.jsonl"
    compressor = ContextCompressor(context_length=700, protect_first_n=1, protect_last_n=1)
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(session_path),
        compaction_manager=CompactionManager(
            compressor,
            summarizer=lambda prompt: "## Goal\nPi file detail summary.",
        ),
    )
    messages = [
        UserMessage(content="goal", timestamp=now_ms()),
        AssistantMessage(
            content=[ToolCall(id="read-1", name="read", arguments={"path": "src/a.py"})],
            api="faux",
            provider="faux",
            model="m",
            usage=empty_usage(),
            stop_reason="toolUse",
            timestamp=now_ms(),
        ),
        ToolResultMessage(
            tool_call_id="read-1",
            tool_name="read",
            content=[TextContent(text="a")],
            is_error=False,
            timestamp=now_ms(),
        ),
        AssistantMessage(
            content=[ToolCall(id="write-1", name="write", arguments={"path": "src/b.py", "content": "b"})],
            api="faux",
            provider="faux",
            model="m",
            usage=empty_usage(),
            stop_reason="toolUse",
            timestamp=now_ms(),
        ),
        ToolResultMessage(
            tool_call_id="write-1",
            tool_name="write",
            content=[TextContent(text="wrote")],
            is_error=False,
            timestamp=now_ms(),
        ),
    ]
    for index in range(14):
        messages.append(UserMessage(content=f"old filler {index} " * 30, timestamp=now_ms()))
        messages.append(
            AssistantMessage(
                content=[TextContent(text=f"old ack {index} " * 30)],
                api="faux",
                provider="faux",
                model="m",
                usage=empty_usage(),
                stop_reason="stop",
                timestamp=now_ms(),
            )
        )
    messages.append(UserMessage(content="latest request", timestamp=now_ms()))

    session.agent.state.messages = list(messages)
    for message in messages:
        session._session_store.append_message(message)

    session.compact()

    persisted = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    compaction_entry = next(entry for entry in persisted if entry["type"] == "compaction")
    assert compaction_entry["details"] == {
        "readFiles": ["src/a.py"],
        "modifiedFiles": ["src/b.py"],
    }

def test_agent_session_manual_compaction_persists_managed_process_ledger(tmp_path: Path) -> None:
    from travis.compaction import CompactionManager, ContextCompressor
    from travis.coding_agent.processes.service import ProcessSessionService
    from travis.coding_agent.processes.types import ProcessOwner

    process_id = "proc_" + "f" * 32
    session_path = tmp_path / "manual-compaction-process-details.jsonl"
    service = ProcessSessionService(directory=tmp_path / "processes")
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(session_path),
        compaction_manager=CompactionManager(
            ContextCompressor(context_length=80, protect_first_n=1, protect_last_n=1),
            summarizer=lambda prompt: "## Goal\nProcess-aware summary.",
        ),
        process_service=service,
        process_owner=ProcessOwner("app", str(tmp_path), "agent"),
    )
    process_result = ToolResultMessage(
        tool_call_id="bash-1",
        tool_name="bash",
        content=[TextContent(text="opaque output must not enter the ledger")],
        details={
            "sessionId": process_id,
            "status": "running",
            "nextCursor": 9,
            "outputSize": 11,
        },
        is_error=False,
        timestamp=now_ms(),
    )
    messages = [UserMessage(content="goal", timestamp=now_ms()), process_result]
    messages.extend(
        UserMessage(content=f"old filler {index} " * 40, timestamp=now_ms())
        for index in range(12)
    )
    session.agent.state.messages = list(messages)
    for message in messages:
        session._session_store.append_message(message)

    try:
        session.compact()
        persisted = [
            json.loads(line)
            for line in session_path.read_text(encoding="utf-8").splitlines()
        ]
        compaction_entry = next(entry for entry in persisted if entry["type"] == "compaction")
        assert compaction_entry["details"]["managedProcesses"] == [
            {
                "sessionId": process_id,
                "status": "unavailable",
                "cursor": 9,
                "outputSize": 11,
                "exitCode": None,
                "durableOutput": False,
            }
        ]
        assert "opaque output" not in json.dumps(compaction_entry["details"])
        summary_text = _user_text(default_convert_to_llm(session.messages)[0])
        assert f"<managed-processes>\n{process_id} status=unavailable" in summary_text
    finally:
        session.shutdown()
        service.close()

def test_agent_session_applied_compaction_merges_managed_process_ledger(tmp_path: Path) -> None:
    from travis.coding_agent.processes.service import ProcessSessionService
    from travis.coding_agent.processes.types import ProcessOwner

    process_id = "proc_" + "a" * 32
    session_path = tmp_path / "applied-compaction-process-details.jsonl"
    service = ProcessSessionService(directory=tmp_path / "processes")
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(session_path),
        process_service=service,
        process_owner=ProcessOwner("app", str(tmp_path), "agent"),
    )
    process_result = ToolResultMessage(
        tool_call_id="bash-1",
        tool_name="bash",
        content=[TextContent(text="not persisted in details")],
        details={
            "sessionId": process_id,
            "status": "running",
            "nextCursor": 4,
            "outputSize": 7,
        },
        is_error=False,
        timestamp=now_ms(),
    )
    source_messages = [UserMessage(content="goal", timestamp=now_ms()), process_result]
    session.agent.state.messages = list(source_messages)
    for message in source_messages:
        session._session_store.append_message(message)
    result = SimpleNamespace(
        compressed=True,
        summary="Process-aware automatic summary.",
        tokens_before=100,
        details={"readFiles": ["src/a.py"]},
        first_kept_message_index=None,
    )

    try:
        session.compaction_adapter.apply_result([], result, source_messages=source_messages)
        persisted = [
            json.loads(line)
            for line in session_path.read_text(encoding="utf-8").splitlines()
        ]
        compaction_entry = next(entry for entry in persisted if entry["type"] == "compaction")
        assert compaction_entry["details"]["readFiles"] == ["src/a.py"]
        assert compaction_entry["details"]["managedProcesses"][0] == {
            "sessionId": process_id,
            "status": "unavailable",
            "cursor": 4,
            "outputSize": 7,
            "exitCode": None,
            "durableOutput": False,
        }
    finally:
        session.shutdown()
        service.close()

def test_session_store_build_context_recreates_compaction_summary_message(tmp_path: Path) -> None:
    store = SessionStore(str(tmp_path / "session.jsonl"), cwd=str(tmp_path))
    first_id = store.append_message(UserMessage(content="kept", timestamp=now_ms()))
    store.append_compaction("Older work summary", first_id, 23456)

    snapshot = store.build_context()

    assert [message.role for message in snapshot.messages] == ["compactionSummary", "user"]
    assert snapshot.messages[0].summary == "Older work summary"
    assert snapshot.messages[0].tokens_before == 23456


def test_session_store_persists_summary_model_provenance_outside_llm_context(tmp_path: Path) -> None:
    store = SessionStore(str(tmp_path / "session.jsonl"), cwd=str(tmp_path))
    first_id = store.append_message(UserMessage(content="kept", timestamp=now_ms()))
    store.append_compaction(
        "Older work summary",
        first_id,
        23456,
        details={
            "summaryModel": {
                "requested": "openrouter/openai/gpt-5.6-luna-pro",
                "used": "openrouter/xiaomi/mimo-v2.5",
                "fallback": True,
                "error": "temporary route failure",
            }
        },
    )

    entry = next(item for item in store.get_branch() if item["type"] == "compaction")
    snapshot = store.build_context()

    assert entry["details"]["summaryModel"] == {
        "requested": "openrouter/openai/gpt-5.6-luna-pro",
        "used": "openrouter/xiaomi/mimo-v2.5",
        "fallback": True,
        "error": "temporary route failure",
    }
    llm_text = _user_text(default_convert_to_llm(snapshot.messages)[0])
    assert "gpt-5.6-luna-pro" not in llm_text
    assert "temporary route failure" not in llm_text


def test_compaction_adapter_persists_dedicated_summary_model_provenance(tmp_path: Path) -> None:
    from travis.coding_agent.compaction_adapter import SessionCompactionAdapter
    from travis.compaction import CompressionResult

    store = SessionStore(str(tmp_path / "session.jsonl"), cwd=str(tmp_path))
    source = [
        UserMessage(content="older work", timestamp=now_ms()),
        UserMessage(content="kept request", timestamp=now_ms()),
    ]
    for message in source:
        store.append_message(message)
    state = SimpleNamespace(messages=list(source), thinking_level="off")
    adapter = SessionCompactionAdapter(
        session_store=store,
        state=state,
        process_context=None,
        emit=lambda _event: None,
        set_session_name=lambda _name: None,
    )
    result = CompressionResult(
        messages=[source[-1]],
        compressed=True,
        savings_pct=50.0,
        summary="checkpoint",
        tokens_before=100,
        first_kept_message_index=1,
        summary_model_requested="openrouter/openai/gpt-5.6-luna-pro",
        summary_model_used="openrouter/openai/gpt-5.6-luna-pro",
        summary_model_dedicated=True,
    )

    adapter.apply_result(
        result.messages,
        result,
        source_messages=source,
        source_indices=[0, 1],
    )

    entry = next(item for item in store.get_branch() if item["type"] == "compaction")
    assert entry["details"]["summaryModel"] == {
        "requested": "openrouter/openai/gpt-5.6-luna-pro",
        "used": "openrouter/openai/gpt-5.6-luna-pro",
        "fallback": False,
    }

def test_session_store_build_context_preserves_compaction_file_details_for_llm(tmp_path: Path) -> None:
    store = SessionStore(str(tmp_path / "session.jsonl"), cwd=str(tmp_path))
    first_id = store.append_message(UserMessage(content="kept", timestamp=now_ms()))
    store.append_compaction(
        "Older work summary without exact file inventory.",
        first_id,
        23456,
        details={
            "readFiles": ["source.md"],
            "modifiedFiles": ["docs/alpha.md", "docs/beta.md"],
        },
    )

    snapshot = store.build_context()
    llm_messages = default_convert_to_llm(snapshot.messages)
    summary_text = _user_text(llm_messages[0])

    assert snapshot.messages[0].details == {
        "readFiles": ["source.md"],
        "modifiedFiles": ["docs/alpha.md", "docs/beta.md"],
    }
    assert "<read-files>\nsource.md\n</read-files>" in summary_text
    assert "<modified-files>\ndocs/alpha.md\ndocs/beta.md\n</modified-files>" in summary_text

def test_session_store_round_trips_bash_execution_and_llm_conversion(tmp_path: Path) -> None:
    store = SessionStore(str(tmp_path / "session.jsonl"), cwd=str(tmp_path))
    store.append_message(
        BashExecutionMessage(
            command="printf hi",
            output="hi",
            exit_code=0,
            cancelled=False,
            truncated=False,
            full_output_path=None,
            timestamp=now_ms(),
        )
    )
    store.append_message(
        BashExecutionMessage(
            command="secret",
            output="hidden",
            exit_code=0,
            cancelled=False,
            truncated=False,
            full_output_path=None,
            timestamp=now_ms(),
            exclude_from_context=True,
        )
    )

    snapshot = store.build_context()

    assert [message.role for message in snapshot.messages] == ["bashExecution", "bashExecution"]
    assert snapshot.messages[0].command == "printf hi"
    assert snapshot.messages[0].exit_code == 0
    assert snapshot.messages[1].exclude_from_context is True

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    converted = session._convert_to_llm(snapshot.messages)
    assert len(converted) == 1
    assert converted[0].role == "user"
    assert "Ran `printf hi`" in converted[0].content[0].text
    assert "```" in converted[0].content[0].text
    assert "secret" not in converted[0].content[0].text
