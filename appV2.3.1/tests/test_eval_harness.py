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

    def wait_for_event(self, event_type: str, timeout: float):
        self.waits.append((event_type, timeout))
        return {"event": event_type, "status": "ok"}

    def select_model(self, query: str, index: int, timeout: float):
        self.lines.extend([f"/model {query}", f"<select:{index}>"])
        return {"provider": "openrouter", "model": "xiaomi/mimo"}

    def send_line(self, text: str) -> None:
        self.lines.append(text)

    def close(self) -> None:
        self.closed = True


class _RunningProcess:
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
        ScenarioResult("ok", "passed", "p", "m", (0,), 1, 0, 10),
        ScenarioResult("bad", "failed", "p", "m", (1,), 1, 0, 20, "Bearer token-value sk-secret123456"),
    ]

    assert aggregate_score(results) == {"total": 2, "passed": 1, "failed": 1, "all_passed": False}
    write_reports(results, tmp_path, {"temperature": 0.2})
    text = (tmp_path / "aggregate.json").read_text(encoding="utf-8")
    assert "token-value" not in text
    assert "sk-secret123456" not in text
    assert "[REDACTED]" in text
    assert sanitized_tail("x" * 3000, limit=10) == "x" * 10


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
        "/model mimo",
        "<select:1>",
        "SDLC scenario 01-first. Work only in scenarios/01-first. implement verify",
        "/compact",
        "/allow package-install",
        "SDLC scenario 02-second. Work only in scenarios/02-second. repair",
        "/exit",
    ]
    assert [result.status for result in results] == ["passed", "passed"]
    assert driver.closed is True
    assert all(timeout >= 900 for event, timeout in driver.waits if event == "turn_end")
    assert ("capability_granted", 60) in driver.waits
    command = list(starts[0][0])
    assert "--conversation-log" in command


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()
