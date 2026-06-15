# AppV2.1 Context Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Pi + Hermes inspired context governor to AppV2.1 so tools, skills, world refs, and completed job state stay bounded and evidence-safe across agent-loop turns.

**Architecture:** Keep `AppV21AgentRuntime` as the facade. Add focused context services for budgets, selection, skill cards, mode-filtered tool specs, run-memory compaction, and overflow recovery. Preserve AppV2.1 Gate A/B invariants: state-machine legality, decision validation, registry-backed broker tools, raw payload evidence refs, mutation leases, and verification receipts.

**Tech Stack:** Python 3.13, dataclasses, pytest, existing AppV2.1 runtime under `appV2.1/appv21`, live probe scripts under `scripts/`.

---

## File Structure

Create:

- `appV2.1/appv21/context/budget.py` - section budget definitions and deterministic size estimation.
- `appV2.1/appv21/context/selector.py` - mode-aware prompt context selection.
- `appV2.1/appv21/context/run_memory.py` - finalize-time run memory artifact builder.
- `appV2.1/appv21/context/overflow.py` - provider context overflow classification and retry policy.
- `appV2.1/appv21/extensions/skill_registry.py` - structured skill cards and activation.
- `tests/appv21/test_context_governance.py` - budget, selector, skills, run memory, and prompt event tests.

Modify:

- `appV2.1/appv21/context/manager.py` - delegate selection and budget metadata to new services.
- `appV2.1/appv21/context/prompt_builder.py` - consume selected context payload instead of raw unbounded state.
- `appV2.1/appv21/context/compactor.py` - preserve immutable refs and return stronger digest metadata.
- `appV2.1/appv21/extensions/skills.py` - delegate to `SkillRegistry` or become compatibility wrapper.
- `appV2.1/appv21/runtime/services.py` - compose new context/skill services.
- `appV2.1/appv21/runtime/agent_runtime.py` - emit richer prompt context events, use mode-filtered tool specs, emit run memory on finalize, and add overflow retry path.
- `appV2.1/appv21/state/models.py` - add optional context budget metadata fields only if needed by reducer tests.
- `scripts/live_appv21_staged_file_management_matrix_probe.py` - add context budget matrix fields.

---

### Task 1: Add Context Budget Estimation

**Files:**
- Create: `appV2.1/appv21/context/budget.py`
- Test: `tests/appv21/test_context_governance.py`

- [ ] **Step 1: Write failing tests**

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.1"))

from appv21.context.budget import ContextBudgetManager, DEFAULT_SECTION_BUDGETS


def test_context_budget_estimates_section_sizes() -> None:
    manager = ContextBudgetManager()
    payload = {"system": {"a": "b"}, "world": {"refs": [{"summary": "x" * 20}]}}

    estimate = manager.estimate(payload)

    assert estimate["sections"]["system"]["chars"] > 0
    assert estimate["sections"]["world"]["chars"] > 0
    assert estimate["total_chars"] >= estimate["sections"]["system"]["chars"]
    assert estimate["sections"]["world"]["budget"] == DEFAULT_SECTION_BUDGETS["world"]


def test_context_budget_marks_over_budget_sections() -> None:
    manager = ContextBudgetManager(section_budgets={"world": 10})

    estimate = manager.estimate({"world": {"summary": "x" * 100}})

    assert estimate["sections"]["world"]["over_budget"] is True
    assert estimate["over_budget_sections"] == ["world"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py::test_context_budget_estimates_section_sizes tests/appv21/test_context_governance.py::test_context_budget_marks_over_budget_sections -q
```

Expected: fail because `appv21.context.budget` does not exist.

- [ ] **Step 3: Implement budget manager**

Create `appV2.1/appv21/context/budget.py`:

```python
"""Prompt context section budgets for AppV2.1."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


DEFAULT_SECTION_BUDGETS: dict[str, int] = {
    "system": 1200,
    "agent": 1200,
    "skills": 1200,
    "tools": 1800,
    "world": 3500,
    "state": 1800,
    "output_contract": 1200,
    "decomposition": 800,
}


@dataclass(frozen=True)
class ContextBudgetManager:
    section_budgets: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_SECTION_BUDGETS))

    def estimate(self, payload: dict[str, Any]) -> dict[str, Any]:
        sections: dict[str, dict[str, Any]] = {}
        total = 0
        over_budget: list[str] = []
        for name, value in sorted(payload.items()):
            chars = len(json.dumps(value, sort_keys=True, default=str))
            budget = int(self.section_budgets.get(name, 2000))
            total += chars
            section = {"chars": chars, "budget": budget, "over_budget": chars > budget}
            sections[name] = section
            if section["over_budget"]:
                over_budget.append(name)
        return {"total_chars": total, "sections": sections, "over_budget_sections": over_budget}
```

- [ ] **Step 4: Run tests**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py::test_context_budget_estimates_section_sizes tests/appv21/test_context_governance.py::test_context_budget_marks_over_budget_sections -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add appV2.1/appv21/context/budget.py tests/appv21/test_context_governance.py
git commit -m "feat(appv21): add context budget estimator"
```

---

### Task 2: Add Structured Skill Registry

**Files:**
- Create: `appV2.1/appv21/extensions/skill_registry.py`
- Modify: `appV2.1/appv21/extensions/skills.py`
- Test: `tests/appv21/test_context_governance.py`

- [ ] **Step 1: Write failing skill tests**

Append to `tests/appv21/test_context_governance.py`:

```python
from appv21.extensions.skill_registry import SkillRegistry
from appv21.state.models import AgentState, RequestEnvelope


def make_state(tmp_path: Path, goal: str) -> AgentState:
    return AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(request_id="req", user_goal=goal, root_path=str(tmp_path)),
    )


def test_workspace_cleanup_skill_activates_as_card(tmp_path: Path) -> None:
    state = make_state(tmp_path, "Organize this workspace safely.")

    skills = SkillRegistry().active_skill_cards(state)

    assert [skill["skill_id"] for skill in skills] == ["workspace_cleanup"]
    assert skills[0]["budget_priority"] == 80
    assert "src/**" in skills[0]["preservation_rules"]
    assert "repo_snapshot" in skills[0]["tool_preferences"]
    assert "prompt_patch" not in skills[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py::test_workspace_cleanup_skill_activates_as_card -q
```

Expected: fail because `SkillRegistry` does not exist.

- [ ] **Step 3: Implement skill registry**

Create `appV2.1/appv21/extensions/skill_registry.py`:

```python
"""Structured skill cards for AppV2.1."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from appv21.state.models import AgentState


@dataclass(frozen=True)
class SkillCard:
    skill_id: str
    triggers: list[str]
    modes: list[str]
    summary: str
    tool_preferences: list[str] = field(default_factory=list)
    artifact_templates: list[str] = field(default_factory=list)
    preservation_rules: list[str] = field(default_factory=list)
    verification_hints: list[str] = field(default_factory=list)
    budget_priority: int = 50

    def to_prompt_card(self) -> dict:
        return asdict(self)


class SkillRegistry:
    def __init__(self) -> None:
        self._cards = [
            SkillCard(
                skill_id="workspace_cleanup",
                triggers=["cleanup", "organize", "move", "workspace"],
                modes=["THINK", "OBSERVE", "PLAN", "VERIFY"],
                summary="Organize workspace files from observed repo evidence while preserving protected paths.",
                tool_preferences=["repo_snapshot", "read_file"],
                artifact_templates=["workspace_manifest"],
                preservation_rules=[
                    "tests/**",
                    "src/**",
                    "assets/**",
                    "secrets/**",
                    "README.md",
                    "docs/**",
                    "**/keep*",
                    "**/do_not_move*",
                    "**/old_blob*",
                ],
                verification_hints=[
                    "Verify protected paths did not move.",
                    "Verify manifest records moves, held files, and collisions.",
                    "Verify secret content was not read.",
                ],
                budget_priority=80,
            )
        ]

    def active_skill_cards(self, state: AgentState) -> list[dict]:
        text = state.request.user_goal.lower()
        active = [card.to_prompt_card() for card in self._cards if any(trigger in text for trigger in card.triggers)]
        return sorted(active, key=lambda card: (-int(card["budget_priority"]), str(card["skill_id"])))
```

Modify `appV2.1/appv21/extensions/skills.py`:

```python
"""Skill router for AppV2.1."""

from __future__ import annotations

from appv21.extensions.skill_registry import SkillRegistry
from appv21.state.models import AgentState


class SkillRouter:
    def __init__(self, *, registry: SkillRegistry | None = None) -> None:
        self.registry = registry or SkillRegistry()

    def active_skills(self, state: AgentState) -> list[dict]:
        return self.registry.active_skill_cards(state)
```

- [ ] **Step 4: Run tests**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py::test_workspace_cleanup_skill_activates_as_card tests/appv21/test_agentic_loop_next_phase.py::test_model_tool_specs_only_expose_callable_tools -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add appV2.1/appv21/extensions/skill_registry.py appV2.1/appv21/extensions/skills.py tests/appv21/test_context_governance.py
git commit -m "feat(appv21): add structured skill cards"
```

---

### Task 3: Add Mode-Aware Context Selector

**Files:**
- Create: `appV2.1/appv21/context/selector.py`
- Test: `tests/appv21/test_context_governance.py`

- [ ] **Step 1: Write failing selector tests**

Append:

```python
from appv21.context.selector import ContextSelector
from appv21.state.models import WorldRef


def test_context_selector_preserves_repo_snapshot_and_latest_refs(tmp_path: Path) -> None:
    state = make_state(tmp_path, "Organize workspace.")
    state.world.refs["world://old"] = WorldRef("world://old", "tool_result", "old", {"tool_name": "read_file"}, "runtime_observed")
    state.world.refs["world://repo_snapshot/latest"] = WorldRef(
        "world://repo_snapshot/latest",
        "repo_snapshot",
        "snapshot",
        {"payload_ref": "world://tool_payload/toolres_1", "prompt_summary": {"file_count": 10}},
        "runtime_observed",
    )
    state.world.refs["world://latest"] = WorldRef("world://latest", "tool_result", "latest", {"tool_name": "read_file"}, "runtime_observed")

    selected = ContextSelector(max_world_refs=2).select(state=state, active_skills=[], tool_specs=[])

    ref_ids = [ref["ref_id"] for ref in selected["world"]["world_refs"]]
    assert "world://repo_snapshot/latest" in ref_ids
    assert "world://latest" in ref_ids
    assert selected["selection"]["mode"] == "START"
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py::test_context_selector_preserves_repo_snapshot_and_latest_refs -q
```

Expected: fail because selector does not exist.

- [ ] **Step 3: Implement selector**

Create `appV2.1/appv21/context/selector.py`:

```python
"""Mode-aware context selection for AppV2.1 prompts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from appv21.state.models import AgentState, WorldRef


@dataclass(frozen=True)
class ContextSelector:
    max_world_refs: int = 6

    def select(self, *, state: AgentState, active_skills: list[dict[str, Any]], tool_specs: list[dict[str, Any]]) -> dict[str, Any]:
        refs = self._select_world_refs(state)
        return {
            "state": {
                "mode": state.mode,
                "plan": state.plan.__dict__ if state.plan is not None else None,
                "mutation_receipts": list(state.world.mutation_receipts),
                "verification_receipts": list(state.world.verification_receipts),
                "pauses": [pause.__dict__ for pause in state.pauses],
                "terminal": state.terminal,
            },
            "world": {
                "request": state.request.user_goal,
                "conversation_summary": state.conversation.summary,
                "world_refs": [self._ref_card(ref) for ref in refs],
                "artifacts": list(state.world.artifacts),
                "mutation_leases": list(state.world.mutation_leases),
                "verification_receipts": list(state.world.verification_receipts),
            },
            "skills": active_skills,
            "tools": self._select_tool_specs(state.mode, tool_specs),
            "selection": {
                "mode": state.mode,
                "selected_world_refs": [ref.ref_id for ref in refs],
                "selected_tools": [tool["name"] for tool in self._select_tool_specs(state.mode, tool_specs)],
                "selected_skills": [skill["skill_id"] for skill in active_skills if "skill_id" in skill],
            },
        }

    def _select_world_refs(self, state: AgentState) -> list[WorldRef]:
        refs = list(state.world.refs.values())
        repo_refs = [ref for ref in refs if ref.ref_id == "world://repo_snapshot/latest" or ref.kind == "repo_snapshot"]
        latest_refs = refs[-self.max_world_refs :]
        selected: dict[str, WorldRef] = {}
        for ref in [*repo_refs, *latest_refs]:
            selected[ref.ref_id] = ref
        return list(selected.values())[-self.max_world_refs :]

    def _select_tool_specs(self, mode: str, tool_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        allowed_by_mode = {
            "START": {"repo_snapshot", "read_file"},
            "THINK": {"repo_snapshot", "read_file"},
            "OBSERVE": {"repo_snapshot", "read_file"},
            "PLAN": set(),
            "ACT": set(),
            "VERIFY": {"repo_snapshot", "read_file"},
            "FINALIZE": set(),
        }
        allowed = allowed_by_mode.get(mode, {"repo_snapshot", "read_file"})
        return [tool for tool in tool_specs if tool.get("name") in allowed]

    def _ref_card(self, ref: WorldRef) -> dict[str, Any]:
        return {"ref_id": ref.ref_id, "kind": ref.kind, "summary": ref.summary, "trust": ref.trust}
```

- [ ] **Step 4: Run tests**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py::test_context_selector_preserves_repo_snapshot_and_latest_refs -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add appV2.1/appv21/context/selector.py tests/appv21/test_context_governance.py
git commit -m "feat(appv21): add mode-aware context selector"
```

---

### Task 4: Integrate Selector and Budgets into Prompt Building

**Files:**
- Modify: `appV2.1/appv21/context/manager.py`
- Modify: `appV2.1/appv21/context/prompt_builder.py`
- Modify: `appV2.1/appv21/runtime/services.py`
- Modify: `appV2.1/appv21/runtime/agent_runtime.py`
- Test: `tests/appv21/test_context_governance.py`

- [ ] **Step 1: Write failing prompt event test**

Append:

```python
from appv21 import AppV21AgentRuntime
from appv21.runtime.decisions import RuntimeDecision
from appv21.runtime.services import create_appv21_runtime_services


class OneObserveProvider:
    provider_id = "one-observe"

    def __init__(self) -> None:
        self.done = False

    def decide(self, prompt_payload: dict) -> RuntimeDecision:
        if not self.done:
            self.done = True
            assert "context_budget" in prompt_payload
            assert "selection" in prompt_payload
            assert prompt_payload["selection"]["selected_tools"] == ["read_file", "repo_snapshot"] or prompt_payload["selection"]["selected_tools"] == ["repo_snapshot", "read_file"]
            return RuntimeDecision(kind="observe", reason="map")
        return RuntimeDecision(kind="finalize", reason="noop", payload={"explicit_noop": True})


def test_prompt_context_prepared_records_budget_and_selection(tmp_path: Path) -> None:
    services = create_appv21_runtime_services(root_path=tmp_path, provider=OneObserveProvider())

    result = AppV21AgentRuntime(root_path=tmp_path, services=services, max_turns=2).run("Organize workspace.")

    prepared = [event for event in result["events"] if event["event_type"] == "PromptContextPrepared"]
    assert prepared
    assert "context_budget" in prepared[0]["payload"]
    assert "selection" in prepared[0]["payload"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py::test_prompt_context_prepared_records_budget_and_selection -q
```

Expected: fail because prompt payload/event lacks `context_budget` and `selection`.

- [ ] **Step 3: Add services**

Modify `AppV21RuntimeServices` in `runtime/services.py`:

```python
from appv21.context.budget import ContextBudgetManager
from appv21.context.selector import ContextSelector

context_budget: ContextBudgetManager
context_selector: ContextSelector
```

In `create_appv21_runtime_services()`:

```python
context_budget=ContextBudgetManager(),
context_selector=ContextSelector(),
```

- [ ] **Step 4: Update PromptBuilder to accept selected payload**

Modify `PromptBuilder.build()` signature to include optional `selected_context` and `context_budget`:

```python
selected_context: dict[str, Any] | None = None,
context_budget: dict[str, Any] | None = None,
selection: dict[str, Any] | None = None,
```

At the end of the returned payload, include:

```python
"context_budget": context_budget or {},
"selection": selection or {},
```

Use `selected_context["state"]`, `selected_context["world"]`, `selected_context["skills"]`, and `selected_context["tools"]` when provided.

- [ ] **Step 5: Update runtime prompt assembly**

In `_build_prompt_payload()`:

```python
active_skills = self.skills.active_skills(state)
tool_specs = self.broker.tool_specs()
selected = self.services.context_selector.select(state=state, active_skills=active_skills, tool_specs=tool_specs)
prompt_payload = self.services.prompt_builder.build(
    state=state,
    turn_context=turn_context,
    active_skills=selected["skills"],
    tool_specs=selected["tools"],
    selected_context=selected,
    selection=selected["selection"],
)
context_budget = self.services.context_budget.estimate(prompt_payload)
prompt_payload["context_budget"] = context_budget
self._apply(state, [RuntimeEvent("PromptContextPrepared", {"sections": sorted(prompt_payload), "tool_count": len(prompt_payload["tools"]), "skill_count": len(prompt_payload["skills"]), "context_budget": context_budget, "selection": selected["selection"], "model": self.services.model_registry.for_role("agent").__dict__})])
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py tests/appv21/test_agentic_loop_next_phase.py::test_model_tool_specs_only_expose_callable_tools -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add appV2.1/appv21/context/manager.py appV2.1/appv21/context/prompt_builder.py appV2.1/appv21/runtime/services.py appV2.1/appv21/runtime/agent_runtime.py tests/appv21/test_context_governance.py
git commit -m "feat(appv21): add prompt context budget metadata"
```

---

### Task 5: Preserve Immutable Evidence During Compaction

**Files:**
- Modify: `appV2.1/appv21/context/compactor.py`
- Test: `tests/appv21/test_context_governance.py`

- [ ] **Step 1: Write failing compaction preservation test**

Append:

```python
from appv21.context.compactor import RuntimeContextCompactor
from appv21.state.models import MutationReceipt


def test_compactor_preserves_receipts_and_repo_refs(tmp_path: Path) -> None:
    state = make_state(tmp_path, "Organize workspace.")
    state.world.refs["world://repo_snapshot/latest"] = WorldRef("world://repo_snapshot/latest", "repo_snapshot", "snapshot", {}, "runtime_observed")
    state.world.mutation_receipts["mut_1"] = MutationReceipt(
        receipt_id="mut_1",
        lease_id="lease_1",
        status="applied",
        operations=[],
        touched_paths=["docs/a.md"],
    )
    state.world.verification_receipts["verify_1"] = {"verification_id": "verify_1", "status": "passed"}

    digest = RuntimeContextCompactor().compact(state)

    assert "world://repo_snapshot/latest" in digest["preserved_world_refs"]
    assert digest["mutation_receipts"] == ["mut_1"]
    assert digest["verification_receipts"] == ["verify_1"]
    assert "immutable_classes" in digest
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py::test_compactor_preserves_receipts_and_repo_refs -q
```

Expected: fail because `immutable_classes` is missing.

- [ ] **Step 3: Update compactor digest**

Modify `compact()` in `context/compactor.py` to include:

```python
"immutable_classes": ["user_request", "constraints", "pause_state", "mutation_receipts", "verification_receipts", "active_leases"],
"preservation_policy": {
    "keep_repo_snapshot_refs": True,
    "keep_artifact_evidence_refs": True,
    "keep_latest_world_ref_count": 3,
},
```

- [ ] **Step 4: Run tests**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py::test_compactor_preserves_receipts_and_repo_refs tests/appv21/test_agentic_loop_next_phase.py::test_durable_pause_resume_rehydrates_from_jsonl -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add appV2.1/appv21/context/compactor.py tests/appv21/test_context_governance.py
git commit -m "feat(appv21): mark immutable context evidence"
```

---

### Task 6: Add Run Memory Artifact on Finalize

**Files:**
- Create: `appV2.1/appv21/context/run_memory.py`
- Modify: `appV2.1/appv21/runtime/services.py`
- Modify: `appV2.1/appv21/runtime/agent_runtime.py`
- Test: `tests/appv21/test_context_governance.py`

- [ ] **Step 1: Write failing run memory test**

Append:

```python
def test_finalize_emits_runtime_verified_run_memory(tmp_path: Path) -> None:
    services = create_appv21_runtime_services(root_path=tmp_path, provider=OneObserveProvider())

    result = AppV21AgentRuntime(root_path=tmp_path, services=services, max_turns=2).run("Organize workspace.")

    artifacts = [event["payload"] for event in result["events"] if event["event_type"] == "ArtifactAccepted"]
    run_memories = [artifact for artifact in artifacts if artifact["artifact_id"] == "run_memory"]
    assert run_memories
    assert run_memories[0]["kind"] == "context_summary"
    assert run_memories[0]["trust"] == "runtime_verified"
    assert "decision_counts" in run_memories[0]["content"]
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py::test_finalize_emits_runtime_verified_run_memory -q
```

Expected: fail because run memory is not emitted.

- [ ] **Step 3: Implement run memory builder**

Create `appV2.1/appv21/context/run_memory.py`:

```python
"""Run memory artifact construction for AppV2.1."""

from __future__ import annotations

from collections import Counter
from typing import Any

from appv21.state.models import AgentState, Artifact


class RunMemoryBuilder:
    def build(self, state: AgentState, events: list[dict[str, Any]]) -> Artifact:
        event_counts = Counter(event.get("event_type") for event in events)
        decision_counts = Counter(
            event.get("payload", {}).get("kind")
            for event in events
            if event.get("event_type") == "DecisionProposed"
        )
        return Artifact(
            artifact_id="run_memory",
            kind="context_summary",
            content={
                "goal": state.request.user_goal,
                "outcome": (state.result or {}).get("status", "unknown"),
                "event_counts": dict(event_counts),
                "decision_counts": {key: value for key, value in decision_counts.items() if key},
                "tools_used": sorted(
                    {
                        event.get("payload", {}).get("tool_name")
                        for event in events
                        if event.get("event_type") in {"ToolCallCompleted", "ToolCallDenied"}
                        and event.get("payload", {}).get("tool_name")
                    }
                ),
                "mutation_receipts": list(state.world.mutation_receipts),
                "verification_receipts": list(state.world.verification_receipts),
                "open_risks": [],
            },
            producer="appv21_runtime",
            trust="runtime_verified",
            lifecycle="runtime_verified",
            evidence_refs=list(state.world.verification_receipts),
        )
```

- [ ] **Step 4: Compose and emit run memory before final summary**

Add `run_memory_builder: RunMemoryBuilder` to services.

In `_finalize()`, before `final_summary` creation:

```python
if "run_memory" not in state.world.artifacts:
    run_memory = self.services.run_memory_builder.build(state, self.store.to_dicts())
    issues = self.artifact_validator.validate(run_memory, state)
    if issues:
        self._fail(state, "artifact_validation_failed", {"issues": issues})
        return
    self._apply(state, [RuntimeEvent("ArtifactAccepted", run_memory.__dict__)])
```

- [ ] **Step 5: Run tests**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py::test_finalize_emits_runtime_verified_run_memory tests/appv21/test_agentic_loop_next_phase.py::test_tool_call_reads_file_through_broker_and_records_world_ref -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add appV2.1/appv21/context/run_memory.py appV2.1/appv21/runtime/services.py appV2.1/appv21/runtime/agent_runtime.py tests/appv21/test_context_governance.py
git commit -m "feat(appv21): emit compact run memory"
```

---

### Task 7: Add Context Overflow Recovery

**Files:**
- Create: `appV2.1/appv21/context/overflow.py`
- Modify: `appV2.1/appv21/runtime/agent_runtime.py`
- Modify: `appV2.1/appv21/runtime/services.py`
- Test: `tests/appv21/test_context_governance.py`

- [ ] **Step 1: Write failing overflow classifier test**

Append:

```python
from appv21.context.overflow import ContextOverflowPolicy


def test_context_overflow_policy_classifies_provider_errors() -> None:
    policy = ContextOverflowPolicy()

    assert policy.is_context_overflow(ValueError("context length exceeded")) is True
    assert policy.is_context_overflow(RuntimeError("413 request too large")) is True
    assert policy.is_context_overflow(RuntimeError("rate limit")) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py::test_context_overflow_policy_classifies_provider_errors -q
```

Expected: fail because overflow policy does not exist.

- [ ] **Step 3: Implement overflow policy**

Create `appV2.1/appv21/context/overflow.py`:

```python
"""Context overflow classification and retry policy."""

from __future__ import annotations


class ContextOverflowPolicy:
    markers = ("context length", "context_length", "maximum context", "too many tokens", "413", "request too large")

    def is_context_overflow(self, error: BaseException) -> bool:
        text = str(error).lower()
        return any(marker in text for marker in self.markers)
```

- [ ] **Step 4: Add runtime retry path**

In `run_turn()` around provider decision:

```python
try:
    decision = self.services.provider.decide(prompt_payload)
except Exception as exc:
    if self.services.context_overflow.is_context_overflow(exc):
        self._apply(state, [RuntimeEvent("ContextOverflowDetected", {"error": str(exc)[:300]})])
        compacted = self.context.maybe_compact(state)
        if compacted:
            self._apply(state, compacted)
            prompt_payload = self._build_prompt_payload(state)
            decision = self.services.provider.decide(prompt_payload)
        else:
            self._apply(state, [RuntimeEvent("ContextOverflowRecoveryFailed", {"reason": "no_compaction_available"})])
            raise
    else:
        raise
```

- [ ] **Step 5: Run tests**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py::test_context_overflow_policy_classifies_provider_errors tests/appv21/test_runtime_first_probe.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add appV2.1/appv21/context/overflow.py appV2.1/appv21/runtime/agent_runtime.py appV2.1/appv21/runtime/services.py tests/appv21/test_context_governance.py
git commit -m "feat(appv21): recover from context overflow"
```

---

### Task 8: Add Context Matrix Fields to Staged Probe

**Files:**
- Modify: `scripts/live_appv21_staged_file_management_matrix_probe.py`
- Test: `tests/appv21/test_context_governance.py`

- [ ] **Step 1: Add failing matrix helper test**

Append:

```python
def test_staged_probe_report_has_context_budget_matrix(tmp_path: Path) -> None:
    from scripts.live_appv21_staged_file_management_matrix_probe import _build_report

    report = _build_report(repo=tmp_path, result={"status": "completed", "events": []}, provider=None, max_turns=1)

    assert "context_budget_matrix" in report
    assert "selection_matrix" in report
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py::test_staged_probe_report_has_context_budget_matrix -q
```

Expected: fail because matrix fields are missing.

- [ ] **Step 3: Add matrix fields**

In `_build_report()` add:

```python
"context_budget_matrix": [_context_event_summary(event) for event in events if event["event_type"] == "PromptContextPrepared"],
"selection_matrix": [_selection_event_summary(event) for event in events if event["event_type"] == "PromptContextPrepared"],
```

Add helpers:

```python
def _context_event_summary(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload", {})
    budget = payload.get("context_budget") or {}
    return {
        "event_id": event.get("event_id"),
        "total_chars": budget.get("total_chars", 0),
        "over_budget_sections": budget.get("over_budget_sections", []),
        "sections": budget.get("sections", {}),
    }


def _selection_event_summary(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload", {})
    selection = payload.get("selection") or {}
    return {
        "event_id": event.get("event_id"),
        "mode": selection.get("mode"),
        "selected_world_refs": selection.get("selected_world_refs", []),
        "selected_tools": selection.get("selected_tools", []),
        "selected_skills": selection.get("selected_skills", []),
    }
```

- [ ] **Step 4: Run tests**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py::test_staged_probe_report_has_context_budget_matrix -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/live_appv21_staged_file_management_matrix_probe.py tests/appv21/test_context_governance.py
git commit -m "test(appv21): add context matrix probe fields"
```

---

### Task 9: Add Preservation Rules to Mutation Validation

**Files:**
- Modify: `appV2.1/appv21/tools/broker.py`
- Test: `tests/appv21/test_context_governance.py`

- [ ] **Step 1: Write failing preservation validation test**

Append:

```python
from appv21.tools.broker import ToolBroker


def test_mutation_validation_rejects_protected_path_moves(tmp_path: Path) -> None:
    (tmp_path / "src" / "config").mkdir(parents=True)
    (tmp_path / "src" / "config" / "settings.json").write_text("{}", encoding="utf-8")
    broker = ToolBroker(root_path=tmp_path)

    errors = broker.validate_mutation_intent(
        [{"action": "move", "source": "src/config/settings.json", "destination": "artifacts/logs/settings.json"}]
    )

    assert "protected_source_path:src/config/settings.json" in errors
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py::test_mutation_validation_rejects_protected_path_moves -q
```

Expected: fail because mutation validation permits `src/config/settings.json`.

- [ ] **Step 3: Add protected path policy**

In `tools/broker.py`, add:

```python
PROTECTED_MUTATION_PREFIXES = ("tests/", "src/", "assets/", "secrets/", "docs/")
PROTECTED_MUTATION_NAMES = {"README.md"}
PROTECTED_MUTATION_MARKERS = ("keep", "do_not_move", "old_blob")
```

Add helper:

```python
def _protected_mutation_path(path: str) -> bool:
    normalized = path.strip("/")
    name = Path(normalized).name
    return (
        normalized in PROTECTED_MUTATION_NAMES
        or normalized.startswith(PROTECTED_MUTATION_PREFIXES)
        or any(marker in name for marker in PROTECTED_MUTATION_MARKERS)
    )
```

In `validate_mutation_intent()` for `move`:

```python
source_path = str(operation.get("source") or "")
if _protected_mutation_path(source_path):
    errors.append(f"protected_source_path:{source_path}")
```

For `write`, reject protected paths unless writing the manifest:

```python
if _protected_mutation_path(path) and path != "docs/workspace_manifest.json":
    errors.append(f"protected_destination_path:{path}")
```

- [ ] **Step 4: Run tests**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py::test_mutation_validation_rejects_protected_path_moves tests/appv21/test_agentic_loop_next_phase.py::test_bad_mutation_intent_is_denied_before_write -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add appV2.1/appv21/tools/broker.py tests/appv21/test_context_governance.py
git commit -m "fix(appv21): enforce protected mutation paths"
```

---

### Task 10: Full Context Governance QA

**Files:**
- No production edits unless tests reveal defects.

- [ ] **Step 1: Run focused context governance tests**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21/test_context_governance.py -q
```

Expected: all context governance tests pass.

- [ ] **Step 2: Run AppV2.1 full QA**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21 tests/test_appv2_prompt_quality.py -q
```

Expected: all tests pass.

- [ ] **Step 3: Run compile check**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run python -m compileall -q appV2.1 tests/appv21 scripts/live_appv21_staged_file_management_matrix_probe.py
```

Expected: exit code `0`.

- [ ] **Step 4: Run staged probe with live model**

Run:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run python scripts/live_appv21_staged_file_management_matrix_probe.py --provider appv2-env --dotenv /Users/htooayelwin/lewis/allthebest/.env
```

Expected:

```json
{
  "status": "completed",
  "verdict": "pass"
}
```

If the model proposes protected moves, expected runtime behavior is deterministic denial with `protected_source_path:<path>`, not silent mutation.

- [ ] **Step 5: Commit final probe/report adjustments if needed**

Only commit source/test changes, not generated live repo directories.

```bash
git add appV2.1/appv21 tests/appv21 scripts/live_appv21_staged_file_management_matrix_probe.py
git commit -m "test(appv21): verify context governance matrix"
```

---

## Spec Coverage Checklist

- Pi-style loop freedom remains: Tasks 3-4 preserve mode-aware context without forcing fixed pipeline.
- Hermes preflight context selection: Tasks 1, 3, 4.
- Hermes post-response/run compaction: Tasks 5-7.
- Skill card management: Task 2.
- Mode-filtered tool specs: Tasks 3-4.
- Durable run memory: Task 6.
- Overflow recovery: Task 7.
- Staged probe matrix: Task 8.
- Preservation failure from probe converted into hard runtime policy: Task 9.
- Full QA and live matrix: Task 10.

## Execution Notes

- Do not reintroduce planner-first mutation scope.
- Do not expose mutating tools as ordinary model-callable tools.
- Do not place raw evidence payloads into prompt context.
- Do not compact away mutation receipts, verification receipts, pause state, active leases, or user constraints.
- Do not stage generated live probe repositories unless explicitly requested.

