"""Independent acceptance checks for one persistent 21-prompt Travis234 run."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from evals.feature_audit import FeatureAudit


EXPECTED_PROMPT_COUNT = 21
DEFAULT_EXPECTED_MODEL = "stepfun/step-3.7-flash"


@dataclass(frozen=True)
class RunVerification:
    expected_model: str
    prompt_count: int
    conversation_count: int
    turn_start_count: int
    turn_end_count: int
    turn_ready_count: int
    session_ids: tuple[str, ...]
    session_paths: tuple[str, ...]
    errors: tuple[str, ...]
    feature_audit: FeatureAudit

    @property
    def passed(self) -> bool:
        return not self.errors and self.feature_audit.passed


def verify_run(
    root: str | Path,
    *,
    expected_model: str = DEFAULT_EXPECTED_MODEL,
) -> RunVerification:
    base = Path(root).expanduser().resolve()
    results = _load_results(base)
    conversation = _load_jsonl(base / "conversation.jsonl")
    events = _load_jsonl(base / "trace.jsonl")
    starts = [event for event in events if event.get("event") == "turn_start"]
    ends = [event for event in events if event.get("event") == "turn_end"]
    ready = [event for event in events if event.get("event") == "turn_ready"]
    errors: list[str] = []

    _require_count(errors, "scenario results", len(results))
    _require_count(errors, "conversation records", len(conversation))
    _require_count(errors, "turn_start events", len(starts))
    _require_count(errors, "turn_end events", len(ends))
    _require_count(errors, "turn_ready events", len(ready))

    failed_results = [
        str(result.get("scenario_id") or "unknown")
        for result in results
        if result.get("status") != "passed"
    ]
    if failed_results:
        errors.append(f"{len(failed_results)} scenario results did not pass")

    session_ids = tuple(sorted({str(item.get("session_id")) for item in results if item.get("session_id")}))
    session_paths = tuple(
        sorted({str(item.get("session_path")) for item in results if item.get("session_path")})
    )
    if len(session_ids) != 1:
        errors.append(f"results span {len(session_ids)} session ids")
    if len(session_paths) != 1:
        errors.append(f"results span {len(session_paths)} session paths")
    tui_ready = [event for event in events if event.get("event") == "tui_ready"]
    if len(tui_ready) != 1:
        errors.append(f"expected one tui_ready event, found {len(tui_ready)}")
    elif (
        len(session_ids) != 1
        or len(session_paths) != 1
        or str(tui_ready[0].get("session_id") or "") != session_ids[0]
        or str(tui_ready[0].get("session_path") or "") != session_paths[0]
    ):
        errors.append("tui_ready session identity does not match scenario results")

    selected = [event for event in events if event.get("event") == "model_selected"]
    if not any(
        event.get("model") == expected_model and bool(event.get("provider"))
        for event in selected
    ):
        errors.append(f"expected selected model {expected_model!r} was not observed")
    if any(
        result.get("model_id") != expected_model or not result.get("model_provider")
        for result in results
    ):
        errors.append("one or more results recorded the wrong provider/model")

    result_turn_ids = [str(item.get("turn_id") or "") for item in results]
    conversation_turn_ids = [str(item.get("turn_id") or "") for item in conversation]
    start_turn_ids = [str(item.get("turn_id") or "") for item in starts]
    end_turn_ids = [str(item.get("turn_id") or "") for item in ends]
    if len(set(result_turn_ids)) != len(results) or "" in result_turn_ids:
        errors.append("result turn ids are missing or duplicated")
    if result_turn_ids != conversation_turn_ids:
        errors.append("result and conversation turn order differ")
    if result_turn_ids != start_turn_ids or result_turn_ids != end_turn_ids:
        errors.append("trace turn ids do not match result order")

    conversation_by_turn = {
        str(item.get("turn_id")): item
        for item in conversation
        if item.get("turn_id")
    }
    for result in results:
        turn_id = str(result.get("turn_id") or "")
        record = conversation_by_turn.get(turn_id)
        if record is None:
            continue
        if result.get("prompt") != record.get("prompt"):
            errors.append(f"prompt mismatch for {turn_id}")
        if result.get("response") != record.get("response"):
            errors.append(f"assistant output mismatch for {turn_id}")
        if not str(record.get("response") or "").strip():
            errors.append(f"assistant output is empty for {turn_id}")

    missing_context = [
        str(result.get("scenario_id") or "unknown")
        for result in results
        if not result.get("context_window") or "context_percent" not in result
    ]
    if missing_context:
        errors.append(f"{len(missing_context)} results lack footer context telemetry")
    if any(
        event.get("context_window") is None or "context_percent" not in event
        for event in ready
    ):
        errors.append("one or more turn_ready events lack footer context telemetry")

    shutdowns = [
        event
        for event in events
        if event.get("event") == "shutdown" and event.get("status") == "ok"
    ]
    if len(shutdowns) != 1:
        errors.append(f"expected one clean shutdown event, found {len(shutdowns)}")

    feature_audit = FeatureAudit.from_artifacts(base, expected_model=expected_model)
    if feature_audit.missing_features:
        errors.append(
            "feature audit missing: " + ", ".join(feature_audit.missing_features)
        )
    if feature_audit.secret_leaks:
        errors.append("secret-shaped material found in sanitized artifacts")
    if feature_audit.nonterminal_processes:
        errors.append("nonterminal processes remain in the final trace")

    return RunVerification(
        expected_model=expected_model,
        prompt_count=len(results),
        conversation_count=len(conversation),
        turn_start_count=len(starts),
        turn_end_count=len(ends),
        turn_ready_count=len(ready),
        session_ids=session_ids,
        session_paths=session_paths,
        errors=tuple(errors),
        feature_audit=feature_audit,
    )


def write_verification(verification: RunVerification, root: str | Path) -> None:
    target = Path(root).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    payload = {**asdict(verification), "passed": verification.passed}
    json_path = target / "verification.json"
    markdown_path = target / "verification.md"
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Travis234 Live Acceptance Verification",
        "",
        f"Passed: {'yes' if verification.passed else 'no'}",
        "",
        f"Prompts: {verification.prompt_count}/{EXPECTED_PROMPT_COUNT}",
        f"Footer checkpoints: {verification.turn_ready_count}/{EXPECTED_PROMPT_COUNT}",
        f"Model: {verification.expected_model}",
        "",
    ]
    if verification.errors:
        lines.extend(["Errors", "", *(f"- {error}" for error in verification.errors), ""])
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    os.chmod(json_path, 0o600)
    os.chmod(markdown_path, 0o600)


def _require_count(errors: list[str], label: str, actual: int) -> None:
    if actual != EXPECTED_PROMPT_COUNT:
        errors.append(f"expected {EXPECTED_PROMPT_COUNT} {label}, found {actual}")


def _load_results(root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted((root / "runs").glob("*/result.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    rows: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


__all__ = [
    "DEFAULT_EXPECTED_MODEL",
    "EXPECTED_PROMPT_COUNT",
    "RunVerification",
    "verify_run",
    "write_verification",
]
