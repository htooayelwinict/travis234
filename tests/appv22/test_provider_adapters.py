import importlib.util as importlib_util
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "appV2.2"))

import appv22.providers.appv2_env as appv2_env_provider
from appv22.providers.appv2_env import (
    AppV2EnvAppV22ProviderAdapter,
    create_appv22_provider_from_appv2_env,
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


def test_appv2_env_adapter_coerces_premature_plan_to_prompt_visible_tool():
    class DelegateProvider:
        provider_id = "appv2-env-worker"

        def decide(self, _prompt):
            return {
                "decision_id": "dec_plan_too_early",
                "kind": "plan",
                "reason": "try planning first",
                "payload": {},
                "evidence_refs": [],
            }

    adapter = AppV2EnvAppV22ProviderAdapter(DelegateProvider())

    decision = adapter.decide(
        {
            "world": {"world_refs": {}},
            "selection": {"selected_tools": ["extension.snapshot"]},
            "state": {"runtime_plan": {}, "mutation_receipts": {}, "verification_receipts": {}},
        }
    )

    assert decision.kind == "tool_call"
    assert decision.payload == {"tool_id": "extension.snapshot", "arguments": {}}


def test_appv2_env_adapter_coerces_context_request_tool_call_to_snapshot():
    class DelegateProvider:
        provider_id = "appv2-env-worker"

        def decide(self, _prompt):
            return {
                "decision_id": "dec_context_request",
                "kind": "tool_call",
                "reason": "need context",
                "payload": {"action": "request_initial_context"},
                "evidence_refs": [],
            }

    adapter = AppV2EnvAppV22ProviderAdapter(DelegateProvider())

    decision = adapter.decide(
        {
            "world": {"world_refs": {}},
            "selection": {"selected_tools": ["file_management.repo_snapshot"]},
            "state": {"runtime_plan": {}, "mutation_receipts": {}, "verification_receipts": {}},
            "skills": [
                {
                    "tool_ids": ("file_management.repo_snapshot",),
                    "observation_contract": {
                        "evidence_refs": ("world://repo_snapshot/latest",),
                        "preferred_tool_id": "file_management.repo_snapshot",
                    },
                }
            ],
        }
    )

    assert decision.kind == "tool_call"
    assert decision.payload == {"tool_id": "file_management.repo_snapshot", "arguments": {}}


def test_appv2_env_adapter_repairs_context_request_even_when_summary_evidence_exists():
    class DelegateProvider:
        provider_id = "appv2-env-worker"

        def decide(self, _prompt):
            return {
                "decision_id": "dec_context_request_after_compaction",
                "kind": "tool_call",
                "reason": "lost evidence after compaction",
                "payload": {"next_step": "request_observation"},
                "evidence_refs": [],
            }

    adapter = AppV2EnvAppV22ProviderAdapter(DelegateProvider())

    decision = adapter.decide(
        {
            "world": {
                "world_refs": {
                    "world://repo_snapshot/latest": {"kind": "file_management.repo_snapshot"}
                }
            },
            "messages": [
                {
                    "name": "context_summary",
                    "summary": {"evidence_refs": ["world://repo_snapshot/latest"]},
                }
            ],
            "selection": {"selected_tools": ["file_management.repo_snapshot"]},
            "state": {"runtime_plan": {}, "mutation_receipts": {}, "verification_receipts": {}},
        }
    )

    assert decision.kind == "tool_call"
    assert decision.payload == {"tool_id": "file_management.repo_snapshot", "arguments": {}}


def test_appv2_env_adapter_does_not_own_runtime_phase_progression():
    class DelegateProvider:
        provider_id = "appv2-env-worker"

        def __init__(self):
            self.calls = 0

        def decide(self, _prompt):
            self.calls += 1
            return {
                "decision_id": "dec_delegate_pause",
                "kind": "pause",
                "reason": "delegate still owns model response only",
                "payload": {},
                "evidence_refs": [],
            }

    delegate = DelegateProvider()
    adapter = AppV2EnvAppV22ProviderAdapter(delegate)

    decision = adapter.decide(
        {
            "state": {
                "runtime_plan": {
                    "mutation_intent": {
                        "operation_batch_id": "batch",
                        "operations": [{"action": "write", "path": "docs/a.md", "content": "a"}],
                    }
                },
                "mutation_receipts": {},
                "verification_receipts": {},
            },
            "world": {"world_refs": {"world://repo_snapshot/latest": {}}},
            "selection": {"selected_tools": ["file_management.repo_snapshot"]},
        }
    )

    assert delegate.calls == 1
    assert decision.kind == "pause"


def test_appv22_adapter_does_not_reobserve_when_summary_satisfies_observation_contract() -> None:
    prompt = {
        "state": {"runtime_plan": {}, "mutation_receipts": {}, "verification_receipts": {}},
        "world": {"world_refs": {}},
        "messages": [
            {
                "role": "system",
                "name": "context_summary",
                "summary": {"evidence_refs": ["world://repo_snapshot/latest"]},
            }
        ],
        "selection": {
            "selected_tools": ["file_management.repo_snapshot", "file_management.read_file"],
        },
        "skills": [
            {
                "skill_id": "file_management.cleanup",
                "tool_ids": ("file_management.repo_snapshot", "file_management.read_file"),
                "observation_contract": {
                    "evidence_refs": ("world://repo_snapshot/latest",),
                    "evidence_kinds": ("file_management.repo_snapshot",),
                    "preferred_tool_id": "file_management.repo_snapshot",
                },
            }
        ],
    }
    decision = RuntimeDecision("plan", "summary evidence exists", {}, ["world://repo_snapshot/latest"])

    coerced = appv2_env_provider._coerce_appv22_progression(prompt, decision)

    assert coerced.kind == "plan"
    assert coerced.reason == "summary evidence exists"


def test_appv22_adapter_suppresses_legacy_observe_replay_when_summary_satisfies_contract() -> None:
    prompt = {
        "state": {"runtime_plan": {}, "mutation_receipts": {}, "verification_receipts": {}},
        "world": {"world_refs": {}},
        "messages": [
            {
                "role": "system",
                "name": "context_summary",
                "summary": {"evidence_refs": ["world://repo_snapshot/latest"]},
            }
        ],
        "selection": {
            "selected_tools": ["file_management.repo_snapshot", "file_management.read_file"],
        },
        "skills": [
            {
                "skill_id": "file_management.cleanup",
                "tool_ids": ("file_management.repo_snapshot", "file_management.read_file"),
                "observation_contract": {
                    "evidence_refs": ("world://repo_snapshot/latest",),
                    "evidence_kinds": ("file_management.repo_snapshot",),
                    "preferred_tool_id": "file_management.repo_snapshot",
                },
            }
        ],
    }
    decision = RuntimeDecision(
        "tool_call",
        "legacy observe replay",
        {"tool_id": "file_management.repo_snapshot", "arguments": {}},
        [],
    )

    coerced = appv2_env_provider._coerce_appv22_progression(prompt, decision)

    assert coerced.kind == "plan"
    assert coerced.payload == {}
    assert coerced.evidence_refs == ["world://repo_snapshot/latest"]


def test_appv22_adapter_suppresses_satisfied_contract_read_tool_replay() -> None:
    prompt = {
        "state": {"runtime_plan": {}, "mutation_receipts": {}, "verification_receipts": {}},
        "world": {"world_refs": {}},
        "messages": [
            {"role": "system", "name": "context_summary", "summary": {"evidence_refs": ["world://repo_snapshot/latest"]}}
        ],
        "selection": {"selected_tools": ["file_management.repo_snapshot", "file_management.read_file"]},
        "skills": [
            {
                "skill_id": "file_management.cleanup",
                "tool_ids": ("file_management.repo_snapshot", "file_management.read_file"),
                "observation_contract": {
                    "evidence_refs": ("world://repo_snapshot/latest",),
                    "evidence_kinds": ("file_management.repo_snapshot",),
                    "preferred_tool_id": "file_management.repo_snapshot",
                },
            }
        ],
    }
    decision = RuntimeDecision(
        "tool_call",
        "legacy read replay",
        {"tool_id": "file_management.read_file", "arguments": {"path": "README.md"}},
        [],
    )

    coerced = appv2_env_provider._coerce_appv22_progression(prompt, decision)

    assert coerced.kind == "plan"
    assert coerced.evidence_refs == ["world://repo_snapshot/latest"]


def test_appv22_adapter_observes_when_contract_evidence_is_missing() -> None:
    prompt = {
        "state": {"runtime_plan": {}, "mutation_receipts": {}, "verification_receipts": {}},
        "world": {"world_refs": {}},
        "messages": [],
        "selection": {
            "selected_tools": ["file_management.repo_snapshot", "file_management.read_file"],
        },
        "skills": [
            {
                "skill_id": "file_management.cleanup",
                "tool_ids": ("file_management.repo_snapshot", "file_management.read_file"),
                "observation_contract": {
                    "evidence_refs": ("world://repo_snapshot/latest",),
                    "evidence_kinds": ("file_management.repo_snapshot",),
                    "preferred_tool_id": "file_management.repo_snapshot",
                },
            }
        ],
    }
    decision = RuntimeDecision("plan", "need observation", {}, [])

    coerced = appv2_env_provider._coerce_appv22_progression(prompt, decision)

    assert coerced.kind == "tool_call"
    assert coerced.payload == {"tool_id": "file_management.repo_snapshot", "arguments": {}}


def test_appv22_adapter_uses_later_selected_missing_observation_contract() -> None:
    prompt = {
        "state": {"runtime_plan": {}, "mutation_receipts": {}, "verification_receipts": {}},
        "world": {"world_refs": {}},
        "messages": [],
        "selection": {
            "selected_tools": ["selected.inventory_probe"],
        },
        "skills": [
            {
                "skill_id": "unselected.contract",
                "observation_contract": {
                    "evidence_refs": ("world://unselected/latest",),
                    "preferred_tool_id": "unselected.repo_snapshot",
                },
            },
            {
                "skill_id": "selected.contract",
                "observation_contract": {
                    "evidence_refs": ("world://selected/latest",),
                    "preferred_tool_id": "selected.inventory_probe",
                },
            },
        ],
    }
    decision = RuntimeDecision("plan", "need selected observation", {}, [])

    coerced = appv2_env_provider._coerce_appv22_progression(prompt, decision)

    assert coerced.kind == "tool_call"
    assert coerced.payload == {"tool_id": "selected.inventory_probe", "arguments": {}}


def test_appv22_adapter_ignores_unrelated_summary_evidence_for_observation_contract() -> None:
    prompt = {
        "state": {"runtime_plan": {}, "mutation_receipts": {}, "verification_receipts": {}},
        "world": {"world_refs": {}},
        "messages": [
            {
                "role": "system",
                "name": "context_summary",
                "summary": {"evidence_refs": ["world://other/latest"]},
            }
        ],
        "selection": {
            "selected_tools": ["file_management.repo_snapshot", "file_management.read_file"],
        },
        "skills": [
            {
                "skill_id": "file_management.cleanup",
                "tool_ids": ("file_management.repo_snapshot", "file_management.read_file"),
                "observation_contract": {
                    "evidence_refs": ("world://repo_snapshot/latest",),
                    "evidence_kinds": ("file_management.repo_snapshot",),
                    "preferred_tool_id": "file_management.repo_snapshot",
                },
            }
        ],
    }
    decision = RuntimeDecision("plan", "need observation", {}, [])

    coerced = appv2_env_provider._coerce_appv22_progression(prompt, decision)

    assert coerced.kind == "tool_call"
    assert coerced.payload["tool_id"] == "file_management.repo_snapshot"


def test_appv22_adapter_requires_contract_ref_when_ref_and_kind_are_declared() -> None:
    prompt = {
        "state": {"runtime_plan": {}, "mutation_receipts": {}, "verification_receipts": {}},
        "world": {
            "world_refs": {
                "world://old_snapshot/stale": {
                    "kind": "file_management.repo_snapshot",
                }
            }
        },
        "messages": [],
        "selection": {
            "selected_tools": ["file_management.repo_snapshot", "file_management.read_file"],
        },
        "skills": [
            {
                "skill_id": "file_management.cleanup",
                "tool_ids": ("file_management.repo_snapshot", "file_management.read_file"),
                "observation_contract": {
                    "evidence_refs": ("world://repo_snapshot/latest",),
                    "evidence_kinds": ("file_management.repo_snapshot",),
                    "preferred_tool_id": "file_management.repo_snapshot",
                },
            }
        ],
    }
    decision = RuntimeDecision("plan", "kind exists but required ref is absent", {}, [])

    coerced = appv2_env_provider._coerce_appv22_progression(prompt, decision)

    assert coerced.kind == "tool_call"
    assert coerced.payload == {"tool_id": "file_management.repo_snapshot", "arguments": {}}


def test_appv2_env_adapter_passes_runtime_plan_alias_without_phase_coercion():
    class DelegateProvider:
        provider_id = "appv2-env-worker"

        def __init__(self):
            self.prompt = None

        def decide(self, prompt):
            self.prompt = prompt
            return {
                "decision_id": "dec_plan_again",
                "kind": "plan",
                "reason": "plan again",
                "payload": {},
                "evidence_refs": [],
            }

    delegate = DelegateProvider()
    adapter = AppV2EnvAppV22ProviderAdapter(delegate)
    runtime_plan = {
        "mutation_intent": {
            "operation_batch_id": "batch_1",
            "operations": [{"action": "write", "path": "docs/workspace_manifest.json", "content": "{}"}],
        }
    }

    decision = adapter.decide(
        {
            "world": {"world_refs": {"world://snapshot/latest": {"kind": "snapshot"}}},
            "selection": {"selected_tools": ["extension.snapshot"]},
            "state": {"runtime_plan": runtime_plan, "mutation_receipts": {}, "verification_receipts": {}},
        }
    )

    assert delegate.prompt["state"]["plan"] == {"runtime_plan": runtime_plan}
    assert decision.kind == "plan"
    assert decision.payload == {}


def test_appv2_env_factory_discovers_local_appv21_sibling_path(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    appv21_root = repo / "appV2.1"
    (appv21_root / "appv21" / "providers").mkdir(parents=True)
    adapter_file = repo / "appV2.2" / "appv22" / "providers" / "appv2_env.py"
    adapter_file.parent.mkdir(parents=True)
    adapter_file.write_text("# adapter anchor\n", encoding="utf-8")
    original_sys_path = list(sys.path)
    captured: dict[str, object] = {}

    class Delegate:
        provider_id = "fake-appv21"

    def fake_find_spec(name: str):
        assert name == "appv21"
        return None

    def fake_import_module(name: str):
        captured["path_added"] = str(appv21_root) in sys.path
        assert name == "appv21.providers.appv2_env"
        return SimpleNamespace(
            create_appv21_provider_from_appv2_env=lambda *, dotenv_path: Delegate()
        )

    monkeypatch.setattr(appv2_env_provider, "__file__", str(adapter_file))
    monkeypatch.setattr(importlib_util, "find_spec", fake_find_spec)
    monkeypatch.setattr(appv2_env_provider, "import_module", fake_import_module)

    try:
        adapter = create_appv22_provider_from_appv2_env(dotenv_path=".env")
    finally:
        sys.path[:] = original_sys_path

    assert captured["path_added"] is True
    assert adapter.provider_id == "fake-appv21-appv22-adapter"
