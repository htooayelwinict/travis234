from __future__ import annotations

import argparse
import json
from pathlib import Path
import queue
import sys
import threading
import time
from typing import Any

from appv22_ui.context_manager import TuiContextManager
from appv22.providers import create_appv22_provider_from_appv2_env
from appv22_ui.runtime_adapter import RuntimeAdapter, RuntimeAdapterConfig
from appv22_ui.session import SessionStore
from appv22_ui.tui_layout import render_tui
from appv22_ui.tui_state import TuiState


class AppV22Tui:
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

    def run(self) -> int:
        self._draw()
        while True:
            try:
                prompt = input("appv22> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nexiting")
                return 0
            if not prompt:
                self._draw()
                continue
            accepted = self._accept_user_prompt(prompt)
            if accepted is None:
                self._draw()
                continue
            prompt = accepted
            if prompt.startswith("/"):
                should_exit = self._command(prompt)
                self._draw()
                if should_exit:
                    return 0
                continue
            self._run_agent_turn(prompt)

    def _command(self, command: str) -> bool:
        if command in {"/exit", "/quit"}:
            return True
        if command == "/reset-ui":
            self.state.conversation.clear()
            self.state.events.clear()
            self.state.add_notice("UI conversation reset. Runtime world/context refs are preserved.")
            preserved = self._previous_result() or {}
            preserved.setdefault("status", self.state.status)
            preserved.setdefault("reason", self.state.reason)
            preserved.setdefault("session_id", self.state.session_id)
            preserved.setdefault("world_refs", {})
            preserved.setdefault("context_summary", self.state.context_summary)
            preserved["ui_context"] = self._ui_context_payload()
            self.store.save(preserved, conversation=self.state.conversation)
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
            blockers = self.state.context_summary.get("blockers")
            blocker_count = len(blockers) if isinstance(blockers, list) else 0
            self.state.add_notice(f"context refs={self.state.world_ref_count} blockers={blocker_count}")
            return False
        if command == "/refs":
            self.state.add_notice(", ".join(self.state.world_refs[-8:]) or "no world refs")
            return False
        self.state.add_notice(f"unknown command: {command}")
        return False

    def _run_agent_turn(self, prompt: str) -> None:
        self.state.add_user(prompt)
        self.state.running = True
        self.state.mode = "START"
        self.state.add_notice("agent running; Ctrl-C waits for the current provider call to finish")
        event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        previous = self._previous_result()

        def event_sink(event: dict[str, Any]) -> None:
            event_queue.put(("event", event))

        def worker() -> None:
            try:
                result = self.adapter.run(
                    self._runtime_prompt(prompt),
                    active_user_request=prompt,
                    ui_context=self._ui_context_payload(),
                    previous_result=previous,
                    event_sink=event_sink,
                )
                event_queue.put(("result", result))
            except BaseException as exc:  # noqa: BLE001 - surface errors in TUI state.
                event_queue.put(("error", exc))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            while thread.is_alive() or not event_queue.empty():
                self._drain_events(event_queue)
                self._draw()
                time.sleep(0.05)
        except KeyboardInterrupt:
            self.state.running = False
            self.state.mode = "INTERRUPTED"
            self.state.add_notice("turn interrupted in UI; late provider/tool results will be ignored")
            self._draw()
            return
        self._drain_events(event_queue)
        self._draw()

    def _accept_user_prompt(self, prompt: str) -> str | None:
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

    def _drain_events(self, event_queue: queue.Queue[tuple[str, Any]]) -> None:
        while True:
            try:
                kind, payload = event_queue.get_nowait()
            except queue.Empty:
                return
            if kind == "event":
                self.state.apply_event(payload)
            elif kind == "result":
                if self.state.mode == "INTERRUPTED":
                    self.state.add_notice("ignored late result from interrupted turn")
                    continue
                self.state.apply_result(payload)
                payload = self._with_ui_context(payload)
                self.store.save(payload, conversation=self.state.conversation)
                self.state.add_notice("turn completed")
            elif kind == "error":
                self.state.running = False
                self.state.mode = "FAILED"
                self.state.status = "failed"
                self.state.reason = type(payload).__name__
                self.state.add_notice(f"agent error: {payload}")

    def _previous_result(self) -> dict[str, Any] | None:
        loaded = self.store.load()
        if not isinstance(loaded, dict):
            return None
        previous = loaded.get("last_result")
        return dict(previous) if isinstance(previous, dict) else None

    def _runtime_prompt(self, prompt: str) -> str:
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

    def _with_ui_context(self, result: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(result)
        enriched["ui_context"] = self._ui_context_payload()
        return enriched

    def _ui_context_payload(self) -> dict[str, Any]:
        return {
            "conversation_summary": self.state.conversation_summary,
            "metrics": dict(self.state.ui_context_metrics),
        }

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

    def _draw(self) -> None:
        print(render_tui(self.state), end="", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Real Pi/Hermes-style TUI for AppV2.2.")
    parser.add_argument("--workspace", default=".", help="Workspace root for the agent.")
    parser.add_argument("--dotenv", default=".env", help="AppV2 dotenv path.")
    parser.add_argument("--max-turns", type=int, default=12)
    parser.add_argument("--extension", action="append", default=["file_management"])
    args = parser.parse_args(argv)
    app = AppV22Tui(
        workspace=Path(args.workspace).expanduser().resolve(),
        dotenv_path=Path(args.dotenv).expanduser().resolve(),
        max_turns=args.max_turns,
        extensions=tuple(args.extension),
    )
    return app.run()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


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
    if marker_hits >= 2:
        return True
    if text.startswith("|") and marker_hits >= 1:
        return True
    return False
