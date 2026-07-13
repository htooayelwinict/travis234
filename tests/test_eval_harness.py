from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
from pathlib import Path

import pytest

import evals.run_sdlc_eval as run_sdlc_eval
from evals.fixtures import build_fixture
from evals.feature_audit import FeatureAudit, REQUIRED_FEATURES
from evals.report import sanitized_tail, write_reports
from evals.run_sdlc_eval import main, run_scenario
from evals.schema import Scenario, ScenarioResult, load_scenarios
from evals.scoring import aggregate_score
from evals.tui_driver import TuiDriver


class FakeDriver:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.waits: list[tuple[str, float]] = []
        self.closed = False
        self.conversation_path: Path | None = None
        self.pending_prompt: str | None = None
        self.turn_count = 0

    def wait_for_event(self, event_type: str, timeout: float):
        self.waits.append((event_type, timeout))
        if event_type == "tui_ready":
            return {
                "event": event_type,
                "status": "ok",
                "session_id": "session-live",
                "session_path": "/tmp/session-live.jsonl",
            }
        if event_type == "turn_end":
            self.turn_count += 1
            turn_id = f"turn-{self.turn_count}"
            if self.conversation_path is not None and self.pending_prompt is not None:
                with self.conversation_path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(
                            {
                                "turn_id": turn_id,
                                "prompt": self.pending_prompt,
                                "response": f"completed {self.pending_prompt}",
                                "status": "ok",
                            }
                        )
                        + "\n"
                    )
            return {"event": event_type, "status": "ok", "turn_id": turn_id}
        if event_type == "turn_ready":
            return {
                "event": event_type,
                "status": "ok",
                "context_tokens": self.turn_count * 1_000,
                "context_window": 256_000,
                "context_percent": self.turn_count / 2.56,
                "context_estimated": False,
                "context_confidence": "provider_real",
                "compression_count": 0,
            }
        return {"event": event_type, "status": "ok"}

    def select_model(self, query: str, index: int, timeout: float):
        self.lines.extend([f"/model {query}", f"<select:{index}>"])
        return {
            "provider": "stepfun" if query == "stepfun/step-3.7-flash" else "openrouter",
            "model": query if "/" in query else "xiaomi/mimo",
        }

    def send_line(self, text: str) -> None:
        self.lines.append(text)
        if text.startswith("SDLC scenario "):
            self.pending_prompt = text

    def send_key(self, data: bytes) -> None:
        self.lines.append(f"<key:{data.hex()}>")

    def close(self) -> None:
        self.closed = True


class _RunningProcess:
    pid = 12345
    returncode = None

    def poll(self):
        return None


def test_driver_sends_turns_and_compacts_at_declared_intervals(tmp_path: Path) -> None:
    scenario = Scenario(
        id="00-canary",
        setup="canary",
        turns=("first", "second", "third"),
        compact_after=(2,),
        verifiers=((sys.executable, "-c", "raise SystemExit(0)"),),
        timeout_seconds=10,
    )
    driver = FakeDriver()

    result = run_scenario(
        scenario,
        root=tmp_path / "runs",
        dotenv=tmp_path / ".env",
        driver_factory=lambda command, cwd, trace: driver,
    )

    assert driver.lines == [
        "/model mimo", "<select:1>", "first", "second", "/compact", "third", "/exit"
    ]
    assert result.verifier_exit_codes == (0,)
    assert result.status == "passed"
    assert driver.closed is True


def test_manifest_contains_exactly_21_unique_scenarios() -> None:
    scenarios = load_scenarios()
    assert len(scenarios) == 21
    assert len({item.id for item in scenarios}) == 21
    assert [item.id[:2] for item in scenarios] == [f"{index:02d}" for index in range(1, 22)]
    assert all(item.turns and item.verifiers for item in scenarios)
    assert {index for item in scenarios for index in item.compact_after} <= {1, 2, 3}


def test_session_resume_smoke_uses_two_processes_and_one_jsonl(tmp_path: Path) -> None:
    from evals.session_resume_smoke import run_smoke

    result = run_smoke(
        workspace=tmp_path / "workspace",
        agent_dir=tmp_path / "agent",
        marker="remember-7f31",
    )

    assert result["first_exit_code"] == 0
    assert result["continued_exit_code"] == 0
    assert result["first_session_path"] == result["continued_session_path"]
    assert result["first_session_id"] == result["continued_session_id"]
    assert result["jsonl_count"] == 1
    assert result["restored_marker"] == "remember-7f31"


def test_fixture_builds_are_deterministic_and_secret_free(tmp_path: Path) -> None:
    for scenario in load_scenarios():
        left = build_fixture(scenario.setup, tmp_path / "left" / scenario.id)
        right = build_fixture(scenario.setup, tmp_path / "right" / scenario.id)
        assert _tree_hash(left) == _tree_hash(right)
        names = {path.name for path in left.rglob("*")}
        assert not names.intersection({".env", "node_modules", "__pycache__", ".pytest_cache"})


def test_each_scenario_builds_its_domain_specific_seed(tmp_path: Path) -> None:
    expected_paths = {
        "python-cli-feature": {"fixture_app/cli.py", "README.md"},
        "python-async-race": {"fixture_app/cache.py", "tests/test_cache.py"},
        "python-parser-refactor": {"fixture_app/parser.py", "tests/test_parser.py"},
        "config-migration": {"fixture_app/config.py", "tests/test_config.py"},
        "http-client-retry": {"fixture_app/client.py", "tests/test_client.py"},
        "path-traversal-repair": {"fixture_app/archive.py", "tests/test_archive.py"},
        "streaming-memory-bound": {"fixture_app/collector.py", "tests/test_collector.py"},
        "jsonl-session-recovery": {"fixture_app/session.py", "tests/test_session.py"},
        "node-cli-dry-run": {"cli.js", "index.test.js"},
        "node-package-install": {"formatter.js", "index.test.js"},
        "node-abort-controller": {"client.js", "index.test.js"},
        "javascript-module-refactor": {"workflow.js", "index.test.js"},
        "frontend-accessibility": {"index.html", "styles.css", "index.test.js"},
        "frontend-responsive-overflow": {"index.html", "styles.css", "index.test.js"},
        "sqlite-migration": {"fixture_app/database.py", "tests/test_database.py"},
        "python-node-contract": {"fixture_app/events.py", "consumer.js", "index.test.js"},
        "failing-suite-diagnosis": {"fixture_app/diagnosis.py", "ROOT_CAUSE.md"},
        "multi-file-domain-rename": {"fixture_app/domain.py", "MIGRATION.md"},
        "docs-code-alignment": {"fixture_app/cli.py", "README.md", "tests/test_docs.py"},
        "long-context-compaction": {"fixture_app/service.py", "REQUIREMENTS.md", "tests/test_requirements.py"},
        "release-packaging": {"Dockerfile", "launcher.js", "tests/test_release.py"},
    }

    for scenario in load_scenarios():
        root = build_fixture(scenario.setup, tmp_path / scenario.id)
        actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
        assert expected_paths[scenario.setup] <= actual, scenario.setup


def test_reports_keep_verifier_failures_primary_and_redact_secret_shapes(tmp_path: Path) -> None:
    results = [
        ScenarioResult(
            "ok",
            "passed",
            "p",
            "m",
            (0,),
            1,
            0,
            10,
            prompt="Implement it",
            response="Implemented and verified",
            context_tokens=64_000,
            context_window=256_000,
            context_percent=25.0,
            context_confidence="provider_real",
        ),
        ScenarioResult(
            "bad",
            "failed",
            "p",
            "m",
            (1,),
            1,
            0,
            20,
            "Bearer token-value sk-secret123456",
            prompt="Repair it",
            response="Bearer response-token sk-response123456",
            fault_domain="model_task_failure",
        ),
    ]

    assert aggregate_score(results) == {"total": 2, "passed": 1, "failed": 1, "all_passed": False}
    write_reports(results, tmp_path, {"temperature": 0.2})
    text = (tmp_path / "aggregate.json").read_text(encoding="utf-8")
    assert "token-value" not in text
    assert "sk-secret123456" not in text
    assert "[REDACTED]" in text
    assert sanitized_tail("x" * 3000, limit=10) == "x" * 10
    markdown = (tmp_path / "aggregate.md").read_text(encoding="utf-8")
    assert "Prompt" in markdown
    assert "Assistant output" in markdown
    assert "25.00% (64,000 / 256,000 tokens; provider_real)" in markdown
    assert "sk-response123456" not in markdown
    assert (tmp_path / "aggregate.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "aggregate.md").stat().st_mode & 0o777 == 0o600


def test_driver_fails_immediately_on_fatal_trace_event(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"event":"fatal","error_code":"provider_error"}\n', encoding="utf-8")
    read_fd, write_fd = os.pipe()
    driver = TuiDriver(_RunningProcess(), read_fd, trace)
    try:
        with pytest.raises(RuntimeError, match="provider_error"):
            driver.wait_for_event("turn_end", 1)
    finally:
        os.close(write_fd)
        os.close(read_fd)


def test_driver_discards_unrelated_past_events_after_reaching_checkpoint(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        '{"event":"tool_end","tool":"read","status":"ok"}\n'
        '{"event":"turn_end","turn_id":"turn-1","status":"ok"}\n',
        encoding="utf-8",
    )
    read_fd, write_fd = os.pipe()
    driver = TuiDriver(_RunningProcess(), read_fd, trace)
    try:
        assert driver.wait_for_event("turn_end", 1)["turn_id"] == "turn-1"
        assert driver._events == []
    finally:
        os.close(write_fd)
        os.close(read_fd)


def test_driver_accepts_exact_model_query_without_waiting_for_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = object.__new__(TuiDriver)
    sent: list[str] = []
    selected = {
        "event": "model_selected",
        "provider": "openrouter",
        "model": "stepfun/step-3.7-flash",
    }
    monkeypatch.setattr(driver, "send_line", sent.append)
    monkeypatch.setattr(driver, "wait_for_events", lambda event_types, timeout: selected)

    assert driver.select_model("stepfun/step-3.7-flash", 1, 60) == selected
    assert sent == ["/model stepfun/step-3.7-flash"]


def test_driver_interrupt_sends_sigint_like_a_controlling_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_fd, write_fd = os.pipe()
    driver = TuiDriver(_RunningProcess(), write_fd, tmp_path / "trace.jsonl")
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda pid, value: signals.append((pid, value)))
    try:
        driver.send_interrupt()
    finally:
        os.close(write_fd)
        os.close(read_fd)

    assert signals == [(_RunningProcess.pid, signal.SIGINT)]


def test_driver_writes_ansi_free_secret_redacted_terminal_transcript(tmp_path: Path) -> None:
    read_fd, write_fd = os.pipe()
    transcript = tmp_path / "terminal.log"
    driver = TuiDriver(_RunningProcess(), read_fd, tmp_path / "trace.jsonl", transcript)
    try:
        os.write(write_fd, b"\x1b[31mAssistant: completed sk-secret123456\x1b[0m\n")
        driver._drain_output()
    finally:
        os.close(write_fd)
        os.close(read_fd)

    text = transcript.read_text(encoding="utf-8")
    assert text == "Assistant: completed [REDACTED]\n"
    assert transcript.stat().st_mode & 0o777 == 0o600


def test_cli_refuses_nonempty_output_without_resume(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "existing").write_text("keep", encoding="utf-8")

    with pytest.raises(SystemExit, match="2"):
        main(["--dotenv", str(tmp_path / ".env"), "--output-dir", str(output)])


def test_cli_resume_skips_completed_scenarios_and_keeps_them_in_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenarios = [
        Scenario("01-done", "done", ("done",), (), (("true",),)),
        Scenario("02-pending", "pending", ("pending",), (), (("true",),)),
    ]
    output = tmp_path / "output"
    completed_path = output / "runs" / "01-done" / "result.json"
    completed_path.parent.mkdir(parents=True)
    completed_path.write_text(
        json.dumps(
            {
                "scenario_id": "01-done",
                "status": "passed",
                "model_provider": "openrouter",
                "model_id": "xiaomi/mimo",
                "verifier_exit_codes": [0],
                "turns": 1,
                "compactions": 0,
                "duration_ms": 10,
                "failure_tail": None,
            }
        ),
        encoding="utf-8",
    )
    calls: list[str] = []
    reports: list[list[str]] = []

    def fake_run(scenario: Scenario, **_kwargs) -> ScenarioResult:
        calls.append(scenario.id)
        return ScenarioResult(scenario.id, "passed", "openrouter", "xiaomi/mimo", (0,), 1, 0, 20)

    monkeypatch.setattr(run_sdlc_eval, "load_scenarios", lambda: scenarios)
    monkeypatch.setattr(run_sdlc_eval, "run_scenario", fake_run)
    monkeypatch.setattr(
        run_sdlc_eval,
        "write_reports",
        lambda results, _output, _metadata: reports.append([result.scenario_id for result in results]),
    )

    exit_code = main(["--dotenv", str(tmp_path / ".env"), "--output-dir", str(output), "--resume"])

    assert exit_code == 0
    assert calls == ["02-pending"]
    assert reports == [["01-done", "02-pending"]]


def test_cli_resume_archives_an_interrupted_scenario_before_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = Scenario("01-interrupted", "interrupted", ("retry",), (), (("true",),))
    output = tmp_path / "output"
    scenario_root = output / "runs" / scenario.id
    fixture = scenario_root / "fixture"
    fixture.mkdir(parents=True)
    (fixture / "partial.txt").write_text("partial", encoding="utf-8")

    def fake_run(current: Scenario, **_kwargs) -> ScenarioResult:
        assert current is scenario
        assert not scenario_root.exists()
        return ScenarioResult(current.id, "passed", "openrouter", "xiaomi/mimo", (0,), 1, 0, 20)

    monkeypatch.setattr(run_sdlc_eval, "load_scenarios", lambda: [scenario])
    monkeypatch.setattr(run_sdlc_eval, "run_scenario", fake_run)
    monkeypatch.setattr(run_sdlc_eval, "write_reports", lambda *_args: None)

    exit_code = main(["--dotenv", str(tmp_path / ".env"), "--output-dir", str(output), "--resume"])

    assert exit_code == 0
    archived = output / "runs" / f"{scenario.id}.interrupted-1" / "fixture" / "partial.txt"
    assert archived.read_text(encoding="utf-8") == "partial"


def test_cli_converts_sigterm_to_interrupt_and_restores_previous_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[signal.Signals, object]] = []
    previous = object()

    def fake_signal(signum, handler):
        calls.append((signum, handler))
        return previous

    monkeypatch.setattr(run_sdlc_eval, "load_scenarios", lambda: [])
    monkeypatch.setattr(run_sdlc_eval, "write_reports", lambda *_args: None)
    monkeypatch.setattr(run_sdlc_eval.signal, "signal", fake_signal)

    assert main(["--dotenv", str(tmp_path / ".env"), "--output-dir", str(tmp_path / "output")]) == 1

    assert calls[0][0] == signal.SIGTERM
    with pytest.raises(KeyboardInterrupt):
        calls[0][1](signal.SIGTERM, None)
    assert calls[-1] == (signal.SIGTERM, previous)


def test_continuous_eval_uses_one_tui_session_for_all_prompts(tmp_path: Path) -> None:
    from evals.run_continuous_sdlc_eval import run_continuous_scenarios

    scenarios = [
        Scenario("01-first", "first", ("implement", "verify"), (), ((sys.executable, "-c", ""),)),
        Scenario(
            "02-second", "second", ("repair",), (), ((sys.executable, "-c", ""),),
            allow_package_install=True,
        ),
    ]
    driver = FakeDriver()
    starts: list[tuple[object, object, object]] = []

    def start(command, cwd, trace):
        starts.append((command, cwd, trace))
        command_parts = list(command)
        driver.conversation_path = Path(command_parts[command_parts.index("--conversation-log") + 1])
        return driver

    results = run_continuous_scenarios(
        scenarios,
        root=tmp_path / "eval",
        dotenv=tmp_path / ".env",
        compact_after={1},
        driver_factory=start,
    )

    assert len(starts) == 1
    assert driver.lines == [
        "/model stepfun/step-3.7-flash",
        "<select:1>",
        "/agents",
        "SDLC scenario 01-first. Work only in scenarios/01-first. implement verify",
        "/compact",
        "/allow package-install",
        "SDLC scenario 02-second. Work only in scenarios/02-second. repair",
        "/exit",
    ]
    assert [result.status for result in results] == ["passed", "passed"]
    assert {result.session_id for result in results} == {"session-live"}
    assert {result.session_path for result in results} == {"/tmp/session-live.jsonl"}
    assert driver.closed is True
    assert all(timeout >= 900 for event, timeout in driver.waits if event == "turn_end")
    assert sum(event == "turn_ready" for event, _timeout in driver.waits) == 2
    assert ("capability_granted", 60) in driver.waits
    command = list(starts[0][0])
    assert "--conversation-log" in command
    assert [result.turn_id for result in results] == ["turn-1", "turn-2"]
    assert [result.context_percent for result in results] == pytest.approx([0.390625, 0.78125])
    assert results[0].prompt == driver.lines[3]
    assert results[0].response == f"completed {driver.lines[3]}"
    assert {result.model_provider for result in results} == {"stepfun"}
    assert {result.model_id for result in results} == {"stepfun/step-3.7-flash"}
    result_paths = sorted((tmp_path / "eval/runs").glob("*/result.json"))
    assert result_paths and all(path.stat().st_mode & 0o777 == 0o600 for path in result_paths)


def test_continuous_eval_can_pin_a_direct_provider_at_process_start(tmp_path: Path) -> None:
    from evals.run_continuous_sdlc_eval import run_continuous_scenarios

    scenario = Scenario(
        "01-direct-provider",
        "first",
        ("implement",),
        (),
        ((sys.executable, "-c", ""),),
    )
    driver = FakeDriver()
    starts: list[list[str]] = []

    def select_direct(query: str, index: int, timeout: float):
        driver.lines.extend([f"/model {query}", f"<select:{index}>"])
        return {"provider": "stepfun", "model": "step-3.7-flash"}

    driver.select_model = select_direct  # type: ignore[method-assign]

    def start(command, _cwd, _trace):
        command_parts = list(command)
        starts.append(command_parts)
        driver.conversation_path = Path(command_parts[command_parts.index("--conversation-log") + 1])
        return driver

    results = run_continuous_scenarios(
        [scenario],
        root=tmp_path / "eval",
        dotenv=tmp_path / ".env",
        model_provider="stepfun",
        model_query="step-3.7-flash",
        driver_factory=start,
    )

    assert starts[0][starts[0].index("--provider") + 1] == "stepfun"
    assert starts[0][starts[0].index("--model") + 1] == "step-3.7-flash"
    assert driver.lines[:2] == ["/model stepfun/step-3.7-flash", "<select:1>"]
    assert results[0].model_provider == "stepfun"
    assert results[0].model_id == "step-3.7-flash"


def test_continuous_eval_stops_and_classifies_provider_billing_failure(tmp_path: Path) -> None:
    from evals.run_continuous_sdlc_eval import run_continuous_scenarios

    verifier_marker = tmp_path / "verifier-ran"
    scenarios = [
        Scenario(
            "01-first",
            "first",
            ("implement",),
            (),
            (
                (
                    sys.executable,
                    "-c",
                    f"from pathlib import Path; Path({str(verifier_marker)!r}).write_text('ran')",
                ),
            ),
        ),
        Scenario("02-second", "second", ("repair",), (), ((sys.executable, "-c", ""),)),
    ]

    class ProviderErrorDriver(FakeDriver):
        def wait_for_event(self, event_type: str, timeout: float):
            if event_type != "turn_end":
                return super().wait_for_event(event_type, timeout)
            self.waits.append((event_type, timeout))
            self.turn_count += 1
            turn_id = f"turn-{self.turn_count}"
            assert self.conversation_path is not None
            assert self.pending_prompt is not None
            with self.conversation_path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {
                            "turn_id": turn_id,
                            "prompt": self.pending_prompt,
                            "response": "Error: OpenRouter billing or quota failed (HTTP 402): Payment Required",
                            "status": "error",
                        }
                    )
                    + "\n"
                )
            return {"event": event_type, "status": "error", "turn_id": turn_id}

    driver = ProviderErrorDriver()

    def start(command, _cwd, _trace):
        command_parts = list(command)
        driver.conversation_path = Path(command_parts[command_parts.index("--conversation-log") + 1])
        return driver

    results = run_continuous_scenarios(
        scenarios,
        root=tmp_path / "eval",
        dotenv=tmp_path / ".env",
        driver_factory=start,
    )

    assert len(results) == 1
    assert results[0].status == "failed"
    assert results[0].fault_domain == "provider_billing_failure"
    assert results[0].verifier_exit_codes == ()
    assert verifier_marker.exists() is False
    assert not any("02-second" in line for line in driver.lines)
    assert driver.lines[-1] == "/exit"
    assert driver.closed is True


def test_continuous_eval_preflights_verifiers_before_starting_tui(tmp_path: Path) -> None:
    from evals.run_continuous_sdlc_eval import run_continuous_scenarios

    scenario = Scenario(
        "01-missing-verifier",
        "first",
        ("implement",),
        (),
        (("definitely-missing-travis234-verifier", "--version"),),
    )
    started = False

    def start(*_args):
        nonlocal started
        started = True
        return FakeDriver()

    with pytest.raises(RuntimeError, match="verifier preflight failed"):
        run_continuous_scenarios(
            [scenario],
            root=tmp_path / "eval",
            dotenv=tmp_path / ".env",
            driver_factory=start,
        )

    assert started is False


def test_continuous_eval_rejects_wrong_selected_model_before_first_prompt(tmp_path: Path) -> None:
    from evals.run_continuous_sdlc_eval import run_continuous_scenarios

    scenario = Scenario(
        "01-wrong-model",
        "first",
        ("implement",),
        (),
        ((sys.executable, "-c", ""),),
    )
    driver = FakeDriver()
    driver.select_model = lambda query, index, timeout: {  # type: ignore[method-assign]
        "provider": "openrouter",
        "model": "another/model",
    }

    with pytest.raises(RuntimeError, match="model selection mismatch"):
        run_continuous_scenarios(
            [scenario],
            root=tmp_path / "eval",
            dotenv=tmp_path / ".env",
            driver_factory=lambda *_args: driver,
        )

    assert not any(line.startswith("SDLC scenario") for line in driver.lines)
    assert driver.closed is True


def test_feature_audit_requires_every_live_capability(tmp_path: Path) -> None:
    root = tmp_path / "live-21"
    for index in range(1, 22):
        scenario_id = {
            1: "01-python-cli-feature",
            2: "02-python-async-race",
            3: "03-python-parser-refactor",
            17: "17-failing-suite-diagnosis",
            21: "21-release-packaging",
        }.get(index, f"{index:02d}-scenario")
        result = {
            "scenario_id": scenario_id,
            "status": "passed",
            "session_id": "session-live",
            "session_path": "/tmp/session-live.jsonl",
        }
        path = root / "runs" / scenario_id / "result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result), encoding="utf-8")
    marker = root / "workspace/scenarios/03-python-parser-refactor/SKILL_APPLIED.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("acceptance-audit skill applied\n", encoding="utf-8")
    events = [
        {"event": "model_selected", "provider": "stepfun", "model": "stepfun/step-3.7-flash"},
        {"event": "extension_command", "status": "ok"},
        {"event": "capability_granted", "status": "ok"},
        {"event": "user_command_interrupt", "status": "ok", "interrupt_count": 2},
        {"event": "process_event", "process_id": "p1", "process_state": "terminated", "status": "terminated"},
        {"event": "compaction_end", "status": "ok", "trigger": "threshold", "compression_count": 1},
        *(
            {
                "event": "turn_ready",
                "status": "ok",
                "context_tokens": index * 1_000,
                "context_window": 256_000,
                "context_percent": index / 2.56,
                "context_estimated": False,
                "context_confidence": "provider_real",
                "compression_count": 1 if index >= 20 else 0,
            }
            for index in range(1, 22)
        ),
        {"event": "shutdown", "status": "ok"},
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
    (root / "trace.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    audit = FeatureAudit.from_artifacts(root)

    assert audit.passed is True
    assert audit.missing_features == ()
    assert set(audit.observed_features) == REQUIRED_FEATURES


def test_feature_audit_uses_latest_process_state_instead_of_flagging_normal_lifecycle(
    tmp_path: Path,
) -> None:
    root = tmp_path / "audit"
    (root / "trace.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (root / "trace.jsonl").write_text(
        "".join(
            json.dumps(event) + "\n"
            for event in (
                {"event": "process_event", "process_id": "done", "process_state": "running"},
                {"event": "process_event", "process_id": "live", "process_state": "running"},
                {"event": "process_event", "process_id": "done", "process_state": "terminated"},
            )
        ),
        encoding="utf-8",
    )

    audit = FeatureAudit.from_artifacts(root)

    assert audit.nonterminal_processes == ("live",)


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()
