"""Explicit construction of interactive controller dependencies."""

from __future__ import annotations

from typing import cast

from travis.controller_ports import ControllerBindingRegistry
from travis.tui.interactive_command_dispatcher import InteractiveCommandDispatcher
from travis.tui.interactive_controllers import (
    INTERACTIVE_CONTROLLER_PORT_ATTRIBUTES,
    InteractiveControllers,
    bind_interactive_controller_owners,
)
from travis.tui.interactive_extensions import InteractiveExtensions
from travis.tui.interactive_lsp import InteractiveLsp
from travis.tui.interactive_memory import InteractiveMemory
from travis.tui.interactive_model_auth import InteractiveModelAuth
from travis.tui.interactive_motion import InteractiveMotion
from travis.tui.interactive_operations import InteractiveOperations
from travis.tui.interactive_params import InteractiveParams
from travis.tui.interactive_process_commands import InteractiveProcessCommands
from travis.tui.interactive_services import (
    InteractiveCommandDependencies,
    InteractiveCommandPort,
    InteractiveCommandPortAdapter,
    InteractiveHistoryPort,
    InteractiveMotionDependencies,
    InteractiveMotionPort,
    InteractiveOwnerThreadPort,
    InteractiveRenderPort,
    InteractiveServices,
    InteractiveSessionBindingPort,
    InteractiveStatusPort,
    InteractiveTerminalInputPort,
    InteractiveThemePort,
    InteractiveViewDependencies,
    InteractiveViewPort,
)
from travis.tui.interactive_session_commands import InteractiveSessionCommands
from travis.tui.interactive_shutdown import InteractiveShutdown
from travis.tui.interactive_state import InteractiveLifecycleState, InteractiveState
from travis.tui.interactive_subagents import InteractiveSubagents
from travis.tui.interactive_turn_controller import InteractiveTurnController
from travis.tui.interactive_view import InteractiveView


def compose_interactive_controllers(
    registry: ControllerBindingRegistry,
    *,
    app: object,
    tui: object,
    history: object,
    status: object,
    theme: object,
    state: InteractiveState,
    lifecycle: InteractiveLifecycleState,
) -> tuple[InteractiveServices, InteractiveControllers]:
    services = InteractiveServices(
        render=cast(InteractiveRenderPort, tui),
        status=cast(InteractiveStatusPort, status),
        history=cast(InteractiveHistoryPort, history),
        sessions=cast(InteractiveSessionBindingPort, app),
        owner_thread=cast(InteractiveOwnerThreadPort, getattr(tui, "dispatcher")),
        terminal_input=cast(InteractiveTerminalInputPort, tui),
        theme=cast(InteractiveThemePort, theme),
    )
    registry.bind_attribute("tui", services, "render")
    registry.bind_attribute("status", services, "status")
    registry.bind_attribute("history", services, "history")
    registry.bind_attribute("theme_context", services, "theme")
    def dependencies(name: str) -> InteractiveCommandDependencies:
        return InteractiveCommandDependencies(
            InteractiveCommandPortAdapter(
                registry.port(INTERACTIVE_CONTROLLER_PORT_ATTRIBUTES[name])
            ),
            state,
            lifecycle,
            services,
        )

    controllers = InteractiveControllers(
        command_dispatch=InteractiveCommandDispatcher(dependencies("command_dispatch")),
        view=InteractiveView(
            InteractiveViewDependencies(
                cast(
                    InteractiveViewPort,
                    registry.port(INTERACTIVE_CONTROLLER_PORT_ATTRIBUTES["view"]),
                ),
                state,
                services,
            )
        ),
        model_auth=InteractiveModelAuth(dependencies("model_auth")),
        params=InteractiveParams(dependencies("params")),
        processes=InteractiveProcessCommands(dependencies("processes")),
        lsp=InteractiveLsp(dependencies("lsp")),
        memory=InteractiveMemory(dependencies("memory")),
        operations=InteractiveOperations(dependencies("operations")),
        subagents=InteractiveSubagents(dependencies("subagents")),
        sessions=InteractiveSessionCommands(dependencies("sessions")),
        extensions=InteractiveExtensions(dependencies("extensions")),
        turns=InteractiveTurnController(dependencies("turns")),
        shutdown=InteractiveShutdown(dependencies("shutdown")),
        motion=InteractiveMotion(
            InteractiveMotionDependencies(
                cast(
                    InteractiveMotionPort,
                    registry.port(INTERACTIVE_CONTROLLER_PORT_ATTRIBUTES["motion"]),
                ),
                state,
                services,
            )
        ),
    )
    bind_interactive_controller_owners(registry, controllers)
    return services, controllers


__all__ = ("compose_interactive_controllers",)
