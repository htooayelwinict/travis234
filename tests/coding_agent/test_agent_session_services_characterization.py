from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import replace
from pathlib import Path

import pytest

import travis.coding_agent.agent_session_services as services_module
from travis.agent.types import AgentMessage, AgentToolResult
from travis.ai.model_resolver import ScopedModel
from travis.ai.providers.faux import faux_model
from travis.ai.types import (
    Context,
    Message,
    Model,
    SimpleStreamOptions,
    TextContent,
    UserMessage,
)
from travis.coding_agent.agent_session_services import (
    create_agent_session_from_services,
)
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.extensions import ExtensionRunner
from travis.coding_agent.memory import MemorySettings
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.operations import RecoveryReport
from travis.coding_agent.resource_loader import DefaultResourceLoader
from travis.coding_agent.session_catalog import SessionCatalog
from travis.coding_agent.session_composition import SessionDependencies
from travis.coding_agent.session_contracts import SessionLifecyclePort
from travis.coding_agent.session_options import SessionBootstrapOptions
from travis.coding_agent.session_store import SessionStore
from travis.coding_agent.settings_manager import (
    InMemorySettingsStorage,
    SettingsManager,
)
from travis.coding_agent.tools.types import ToolDefinition


class _StateStore:
    def __init__(self, entries: list[dict[str, object]] | None = None) -> None:
        self.entries = list(entries or [])
        self.changes: list[tuple[str, ...]] = []

    def append_model_change(self, provider: str, model_id: str) -> str:
        self.changes.append(("model", provider, model_id))
        return "model-entry"

    def append_thinking_level_change(self, thinking_level: str) -> str:
        self.changes.append(("thinking", thinking_level))
        return "thinking-entry"


class _CapturedSession:
    def __init__(self, store: _StateStore | None = None) -> None:
        self._session_store = store
        self.disposed = False
        self.shutdown_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def dispose(self) -> None:
        self.disposed = True

    def shutdown(self, *args: object, **kwargs: object) -> None:
        self.shutdown_calls.append((args, kwargs))


class _SessionFactory:
    def __init__(
        self,
        *,
        session: _CapturedSession | None = None,
        error: BaseException | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.session = session or _CapturedSession()
        self.error = error
        self.events = events
        self.calls: list[dict[str, object]] = []

    def __call__(self, **kwargs: object) -> SessionLifecyclePort:
        self.calls.append(dict(kwargs))
        if self.events is not None:
            self.events.append("session_factory")
        if self.error is not None:
            raise self.error
        return self.session

    @property
    def call(self) -> dict[str, object]:
        assert self.calls
        return self.calls[-1]


class _BorrowedRuntime:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _OwnedRuntime(_BorrowedRuntime):
    def __init__(
        self,
        recovery_report: RecoveryReport | None = None,
        events: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.recovery_report = recovery_report or RecoveryReport()
        self.events = events

    def close(self) -> None:
        super().close()
        if self.events is not None:
            self.events.append("runtime_close")


class _RecordingResourceLoader(DefaultResourceLoader):
    def __init__(
        self,
        *,
        cwd: str,
        agent_dir: str,
        settings_manager: SettingsManager,
        events: list[str],
    ) -> None:
        super().__init__(
            cwd=cwd,
            agent_dir=agent_dir,
            settings_manager=settings_manager,
            no_extensions=True,
        )
        self.events = events
        self.extension_calls = 0
        initial = super().get_extensions()
        runtime = initial.get("runtime")
        assert isinstance(runtime, ExtensionRunner)
        self.extension_runtime = runtime

    def get_extensions(self) -> dict[str, object]:
        self.extension_calls += 1
        self.events.append("extensions")
        stage = "initial" if self.extension_calls == 1 else "final"
        return {"runtime": self.extension_runtime, "stage": stage}


class _CamelSettings(SettingsManager):
    def __init__(
        self,
        events: list[str],
        *,
        default_provider: str | None = None,
        default_model: str | None = None,
        default_thinking: str | None = None,
        memory_enabled: bool = False,
    ) -> None:
        super().__init__(
            InMemorySettingsStorage(),
            project_trusted=True,
        )
        self.events = events
        self.camel_default_provider = default_provider
        self.camel_default_model = default_model
        self.camel_default_thinking = default_thinking
        self.memory_enabled = memory_enabled

    def getDefaultProvider(self) -> str | None:
        self.events.append("default_provider")
        return self.camel_default_provider

    def getDefaultModel(self) -> str | None:
        self.events.append("default_model")
        return self.camel_default_model

    def getDefaultThinkingLevel(self) -> str | None:
        self.events.append("default_thinking")
        return self.camel_default_thinking

    def getProviderRetrySettings(self) -> dict[str, object]:
        self.events.append("provider_retry")
        return {
            "maxRetryDelayMs": 345,
            "max_retry_delay_ms": 999,
        }

    def getTransport(self) -> str:
        self.events.append("transport")
        return "camel-websocket"

    def getThinkingBudgets(self) -> dict[str, int]:
        self.events.append("thinking_budgets")
        return {"low": 111, "high": 333}

    def get_memory_settings(self) -> MemorySettings:
        self.events.append("memory")
        return MemorySettings(enabled=self.memory_enabled)


class _DependenciesFactory:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.catalogs: list[SessionCatalog] = []
        self.counter = 0

    def create(
        self,
        *,
        session_factory: _SessionFactory,
        settings_manager: SettingsManager | None = None,
        resource_loader: DefaultResourceLoader | None = None,
        model_registry: ModelRegistry | None = None,
        session_path: str | None = None,
        session_id: str = "service-session",
        operation_runtime: object | None = None,
        diagnostics: tuple[Mapping[str, object], ...] = (),
    ) -> SessionDependencies:
        self.counter += 1
        cwd_path = self.tmp_path / f"project-{self.counter}"
        cwd_path.mkdir(parents=True, exist_ok=True)
        agent_path = self.tmp_path / f"agent-{self.counter}"
        settings = settings_manager or SettingsManager.in_memory()
        auth_storage = (
            model_registry.auth_storage
            if model_registry is not None
            else AuthStorage.in_memory()
        )
        registry = model_registry or ModelRegistry.in_memory(auth_storage)
        loader = resource_loader or DefaultResourceLoader(
            cwd=str(cwd_path),
            agent_dir=str(agent_path),
            settings_manager=settings,
            no_extensions=True,
        )
        catalog = SessionCatalog(str(agent_path))
        self.catalogs.append(catalog)
        return SessionDependencies(
            cwd=str(cwd_path.resolve()),
            agent_dir=str(agent_path.resolve()),
            settings_manager=settings,
            resource_loader=loader,
            auth_storage=auth_storage,
            model_registry=registry,
            session_catalog=catalog,
            session_path=(
                session_path
                if session_path is not None
                else str(self.tmp_path / f"service-{self.counter}.jsonl")
            ),
            session_id=session_id,
            operation_runtime=operation_runtime,
            diagnostics=diagnostics,
            session_factory=session_factory,
        )

    def close(self) -> None:
        for catalog in self.catalogs:
            catalog.close()


@pytest.fixture
def dependencies_factory(tmp_path: Path) -> Iterator[_DependenciesFactory]:
    factory = _DependenciesFactory(tmp_path)
    try:
        yield factory
    finally:
        factory.close()


def _authorized_registry(*models: Model) -> ModelRegistry:
    auth_storage = AuthStorage.in_memory()
    for provider in {model.provider for model in models}:
        auth_storage.set(
            provider,
            {"type": "api_key", "key": f"{provider}-key"},
        )
    registry = ModelRegistry.in_memory(auth_storage)
    for model in models:
        registry.ensure_model(model)
    return registry


def _existing_session(
    path: Path,
    *,
    cwd: str,
    provider: str,
    model_id: str,
    thinking_level: str | None,
) -> None:
    store = SessionStore(str(path), cwd=cwd)
    store.append_model_change(provider, model_id)
    if thinking_level is not None:
        store.append_thinking_level_change(thinking_level)
    store.append_message(UserMessage(content=[TextContent(text="previous")]))


def _callable(value: object) -> Callable[..., object]:
    assert callable(value)
    return value


@pytest.mark.parametrize(
    ("options", "error_type", "message"),
    [
        ({}, ValueError, "services are required"),
        (
            {"services": object()},
            TypeError,
            "services must be SessionDependencies or a mapping",
        ),
        (
            {"services": {}},
            TypeError,
            "settingsManager must be a SettingsManager",
        ),
    ],
)
def test_service_factory_rejects_missing_and_invalid_services_exactly(
    options: dict[str, object],
    error_type: type[Exception],
    message: str,
) -> None:
    with pytest.raises(error_type, match=message):
        create_agent_session_from_services(options)


def test_typed_and_legacy_dependencies_normalize_to_the_same_factory_payload(
    dependencies_factory: _DependenciesFactory,
) -> None:
    runtime = _BorrowedRuntime()
    factory = _SessionFactory()
    dependencies = dependencies_factory.create(
        session_factory=factory,
        operation_runtime=runtime,
        diagnostics=({"type": "existing", "message": "kept"},),
    )
    model = faux_model()

    typed_result = create_agent_session_from_services(
        {"services": dependencies, "model": model}
    )
    typed_call = dict(factory.call)
    legacy = dependencies.to_legacy_mapping()
    legacy_result = create_agent_session_from_services(
        {"services": legacy, "model": model}
    )

    assert typed_result.session is factory.session
    assert legacy_result.session is factory.session
    for name, typed_value in typed_call.items():
        legacy_value = factory.call[name]
        if name in {"convert_to_llm", "stream_fn"}:
            assert callable(typed_value)
            assert callable(legacy_value)
        else:
            assert legacy_value == typed_value
    assert factory.call["resource_loader"] is dependencies.resource_loader
    assert factory.call["settings_manager"] is dependencies.settings_manager
    assert factory.call["model_registry"] is dependencies.model_registry
    assert factory.call["session_index"] is dependencies.session_catalog.index
    assert legacy["diagnostics"] == [
        {"type": "existing", "message": "kept"}
    ]


def test_service_location_and_runtime_defaults_are_used_with_resolved_path(
    dependencies_factory: _DependenciesFactory,
) -> None:
    factory = _SessionFactory()
    runtime = _BorrowedRuntime()
    relative_path = "relative-session.jsonl"
    dependencies = dependencies_factory.create(
        session_factory=factory,
        operation_runtime=runtime,
        session_path=relative_path,
        session_id="from-services",
    )

    create_agent_session_from_services(
        {"services": dependencies, "model": faux_model()}
    )

    assert factory.call["session_path"] == str(Path(relative_path).resolve())
    assert factory.call["session_id"] == "from-services"
    assert factory.call["operation_runtime"] is runtime
    assert factory.call["owns_operation_runtime"] is False


def test_session_path_probes_preserve_fresh_load_then_entry_order(
    dependencies_factory: _DependenciesFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _SessionFactory()
    dependencies = dependencies_factory.create(
        session_factory=factory,
        operation_runtime=_BorrowedRuntime(),
    )
    probes: list[str] = []

    def is_fresh(_session_path: str | None) -> bool:
        probes.append("fresh")
        return True

    def load_existing(
        _cwd: str,
        _session_path: str | None,
        _thinking_level: str,
    ) -> None:
        probes.append("load")
        return None

    def has_entry(_session_path: str | None, _entry_type: str) -> bool:
        probes.append("entry")
        return False

    monkeypatch.setattr(services_module, "_is_fresh_session_path", is_fresh)
    monkeypatch.setattr(
        services_module,
        "_load_existing_session_context",
        load_existing,
    )
    monkeypatch.setattr(services_module, "_has_session_entry_type", has_entry)

    create_agent_session_from_services(
        {"services": dependencies, "model": faux_model()}
    )

    assert probes == ["fresh", "load", "entry"]


def test_explicit_location_and_runtime_override_services_including_none(
    dependencies_factory: _DependenciesFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_runtime = _BorrowedRuntime()
    explicit_runtime = _BorrowedRuntime()
    value_factory = _SessionFactory()
    value_dependencies = dependencies_factory.create(
        session_factory=value_factory,
        operation_runtime=service_runtime,
        session_path="service-session.jsonl",
        session_id="service-id",
    )
    explicit_path = "explicit-session.jsonl"

    create_agent_session_from_services(
        {
            "services": value_dependencies,
            "model": faux_model(),
            "sessionPath": explicit_path,
            "sessionId": "explicit-id",
            "operationRuntime": explicit_runtime,
        }
    )

    assert value_factory.call["session_path"] == str(Path(explicit_path).resolve())
    assert value_factory.call["session_id"] == "explicit-id"
    assert value_factory.call["operation_runtime"] is explicit_runtime
    assert value_factory.call["owns_operation_runtime"] is False

    owned_runtime = _OwnedRuntime()
    none_factory = _SessionFactory()
    none_dependencies = dependencies_factory.create(
        session_factory=none_factory,
        operation_runtime=service_runtime,
        session_path="service-session.jsonl",
        session_id="service-id",
    )
    monkeypatch.setattr(
        services_module.OperationRuntime,
        "from_settings",
        lambda *_args, **_kwargs: owned_runtime,
    )

    create_agent_session_from_services(
        {
            "services": none_dependencies,
            "model": faux_model(),
            "sessionPath": None,
            "sessionId": None,
            "operationRuntime": None,
        }
    )

    assert none_factory.call["session_path"] is None
    assert none_factory.call["session_id"] is None
    assert none_factory.call["operation_runtime"] is owned_runtime
    assert none_factory.call["owns_operation_runtime"] is True


def test_existing_session_restores_authenticated_model_and_persisted_thinking(
    dependencies_factory: _DependenciesFactory,
    tmp_path: Path,
) -> None:
    saved = replace(faux_model(), provider="saved", id="session", name="Saved")
    default = replace(faux_model(), provider="default", id="fallback", name="Default")
    registry = _authorized_registry(saved, default)
    settings = SettingsManager.in_memory(
        {
            "defaultProvider": "default",
            "defaultModel": "fallback",
            "defaultThinkingLevel": "off",
        }
    )
    factory = _SessionFactory()
    session_path = tmp_path / "existing-authenticated.jsonl"
    dependencies = dependencies_factory.create(
        session_factory=factory,
        settings_manager=settings,
        model_registry=registry,
        operation_runtime=_BorrowedRuntime(),
        session_path=str(session_path),
    )
    _existing_session(
        session_path,
        cwd=dependencies.cwd,
        provider="saved",
        model_id="session",
        thinking_level="medium",
    )

    result = create_agent_session_from_services({"services": dependencies})

    assert factory.call["model"] is saved
    assert factory.call["thinking_level"] == "medium"
    assert result.model_fallback_message is None


def test_unavailable_restore_message_composes_with_selected_initial_model(
    dependencies_factory: _DependenciesFactory,
    tmp_path: Path,
) -> None:
    fallback = replace(
        faux_model(),
        provider="fallback",
        id="default",
        name="Fallback",
    )
    registry = _authorized_registry(fallback)
    settings = SettingsManager.in_memory(
        {
            "defaultProvider": "fallback",
            "defaultModel": "default",
        }
    )
    factory = _SessionFactory()
    session_path = tmp_path / "existing-unavailable.jsonl"
    dependencies = dependencies_factory.create(
        session_factory=factory,
        settings_manager=settings,
        model_registry=registry,
        operation_runtime=_BorrowedRuntime(),
        session_path=str(session_path),
    )
    _existing_session(
        session_path,
        cwd=dependencies.cwd,
        provider="saved",
        model_id="missing",
        thinking_level="high",
    )

    result = create_agent_session_from_services({"services": dependencies})

    assert factory.call["model"] is fallback
    assert result.model_fallback_message == (
        "Could not restore model saved/missing. Using fallback/default"
    )


def test_initial_model_fallback_text_takes_precedence_over_restore_text(
    dependencies_factory: _DependenciesFactory,
    tmp_path: Path,
) -> None:
    fallback = replace(
        faux_model(),
        provider="fallback",
        id="available",
        name="Fallback",
    )
    registry = _authorized_registry(fallback)
    settings = SettingsManager.in_memory(
        {
            "defaultProvider": "configured",
            "defaultModel": "missing",
        }
    )
    factory = _SessionFactory()
    session_path = tmp_path / "existing-double-fallback.jsonl"
    dependencies = dependencies_factory.create(
        session_factory=factory,
        settings_manager=settings,
        model_registry=registry,
        operation_runtime=_BorrowedRuntime(),
        session_path=str(session_path),
    )
    _existing_session(
        session_path,
        cwd=dependencies.cwd,
        provider="saved",
        model_id="missing",
        thinking_level=None,
    )

    result = create_agent_session_from_services({"services": dependencies})

    assert factory.call["model"] is fallback
    assert result.model_fallback_message == (
        'Configured default "configured/missing" is unavailable; '
        'using "fallback/available".'
    )


@pytest.mark.parametrize(
    ("persisted_thinking", "expected_thinking"),
    [("xhigh", "xhigh"), (None, "off")],
)
def test_existing_thinking_is_restored_only_when_entry_is_persisted(
    dependencies_factory: _DependenciesFactory,
    tmp_path: Path,
    persisted_thinking: str | None,
    expected_thinking: str,
) -> None:
    factory = _SessionFactory()
    session_path = tmp_path / f"thinking-{persisted_thinking or 'absent'}.jsonl"
    dependencies = dependencies_factory.create(
        session_factory=factory,
        operation_runtime=_BorrowedRuntime(),
        session_path=str(session_path),
    )
    _existing_session(
        session_path,
        cwd=dependencies.cwd,
        provider="ignored",
        model_id="ignored",
        thinking_level=persisted_thinking,
    )

    create_agent_session_from_services(
        {"services": dependencies, "model": faux_model()}
    )

    assert factory.call["thinking_level"] == expected_thinking


@pytest.mark.parametrize("create_empty_path", [False, True])
def test_fresh_session_records_initial_model_then_thinking_exactly_once(
    dependencies_factory: _DependenciesFactory,
    tmp_path: Path,
    create_empty_path: bool,
) -> None:
    state_store = _StateStore()
    factory = _SessionFactory(session=_CapturedSession(state_store))
    session_path = tmp_path / f"fresh-{create_empty_path}.jsonl"
    if create_empty_path:
        session_path.touch()
    dependencies = dependencies_factory.create(
        session_factory=factory,
        operation_runtime=_BorrowedRuntime(),
        session_path=str(session_path),
    )
    model = faux_model()

    create_agent_session_from_services(
        {
            "services": dependencies,
            "model": model,
            "thinkingLevel": "high",
        }
    )

    assert state_store.changes == [
        ("model", "faux", "faux-model"),
        ("thinking", "high"),
    ]


@pytest.mark.parametrize(
    ("options", "expected_active", "expected_allowed"),
    [
        ({}, ["read", "bash", "edit", "write", "memory"], None),
        (
            {"tools": ["read", "memory"], "noTools": "all"},
            ["read", "memory"],
            ["read", "memory"],
        ),
        ({"noTools": True}, [], None),
        ({"noTools": "all"}, [], []),
    ],
)
def test_memory_and_tool_selection_preserve_precedence(
    dependencies_factory: _DependenciesFactory,
    options: dict[str, object],
    expected_active: list[str],
    expected_allowed: list[str] | None,
) -> None:
    factory = _SessionFactory()
    dependencies = dependencies_factory.create(
        session_factory=factory,
        settings_manager=SettingsManager.in_memory(
            {"memory": {"enabled": True}}
        ),
        operation_runtime=_BorrowedRuntime(),
    )

    create_agent_session_from_services(
        {"services": dependencies, "model": faux_model(), **options}
    )

    assert factory.call["active_tool_names"] == expected_active
    assert factory.call["allowed_tool_names"] == expected_allowed


def test_snake_settings_methods_wire_transport_thinking_and_retry(
    dependencies_factory: _DependenciesFactory,
) -> None:
    factory = _SessionFactory()
    settings = SettingsManager.in_memory(
        {
            "transport": "websocket",
            "thinkingBudgets": {"low": 12},
            "retry": {"provider": {"maxRetryDelayMs": 789}},
        }
    )
    dependencies = dependencies_factory.create(
        session_factory=factory,
        settings_manager=settings,
        operation_runtime=_BorrowedRuntime(),
    )

    create_agent_session_from_services(
        {"services": dependencies, "model": faux_model()}
    )

    assert factory.call["transport"] == "websocket"
    assert factory.call["thinking_budgets"] == {"low": 12}
    assert factory.call["max_retry_delay_ms"] == 789


def test_factory_payload_wires_camel_settings_tools_converter_stream_and_order(
    dependencies_factory: _DependenciesFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    settings = _CamelSettings(events, memory_enabled=True)
    loader = _RecordingResourceLoader(
        cwd=str(tmp_path / "ordered-project"),
        agent_dir=str(tmp_path / "ordered-agent"),
        settings_manager=settings,
        events=events,
    )
    factory = _SessionFactory(events=events)
    runtime = _BorrowedRuntime()
    model = faux_model()
    registry = _authorized_registry(model)
    dependencies = dependencies_factory.create(
        session_factory=factory,
        settings_manager=settings,
        resource_loader=loader,
        model_registry=registry,
        operation_runtime=runtime,
        session_path=str(tmp_path / "ordered.jsonl"),
        session_id="ordered-id",
    )
    builtin = object()
    custom = ToolDefinition(
        name="custom",
        label="Custom",
        description="custom tool",
        parameters={"type": "object", "properties": {}},
        execute=lambda *_args: AgentToolResult(content=[TextContent(text="ok")]),
    )
    builtin_calls: list[tuple[str, dict[str, dict[str, object]]]] = []

    def create_builtins(
        cwd: str,
        tool_options: dict[str, dict[str, object]],
    ) -> list[object]:
        builtin_calls.append((cwd, tool_options))
        return [builtin]

    monkeypatch.setattr(
        services_module,
        "create_all_tool_definitions",
        create_builtins,
    )
    converted: list[Message] = [UserMessage(content="converted")]
    converter_calls: list[list[AgentMessage]] = []

    def converter(messages: list[AgentMessage]) -> list[Message]:
        converter_calls.append(messages)
        return converted

    stream_result = object()
    stream_calls: list[tuple[Model, Context, SimpleStreamOptions | None]] = []

    def stream_simple(
        stream_model: Model,
        context: Context,
        options: SimpleStreamOptions | None = None,
    ) -> object:
        stream_calls.append((stream_model, context, options))
        return stream_result

    monkeypatch.setattr(registry, "stream_simple", stream_simple)
    role_binding = ScopedModel(model, "low")
    role_events: list[dict[str, object]] = []

    def role_sink(event: dict[str, object]) -> None:
        role_events.append(event)

    session_start = {"type": "session_start", "source": "test"}

    result = create_agent_session_from_services(
        {
            "services": dependencies,
            "model": model,
            "thinkingLevel": "low",
            "scopedModels": [role_binding],
            "excludeTools": ["write"],
            "customTools": [custom],
            "convertToLlm": converter,
            "parentSession": "parent.jsonl",
            "sessionStartEvent": session_start,
            "deferSessionStart": True,
            "modelRoleBindings": {"worker": role_binding},
            "modelRoleEventSink": role_sink,
        }
    )

    assert events == [
        "extensions",
        "memory",
        "provider_retry",
        "transport",
        "thinking_budgets",
        "session_factory",
        "extensions",
    ]
    assert tuple(factory.call) == (
        "cwd",
        "agent_dir",
        "model",
        "thinking_level",
        "scoped_models",
        "active_tool_names",
        "allowed_tool_names",
        "excluded_tool_names",
        "transport",
        "thinking_budgets",
        "max_retry_delay_ms",
        "tool_definitions",
        "convert_to_llm",
        "resource_loader",
        "settings_manager",
        "extension_runner",
        "stream_fn",
        "model_registry",
        "session_index",
        "session_path",
        "parent_session_path",
        "session_id",
        "session_start_event",
        "defer_session_start",
        "model_role_bindings",
        "model_role_event_sink",
        "operation_runtime",
        "owns_operation_runtime",
    )
    assert factory.call["cwd"] == dependencies.cwd
    assert factory.call["agent_dir"] == dependencies.agent_dir
    assert factory.call["model"] is model
    assert factory.call["thinking_level"] == "low"
    assert factory.call["scoped_models"] == [role_binding]
    assert factory.call["active_tool_names"] == [
        "read",
        "bash",
        "edit",
        "write",
        "memory",
    ]
    assert factory.call["allowed_tool_names"] is None
    assert factory.call["excluded_tool_names"] == ["write"]
    assert factory.call["transport"] == "camel-websocket"
    assert factory.call["thinking_budgets"] == {"low": 111, "high": 333}
    assert factory.call["max_retry_delay_ms"] == 345
    assert factory.call["tool_definitions"] == [builtin, custom]
    assert factory.call["resource_loader"] is loader
    assert factory.call["settings_manager"] is settings
    assert factory.call["extension_runner"] is loader.extension_runtime
    assert factory.call["model_registry"] is registry
    assert factory.call["session_index"] is dependencies.session_catalog.index
    assert factory.call["session_path"] == str((tmp_path / "ordered.jsonl").resolve())
    assert factory.call["parent_session_path"] == "parent.jsonl"
    assert factory.call["session_id"] == "ordered-id"
    assert factory.call["session_start_event"] is session_start
    assert factory.call["defer_session_start"] is True
    assert factory.call["model_role_bindings"] == {"worker": role_binding}
    assert factory.call["model_role_event_sink"] is role_sink
    assert factory.call["operation_runtime"] is runtime
    assert factory.call["owns_operation_runtime"] is False
    assert builtin_calls == [
        (
            dependencies.cwd,
            {
                "read": {"auto_resize_images": True},
                "bash": {"command_prefix": None, "shell_path": None},
            },
        )
    ]
    assert result.extensions_result == {
        "runtime": loader.extension_runtime,
        "stage": "final",
    }

    convert_to_llm = _callable(factory.call["convert_to_llm"])
    source_messages: list[AgentMessage] = [UserMessage(content="source")]
    assert convert_to_llm(source_messages) is converted
    assert converter_calls == [source_messages]

    stream_fn = _callable(factory.call["stream_fn"])
    context = Context(messages=[])
    assert stream_fn(model, context, SimpleStreamOptions()) is stream_result
    assert len(stream_calls) == 1
    assert stream_calls[0][0] is model
    assert stream_calls[0][1] is context
    stream_options = stream_calls[0][2]
    assert isinstance(stream_options, SimpleStreamOptions)
    assert stream_options.max_retry_delay_ms == 345


def test_camel_default_model_aliases_are_used_for_initial_selection(
    dependencies_factory: _DependenciesFactory,
) -> None:
    events: list[str] = []
    selected = replace(faux_model(), provider="camel", id="selected", name="Selected")
    registry = _authorized_registry(selected)
    settings = _CamelSettings(
        events,
        default_provider="camel",
        default_model="selected",
        default_thinking="high",
    )
    factory = _SessionFactory()
    dependencies = dependencies_factory.create(
        session_factory=factory,
        settings_manager=settings,
        model_registry=registry,
        operation_runtime=_BorrowedRuntime(),
    )

    create_agent_session_from_services({"services": dependencies})

    assert factory.call["model"] is selected
    assert factory.call["thinking_level"] == "high"
    assert events[:3] == [
        "default_provider",
        "default_model",
        "default_thinking",
    ]


def test_no_available_model_preserves_exact_error(
    dependencies_factory: _DependenciesFactory,
) -> None:
    factory = _SessionFactory()
    dependencies = dependencies_factory.create(
        session_factory=factory,
        operation_runtime=_BorrowedRuntime(),
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "No models available\\. Check your installation or add models "
            "to models\\.json\\."
        ),
    ):
        create_agent_session_from_services({"services": dependencies})


def test_owned_runtime_recovery_updates_typed_and_mutable_legacy_services(
    dependencies_factory: _DependenciesFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recovery = RecoveryReport(
        inspected_runtime_count=2,
        stale_runtime_count=1,
        uncertain_effect_count=3,
        uncertain_operation_count=2,
    )
    typed_runtime = _OwnedRuntime(recovery)
    legacy_runtime = _OwnedRuntime(recovery)
    runtimes = [typed_runtime, legacy_runtime]
    create_calls: list[tuple[str, Mapping[str, object], float | None]] = []

    def create_runtime(
        agent_dir: str,
        settings: Mapping[str, object],
        *,
        heartbeat_interval_seconds: float | None = 20.0,
    ) -> _OwnedRuntime:
        create_calls.append(
            (agent_dir, dict(settings), heartbeat_interval_seconds)
        )
        return runtimes.pop(0)

    monkeypatch.setattr(
        services_module.OperationRuntime,
        "from_settings",
        create_runtime,
    )
    observed_services: list[SessionDependencies] = []

    def capture_tool_services(
        services: SessionDependencies,
        _options: SessionBootstrapOptions,
    ) -> list[object] | None:
        observed_services.append(services)
        return None

    monkeypatch.setattr(
        services_module,
        "_tool_definitions_for_sdk",
        capture_tool_services,
    )
    typed_factory = _SessionFactory()
    typed = dependencies_factory.create(
        session_factory=typed_factory,
        operation_runtime=None,
        diagnostics=({"type": "existing"},),
    )

    create_agent_session_from_services(
        {"services": typed, "model": faux_model()}
    )

    assert typed.operation_runtime is None
    assert typed.diagnostics == ({"type": "existing"},)
    assert observed_services[0].operation_runtime is typed_runtime
    assert observed_services[0].diagnostics == (
        {"type": "existing"},
        recovery.as_dict(),
    )
    assert typed_factory.call["operation_runtime"] is typed_runtime
    assert typed_factory.call["owns_operation_runtime"] is True

    legacy_factory = _SessionFactory()
    legacy_dependencies = dependencies_factory.create(
        session_factory=legacy_factory,
        operation_runtime=None,
        diagnostics=({"type": "existing"},),
    )
    legacy = legacy_dependencies.to_legacy_mapping()

    create_agent_session_from_services(
        {"services": legacy, "model": faux_model()}
    )

    assert legacy["operationRuntime"] is legacy_runtime
    assert legacy["diagnostics"] == [
        {"type": "existing"},
        recovery.as_dict(),
    ]
    assert observed_services[1].operation_runtime is legacy_runtime
    assert observed_services[1].diagnostics == (
        {"type": "existing"},
        recovery.as_dict(),
    )
    assert create_calls == [
        (
            typed.agent_dir,
            typed.settings_manager.get_operation_settings(),
            None,
        ),
        (
            legacy_dependencies.agent_dir,
            legacy_dependencies.settings_manager.get_operation_settings(),
            None,
        ),
    ]


def test_owned_runtime_closes_on_factory_error_but_borrowed_runtime_does_not(
    dependencies_factory: _DependenciesFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    owned_runtime = _OwnedRuntime(events=events)
    expected_error = RuntimeError("session construction failed")
    owned_factory = _SessionFactory(error=expected_error, events=events)
    owned_dependencies = dependencies_factory.create(
        session_factory=owned_factory,
        operation_runtime=None,
    )

    def create_runtime(*_args: object, **_kwargs: object) -> _OwnedRuntime:
        events.append("runtime_create")
        return owned_runtime

    monkeypatch.setattr(
        services_module.OperationRuntime,
        "from_settings",
        create_runtime,
    )

    with pytest.raises(RuntimeError) as caught:
        create_agent_session_from_services(
            {"services": owned_dependencies, "model": faux_model()}
        )

    assert caught.value is expected_error
    assert events == ["runtime_create", "session_factory", "runtime_close"]
    assert owned_runtime.close_calls == 1

    borrowed_runtime = _BorrowedRuntime()
    borrowed_error = RuntimeError("borrowed construction failed")
    borrowed_factory = _SessionFactory(error=borrowed_error)
    borrowed_dependencies = dependencies_factory.create(
        session_factory=borrowed_factory,
        operation_runtime=borrowed_runtime,
    )

    with pytest.raises(RuntimeError) as borrowed_caught:
        create_agent_session_from_services(
            {"services": borrowed_dependencies, "model": faux_model()}
        )

    assert borrowed_caught.value is borrowed_error
    assert borrowed_runtime.close_calls == 0
