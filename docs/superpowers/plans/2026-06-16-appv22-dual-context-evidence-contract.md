# AppV2.2 Dual Context Evidence Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the dual-compaction observe loop by making runtime progression read semantic evidence from both raw world context and compacted summary context.

**Architecture:** Add a generic `ContextEvidence` reader, add domain-owned observation contracts to active skill cards, then update the AppV2-env adapter to coerce observation only when required contract evidence is absent. The compressor remains domain-neutral: it preserves evidence and compact control-plane contracts, but it does not interpret file-management semantics.

**Tech Stack:** Python, pytest, AppV2.2 runtime/provider adapter, existing AppV2.1 appv2-env LLM provider bridge.

---

## File Structure

- Create: `appV2.2/appv22/context/evidence.py`
  - Owns generic prompt evidence extraction from raw `world_refs`, compacted `messages[].summary.evidence_refs`, and optional future `state.context_summary`.
- Modify: `appV2.2/appv22/extensions/base.py`
  - Adds `ObservationContract` and optional `SkillCard.observation_contract`.
- Modify: `appV2.2/appv22/extensions/file_management/skills.py`
  - Declares file-management observation contract using `world://repo_snapshot/latest` and `file_management.repo_snapshot`.
- Modify: `appV2.2/appv22/context/compressor.py`
  - Preserves compact `observation_contract` in compacted skill payloads.
- Modify: `appV2.2/appv22/providers/appv2_env.py`
  - Replaces raw `world_refs`-only progression check with contract-aware `ContextEvidence`.
- Modify: `scripts/live_appv22_dual_compaction_rehydration_probe.py`
  - Updates proof matrix to assert no repeated forced observation after compacted summary evidence appears.
- Test: `tests/appv22/test_context_evidence.py`
  - Covers evidence extraction and malformed prompt safety.
- Test: `tests/appv22/test_provider_adapters.py`
  - Covers adapter coercion behavior with raw evidence, compacted evidence, missing evidence, and unrelated evidence.
- Test: `tests/appv22/test_agent_loop_extension_runtime.py`
  - Keeps existing dual-compaction and rehydration proofs green.

---

## Phase 1: Generic Context Evidence Reader

**Purpose:** Establish a domain-neutral interface for asking “does this prompt contain evidence X?” regardless of whether evidence is raw or compacted.

**Files:**
- Create: `appV2.2/appv22/context/evidence.py`
- Test: `tests/appv22/test_context_evidence.py`

- [ ] **Step 1: Write failing tests for raw and compacted evidence extraction**

Create `tests/appv22/test_context_evidence.py`:

```python
from appv22.context.evidence import ContextEvidence


def test_context_evidence_reads_raw_world_refs() -> None:
    prompt = {
        "world": {
            "world_refs": {
                "world://repo_snapshot/latest": {
                    "ref_id": "world://repo_snapshot/latest",
                    "kind": "file_management.repo_snapshot",
                    "summary": "file_management.repo_snapshot result",
                }
            }
        },
        "messages": [],
    }

    evidence = ContextEvidence.from_prompt(prompt)

    assert evidence.has_ref("world://repo_snapshot/latest")
    assert evidence.has_kind("file_management.repo_snapshot")
    assert evidence.refs == ("world://repo_snapshot/latest",)
    assert evidence.kinds == ("file_management.repo_snapshot",)


def test_context_evidence_reads_compacted_summary_refs() -> None:
    prompt = {
        "world": {"world_refs": {}},
        "messages": [
            {
                "role": "system",
                "name": "context_summary",
                "summary": {
                    "evidence_refs": ["world://repo_snapshot/latest"],
                    "progress": ["file_management.repo_snapshot result"],
                },
            }
        ],
    }

    evidence = ContextEvidence.from_prompt(prompt)

    assert evidence.has_ref("world://repo_snapshot/latest")
    assert not evidence.has_kind("file_management.repo_snapshot")


def test_context_evidence_deduplicates_refs_across_layers() -> None:
    prompt = {
        "world": {
            "world_refs": {
                "world://repo_snapshot/latest": {
                    "ref_id": "world://repo_snapshot/latest",
                    "kind": "file_management.repo_snapshot",
                }
            }
        },
        "messages": [
            {
                "role": "system",
                "name": "context_summary",
                "summary": {"evidence_refs": ["world://repo_snapshot/latest"]},
            }
        ],
    }

    evidence = ContextEvidence.from_prompt(prompt)

    assert evidence.refs == ("world://repo_snapshot/latest",)
    assert evidence.kinds == ("file_management.repo_snapshot",)


def test_context_evidence_ignores_malformed_summary_without_crashing() -> None:
    prompt = {
        "world": {"world_refs": "not-a-dict"},
        "messages": [
            {"role": "system", "name": "context_summary", "summary": "not-a-dict"},
            {"role": "system", "name": "context_summary", "summary": {"evidence_refs": "not-a-list"}},
        ],
        "state": {"context_summary": "not-a-dict"},
    }

    evidence = ContextEvidence.from_prompt(prompt)

    assert evidence.refs == ()
    assert evidence.kinds == ()
```

- [ ] **Step 2: Run Phase 1 tests to verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_context_evidence.py -q
```

Expected:

```text
ModuleNotFoundError: No module named 'appv22.context.evidence'
```

- [ ] **Step 3: Implement minimal `ContextEvidence`**

Create `appV2.2/appv22/context/evidence.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContextEvidence:
    refs: tuple[str, ...]
    kinds: tuple[str, ...]

    @classmethod
    def from_prompt(cls, prompt: dict[str, Any]) -> "ContextEvidence":
        refs: list[str] = []
        kinds: list[str] = []
        _collect_world_refs(prompt, refs, kinds)
        _collect_message_summary_refs(prompt, refs)
        _collect_state_summary_refs(prompt, refs)
        return cls(refs=_dedupe(refs), kinds=_dedupe(kinds))

    def has_ref(self, ref_id: str) -> bool:
        return ref_id in self.refs

    def has_kind(self, kind: str) -> bool:
        return kind in self.kinds

    def has_any_ref(self, ref_ids: tuple[str, ...] | list[str]) -> bool:
        return any(ref_id in self.refs for ref_id in ref_ids)

    def has_any_kind(self, kinds: tuple[str, ...] | list[str]) -> bool:
        return any(kind in self.kinds for kind in kinds)


def _collect_world_refs(prompt: dict[str, Any], refs: list[str], kinds: list[str]) -> None:
    world = prompt.get("world")
    if not isinstance(world, dict):
        return
    world_refs = world.get("world_refs")
    if not isinstance(world_refs, dict):
        return
    for fallback_ref_id, world_ref in world_refs.items():
        if not isinstance(world_ref, dict):
            continue
        ref_id = world_ref.get("ref_id") or fallback_ref_id
        kind = world_ref.get("kind")
        if ref_id:
            refs.append(str(ref_id))
        if kind:
            kinds.append(str(kind))


def _collect_message_summary_refs(prompt: dict[str, Any], refs: list[str]) -> None:
    messages = prompt.get("messages")
    if not isinstance(messages, list):
        return
    for message in messages:
        if not isinstance(message, dict):
            continue
        summary = message.get("summary")
        if not isinstance(summary, dict):
            continue
        evidence_refs = summary.get("evidence_refs")
        if not isinstance(evidence_refs, list):
            continue
        refs.extend(str(ref) for ref in evidence_refs if ref)


def _collect_state_summary_refs(prompt: dict[str, Any], refs: list[str]) -> None:
    state = prompt.get("state")
    if not isinstance(state, dict):
        return
    summary = state.get("context_summary")
    if not isinstance(summary, dict):
        return
    evidence_refs = summary.get("evidence_refs")
    if not isinstance(evidence_refs, list):
        return
    refs.extend(str(ref) for ref in evidence_refs if ref)


def _dedupe(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return tuple(deduped)
```

- [ ] **Step 4: Run Phase 1 tests to verify GREEN**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_context_evidence.py -q
```

Expected:

```text
4 passed
```

- [ ] **Step 5: Commit Phase 1**

Run:

```bash
git add appV2.2/appv22/context/evidence.py tests/appv22/test_context_evidence.py
git commit -m "feat(appv22): add generic context evidence reader"
```

---

## Phase 2: Skill Observation Contracts

**Purpose:** Move domain-specific observation requirements into extensions, while preserving a compact version of that contract through dual compaction.

**Files:**
- Modify: `appV2.2/appv22/extensions/base.py`
- Modify: `appV2.2/appv22/extensions/file_management/skills.py`
- Modify: `appV2.2/appv22/context/compressor.py`
- Test: `tests/appv22/test_extension_registry.py`
- Test: `tests/appv22/test_agent_loop_extension_runtime.py`

- [ ] **Step 1: Write failing test for file-management observation contract**

Add to `tests/appv22/test_extension_registry.py`:

```python
from appv22.extensions.file_management.skills import FILE_MANAGEMENT_SKILL


def test_file_management_skill_declares_observation_contract() -> None:
    contract = FILE_MANAGEMENT_SKILL.observation_contract

    assert contract is not None
    assert contract.evidence_refs == ("world://repo_snapshot/latest",)
    assert contract.evidence_kinds == ("file_management.repo_snapshot",)
    assert contract.preferred_tool_id == "file_management.repo_snapshot"
```

- [ ] **Step 2: Run observation-contract test to verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_extension_registry.py::test_file_management_skill_declares_observation_contract -q
```

Expected:

```text
AttributeError: 'SkillCard' object has no attribute 'observation_contract'
```

- [ ] **Step 3: Add `ObservationContract` to skill base**

Modify `appV2.2/appv22/extensions/base.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from appv22.state.models import AgentState


@dataclass(frozen=True)
class ObservationContract:
    evidence_refs: tuple[str, ...] = ()
    evidence_kinds: tuple[str, ...] = ()
    preferred_tool_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_refs", tuple(self.evidence_refs))
        object.__setattr__(self, "evidence_kinds", tuple(self.evidence_kinds))


@dataclass(frozen=True)
class SkillCard:
    skill_id: str
    extension_id: str
    triggers: tuple[str, ...]
    modes: tuple[str, ...]
    summary: str
    planner_id: str
    mutation_policy_id: str
    mutation_executor_id: str
    verifier_id: str
    tool_ids: tuple[str, ...]
    artifact_schema_ids: tuple[str, ...]
    observation_contract: ObservationContract | None = None

    def __post_init__(self) -> None:
        for field_name in ("triggers", "modes", "tool_ids", "artifact_schema_ids"):
            object.__setattr__(self, field_name, tuple(getattr(self, field_name)))

    def activates_for(self, state: AgentState) -> bool:
        if self.modes and state.mode not in self.modes:
            return False
        text = state.request.user_goal.lower()
        return any(trigger.lower() in text for trigger in self.triggers)


class RuntimeExtension(Protocol):
    extension_id: str

    def skill_cards(self) -> list[SkillCard]:
        ...

    def register_capabilities(self, capabilities: object) -> None:
        ...
```

- [ ] **Step 4: Add file-management observation contract**

Modify `appV2.2/appv22/extensions/file_management/skills.py`:

```python
from __future__ import annotations

from appv22.extensions.base import ObservationContract, SkillCard

FILE_MANAGEMENT_SKILL = SkillCard(
    skill_id="file_management.cleanup",
    extension_id="file_management",
    triggers=(
        "clean",
        "cleanup",
        "organize",
        "mess",
        "tidy",
        "workspace",
        "clutter",
        "sane",
        "record",
    ),
    modes=("START", "THINK", "OBSERVE", "PLAN", "ACT", "VERIFY"),
    summary="Safely organize workspace files and record moves, held paths, and collisions.",
    planner_id="file_management.cleanup_planner",
    mutation_policy_id="file_management.safe_file_moves",
    mutation_executor_id="file_management.file_mutation_executor",
    verifier_id="file_management.manifest_verifier",
    tool_ids=("file_management.repo_snapshot", "file_management.read_file"),
    artifact_schema_ids=("file_management.workspace_manifest",),
    observation_contract=ObservationContract(
        evidence_refs=("world://repo_snapshot/latest",),
        evidence_kinds=("file_management.repo_snapshot",),
        preferred_tool_id="file_management.repo_snapshot",
    ),
)
```

- [ ] **Step 5: Run observation-contract test to verify GREEN**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_extension_registry.py::test_file_management_skill_declares_observation_contract -q
```

Expected:

```text
1 passed
```

- [ ] **Step 6: Write failing test that compacted skill payload preserves observation contract**

Add to `tests/appv22/test_agent_loop_extension_runtime.py`:

```python
def test_dual_context_preserves_compact_observation_contract(tmp_path):
    raw_marker = "RAW_OBSERVATION_CONTRACT_SENTINEL"
    noisy_root = tmp_path / "incoming"
    noisy_root.mkdir()
    for index in range(100):
        (noisy_root / f"{raw_marker}_{index:03d}.md").write_text("x", encoding="utf-8")
    provider = SequenceProvider(
        [
            RuntimeDecision(
                "tool_call",
                "observe workspace",
                {"tool_id": "file_management.repo_snapshot", "arguments": {}},
            ),
            RuntimeDecision("pause", "inspect compacted prompt"),
        ]
    )
    services = create_appv22_services(
        root_path=tmp_path,
        provider=provider,
        extensions=[FileManagementExtension()],
    )
    services.gateway_guard = GatewayContextGuard(max_chars=50_000, threshold=1.0)
    services.compressor = AgentContextCompressor(max_chars=2_800, threshold=0.50)

    AppV22AgentRuntime(root_path=tmp_path, services=services, max_turns=2).run(
        "make this workspace sane and keep a record"
    )

    skills = provider.prompts[1]["skills"]
    assert skills
    assert skills[0]["observation_contract"] == {
        "evidence_refs": ("world://repo_snapshot/latest",),
        "evidence_kinds": ("file_management.repo_snapshot",),
        "preferred_tool_id": "file_management.repo_snapshot",
    }
```

- [ ] **Step 7: Run compact-contract test to verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_agent_loop_extension_runtime.py::test_dual_context_preserves_compact_observation_contract -q
```

Expected:

```text
KeyError: 'observation_contract'
```

- [ ] **Step 8: Preserve compact observation contract in compressor**

Modify the `skills` branch inside `_compact_preserved_context_section()` in `appV2.2/appv22/context/compressor.py` so each retained skill includes:

```python
"observation_contract": skill.get("observation_contract"),
```

The compact skill dictionary should become:

```python
{
    "skill_id": skill.get("skill_id"),
    "extension_id": skill.get("extension_id"),
    "summary": str(skill.get("summary", ""))[:240],
    "tool_ids": skill.get("tool_ids", ()),
    "observation_contract": skill.get("observation_contract"),
}
```

- [ ] **Step 9: Run Phase 2 tests to verify GREEN**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_extension_registry.py::test_file_management_skill_declares_observation_contract tests/appv22/test_agent_loop_extension_runtime.py::test_dual_context_preserves_compact_observation_contract -q
```

Expected:

```text
2 passed
```

- [ ] **Step 10: Commit Phase 2**

Run:

```bash
git add appV2.2/appv22/extensions/base.py appV2.2/appv22/extensions/file_management/skills.py appV2.2/appv22/context/compressor.py tests/appv22/test_extension_registry.py tests/appv22/test_agent_loop_extension_runtime.py
git commit -m "feat(appv22): add skill observation contracts"
```

---

## Phase 3: Contract-Aware Adapter Progression and Live Proof

**Purpose:** Stop observe loops after compaction by making the AppV2-env adapter use generic evidence and active observation contracts.

**Files:**
- Modify: `appV2.2/appv22/providers/appv2_env.py`
- Modify: `scripts/live_appv22_dual_compaction_rehydration_probe.py`
- Test: `tests/appv22/test_provider_adapters.py`

- [ ] **Step 1: Write failing adapter test for compacted evidence satisfying observation contract**

Add to `tests/appv22/test_provider_adapters.py`:

```python
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
```

- [ ] **Step 2: Write failing adapter test for missing evidence still observing**

Add to `tests/appv22/test_provider_adapters.py`:

```python
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
```

- [ ] **Step 3: Write failing adapter test for unrelated evidence not satisfying contract**

Add to `tests/appv22/test_provider_adapters.py`:

```python
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
```

- [ ] **Step 4: Run Phase 3 adapter tests to verify RED**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_provider_adapters.py::test_appv22_adapter_does_not_reobserve_when_summary_satisfies_observation_contract tests/appv22/test_provider_adapters.py::test_appv22_adapter_observes_when_contract_evidence_is_missing tests/appv22/test_provider_adapters.py::test_appv22_adapter_ignores_unrelated_summary_evidence_for_observation_contract -q
```

Expected:

```text
at least test_appv22_adapter_does_not_reobserve_when_summary_satisfies_observation_contract fails because current adapter checks only raw world_refs
```

- [ ] **Step 5: Implement contract-aware progression helper**

Modify `appV2.2/appv22/providers/appv2_env.py`.

Add import:

```python
from appv22.context.evidence import ContextEvidence
```

Replace the first branch in `_coerce_appv22_progression()`:

```python
    world_refs = _world_refs(prompt)
    selected_tools = _selected_tools(prompt)
    if not world_refs and selected_tools:
        return RuntimeDecision(
            kind="tool_call",
            reason="Observe prompt-visible context before planning.",
            payload={"tool_id": selected_tools[0], "arguments": {}},
            evidence_refs=[],
        )
```

with:

```python
    selected_tools = _selected_tools(prompt)
    missing_observation_tool = _missing_observation_tool(prompt, selected_tools)
    if missing_observation_tool is not None:
        return RuntimeDecision(
            kind="tool_call",
            reason="Observe prompt-visible context before planning.",
            payload={"tool_id": missing_observation_tool, "arguments": {}},
            evidence_refs=[],
        )
```

Add helpers:

```python
def _missing_observation_tool(prompt: dict, selected_tools: list[str]) -> str | None:
    if not selected_tools:
        return None
    contracts = _observation_contracts(prompt)
    if not contracts:
        return selected_tools[0] if not _world_refs(prompt) else None
    evidence = ContextEvidence.from_prompt(prompt)
    for contract in contracts:
        if _contract_satisfied(contract, evidence):
            continue
        preferred_tool_id = contract.get("preferred_tool_id")
        if isinstance(preferred_tool_id, str) and preferred_tool_id in selected_tools:
            return preferred_tool_id
    return None


def _observation_contracts(prompt: dict) -> list[dict[str, Any]]:
    skills = prompt.get("skills")
    if not isinstance(skills, list):
        return []
    contracts: list[dict[str, Any]] = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        contract = skill.get("observation_contract")
        if isinstance(contract, dict):
            contracts.append(contract)
    return contracts


def _contract_satisfied(contract: dict[str, Any], evidence: ContextEvidence) -> bool:
    evidence_refs = contract.get("evidence_refs")
    evidence_kinds = contract.get("evidence_kinds")
    ref_values = tuple(str(ref) for ref in evidence_refs) if isinstance(evidence_refs, (list, tuple)) else ()
    kind_values = tuple(str(kind) for kind in evidence_kinds) if isinstance(evidence_kinds, (list, tuple)) else ()
    if ref_values and evidence.has_any_ref(ref_values):
        return True
    if kind_values and evidence.has_any_kind(kind_values):
        return True
    return not ref_values and not kind_values
```

- [ ] **Step 6: Run Phase 3 adapter tests to verify GREEN**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_provider_adapters.py::test_appv22_adapter_does_not_reobserve_when_summary_satisfies_observation_contract tests/appv22/test_provider_adapters.py::test_appv22_adapter_observes_when_contract_evidence_is_missing tests/appv22/test_provider_adapters.py::test_appv22_adapter_ignores_unrelated_summary_evidence_for_observation_contract -q
```

Expected:

```text
3 passed
```

- [ ] **Step 7: Update live probe proof matrix for no repeated forced observation**

Modify `scripts/live_appv22_dual_compaction_rehydration_probe.py`.

Add this proof value inside `proof`:

```python
"no_reobserve_after_summary_evidence": not any(
    row["turn"] > 1
    and row["summary_evidence_ref_count"] > 0
    and decision.get("kind") == "tool_call"
    and (decision.get("payload") or {}).get("tool_id") == "file_management.repo_snapshot"
    for row, decision in zip(prompt_matrix, decisions, strict=False)
),
```

Update return condition:

```python
return 0 if (
    report["proof"]["dual_compaction_carried"]
    and report["proof"]["rehydration_attempted"]
    and report["proof"]["no_reobserve_after_summary_evidence"]
) else 1
```

- [ ] **Step 8: Run focused deterministic regression tests**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22/test_context_evidence.py tests/appv22/test_provider_adapters.py tests/appv22/test_agent_loop_extension_runtime.py::test_dual_context_compacts_large_world_context_and_carries_summary_to_next_turn tests/appv22/test_agent_loop_extension_runtime.py::test_dual_context_allows_tool_rehydration_after_compaction -q
```

Expected:

```text
all selected tests pass
```

- [ ] **Step 9: Run full AppV2.2 suite**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22 -q
```

Expected:

```text
all AppV2.2 tests pass
```

- [ ] **Step 10: Run live LLM proof outside sandbox**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run python scripts/live_appv22_dual_compaction_rehydration_probe.py --dotenv /Users/htooayelwin/lewis/allthebest/.env --run-timeout-seconds 180 --worker-timeout 60
```

Expected JSON includes:

```json
{
  "proof": {
    "dual_compaction_carried": true,
    "summary_carries_evidence": true,
    "raw_marker_leaked_after_compaction": false,
    "no_reobserve_after_summary_evidence": true
  }
}
```

- [ ] **Step 11: Commit Phase 3**

Run:

```bash
git add appV2.2/appv22/providers/appv2_env.py scripts/live_appv22_dual_compaction_rehydration_probe.py tests/appv22/test_provider_adapters.py
git commit -m "fix(appv22): make adapter progression evidence-aware"
```

---

## Final Verification

- [ ] **Step 1: Run complete AppV2.2 test suite**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv22 -q
```

Expected:

```text
all AppV2.2 tests pass
```

- [ ] **Step 2: Run live dual-compaction proof**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run python scripts/live_appv22_dual_compaction_rehydration_probe.py --dotenv /Users/htooayelwin/lewis/allthebest/.env --run-timeout-seconds 180 --worker-timeout 60
```

Expected:

```text
exit code 0
proof.dual_compaction_carried == true
proof.no_reobserve_after_summary_evidence == true
```

- [ ] **Step 3: Inspect final live report matrix**

Open:

```bash
cat plan/live-appv22-dual-compaction-rehydration-probe.appv2-env.json
```

Expected:

```text
prompt_matrix turn > 1 has summary_evidence_ref_count >= 1
decision_matrix does not repeatedly force file_management.repo_snapshot after summary evidence exists
raw_model_decision_matrix shows model saw compacted evidence
```

---

## Self-Review

- Spec coverage: Covers generic evidence reader, skill-owned observation contract, compacted contract preservation, adapter progression, deterministic tests, full suite, and live LLM proof.
- Placeholder scan: No TBD/TODO placeholders remain.
- Type consistency: `ObservationContract`, `ContextEvidence`, `observation_contract`, `evidence_refs`, `evidence_kinds`, and `preferred_tool_id` are used consistently across all phases.
- Scope check: This is one focused subsystem fix. It does not attempt planner redesign, broader context memory, or non-file-management extension implementation.
