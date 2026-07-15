from __future__ import annotations

import json
from pathlib import Path

from evals.schema import load_scenarios
from evals.verify_run import verify_run, write_verification


def test_verify_run_accepts_one_complete_twenty_one_prompt_session(tmp_path: Path) -> None:
    _write_complete_run(tmp_path)

    verification = verify_run(tmp_path)
    write_verification(verification, tmp_path)

    assert verification.passed is True
    assert verification.errors == ()
    assert verification.prompt_count == 21
    assert verification.conversation_count == 21
    assert verification.turn_ready_count == 21
    payload = json.loads((tmp_path / "verification.json").read_text(encoding="utf-8"))
    assert payload["passed"] is True


def test_verify_run_rejects_missing_footer_checkpoint_and_session_drift(tmp_path: Path) -> None:
    _write_complete_run(tmp_path)
    trace_path = tmp_path / "trace.jsonl"
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    removed = False
    kept: list[dict[str, object]] = []
    for event in events:
        if event.get("event") == "turn_ready" and not removed:
            removed = True
            continue
        kept.append(event)
    trace_path.write_text("".join(json.dumps(event) + "\n" for event in kept), encoding="utf-8")
    result_path = sorted((tmp_path / "runs").glob("*/result.json"))[0]
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["session_id"] = "drifted-session"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    verification = verify_run(tmp_path)

    assert verification.passed is False
    assert "expected 21 turn_ready events, found 20" in verification.errors
    assert "results span 2 session ids" in verification.errors


def test_verify_run_rejects_tui_session_identity_drift(tmp_path: Path) -> None:
    _write_complete_run(tmp_path)
    trace_path = tmp_path / "trace.jsonl"
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    ready = next(event for event in events if event.get("event") == "tui_ready")
    ready["session_id"] = "different-session"
    trace_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")

    verification = verify_run(tmp_path)

    assert verification.passed is False
    assert "tui_ready session identity does not match scenario results" in verification.errors


def test_verify_run_requires_context_confidence_after_compaction(tmp_path: Path) -> None:
    _write_complete_run(tmp_path)
    result_path = sorted((tmp_path / "runs").glob("*/result.json"))[0]
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result.pop("context_confidence")
    result_path.write_text(json.dumps(result), encoding="utf-8")
    trace_path = tmp_path / "trace.jsonl"
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    next(event for event in events if event.get("event") == "turn_ready").pop("context_confidence")
    trace_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")

    verification = verify_run(tmp_path)

    assert verification.passed is False
    assert "one or more results lack context confidence" in verification.errors
    assert "one or more turn_ready events lack context confidence" in verification.errors


def _write_complete_run(root: Path) -> None:
    scenarios = load_scenarios()
    conversation: list[dict[str, object]] = []
    events: list[dict[str, object]] = [
        {
            "event": "tui_ready",
            "run_id": "run-live",
            "session_id": "session-live",
            "session_path": "/tmp/session-live.jsonl",
        },
        {"event": "model_selected", "provider": "stepfun", "model": "stepfun/step-3.7-flash"},
        {"event": "extension_command", "status": "ok"},
        {"event": "capability_granted", "status": "ok"},
        {"event": "user_command_interrupt", "status": "ok", "interrupt_count": 2},
        {"event": "process_event", "process_id": "p1", "process_state": "terminated"},
        {"event": "compaction_end", "status": "ok", "trigger": "threshold", "compression_count": 1},
        {"event": "tool_end", "tool": "read", "status": "ok"},
        {"event": "tool_end", "tool": "write", "status": "ok"},
        {"event": "tool_end", "tool": "edit", "status": "ok"},
        {"event": "tool_end", "tool": "bash", "status": "ok", "operation": "search"},
        {"event": "tool_end", "tool": "read", "status": "error", "reason_code": "before_hook_block"},
        {"event": "tool_end", "tool": "spawn_subagent", "status": "ok"},
        *(
            {"event": "tool_end", "tool": "process", "status": "ok", "action": action}
            for action in ("start", "poll", "write", "interrupt")
        ),
    ]
    for index, scenario in enumerate(scenarios, start=1):
        turn_id = f"turn-{index}"
        prompt = f"prompt {index}"
        response = f"response {index}"
        result = {
            "scenario_id": scenario.id,
            "status": "passed",
            "model_provider": "stepfun",
            "model_id": "stepfun/step-3.7-flash",
            "verifier_exit_codes": [0],
            "turns": 1,
            "compactions": 1 if index == 20 else 0,
            "duration_ms": 10,
            "failure_tail": None,
            "session_id": "session-live",
            "session_path": "/tmp/session-live.jsonl",
            "turn_id": turn_id,
            "prompt": prompt,
            "response": response,
            "context_tokens": index * 1_000,
            "context_window": 256_000,
            "context_percent": index / 2.56,
            "context_estimated": False,
            "context_confidence": "provider_real",
            "fault_domain": None,
            "failure_evidence": None,
        }
        path = root / "runs" / scenario.id / "result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result), encoding="utf-8")
        conversation.append(
            {"turn_id": turn_id, "prompt": prompt, "response": response, "status": "ok"}
        )
        events.extend(
            [
                {"event": "turn_start", "turn_id": turn_id},
                {"event": "turn_end", "turn_id": turn_id, "status": "ok"},
                {
                    "event": "turn_ready",
                    "status": "ok",
                    "context_tokens": index * 1_000,
                    "context_window": 256_000,
                    "context_percent": index / 2.56,
                    "context_estimated": False,
                    "context_confidence": "provider_real",
                    "compression_count": 1 if index >= 20 else 0,
                },
            ]
        )
    events.append({"event": "shutdown", "status": "ok"})
    (root / "conversation.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in conversation),
        encoding="utf-8",
    )
    (root / "trace.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    marker = root / "workspace/scenarios/03-python-parser-refactor/SKILL_APPLIED.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("acceptance-audit skill applied\n", encoding="utf-8")
