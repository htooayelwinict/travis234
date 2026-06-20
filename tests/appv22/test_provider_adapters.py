import importlib.util as importlib_util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "appV2.2"))

import appv22.providers as appv22_providers
import appv22.providers.appv2_env as appv2_env_provider
from appv22.providers.appv2_env import (
    AppV22NativeProvider,
    create_appv22_provider_from_appv2_env,
    normalize_appv22_decision_payload,
)
from appv22.runtime.decisions import RuntimeDecision


def test_normalize_synthesizes_non_empty_decision_id_when_missing():
    first = normalize_appv22_decision_payload(
        {
            "kind": "tool_call",
            "reason": "provider omitted id",
            "payload": {"tool_id": "inventory_probe", "arguments": {"depth": 1}},
            "evidence_refs": ["world://seed"],
        }
    )
    second = normalize_appv22_decision_payload(
        {
            "kind": "tool_call",
            "reason": "provider omitted id",
            "payload": {"tool_id": "inventory_probe", "arguments": {"depth": 1}},
            "evidence_refs": ["world://seed"],
        }
    )

    assert first.decision_id
    assert first.decision_id == second.decision_id


def test_normalize_preserves_native_tool_call_payload_without_legacy_translation():
    decision = normalize_appv22_decision_payload(
        {
            "decision_id": "dec_native",
            "kind": "tool_call",
            "reason": "use selected tool id",
            "payload": {"tool_id": "extension.generic.tool", "arguments": {"limit": 3}},
            "evidence_refs": [],
        }
    )

    assert decision == RuntimeDecision(
        kind="tool_call",
        reason="use selected tool id",
        payload={"tool_id": "extension.generic.tool", "arguments": {"limit": 3}},
        evidence_refs=[],
        decision_id="dec_native",
    )


def test_normalize_does_not_translate_legacy_tool_name_or_params():
    decision = normalize_appv22_decision_payload(
        {
            "decision_id": "dec_legacy_tool_name",
            "kind": "tool_call",
            "reason": "legacy tool shape",
            "payload": {"tool_name": "repo_snapshot", "params": {"depth": 1}},
            "evidence_refs": [],
        }
    )

    assert decision.kind == "tool_call"
    assert decision.payload == {"tool_name": "repo_snapshot", "params": {"depth": 1}}


def test_normalize_rejects_legacy_observe_verify_and_plan_kinds():
    for legacy_kind in ("observe", "verify", "plan", "read_file"):
        decision = normalize_appv22_decision_payload(
            {
                "decision_id": f"dec_{legacy_kind}",
                "kind": legacy_kind,
                "reason": "legacy decision kind",
                "payload": {"tool_id": "file_management.read_file", "arguments": {"path": "README.md"}},
                "evidence_refs": [],
            }
        )

        assert decision.kind == "pause"
        assert decision.payload["pause_type"] == "unsupported_decision_kind_removed"
        assert decision.payload["rejected_kind"] == legacy_kind
        assert "model/tool/result loop" in decision.payload["rejection_reason"]


def test_legacy_appv21_provider_adapter_is_not_exported():
    assert not hasattr(appv2_env_provider, "AppV2EnvAppV22ProviderAdapter")
    assert "AppV2EnvAppV22ProviderAdapter" not in getattr(appv22_providers, "__all__", ())


def test_native_appv22_provider_uses_appv22_stage_schema_and_prompt_directly():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def complete_json(self, *, stage, prompt, schema):
            self.calls.append({"stage": stage, "prompt": prompt, "schema": schema})
            return (
                '{"decision_id":"dec_native","kind":"tool_call","reason":"native appv22 write",'
                '"evidence_refs":["world://file_management.repo_snapshot/latest"],'
                '"payload":{"tool_id":"file_management.write_file","arguments":{"path":"docs/a.md","content":"a"}}}'
            )

    client = FakeClient()
    provider = AppV22NativeProvider(client=client)

    decision = provider.decide(
        {
            "system": {"identity": "AppV2.2 Pi-Hermes coding agent"},
            "state": {"mode": "THINK"},
            "world": {"world_refs": {"world://file_management.repo_snapshot/latest": {"kind": "file_management.repo_snapshot"}}},
            "selection": {"selected_tools": ["file_management.repo_snapshot"]},
        }
    )

    assert decision.kind == "tool_call"
    assert decision.payload["tool_id"] == "file_management.write_file"
    assert decision.payload["arguments"]["path"] == "docs/a.md"
    assert provider.provider_id == "appv2-env-worker-appv22-native"
    assert client.calls[0]["stage"] == "appv22_decision"
    assert "AppV2.2 Pi-Hermes coding agent" in client.calls[0]["prompt"]
    assert client.calls[0]["schema"]["properties"]["kind"]["enum"] == [
        "tool_call",
        "pause",
        "compact",
        "finalize",
    ]


def test_native_appv22_provider_prompt_prioritizes_open_risk_tool_call_over_finalize():
    prompt = appv2_env_provider._appv22_decision_prompt(
        {
            "state": {
                "mode": "ACT",
                "context_summary": {
                    "open_risks": [
                        "A required record is missing; the next decision must be a tool_call to selected.write_record."
                    ]
                },
            },
            "selection": {"selected_tools": ["selected.write_record"]},
        }
    )

    assert "state.mode is ACT" in prompt
    assert "next decision must be a tool_call" in prompt
    assert "finalize/pause/compact are invalid" in prompt
    assert "CURRENT OPEN RISKS:" in prompt
    assert (
        "A required record is missing; the next decision must be a tool_call to selected.write_record."
        in prompt.split("{", 1)[0]
    )


def test_native_appv22_provider_prompt_forbids_tool_calls_when_no_tools_selected():
    prompt = appv2_env_provider._appv22_decision_prompt(
        {
            "state": {"mode": "START", "context_summary": {}},
            "selection": {"selected_tools": []},
            "agent": {"request": "hi"},
        }
    )

    assert "NO SELECTED TOOLS:" in prompt
    assert "Do not emit kind=tool_call" in prompt
    assert "finalize" in prompt


def test_native_appv22_schema_does_not_expose_planner_kind():
    assert "plan" not in appv2_env_provider.APPV22_DECISION_SCHEMA["properties"]["kind"]["enum"]


def test_native_appv22_provider_returns_compact_for_invalid_json_without_leaking_raw_text():
    class FakeClient:
        def complete_json(self, *, stage, prompt, schema):
            return "not json tok_private"

    decision = AppV22NativeProvider(client=FakeClient()).decide({"state": {}})

    assert decision.kind == "compact"
    assert decision.payload["error_type"] == "invalid_provider_json"
    assert "tok_private" not in json.dumps(decision.to_dict(), sort_keys=True)
    assert "invalid JSON" in decision.reason


def test_appv2_env_factory_discovers_local_appv21_transport_path(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    appv21_root = repo / "appV2.1"
    (appv21_root / "appv21" / "providers").mkdir(parents=True)
    adapter_file = repo / "appV2.2" / "appv22" / "providers" / "appv2_env.py"
    adapter_file.parent.mkdir(parents=True)
    adapter_file.write_text("# adapter anchor\n", encoding="utf-8")
    original_sys_path = list(sys.path)
    captured: dict[str, object] = {}

    class FakeClient:
        def complete_json(self, *, stage, prompt, schema):
            return '{"kind":"pause","reason":"x","evidence_refs":[],"payload":{}}'

    def fake_find_spec(name: str):
        assert name == "appv21"
        return None

    def fake_import_module(name: str):
        captured["path_added"] = str(appv21_root) in sys.path
        if name == "appv21.providers.env_config":
            return SimpleNamespace(
                build_appv21_model_client=lambda prefix, *, dotenv_path: FakeClient()
            )
        if name == "appv21.providers.null_model":
            return SimpleNamespace(NullModelProvider=lambda: object())
        raise AssertionError(name)

    monkeypatch.setattr(appv2_env_provider, "__file__", str(adapter_file))
    monkeypatch.setattr(importlib_util, "find_spec", fake_find_spec)
    monkeypatch.setattr(appv2_env_provider, "import_module", fake_import_module)

    try:
        provider = create_appv22_provider_from_appv2_env(dotenv_path=".env")
    finally:
        sys.path[:] = original_sys_path

    assert captured["path_added"] is True
    assert provider.provider_id == "appv2-env-worker-appv22-native"
