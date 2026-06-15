# AppV2.1 Architecture Gap Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn AppV2.1 from a working probe runtime into the Phase 1/2 hardened Pi + Hermes + AppV2 runtime described by the architecture gap spec.

**Architecture:** Keep `AppV21AgentRuntime` as the only public facade, but move transition legality into `RuntimeStateMachine`, decision validation into `DecisionValidator`, and all tool behavior into a registry-backed `ToolBroker`. Preserve the current event-sourced reducer, mutation leases, runtime-owned evidence, and verification receipts while making the boundaries explicit enough for later context, provider, planner, artifact, session, and eval phases.

**Tech Stack:** Python 3.13, dataclasses, pytest, existing AppV2.1 package under `appV2.1/appv21`, existing probes under `scripts/live_appv21_*.py`.

---

## Scope and Execution Strategy

This plan covers the full gap review, but implementation should be executed in phase gates:

- Phase Gate A: Tasks 1-5. Runtime state machine and decision validation.
- Phase Gate B: Tasks 6-9. Registry-backed ToolBroker and evidence envelopes.
- Phase Gate C: Tasks 10-14. Context, provider routing, planner/artifacts/verification.
- Phase Gate D: Tasks 15-17. Session replay, surfaces, eval matrix, docs.

Do not start Phase Gate C until Phase Gate A and B tests pass. The current production risk is not planner intelligence; it is unclear runtime/tool interfaces.

## Phase Gate A Execution Addendum

Status: implemented and reviewed on branch `codex/appv21-architecture-hardening`.

Fresh verification after Phase Gate A:

```bash
UV_CACHE_DIR=/private/tmp/allthebest-uv-cache uv run pytest tests/appv21 tests/test_appv2_prompt_quality.py -q
```

Expected: `56 passed`.

Phase Gate A final behavior is stricter than the initial task snippets below. Future workers must preserve these reviewed corrections:

- `tests/appv21/test_decision_validator.py` asserts all 10 rejection constants, including `INVALID_TRANSITION`, `FINALIZE_WITHOUT_VERIFICATION`, and `INVALID_PAYLOAD`.
- `DecisionValidator` uses `appv21.runtime.decisions.KNOWN_DECISION_KINDS` as the canonical decision-kind source.
- `RuntimeStateMachine` validates unknown modes separately with `invalid_mode:<mode>`.
- `RuntimeStateMachine.record_progress()` counts only consecutive nonproductive decisions.
- `RuntimeStateMachine.reset_progress()` is called at the start of each new runtime `run()` to prevent loop-state leakage across reused runtime/service objects.
- `AppV21AgentRuntime.run_turn()` validates transitions from `mode_before_prompt`, not the temporary `THINK` mode introduced while building prompt context.
- Rejected decisions restore the previous semantic mode before `DecisionRejected` is emitted.
- Loop progress detection uses semantic state only. Do not use event-count or mode changes as progress signals.
- Direct mutation test/probe providers must follow the legal runtime path: `observe -> plan -> mutation_intent`. Do not reintroduce `START -> mutation_intent` fixtures.

Phase Gate B workers should treat the above as non-negotiable compatibility requirements.

## File Structure

Create:

- `appV2.1/appv21/runtime/state_machine.py` - legal runtime transitions, guards, repeated-progress detection.
- `appV2.1/appv21/runtime/decision_validator.py` - decision kind, evidence, payload, and finalization validation.
- `appV2.1/appv21/runtime/rejections.py` - stable rejection reason constants.
- `appV2.1/appv21/tools/definitions.py` - typed tool definitions, categories, schemas, and result envelopes.
- `appV2.1/appv21/tools/registry.py` - registry for brokered tools.
- `appV2.1/appv21/tools/evidence_store.py` - raw tool result payload retention by world ref.
- `appV2.1/appv21/context/stores.py` - conversation/world/artifact/memory/scratch store shells.
- `appV2.1/appv21/context/selector.py` - deterministic relevance and budget selection.
- `appV2.1/appv21/runtime/provider_registry.py` - role/capability/cost-aware provider selection.
- `appV2.1/appv21/extensions/planning_contracts.py` - general planning request/proposal/lifecycle types.
- `appV2.1/appv21/validators/verification.py` - verification policy and stale/freshness checks.
- `appV2.1/appv21/runtime/replay.py` - replay and inspect helpers for JSONL sessions.
- `tests/appv21/test_state_machine.py`
- `tests/appv21/test_decision_validator.py`
- `tests/appv21/test_tool_registry.py`
- `tests/appv21/test_context_system.py`
- `tests/appv21/test_provider_registry.py`
- `tests/appv21/test_planning_artifacts_verification.py`
- `tests/appv21/test_session_replay_surfaces.py`

Modify:

- `appV2.1/appv21/runtime/agent_runtime.py` - delegate validation/transitions/tool dispatch to new components.
- `appV2.1/appv21/runtime/services.py` - compose new services.
- `appV2.1/appv21/runtime/reducer.py` - reduce new events.
- `appV2.1/appv21/state/models.py` - add lifecycle/cost/session metadata fields.
- `appV2.1/appv21/tools/broker.py` - convert from hard-coded broker to registry mediator.
- `appV2.1/appv21/context/manager.py` - route through stores/selector.
- `appV2.1/appv21/context/compactor.py` - add immutable evidence classes and budgeted compaction.
- `appV2.1/appv21/context/prompt_builder.py` - consume selected context payload.
- `appV2.1/appv21/runtime/model_registry.py` - either replace with or delegate to `provider_registry.py`.
- `appV2.1/appv21/extensions/planner.py` - implement general planning contract while keeping workspace cleanup fixture.
- `appV2.1/appv21/extensions/verifier.py` - delegate freshness/policy checks.
- `appV2.1/appv21/validators/artifacts.py` - artifact lifecycle enforcement only; decision validation moves out.
- `scripts/live_appv21_*.py` - keep probe outputs stable and add new matrix fields where needed.

---

### Task 1: Add Rejection Reason Constants

**Files:**
- Create: `appV2.1/appv21/runtime/rejections.py`
- Test: `tests/appv21/test_decision_validator.py`

- [ ] **Step 1: Write the failing test**

```python
from appv21.runtime import rejections


def test_rejection_constants_are_stable_strings() -> None:
    assert rejections.MISSING_EVIDENCE == "missing_evidence"
    assert rejections.UNSUPPORTED_DECISION == "unsupported_decision"
    assert rejections.UNSAFE_TOOL == "unsafe_tool"
    assert rejections.INVALID_MUTATION == "invalid_mutation"
    assert rejections.STALE_PLAN == "stale_plan"
    assert rejections.VERIFICATION_FAILED == "verification_failed"
    assert rejections.REPEATED_LOOP == "repeated_loop"
    assert rejections.INVALID_TRANSITION == "invalid_transition"
    assert rejections.FINALIZE_WITHOUT_VERIFICATION == "finalize_without_verification"
    assert rejections.INVALID_PAYLOAD == "invalid_payload"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/appv21/test_decision_validator.py::test_rejection_constants_are_stable_strings -q`

Expected: FAIL with `ModuleNotFoundError` or missing attributes.

- [ ] **Step 3: Add the constants**

```python
"""Stable rejection reason constants for AppV2.1."""

MISSING_EVIDENCE = "missing_evidence"
UNSUPPORTED_DECISION = "unsupported_decision"
UNSAFE_TOOL = "unsafe_tool"
INVALID_MUTATION = "invalid_mutation"
STALE_PLAN = "stale_plan"
VERIFICATION_FAILED = "verification_failed"
REPEATED_LOOP = "repeated_loop"
INVALID_TRANSITION = "invalid_transition"
FINALIZE_WITHOUT_VERIFICATION = "finalize_without_verification"
INVALID_PAYLOAD = "invalid_payload"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/appv21/test_decision_validator.py::test_rejection_constants_are_stable_strings -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appV2.1/appv21/runtime/rejections.py tests/appv21/test_decision_validator.py
git commit -m "feat(appv21): add runtime rejection constants"
```

---

### Task 2: Extract DecisionValidator from ArtifactValidator

**Files:**
- Create: `appV2.1/appv21/runtime/decision_validator.py`
- Modify: `appV2.1/appv21/validators/artifacts.py`
- Test: `tests/appv21/test_decision_validator.py`

- [ ] **Step 1: Write failing tests for evidence and finalization**

```python
from pathlib import Path

from appv21.runtime.decision_validator import DecisionValidator
from appv21.runtime.decisions import RuntimeDecision
from appv21.state.models import AgentState, RequestEnvelope, WorldRef


def make_state(tmp_path: Path) -> AgentState:
    return AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(request_id="req", user_goal="Clean up.", root_path=str(tmp_path)),
    )


def test_decision_validator_rejects_missing_world_evidence(tmp_path: Path) -> None:
    state = make_state(tmp_path)
    decision = RuntimeDecision(kind="plan", reason="bad", evidence_refs=["world://missing"])

    issues = DecisionValidator().validate(decision, state)

    assert issues == ["missing_evidence:world://missing"]


def test_decision_validator_accepts_runtime_world_evidence(tmp_path: Path) -> None:
    state = make_state(tmp_path)
    state.world.refs["world://repo_snapshot/latest"] = WorldRef(
        ref_id="world://repo_snapshot/latest",
        kind="repo_snapshot",
        summary="snapshot",
        payload={"files": []},
        trust="runtime_observed",
    )
    decision = RuntimeDecision(kind="plan", reason="ok", evidence_refs=["world://repo_snapshot/latest"])

    assert DecisionValidator().validate(decision, state) == []


def test_decision_validator_rejects_finalize_without_verification(tmp_path: Path) -> None:
    state = make_state(tmp_path)
    decision = RuntimeDecision(kind="finalize", reason="done")

    assert DecisionValidator().validate(decision, state) == ["finalize_without_verification"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/appv21/test_decision_validator.py -q`

Expected: FAIL because `DecisionValidator` does not exist.

- [ ] **Step 3: Implement DecisionValidator**

```python
"""Runtime decision validation for AppV2.1."""

from __future__ import annotations

from appv21.runtime.decisions import KNOWN_DECISION_KINDS, RuntimeDecision
from appv21.runtime import rejections
from appv21.state.models import AgentState


class DecisionValidator:
    known_decision_kinds = KNOWN_DECISION_KINDS

    def validate(self, decision: RuntimeDecision, state: AgentState) -> list[str]:
        issues: list[str] = []
        if decision.kind not in self.known_decision_kinds:
            issues.append(f"{rejections.UNSUPPORTED_DECISION}:{decision.kind}")
        issues.extend(self._validate_evidence(decision, state))
        issues.extend(self._validate_payload(decision))
        if decision.kind == "finalize" and not state.world.verification_receipts and not decision.payload.get("explicit_noop"):
            issues.append(rejections.FINALIZE_WITHOUT_VERIFICATION)
        return issues

    def _validate_evidence(self, decision: RuntimeDecision, state: AgentState) -> list[str]:
        issues: list[str] = []
        for ref in decision.evidence_refs:
            if ref == "plan://accepted/latest":
                if state.plan is None:
                    issues.append(f"{rejections.MISSING_EVIDENCE}:{ref}")
                continue
            if ref == "verification://latest":
                if not state.world.verification_receipts:
                    issues.append(f"{rejections.MISSING_EVIDENCE}:{ref}")
                continue
            if ref not in state.world.refs and ref not in state.world.mutation_receipts and ref not in state.world.verification_receipts:
                issues.append(f"{rejections.MISSING_EVIDENCE}:{ref}")
        return issues

    def _validate_payload(self, decision: RuntimeDecision) -> list[str]:
        if not isinstance(decision.payload, dict):
            return [f"{rejections.INVALID_PAYLOAD}:payload_not_object"]
        if decision.kind == "tool_call":
            tool_name = decision.payload.get("tool_name") or decision.payload.get("tool")
            if not tool_name:
                return [f"{rejections.INVALID_PAYLOAD}:tool_name_required"]
        if decision.kind == "mutation_intent":
            operations = decision.payload.get("operations")
            if operations is not None and not isinstance(operations, list):
                return [f"{rejections.INVALID_PAYLOAD}:operations_not_list"]
        return []
```

- [ ] **Step 4: Reduce ArtifactValidator to artifacts only**

Change `appV2.1/appv21/validators/artifacts.py` so `validate_decision()` delegates for compatibility only:

```python
from appv21.runtime.decision_validator import DecisionValidator


class ArtifactValidator:
    def validate_decision(self, decision: RuntimeDecision, state: AgentState) -> list[str]:
        return DecisionValidator().validate(decision, state)
```

Keep existing artifact validation methods unchanged in this task.

- [ ] **Step 5: Run focused tests**

Run: `uv run pytest tests/appv21/test_decision_validator.py tests/appv21/test_runtime_first_probe.py::test_appv21_runtime_rejects_missing_decision_evidence -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add appV2.1/appv21/runtime/decision_validator.py appV2.1/appv21/validators/artifacts.py tests/appv21/test_decision_validator.py
git commit -m "feat(appv21): extract runtime decision validator"
```

---

### Task 3: Add RuntimeStateMachine

**Files:**
- Create: `appV2.1/appv21/runtime/state_machine.py`
- Test: `tests/appv21/test_state_machine.py`

- [ ] **Step 1: Write failing transition tests**

```python
from appv21.runtime.decisions import RuntimeDecision
from appv21.runtime.state_machine import RuntimeStateMachine


def test_state_machine_allows_open_observe_act_revise_loop() -> None:
    machine = RuntimeStateMachine()

    assert machine.next_mode("START", RuntimeDecision(kind="observe", reason="map")) == "OBSERVE"
    assert machine.next_mode("OBSERVE", RuntimeDecision(kind="plan", reason="plan")) == "PLAN"
    assert machine.next_mode("PLAN", RuntimeDecision(kind="mutation_intent", reason="act")) == "ACT"
    assert machine.next_mode("ACT", RuntimeDecision(kind="verify", reason="check")) == "VERIFY"
    assert machine.next_mode("VERIFY", RuntimeDecision(kind="finalize", reason="done")) == "FINALIZE"


def test_state_machine_rejects_finalize_from_start() -> None:
    machine = RuntimeStateMachine()

    rejection = machine.validate_transition("START", RuntimeDecision(kind="finalize", reason="done"))

    assert rejection == "invalid_transition:START->finalize"


def test_state_machine_detects_repeated_nonproductive_decisions() -> None:
    machine = RuntimeStateMachine(max_repeated_decisions=3)
    decision = RuntimeDecision(kind="observe", reason="again")

    assert machine.record_progress(decision, changed=False) is None
    assert machine.record_progress(decision, changed=False) is None
    assert machine.record_progress(decision, changed=False) == "repeated_loop:observe"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/appv21/test_state_machine.py -q`

Expected: FAIL because module does not exist.

- [ ] **Step 3: Implement RuntimeStateMachine**

```python
"""Formal runtime transition policy for AppV2.1."""

from __future__ import annotations

from dataclasses import dataclass, field

from appv21.runtime.decisions import RuntimeDecision
from appv21.state.models import RuntimeMode


TRANSITIONS: dict[str, set[str]] = {
    "START": {"observe", "tool_call", "read_file", "pause"},
    "THINK": {"observe", "tool_call", "read_file", "plan", "mutation_intent", "verify", "compact", "pause", "finalize"},
    "OBSERVE": {"observe", "tool_call", "read_file", "plan", "compact", "pause", "finalize"},
    "PLAN": {"observe", "tool_call", "read_file", "mutation_intent", "compact", "pause", "finalize"},
    "ACT": {"verify", "observe", "tool_call", "read_file", "compact", "pause", "finalize"},
    "VERIFY": {"finalize", "plan", "observe", "tool_call", "read_file", "compact", "pause"},
    "REVISE": {"observe", "tool_call", "read_file", "plan", "pause"},
    "COMPACT": {"observe", "tool_call", "read_file", "plan", "mutation_intent", "verify", "pause", "finalize"},
    "PAUSE": set(),
    "FINALIZE": set(),
    "FAILED": set(),
}

TARGET_MODE_BY_DECISION: dict[str, RuntimeMode] = {
    "observe": "OBSERVE",
    "tool_call": "OBSERVE",
    "read_file": "OBSERVE",
    "plan": "PLAN",
    "mutation_intent": "ACT",
    "verify": "VERIFY",
    "compact": "COMPACT",
    "pause": "PAUSE",
    "finalize": "FINALIZE",
}


@dataclass
class RuntimeStateMachine:
    max_repeated_decisions: int = 3
    _last_nonproductive_key: str | None = None
    _repeated_count: int = 0

    def validate_transition(self, current_mode: RuntimeMode | str, decision: RuntimeDecision) -> str | None:
        if current_mode not in TRANSITIONS:
            return f"invalid_mode:{current_mode}"
        allowed = TRANSITIONS[current_mode]  # type: ignore[index]
        if decision.kind not in allowed:
            return f"invalid_transition:{current_mode}->{decision.kind}"
        return None

    def next_mode(self, current_mode: RuntimeMode | str, decision: RuntimeDecision) -> RuntimeMode:
        rejection = self.validate_transition(current_mode, decision)
        if rejection is not None:
            raise ValueError(rejection)
        return TARGET_MODE_BY_DECISION[decision.kind]

    def record_progress(self, decision: RuntimeDecision, *, changed: bool) -> str | None:
        key = decision.kind
        if changed:
            self._last_nonproductive_key = None
            self._repeated_count = 0
            return None
        if key != self._last_nonproductive_key:
            self._last_nonproductive_key = key
            self._repeated_count = 0
        self._repeated_count += 1
        if self._repeated_count >= self.max_repeated_decisions:
            return f"repeated_loop:{key}"
        return None
```

The final implementation also includes `reset_progress()` and tests for canonical decision-kind coverage, unknown-mode handling, consecutive-only loop detection, and progress reset between runs.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/appv21/test_state_machine.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appV2.1/appv21/runtime/state_machine.py tests/appv21/test_state_machine.py
git commit -m "feat(appv21): add runtime state machine"
```

---

### Task 4: Integrate StateMachine and DecisionValidator into Runtime

**Files:**
- Modify: `appV2.1/appv21/runtime/services.py`
- Modify: `appV2.1/appv21/runtime/agent_runtime.py`
- Test: `tests/appv21/test_state_machine.py`
- Test: `tests/appv21/test_runtime_first_probe.py`

- [ ] **Step 1: Add integration tests**

```python
from pathlib import Path

from appv21 import AppV21AgentRuntime
from appv21.runtime.decisions import RuntimeDecision
from appv21.runtime.services import create_appv21_runtime_services


class QueueProvider:
    provider_id = "queue"

    def __init__(self, decisions: list[RuntimeDecision]) -> None:
        self.decisions = decisions

    def decide(self, prompt_payload: dict) -> RuntimeDecision:
        return self.decisions.pop(0)


def test_runtime_rejects_illegal_finalize_transition_from_start(tmp_path: Path) -> None:
    provider = QueueProvider([RuntimeDecision(kind="finalize", reason="too early", payload={"explicit_noop": True})])
    services = create_appv21_runtime_services(root_path=tmp_path, provider=provider)

    result = AppV21AgentRuntime(root_path=tmp_path, services=services, max_turns=1).run("Finalize too early.")

    assert result["status"] == "failed"
    assert result["reason"] == "invalid_transition"
    assert any(
        event["event_type"] == "DecisionRejected" and event["payload"]["reason"] == "invalid_transition:START->finalize"
        for event in result["events"]
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/appv21/test_state_machine.py::test_runtime_rejects_illegal_finalize_transition_from_start -q`

Expected: FAIL because runtime does not use `RuntimeStateMachine`.

- [ ] **Step 3: Extend service composition**

Add fields to `AppV21RuntimeServices`:

```python
from appv21.runtime.decision_validator import DecisionValidator
from appv21.runtime.state_machine import RuntimeStateMachine

decision_validator: DecisionValidator
state_machine: RuntimeStateMachine
```

Add constructor values:

```python
decision_validator=DecisionValidator(),
state_machine=RuntimeStateMachine(),
```

- [ ] **Step 4: Update runtime validation path**

In `AppV21AgentRuntime.__init__`, add:

```python
self.decision_validator = self.services.decision_validator
self.state_machine = self.services.state_machine
```

In `run_turn()`, capture the semantic mode before prompt building and replace artifact validator decision validation with:

```python
mode_before_prompt = state.mode
prompt_payload = self._build_prompt_payload(state)
decision = self.services.provider.decide(prompt_payload)

transition_rejection = self.state_machine.validate_transition(mode_before_prompt, decision)
if transition_rejection is not None:
    self._restore_mode_after_rejection(state, mode_before_prompt)
    self._apply(state, [RuntimeEvent("DecisionRejected", {"decision_id": decision.decision_id, "reason": transition_rejection})])
    return decision, transition_rejection

validation_issues = self.decision_validator.validate(decision, state)
```

Change repeated rejection failure mapping:

```python
if rejection.startswith(("invalid_transition:", "invalid_mode:")):
    return self._fail(state, "invalid_transition", {"decision": decision.to_dict(), "reason": rejection})
```

Add `_restore_mode_after_rejection()` so rejected decisions do not leave the runtime in the transient prompt-preparation `THINK` mode:

```python
def _restore_mode_after_rejection(self, state: AgentState, mode_before_prompt: str) -> None:
    if state.mode != mode_before_prompt:
        self._apply(state, [RuntimeEvent("ModeChanged", {"mode": mode_before_prompt})])
```

- [ ] **Step 5: Run focused tests**

Run: `uv run pytest tests/appv21/test_state_machine.py tests/appv21/test_decision_validator.py tests/appv21/test_runtime_first_probe.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add appV2.1/appv21/runtime/services.py appV2.1/appv21/runtime/agent_runtime.py tests/appv21/test_state_machine.py
git commit -m "feat(appv21): enforce runtime transitions"
```

---

### Task 5: Add Progress Detection Events

**Files:**
- Modify: `appV2.1/appv21/runtime/agent_runtime.py`
- Modify: `appV2.1/appv21/runtime/state_machine.py`
- Test: `tests/appv21/test_state_machine.py`

- [ ] **Step 1: Add failing repeated-loop runtime test**

```python
def test_runtime_fails_repeated_nonproductive_observe_loop(tmp_path: Path) -> None:
    class RepeatingObserveProvider:
        provider_id = "repeat-observe"

        def decide(self, prompt_payload: dict) -> RuntimeDecision:
            return RuntimeDecision(kind="observe", reason="again")

    services = create_appv21_runtime_services(root_path=tmp_path, provider=RepeatingObserveProvider())
    result = AppV21AgentRuntime(root_path=tmp_path, services=services, max_turns=8).run("Loop.")

    assert result["status"] == "failed"
    assert result["reason"] == "repeated_loop"
    assert any(event["event_type"] == "LoopProgressRejected" for event in result["events"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/appv21/test_state_machine.py::test_runtime_fails_repeated_nonproductive_observe_loop -q`

Expected: FAIL because runtime does not record nonproductive progress.

- [ ] **Step 3: Record semantic progress around route execution**

Do not use event count or mode changes as progress. Those are bookkeeping and will mask nonproductive loops.

In `run_turn()`, capture semantic state before routing:

```python
progress_before = self._progress_snapshot(state)
had_latest_repo_snapshot = "world://repo_snapshot/latest" in state.world.refs
self.route_decision(state, decision)
if decision.kind == "observe" and had_latest_repo_snapshot:
    changed = False
else:
    changed = progress_before != self._progress_snapshot(state)
progress_rejection = self.state_machine.record_progress(decision, changed=changed)
if progress_rejection is not None:
    self._apply(state, [RuntimeEvent("LoopProgressRejected", {"decision_id": decision.decision_id, "reason": progress_rejection})])
    self._fail(state, "repeated_loop", {"decision": decision.to_dict(), "reason": progress_rejection})
```

Use a semantic progress snapshot:

```python
def _progress_snapshot(self, state: AgentState) -> tuple[Any, ...]:
    return (
        tuple(state.world.refs),
        repr(state.plan),
        tuple(state.world.mutation_receipts),
        tuple(state.world.verification_receipts),
        tuple(state.world.artifacts),
        state.terminal,
        repr(state.result),
    )
```

Add `RuntimeStateMachine.reset_progress()` and call it at the start of `AppV21AgentRuntime.run()` before `_run_loop()` so reused runtime instances do not leak loop-progress counters across independent runs.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/appv21/test_state_machine.py tests/appv21/test_runtime_first_probe.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appV2.1/appv21/runtime/agent_runtime.py tests/appv21/test_state_machine.py
git commit -m "feat(appv21): detect repeated nonproductive loops"
```

---

### Task 6: Define Typed Tool Definitions and Envelopes

**Files:**
- Create: `appV2.1/appv21/tools/definitions.py`
- Test: `tests/appv21/test_tool_registry.py`

- [ ] **Step 1: Write failing tests**

```python
from appv21.tools.definitions import ToolCategory, ToolDefinition, ToolResultEnvelope


def test_tool_definition_requires_name_category_and_schema() -> None:
    definition = ToolDefinition(
        name="repo_snapshot",
        category=ToolCategory.OBSERVE,
        argument_schema={"type": "object", "additionalProperties": False, "properties": {}},
        result_schema={"type": "object"},
        risk_level="low",
    )

    assert definition.name == "repo_snapshot"
    assert definition.category.value == "observe"


def test_tool_result_envelope_uses_payload_ref() -> None:
    envelope = ToolResultEnvelope(
        tool_result_id="toolres_1",
        tool_name="repo_snapshot",
        status="completed",
        trust="runtime_observed",
        payload_ref="world://tool_result/toolres_1",
        prompt_summary={"file_count": 1},
        evidence_refs=[],
        artifacts=[],
    )

    assert envelope.payload_ref == "world://tool_result/toolres_1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/appv21/test_tool_registry.py -q`

Expected: FAIL because definitions do not exist.

- [ ] **Step 3: Implement definitions**

```python
"""Typed tool definitions and result envelopes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ToolCategory(str, Enum):
    OBSERVE = "observe"
    INSPECT = "inspect"
    SEARCH = "search"
    ANALYZE = "analyze"
    PLAN_HELPER = "plan-helper"
    MUTATE = "mutate"
    VERIFY = "verify"
    EXTERNAL = "external"


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    category: ToolCategory
    argument_schema: dict[str, Any]
    result_schema: dict[str, Any]
    risk_level: str = "low"
    trust: str = "runtime_observed"
    guidance: str = ""
    cacheable: bool = False


@dataclass(frozen=True)
class ToolResultEnvelope:
    tool_result_id: str
    tool_name: str
    status: str
    trust: str
    payload_ref: str
    prompt_summary: dict[str, Any]
    evidence_refs: list[str] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_result_id": self.tool_result_id,
            "tool_name": self.tool_name,
            "status": self.status,
            "trust": self.trust,
            "payload_ref": self.payload_ref,
            "prompt_summary": self.prompt_summary,
            "evidence_refs": self.evidence_refs,
            "artifacts": self.artifacts,
        }
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/appv21/test_tool_registry.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appV2.1/appv21/tools/definitions.py tests/appv21/test_tool_registry.py
git commit -m "feat(appv21): add typed tool definitions"
```

---

### Task 7: Add Tool Registry with Argument Validation

**Files:**
- Create: `appV2.1/appv21/tools/registry.py`
- Test: `tests/appv21/test_tool_registry.py`

- [ ] **Step 1: Add failing registry tests**

```python
from appv21.tools.definitions import ToolCategory, ToolDefinition
from appv21.tools.registry import ToolRegistry


def test_registry_denies_unknown_tools() -> None:
    registry = ToolRegistry()

    assert registry.validate_call("missing", {}) == ["unknown_tool:missing"]


def test_registry_validates_required_arguments() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="read_file",
            category=ToolCategory.INSPECT,
            argument_schema={
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
            result_schema={"type": "object"},
        )
    )

    assert registry.validate_call("read_file", {}) == ["missing_argument:path"]
    assert registry.validate_call("read_file", {"path": "README.md", "extra": True}) == ["unknown_argument:extra"]
    assert registry.validate_call("read_file", {"path": "README.md"}) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/appv21/test_tool_registry.py::test_registry_denies_unknown_tools tests/appv21/test_tool_registry.py::test_registry_validates_required_arguments -q`

Expected: FAIL because `ToolRegistry` does not exist.

- [ ] **Step 3: Implement ToolRegistry**

```python
"""Registry for AppV2.1 brokered tools."""

from __future__ import annotations

from appv21.tools.definitions import ToolDefinition


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        self._definitions[definition.name] = definition

    def get(self, name: str) -> ToolDefinition | None:
        return self._definitions.get(name)

    def list(self) -> list[ToolDefinition]:
        return [self._definitions[name] for name in sorted(self._definitions)]

    def validate_call(self, tool_name: str, arguments: dict) -> list[str]:
        definition = self.get(tool_name)
        if definition is None:
            return [f"unknown_tool:{tool_name}"]
        schema = definition.argument_schema
        issues: list[str] = []
        required = schema.get("required") or []
        for key in required:
            if key not in arguments:
                issues.append(f"missing_argument:{key}")
        properties = set((schema.get("properties") or {}).keys())
        if schema.get("additionalProperties") is False:
            for key in arguments:
                if key not in properties:
                    issues.append(f"unknown_argument:{key}")
        for key, rule in (schema.get("properties") or {}).items():
            if key not in arguments:
                continue
            expected_type = rule.get("type")
            if expected_type == "string" and not isinstance(arguments[key], str):
                issues.append(f"invalid_argument_type:{key}:string")
            if expected_type == "object" and not isinstance(arguments[key], dict):
                issues.append(f"invalid_argument_type:{key}:object")
            if expected_type == "array" and not isinstance(arguments[key], list):
                issues.append(f"invalid_argument_type:{key}:array")
        return issues
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/appv21/test_tool_registry.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appV2.1/appv21/tools/registry.py tests/appv21/test_tool_registry.py
git commit -m "feat(appv21): add tool registry"
```

---

### Task 8: Convert ToolBroker to Registry-Backed Mediator

**Files:**
- Modify: `appV2.1/appv21/tools/broker.py`
- Modify: `appV2.1/appv21/runtime/services.py`
- Test: `tests/appv21/test_tool_registry.py`
- Test: `tests/appv21/test_agentic_loop_next_phase.py`

- [ ] **Step 1: Add failing broker registry test**

```python
from pathlib import Path

from appv21.tools.broker import ToolBroker


def test_broker_specs_are_registry_backed(tmp_path: Path) -> None:
    broker = ToolBroker(root_path=tmp_path)

    specs = broker.tool_specs()

    assert {spec["name"] for spec in specs} == {"read_file", "repo_snapshot"}
    assert all("argument_schema" in spec for spec in specs)
    assert all("category" in spec for spec in specs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/appv21/test_tool_registry.py::test_broker_specs_are_registry_backed -q`

Expected: FAIL because specs do not include schemas/categories.

- [ ] **Step 3: Add default registry to broker**

In `ToolBroker.__init__`:

```python
from appv21.tools.definitions import ToolCategory, ToolDefinition
from appv21.tools.registry import ToolRegistry

self.registry = registry or ToolRegistry()
self._register_default_tools()
```

Add:

```python
def _register_default_tools(self) -> None:
    self.registry.register(
        ToolDefinition(
            name="repo_snapshot",
            category=ToolCategory.OBSERVE,
            argument_schema={"type": "object", "properties": {}, "additionalProperties": False},
            result_schema={"type": "object"},
            trust="runtime_observed",
            guidance="Use before planning; returns file and directory map only.",
            cacheable=True,
        )
    )
    self.registry.register(
        ToolDefinition(
            name="read_file",
            category=ToolCategory.INSPECT,
            argument_schema={
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
            result_schema={"type": "object"},
            trust="runtime_observed",
            guidance="Use for targeted file evidence; never infer file contents without this.",
        )
    )
```

- [ ] **Step 4: Replace hard-coded specs**

```python
def tool_specs(self) -> list[dict[str, Any]]:
    return [
        {
            "name": definition.name,
            "category": definition.category.value,
            "trust": definition.trust,
            "guidance": definition.guidance,
            "argument_schema": definition.argument_schema,
            "result_schema": definition.result_schema,
            "risk_level": definition.risk_level,
        }
        for definition in self.registry.list()
    ]
```

- [ ] **Step 5: Route validation through registry first**

```python
schema_errors = self.registry.validate_call(tool_name, arguments)
if schema_errors:
    return schema_errors
```

Then keep path/sensitive validation for `read_file`.

- [ ] **Step 6: Run focused tests**

Run: `uv run pytest tests/appv21/test_tool_registry.py tests/appv21/test_agentic_loop_next_phase.py::test_model_tool_specs_only_expose_callable_tools -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add appV2.1/appv21/tools/broker.py appV2.1/appv21/runtime/services.py tests/appv21/test_tool_registry.py
git commit -m "feat(appv21): make tool broker registry backed"
```

---

### Task 9: Store Raw Tool Payloads Outside Prompt Context

**Files:**
- Create: `appV2.1/appv21/tools/evidence_store.py`
- Modify: `appV2.1/appv21/tools/broker.py`
- Modify: `appV2.1/appv21/runtime/services.py`
- Modify: `appV2.1/appv21/runtime/agent_runtime.py`
- Test: `tests/appv21/test_tool_registry.py`

- [ ] **Step 1: Add failing raw payload retention test**

```python
from appv21.runtime.decisions import RuntimeDecision
from appv21.runtime.services import create_appv21_runtime_services
from appv21 import AppV21AgentRuntime


class ReadThenFinalizeProvider:
    provider_id = "read-then-finalize"

    def decide(self, prompt_payload: dict) -> RuntimeDecision:
        if not prompt_payload["world"]["world_refs"]:
            return RuntimeDecision(kind="tool_call", reason="read", payload={"tool_name": "read_file", "arguments": {"path": "README.md"}})
        return RuntimeDecision(kind="finalize", reason="noop", payload={"explicit_noop": True})


def test_tool_raw_payload_is_retained_by_ref_not_prompt(tmp_path) -> None:
    (tmp_path / "README.md").write_text("secret-free long content", encoding="utf-8")
    services = create_appv21_runtime_services(root_path=tmp_path, provider=ReadThenFinalizeProvider())

    result = AppV21AgentRuntime(root_path=tmp_path, services=services).run("Read README.")

    completed = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted"][0]["payload"]
    assert "payload_ref" in completed
    assert "content" not in completed
    assert services.tool_evidence_store.get(completed["payload_ref"])["payload"]["content"] == "secret-free long content"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/appv21/test_tool_registry.py::test_tool_raw_payload_is_retained_by_ref_not_prompt -q`

Expected: FAIL because raw result payload is embedded in events.

- [ ] **Step 3: Implement evidence store**

```python
"""Runtime-owned raw tool evidence storage."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class ToolEvidenceStore:
    def __init__(self) -> None:
        self._payloads: dict[str, dict[str, Any]] = {}

    def put(self, ref: str, payload: dict[str, Any]) -> None:
        self._payloads[ref] = deepcopy(payload)

    def get(self, ref: str) -> dict[str, Any]:
        return deepcopy(self._payloads[ref])

    def has(self, ref: str) -> bool:
        return ref in self._payloads
```

- [ ] **Step 4: Add service field**

In `services.py`:

```python
from appv21.tools.evidence_store import ToolEvidenceStore

tool_evidence_store: ToolEvidenceStore
```

Construct:

```python
tool_evidence_store=ToolEvidenceStore(),
```

- [ ] **Step 5: Change broker envelopes to include `payload_ref`**

In `tool_result_envelope()`, compute:

```python
tool_result_id = f"toolres_{uuid4().hex}"
payload_ref = f"world://tool_result/{tool_result_id}"
```

Return no raw `payload`:

```python
{
    "tool_result_id": tool_result_id,
    "tool_name": tool_name,
    "status": status,
    "trust": "runtime_observed" if tool_name in {"repo_snapshot", "read_file"} else "runtime_owned",
    "payload_ref": payload_ref,
    "prompt_summary": prompt_summary or self.compact_tool_result(payload),
    "evidence_refs": list(evidence_refs or []),
}
```

- [ ] **Step 6: Store raw payload in runtime before emitting event**

In `_record_tool_result()`:

```python
payload_ref = result["payload_ref"]
self.services.tool_evidence_store.put(payload_ref, {"payload": result.pop("_raw_payload"), "tool_name": result["tool_name"]})
```

If avoiding hidden keys, return `(envelope, raw_payload)` from broker and update call sites explicitly.

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/appv21/test_tool_registry.py tests/appv21/test_agentic_loop_next_phase.py -q`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add appV2.1/appv21/tools/evidence_store.py appV2.1/appv21/tools/broker.py appV2.1/appv21/runtime/services.py appV2.1/appv21/runtime/agent_runtime.py tests/appv21/test_tool_registry.py
git commit -m "feat(appv21): retain raw tool evidence by ref"
```

---

### Task 10: Add Context Stores, Budgets, and Selector

**Files:**
- Create: `appV2.1/appv21/context/stores.py`
- Create: `appV2.1/appv21/context/selector.py`
- Modify: `appV2.1/appv21/context/manager.py`
- Modify: `appV2.1/appv21/context/compactor.py`
- Test: `tests/appv21/test_context_system.py`

- [ ] **Step 1: Add failing budget/immutable evidence tests**

```python
from appv21.context.selector import ContextBudget, ContextSelector
from appv21.state.models import AgentState, RequestEnvelope, WorldRef


def test_context_selector_preserves_receipts_under_budget() -> None:
    state = AgentState(session_id="sess", run_id="run", request=RequestEnvelope(request_id="req", user_goal="Goal", root_path="."))
    for index in range(20):
        state.world.refs[f"world://tool_result/{index}"] = WorldRef(
            ref_id=f"world://tool_result/{index}",
            kind="tool_result",
            summary="x" * 100,
            payload={"index": index},
        )
    state.world.mutation_receipts["mut_1"] = object()
    state.world.verification_receipts["verify_1"] = {"status": "passed"}

    selected = ContextSelector(ContextBudget(max_world_refs=5, max_summary_chars=200)).select(state)

    assert len(selected.world_refs) <= 5
    assert selected.mutation_receipts == ["mut_1"]
    assert selected.verification_receipts == ["verify_1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/appv21/test_context_system.py -q`

Expected: FAIL because selector does not exist.

- [ ] **Step 3: Implement selector**

```python
"""Deterministic context relevance selection."""

from __future__ import annotations

from dataclasses import dataclass

from appv21.state.models import AgentState


@dataclass(frozen=True)
class ContextBudget:
    max_world_refs: int = 12
    max_summary_chars: int = 4000


@dataclass(frozen=True)
class SelectedContext:
    world_refs: list[dict]
    artifacts: list[str]
    mutation_leases: list[str]
    mutation_receipts: list[str]
    verification_receipts: list[str]
    world_digest: dict


class ContextSelector:
    def __init__(self, budget: ContextBudget | None = None) -> None:
        self.budget = budget or ContextBudget()

    def select(self, state: AgentState) -> SelectedContext:
        immutable_refs = {"world://repo_snapshot/latest"}
        latest_refs = list(state.world.refs)[-self.budget.max_world_refs :]
        chosen_ids = sorted(immutable_refs & set(state.world.refs))
        for ref_id in latest_refs:
            if ref_id not in chosen_ids and len(chosen_ids) < self.budget.max_world_refs:
                chosen_ids.append(ref_id)
        return SelectedContext(
            world_refs=[
                {
                    "ref_id": ref.ref_id,
                    "kind": ref.kind,
                    "summary": ref.summary[: self.budget.max_summary_chars],
                    "trust": ref.trust,
                }
                for ref_id in chosen_ids
                for ref in [state.world.refs[ref_id]]
            ],
            artifacts=list(state.world.artifacts),
            mutation_leases=list(state.world.mutation_leases),
            mutation_receipts=list(state.world.mutation_receipts),
            verification_receipts=list(state.world.verification_receipts),
            world_digest={
                "selected_world_refs": chosen_ids,
                "total_world_ref_count": len(state.world.refs),
                "immutable_receipts": [*state.world.mutation_receipts, *state.world.verification_receipts],
            },
        )
```

- [ ] **Step 4: Update DualContextManager to use ContextSelector**

Add:

```python
from appv21.context.selector import ContextSelector

def __init__(self, *, compactor: RuntimeContextCompactor | None = None, selector: ContextSelector | None = None) -> None:
    self.compactor = compactor or RuntimeContextCompactor()
    self.selector = selector or ContextSelector()
```

In `build_turn_context()`:

```python
selected = self.selector.select(state)
context = {
    "request": state.request.user_goal,
    "mode": state.mode,
    "conversation_summary": state.conversation.summary,
    "world_refs": selected.world_refs,
    "artifacts": selected.artifacts,
    "mutation_leases": selected.mutation_leases,
    "mutation_receipts": selected.mutation_receipts,
    "verification_receipts": selected.verification_receipts,
    "world_digest": selected.world_digest,
}
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/appv21/test_context_system.py tests/appv21/test_agentic_loop_next_phase.py::test_build_turn_context_bounds_world_refs_after_compaction -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add appV2.1/appv21/context/stores.py appV2.1/appv21/context/selector.py appV2.1/appv21/context/manager.py appV2.1/appv21/context/compactor.py tests/appv21/test_context_system.py
git commit -m "feat(appv21): add budgeted context selector"
```

---

### Task 11: Add Provider Registry and Runtime Cost Accounting

**Files:**
- Create: `appV2.1/appv21/runtime/provider_registry.py`
- Modify: `appV2.1/appv21/runtime/model_registry.py`
- Modify: `appV2.1/appv21/runtime/services.py`
- Modify: `appV2.1/appv21/runtime/agent_runtime.py`
- Modify: `appV2.1/appv21/state/models.py`
- Test: `tests/appv21/test_provider_registry.py`

- [ ] **Step 1: Add failing provider routing tests**

```python
from appv21.runtime.provider_registry import ProviderCapability, ProviderProfile, ProviderRegistry


def test_provider_registry_selects_by_role_and_capability() -> None:
    registry = ProviderRegistry(
        [
            ProviderProfile(provider_id="cheap", model_id="cheap/model", roles={"agent"}, capabilities={ProviderCapability.JSON_SCHEMA}),
            ProviderProfile(provider_id="reviewer", model_id="review/model", roles={"reviewer"}, capabilities={ProviderCapability.LONG_CONTEXT}),
        ]
    )

    assert registry.select(role="agent", required={ProviderCapability.JSON_SCHEMA}).provider_id == "cheap"
    assert registry.select(role="reviewer", required={ProviderCapability.LONG_CONTEXT}).provider_id == "reviewer"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/appv21/test_provider_registry.py -q`

Expected: FAIL because provider registry does not exist.

- [ ] **Step 3: Implement provider registry**

```python
"""Role and capability provider registry for AppV2.1."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ProviderCapability(str, Enum):
    JSON_SCHEMA = "json_schema"
    TOOL_CALLS = "tool_calls"
    STREAMING = "streaming"
    LONG_CONTEXT = "long_context"
    REASONING = "reasoning"
    VISION = "vision"


@dataclass(frozen=True)
class ProviderProfile:
    provider_id: str
    model_id: str
    roles: set[str]
    capabilities: set[ProviderCapability] = field(default_factory=set)
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    context_tokens: int = 0
    enabled_by_default: bool = False


class ProviderRegistry:
    def __init__(self, profiles: list[ProviderProfile] | None = None) -> None:
        self._profiles = profiles or [
            ProviderProfile(
                provider_id="deterministic-workspace",
                model_id="deterministic-runtime",
                roles={"agent", "planner", "verifier", "compactor", "reviewer"},
                capabilities={ProviderCapability.JSON_SCHEMA},
                enabled_by_default=True,
            )
        ]

    def select(self, *, role: str, required: set[ProviderCapability] | None = None) -> ProviderProfile:
        required = required or set()
        for profile in self._profiles:
            if role in profile.roles and required <= profile.capabilities:
                return profile
        raise LookupError(f"no_provider_for_role:{role}")
```

- [ ] **Step 4: Add cost fields to CostState**

In `state/models.py`:

```python
@dataclass
class CostState:
    model_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
```

- [ ] **Step 5: Add provider usage snapshot integration**

After each provider decision in `run_turn()`:

```python
usage_snapshot = getattr(self.services.provider, "usage_snapshot", None)
if callable(usage_snapshot):
    self._apply(state, [RuntimeEvent("ModelUsageRecorded", usage_snapshot(reset=True))])
else:
    self._apply(state, [RuntimeEvent("ModelUsageRecorded", {"model_calls": 1})])
```

Add reducer case:

```python
elif event_type == "ModelUsageRecorded":
    state.costs.model_calls += int(payload.get("model_calls", 0))
    state.costs.input_tokens += int(payload.get("input_tokens", payload.get("prompt_tokens", 0)))
    state.costs.output_tokens += int(payload.get("output_tokens", payload.get("completion_tokens", 0)))
    state.costs.total_tokens += int(payload.get("total_tokens", 0))
    state.costs.cost += float(payload.get("cost", 0.0))
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/appv21/test_provider_registry.py tests/appv21 -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add appV2.1/appv21/runtime/provider_registry.py appV2.1/appv21/runtime/model_registry.py appV2.1/appv21/runtime/services.py appV2.1/appv21/runtime/agent_runtime.py appV2.1/appv21/runtime/reducer.py appV2.1/appv21/state/models.py tests/appv21/test_provider_registry.py
git commit -m "feat(appv21): add provider registry and usage accounting"
```

---

### Task 12: Generalize Planner Contract and Lifecycle

**Files:**
- Create: `appV2.1/appv21/extensions/planning_contracts.py`
- Modify: `appV2.1/appv21/extensions/planner.py`
- Modify: `appV2.1/appv21/runtime/reducer.py`
- Modify: `appV2.1/appv21/state/models.py`
- Test: `tests/appv21/test_planning_artifacts_verification.py`

- [ ] **Step 1: Add failing planner lifecycle test**

```python
from appv21.extensions.planning_contracts import PlanLifecycle, PlanningRequest


def test_planning_contract_can_request_more_observation() -> None:
    request = PlanningRequest(
        request_id="req",
        world_refs=[],
        constraints=["no secrets"],
        active_skills=[],
        previous_failures=[],
    )

    assert request.world_refs == []
    assert PlanLifecycle.PROPOSED.value == "proposed"
    assert PlanLifecycle.ACCEPTED.value == "accepted"
    assert PlanLifecycle.REJECTED.value == "rejected"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/appv21/test_planning_artifacts_verification.py::test_planning_contract_can_request_more_observation -q`

Expected: FAIL because planning contracts do not exist.

- [ ] **Step 3: Implement planning contracts**

```python
"""General planning contracts for AppV2.1 extensions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PlanLifecycle(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REVISED = "revised"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


@dataclass(frozen=True)
class PlanningRequest:
    request_id: str
    world_refs: list[str]
    constraints: list[str]
    active_skills: list[dict[str, Any]]
    previous_failures: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class PlanProposal:
    plan_id: str
    lifecycle: PlanLifecycle
    evidence_refs: list[str]
    intent: str
    steps: list[dict[str, Any]]
    mutation_intent: dict[str, Any] = field(default_factory=dict)
    verification_intent: dict[str, Any] = field(default_factory=dict)
    unknowns: list[str] = field(default_factory=list)
    needs_observation: list[str] = field(default_factory=list)
```

- [ ] **Step 4: Update PlannerExtension to emit proposal-shaped dicts**

Keep workspace cleanup behavior, but return:

```python
{
    "plan_id": f"plan_{uuid4().hex}",
    "lifecycle": "proposed",
    "evidence_refs": ["world://repo_snapshot/latest"],
    "intent": "workspace cleanup from observed repo state",
    ...
}
```

- [ ] **Step 5: Update PlanAccepted event payload**

In runtime plan route, include:

```python
"plan_id": plan.get("plan_id"),
"lifecycle": "accepted",
"evidence_refs": plan.get("evidence_refs", []),
```

In `PlanState`, add:

```python
plan_id: str = ""
lifecycle: str = "accepted"
evidence_refs: list[str] = field(default_factory=list)
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/appv21/test_planning_artifacts_verification.py tests/appv21 -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add appV2.1/appv21/extensions/planning_contracts.py appV2.1/appv21/extensions/planner.py appV2.1/appv21/runtime/agent_runtime.py appV2.1/appv21/runtime/reducer.py appV2.1/appv21/state/models.py tests/appv21/test_planning_artifacts_verification.py
git commit -m "feat(appv21): add general planning lifecycle"
```

---

### Task 13: Harden Artifact Lifecycle Validation

**Files:**
- Modify: `appV2.1/appv21/state/models.py`
- Modify: `appV2.1/appv21/validators/artifacts.py`
- Test: `tests/appv21/test_planning_artifacts_verification.py`

- [ ] **Step 1: Add failing artifact lifecycle tests**

```python
from appv21.validators.artifacts import ArtifactValidator
from appv21.state.models import Artifact


def test_runtime_verified_artifact_requires_known_lifecycle_and_evidence(tmp_path) -> None:
    state = make_state(tmp_path)
    artifact = Artifact(
        artifact_id="a1",
        kind="final_report",
        content={},
        producer="appv21_runtime",
        trust="runtime_verified",
        lifecycle="runtime_verified",
        evidence_refs=[],
    )

    assert ArtifactValidator().validate(artifact, state) == ["runtime_verified_requires_evidence"]


def test_artifact_rejects_unknown_lifecycle(tmp_path) -> None:
    state = make_state(tmp_path)
    artifact = Artifact(
        artifact_id="a1",
        kind="final_report",
        content={},
        producer="appv21_runtime",
        lifecycle="magic",
    )

    assert ArtifactValidator().validate(artifact, state) == ["unknown_artifact_lifecycle:magic"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/appv21/test_planning_artifacts_verification.py -q`

Expected: FAIL because lifecycle checks are incomplete.

- [ ] **Step 3: Add lifecycle/type constants to ArtifactValidator**

```python
KNOWN_LIFECYCLES = {"proposed", "runtime_observed", "runtime_generated", "runtime_verified", "rejected", "superseded"}
KNOWN_KINDS = {"plan", "file_change_manifest", "verification_report", "final_report", "context_summary", "run_matrix", "user_approval"}
```

In `validate()`:

```python
if artifact.lifecycle not in KNOWN_LIFECYCLES:
    issues.append(f"unknown_artifact_lifecycle:{artifact.lifecycle}")
if artifact.kind not in KNOWN_KINDS:
    issues.append(f"unknown_artifact_kind:{artifact.kind}")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/appv21/test_planning_artifacts_verification.py tests/appv21/test_runtime_first_probe.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add appV2.1/appv21/validators/artifacts.py tests/appv21/test_planning_artifacts_verification.py
git commit -m "feat(appv21): enforce artifact lifecycle"
```

---

### Task 14: Add Verification Policy, File Hash Receipts, and Freshness Checks

**Files:**
- Create: `appV2.1/appv21/validators/verification.py`
- Modify: `appV2.1/appv21/extensions/verifier.py`
- Modify: `appV2.1/appv21/tools/broker.py`
- Modify: `appV2.1/appv21/state/models.py`
- Test: `tests/appv21/test_planning_artifacts_verification.py`

- [ ] **Step 1: Add failing verification freshness test**

```python
from appv21.validators.verification import VerificationPolicy


def test_verification_policy_rejects_verify_without_mutation_receipt(tmp_path) -> None:
    state = make_state(tmp_path)

    issues = VerificationPolicy().validate_before_verify(state, {"manifest_path": "docs/workspace_manifest.json"})

    assert issues == ["verification_requires_recent_mutation_or_explicit_noop"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/appv21/test_planning_artifacts_verification.py::test_verification_policy_rejects_verify_without_mutation_receipt -q`

Expected: FAIL because verification policy does not exist.

- [ ] **Step 3: Implement VerificationPolicy**

```python
"""Verification policy and freshness checks."""

from __future__ import annotations

from appv21.state.models import AgentState


class VerificationPolicy:
    def validate_before_verify(self, state: AgentState, verification_intent: dict) -> list[str]:
        if verification_intent.get("explicit_noop"):
            return []
        if not state.world.mutation_receipts:
            return ["verification_requires_recent_mutation_or_explicit_noop"]
        latest = next(reversed(state.world.mutation_receipts.values()))
        if latest.status != "applied":
            return [f"latest_mutation_not_applied:{latest.status}"]
        return []
```

- [ ] **Step 4: Record file hashes in mutation receipts**

Add to `MutationReceipt`:

```python
file_hashes: dict[str, str] = field(default_factory=dict)
```

In `ToolBroker.apply_mutation_lease()`, after writes/moves:

```python
file_hashes = {path: _sha256(self._safe_path(path)) for path in touched if self._safe_path(path) and self._safe_path(path).is_file()}
```

Add helper:

```python
def _sha256(path: Path | None) -> str:
    if path is None or not path.is_file():
        return ""
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()
```

- [ ] **Step 5: Use policy before verifier execution**

In runtime verify route:

```python
issues = self.services.verification_policy.validate_before_verify(state, decision.payload)
if issues:
    self._apply(state, [RuntimeEvent("VerificationRejected", {"decision_id": decision.decision_id, "issues": issues})])
    self._fail(state, "verification_rejected", {"issues": issues})
    return
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/appv21/test_planning_artifacts_verification.py tests/appv21 -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add appV2.1/appv21/validators/verification.py appV2.1/appv21/extensions/verifier.py appV2.1/appv21/tools/broker.py appV2.1/appv21/runtime/agent_runtime.py appV2.1/appv21/runtime/services.py appV2.1/appv21/state/models.py tests/appv21/test_planning_artifacts_verification.py
git commit -m "feat(appv21): add verification freshness policy"
```

---

### Task 15: Add Session Replay and Inspection

**Files:**
- Create: `appV2.1/appv21/runtime/replay.py`
- Modify: `appV2.1/appv21/runtime/session_store.py`
- Test: `tests/appv21/test_session_replay_surfaces.py`

- [ ] **Step 1: Add failing replay test**

```python
from appv21.runtime.replay import replay_run, inspect_sessions
from appv21.runtime.session_store import JsonlSessionStore
from appv21.state.events import RuntimeEvent


def test_replay_rebuilds_run_events(tmp_path) -> None:
    store = JsonlSessionStore(tmp_path / "session.jsonl")
    store.append_event(session_id="sess", run_id="run", event=RuntimeEvent("UserMessageReceived", {"content": "hi"}))
    store.append_event(session_id="sess", run_id="run", event=RuntimeEvent("RunCompleted", {"status": "completed"}))

    replayed = replay_run(store, session_id="sess", run_id="run")
    summary = inspect_sessions(store)

    assert [event.event_type for event in replayed] == ["UserMessageReceived", "RunCompleted"]
    assert summary["sessions"]["sess"]["runs"] == ["run"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/appv21/test_session_replay_surfaces.py::test_replay_rebuilds_run_events -q`

Expected: FAIL because replay helpers do not exist.

- [ ] **Step 3: Implement replay helpers**

```python
"""Replay and inspection helpers for AppV2.1 sessions."""

from __future__ import annotations

from appv21.runtime.session_store import JsonlSessionStore
from appv21.state.events import RuntimeEvent


def replay_run(store: JsonlSessionStore, *, session_id: str, run_id: str) -> list[RuntimeEvent]:
    return store.events_for_run(session_id=session_id, run_id=run_id)


def inspect_sessions(store: JsonlSessionStore) -> dict:
    sessions: dict[str, dict[str, list[str]]] = {}
    for row in store.read_all():
        session_id = str(row.get("session_id") or "")
        run_id = str(row.get("run_id") or "")
        if not session_id or not run_id:
            continue
        sessions.setdefault(session_id, {"runs": []})
        if run_id not in sessions[session_id]["runs"]:
            sessions[session_id]["runs"].append(run_id)
    return {"sessions": sessions}
```

- [ ] **Step 4: Add partial JSONL corruption handling**

In `JsonlSessionStore.read_all()`:

```python
try:
    rows.append(json.loads(line))
except json.JSONDecodeError:
    rows.append({"event_type": "SessionCorruptionDetected", "payload": {"line_preview": line[:120]}})
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/appv21/test_session_replay_surfaces.py tests/appv21/test_agentic_loop_next_phase.py::test_durable_pause_resume_rehydrates_from_jsonl -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add appV2.1/appv21/runtime/replay.py appV2.1/appv21/runtime/session_store.py tests/appv21/test_session_replay_surfaces.py
git commit -m "feat(appv21): add session replay inspection"
```

---

### Task 16: Add Thin Surface Contract Tests and Package Boundary

**Files:**
- Modify: `appV2.1/appv21/surfaces/__init__.py`
- Create: `appV2.1/appv21/surfaces/cli.py`
- Modify: `scripts/live_appv21_runtime_probe.py`
- Test: `tests/appv21/test_session_replay_surfaces.py`

- [ ] **Step 1: Add failing surface contract test**

```python
from appv21.surfaces.cli import run_cli_request


def test_cli_surface_uses_runtime_facade(tmp_path) -> None:
    (tmp_path / "a.md").write_text("a", encoding="utf-8")

    result = run_cli_request(root_path=tmp_path, user_goal="Clean up workspace.")

    assert result["status"] == "completed"
    assert result["summary"] == "AppV2.1 runtime-first decision loop completed."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/appv21/test_session_replay_surfaces.py::test_cli_surface_uses_runtime_facade -q`

Expected: FAIL because CLI surface does not exist.

- [ ] **Step 3: Implement thin CLI adapter**

```python
"""Thin CLI/runtime adapter for AppV2.1."""

from __future__ import annotations

from pathlib import Path

from appv21 import AppV21AgentRuntime
from appv21.providers.base import AgentProvider
from appv21.runtime.services import create_appv21_runtime_services


def run_cli_request(*, root_path: str | Path, user_goal: str, provider: AgentProvider | None = None) -> dict:
    services = create_appv21_runtime_services(root_path=root_path, provider=provider) if provider is not None else None
    return AppV21AgentRuntime(root_path=root_path, services=services).run(user_goal)
```

- [ ] **Step 4: Update probe script to use adapter**

In `scripts/live_appv21_runtime_probe.py`, replace direct runtime construction for deterministic path with:

```python
from appv21.surfaces.cli import run_cli_request

result = run_cli_request(root_path=repo, user_goal="Clean up and organize this workspace, move notes/logs/artifacts, and create a workspace manifest.", provider=provider)
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/appv21/test_session_replay_surfaces.py tests/appv21 -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add appV2.1/appv21/surfaces/__init__.py appV2.1/appv21/surfaces/cli.py scripts/live_appv21_runtime_probe.py tests/appv21/test_session_replay_surfaces.py
git commit -m "feat(appv21): add thin runtime surface adapter"
```

---

### Task 17: Expand Eval Matrix and Documentation

**Files:**
- Modify: `scripts/live_appv21_file_management_matrix_probe.py`
- Modify: `scripts/live_appv21_nested_file_management_matrix_probe.py`
- Create: `scripts/live_appv21_state_machine_matrix_probe.py`
- Modify: `docs/superpowers/specs/2026-06-15-appv21-pi-hermes-architecture-gap-design.md`
- Test: `tests/appv21/test_agentic_loop_next_phase.py`

- [ ] **Step 1: Add failing probe report schema test**

```python
def test_matrix_reports_include_architecture_phase_fields(tmp_path: Path) -> None:
    from scripts.live_appv21_file_management_matrix_probe import _build_report

    report = _build_report(repo=tmp_path, result={"status": "completed", "events": []}, provider=None, max_turns=1)

    assert "state_machine_matrix" in report
    assert "decision_validation_matrix" in report
    assert "tool_registry_matrix" in report
    assert "context_budget_matrix" in report
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/appv21/test_agentic_loop_next_phase.py::test_matrix_reports_include_architecture_phase_fields -q`

Expected: FAIL because reports do not include the new fields.

- [ ] **Step 3: Add matrix fields to report builders**

For each matrix report builder, add:

```python
"state_machine_matrix": _event_matrix(events, {"ModeChanged", "DecisionRejected", "LoopProgressRejected"}),
"decision_validation_matrix": _event_matrix(events, {"DecisionProposed", "DecisionRejected"}),
"tool_registry_matrix": _event_matrix(events, {"ToolCallCompleted", "ToolCallDenied"}),
"context_budget_matrix": _event_matrix(events, {"PromptContextPrepared", "ContextCompacted", "ContextCompactionRequested"}),
```

Add helper if missing:

```python
def _event_matrix(events: list[dict], event_types: set[str]) -> list[dict]:
    return [
        {
            "event_id": event.get("event_id"),
            "event_type": event.get("event_type"),
            "payload_keys": sorted((event.get("payload") or {}).keys()),
        }
        for event in events
        if event.get("event_type") in event_types
    ]
```

- [ ] **Step 4: Add state machine live probe**

Create `scripts/live_appv21_state_machine_matrix_probe.py`:

```python
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "appV2.1"))

from appv21 import AppV21AgentRuntime
from appv21.runtime.decisions import RuntimeDecision
from appv21.runtime.services import create_appv21_runtime_services


class IllegalFinalizeProvider:
    provider_id = "illegal-finalize"

    def decide(self, prompt_payload: dict) -> RuntimeDecision:
        return RuntimeDecision(kind="finalize", reason="too early", payload={"explicit_noop": True})


def main() -> int:
    repo = ROOT / "live_appv21_state_machine_repo"
    repo.mkdir(exist_ok=True)
    services = create_appv21_runtime_services(root_path=repo, provider=IllegalFinalizeProvider())
    result = AppV21AgentRuntime(root_path=repo, services=services, max_turns=1).run("Trigger illegal transition.")
    out = ROOT / "plan" / "live-appv21-state-machine-matrix-probe.json"
    out.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(f"OUTPUT_PATH={out}")
    print(json.dumps({"status": result["status"], "reason": result.get("reason")}, sort_keys=True))
    return 0 if result["status"] == "failed" and result.get("reason") == "invalid_transition" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Update spec status**

Update the gap spec with an implementation status table:

```markdown
## Implementation Status

| Area | Status | Evidence |
|---|---|---|
| RuntimeStateMachine | Implemented | `appV2.1/appv21/runtime/state_machine.py` |
| DecisionValidator | Implemented | `appV2.1/appv21/runtime/decision_validator.py` |
| ToolBroker Registry | Implemented | `appV2.1/appv21/tools/registry.py` |
| Raw Tool Evidence Store | Implemented | `appV2.1/appv21/tools/evidence_store.py` |
| Context Selector | Implemented | `appV2.1/appv21/context/selector.py` |
| Provider Registry | Implemented | `appV2.1/appv21/runtime/provider_registry.py` |
| Session Replay | Implemented | `appV2.1/appv21/runtime/replay.py` |
```

- [ ] **Step 6: Run final AppV2.1 QA**

Run: `uv run pytest tests/appv21 tests/test_appv2_prompt_quality.py -q`

Expected: PASS.

Run: `uv run python scripts/live_appv21_state_machine_matrix_probe.py`

Expected: `{"reason": "invalid_transition", "status": "failed"}`

- [ ] **Step 7: Commit**

```bash
git add scripts/live_appv21_file_management_matrix_probe.py scripts/live_appv21_nested_file_management_matrix_probe.py scripts/live_appv21_state_machine_matrix_probe.py docs/superpowers/specs/2026-06-15-appv21-pi-hermes-architecture-gap-design.md tests/appv21/test_agentic_loop_next_phase.py
git commit -m "test(appv21): expand architecture hardening matrix"
```

---

## Final Verification

- [ ] Run full focused QA:

```bash
uv run pytest tests/appv21 tests/test_appv2_prompt_quality.py -q
```

Expected: all tests pass.

- [ ] Run compile check:

```bash
uv run python -m compileall -q appV2.1 tests/appv21 scripts/live_appv21_state_machine_matrix_probe.py
```

Expected: exit code `0`.

- [ ] Run required probes:

```bash
uv run python scripts/live_appv21_runtime_probe.py
uv run python scripts/live_appv21_agent_loop_probe.py
uv run python scripts/live_appv21_bad_mutation_probe.py
uv run python scripts/live_appv21_pause_resume_probe.py
uv run python scripts/live_appv21_context_compaction_probe.py
uv run python scripts/live_appv21_planner_disabled_probe.py
uv run python scripts/live_appv21_state_machine_matrix_probe.py
```

Expected: existing success probes complete, and the state-machine negative probe fails for the expected `invalid_transition` reason.

---

## Spec Coverage Checklist

- RuntimeStateMachine: Tasks 3-5.
- DecisionValidator: Tasks 1-2.
- Rejection taxonomy: Tasks 1, 3, 5.
- Loop progress detection: Task 5.
- Tool registry: Tasks 6-8.
- Tool argument schema validation: Task 7.
- Tool result envelope and raw payload refs: Task 9.
- Denial evidence: Tasks 8-9.
- Context budgets and selection: Task 10.
- Immutable receipt preservation: Task 10.
- Provider role/capability routing: Task 11.
- Cost accounting: Task 11.
- General planning lifecycle: Task 12.
- Artifact lifecycle: Task 13.
- Verification freshness and file hashes: Task 14.
- Session replay/inspection: Task 15.
- Thin surface adapter: Task 16.
- Live eval matrix: Task 17.

Known follow-up after this plan:

- Background maintenance and plugin ecosystem are intentionally deferred until these runtime/tool/context boundaries are stable.
- Model-backed general planner is intentionally deferred until the schema-first planner lifecycle is implemented and tested.
