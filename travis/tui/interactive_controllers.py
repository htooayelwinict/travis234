"""Typed ownership bundle for interactive collaborators."""

from __future__ import annotations

from dataclasses import dataclass

from travis.tui.interactive_command_dispatcher import InteractiveCommandDispatcher
from travis.tui.interactive_extensions import InteractiveExtensions
from travis.tui.interactive_lsp import InteractiveLsp
from travis.tui.interactive_memory import InteractiveMemory
from travis.tui.interactive_model_auth import InteractiveModelAuth
from travis.tui.interactive_motion import InteractiveMotion
from travis.tui.interactive_operations import InteractiveOperations
from travis.tui.interactive_params import InteractiveParams
from travis.tui.interactive_process_commands import InteractiveProcessCommands
from travis.tui.interactive_session_commands import InteractiveSessionCommands
from travis.tui.interactive_shutdown import InteractiveShutdown
from travis.tui.interactive_subagents import InteractiveSubagents
from travis.tui.interactive_turn_controller import InteractiveTurnController
from travis.tui.interactive_view import InteractiveView


@dataclass(frozen=True, slots=True)
class InteractiveControllers:
    command_dispatch: InteractiveCommandDispatcher
    view: InteractiveView
    model_auth: InteractiveModelAuth
    params: InteractiveParams
    processes: InteractiveProcessCommands
    lsp: InteractiveLsp
    memory: InteractiveMemory
    operations: InteractiveOperations
    subagents: InteractiveSubagents
    sessions: InteractiveSessionCommands
    extensions: InteractiveExtensions
    turns: InteractiveTurnController
    shutdown: InteractiveShutdown
    motion: InteractiveMotion


__all__ = ["InteractiveControllers"]
