from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


MAX_DETAIL_CHARS = 700


@dataclass(frozen=True)
class UIEvent:
    kind: str
    title: str
    detail: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def events_from_result(result: dict[str, Any] | None) -> list[UIEvent]:
    if not isinstance(result, dict):
        return []
    events = result.get("events")
    if not isinstance(events, list):
        return []
    return [event_from_runtime_event(event) for event in events if isinstance(event, dict)]


def event_from_runtime_event(event: dict[str, Any]) -> UIEvent:
    event_type = str(event.get("event_type") or event.get("type") or "RuntimeEvent")
    payload = _payload_dict(event.get("payload"))
    title = _title_for(event_type, payload)
    detail = _detail_for(event_type, payload)
    return UIEvent(
        kind=event_type,
        title=title,
        detail=_clip(detail),
        payload=_sanitize_payload(payload),
    )


def result_summary(result: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {
            "status": "empty",
            "reason": "no persisted session",
            "session_id": "",
            "world_ref_count": 0,
            "context_summary": {},
            "usage": {},
        }
    world_refs = result.get("world_refs") if isinstance(result.get("world_refs"), dict) else {}
    context_summary = result.get("context_summary") if isinstance(result.get("context_summary"), dict) else {}
    return {
        "status": str(result.get("status") or "unknown"),
        "reason": str(result.get("reason") or ""),
        "session_id": str(result.get("session_id") or ""),
        "world_ref_count": len(world_refs),
        "world_refs": sorted(world_refs.keys())[:20],
        "context_summary": _sanitize_payload(context_summary),
        "usage": _usage_from_result(result),
    }


def _payload_dict(payload: Any) -> dict[str, Any]:
    return dict(payload) if isinstance(payload, dict) else {}


def _title_for(event_type: str, payload: dict[str, Any]) -> str:
    titles = {
        "AgentStarted": "agent started",
        "DecisionProposed": "decision proposed",
        "ModeChanged": "mode changed",
        "ToolCallCompleted": "tool completed",
        "ToolCallDenied": "tool denied",
        "ToolCallFailed": "tool failed",
        "ToolResultRecorded": "tool result recorded",
        "WorldRefAdded": "world ref added",
        "ContextSummaryUpdated": "context compacted",
        "ProviderCallFailed": "provider call failed",
        "RunCompleted": "run completed",
        "RunFailed": "run failed",
    }
    if event_type == "DecisionProposed":
        decision_kind = payload.get("kind")
        return f"decision proposed: {decision_kind}" if decision_kind else titles[event_type]
    if event_type == "ModeChanged":
        mode = payload.get("mode")
        return f"mode: {mode}" if mode else titles[event_type]
    return titles.get(event_type, event_type)


def _detail_for(event_type: str, payload: dict[str, Any]) -> str:
    if event_type == "DecisionProposed":
        return str(payload.get("reason") or payload.get("decision_reason") or "")
    if event_type in {"ToolCallCompleted", "ToolCallDenied", "ToolCallFailed", "ToolResultRecorded"}:
        tool_id = payload.get("tool_id")
        status = payload.get("status")
        reason = payload.get("reason") or payload.get("message") or ""
        return " ".join(str(item) for item in (tool_id, status, reason) if item)
    if event_type == "WorldRefAdded":
        return str(payload.get("ref") or payload.get("world_ref") or payload.get("uri") or "")
    if event_type == "ContextSummaryUpdated":
        blockers = payload.get("blockers")
        if isinstance(blockers, list) and blockers:
            return f"blockers: {len(blockers)}"
        return "summary refreshed"
    if event_type in {"RunCompleted", "RunFailed", "ProviderCallFailed"}:
        return str(payload.get("reason") or payload.get("message") or payload.get("status") or "")
    return str(payload.get("reason") or payload.get("status") or "")


def _usage_from_result(result: dict[str, Any]) -> dict[str, Any]:
    for key in ("usage", "usage_snapshot", "provider_usage", "costs", "metrics"):
        value = result.get(key)
        if isinstance(value, dict):
            return _sanitize_payload(value)
    return {}


def _sanitize_payload(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "<nested>"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _looks_secret(key_str):
                sanitized[key_str] = "<redacted>"
            else:
                sanitized[key_str] = _sanitize_payload(item, depth=depth + 1)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_payload(item, depth=depth + 1) for item in value[:20]]
    if isinstance(value, str):
        return _clip(value)
    return value


def _looks_secret(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in ("api_key", "token", "secret", "password", "authorization"))


def _clip(value: str) -> str:
    if len(value) <= MAX_DETAIL_CHARS:
        return value
    return f"{value[:MAX_DETAIL_CHARS - 15]}...<truncated>"
