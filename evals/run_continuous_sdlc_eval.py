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
from evals.report import sanitized_text, write_reports
from evals.schema import Scenario, ScenarioResult, load_scenarios
from evals.tui_driver import TuiDriver
from evals.verify_run import verify_run, write_verification
from travis.ai.providers.catalog import normalize_provider

DEFAULT_COMPACT_AFTER = frozenset()
MIN_TURN_TIMEOUT_SECONDS = 900
DEFAULT_MODEL = "stepfun/step-3.7-flash"


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
    model_query: str = DEFAULT_MODEL,
    model_provider: str | None = None,
    model_index: int = 1,
    thinking: str = "medium",
    temperature: float = 0.2,
    compact_after: set[int] | frozenset[int] = DEFAULT_COMPACT_AFTER,
    console_script: str | Path | None = None,
    verifier_python: str | Path | None = None,
    driver_factory: Callable[..., object] = TuiDriver.start,
) -> list[ScenarioResult]:
    scenario_list = list(scenarios)
    verifier_executable = (
        os.path.abspath(os.path.expanduser(os.fspath(verifier_python)))
        if verifier_python
        else sys.executable
    )
    _preflight_verifiers(scenario_list, verifier_executable)
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
    selected_console = (
        Path(console_script).expanduser().resolve()
        if console_script is not None
        else Path(sys.executable).with_name("travis234")
    )
    if console_script is not None and not selected_console.is_file():
        raise RuntimeError(f"console script does not exist: {selected_console}")
    command = [
        str(selected_console) if selected_console.is_file() else sys.executable,
        *([] if selected_console.is_file() else ["-m", "travis.cli"]),
        "--cwd",
        str(workspace),
        "--dotenv",
        str(Path(dotenv).expanduser().resolve()),
        "--thinking",
        thinking,
        "--temperature",
        str(temperature),
        *(["--provider", model_provider, "--model", model_query] if model_provider else []),
        "--event-trace",
        str(trace_path),
        "--conversation-log",
        str(conversation_path),
    ]
    driver = driver_factory(command, workspace, trace_path)
    results: list[ScenarioResult] = []
    previous_compression_count = 0
    try:
        ready = driver.wait_for_event("tui_ready", 60)
        session_id = str(ready.get("session_id") or "") or None
        session_path = str(ready.get("session_path") or "") or None
        selection_query = f"{model_provider}/{model_query}" if model_provider else model_query
        selected = driver.select_model(selection_query, model_index, 60)
        provider = str(selected.get("provider") or "") or None
        model = str(selected.get("model") or "") or None
        expected_model = model_query
        if model_provider and model_query.startswith(f"{model_provider}/"):
            expected_model = model_query.split("/", 1)[1]
        if model_provider and normalize_provider(provider) != normalize_provider(model_provider):
            raise RuntimeError(
                f"provider selection mismatch: requested {model_provider!r}, selected {provider!r}"
            )
        if (model_provider or "/" in model_query) and model != expected_model:
            raise RuntimeError(
                f"model selection mismatch: requested {expected_model!r}, selected {model!r}"
            )
        driver.send_line("/agents")
        driver.wait_for_event("extension_command", 60)
        for index, scenario in enumerate(scenario_list, start=1):
            started = time.monotonic()
            turn_timeout = max(MIN_TURN_TIMEOUT_SECONDS, scenario.timeout_seconds)
            verifier_codes: list[int] = []
            failure_tail: str | None = None
            failure_evidence: str | None = None
            fault_domain: str | None = None
            turn_finished = False
            compacted = 0
            turn_id: str | None = None
            response: str | None = None
            context_tokens: int | None = None
            context_window: int | None = None
            context_percent: float | None = None
            context_estimated: bool | None = None
            context_confidence: str | None = None
            provider_blocked = False
            prompt = _prompt_for(scenario)
            if scenario.allow_package_install:
                driver.send_line("/allow package-install")
                granted = driver.wait_for_event("capability_granted", 60)
                if granted.get("status") != "ok":
                    raise RuntimeError("package-install capability grant failed")
            driver.send_line(prompt)
            try:
                turn_end = driver.wait_for_event("turn_end", turn_timeout)
                turn_id = str(turn_end.get("turn_id") or "") or None
                turn_ready = driver.wait_for_event("turn_ready", 60)
                turn_finished = True
                context_tokens = _optional_int(turn_ready.get("context_tokens"))
                context_window = _optional_int(turn_ready.get("context_window"))
                context_percent = _optional_float(turn_ready.get("context_percent"))
                context_estimated = bool(turn_ready.get("context_estimated"))
                context_confidence = str(turn_ready.get("context_confidence") or "unknown")
                current_compressions = _optional_int(turn_ready.get("compression_count"))
                if current_compressions is not None:
                    compacted = max(0, current_compressions - previous_compression_count)
                    previous_compression_count = current_compressions
                else:
                    compacted = 0
                if context_window is None or "context_percent" not in turn_ready:
                    failure_tail = "turn_ready did not include finalized footer context telemetry"
                    failure_evidence = failure_tail
                    fault_domain = "agent_runtime_failure"
                conversation = _conversation_record(conversation_path, turn_id)
                if conversation is None:
                    failure_tail = failure_tail or "conversation record missing for completed turn"
                    failure_evidence = failure_evidence or failure_tail
                    fault_domain = fault_domain or "harness_failure"
                else:
                    response = str(conversation.get("response") or "")
                    recorded_status = str(
                        conversation.get("status") or turn_end.get("status") or "ok"
                    )
                    if recorded_status != "ok":
                        failure_tail = response or f"turn ended with status {recorded_status}"
                        failure_evidence = failure_tail
                        fault_domain = _fault_domain_for_turn_failure(failure_tail)
                        provider_blocked = fault_domain.startswith("provider_")
            except TimeoutError:
                failure_tail = "TimeoutError: timed out waiting for turn_end"
                failure_evidence = failure_tail
                fault_domain = "runtime_timeout_unresolved"
                driver.send_interrupt()
                try:
                    driver.wait_for_event("turn_end", 30)
                    driver.wait_for_event("turn_ready", 30)
                except Exception:
                    failure_tail = "TimeoutError: turn did not abort cleanly"
                    failure_evidence = failure_tail
                    fault_domain = "agent_runtime_failure"
            except Exception as error:  # noqa: BLE001 - converted to bounded matrix metadata.
                failure_tail = f"{type(error).__name__}: {str(error).split('; tail=', 1)[0]}"[:500]
                failure_evidence = failure_tail
                fault_domain = _fault_domain_for_error(error)

            if turn_finished and failure_tail is None:
                verifier_codes, verifier_failure = _run_verifiers(
                    scenario,
                    scenario_workspaces[scenario.id],
                    verifier_executable,
                )
                failure_tail = failure_tail or verifier_failure
                if verifier_failure and fault_domain is None:
                    fault_domain = "model_task_failure"
                    failure_evidence = verifier_failure

            if index in compact_after:
                driver.send_line("/compact")
                try:
                    compaction = driver.wait_for_event("compaction_end", turn_timeout)
                    manual_count = _optional_int(compaction.get("compression_count"))
                    if manual_count is not None:
                        compacted += max(0, manual_count - previous_compression_count)
                        previous_compression_count = manual_count
                    else:
                        compacted += 1
                        previous_compression_count += 1
                except Exception as error:  # noqa: BLE001
                    failure_tail = failure_tail or f"compaction failed: {type(error).__name__}"
                    failure_evidence = failure_evidence or failure_tail
                    fault_domain = fault_domain or "agent_runtime_failure"

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
                turn_id=turn_id,
                prompt=prompt,
                response=response,
                context_tokens=context_tokens,
                context_window=context_window,
                context_percent=context_percent,
                context_estimated=context_estimated,
                context_confidence=context_confidence,
                fault_domain=fault_domain,
                failure_evidence=failure_evidence,
            )
            result_path = output / "runs" / scenario.id / "result.json"
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(
                json.dumps(_safe_result_payload(result), indent=2) + "\n",
                encoding="utf-8",
            )
            os.chmod(result_path, 0o600)
            results.append(result)

            if provider_blocked:
                break
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


def _run_verifiers(
    scenario: Scenario,
    cwd: Path,
    verifier_python: str = sys.executable,
) -> tuple[list[int], str | None]:
    codes: list[int] = []
    failure: str | None = None
    for verifier in scenario.verifiers:
        command = list(verifier)
        if command and command[0] == "python":
            command[0] = verifier_python
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


def _preflight_verifiers(
    scenarios: Iterable[Scenario],
    verifier_python: str = sys.executable,
) -> None:
    missing: set[str] = set()
    python_modules: set[tuple[str, str]] = set()
    for scenario in scenarios:
        for verifier in scenario.verifiers:
            if not verifier:
                missing.add(f"{scenario.id}:<empty>")
                continue
            executable = verifier_python if verifier[0] == "python" else verifier[0]
            if Path(executable).is_absolute():
                available = Path(executable).is_file()
            else:
                available = shutil.which(executable) is not None
            if not available:
                missing.add(f"{scenario.id}:{verifier[0]}")
                continue
            if verifier[0] == "python" and len(verifier) >= 3 and verifier[1] == "-m":
                python_modules.add((scenario.id, verifier[2]))
    for scenario_id, module in sorted(python_modules):
        probe = subprocess.run(
            [
                verifier_python,
                "-c",
                "import importlib.util,sys; raise SystemExit(0 if importlib.util.find_spec(sys.argv[1]) else 1)",
                module,
            ],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
        if probe.returncode != 0:
            missing.add(f"{scenario_id}:python -m {module}")
    if missing:
        raise RuntimeError(f"verifier preflight failed: {', '.join(sorted(missing))}")


def _conversation_record(path: Path, turn_id: str | None) -> dict[str, object] | None:
    if not turn_id or not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("turn_id") == turn_id:
            return value
    return None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _fault_domain_for_error(error: BaseException) -> str:
    lowered = f"{type(error).__name__}: {error}".lower()
    if any(
        marker in lowered
        for marker in (
            "connectionerror",
            "connection reset",
            "connection refused",
            "network",
            "dns",
            "temporary failure",
            "service unavailable",
            "gateway timeout",
        )
    ):
        return "provider_network_failure"
    return "agent_runtime_failure"


def _fault_domain_for_turn_failure(message: str) -> str:
    lowered = message.lower()
    if any(marker in lowered for marker in ("http 402", "payment required", "billing or quota", "account limit")):
        return "provider_billing_failure"
    if any(marker in lowered for marker in ("http 401", "authentication failed")):
        return "provider_authentication_failure"
    if "prompt-injection guardrail" in lowered or "data-policy settings" in lowered:
        return "provider_policy_failure"
    if any(marker in lowered for marker in ("http 403", "authorization failed")):
        return "provider_authorization_failure"
    if any(marker in lowered for marker in ("http 429", "rate limit", "too many requests")):
        return "provider_rate_limit"
    if any(
        marker in lowered
        for marker in (
            "connection reset",
            "connection refused",
            "dns",
            "service unavailable",
            "gateway timeout",
            "network",
        )
    ):
        return "provider_network_failure"
    if any(marker in lowered for marker in ("provider message:", " api error (http ", "openrouter")):
        return "provider_api_failure"
    return "agent_runtime_failure"


def _safe_result_payload(result: ScenarioResult) -> dict[str, object]:
    payload = asdict(result)
    for field in ("failure_tail", "prompt", "response", "failure_evidence"):
        payload[field] = sanitized_text(payload.get(field))  # type: ignore[arg-type]
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run 21 SDLC prompts in one travis TUI session")
    parser.add_argument("--dotenv", required=True)
    parser.add_argument("--model-query", default=DEFAULT_MODEL)
    parser.add_argument("--model-provider")
    parser.add_argument("--model-index", type=int, default=1)
    parser.add_argument("--thinking", default="medium")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--console-script")
    parser.add_argument("--verifier-python")
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
            model_provider=args.model_provider,
            model_index=args.model_index,
            thinking=args.thinking,
            temperature=args.temperature,
            console_script=args.console_script,
            verifier_python=args.verifier_python,
        )
        write_reports(
            results,
            output,
            {
                "mode": "continuous-session",
                "prompt_count": len(scenarios),
                "model_query": args.model_query,
                "model_provider": args.model_provider,
                "expected_model": args.model_query,
                "model_index": args.model_index,
                "thinking": args.thinking,
                "temperature": args.temperature,
                "console_script": args.console_script,
                "verifier_python": args.verifier_python,
            },
        )
        verification = verify_run(output, expected_model=args.model_query)
        write_verification(verification, output)
        return 0 if verification.passed else 1
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    raise SystemExit(main())
