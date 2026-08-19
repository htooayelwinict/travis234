from __future__ import annotations

from tests._support_tui import CodingApp, FakeTerminal, faux_model
from travis.tui.interactive_contracts import INTERACTIVE_MODE_PUBLIC_MEMBERS
from travis.tui.interactive_mode import InteractiveMode


def test_interactive_mode_composes_bounded_runtime_owners(tmp_path) -> None:
    from travis.tui.interactive_command_dispatcher import InteractiveCommandDispatcher
    from travis.tui.interactive_extensions import InteractiveExtensions
    from travis.tui.interactive_model_auth import InteractiveModelAuth
    from travis.tui.interactive_memory import InteractiveMemory
    from travis.tui.interactive_operations import InteractiveOperations
    from travis.tui.interactive_process_commands import InteractiveProcessCommands
    from travis.tui.interactive_subagents import InteractiveSubagents
    from travis.tui.interactive_session_commands import InteractiveSessionCommands
    from travis.tui.interactive_shutdown import InteractiveShutdown
    from travis.tui.interactive_turn_controller import InteractiveTurnController
    from travis.tui.interactive_tool_approval import InteractiveToolApprovalBroker
    from travis.tui.interactive_view import InteractiveView

    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    mode = InteractiveMode(app)
    runtime = mode._runtime

    assert isinstance(runtime, InteractiveCommandDispatcher)
    assert isinstance(runtime, InteractiveExtensions)
    assert isinstance(runtime, InteractiveModelAuth)
    assert isinstance(runtime, InteractiveMemory)
    assert isinstance(runtime, InteractiveOperations)
    assert isinstance(runtime, InteractiveProcessCommands)
    assert isinstance(runtime, InteractiveSubagents)
    assert isinstance(runtime, InteractiveSessionCommands)
    assert isinstance(runtime, InteractiveShutdown)
    assert isinstance(runtime, InteractiveTurnController)
    assert isinstance(runtime, InteractiveView)
    assert isinstance(runtime.tool_approval_broker, InteractiveToolApprovalBroker)


def test_interactive_mode_forwards_runtime_overrides(tmp_path) -> None:
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    mode = InteractiveMode(app)

    mode._shutdown_requested = True
    mode._show_status = lambda message, kind="info": (message, kind)

    assert mode._runtime._shutdown_requested is True
    assert mode._show_status("ready") == ("ready", "info")


def test_interactive_mode_records_extension_compatibility_surface() -> None:
    assert {
        "add_autocomplete_provider",
        "add_terminal_input_listener",
        "prompt_extension_confirm",
        "prompt_extension_custom",
        "prompt_extension_editor",
        "prompt_extension_input",
        "prompt_extension_select",
        "set_editor_text",
        "set_extension_footer",
        "set_extension_header",
        "set_extension_status",
        "set_extension_widget",
        "set_hidden_thinking_label",
        "set_terminal_title",
        "set_working_indicator",
        "set_working_message",
        "set_working_visible",
    } <= INTERACTIVE_MODE_PUBLIC_MEMBERS
