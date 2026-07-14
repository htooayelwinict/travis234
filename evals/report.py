from __future__ import annotations

import html
import json
import os
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


def sanitized_text(value: str | None) -> str | None:
    if value is None:
        return None
    return _SECRET.sub("[REDACTED]", value)


def _safe_result(result: ScenarioResult) -> dict[str, object]:
    payload = asdict(result)
    for field in ("failure_tail", "prompt", "response", "failure_evidence"):
        payload[field] = sanitized_text(payload.get(field))  # type: ignore[arg-type]
    return payload


def _context_summary(result: ScenarioResult) -> str:
    confidence = result.context_confidence or "unknown"
    if result.context_window is None:
        return f"unknown (confidence: {confidence})"
    tokens = f"{result.context_tokens:,}" if result.context_tokens is not None else "unknown"
    percent = f"{result.context_percent:.2f}%" if result.context_percent is not None else "unknown"
    estimate = "estimated, " if result.context_estimated else ""
    return f"{percent} ({tokens} / {result.context_window:,} tokens; {estimate}{confidence})"


def write_reports(results: list[ScenarioResult], output_dir: str | Path, metadata: dict[str, object]) -> None:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    safe_results = [_safe_result(result) for result in results]
    aggregate = aggregate_score(results)
    payload = {"metadata": metadata, "aggregate": aggregate, "results": safe_results}
    json_path = target / "aggregate.json"
    markdown_path = target / "aggregate.md"
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = ["# Travis234 SDLC Evaluation", "", f"Passed: {aggregate['passed']}/{aggregate['total']}", ""]
    for result in results:
        prompt = sanitized_text(result.prompt) or ""
        response = sanitized_text(result.response) or ""
        lines.extend(
            [
                f"## {result.scenario_id}",
                "",
                f"Status: {result.status} · Fault domain: {result.fault_domain or 'none'} · "
                f"Duration: {result.duration_ms} ms",
                "",
                f"Footer context: {_context_summary(result)}",
                "",
                "Prompt",
                "",
                f"<pre>{html.escape(prompt)}</pre>",
                "",
                "Assistant output",
                "",
                f"<pre>{html.escape(response)}</pre>",
                "",
            ]
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(json_path, 0o600)
    os.chmod(markdown_path, 0o600)


__all__ = ["sanitized_tail", "sanitized_text", "write_reports"]
