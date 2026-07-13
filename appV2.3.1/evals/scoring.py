from __future__ import annotations

from evals.schema import ScenarioResult


def score_result(result: ScenarioResult) -> dict[str, object]:
    verifier_pass = bool(result.verifier_exit_codes) and all(code == 0 for code in result.verifier_exit_codes)
    return {
        "scenario_id": result.scenario_id,
        "passed": result.status == "passed" and verifier_pass,
        "verifier_pass": verifier_pass,
        "turns": result.turns,
        "compactions": result.compactions,
        "duration_ms": result.duration_ms,
    }


def aggregate_score(results: list[ScenarioResult]) -> dict[str, object]:
    scored = [score_result(result) for result in results]
    passed = sum(1 for item in scored if item["passed"])
    return {"total": len(scored), "passed": passed, "failed": len(scored) - passed, "all_passed": passed == len(scored)}


__all__ = ["aggregate_score", "score_result"]
