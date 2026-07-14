from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Scenario:
    id: str
    setup: str
    turns: tuple[str, ...]
    compact_after: tuple[int, ...]
    verifiers: tuple[tuple[str, ...], ...]
    timeout_seconds: int = 300


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    status: str
    model_provider: str | None
    model_id: str | None
    verifier_exit_codes: tuple[int, ...]
    turns: int
    compactions: int
    duration_ms: int
    failure_tail: str | None = None
    session_id: str | None = None
    session_path: str | None = None
    turn_id: str | None = None
    prompt: str | None = None
    response: str | None = None
    context_tokens: int | None = None
    context_window: int | None = None
    context_percent: float | None = None
    context_estimated: bool | None = None
    context_confidence: str | None = None
    fault_domain: str | None = None
    failure_evidence: str | None = None


def load_scenarios(path: str | Path | None = None) -> list[Scenario]:
    source = Path(path) if path else Path(__file__).with_name("scenarios.json")
    data = json.loads(source.read_text(encoding="utf-8"))
    return [
        Scenario(
            id=str(item["id"]),
            setup=str(item["setup"]),
            turns=tuple(str(turn) for turn in item["turns"]),
            compact_after=tuple(int(index) for index in item.get("compact_after", [])),
            verifiers=tuple(tuple(str(part) for part in command) for command in item["verifiers"]),
            timeout_seconds=int(item.get("timeout_seconds", 300)),
        )
        for item in data
    ]


__all__ = ["Scenario", "ScenarioResult", "load_scenarios"]
