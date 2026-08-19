"""Transactional, narrow session bindings for interactive controllers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from travis.coding_agent.artifacts import ArtifactRegistry
from travis.coding_agent.extensions import ExtensionRunner
from travis.coding_agent.session_bash import SessionBashController
from travis.coding_agent.session_controllers import SessionControllers
from travis.coding_agent.session_events import SessionEventController
from travis.coding_agent.session_extensions import SessionExtensionController
from travis.coding_agent.session_generation_params import SessionGenerationParams
from travis.coding_agent.session_models import SessionModelController
from travis.coding_agent.session_persistence import SessionPersistence
from travis.coding_agent.session_ports import SessionRuntimeBindings
from travis.coding_agent.session_tooling import SessionToolController
from travis.coding_agent.session_types import BashResult
from travis.controller_ports import ControllerBinding
from travis.runtime_facade import RuntimeFacade
from travis.tui.user_commands import UserCommandExtensionPort


def _controllers_from_session(session: object) -> SessionControllers:
    try:
        runtime = object.__getattribute__(session, "_runtime")
        controllers = object.__getattribute__(runtime, "controllers")
    except AttributeError as error:
        raise TypeError("interactive session binding requires an AgentSession facade") from error
    if not isinstance(controllers, SessionControllers):
        raise TypeError("interactive session binding received an incompatible controller graph")
    return controllers


def _structural_compatibility_session(session: object) -> object:
    if isinstance(session, RuntimeFacade):
        raise TypeError("interactive session binding requires a composed session graph")
    return session


@dataclass(frozen=True, slots=True)
class InteractiveViewSession:
    """Session surface consumed by rendering and footer updates."""

    events: SessionEventController
    models: SessionModelController
    extensions: SessionExtensionController
    persistence: SessionPersistence
    context_usage: ControllerBinding[object]

    def subscribe(self, listener: Callable[[object], None]) -> Callable[[], None]:
        return self.events.subscribe(listener)

    def bind_extensions(self, bindings: dict[str, object]) -> None:
        self.extensions.bind_extensions(bindings)

    @property
    def model(self) -> object:
        return self.models.model

    @property
    def thinking_level(self) -> str:
        return self.models.thinking_level

    @property
    def session_name(self) -> str | None:
        return self.models.session_name

    @property
    def messages(self) -> object:
        return self.models.messages

    @property
    def agent(self) -> object:
        return self.models.agent

    @property
    def extension_runner(self) -> ExtensionRunner:
        return self.models.extension_runner

    @property
    def prompt_templates(self) -> object:
        return self.models.prompt_templates

    @property
    def session_id(self) -> str:
        return self.persistence.session_id

    @property
    def session_path(self) -> str | None:
        return self.persistence.session_path

    def get_context_usage(self) -> object:
        callback = self.context_usage.get()
        if not callable(callback):
            raise TypeError("session context-usage dependency is not callable")
        return callback()


@dataclass(frozen=True, slots=True)
class InteractiveParamsSession:
    """Session surface consumed by generation-parameter commands."""

    models: SessionModelController
    generation: SessionGenerationParams
    tools: SessionToolController

    @property
    def generation_param_overrides(self) -> object:
        return self.generation.generation_param_overrides

    @property
    def model(self) -> object:
        return self.models.model

    @property
    def model_registry(self) -> object:
        return self.models.model_registry

    @property
    def thinking_level(self) -> str:
        return self.models.thinking_level

    def get_active_tool_names(self) -> list[str]:
        return self.tools.get_active_tool_names()

    def set_thinking_level(self, level: str) -> None:
        self.models.set_thinking_level(level)

    def set_generation_param_override(self, name: str, value: object) -> object:
        return self.generation.set_generation_param_override(name, value)

    def reset_generation_param_override(self, name: str) -> object:
        return self.generation.reset_generation_param_override(name)

    def reset_generation_param_overrides(self) -> object:
        return self.generation.reset_generation_param_overrides()


@dataclass(frozen=True, slots=True)
class InteractiveProcessSession:
    """Session surface consumed by process display and user-command binding."""

    events: SessionEventController
    persistence: SessionPersistence
    models: SessionModelController
    bash: SessionBashController
    tools: SessionToolController

    def subscribe(self, listener: Callable[[object], None]) -> Callable[[], None]:
        return self.events.subscribe(listener)

    @property
    def session_id(self) -> str:
        return self.persistence.session_id

    @property
    def session_path(self) -> str | None:
        return self.persistence.session_path

    @property
    def extension_runner(self) -> UserCommandExtensionPort:
        return self.models.extension_runner

    @property
    def cwd(self) -> str:
        return self.bash.cwd

    @property
    def _artifacts(self) -> ArtifactRegistry:
        return self.bash._artifacts

    def _settings_shell_command_prefix(self) -> str | None:
        return self.tools._settings_shell_command_prefix()

    def _settings_shell_path(self) -> str | None:
        return self.tools._settings_shell_path()

    def record_bash_result(
        self,
        command: str,
        result: BashResult,
        options: dict[str, object] | None = None,
    ) -> None:
        self.bash.record_bash_result(command, result, options)


def _session_bindings(session: object) -> SessionRuntimeBindings:
    runtime = object.__getattribute__(session, "_runtime")
    bindings = object.__getattribute__(runtime, "_session_bindings")
    if not isinstance(bindings, SessionRuntimeBindings):
        raise TypeError("interactive session binding received incompatible session bindings")
    return bindings


def build_view_session(session: object) -> object:
    if isinstance(session, InteractiveViewSession):
        return session
    try:
        controllers = _controllers_from_session(session)
    except TypeError:
        return _structural_compatibility_session(session)
    return InteractiveViewSession(
        events=controllers.events,
        models=controllers.models,
        extensions=controllers.extensions,
        persistence=controllers.persistence,
        context_usage=_session_bindings(session).get_context_usage,
    )


def build_params_session(session: object) -> object:
    if isinstance(session, InteractiveParamsSession):
        return session
    try:
        controllers = _controllers_from_session(session)
    except TypeError:
        return _structural_compatibility_session(session)
    return InteractiveParamsSession(
        models=controllers.models,
        generation=controllers.generation,
        tools=controllers.tools,
    )


def build_process_session(session: object) -> object:
    if isinstance(session, InteractiveProcessSession):
        return session
    try:
        controllers = _controllers_from_session(session)
    except TypeError:
        return _structural_compatibility_session(session)
    return InteractiveProcessSession(
        events=controllers.events,
        persistence=controllers.persistence,
        models=controllers.models,
        bash=controllers.bash,
        tools=controllers.tools,
    )


def rebind_cached_session(controller: object, session: object) -> object:
    dependencies = object.__getattribute__(controller, "dependencies")
    binding = dependencies.session
    if not isinstance(binding, ControllerBinding):
        raise TypeError("interactive controller session dependency is not rebindable")
    return binding.swap(session)


__all__ = (
    "InteractiveParamsSession",
    "InteractiveProcessSession",
    "InteractiveViewSession",
    "build_params_session",
    "build_process_session",
    "build_view_session",
    "rebind_cached_session",
)
