from __future__ import annotations

import json
import re
from dataclasses import asdict
from pathlib import Path

from evals.schema import ScenarioResult
from evals.scoring import aggregate_score

_SECRET = re.compile(r"(?:sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+)", re.IGNORECASE)


def sanitized_tail(value: str | None, limit: int = 2000) -> str | None:
    if not value:
        return None
    return _SECRET.sub("[REDACTED]", value)[-limit:]


def write_reports(results: list[ScenarioResult], output_dir: str | Path, metadata: dict[str, object]) -> None:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    safe_results = [
        {**asdict(result), "failure_tail": sanitized_tail(result.failure_tail)}
        for result in results
    ]
    aggregate = aggregate_score(results)
    payload = {"metadata": metadata, "aggregate": aggregate, "results": safe_results}
    (target / "aggregate.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = ["# travis SDLC Evaluation", "", f"Passed: {aggregate['passed']}/{aggregate['total']}", ""]
    lines.extend(
        f"- {result.scenario_id}: {result.status} ({result.duration_ms} ms)"
        for result in results
    )
    (target / "aggregate.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


__all__ = ["sanitized_tail", "write_reports"]
