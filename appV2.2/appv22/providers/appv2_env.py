"""AppV2-env provider adapter for AppV2.2."""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from importlib import import_module
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Mapping

from appv22.runtime.decisions import KNOWN_DECISION_KINDS, RuntimeDecision


APPV22_DECISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decision_id": {"type": "string"},
        "kind": {
            "type": "string",
            "enum": ["tool_call", "pause", "compact", "finalize"],
        },
        "reason": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "payload": {"type": "object", "additionalProperties": True},
    },
    "required": ["kind", "reason", "evidence_refs", "payload"],
}


class AppV22NativeProvider:
    """AppV2.2-native JSON provider using the appv2-env model transport."""

    provider_id = "appv2-env-worker-appv22-native"

    def __init__(self, *, client: Any) -> None:
        self.client = client

    def decide(self, prompt: dict) -> RuntimeDecision:
        raw = self.client.complete_json(
            stage="appv22_decision",
            prompt=_appv22_decision_prompt(prompt),
            schema=APPV22_DECISION_SCHEMA,
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return RuntimeDecision(
                kind="compact",
                reason="Model returned invalid JSON for AppV2.2 decision.",
                payload={"error_type": "invalid_provider_json"},
            )
        if not isinstance(payload, dict):
            return RuntimeDecision(
                kind="pause",
                reason="Model decision was not a JSON object.",
                payload={"pause_type": "tool_blocked"},
            )
        return normalize_appv22_decision_payload(payload)

    def usage_snapshot(self, *, reset: bool = False) -> dict[str, Any]:
        usage_snapshot = getattr(self.client, "usage_snapshot", None)
        if callable(usage_snapshot):
            return usage_snapshot(reset=reset)
        return {}


def create_appv22_provider_from_appv2_env(
    dotenv_path: str | Path,
) -> AppV22NativeProvider:
    """Create an AppV2.2-native provider from the appv2-env model settings."""

    _ensure_local_appv21_import_path()
    try:
        env_config = import_module("appv21.providers.env_config")
        null_model = import_module("appv21.providers.null_model")
    except ImportError as exc:
        raise ImportError(
            "AppV2.1 appv2-env transport is unavailable; ensure appv21 is importable "
            "before creating the AppV2.2 native provider."
        ) from exc

    client = env_config.build_appv21_model_client("APPV2_WORKER_LLM", dotenv_path=dotenv_path)
    if client is None:
        return null_model.NullModelProvider()
    return AppV22NativeProvider(client=client)


def _appv22_decision_prompt(prompt_payload: dict) -> str:
    open_risks = _active_open_risk_lines(prompt_payload)
    return "\n".join(
        [
            "You are the AppV2.2 Pi-Hermes coding agent decision engine.",
            "Return only JSON matching the supplied schema. No markdown.",
            "Use a Pi-style coding-agent loop: decide, call tools when needed, consume tool results, then stop when evidence proves completion.",
            "Hermes dual context is active: summaries may compress prose, but exact evidence_refs and tool results are durable pointers.",
            "Reason internally only. Never rely on a separate planning lane.",
            "Use selected tools only. If exact facts are needed, request a read-only tool_call instead of trusting summaries.",
            "If you say a tool is required, kind must be tool_call and payload must include tool_id plus arguments.",
            "If world_refs or context_summary contain an exact durable ref, treat that observation as already done.",
            "After denied/failed tool feedback, runtime guidance and context_summary.open_risks supersede earlier one-shot user or skill instructions that the feedback says already happened.",
            "Use structured evidence_refs as authoritative and ignore partial/truncated world:// strings in prose.",
            "Do not repeat broad observation tools just because raw payload was compacted; rehydrate only when missing raw details are necessary.",
            "Use only selected tool calls for workspace changes; unsupported payload shapes are invalid.",
            "If state.mode is ACT and any context_summary.open_risks says the next decision must be a tool_call, emit kind=tool_call for the named selected tool; finalize/pause/compact are invalid while that risk remains.",
            "After successful action evidence, emit finalize or pause; do not repeat the same tool call.",
            "Do not claim workspace changes unless tool results or verification receipts prove them.",
            "For kind=finalize, put the public user-facing response in payload.message. Keep it concise. Do not include hidden reasoning.",
            *open_risks,
            json.dumps(prompt_payload, indent=2, sort_keys=True, default=str),
        ]
    )


def _active_open_risk_lines(prompt_payload: dict) -> list[str]:
    state = prompt_payload.get("state") if isinstance(prompt_payload, dict) else {}
    if not isinstance(state, dict):
        return []
    summary = state.get("context_summary")
    if not isinstance(summary, dict):
        return []
    risks = summary.get("open_risks")
    if not isinstance(risks, list) or not risks:
        return []
    lines = ["CURRENT OPEN RISKS:"]
    for risk in risks[-6:]:
        if risk:
            lines.append(f"- {str(risk)[:600]}")
    return lines


def _ensure_local_appv21_import_path() -> None:
    if importlib.util.find_spec("appv21") is not None:
        return

    appv21_root = _discover_local_appv21_root(Path(__file__))
    if appv21_root is None:
        return

    appv21_root_str = str(appv21_root)
    if appv21_root_str not in sys.path:
        sys.path.insert(0, appv21_root_str)


def _discover_local_appv21_root(anchor: Path) -> Path | None:
    for parent in anchor.resolve().parents:
        candidate = parent / "appV2.1"
        if (candidate / "appv21").is_dir():
            return candidate
    return None


def normalize_appv22_decision_payload(raw: Any) -> RuntimeDecision:
    """Normalize a raw native AppV2.2 provider payload into a runtime decision."""

    raw_decision = _raw_decision_dict(raw)
    payload = raw_decision.get("payload") if isinstance(raw_decision.get("payload"), dict) else {}
    payload = deepcopy(payload)

    kind = str(raw_decision.get("kind") or "pause")
    if kind not in KNOWN_DECISION_KINDS:
        payload = {
            "pause_type": "unsupported_decision_kind_removed",
            "rejected_kind": raw_decision.get("kind"),
            "rejection_reason": "Pi-style runtime accepts only model/tool/result loop decisions",
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
