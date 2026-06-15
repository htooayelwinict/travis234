"""AppV2-env provider adapter for AppV2.2."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from importlib import import_module
import json
from pathlib import Path
from typing import Any, Mapping

from appv22.runtime.decisions import KNOWN_DECISION_KINDS, RuntimeDecision


class AppV2EnvAppV22ProviderAdapter:
    """Adapts an AppV2.1 provider to the AppV2.2 provider boundary."""

    def __init__(
        self,
        delegate: Any,
        *,
        tool_name_map: Mapping[str, str] | None = None,
    ) -> None:
        self.delegate = delegate
        self.tool_name_map = dict(tool_name_map or {})
        delegate_id = str(getattr(delegate, "provider_id", "appv2-env"))
        self.provider_id = f"{delegate_id}-appv22-adapter"

    def decide(self, prompt: dict) -> RuntimeDecision:
        raw_decision = self.delegate.decide(prompt)
        return normalize_appv22_decision_payload(raw_decision, tool_name_map=self.tool_name_map)


def create_appv22_provider_from_appv2_env(
    dotenv_path: str | Path,
    tool_name_map: Mapping[str, str] | None = None,
) -> AppV2EnvAppV22ProviderAdapter:
    """Create an AppV2.2 adapter around the AppV2.1 appv2-env provider."""

    try:
        appv2_env = import_module("appv21.providers.appv2_env")
    except ImportError as exc:
        raise ImportError(
            "AppV2.1 appv2-env provider is unavailable; ensure appv21 is importable "
            "before creating the AppV2.2 adapter."
        ) from exc

    delegate = appv2_env.create_appv21_provider_from_appv2_env(dotenv_path=dotenv_path)
    return AppV2EnvAppV22ProviderAdapter(delegate, tool_name_map=tool_name_map)


def normalize_appv22_decision_payload(
    raw: Any,
    tool_name_map: Mapping[str, str] | None = None,
) -> RuntimeDecision:
    """Normalize a raw AppV2/AppV2.1-style decision into an AppV2.2 decision."""

    raw_decision = _raw_decision_dict(raw)
    payload = raw_decision.get("payload") if isinstance(raw_decision.get("payload"), dict) else {}
    payload = deepcopy(payload)
    _normalize_tool_payload(payload, tool_name_map=dict(tool_name_map or {}))

    kind = str(raw_decision.get("kind") or "pause")
    if kind in {"observe", "read_file"}:
        kind = "tool_call"
    elif kind not in KNOWN_DECISION_KINDS:
        payload = {
            "pause_type": "tool_blocked",
            "rejected_kind": raw_decision.get("kind"),
            "rejection_reason": "unknown_decision_kind",
        }
        kind = "pause"

    evidence_refs = raw_decision.get("evidence_refs")
    if not isinstance(evidence_refs, list):
        evidence_refs = []

    return RuntimeDecision(
        kind=kind,
        reason=str(raw_decision.get("reason") or ""),
        payload=payload,
        evidence_refs=[str(ref) for ref in evidence_refs],
        decision_id=_normalize_decision_id(raw_decision),
    )


def _raw_decision_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if hasattr(raw, "to_dict"):
        raw_dict = raw.to_dict()
        if isinstance(raw_dict, dict):
            return dict(raw_dict)
    return {
        "decision_id": getattr(raw, "decision_id", ""),
        "kind": getattr(raw, "kind", None),
        "reason": getattr(raw, "reason", ""),
        "payload": getattr(raw, "payload", {}),
        "evidence_refs": getattr(raw, "evidence_refs", []),
    }


def _normalize_decision_id(raw_decision: Mapping[str, Any]) -> str:
    decision_id = raw_decision.get("decision_id")
    if isinstance(decision_id, str) and decision_id:
        return decision_id

    stable_payload = {
        "kind": raw_decision.get("kind"),
        "reason": raw_decision.get("reason"),
        "payload": raw_decision.get("payload"),
        "evidence_refs": raw_decision.get("evidence_refs"),
    }
    encoded = json.dumps(stable_payload, sort_keys=True, default=str, separators=(",", ":"))
    return f"appv22-adapter-{sha256(encoded.encode('utf-8')).hexdigest()[:16]}"


def _normalize_tool_payload(payload: dict[str, Any], *, tool_name_map: Mapping[str, str]) -> None:
    if "params" in payload:
        payload["params"] = deepcopy(payload["params"])

    if "arguments" in payload:
        payload["arguments"] = deepcopy(payload["arguments"])
    elif "params" in payload:
        payload["arguments"] = deepcopy(payload["params"])

    if "tool_id" in payload:
        return

    tool_name = payload.get("tool_name", payload.get("tool"))
    if tool_name is None:
        return

    tool_name = str(tool_name)
    payload["tool_id"] = tool_name_map.get(tool_name, tool_name)
    payload.setdefault("arguments", {})
