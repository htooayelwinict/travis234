import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.1"))

from appv21 import AppV21AgentRuntime
from appv21.runtime.decisions import RuntimeDecision
from appv21.runtime.decisions import KNOWN_DECISION_KINDS
from appv21.runtime.services import create_appv21_runtime_services
from appv21.runtime.state_machine import TARGET_MODE_BY_DECISION, RuntimeStateMachine, unmapped_decision_kinds


class QueueProvider:
    provider_id = "queue"

    def __init__(self, decisions: list[RuntimeDecision]) -> None:
        self.decisions = decisions

    def decide(self, prompt_payload: dict) -> RuntimeDecision:
        return self.decisions.pop(0)


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


def test_state_machine_reports_unknown_modes_separately() -> None:
    machine = RuntimeStateMachine()

    rejection = machine.validate_transition("TYPO", RuntimeDecision(kind="observe", reason="map"))

    assert rejection == "invalid_mode:TYPO"


def test_state_machine_detects_repeated_nonproductive_decisions() -> None:
    machine = RuntimeStateMachine(max_repeated_decisions=3)
    decision = RuntimeDecision(kind="observe", reason="again")

    assert machine.record_progress(decision, changed=False) is None
    assert machine.record_progress(decision, changed=False) is None
    assert machine.record_progress(decision, changed=False) == "repeated_loop:observe"


def test_state_machine_counts_only_consecutive_nonproductive_decisions() -> None:
    machine = RuntimeStateMachine(max_repeated_decisions=3)

    assert machine.record_progress(RuntimeDecision(kind="observe", reason="again"), changed=False) is None
    assert machine.record_progress(RuntimeDecision(kind="plan", reason="again"), changed=False) is None
    assert machine.record_progress(RuntimeDecision(kind="observe", reason="again"), changed=False) is None


def test_state_machine_progress_can_be_reset_between_runs() -> None:
    machine = RuntimeStateMachine(max_repeated_decisions=2)
    decision = RuntimeDecision(kind="observe", reason="again")

    assert machine.record_progress(decision, changed=False) is None
    machine.reset_progress()

    assert machine.record_progress(decision, changed=False) is None


def test_state_machine_target_modes_cover_canonical_decision_kinds() -> None:
    assert set(TARGET_MODE_BY_DECISION) == KNOWN_DECISION_KINDS
    assert unmapped_decision_kinds() == set()


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


def test_runtime_rejects_illegal_plan_transition_from_start(tmp_path: Path) -> None:
    provider = QueueProvider([RuntimeDecision(kind="plan", reason="too early")])
    services = create_appv21_runtime_services(root_path=tmp_path, provider=provider)

    result = AppV21AgentRuntime(root_path=tmp_path, services=services, max_turns=1).run("Plan too early.")

    assert result["status"] == "failed"
    assert result["reason"] == "invalid_transition"
    assert any(
        event["event_type"] == "DecisionRejected" and event["payload"]["reason"] == "invalid_transition:START->plan"
        for event in result["events"]
    )


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
