from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterable

from evals.fixtures import build_fixture
from evals.report import write_reports
from evals.schema import Scenario, ScenarioResult, load_scenarios
from evals.tui_driver import TuiDriver

DEFAULT_COMPACT_AFTER = frozenset()
MIN_TURN_TIMEOUT_SECONDS = 900


def _prompt_for(scenario: Scenario) -> str:
    requirements = " ".join(turn.strip() for turn in scenario.turns if turn.strip())
    return f"SDLC scenario {scenario.id}. Work only in scenarios/{scenario.id}. {requirements}"


def _interrupt_on_termination(_signum, _frame) -> None:
    raise KeyboardInterrupt


def run_continuous_scenarios(
    scenarios: Iterable[Scenario],
    *,
    root: str | Path,
    dotenv: str | Path,
    model_query: str = "mimo",
    model_index: int = 1,
    thinking: str = "medium",
    temperature: float = 0.2,
    compact_after: set[int] | frozenset[int] = DEFAULT_COMPACT_AFTER,
    driver_factory: Callable[..., object] = TuiDriver.start,
) -> list[ScenarioResult]:
    scenario_list = list(scenarios)
    output = Path(root).expanduser().resolve()
    workspace = output / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    scenario_workspaces: dict[str, Path] = {}
    for scenario in scenario_list:
        scenario_workspaces[scenario.id] = build_fixture(
            scenario.setup,
            workspace / "scenarios" / scenario.id,
        )

    trace_path = output / "trace.jsonl"
    conversation_path = output / "conversation.jsonl"
    _seed_acceptance_agent_resources()
    console_script = Path(sys.executable).with_name("travis234")
    command = [
        str(console_script) if console_script.is_file() else sys.executable,
        *([] if console_script.is_file() else ["-m", "travis.cli"]),
        "--cwd",
        str(workspace),
        "--dotenv",
        str(Path(dotenv).expanduser().resolve()),
        "--thinking",
        thinking,
        "--temperature",
        str(temperature),
        "--event-trace",
        str(trace_path),
        "--conversation-log",
        str(conversation_path),
    ]
    driver = driver_factory(command, workspace, trace_path)
    results: list[ScenarioResult] = []
    try:
        ready = driver.wait_for_event("tui_ready", 60)
        session_id = str(ready.get("session_id") or "") or None
        session_path = str(ready.get("session_path") or "") or None
        selected = driver.select_model(model_query, model_index, 60)
        provider = str(selected.get("provider") or "") or None
        model = str(selected.get("model") or "") or None
        driver.send_line("/agents")
        driver.wait_for_event("extension_command", 60)
        for index, scenario in enumerate(scenario_list, start=1):
            started = time.monotonic()
            turn_timeout = max(MIN_TURN_TIMEOUT_SECONDS, scenario.timeout_seconds)
            verifier_codes: list[int] = []
            failure_tail: str | None = None
            turn_finished = False
            if scenario.allow_package_install:
                driver.send_line("/allow package-install")
                granted = driver.wait_for_event("capability_granted", 60)
                if granted.get("status") != "ok":
                    raise RuntimeError("package-install capability grant failed")
            driver.send_line(_prompt_for(scenario))
            try:
                driver.wait_for_event("turn_end", turn_timeout)
                turn_finished = True
            except TimeoutError:
                failure_tail = "TimeoutError: timed out waiting for turn_end"
                driver.send_interrupt()
                try:
                    driver.wait_for_event("turn_end", 30)
                except Exception:
                    failure_tail = "TimeoutError: turn did not abort cleanly"
            except Exception as error:  # noqa: BLE001 - converted to bounded matrix metadata.
                failure_tail = f"{type(error).__name__}: {str(error).split('; tail=', 1)[0]}"[:500]

            if turn_finished:
                verifier_codes, verifier_failure = _run_verifiers(
                    scenario,
                    scenario_workspaces[scenario.id],
                )
                failure_tail = failure_tail or verifier_failure

            compacted = 0
            if index in compact_after:
                driver.send_line("/compact")
                try:
                    driver.wait_for_event("compaction_end", turn_timeout)
                    compacted = 1
                except Exception as error:  # noqa: BLE001
                    failure_tail = failure_tail or f"compaction failed: {type(error).__name__}"

            status = (
                "passed"
                if turn_finished and verifier_codes and all(code == 0 for code in verifier_codes) and failure_tail is None
                else "failed"
            )
            result = ScenarioResult(
                scenario_id=scenario.id,
                status=status,
                model_provider=provider,
                model_id=model,
                verifier_exit_codes=tuple(verifier_codes),
                turns=1,
                compactions=compacted,
                duration_ms=int((time.monotonic() - started) * 1000),
                failure_tail=failure_tail,
                session_id=session_id,
                session_path=session_path,
            )
            result_path = output / "runs" / scenario.id / "result.json"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
            results.append(result)

            if index == 11:
                _exercise_ctrl_c_escalation(driver)

        driver.send_line("/exit")
        driver.wait_for_event("shutdown", 60)
    finally:
        driver.close()
    return results


def _seed_acceptance_agent_resources() -> None:
    configured = os.environ.get("TRAVIS234_CODING_AGENT_DIR")
    if not configured:
        return
    agent_dir = Path(configured).expanduser().resolve()
    skills_dir = agent_dir / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    source_skill = Path(__file__).resolve().parents[1] / "skills" / "subagent-delegation"
    if source_skill.is_dir():
        shutil.copytree(source_skill, skills_dir / source_skill.name, dirs_exist_ok=True)
    audit_skill = skills_dir / "acceptance-audit" / "SKILL.md"
    audit_skill.parent.mkdir(parents=True, exist_ok=True)
    audit_skill.write_text(
        "---\n"
        "name: acceptance-audit\n"
        "description: Use when a prompt explicitly requests the acceptance-audit skill.\n"
        "---\n\n"
        "# Acceptance audit\n\n"
        "When explicitly invoked, create `SKILL_APPLIED.txt` in the named scenario directory "
        "with exactly `acceptance-audit skill applied\\n`, then continue the requested task.\n",
        encoding="utf-8",
    )


def _exercise_ctrl_c_escalation(driver) -> None:
    driver.send_line("!python3 -c 'import signal,time; signal.signal(signal.SIGINT, signal.SIG_IGN); time.sleep(300)'")
    driver.wait_for_event("user_command_started", 30)
    time.sleep(0.25)
    driver.send_interrupt()
    first = driver.wait_for_event("user_command_interrupt", 30)
    if int(first.get("interrupt_count") or 0) < 1:
        raise RuntimeError("first Ctrl-C was not recorded")
    driver.send_interrupt()
    second = driver.wait_for_event("user_command_interrupt", 30)
    if int(second.get("interrupt_count") or 0) < 2:
        raise RuntimeError("second Ctrl-C did not escalate")
    driver.wait_for_event("process_event", 30)


def _run_verifiers(scenario: Scenario, cwd: Path) -> tuple[list[int], str | None]:
    codes: list[int] = []
    failure: str | None = None
    for verifier in scenario.verifiers:
        command = list(verifier)
        if command and command[0] == "python":
            command[0] = sys.executable
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=scenario.timeout_seconds,
            check=False,
        )
        codes.append(completed.returncode)
        if completed.returncode != 0 and failure is None:
            failure = f"verifier {Path(command[0]).name} exited {completed.returncode}"
    return codes, failure


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run 21 SDLC prompts in one travis TUI session")
    parser.add_argument("--dotenv", required=True)
    parser.add_argument("--model-query", default="mimo")
    parser.add_argument("--model-index", type=int, default=1)
    parser.add_argument("--thinking", default="medium")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    output = Path(args.output_dir).expanduser().resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("output directory is not empty")
    output.mkdir(parents=True, exist_ok=True)
    previous_sigterm = signal.signal(signal.SIGTERM, _interrupt_on_termination)
    try:
        scenarios = load_scenarios()
        results = run_continuous_scenarios(
            scenarios,
            root=output,
            dotenv=args.dotenv,
            model_query=args.model_query,
            model_index=args.model_index,
            thinking=args.thinking,
            temperature=args.temperature,
        )
        write_reports(
            results,
            output,
            {
                "mode": "continuous-session",
                "prompt_count": len(scenarios),
                "model_query": args.model_query,
                "model_index": args.model_index,
                "thinking": args.thinking,
                "temperature": args.temperature,
            },
        )
        return 0 if results and all(result.status == "passed" for result in results) else 1
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    raise SystemExit(main())
