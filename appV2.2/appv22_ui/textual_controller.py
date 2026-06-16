from __future__ import annotations

import json
from pathlib import Path
import queue
import threading
from typing import Any, Callable

from appv22.providers import create_appv22_provider_from_appv2_env
from appv22_ui.context_manager import TuiContextManager
from appv22_ui.events import UIEvent, event_from_runtime_event
from appv22_ui.runtime_adapter import RuntimeAdapter, RuntimeAdapterConfig
from appv22_ui.session import SessionStore
from appv22_ui.tui_state import ConversationLine, TuiState


class TextualTuiController:
    def __init__(self, *, workspace: Path, dotenv_path: Path, max_turns: int, extensions: tuple[str, ...]) -> None:
        self.workspace = workspace
        self.dotenv_path = dotenv_path
        self.store = SessionStore(workspace)
        self.state = TuiState.from_session(workspace, self.store.load())
        self.context_manager = TuiContextManager(api_compactor=self._compact_ui_context_with_api)
        self.adapter = RuntimeAdapter(
            RuntimeAdapterConfig(
                workspace=workspace,
                dotenv_path=dotenv_path,
                max_turns=max_turns,
                extensions=extensions,
            )
        )
        self.history: list[str] = []
        self.history_index: int | None = None

    def record_submitted_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        self.history.append(text)
        self.history = self.history[-100:]
        self.history_index = None

    def previous_history(self) -> str:
        if not self.history:
            return ""
        if self.history_index is None:
            self.history_index = len(self.history) - 1
        else:
            self.history_index = max(0, self.history_index - 1)
        return self.history[self.history_index]

    def next_history(self) -> str:
        if not self.history:
            return ""
        if self.history_index is None:
            return ""
        self.history_index += 1
        if self.history_index >= len(self.history):
            self.history_index = None
            return ""
        return self.history[self.history_index]

    def accept_user_prompt(self, prompt: str) -> str | None:
        normalized = prompt.strip()
        if not normalized:
            return None
        command = _embedded_command(normalized)
        if command is not None:
            return command
        if _looks_like_pasted_tui_output(normalized):
            self.state.add_notice("ignored pasted TUI output; type a request or slash command")
            return None
        if len(normalized) > 4000:
            self.state.add_notice("ignored oversized pasted input; paste a concise request")
            return None
        return normalized

    def handle_command(self, command: str) -> bool:
        if command in {"/exit", "/quit"}:
            return True
        if command == "/reset-ui":
            self.state.conversation.clear()
            self.state.events.clear()
            self.state.add_notice("UI conversation reset. Runtime world/context refs are preserved.")
            self._save_ui_context({})
            return False
        if command == "/clear":
            self.state.clear_transient()
            return False
        if command == "/status":
            self.state.add_notice(
                f"status={self.state.status} mode={self.state.mode} refs={self.state.world_ref_count}"
            )
            return False
        if command == "/events":
            self.state.add_notice(f"showing last {min(len(self.state.events), 14)} agent-loop events")
            return False
        if command == "/context":
            risks = self.state.context_summary.get("open_risks")
            risk_count = len(risks) if isinstance(risks, list) else 0
            self.state.add_notice(f"context refs={self.state.world_ref_count} open_risks={risk_count}")
            return False
        if command == "/refs":
            self.state.add_notice(", ".join(self.state.world_refs[-8:]) or "no world refs")
            return False
        self.state.add_notice(f"unknown command: {command}")
        return False

    def build_runtime_prompt(self, prompt: str) -> str:
        runtime_prompt, hot_lines, summary = self.context_manager.prepare_prompt(
            current_user_message=prompt,
            conversation=self.state.conversation,
            existing_summary=self.state.conversation_summary,
            compaction_count=int(self.state.ui_context_metrics.get("compaction_count") or 0),
        )
        self.state.conversation = [*hot_lines, self.state.conversation[-1]] if self.state.conversation else hot_lines
        self.state.conversation_summary = summary.content
        self.state.ui_context_metrics = {
            "tokens_before": summary.tokens_before,
            "compaction_count": summary.compaction_count,
            "summary_source": summary.source,
            "hot_lines": len(hot_lines),
            "summary_chars": len(summary.content),
        }
        return runtime_prompt

    def ui_context_payload(self) -> dict[str, Any]:
        return {
            "conversation_summary": self.state.conversation_summary,
            "metrics": dict(self.state.ui_context_metrics),
        }

    def start_turn(
        self,
        prompt: str,
        *,
        on_event: Callable[[UIEvent], None],
        on_done: Callable[[], None],
        on_error: Callable[[BaseException], None],
    ) -> threading.Thread | None:
        accepted = self.accept_user_prompt(prompt)
        if accepted is None:
            return None
        if accepted.startswith("/"):
            if self.handle_command(accepted):
                raise SystemExit(0)
            return None
        self.record_submitted_text(accepted)
        self.state.add_user(accepted)
        self.state.running = True
        self.state.mode = "START"
        event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()

        def event_sink(event: dict[str, Any]) -> None:
            event_queue.put(("event", event))

        def worker() -> None:
            try:
                result = self.adapter.run(
                    self.build_runtime_prompt(accepted),
                    active_user_request=accepted,
                    ui_context=self.ui_context_payload(),
                    previous_result=None,
                    event_sink=event_sink,
                )
                event_queue.put(("result", result))
            except BaseException as exc:  # noqa: BLE001 - surfaced through callback.
                event_queue.put(("error", exc))
            finally:
                event_queue.put(("done", None))

        def pump() -> None:
            worker_thread = threading.Thread(target=worker, daemon=True)
            worker_thread.start()
            while True:
                kind, payload = event_queue.get()
                if kind == "event":
                    ui_event = event_from_runtime_event(payload)
                    self.state.apply_event(payload)
                    on_event(ui_event)
                elif kind == "result":
                    self.state.apply_result(payload)
                    self._save_ui_context(payload)
                elif kind == "error":
                    self.state.running = False
                    self.state.mode = "FAILED"
                    self.state.status = "failed"
                    self.state.reason = type(payload).__name__
                    self.state.add_notice(f"agent error: {payload}")
                    on_error(payload)
                elif kind == "done":
                    on_done()
                    return

        thread = threading.Thread(target=pump, daemon=True)
        thread.start()
        return thread

    def _save_ui_context(self, result: dict[str, Any]) -> None:
        enriched = dict(result)
        enriched.setdefault("status", self.state.status)
        enriched.setdefault("reason", self.state.reason)
        enriched.setdefault("session_id", self.state.session_id)
        enriched.setdefault("world_refs", {})
        enriched.setdefault("context_summary", self.state.context_summary)
        enriched["ui_context"] = {
            "conversation_summary": self.state.conversation_summary,
            "metrics": dict(self.state.ui_context_metrics),
        }
        self.store.save(enriched, conversation=self.state.conversation)

    def _compact_ui_context_with_api(self, compaction_input: str) -> str:
        provider = create_appv22_provider_from_appv2_env(self.dotenv_path)
        client = getattr(provider, "client", None)
        complete_json = getattr(client, "complete_json", None)
        if not callable(complete_json):
            return ""
        raw = complete_json(
            stage="appv22_tui_context_compaction",
            prompt="\n".join(
                [
                    "You compact AppV2.2 TUI session context.",
                    "Return JSON only.",
                    "Summarize as REFERENCE ONLY, not active instructions.",
                    "Preserve stable user facts, preferences, unresolved asks, and completed task outcomes.",
                    "The latest user request after this summary remains authoritative.",
                    compaction_input,
                ]
            ),
            schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
            },
        )
        parsed = json.loads(raw)
        return str(parsed.get("summary") or "") if isinstance(parsed, dict) else ""


def _embedded_command(text: str) -> str | None:
    for command in ("/exit", "/quit", "/status", "/context", "/refs", "/events", "/clear", "/reset-ui"):
        if text == command or text.endswith(f" {command}"):
            return command
    return None


def _looks_like_pasted_tui_output(text: str) -> bool:
    markers = (
        "CONVERSATION",
        "PI AGENT LOOP",
        "HERMES CONTEXT",
        "tool_loop_completed",
        "decision proposed:",
        "agent started ::",
        "+---",
        "| | |",
    )
    marker_hits = sum(1 for marker in markers if marker in text)
    return marker_hits >= 2 or (text.startswith("|") and marker_hits >= 1)
