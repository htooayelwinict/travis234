"""JSON Lines RPC transport for one CodingApp session."""

from __future__ import annotations

import json
import threading
from typing import Mapping, TextIO

from travis.ai.types import AssistantMessage, TextContent
from travis.coding_agent.automation import serialize_machine_value
from travis.coding_agent.extension_host import (
    ExtensionHostAdapter,
    noninteractive_extension_bindings,
)

_MUTATING_METHODS = {
    "prompt",
    "continue",
    "set_model",
    "set_thinking",
    "compact",
    "close",
}


class RpcServer:
    def __init__(self, app, input: TextIO, output: TextIO) -> None:
        self.app = app
        self.input = input
        self.output = output
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._active_id: object | None = None
        self._active_thread: threading.Thread | None = None
        self._workers: list[threading.Thread] = []
        self._abort_requested = False
        self._closed = False

    def run(self) -> int:
        extension_host = self._create_extension_host()
        if extension_host is not None:
            extension_host.start()
        subscribe = getattr(self.app.session, "subscribe", None)
        unsubscribe = subscribe(self._on_session_event) if callable(subscribe) else None
        try:
            for raw_line in self.input:
                if self._closed:
                    break
                line = raw_line.strip()
                if not line:
                    continue
                self._handle_line(line)
        finally:
            for worker in list(self._workers):
                worker.join()
            if callable(unsubscribe):
                unsubscribe()
            if extension_host is not None:
                extension_host.dispose()
        return 0

    def _create_extension_host(self) -> ExtensionHostAdapter | None:
        if not callable(getattr(self.app, "subscribe_session_rebound", None)):
            return None
        if not callable(getattr(self.app.session, "bind_extensions", None)):
            return None
        return ExtensionHostAdapter(
            self.app,
            mode="rpc",
            bindings_factory=lambda session: noninteractive_extension_bindings(
                self.app,
                session,
            ),
        )

    def _handle_line(self, line: str) -> None:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            self._write_error(None, "parse_error", "Invalid JSON frame")
            return
        if not isinstance(request, dict):
            self._write_error(None, "invalid_request", "Request must be an object")
            return
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})
        if "id" not in request or not isinstance(method, str):
            self._write_error(request_id, "invalid_request", "Request requires id and method")
            return
        if not isinstance(params, dict):
            self._write_error(request_id, "invalid_params", "params must be an object")
            return
        if method not in {
            "prompt",
            "continue",
            "abort",
            "get_state",
            "set_model",
            "set_thinking",
            "compact",
            "close",
        }:
            self._write_error(request_id, "unknown_method", f"Unknown method: {method}")
            return
        with self._lock:
            busy = self._active_id is not None
        if busy and method in _MUTATING_METHODS:
            self._write_error(request_id, "busy_session", "Another request owns the active turn")
            return
        try:
            if method == "prompt":
                text = params.get("text")
                if not isinstance(text, str):
                    raise _InvalidParams("prompt requires string params.text")
                images = params.get("images", [])
                if not isinstance(images, list) or not all(
                    isinstance(path, str) for path in images
                ):
                    raise _InvalidParams("prompt params.images must be an array of paths")
                self._start_turn(
                    request_id,
                    (
                        (lambda: self.app.run_turn(text, image_paths=images, input_source="rpc"))
                        if images
                        else (lambda: self.app.run_turn(text, input_source="rpc"))
                    ),
                )
            elif method == "continue":
                if params:
                    raise _InvalidParams("continue does not accept params")
                self._start_turn(request_id, self.app.session.continue_)
            elif method == "abort":
                self._handle_abort(request_id)
            elif method == "get_state":
                if params:
                    raise _InvalidParams("get_state does not accept params")
                self._write_result(request_id, self._state())
            elif method == "set_model":
                self._handle_set_model(request_id, params)
            elif method == "set_thinking":
                self._handle_set_thinking(request_id, params)
            elif method == "compact":
                self._handle_compact(request_id, params)
            else:
                if params:
                    raise _InvalidParams("close does not accept params")
                self._write_result(request_id, {"closed": True})
                self._closed = True
        except _InvalidParams as error:
            self._write_error(request_id, "invalid_params", str(error))
        except Exception:
            self._write_error(request_id, "internal_error", "Request failed")

    def _start_turn(self, request_id: object, operation) -> None:
        with self._lock:
            if self._active_id is not None:
                self._write_error(request_id, "busy_session", "Another request owns the active turn")
                return
            self._active_id = request_id
            self._abort_requested = False

        def run() -> None:
            try:
                messages = operation() or []
                assistant = _last_assistant(messages)
                with self._lock:
                    aborted = self._abort_requested
                self._write_result(
                    request_id,
                    {
                        "stopReason": (
                            assistant.stop_reason
                            if assistant is not None
                            else "aborted"
                            if aborted
                            else "stop"
                        ),
                        "text": _assistant_text(assistant) if assistant is not None else "",
                    },
                )
            except Exception:
                self._write_error(request_id, "internal_error", "Turn failed")
            finally:
                with self._lock:
                    self._active_id = None
                    self._active_thread = None
                    self._abort_requested = False

        worker = threading.Thread(target=run, name="travis-rpc-turn", daemon=True)
        with self._lock:
            self._active_thread = worker
            self._workers.append(worker)
        worker.start()

    def _handle_abort(self, request_id: object) -> None:
        with self._lock:
            active = self._active_id is not None
            if active:
                self._abort_requested = True
        if active:
            self.app.session.agent.abort()
        self._write_result(request_id, {"aborted": active})

    def _handle_set_model(self, request_id: object, params: Mapping[str, object]) -> None:
        provider = params.get("provider")
        model_id = params.get("id", params.get("model"))
        if not isinstance(provider, str) or not isinstance(model_id, str):
            raise _InvalidParams("set_model requires string provider and id")
        model = self.app.model_registry.find(provider, model_id)
        if model is None:
            raise _InvalidParams(f"Unknown model: {provider}/{model_id}")
        self.app.session.set_model(model)
        self._write_result(request_id, {"provider": provider, "id": model_id})

    def _handle_set_thinking(self, request_id: object, params: Mapping[str, object]) -> None:
        level = params.get("level")
        if not isinstance(level, str):
            raise _InvalidParams("set_thinking requires string level")
        self.app.session.set_thinking_level(level)
        self._write_result(request_id, {"level": self.app.session.thinking_level})

    def _handle_compact(self, request_id: object, params: Mapping[str, object]) -> None:
        focus = params.get("focus")
        deep = params.get("deep", False)
        if focus is not None and not isinstance(focus, str):
            raise _InvalidParams("compact focus must be a string")
        if not isinstance(deep, bool):
            raise _InvalidParams("compact deep must be a boolean")
        result = self.app.session.compact(focus=focus, deep=deep)
        self._write_result(request_id, {"compaction": serialize_machine_value(result)})

    def _state(self) -> dict[str, object]:
        with self._lock:
            busy = self._active_id is not None
        session = self.app.session
        return {
            "busy": busy,
            "sessionId": session.session_id or None,
            "cwd": session.cwd,
            "model": {"provider": session.model.provider, "id": session.model.id},
            "thinkingLevel": session.thinking_level,
            "messageCount": len(session.messages),
        }

    def _on_session_event(self, event: object) -> None:
        with self._lock:
            request_id = self._active_id
        if request_id is None:
            return
        event_data = serialize_machine_value(event)
        if not isinstance(event_data, dict):
            return
        self._write({"id": request_id, "event": event_data})

    def _write_result(self, request_id: object, result: Mapping[str, object]) -> None:
        self._write({"id": request_id, "result": result})

    def _write_error(self, request_id: object, code: str, message: str) -> None:
        self._write({"id": request_id, "error": {"code": code, "message": message}})

    def _write(self, frame: Mapping[str, object]) -> None:
        encoded = json.dumps(serialize_machine_value(frame), ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            self.output.write(encoded + "\n")
            self.output.flush()


class _InvalidParams(ValueError):
    pass


def _last_assistant(messages) -> AssistantMessage | None:
    return next((message for message in reversed(messages) if isinstance(message, AssistantMessage)), None)


def _assistant_text(message: AssistantMessage) -> str:
    return "".join(block.text for block in message.content if isinstance(block, TextContent))


__all__ = ["RpcServer"]
