"""Stable print and JSON automation transports around a CodingApp session."""

from __future__ import annotations

import json
import re
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Mapping, Sequence, TextIO

from travis.ai.types import AssistantMessage, TextContent
from travis.coding_agent.extension_host import (
    ExtensionHostAdapter,
    noninteractive_extension_bindings,
)

_SENSITIVE_KEYS = {
    "apikey",
    "authorization",
    "credentials",
    "headers",
    "password",
    "providerheaders",
    "rawheaders",
    "refreshtoken",
    "secret",
    "token",
    "accesstoken",
}


def run_print_mode(
    app,
    prompt: str,
    output: TextIO,
    *,
    image_paths: Sequence[str] = (),
) -> int:
    extension_host = ExtensionHostAdapter(
        app,
        mode="print",
        bindings_factory=lambda session: noninteractive_extension_bindings(app, session),
    )
    extension_host.start()
    try:
        messages = (
            app.run_turn(prompt, image_paths=list(image_paths))
            if image_paths
            else app.run_turn(prompt)
        ) or []
    finally:
        extension_host.dispose()
    assistant = _last_assistant(messages) or _last_assistant(_app_messages(app))
    if assistant is None:
        return 0
    text = _assistant_text(assistant)
    if text:
        output.write(text)
    output.write("\n")
    output.flush()
    return 1 if assistant.stop_reason == "error" else 0


def run_json_mode(
    app,
    prompt: str,
    output: TextIO,
    *,
    image_paths: Sequence[str] = (),
) -> int:
    extension_host = ExtensionHostAdapter(
        app,
        mode="json",
        bindings_factory=lambda session: noninteractive_extension_bindings(app, session),
    )
    extension_host.start()
    _write_frame(
        output,
        {
            "type": "session",
            "schemaVersion": 1,
            "sessionId": app.session.session_id or None,
            "cwd": app.session.cwd,
            "model": {
                "provider": app.session.model.provider,
                "id": app.session.model.id,
            },
        },
    )

    def on_event(event: object) -> None:
        event_type = getattr(event, "type", None)
        message = getattr(event, "message", None)
        if event_type not in {"message_start", "message_end"} or not isinstance(message, AssistantMessage):
            return
        _write_frame(
            output,
            {
                "type": event_type,
                "message": serialize_machine_value(message),
            },
        )

    unsubscribe = app.session.subscribe(on_event)
    try:
        messages = (
            app.run_turn(prompt, image_paths=list(image_paths))
            if image_paths
            else app.run_turn(prompt)
        )
    finally:
        unsubscribe()
        extension_host.dispose()
    assistant = _last_assistant(messages or []) or _last_assistant(_app_messages(app))
    if assistant is None:
        _write_frame(output, {"type": "result", "stopReason": "error", "text": ""})
        return 1
    _write_frame(
        output,
        {
            "type": "result",
            "stopReason": assistant.stop_reason,
            "text": _assistant_text(assistant),
            "message": serialize_machine_value(assistant),
        },
    )
    return 1 if assistant.stop_reason == "error" else 0


def serialize_machine_value(value: object) -> object:
    """Return credential-safe, camelCase, JSON-compatible data."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        serialized: dict[str, object] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            if _is_sensitive_key(key):
                continue
            serialized[_camel_case(key)] = serialize_machine_value(item)
        return serialized
    if is_dataclass(value) and not isinstance(value, type):
        serialized = {}
        for field in fields(value):
            if _is_sensitive_key(field.name):
                continue
            serialized[_camel_case(field.name)] = serialize_machine_value(getattr(value, field.name))
        return serialized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [serialize_machine_value(item) for item in value]
    return f"<{type(value).__name__}>"


def _write_frame(output: TextIO, frame: Mapping[str, object]) -> None:
    output.write(json.dumps(serialize_machine_value(frame), ensure_ascii=False, separators=(",", ":")))
    output.write("\n")
    output.flush()


def _last_assistant(messages: Sequence[object]) -> AssistantMessage | None:
    return next((message for message in reversed(messages) if isinstance(message, AssistantMessage)), None)


def _app_messages(app) -> Sequence[object]:
    session = getattr(app, "session", None)
    messages = getattr(session, "messages", None)
    if isinstance(messages, Sequence):
        return messages
    messages = getattr(app, "messages", None)
    return messages if isinstance(messages, Sequence) else []


def _assistant_text(message: AssistantMessage) -> str:
    return "".join(block.text for block in message.content if isinstance(block, TextContent))


def _camel_case(value: str) -> str:
    return re.sub(r"_([a-zA-Z0-9])", lambda match: match.group(1).upper(), value)


def _is_sensitive_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.lower())
    return normalized in _SENSITIVE_KEYS


__all__ = ["run_json_mode", "run_print_mode", "serialize_machine_value"]
