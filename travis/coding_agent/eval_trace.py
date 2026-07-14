"""Strict, sanitized JSONL lifecycle traces for coding-agent evaluation."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path

SAFE_EVENT_TYPES = {
    "tui_ready", "model_picker_ready", "model_selected", "turn_start", "tool_end",
    "compaction_end", "turn_end", "turn_ready", "capability_granted", "fatal", "shutdown",
    "process_event", "user_command_started", "user_command_interrupt", "extension_command",
}
SAFE_FIELDS = {
    "run_id", "turn_id", "tool_call_id", "tool", "status", "error_code", "duration_ms",
    "input_tokens", "output_tokens", "compression_count", "provider", "model", "model_count",
    "picker_query", "action", "operation", "reason_code", "trigger", "session_id", "session_path",
    "process_id", "process_state", "origin", "interrupt_count",
    "context_tokens", "context_window", "context_percent", "context_estimated", "context_confidence",
    "summary_model_requested", "summary_model_used", "summary_model_fallback",
}
_SECRET_SHAPE = re.compile(r"(?:sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+)", re.IGNORECASE)


class SecretRedactor:
    def __init__(self, secret_values: Iterable[str] = ()) -> None:
        values = {str(value) for value in secret_values if len(str(value)) >= 4}
        self._secrets = tuple(sorted(values, key=len, reverse=True))

    def contains_secret(self, value: object) -> bool:
        text = json.dumps(value, ensure_ascii=False, default=str)
        return bool(_SECRET_SHAPE.search(text)) or any(secret in text for secret in self._secrets)

    def redact_text(self, value: object) -> str:
        text = _SECRET_SHAPE.sub("[REDACTED]", str(value))
        for secret in self._secrets:
            text = text.replace(secret, "[REDACTED]")
        return text


class EvalTraceWriter:
    def __init__(self, path: str | os.PathLike[str], *, redactor: SecretRedactor | None = None) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.close(descriptor)
        os.chmod(self.path, 0o600)
        self.redactor = redactor or SecretRedactor()
        self.run_id = uuid.uuid4().hex
        self._lock = threading.RLock()

    def write(self, event_type: str, fields: Mapping[str, object] | None = None) -> None:
        if event_type not in SAFE_EVENT_TYPES:
            raise ValueError(f"unsafe trace event: {event_type}")
        values = dict(fields or {})
        unsafe = set(values) - SAFE_FIELDS
        if unsafe:
            raise ValueError(f"unsafe trace field: {sorted(unsafe)[0]}")
        if self.redactor.contains_secret(values):
            raise ValueError("unsafe trace field contains secret material")
        payload = {
            "event": event_type,
            "timestamp_ms": int(time.time() * 1000),
            "run_id": self.run_id,
            **values,
        }
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n"
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()


class ConversationLogWriter:
    """Opt-in semantic turn capture for explicitly authorized evaluations."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        redactor: SecretRedactor | None = None,
    ) -> None:
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.close(descriptor)
        os.chmod(self.path, 0o600)
        self.redactor = redactor or SecretRedactor()
        self._lock = threading.RLock()

    def write(
        self,
        *,
        turn_id: str,
        prompt: str,
        response: str | None,
        status: str,
    ) -> None:
        payload = {
            "turn_id": str(turn_id),
            "prompt": self.redactor.redact_text(prompt),
            "response": self.redactor.redact_text(response or ""),
            "status": str(status),
        }
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n"
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()


__all__ = [
    "ConversationLogWriter",
    "EvalTraceWriter",
    "SecretRedactor",
    "SAFE_EVENT_TYPES",
    "SAFE_FIELDS",
]
