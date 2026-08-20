from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict, Unpack

import pytest

import travis.app as app_module
from travis.ai.event_stream import AssistantMessageEventStream, create_assistant_message_event_stream
from travis.ai.model_resolver import ScopedModel
from travis.ai.providers.faux import text_response_events
from travis.ai.types import Context, Model, SimpleStreamOptions, ThinkingLevel
from travis.app import CodingApp
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.model_roles import ModelRole
from travis.coding_agent.resource_loader import DefaultResourceLoader
from travis.coding_agent.settings_manager import SettingsManager
from travis.tui.terminal import FakeTerminal, Terminal


def _model(name: str) -> Model:
    return Model(
        id=name,
        name=name,
        api="openai-completions",
        provider="test-provider",
        base_url="https://provider.example.test/v1",
        reasoning=True,
    )


class _Registry(ModelRegistry):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def set_offline(self, enabled: bool) -> None:
        self.events.append(f"registry:offline:{enabled}")

    def ensure_model(self, model: Model) -> None:
        self.events.append(f"registry:ensure:{model.id}")

    def stream_simple(
        self,
        model: Model,
        context: Context,
        options: SimpleStreamOptions | None = None,
    ) -> AssistantMessageEventStream:
        self.events.append(f"registry:stream:{model.id}:{context.system_prompt}:{options is not None}")
        stream = create_assistant_message_event_stream()
        for event in text_response_events(model, "summary response"):
            stream.push(event)
        return stream


class _SnakeSettings(SettingsManager):
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.operation_settings = {"mode": "observe", "maxPayloadBytes": 4096}

    def set_default_thinking_level(self, level: str) -> None:
        self.events.append(f"settings:thinking:snake:{level}")

    def get_retry_settings(self) -> dict[str, object]:
        self.events.append("settings:retry:snake")
        return {"max_retries": "3", "base_delay_ms": "7"}

    def get_retry_enabled(self) -> bool:
        self.events.append("settings:retry-enabled:snake")
        return False

    def get_session_dir(self) -> str | None:
        self.events.append("settings:session-dir:snake")
        return "/snake/sessions"

    def get_operation_settings(self) -> dict[str, object]:
        self.events.append("settings:operations")
        return dict(self.operation_settings)


class _CamelSettings(SettingsManager):
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.operation_settings = {"mode": "enforce", "maxPayloadBytes": 8192}

    def setDefaultThinkingLevel(self, level: str) -> None:
        self.events.append(f"settings:thinking:camel:{level}")

    def getRetrySettings(self) -> dict[str, object]:
        self.events.append("settings:retry:camel")
        return {"maxRetries": "5", "baseDelayMs": "11"}

    def getRetryEnabled(self) -> bool:
        self.events.append("settings:retry-enabled:camel")
        return True

    def getSessionDir(self) -> str:
        self.events.append("settings:session-dir:camel")
        return "/camel/sessions"

    def get_operation_settings(self) -> dict[str, object]:
        self.events.append("settings:operations")
        return dict(self.operation_settings)


class _SessionState:
    def __init__(self, cwd: str, model: Model, thinking_level: ThinkingLevel) -> None:
        self.cwd = cwd
        self.model = model
        self.thinking_level = thinking_level


def _session(cwd: Path, model: Model, thinking_level: ThinkingLevel) -> AgentSession:
    session = object.__new__(AgentSession)
    object.__setattr__(session, "_runtime", _SessionState(str(cwd), model, thinking_level))
    return session


class _OperationOwner:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.closed = False

    def close(self) -> None:
        self._events.append("operation:close")
        self.closed = True


class _RuntimeProbe:
    def __init__(
        self,
        events: list[str],
        session: AgentSession,
        services: dict[str, object],
        create_runtime: Callable[[dict[str, object]], object],
    ) -> None:
        self._events = events
        self.session = session
        self.services = services
        self.create_runtime = create_runtime
        self.before_invalidate: Callable[[], object] | None = None
        self.rebind: Callable[[object], object] | None = None
        events.append("session-runtime:init")

    def set_before_session_invalidate(
        self,
        callback: Callable[[], object] | None = None,
    ) -> None:
        self._events.append("session-runtime:before-invalidate")
        self.before_invalidate = callback

    def set_rebind_session(
        self,
        callback: Callable[[object], object] | None = None,
    ) -> None:
        self._events.append("session-runtime:rebind")
        self.rebind = callback


@dataclass
class _SummarizerCall:
    model: Model | Callable[[], Model]
    thinking_level: ThinkingLevel | Callable[[], ThinkingLevel]
    complete_fn: Callable[[Model, Context, SimpleStreamOptions], object]


@dataclass
class _ConstructionProbe:
    events: list[str]
    registry: _Registry
    settings: SettingsManager
    session: AgentSession
    operation_owner: _OperationOwner
    operation_options: dict[str, object] = field(default_factory=dict)
    create_session_kwargs: dict[str, object] = field(default_factory=dict)
    completion_path: Path | None = None
    completion_store: object | None = None
    process_options: dict[str, object] = field(default_factory=dict)
    catalog_options: dict[str, object] = field(default_factory=dict)
    tui_terminal: Terminal | None = None
    tui_interval: float | None = None
    default_terminal: Terminal | None = None
    summarizer_calls: list[_SummarizerCall] = field(default_factory=list)
    runtime: _RuntimeProbe | None = None


def _install_constructor_probes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    registry: _Registry | None = None,
    settings: SettingsManager | None = None,
    session_error: BaseException | None = None,
) -> _ConstructionProbe:
    events = (
        registry.events
        if registry is not None
        else settings.events
        if isinstance(settings, (_SnakeSettings, _CamelSettings))
        else []
    )
    resolved_registry = registry or _Registry(events)
    resolved_settings = settings or _SnakeSettings(events)
    primary = _model("primary")
    fake_session = _session(tmp_path, primary, "high")
    operation_owner = _OperationOwner(events)
    probe = _ConstructionProbe(
        events=events,
        registry=resolved_registry,
        settings=resolved_settings,
        session=fake_session,
        operation_owner=operation_owner,
    )

    class FakeCompletionStore:
        def __init__(self, path: Path) -> None:
            events.append(f"process-store:{path}")
            probe.completion_path = path
            probe.completion_store = self

    class FakeProcessService:
        def __init__(self, **options: object) -> None:
            events.append("process-service:init")
            probe.process_options = dict(options)

    class FakeSessionCatalog:
        def __init__(self, agent_dir: str, *, session_dir: str | None = None) -> None:
            events.append("session-catalog:init")
            probe.catalog_options = {
                "agent_dir": agent_dir,
                "session_dir": session_dir,
            }

    class FakeTui:
        def __init__(self, terminal: Terminal, *, render_interval: float) -> None:
            events.append("tui:init")
            probe.tui_terminal = terminal
            probe.tui_interval = render_interval

    class FakeRuntime:
        def __init__(
            self,
            session: AgentSession,
            services: dict[str, object],
            create_runtime: Callable[[dict[str, object]], object],
        ) -> None:
            probe.runtime = _RuntimeProbe(events, session, services, create_runtime)

        def set_before_session_invalidate(
            self,
            callback: Callable[[], object] | None = None,
        ) -> None:
            assert probe.runtime is not None
            probe.runtime.set_before_session_invalidate(callback)

        def set_rebind_session(
            self,
            callback: Callable[[object], object] | None = None,
        ) -> None:
            assert probe.runtime is not None
            probe.runtime.set_rebind_session(callback)

    def process_terminal() -> Terminal:
        events.append("terminal:default")
        terminal = FakeTerminal(columns=101, rows=37)
        probe.default_terminal = terminal
        return terminal

    def model_summarizer(
        model: Model | Callable[[], Model],
        *,
        thinking_level: ThinkingLevel | Callable[[], ThinkingLevel] = "off",
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        generation_params: object | None = None,
        complete_fn: Callable[[Model, Context, SimpleStreamOptions], object] | None = None,
    ) -> Callable[[str], str]:
        del api_key, timeout_seconds, generation_params
        assert complete_fn is not None
        events.append("summarizer:default")
        probe.summarizer_calls.append(
            _SummarizerCall(model, thinking_level, complete_fn)
        )
        return lambda prompt: f"summary:{prompt}"

    def operation_from_settings(
        agent_dir: str,
        operation_settings: Mapping[str, object],
    ) -> _OperationOwner:
        events.append(f"operation:init:{agent_dir}:{dict(operation_settings)}")
        probe.operation_options = dict(operation_settings)
        return operation_owner

    def create_session(self: CodingApp, **kwargs: object) -> AgentSession:
        del self
        events.append("session:create")
        probe.create_session_kwargs = dict(kwargs)
        if session_error is not None:
            raise session_error
        return fake_session

    def bind_session(self: CodingApp, session: AgentSession) -> None:
        events.append("session:bind")
        self.session = session

    monkeypatch.setattr(app_module, "ProcessCompletionStore", FakeCompletionStore)
    monkeypatch.setattr(app_module, "ProcessSessionService", FakeProcessService)
    monkeypatch.setattr(app_module, "SessionCatalog", FakeSessionCatalog)
    monkeypatch.setattr(app_module, "ProcessTerminal", process_terminal)
    monkeypatch.setattr(app_module, "TUI", FakeTui)
    monkeypatch.setattr(app_module, "AgentSessionRuntime", FakeRuntime)
    monkeypatch.setattr(app_module, "_model_summarizer", model_summarizer)
    monkeypatch.setattr(
        app_module.OperationRuntime,
        "from_settings",
        staticmethod(operation_from_settings),
    )
    monkeypatch.setattr(CodingApp, "_create_session", create_session)
    monkeypatch.setattr(CodingApp, "_bind_session", bind_session)
    return probe


class _AppOptions(TypedDict, total=False):
    terminal: Terminal | None
    context_length: int | None
    summarizer: Callable[[str], str] | None
    compression_model: Model | None
    compression_api_key: str | None
    compression_timeout_seconds: float | None
    compression_generation_params: object | None
    thinking_level: ThinkingLevel
    scoped_models: list[ScopedModel] | None
    enable_tui: bool
    project_trust_override: bool | None
    session_path: str | None
    session_id: str | None
    allowed_tool_names: list[str] | None
    excluded_tool_names: list[str] | None
    additional_active_tool_names: list[str] | None
    additional_extension_paths: list[str] | None
    additional_skill_paths: list[str] | None
    additional_prompt_template_paths: list[str] | None
    additional_theme_paths: list[str] | None
    initial_resource_loader: DefaultResourceLoader | None
    extension_flag_values: Mapping[str, bool | str] | None
    offline: bool
    model_role_bindings: Mapping[ModelRole, ScopedModel] | None


def _new_app(
    tmp_path: Path,
    probe: _ConstructionProbe,
    **options: Unpack[_AppOptions],
) -> CodingApp:
    return CodingApp(
        cwd=str(tmp_path),
        model=_model("primary"),
        agent_dir=str(tmp_path / "agent"),
        model_registry=probe.registry,
        settings_manager=probe.settings,
        **options,
    )


def test_constructor_registers_models_and_applies_compression_binding_last(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    registry = _Registry(events)
    settings = _SnakeSettings(events)
    probe = _install_constructor_probes(
        monkeypatch,
        tmp_path,
        registry=registry,
        settings=settings,
    )
    review_model = _model("review")
    configured_compression = _model("configured-compression")
    legacy_compression = _model("legacy-compression")
    review_binding = ScopedModel(review_model, "high")
    configured_binding = ScopedModel(configured_compression, "low")
    bindings: dict[ModelRole, ScopedModel] = {
        "reviewer": review_binding,
        "compression": configured_binding,
    }

    app = _new_app(
        tmp_path,
        probe,
        offline=True,
        compression_model=legacy_compression,
        model_role_bindings=bindings,
    )

    assert events[:5] == [
        "registry:offline:True",
        "registry:ensure:primary",
        "registry:ensure:legacy-compression",
        "registry:ensure:review",
        "registry:ensure:legacy-compression",
    ]
    assert app._model_role_bindings == {
        "reviewer": ScopedModel(review_model, "high"),
        "compression": ScopedModel(legacy_compression, "off"),
    }
    assert app._model_role_bindings["reviewer"] is not review_binding
    assert configured_compression.id not in " ".join(events)


def test_constructor_creates_default_registry_from_agent_paths_before_registration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    registry = _Registry(events)
    settings = _SnakeSettings(events)
    probe = _install_constructor_probes(
        monkeypatch,
        tmp_path,
        registry=registry,
        settings=settings,
    )
    auth_storage = AuthStorage.in_memory()

    def create_auth(path: Path) -> AuthStorage:
        events.append(f"auth:create:{path}")
        return auth_storage

    def create_registry(
        received_auth: AuthStorage,
        path: Path,
        *,
        provider_config: object | None = None,
    ) -> ModelRegistry:
        assert received_auth is auth_storage
        assert provider_config is None
        events.append(f"registry:create:{path}")
        return registry

    monkeypatch.setattr(app_module.AuthStorage, "create", staticmethod(create_auth))
    monkeypatch.setattr(app_module.ModelRegistry, "create", staticmethod(create_registry))

    app = CodingApp(
        cwd=str(tmp_path),
        model=_model("primary"),
        agent_dir=str(tmp_path / "agent"),
        settings_manager=settings,
    )

    agent_dir = (tmp_path / "agent").resolve()
    assert app.model_registry is registry
    assert events[:4] == [
        f"auth:create:{agent_dir / 'auth.json'}",
        f"registry:create:{agent_dir / 'models.json'}",
        "registry:offline:False",
        "registry:ensure:primary",
    ]
    assert probe.operation_owner.closed is False


def test_constructor_uses_snake_settings_retry_and_session_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    settings = _SnakeSettings(events)
    probe = _install_constructor_probes(monkeypatch, tmp_path, settings=settings)

    app = _new_app(tmp_path, probe, thinking_level="high")

    assert "settings:thinking:snake:high" in events
    assert app._retry_settings == (False, 3, 7)
    assert probe.catalog_options == {
        "agent_dir": str((tmp_path / "agent").resolve()),
        "session_dir": "/snake/sessions",
    }
    operation_event = next(event for event in events if event.startswith("operation:init:"))
    assert str(settings.operation_settings) in operation_event


def test_constructor_falls_back_to_camel_settings_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(_CamelSettings, "set_default_thinking_level", None)
    monkeypatch.setattr(_CamelSettings, "get_retry_settings", None)
    monkeypatch.setattr(_CamelSettings, "get_retry_enabled", None)
    monkeypatch.setattr(_CamelSettings, "get_session_dir", None)
    events: list[str] = []
    settings = _CamelSettings(events)
    probe = _install_constructor_probes(monkeypatch, tmp_path, settings=settings)

    app = _new_app(tmp_path, probe, thinking_level="medium")

    assert "settings:thinking:camel:medium" in events
    assert app._retry_settings == (True, 5, 11)
    assert probe.catalog_options["session_dir"] == "/camel/sessions"
    assert not [event for event in events if ":snake" in event]


def test_constructor_does_not_persist_default_thinking_when_level_is_off(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    settings = _SnakeSettings(events)
    probe = _install_constructor_probes(monkeypatch, tmp_path, settings=settings)

    _new_app(tmp_path, probe, thinking_level="off")

    assert not [event for event in events if event.startswith("settings:thinking:")]


def test_constructor_copies_tool_resource_and_scope_inputs_and_configures_process_owners(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    probe = _install_constructor_probes(monkeypatch, tmp_path)
    allowed = ["read", "bash"]
    excluded = ["bash"]
    active = ["mcp"]
    extensions = ["/operator/extension.py"]
    skills = ["/operator/SKILL.md"]
    prompts = ["/operator/prompt.md"]
    themes = ["/operator/theme.json"]
    scoped = [ScopedModel(_model("secondary"), "low")]
    flag_values = {"profile": "security"}
    loader = object.__new__(DefaultResourceLoader)

    app = _new_app(
        tmp_path,
        probe,
        allowed_tool_names=allowed,
        excluded_tool_names=excluded,
        additional_active_tool_names=active,
        additional_extension_paths=extensions,
        additional_skill_paths=skills,
        additional_prompt_template_paths=prompts,
        additional_theme_paths=themes,
        scoped_models=scoped,
        initial_resource_loader=loader,
        extension_flag_values=flag_values,
    )
    allowed.append("write")
    excluded.clear()
    active.clear()
    extensions.clear()
    skills.clear()
    prompts.clear()
    themes.clear()
    scoped.clear()
    flag_values.clear()

    assert app._allowed_tool_names == ["read", "bash"]
    assert app._excluded_tool_names == ["bash"]
    assert app._additional_active_tool_names == ["mcp"]
    assert app._additional_extension_paths == ["/operator/extension.py"]
    assert app._additional_skill_paths == ["/operator/SKILL.md"]
    assert app._additional_prompt_template_paths == ["/operator/prompt.md"]
    assert app._additional_theme_paths == ["/operator/theme.json"]
    assert app._scoped_models == [ScopedModel(_model("secondary"), "low")]
    assert app._initial_resource_loader is loader
    assert app._extension_flag_values == {"profile": "security"}
    expected_store_path = (tmp_path / "agent" / "process-results").resolve()
    assert probe.completion_path == expected_store_path
    assert probe.process_options == {
        "completion_store": probe.completion_store,
        "max_active_per_owner": 4,
        "max_active_total": 16,
    }
    assert app._closed is False


def test_constructor_wires_explicit_terminal_tui_and_default_summarizer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    probe = _install_constructor_probes(monkeypatch, tmp_path)
    terminal = FakeTerminal(columns=144, rows=55)

    app = _new_app(
        tmp_path,
        probe,
        terminal=terminal,
        enable_tui=False,
        summarizer=None,
    )

    assert app.terminal is terminal
    assert probe.tui_terminal is terminal
    assert probe.tui_interval == 0.016
    assert app._enable_tui is False
    assert len(probe.summarizer_calls) == 1
    summarizer_call = probe.summarizer_calls[0]
    assert callable(summarizer_call.model)
    assert summarizer_call.model() is probe.session.model
    assert callable(summarizer_call.thinking_level)
    assert summarizer_call.thinking_level() == "high"
    assert probe.events[-1] == "session-runtime:rebind"
    context = Context(system_prompt="summary-system", messages=[])
    options = SimpleStreamOptions()
    response = summarizer_call.complete_fn(_model("primary"), context, options)
    assert getattr(response, "content", [])[0].text == "summary response"


def test_constructor_uses_default_terminal_and_preserves_custom_summarizer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    probe = _install_constructor_probes(monkeypatch, tmp_path)

    def custom_summarizer(prompt: str) -> str:
        return f"custom:{prompt}"

    app = _new_app(tmp_path, probe, summarizer=custom_summarizer)

    assert app.terminal is probe.default_terminal
    assert probe.tui_terminal is probe.default_terminal
    assert app._summarizer is custom_summarizer
    assert probe.summarizer_calls == []


def test_constructor_closes_operation_runtime_when_initial_session_creation_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    failure = RuntimeError("initial session failed")
    probe = _install_constructor_probes(
        monkeypatch,
        tmp_path,
        session_error=failure,
    )

    with pytest.raises(RuntimeError, match="initial session failed") as captured:
        _new_app(tmp_path, probe)

    assert captured.value is failure
    assert probe.operation_owner.closed is True
    assert probe.events[-2:] == ["session:create", "operation:close"]
    assert "session:bind" not in probe.events
    assert "session-runtime:init" not in probe.events


def test_constructor_binds_session_before_runtime_and_installs_hooks_in_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    probe = _install_constructor_probes(monkeypatch, tmp_path)
    session_path = str(tmp_path / "session.jsonl")

    app = _new_app(
        tmp_path,
        probe,
        thinking_level="low",
        session_path=session_path,
        session_id="session-id",
    )

    lifecycle = [
        event
        for event in probe.events
        if event.startswith(("operation:", "session:", "session-runtime:"))
    ]
    assert lifecycle == [
        f"operation:init:{(tmp_path / 'agent').resolve()}:{probe.operation_options}",
        "session:create",
        "session:bind",
        "session-runtime:init",
        "session-runtime:before-invalidate",
        "session-runtime:rebind",
    ]
    assert probe.create_session_kwargs == {
        "cwd": str(tmp_path.resolve()),
        "fallback_model": _model("primary"),
        "thinking_level": "low",
        "session_path": session_path,
        "session_id": "session-id",
    }
    assert app.session is probe.session
    assert app.operation_runtime is probe.operation_owner
    assert probe.runtime is not None
    assert probe.runtime.session is probe.session
    assert probe.runtime.services == {
        "cwd": str(tmp_path.resolve()),
        "agentDir": str((tmp_path / "agent").resolve()),
        "sessionCatalog": app.session_catalog,
    }
    assert probe.runtime.create_runtime == app._create_runtime_session
    assert probe.runtime.before_invalidate == app._unbind_session
    assert probe.runtime.rebind == app._handle_session_rebound
