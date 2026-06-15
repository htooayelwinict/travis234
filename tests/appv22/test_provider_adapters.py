from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "appV2.2"))

from appv22.providers.appv2_env import (
    AppV2EnvAppV22ProviderAdapter,
    normalize_appv22_decision_payload,
)
from appv22.runtime.decisions import RuntimeDecision


def test_normalize_synthesizes_non_empty_decision_id_when_missing():
    first = normalize_appv22_decision_payload(
        {
            "kind": "tool_call",
            "reason": "legacy provider omitted id",
            "payload": {"tool_name": "inventory_probe", "params": {"depth": 1}},
            "evidence_refs": ["world://seed"],
        }
    )
    second = normalize_appv22_decision_payload(
        {
            "kind": "tool_call",
            "reason": "legacy provider omitted id",
            "payload": {"tool_name": "inventory_probe", "params": {"depth": 1}},
            "evidence_refs": ["world://seed"],
        }
    )

    assert first.decision_id
    assert first.decision_id == second.decision_id
    assert first.decision_id != ""


def test_normalize_deep_copies_params_into_arguments_without_aliasing():
    params = {"nested": {"depth": 1}}
    decision = normalize_appv22_decision_payload(
        {
            "decision_id": "dec_params",
            "kind": "tool_call",
            "reason": "copy params safely",
            "payload": {"tool_name": "inventory_probe", "params": params},
            "evidence_refs": [],
        }
    )

    params["nested"]["depth"] = 99
    decision.payload["params"]["nested"]["depth"] = 2

    assert decision.payload["arguments"] == {"nested": {"depth": 1}}
    assert decision.payload["arguments"] is not decision.payload["params"]
    assert decision.payload["arguments"]["nested"] is not decision.payload["params"]["nested"]


def test_normalize_preserves_generic_tool_name_as_tool_id_without_mapping():
    decision = normalize_appv22_decision_payload(
        {
            "decision_id": "dec_generic",
            "kind": "tool_call",
            "reason": "inspect generic context",
            "payload": {"tool_name": "inventory_probe", "params": {"depth": 1}},
            "evidence_refs": ["world://seed"],
        }
    )

    assert decision == RuntimeDecision(
        kind="tool_call",
        reason="inspect generic context",
        payload={
            "tool_name": "inventory_probe",
            "params": {"depth": 1},
            "arguments": {"depth": 1},
            "tool_id": "inventory_probe",
        },
        evidence_refs=["world://seed"],
        decision_id="dec_generic",
    )


def test_normalize_passes_through_existing_tool_id_without_mapping():
    decision = normalize_appv22_decision_payload(
        {
            "kind": "tool_call",
            "reason": "use selected tool id",
            "payload": {"tool_id": "extension.generic.tool", "arguments": {"limit": 3}},
            "evidence_refs": [],
        }
    )

    assert decision.payload["tool_id"] == "extension.generic.tool"
    assert decision.payload["arguments"] == {"limit": 3}


def test_normalize_uses_caller_supplied_tool_name_mapping():
    decision = normalize_appv22_decision_payload(
        {
            "kind": "tool_call",
            "reason": "use legacy name",
            "payload": {"tool_name": "legacy_probe", "arguments": {"limit": 2}},
            "evidence_refs": [],
        },
        tool_name_map={"legacy_probe": "extensions.generic_inventory.probe"},
    )

    assert decision.payload["tool_id"] == "extensions.generic_inventory.probe"
    assert decision.payload["arguments"] == {"limit": 2}


def test_normalize_reconstructs_appv2_env_like_raw_decision():
    class AppV21LikeDecision:
        decision_id = "dec_appv21"
        kind = "observe"
        reason = "Need current repo map before planning."
        payload = {"tool_name": "repo_snapshot"}
        evidence_refs = []

    decision = normalize_appv22_decision_payload(
        AppV21LikeDecision(),
        tool_name_map={"repo_snapshot": "file_management.repo_snapshot"},
    )

    assert decision == RuntimeDecision(
        kind="tool_call",
        reason="Need current repo map before planning.",
        payload={"tool_name": "repo_snapshot", "tool_id": "file_management.repo_snapshot", "arguments": {}},
        evidence_refs=[],
        decision_id="dec_appv21",
    )


def test_appv2_env_adapter_wraps_delegate_and_normalizes_decisions():
    class DelegateProvider:
        provider_id = "appv2-env-worker"

        def __init__(self):
            self.prompts = []

        def decide(self, prompt):
            self.prompts.append(prompt)
            return {
                "decision_id": "dec_delegate",
                "kind": "tool_call",
                "reason": "legacy tool name",
                "payload": {"tool_name": "repo_snapshot"},
                "evidence_refs": [],
            }

    delegate = DelegateProvider()
    adapter = AppV2EnvAppV22ProviderAdapter(
        delegate,
        tool_name_map={"repo_snapshot": "file_management.repo_snapshot"},
    )

    decision = adapter.decide({"prompt": "safe"})

    assert delegate.prompts == [{"prompt": "safe"}]
    assert decision.kind == "tool_call"
    assert decision.payload["tool_id"] == "file_management.repo_snapshot"
    assert adapter.provider_id == "appv2-env-worker-appv22-adapter"
