import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.1"))

from appv21.runtime import rejections
from appv21.runtime.decision_validator import DecisionValidator
from appv21.runtime.decisions import RuntimeDecision
from appv21.state.models import AgentState, RequestEnvelope, WorldRef


def make_state(tmp_path: Path) -> AgentState:
    return AgentState(
        session_id="sess",
        run_id="run",
        request=RequestEnvelope(request_id="req", user_goal="Clean up.", root_path=str(tmp_path)),
    )


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
