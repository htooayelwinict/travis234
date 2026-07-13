from __future__ import annotations

from tests._support_coding_agent import *  # noqa: F403


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

    runner.register_provider("proxy", {"baseUrl": "https://proxy.example.test", "api": "faux", "oauth": {}, "models": [model_config]})
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
            "oauth": {"name": "Corporate SSO", "getApiKey": lambda credentials: credentials["access"]},
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
    assert model_registry.get_oauth_providers() == [{"id": "sso", "name": "Corporate SSO"}]

    runner.unregister_provider("sso")

    assert model_registry.get_oauth_providers() == []

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
    assert model_registry.get_oauth_providers() == [{"id": "sso", "name": "Corporate SSO"}]

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
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    active = Model(id="env-model", name="Env", api="faux", provider="openrouter", base_url="http://localhost", reasoning=True)
    alternate = Model(id="registered-model", name="Registered", api="faux", provider="openrouter", base_url="http://localhost", reasoning=True)
    register_model(alternate)
    session = AgentSession(cwd=str(tmp_path), model=active, thinking_level="high")

    result = session.cycle_model()

    assert result is not None
    assert result.model is alternate
    assert result.thinking_level == "high"
    assert result.is_scoped is False
    assert session.model is alternate

def test_agent_session_extension_model_registry_includes_active_model_without_registered_model(tmp_path: Path) -> None:
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "unused")))
    runner = ExtensionRunner()
    active = Model(id="env-model", name="Env", api="faux", provider="openrouter", base_url="http://localhost", reasoning=True)
    session = AgentSession(cwd=str(tmp_path), model=active, extension_runner=runner)

    registry = runner.create_context().model_registry

    assert registry is not None
    assert registry.find("openrouter", "env-model") is active
    assert registry.get_all() == [active]
    assert registry.get_available() == [active]
    assert registry.has_configured_auth(active) is False
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

def test_agent_session_execute_bash_records_message_and_session(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(tmp_path / "session.jsonl"))
    chunks: list[str] = []

    def exec_command(command: str, cwd: str, options) -> dict[str, int | None]:
        assert command == "printf hi"
        assert cwd == str(tmp_path)
        options.on_data(b"hi")
        return {"exit_code": 0}

    result = session.execute_bash(
        "printf hi",
        chunks.append,
        {"operations": BashOperations(exec=exec_command)},
    )

    assert result.output == "hi"
    assert chunks == ["hi"]
    assert session.messages[-1].role == "bashExecution"
    assert session.messages[-1].command == "printf hi"
    assert session.messages[-1].output == "hi"
    assert session.session_entries[-1]["message"]["role"] == "bashExecution"

def test_travis234_execute_bash_with_operations_is_public_and_sanitizes_streamed_output(tmp_path: Path) -> None:
    from travis.coding_agent import execute_bash_with_operations

    chunks: list[str] = []

    def exec_command(command: str, cwd: str, options) -> dict[str, int | None]:
        assert command == "printf hi"
        assert cwd == str(tmp_path)
        options.on_data(b"\x1b[31mhi\x1b[0m\x00\n")
        return {"exit_code": 0}

    result = execute_bash_with_operations(
        "printf hi",
        str(tmp_path),
        BashOperations(exec=exec_command),
        {"onChunk": chunks.append},
    )

    assert result.output == "hi\n"
    assert chunks == ["hi\n"]
    assert result.exit_code == 0
    assert result.cancelled is False
    assert result.truncated is False
    assert result.full_output_path is None

def test_travis234_experimental_feature_gate_uses_travis234_experimental_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from travis.coding_agent import are_experimental_features_enabled

    monkeypatch.delenv("TRAVIS234_EXPERIMENTAL", raising=False)
    assert are_experimental_features_enabled() is False

    monkeypatch.setenv("TRAVIS234_EXPERIMENTAL", "0")
    assert are_experimental_features_enabled() is False

    monkeypatch.setenv("TRAVIS234_EXPERIMENTAL", "1")
    assert are_experimental_features_enabled() is True

def test_travis234_create_synthetic_source_info_uses_canonical_keyword_arguments() -> None:
    from travis.coding_agent import SourceInfo, create_synthetic_source_info

    explicit = create_synthetic_source_info(
        "tools/example.ts",
        source="extension",
        scope="project",
        origin="package",
        base_dir="/repo/.travis234/extensions/example",
    )

    assert explicit == SourceInfo(
        path="tools/example.ts",
        source="extension",
        scope="project",
        origin="package",
        base_dir="/repo/.travis234/extensions/example",
    )
    assert explicit.base_dir == "/repo/.travis234/extensions/example"

    defaulted = create_synthetic_source_info("inline", source="sdk")
    assert defaulted.scope == "temporary"
    assert defaulted.origin == "top-level"
    assert defaulted.base_dir is None

def test_travis234_compaction_result_public_shape() -> None:
    from travis.coding_agent import CompactionResult

    result = CompactionResult(
        summary="summary",
        first_kept_entry_id="entry-2",
        tokens_before=1234,
        details={"kind": "artifact-index"},
    )

    assert result.summary == "summary"
    assert result.first_kept_entry_id == "entry-2"
    assert result.tokens_before == 1234
    assert result.details == {"kind": "artifact-index"}

def test_agent_session_execute_bash_applies_travis234_command_prefix_but_records_original(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    seen: dict[str, str] = {}

    def exec_command(command: str, cwd: str, options) -> dict[str, int | None]:
        seen["command"] = command
        seen["cwd"] = cwd
        options.on_data(b"ok")
        return {"exit_code": 0}

    result = session.execute_bash(
        "printf hi",
        options={
            "operations": BashOperations(exec=exec_command),
            "commandPrefix": "source ~/.profile",
        },
    )

    assert seen == {"command": "source ~/.profile\nprintf hi", "cwd": str(tmp_path)}
    assert result.output == "ok"
    assert session.messages[-1].command == "printf hi"

def test_agent_session_uses_travis234_settings_manager_for_built_in_and_user_bash(tmp_path: Path) -> None:
    class ShellSettings:
        def get_shell_command_prefix(self) -> str:
            return "printf settings-prefix;"

        def get_shell_path(self) -> None:
            return None

    session = AgentSession(cwd=str(tmp_path), model=faux_model(), settings_manager=ShellSettings())
    bash_definition = session.get_tool_definition("bash")
    assert bash_definition is not None

    tool_result = bash_definition.execute("c1", {"command": "printf tool"})
    user_result = session.execute_bash("printf user")

    assert tool_result.content[0].text == "settings-prefixtool"
    assert user_result.output == "settings-prefixuser"
    assert session.messages[-1].command == "printf user"

def test_agent_session_execute_bash_uses_travis234_shell_path_option(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str | None] = {}

    def fake_create_local_bash_operations(shell_path: str | None = None) -> BashOperations:
        captured["shell_path"] = shell_path
        return BashOperations(exec=lambda command, cwd, options: {"exit_code": 0})

    monkeypatch.setattr(
        "travis.coding_agent.session_bash.create_local_bash_operations",
        fake_create_local_bash_operations,
    )

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.execute_bash("true", options={"shellPath": "/bin/zsh"})

    assert captured == {"shell_path": "/bin/zsh"}
    assert session.messages[-1].command == "true"

def test_coding_agent_package_exports_bash_result() -> None:
    from travis.coding_agent import BashResult as ExportedBashResult

    assert ExportedBashResult is BashResult

def test_agent_session_defers_bash_result_while_streaming_then_flushes(tmp_path: Path) -> None:
    model = faux_model()
    session_path = tmp_path / "session.jsonl"
    stream_started = threading.Event()
    release_stream = threading.Event()

    def stream_fn(model, context, options):
        events = text_response_events(model, "streamed response")
        stream = create_assistant_message_event_stream()
        stream.push(events[0])
        stream_started.set()

        def finish() -> None:
            release_stream.wait(timeout=2)
            for event in events[1:]:
                stream.push(event)

        threading.Thread(target=finish, daemon=True).start()
        return stream

    session = AgentSession(cwd=str(tmp_path), model=model, session_path=str(session_path))
    run_error: list[BaseException] = []

    def run_prompt() -> None:
        try:
            session.prompt("start", stream_fn=stream_fn)
        except BaseException as error:  # noqa: BLE001
            run_error.append(error)

    thread = threading.Thread(target=run_prompt)
    thread.start()
    assert stream_started.wait(timeout=2)
    assert session.is_streaming is True

    session.record_bash_result(
        "echo hi",
        BashResult(output="hi", exit_code=0, cancelled=False, truncated=False),
    )

    assert session.has_pending_bash_messages is True
    assert not any(message.role == "bashExecution" for message in session.messages)
    assert not any(entry.get("message", {}).get("role") == "bashExecution" for entry in session.session_entries)

    release_stream.set()
    thread.join(timeout=2)

    assert run_error == []
    assert session.has_pending_bash_messages is False
    assert any(message.role == "bashExecution" for message in session.messages)
    assert session.session_entries[-1]["message"]["role"] == "bashExecution"

def test_agent_session_abort_bash_cancels_running_command(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    command_started = threading.Event()
    result_holder: list[BashResult] = []
    errors: list[BaseException] = []

    def exec_command(command: str, cwd: str, options) -> dict[str, int | None]:
        command_started.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if options.signal and options.signal.aborted:
                raise RuntimeError("aborted")
            time.sleep(0.005)
        return {"exit_code": 0}

    def run_bash() -> None:
        try:
            result_holder.append(
                session.execute_bash(
                    "sleep",
                    options={"operations": BashOperations(exec=exec_command)},
                )
            )
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    thread = threading.Thread(target=run_bash)
    thread.start()
    assert command_started.wait(timeout=2)
    assert session.is_bash_running is True

    session.abort_bash()
    thread.join(timeout=2)

    assert errors == []
    assert len(result_holder) == 1
    assert result_holder[0].cancelled is True
    assert session.is_bash_running is False

def test_agent_session_auto_retry_events_for_transient_provider_error(tmp_path: Path) -> None:
    model = faux_model()
    calls = {"n": 0}

    def stream_fn(model, context, options):
        calls["n"] += 1
        if calls["n"] == 1:
            stream = create_assistant_message_event_stream()
            error = AssistantMessage(
                content=[TextContent(text="")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=empty_usage(),
                stop_reason="error",
                error_message="Provider finish_reason: network_error",
            )
            stream.push(ErrorEvent(reason="error", error=error))
            return stream
        return create_faux_provider(lambda m, c: text_response_events(m, "Recovered")).stream_simple(
            model,
            context,
            options,
        )

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        retry_enabled=True,
        max_retries=2,
        retry_delay_ms=0,
    )
    events: list[object] = []
    session.subscribe(events.append)

    session.prompt("Test", stream_fn=stream_fn)

    assert calls["n"] == 2
    retry_events = [event for event in events if event.type.startswith("auto_retry_")]
    assert retry_events[0].type == "auto_retry_start"
    assert retry_events[0].attempt == 1
    assert retry_events[0].max_attempts == 2
    assert retry_events[0].delay_ms == 0
    assert retry_events[0].error_message == "Provider finish_reason: network_error"
    assert retry_events[1].type == "auto_retry_end"
    assert retry_events[1].success is True
    assert retry_events[1].attempt == 1
    assert session.retry_attempt == 0

def test_agent_session_auto_retry_adds_malformed_tool_args_correction_context(tmp_path: Path) -> None:
    model = faux_model()
    captured_contexts: list[Context] = []

    def stream_fn(model, context, options):
        captured_contexts.append(context)
        if len(captured_contexts) == 1:
            stream = create_assistant_message_event_stream()
            error = AssistantMessage(
                content=[TextContent(text="")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=empty_usage(),
                stop_reason="error",
                error_message=(
                    "Stream ended with malformed streamed tool-call arguments "
                    "for write; dropped tool call before dispatch."
                ),
            )
            stream.push(ErrorEvent(reason="error", error=error))
            return stream
        return create_faux_provider(lambda m, c: text_response_events(m, "Recovered")).stream_simple(
            model,
            context,
            options,
        )

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        retry_enabled=True,
        max_retries=2,
        retry_delay_ms=0,
    )

    session.prompt("Create a protocol literal fixture.", stream_fn=stream_fn)

    assert len(captured_contexts) == 2
    retry_user_messages = [
        message.content
        for message in captured_contexts[1].messages
        if getattr(message, "role", None) == "user" and isinstance(message.content, str)
    ]
    recovery = retry_user_messages[-1]
    assert "malformed streamed tool-call arguments" in recovery
    assert "write" in recovery
    assert "Do not retry the same malformed tool call" in recovery
    assert "protocol-looking literal" in recovery
    assert "Retry the write tool" in recovery
    assert "JSON unicode escapes" in recovery
    assert "change strategy with available tools" in recovery
    assert "base64" not in recovery
    assert "content_escaped" not in recovery

def test_agent_session_continues_partial_stream_dropped_tool_calls_with_chunk_guidance(tmp_path: Path) -> None:
    model = faux_model()
    captured_contexts: list[object] = []

    def stream_fn(model, context, options):
        captured_contexts.append(context)
        if len(captured_contexts) == 1:
            stream = create_assistant_message_event_stream()
            partial = AssistantMessage(
                content=[TextContent(text="I will write the fixture.")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=empty_usage(),
                stop_reason="length",
                response_id="partial-stream-stub",
                diagnostics=[
                    {
                        "code": "partial_stream_dropped_tool_calls",
                        "dropped_tool_names": ["bash"],
                        "finish_reason": "tool_calls",
                    }
                ],
            )
            stream.push(DoneEvent(reason="length", message=partial))
            return stream
        return create_faux_provider(lambda m, c: text_response_events(m, "Recovered")).stream_simple(
            model,
            context,
            options,
        )

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        retry_enabled=True,
        max_retries=2,
        retry_delay_ms=0,
    )

    messages = session.prompt("Create a protocol literal fixture.", stream_fn=stream_fn)

    assert len(captured_contexts) == 2
    assert any(
        isinstance(message, AssistantMessage)
        and message.stop_reason == "stop"
        and _content_text(message.content) == "Recovered"
        for message in messages
    )
    follow_up = captured_contexts[1].messages[-1]
    assert isinstance(follow_up, UserMessage)
    assert isinstance(follow_up.content, list)
    follow_up_text = _content_text(follow_up.content)
    assert "previous tool call (bash)" in follow_up_text
    assert "Do NOT retry the same tool call" in follow_up_text
    assert "Retry the write tool" in follow_up_text
    assert "JSON unicode escapes" in follow_up_text
    assert "change strategy with available tools" in follow_up_text
    assert "write smaller files" not in follow_up_text
    assert "base64" not in follow_up_text
    assert "content_escaped" not in follow_up_text

def test_agent_session_continues_malformed_streamed_mutating_tool_args_with_recovery_guidance(tmp_path: Path) -> None:
    model = faux_model()
    captured_contexts: list[object] = []

    def stream_fn(model, context, options):
        captured_contexts.append(context)
        if len(captured_contexts) == 1:
            stream = create_assistant_message_event_stream()
            malformed = AssistantMessage(
                content=[TextContent(text="I will write the fixture.")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=empty_usage(),
                stop_reason="length",
                response_id="partial-stream-stub",
                diagnostics=[
                    {
                        "code": "malformed_streamed_tool_call_arguments",
                        "dropped_tool_names": ["write"],
                        "finish_reason": "tool_calls",
                    }
                ],
            )
            stream.push(DoneEvent(reason="length", message=malformed))
            return stream
        return create_faux_provider(lambda m, c: text_response_events(m, "Recovered")).stream_simple(
            model,
            context,
            options,
        )

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        retry_enabled=True,
        max_retries=2,
        retry_delay_ms=0,
    )

    messages = session.prompt("Create a protocol literal fixture.", stream_fn=stream_fn)

    assert len(captured_contexts) == 2
    assert any(
        isinstance(message, AssistantMessage)
        and message.stop_reason == "stop"
        and _content_text(message.content) == "Recovered"
        for message in messages
    )
    follow_up = captured_contexts[1].messages[-1]
    assert isinstance(follow_up, UserMessage)
    follow_up_text = _content_text(follow_up.content)
    assert "malformed streamed tool-call arguments" in follow_up_text
    assert "This is a tool-argument formatting failure" in follow_up_text
    assert "Do not retry the same malformed tool call" in follow_up_text
    assert "Retry the write tool" in follow_up_text
    assert "JSON unicode escapes" in follow_up_text
    assert "change strategy with available tools" in follow_up_text
    assert "too large or malformed" not in follow_up_text
    assert "base64" not in follow_up_text
    assert "content_escaped" not in follow_up_text

def test_agent_session_internal_malformed_stream_recovery_does_not_trigger_user_process_limit(
    tmp_path: Path,
) -> None:
    model = faux_model()
    captured_contexts: list[object] = []
    bash_executions: list[dict] = []
    command = {"command": "echo '# Protocol Fixture"}

    def execute_bash(tool_call_id, args, signal=None, on_update=None, ctx=None):
        bash_executions.append(dict(args))
        return AgentToolResult(
            content=[TextContent(text="zsh:1: unmatched '\nCommand exited with code 1")],
            details={},
        )

    bash_definition = ToolDefinition(
        name="bash",
        label="bash",
        description="Execute a bash command",
        parameters={
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
        execute=execute_bash,
    )

    def stream_fn(model, context, options):
        captured_contexts.append(context)
        if len(captured_contexts) == 1:
            stream = create_assistant_message_event_stream()
            malformed = AssistantMessage(
                content=[TextContent(text="I will write the fixture.")],
                api=model.api,
                provider=model.provider,
                model=model.id,
                usage=empty_usage(),
                stop_reason="length",
                response_id="partial-stream-stub",
                diagnostics=[
                    {
                        "code": "malformed_streamed_tool_call_arguments",
                        "dropped_tool_names": ["write"],
                        "finish_reason": "tool_calls",
                    }
                ],
            )
            stream.push(DoneEvent(reason="length", message=malformed))
            return stream
        if len(captured_contexts) == 2:
            return create_faux_provider(
                lambda m, c: tool_call_response_events(m, "bash", command, call_id="bad_bash")
            ).stream_simple(model, context, options)
        return create_faux_provider(
            lambda m, c: text_response_events(m, "I can continue after the failed bash fallback.")
        ).stream_simple(model, context, options)

    session = AgentSession(cwd=str(tmp_path), model=model, tool_definitions=[bash_definition])

    messages = session.prompt("Create a protocol literal fixture.", stream_fn=stream_fn)

    assert bash_executions == [command]
    assert len(captured_contexts) == 3
    tool_results = [message for message in session.messages if getattr(message, "role", None) == "toolResult"]
    assert "user_process_limit" not in _content_text(tool_results[-1].content)
    assert messages[-1].role == "assistant"
    assert _content_text(messages[-1].content) == "I can continue after the failed bash fallback."

def test_agent_session_does_not_retry_travis234_non_retryable_provider_limit_errors(tmp_path: Path) -> None:
    model = faux_model()
    calls = {"n": 0}

    def stream_fn(model, context, options):
        calls["n"] += 1
        stream = create_assistant_message_event_stream()
        error = AssistantMessage(
            content=[TextContent(text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="error",
            error_message="rate limit: insufficient_quota billing",
        )
        stream.push(ErrorEvent(reason="error", error=error))
        return stream

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        retry_enabled=True,
        max_retries=2,
        retry_delay_ms=0,
    )
    events: list[object] = []
    session.subscribe(events.append)

    session.prompt("Test", stream_fn=stream_fn)

    assert calls["n"] == 1
    assert [event.type for event in events if event.type.startswith("auto_retry_")] == []

def test_agent_session_auto_retry_exhaustion_emits_failure(tmp_path: Path) -> None:
    model = faux_model()
    calls = {"n": 0}

    def stream_fn(model, context, options):
        calls["n"] += 1
        stream = create_assistant_message_event_stream()
        error = AssistantMessage(
            content=[TextContent(text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="error",
            error_message=f"network_error attempt {calls['n']}",
        )
        stream.push(ErrorEvent(reason="error", error=error))
        return stream

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        retry_enabled=True,
        max_retries=2,
        retry_delay_ms=0,
    )
    events: list[object] = []
    session.subscribe(events.append)

    session.prompt("Test", stream_fn=stream_fn)

    assert calls["n"] == 3
    retry_events = [event for event in events if event.type.startswith("auto_retry_")]
    assert [event.type for event in retry_events] == [
        "auto_retry_start",
        "auto_retry_start",
        "auto_retry_end",
    ]
    assert [event.attempt for event in retry_events] == [1, 2, 2]
    assert retry_events[-1].success is False
    assert retry_events[-1].final_error == "network_error attempt 3"
    assert session.retry_attempt == 0

def test_agent_session_auto_retry_facade_toggles_retry_setting(tmp_path: Path) -> None:
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        retry_enabled=True,
        max_retries=1,
        retry_delay_ms=0,
    )

    assert session.auto_retry_enabled is True
    assert session.is_retrying is False

    session.set_auto_retry_enabled(False)
    assert session.auto_retry_enabled is False

    session.set_auto_retry_enabled(True)
    assert session.auto_retry_enabled is True

def test_agent_session_abort_retry_cancels_retry_delay(tmp_path: Path) -> None:
    model = faux_model()
    calls = {"n": 0}
    retry_started = threading.Event()
    prompt_finished = threading.Event()
    errors: list[BaseException] = []

    def stream_fn(model, context, options):
        calls["n"] += 1
        stream = create_assistant_message_event_stream()
        error = AssistantMessage(
            content=[TextContent(text="")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="error",
            error_message="network_error before retry",
        )
        stream.push(ErrorEvent(reason="error", error=error))
        return stream

    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        retry_enabled=True,
        max_retries=2,
        retry_delay_ms=1000,
    )
    events: list[object] = []

    def handle_event(event: object) -> None:
        events.append(event)
        if getattr(event, "type", None) == "auto_retry_start":
            retry_started.set()

    session.subscribe(handle_event)

    def run_prompt() -> None:
        try:
            session.prompt("Test", stream_fn=stream_fn)
        except BaseException as error:  # noqa: BLE001
            errors.append(error)
        finally:
            prompt_finished.set()

    thread = threading.Thread(target=run_prompt)
    thread.start()
    assert retry_started.wait(timeout=2)
    assert session.is_retrying is True

    session.abort_retry()
    thread.join(timeout=2)

    assert prompt_finished.is_set()
    assert errors == []
    assert calls["n"] == 1
    retry_events = [event for event in events if getattr(event, "type", "").startswith("auto_retry_")]
    assert [event.type for event in retry_events] == ["auto_retry_start", "auto_retry_end"]
    assert retry_events[-1].success is False
    assert retry_events[-1].attempt == 1
    assert retry_events[-1].final_error == "Retry cancelled"
    assert session.retry_attempt == 0
    assert session.is_retrying is False

def test_agent_session_persists_and_reloads_typed_session_entries(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    model = faux_model()
    model.reasoning = True

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply")))
    session = AgentSession(cwd=str(tmp_path), model=model, session_path=str(session_path))
    session.prompt("hello")
    session.set_session_name("persisted name")
    session.set_thinking_level("high")
    replacement = faux_model()
    replacement.id = "replacement"
    replacement.provider = "faux"
    session.set_model(replacement)

    entries = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    assert entries[0]["type"] == "session"
    assert [entry["type"] for entry in entries[1:]] == [
        "message",
        "message",
        "session_info",
        "thinking_level_change",
        "model_change",
        "thinking_level_change",
    ]
    assert entries[-2]["provider"] == "faux"
    assert entries[-2]["modelId"] == "replacement"
    assert entries[-1]["thinkingLevel"] == "off"

    restored = AgentSession(cwd=str(tmp_path), model=replacement, session_path=str(session_path))

    assert restored.session_name == "persisted name"
    assert restored.thinking_level == "off"
    assert [getattr(message, "role", None) for message in restored.messages] == ["user", "assistant"]
    assert restored.messages[0].content == [TextContent(text="hello")]
    assert restored.messages[1].content[0].text == "reply"

def test_agent_session_branch_repoints_leaf_and_persists_new_child(tmp_path: Path) -> None:
    session_path = tmp_path / "branch-session.jsonl"
    model = faux_model()
    responses = iter(["first reply", "second reply", "branch reply"])

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, next(responses))))
    session = AgentSession(cwd=str(tmp_path), model=model, session_path=str(session_path))
    session.prompt("first")
    branch_point = session.session_entries[-1]["id"]
    session.prompt("second")

    assert [
        "".join(block.text for block in message.content if isinstance(block, TextContent))
        for message in session.messages
        if isinstance(message, UserMessage)
    ] == ["first", "second"]

    session.branch(branch_point)
    session.prompt("branch")

    assert [
        "".join(block.text for block in message.content if isinstance(block, TextContent))
        for message in session.messages
        if isinstance(message, UserMessage)
    ] == ["first", "branch"]
    persisted = [json.loads(line) for line in session_path.read_text(encoding="utf-8").splitlines()]
    branch_user = next(
        entry
        for entry in persisted
        if entry["type"] == "message"
        and entry["message"]["content"] == [{"type": "text", "text": "branch", "textSignature": None}]
    )
    assert branch_user["parentId"] == branch_point

    restored = AgentSession(cwd=str(tmp_path), model=model, session_path=str(session_path))
    assert [
        "".join(block.text for block in message.content if isinstance(block, TextContent))
        for message in restored.messages
        if isinstance(message, UserMessage)
    ] == ["first", "branch"]

def test_agent_session_export_to_jsonl_writes_active_branch_with_linear_parent_ids(tmp_path: Path) -> None:
    session_path = tmp_path / "export-source.jsonl"
    export_path = tmp_path / "exports" / "active-branch.jsonl"
    model = faux_model()
    responses = iter(["first reply", "second reply", "branch reply"])

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, next(responses))))
    session = AgentSession(cwd=str(tmp_path), model=model, session_path=str(session_path))
    session.prompt("first")
    branch_point = session.session_entries[-1]["id"]
    session.prompt("second")
    session.branch(branch_point)
    session.prompt("branch")

    returned_path = session.export_to_jsonl(str(export_path))

    assert returned_path == str(export_path)
    assert session.export_to_jsonl(str(tmp_path / "exports" / "active-branch-alias.jsonl")).endswith(
        "active-branch-alias.jsonl"
    )
    exported = [json.loads(line) for line in export_path.read_text(encoding="utf-8").splitlines()]
    assert exported[0]["type"] == "session"
    assert exported[0]["id"] == session.session_id
    assert exported[0]["cwd"] == str(tmp_path)
    assert [
        entry["message"]["content"]
        for entry in exported[1:]
        if entry["type"] == "message" and entry["message"]["role"] == "user"
    ] == [
        _serialized_text_content("first"),
        _serialized_text_content("branch"),
    ]
    assert "second" not in json.dumps(exported)

    previous_id = None
    for entry in exported[1:]:
        assert entry.get("parentId") == previous_id
        previous_id = entry["id"]

def test_agent_session_export_to_html_writes_standalone_session_view(tmp_path: Path) -> None:
    session_path = tmp_path / "html-source.jsonl"
    export_path = tmp_path / "exports" / "session.html"

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply <ok>")))
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session.prompt("hello <world>")

    returned_path = session.export_to_html(str(export_path))

    assert returned_path == str(export_path)
    assert session.export_to_html(str(tmp_path / "exports" / "session-alias.html")).endswith("session-alias.html")
    html = export_path.read_text(encoding="utf-8")
    assert "<title>Session Export</title>" in html
    assert 'id="session-data"' in html
    assert 'id="messages"' in html
    assert "hello &lt;world&gt;" not in html
    assert "reply &lt;ok&gt;" not in html
    encoded = html.split('<script id="session-data" type="application/json">', 1)[1].split("</script>", 1)[0]
    session_data = json.loads(base64.b64decode(encoded).decode("utf-8"))
    assert session_data["header"]["id"] == session.session_id
    assert session_data["header"]["cwd"] == str(tmp_path)
    assert session_data["leafId"] == session.session_entries[-1]["id"]
    assert [entry["type"] for entry in session_data["entries"]] == ["message", "message"]
    assert session_data["entries"][0]["message"]["content"] == _serialized_text_content("hello <world>")
    assert session_data["entries"][1]["message"]["content"][0]["text"] == "reply <ok>"
    assert "Available tools:" in session_data["systemPrompt"]
    assert any(tool["name"] == "read" for tool in session_data["tools"])

def test_agent_session_export_to_html_uses_travis234_browser_shell_contract(tmp_path: Path) -> None:
    session_path = tmp_path / "html-shell.jsonl"
    export_path = tmp_path / "exports" / "shell.html"

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply")))
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session.prompt("hello")

    session.export_to_html(str(export_path))

    html = export_path.read_text(encoding="utf-8")
    assert 'id="hamburger"' in html
    assert 'id="sidebar-overlay"' in html
    assert 'id="tree-search"' in html
    assert 'data-filter="no-tools"' in html
    assert 'id="tree-container"' in html
    assert 'id="tree-status"' in html
    assert 'id="sidebar-resizer"' in html
    assert 'id="image-modal"' in html
    assert "const base64 = document.getElementById('session-data').textContent;" in html
    assert "new TextDecoder('utf-8').decode(bytes)" in html
    assert "const { header, entries, leafId: defaultLeafId, systemPrompt, tools, renderedTools } = data;" in html
    assert "new URLSearchParams" in html
    assert "function buildTree()" in html
    assert "function getPath(targetId)" in html

def test_agent_session_export_to_html_uses_travis234_theme_and_layout_tokens(tmp_path: Path) -> None:
    session_path = tmp_path / "html-theme.jsonl"
    export_path = tmp_path / "exports" / "theme.html"

    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "reply")))
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(session_path))
    session.prompt("theme")

    session.export_to_html(str(export_path))

    html = export_path.read_text(encoding="utf-8")
    assert "--line-height: 18px;" in html
    assert "--dim:" in html
    assert "--selectedBg:" in html
    assert "--borderAccent:" in html
    assert "--customMessageBg:" in html
    assert "--userMessageBg:" in html
    assert "--toolPendingBg:" in html
    assert "font-size: 12px;" in html
    assert "line-height: var(--line-height);" in html
    assert "border-right: 1px solid var(--dim);" in html
    assert "background: var(--selectedBg);" in html
    assert "padding: var(--line-height) calc(var(--line-height) * 2);" in html
    assert "align-items: center;" in html
    assert "#content > *" in html
    assert "max-width: 800px;" in html
