from __future__ import annotations

from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static

from appv22_ui.tui_state import ConversationLine
from appv22_ui.textual_controller import TextualTuiController


class AppV22TextualApp(App):
    CSS = """
    Screen { layout: vertical; }
    #main { height: 1fr; }
    #conversation { width: 55%; border: solid $accent; }
    #side { width: 45%; }
    #events { height: 2fr; border: solid $accent; }
    #context { height: 1fr; border: solid $accent; }
    #notice { height: 1; color: $text-muted; }
    Input { dock: bottom; }
    """
    BINDINGS = [
        ("ctrl+c", "cancel_or_quit", "Cancel/Quit"),
        ("ctrl+l", "clear_events", "Clear"),
        ("up", "history_previous", "Previous prompt"),
        ("down", "history_next", "Next prompt"),
    ]

    def __init__(self, controller: TextualTuiController) -> None:
        super().__init__()
        self.controller = controller

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            yield RichLog(id="conversation", wrap=True, highlight=False)
            with Vertical(id="side"):
                yield RichLog(id="events", wrap=True, highlight=False)
                yield RichLog(id="context", wrap=True, highlight=False)
        yield Static("", id="notice")
        yield Input(placeholder="Message AppV22. Commands: /status /context /refs /reset-ui /exit", id="input")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_all()
        self.query_one("#input", Input).focus()

    @on(Input.Submitted, "#input")
    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        accepted = self.controller.accept_user_prompt(text)
        if accepted is None:
            self._refresh_all()
            return
        if accepted in {"/exit", "/quit"}:
            self.exit()
            return
        if accepted.startswith("/"):
            self.controller.handle_command(accepted)
            self._refresh_all()
            return
        self._refresh_notice("agent running")

        def on_event(_event) -> None:
            self.call_from_thread(self._refresh_events)
            self.call_from_thread(self._refresh_context)

        def on_done() -> None:
            self.call_from_thread(self._refresh_all)

        def on_error(_exc: BaseException) -> None:
            self.call_from_thread(self._refresh_all)

        self.controller.start_turn(accepted, on_event=on_event, on_done=on_done, on_error=on_error)

    def action_history_previous(self) -> None:
        self.query_one("#input", Input).value = self.controller.previous_history()

    def action_history_next(self) -> None:
        self.query_one("#input", Input).value = self.controller.next_history()

    def action_clear_events(self) -> None:
        self.controller.state.clear_transient()
        self._refresh_all()

    def action_cancel_or_quit(self) -> None:
        if self.controller.state.running:
            self.controller.state.running = False
            self.controller.state.mode = "INTERRUPTED"
            self.controller.state.add_notice("UI interrupted; provider call may finish in background")
            self._refresh_all()
            return
        self.exit()

    def _refresh_all(self) -> None:
        self._refresh_conversation()
        self._refresh_events()
        self._refresh_context()
        self._refresh_notice()

    def _refresh_conversation(self) -> None:
        log = self.query_one("#conversation", RichLog)
        log.clear()
        log.write("[bold]Conversation[/bold]")
        for line in self.controller.state.conversation[-80:]:
            log.write(_conversation_line(line))

    def _refresh_events(self) -> None:
        log = self.query_one("#events", RichLog)
        log.clear()
        log.write("[bold]Pi Agent Loop[/bold]")
        for index, event in enumerate(self.controller.state.events[-80:], start=1):
            detail = f" :: {event.detail}" if event.detail else ""
            log.write(f"{index:02d} {event.title}{detail}")

    def _refresh_context(self) -> None:
        log = self.query_one("#context", RichLog)
        log.clear()
        state = self.controller.state
        log.write("[bold]Hermes Context[/bold]")
        log.write(f"status={state.status} mode={state.mode} refs={state.world_ref_count}")
        if state.conversation_summary:
            log.write(f"ui summary chars={len(state.conversation_summary)}")
        if state.ui_context_metrics:
            log.write(f"ui metrics={state.ui_context_metrics}")
        risks = state.context_summary.get("open_risks")
        if isinstance(risks, list) and risks:
            log.write(f"open risks={len(risks)}")
            for risk in risks[-4:]:
                log.write(f"- {risk}")

    def _refresh_notice(self, override: str | None = None) -> None:
        self.query_one("#notice", Static).update(override or self.controller.state.notice)


def _conversation_line(line: ConversationLine) -> str:
    role = "you" if line.role == "user" else line.role
    return f"[bold]{role}:[/bold] {line.text}"
