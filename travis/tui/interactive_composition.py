"""Explicit construction of interactive controller dependencies."""

from __future__ import annotations

from typing import Protocol, cast

from travis.controller_ports import ControllerBinding, compose_controller_dependencies
from travis.tui.interactive_command_dispatcher import InteractiveCommandDispatcher
from travis.tui.interactive_controllers import (
    InteractiveControllers,
    bind_interactive_controller_owners,
)
from travis.tui.interactive_dependencies import (
    InteractiveCommandDispatchDependencies,
    InteractiveExtensionDependencies,
    InteractiveLspDependencies,
    InteractiveMemoryDependencies,
    InteractiveModelAuthDependencies,
    InteractiveMotionDependencies,
    InteractiveOperationsDependencies,
    InteractiveParamsDependencies,
    InteractiveProcessDependencies,
    InteractiveRuntimeBindings,
    InteractiveSessionDependencies,
    InteractiveShutdownDependencies,
    InteractiveSubagentDependencies,
    InteractiveTurnDependencies,
    InteractiveViewDependencies,
)
from travis.tui.interactive_extensions import InteractiveExtensions
from travis.tui.interactive_lsp import InteractiveLsp
from travis.tui.interactive_memory import InteractiveMemory
from travis.tui.interactive_model_auth import InteractiveModelAuth
from travis.tui.interactive_motion import InteractiveMotion
from travis.tui.interactive_operations import InteractiveOperations
from travis.tui.interactive_params import InteractiveParams
from travis.tui.interactive_process_commands import InteractiveProcessCommands
from travis.tui.interactive_rebind import (
    build_params_session,
    build_process_session,
    build_view_session,
)
from travis.tui.interactive_services import (
    InteractiveAppAdapter,
    InteractiveHistoryPort,
    InteractiveOwnerThreadPort,
    InteractiveRenderPort,
    InteractiveServices,
    InteractiveSessionBindingPort,
    InteractiveStatusPort,
    InteractiveTerminalInputPort,
    InteractiveThemePort,
)
from travis.tui.interactive_session_commands import InteractiveSessionCommands
from travis.tui.interactive_shutdown import InteractiveShutdown
from travis.tui.interactive_state import InteractiveLifecycleState, InteractiveState
from travis.tui.interactive_subagents import InteractiveSubagents
from travis.tui.interactive_turn_controller import InteractiveTurnController
from travis.tui.interactive_view import InteractiveView


class _InteractiveTuiCompositionPort(Protocol):
    @property
    def dispatcher(self) -> InteractiveOwnerThreadPort: ...


def compose_interactive_controllers(
    bindings: InteractiveRuntimeBindings,
    *,
    app: InteractiveSessionBindingPort,
    tui: _InteractiveTuiCompositionPort,
    history: object,
    status: object,
    theme: object,
    state: InteractiveState,
    lifecycle: InteractiveLifecycleState,
) -> tuple[InteractiveServices, InteractiveControllers]:
    app_port = InteractiveAppAdapter(app)
    services = InteractiveServices(
        render=cast(InteractiveRenderPort, tui),
        status=cast(InteractiveStatusPort, status),
        history=cast(InteractiveHistoryPort, history),
        sessions=cast(InteractiveSessionBindingPort, app_port),
        owner_thread=cast(InteractiveOwnerThreadPort, tui.dispatcher),
        terminal_input=cast(InteractiveTerminalInputPort, tui),
        theme=cast(InteractiveThemePort, theme),
    )
    bindings.tui.bind_attribute(services, "render")
    bindings.status.bind_attribute(services, "status")
    bindings.history.bind_attribute(services, "history")
    bindings.theme_context.bind_attribute(services, "theme")
    session = app.session

    def command_dependencies[DependenciesT](
        dependency_type: type[DependenciesT],
        *,
        session_binding: ControllerBinding[object] | None = None,
    ) -> DependenciesT:
        explicit: dict[str, object] = {
            "app": ControllerBinding(app_port),
            "state": state,
            "lifecycle": lifecycle,
            "services": services,
        }
        if session_binding is not None:
            explicit["session"] = session_binding
        return compose_controller_dependencies(
            dependency_type,
            bindings,
            **explicit,
        )

    controllers = InteractiveControllers(
        command_dispatch=InteractiveCommandDispatcher(
            command_dependencies(InteractiveCommandDispatchDependencies)
        ),
        view=InteractiveView(
            compose_controller_dependencies(
                InteractiveViewDependencies,
                bindings,
                app=ControllerBinding(app_port),
                session=ControllerBinding(session, coerce=build_view_session),
                state=state,
                services=services,
            )
        ),
        model_auth=InteractiveModelAuth(
            command_dependencies(InteractiveModelAuthDependencies)
        ),
        params=InteractiveParams(
            command_dependencies(
                InteractiveParamsDependencies,
                session_binding=ControllerBinding(session, coerce=build_params_session),
            )
        ),
        processes=InteractiveProcessCommands(
            command_dependencies(
                InteractiveProcessDependencies,
                session_binding=ControllerBinding(session, coerce=build_process_session),
            )
        ),
        lsp=InteractiveLsp(command_dependencies(InteractiveLspDependencies)),
        memory=InteractiveMemory(command_dependencies(InteractiveMemoryDependencies)),
        operations=InteractiveOperations(
            command_dependencies(InteractiveOperationsDependencies)
        ),
        subagents=InteractiveSubagents(
            command_dependencies(InteractiveSubagentDependencies)
        ),
        sessions=InteractiveSessionCommands(
            command_dependencies(InteractiveSessionDependencies)
        ),
        extensions=InteractiveExtensions(
            command_dependencies(InteractiveExtensionDependencies)
        ),
        turns=InteractiveTurnController(
            command_dependencies(InteractiveTurnDependencies)
        ),
        shutdown=InteractiveShutdown(
            command_dependencies(InteractiveShutdownDependencies)
        ),
        motion=InteractiveMotion(
            compose_controller_dependencies(
                InteractiveMotionDependencies,
                bindings,
                state=state,
                services=services,
            )
        ),
    )
    bind_interactive_controller_owners(bindings, controllers)
    return services, controllers


__all__ = ("compose_interactive_controllers",)
