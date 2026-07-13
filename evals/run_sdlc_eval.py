from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from evals.fixtures import build_fixture
from evals.report import write_reports
from evals.schema import Scenario, ScenarioResult, load_scenarios
from evals.tui_driver import TuiDriver


def _load_result(path: Path) -> ScenarioResult:
    data = json.loads(path.read_text(encoding="utf-8"))
    return ScenarioResult(
        scenario_id=str(data["scenario_id"]),
        status=str(data["status"]),
        model_provider=data.get("model_provider"),
        model_id=data.get("model_id"),
        verifier_exit_codes=tuple(int(code) for code in data.get("verifier_exit_codes", [])),
        turns=int(data["turns"]),
        compactions=int(data["compactions"]),
        duration_ms=int(data["duration_ms"]),
        failure_tail=data.get("failure_tail"),
    )


def _archive_incomplete_run(path: Path) -> Path:
    suffix = 1
    while True:
        archive = path.with_name(f"{path.name}.interrupted-{suffix}")
        if not archive.exists():
            path.rename(archive)
            return archive
        suffix += 1


def _interrupt_on_termination(_signum, _frame) -> None:
    raise KeyboardInterrupt


def run_scenario(
    scenario: Scenario,
    *,
    root: str | Path,
    dotenv: str | Path,
    model_query: str = "mimo",
    model_index: int = 1,
    thinking: str = "medium",
    temperature: float = 0.2,
    driver_factory: Callable[..., object] = TuiDriver.start,
) -> ScenarioResult:
    started = time.monotonic()
    scenario_root = Path(root) / scenario.id
    fixture = build_fixture(scenario.setup, scenario_root / "fixture")
    trace_path = scenario_root / "trace.jsonl"
    command = [
        sys.executable, "-m", "travis.cli", "--cwd", str(fixture), "--dotenv", str(Path(dotenv).resolve()),
        "--thinking", thinking, "--temperature", str(temperature), "--event-trace", str(trace_path),
    ]
    driver = driver_factory(command, fixture, trace_path)
    provider = None
    model = None
    verifier_codes: list[int] = []
    failure_tail: str | None = None
    status = "failed"
    try:
        driver.wait_for_event("tui_ready", scenario.timeout_seconds)
        selected = driver.select_model(model_query, model_index, scenario.timeout_seconds)
        provider = str(selected.get("provider") or "") or None
        model = str(selected.get("model") or "") or None
        if scenario.allow_package_install:
            driver.send_line("/allow package-install")
        for index, turn in enumerate(scenario.turns, start=1):
            driver.send_line(turn)
            driver.wait_for_event("turn_end", scenario.timeout_seconds)
            if index in scenario.compact_after:
                driver.send_line("/compact")
                driver.wait_for_event("compaction_end", scenario.timeout_seconds)
        driver.send_line("/exit")
        driver.wait_for_event("shutdown", scenario.timeout_seconds)
        for verifier in scenario.verifiers:
            verifier_command = list(verifier)
            if verifier_command and verifier_command[0] == "python":
                verifier_command[0] = sys.executable
            completed = subprocess.run(
                verifier_command, cwd=fixture, text=True, capture_output=True,
                timeout=scenario.timeout_seconds, check=False,
            )
            verifier_codes.append(completed.returncode)
            if completed.returncode != 0 and failure_tail is None:
                failure_tail = f"verifier {Path(verifier_command[0]).name} exited {completed.returncode}"
        status = "passed" if verifier_codes and all(code == 0 for code in verifier_codes) else "failed"
    except Exception as error:  # noqa: BLE001 - converted to sanitized result metadata.
        detail = str(error).split("; tail=", 1)[0]
        failure_tail = f"{type(error).__name__}: {detail}"[:500]
    finally:
        driver.close()
    result = ScenarioResult(
        scenario_id=scenario.id,
        status=status,
        model_provider=provider,
        model_id=model,
        verifier_exit_codes=tuple(verifier_codes),
        turns=len(scenario.turns),
        compactions=len(scenario.compact_after),
        duration_ms=int((time.monotonic() - started) * 1000),
        failure_tail=failure_tail,
    )
    scenario_root.mkdir(parents=True, exist_ok=True)
    (scenario_root / "result.json").write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run 21 live travis SDLC TUI scenarios")
    parser.add_argument("--dotenv", required=True)
    parser.add_argument("--model-query", default="mimo")
    parser.add_argument("--model-index", type=int, default=1)
    parser.add_argument("--thinking", default="medium")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--scenario", action="append", default=[])
    args = parser.parse_args(argv)
    output = Path(args.output_dir).expanduser().resolve()
    if output.exists() and any(output.iterdir()) and not args.resume:
        parser.error("output directory is not empty; pass --resume to reuse it")
    output.mkdir(parents=True, exist_ok=True)
    scenarios = load_scenarios()
    if args.scenario:
        selected = set(args.scenario)
        scenarios = [scenario for scenario in scenarios if scenario.id in selected]
    previous_sigterm = signal.signal(signal.SIGTERM, _interrupt_on_termination)
    try:
        results: list[ScenarioResult] = []
        for scenario in scenarios:
            scenario_root = output / "runs" / scenario.id
            result_path = scenario_root / "result.json"
            if args.resume and result_path.is_file():
                results.append(_load_result(result_path))
                continue
            if args.resume and scenario_root.exists():
                _archive_incomplete_run(scenario_root)
            results.append(
                run_scenario(
                    scenario, root=output / "runs", dotenv=args.dotenv, model_query=args.model_query,
                    model_index=args.model_index, thinking=args.thinking, temperature=args.temperature,
                )
            )
        write_reports(
            results,
            output,
            {
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
