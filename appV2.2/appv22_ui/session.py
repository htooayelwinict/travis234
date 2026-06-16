from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


SESSION_DIR_NAME = ".appv22-ui"
SESSION_FILE_NAME = "session.json"


@dataclass(frozen=True)
class SessionStore:
    workspace: Path

    @property
    def path(self) -> Path:
        return self.workspace / SESSION_DIR_NAME / SESSION_FILE_NAME

    def load(self) -> dict[str, Any] | None:
        try:
            raw = self.path.read_text(encoding="utf-8")
            loaded = json.loads(raw)
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError):
            return None
        return loaded if isinstance(loaded, dict) else None

    def save(self, result: dict[str, Any], *, conversation: list[Any] | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(_session_payload(result, conversation=conversation), indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )


def _session_payload(result: dict[str, Any], *, conversation: list[Any] | None = None) -> dict[str, Any]:
    world_refs = result.get("world_refs") if isinstance(result.get("world_refs"), dict) else {}
    context_summary = result.get("context_summary") if isinstance(result.get("context_summary"), dict) else {}
    ui_context = result.get("ui_context") if isinstance(result.get("ui_context"), dict) else {}
    return {
        "session_id": str(result.get("session_id") or ""),
        "status": str(result.get("status") or ""),
        "reason": str(result.get("reason") or ""),
        "world_refs": world_refs,
        "context_summary": context_summary,
        "ui_context": ui_context,
        "conversation": _conversation_payload(conversation),
        "last_result": {
            "session_id": str(result.get("session_id") or ""),
            "world_refs": world_refs,
            "context_summary": context_summary,
        },
    }


def _conversation_payload(conversation: list[Any] | None) -> list[dict[str, str]]:
    lines: list[dict[str, str]] = []
    for item in conversation or []:
        role = getattr(item, "role", None)
        text = getattr(item, "text", None)
        if isinstance(role, str) and isinstance(text, str) and role and text:
            lines.append({"role": role, "text": text})
    return lines[-40:]
