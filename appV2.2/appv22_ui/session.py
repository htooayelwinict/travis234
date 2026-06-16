from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from appv22.context.summary_hygiene import (
    normalized_context_summary,
    resolve_tool_risks_from_world_refs,
    strip_cross_turn_tool_availability_risks,
    strip_turn_local_repair_risks,
)


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
        if not isinstance(loaded, dict):
            return None
        return _loaded_session_payload(loaded)

    def save(self, result: dict[str, Any], *, conversation: list[Any] | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(_session_payload(result, conversation=conversation), indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )


def _session_payload(result: dict[str, Any], *, conversation: list[Any] | None = None) -> dict[str, Any]:
    world_refs = result.get("world_refs") if isinstance(result.get("world_refs"), dict) else {}
    sanitized_world_refs = _sanitized_world_refs(world_refs)
    context_summary = _sanitized_context_summary(
        result.get("context_summary") if isinstance(result.get("context_summary"), dict) else {},
        world_refs=sanitized_world_refs,
    )
    ui_context = result.get("ui_context") if isinstance(result.get("ui_context"), dict) else {}
    return {
        "session_id": str(result.get("session_id") or ""),
        "status": str(result.get("status") or ""),
        "reason": str(result.get("reason") or ""),
        "world_refs": sanitized_world_refs,
        "context_summary": context_summary,
        "turn_feedback": list(result.get("turn_feedback", [])) if isinstance(result.get("turn_feedback"), list) else [],
        "usage": dict(result.get("usage", {})) if isinstance(result.get("usage"), dict) else {},
        "ui_context": ui_context,
        "conversation": _conversation_payload(conversation),
        "last_result": {
            "status": str(result.get("status") or ""),
            "reason": str(result.get("reason") or ""),
            "session_id": str(result.get("session_id") or ""),
            "world_refs": sanitized_world_refs,
            "context_summary": context_summary,
            "turn_feedback": list(result.get("turn_feedback", [])) if isinstance(result.get("turn_feedback"), list) else [],
            "usage": dict(result.get("usage", {})) if isinstance(result.get("usage"), dict) else {},
            "assistant_message": str(result.get("assistant_message") or ""),
        },
    }


def _loaded_session_payload(loaded: dict[str, Any]) -> dict[str, Any]:
    base = loaded.get("last_result") if isinstance(loaded.get("last_result"), dict) else loaded
    payload = _session_payload(base, conversation=_loaded_conversation_lines(loaded.get("conversation")))
    ui_context = loaded.get("ui_context")
    if isinstance(ui_context, dict):
        payload["ui_context"] = ui_context
    return payload


def _sanitized_world_refs(world_refs: dict[str, Any]) -> dict[str, dict[str, Any]]:
    sanitized: dict[str, dict[str, Any]] = {}
    for ref_id, ref in world_refs.items():
        if not isinstance(ref_id, str) or not isinstance(ref, dict):
            continue
        if _is_legacy_latest_world_ref(ref_id):
            continue
        item = {
            "ref_id": str(ref.get("ref_id") or ref_id),
            "kind": str(ref.get("kind") or ""),
            "summary": str(ref.get("summary") or ""),
        }
        arguments = ref.get("arguments")
        if isinstance(arguments, dict):
            item["arguments"] = dict(arguments)
        for key in ("freshness", "request_id", "run_id", "mutation_seq"):
            value = ref.get(key)
            if isinstance(value, str | int):
                item[key] = value
        payload = _sanitized_world_ref_payload(str(ref.get("kind") or ""), ref.get("payload"))
        if payload:
            item["payload"] = payload
        sanitized[ref_id] = item
    return sanitized


def _sanitized_world_ref_payload(kind: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if kind == "file_management.repo_snapshot":
        item: dict[str, Any] = {}
        files = payload.get("files")
        directories = payload.get("directories")
        if isinstance(files, list):
            item["files"] = [str(path)[:240] for path in files[:600] if isinstance(path, str)]
        if isinstance(directories, list):
            item["directories"] = [str(path)[:240] for path in directories[:300] if isinstance(path, str)]
        previews = payload.get("text_previews")
        if isinstance(previews, dict):
            item["text_previews"] = {
                str(path)[:240]: str(text)[:700]
                for path, text in list(previews.items())[:40]
                if isinstance(path, str) and isinstance(text, str)
            }
        return item
    if kind == "file_management.read_file":
        content = payload.get("content")
        path = payload.get("path")
        item = {}
        if isinstance(path, str):
            item["path"] = path[:240]
        if isinstance(content, str):
            item["content"] = content[:12000]
        return item
    return {}


def _sanitized_context_summary(summary: dict[str, Any], *, world_refs: dict[str, Any]) -> dict[str, Any]:
    normalized = resolve_tool_risks_from_world_refs(
        strip_turn_local_repair_risks(strip_cross_turn_tool_availability_risks(summary)),
        world_refs,
    )
    normalized = normalized_context_summary(normalized)
    live_refs = set(world_refs)
    normalized["evidence_refs"] = [
        ref for ref in normalized.get("evidence_refs", []) if isinstance(ref, str) and ref in live_refs
    ]
    return normalized


def _conversation_payload(conversation: list[Any] | None) -> list[dict[str, str]]:
    lines: list[dict[str, str]] = []
    for item in conversation or []:
        role = getattr(item, "role", None)
        text = getattr(item, "text", None)
        if isinstance(role, str) and isinstance(text, str) and role and text:
            lines.append({"role": role, "text": text})
    return lines[-40:]


def _loaded_conversation_lines(conversation: Any) -> list[Any]:
    if not isinstance(conversation, list):
        return []
    lines = []
    for item in conversation:
        if isinstance(item, dict):
            lines.append(type("ConversationLineLike", (), {"role": item.get("role"), "text": item.get("text")})())
    return lines


def _is_legacy_latest_world_ref(ref_id: str) -> bool:
    return ref_id.endswith("/latest")
