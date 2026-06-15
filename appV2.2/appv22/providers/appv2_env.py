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

from appv22.context.evidence import ContextEvidence
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
        delegate_prompt = _appv21_compatible_prompt(prompt)
        raw_decision = self.delegate.decide(delegate_prompt)
        decision = normalize_appv22_decision_payload(raw_decision, tool_name_map=self.tool_name_map)
        return _coerce_appv22_progression(prompt, decision)


def create_appv22_provider_from_appv2_env(
    dotenv_path: str | Path,
    tool_name_map: Mapping[str, str] | None = None,
) -> AppV2EnvAppV22ProviderAdapter:
    """Create an AppV2.2 adapter around the AppV2.1 appv2-env provider."""

    _ensure_local_appv21_import_path()
    try:
        appv2_env = import_module("appv21.providers.appv2_env")
    except ImportError as exc:
        raise ImportError(
            "AppV2.1 appv2-env provider is unavailable; ensure appv21 is importable "
            "before creating the AppV2.2 adapter."
        ) from exc

    delegate = appv2_env.create_appv21_provider_from_appv2_env(dotenv_path=dotenv_path)
    return AppV2EnvAppV22ProviderAdapter(delegate, tool_name_map=tool_name_map)


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


def _appv21_compatible_prompt(prompt: dict) -> dict:
    adapted = deepcopy(prompt)
    state = adapted.get("state")
    if isinstance(state, dict):
        runtime_plan = state.get("runtime_plan")
        if isinstance(runtime_plan, dict) and runtime_plan:
            state.setdefault("plan", {"runtime_plan": deepcopy(runtime_plan)})
    return adapted


def _coerce_appv22_progression(prompt: dict, decision: RuntimeDecision) -> RuntimeDecision:
    if decision.kind != "plan":
        return decision

    selected_tools = _selected_tools(prompt)
    observation_tool = _missing_observation_tool(prompt, selected_tools)
    if observation_tool is not None:
        return RuntimeDecision(
            kind="tool_call",
            reason="Observe prompt-visible context before planning.",
            payload={"tool_id": observation_tool, "arguments": {}},
            evidence_refs=[],
        )

    state = prompt.get("state") if isinstance(prompt.get("state"), dict) else {}
    runtime_plan = state.get("runtime_plan") if isinstance(state, dict) else None
    if not isinstance(runtime_plan, dict) or not runtime_plan:
        return decision

    if not state.get("mutation_receipts"):
        mutation_intent = runtime_plan.get("mutation_intent")
        if isinstance(mutation_intent, dict):
            return RuntimeDecision(
                kind="mutation_intent",
                reason="Plan already exists; advance to mutation intent.",
                payload=deepcopy(mutation_intent),
                evidence_refs=["plan://accepted/latest"],
            )

    if state.get("mutation_receipts") and not state.get("verification_receipts"):
        return RuntimeDecision(
            kind="finalize",
            reason="Mutation receipt exists; advance to verification/finalization.",
            payload={},
            evidence_refs=["plan://accepted/latest"],
        )

    return decision


def _world_refs(prompt: dict) -> list[Any]:
    world = prompt.get("world") if isinstance(prompt.get("world"), dict) else {}
    refs = world.get("world_refs") if isinstance(world, dict) else None
    if isinstance(refs, dict):
        return list(refs.values())
    if isinstance(refs, list):
        return refs
    return []


def _missing_observation_tool(prompt: dict, selected_tools: list[str]) -> str | None:
    if not selected_tools:
        return None

    contracts = _observation_contracts(prompt)
    if not contracts:
        return selected_tools[0] if not _world_refs(prompt) else None

    selected_tool_ids = set(selected_tools)
    evidence = ContextEvidence.from_prompt(prompt)
    for contract in contracts:
        if _contract_satisfied(contract, evidence):
            continue
        preferred_tool_id = contract.get("preferred_tool_id")
        if isinstance(preferred_tool_id, str) and preferred_tool_id in selected_tool_ids:
            return preferred_tool_id
        return None
    return None


def _observation_contracts(prompt: dict) -> list[dict[str, Any]]:
    skills = prompt.get("skills") if isinstance(prompt.get("skills"), list) else []
    contracts: list[dict[str, Any]] = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        contract = skill.get("observation_contract")
        if isinstance(contract, dict):
            contracts.append(contract)
    return contracts


def _contract_satisfied(contract: Mapping[str, Any], evidence: ContextEvidence) -> bool:
    evidence_refs = _contract_values(contract.get("evidence_refs"))
    evidence_kinds = _contract_values(contract.get("evidence_kinds"))
    if not evidence_refs and not evidence_kinds:
        return True
    return evidence.has_any_ref(evidence_refs) or evidence.has_any_kind(evidence_kinds)


def _contract_values(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if item)
    return ()


def _selected_tools(prompt: dict) -> list[str]:
    selection = prompt.get("selection") if isinstance(prompt.get("selection"), dict) else {}
    selected = selection.get("selected_tools") if isinstance(selection, dict) else None
    if isinstance(selected, list):
        return [str(tool_id) for tool_id in selected if tool_id]
    tools = prompt.get("tools")
    if isinstance(tools, list):
        return [str(tool_id) for tool_id in tools if tool_id]
    return []
