"""Audit sanitized artifacts from the continuous 21-prompt TUI run."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path


REQUIRED_FEATURES = frozenset(
    {
        "read",
        "search",
        "write",
        "edit",
        "bash",
        "process_start",
        "process_poll",
        "process_write",
        "process_interrupt",
        "ctrl_c_escalation",
        "subagent",
        "compaction",
        "auto_compaction",
        "session_persistence",
        "guardrail",
        "capability_grant",
        "provider_model",
        "tdd",
        "debugging",
        "review",
        "package_build",
        "skills",
        "extensions",
        "shutdown",
    }
)
_SECRET = re.compile(r"(?:sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+)", re.IGNORECASE)


@dataclass(frozen=True)
class FeatureAudit:
    observed_features: tuple[str, ...]
    missing_features: tuple[str, ...]
    result_count: int
    passed_count: int
    session_ids: tuple[str, ...]
    session_paths: tuple[str, ...]
    secret_leaks: tuple[str, ...]
    nonterminal_processes: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return (
            self.result_count == 21
            and self.passed_count == 21
            and not self.missing_features
            and len(self.session_ids) == 1
            and len(self.session_paths) == 1
            and not self.secret_leaks
            and not self.nonterminal_processes
        )

    @classmethod
    def from_artifacts(cls, root: str | Path) -> "FeatureAudit":
        base = Path(root).expanduser().resolve()
        results = _load_results(base)
        events = _load_jsonl(base / "trace.jsonl")
        observed: set[str] = set()

        tool_events = [event for event in events if event.get("event") == "tool_end"]
        tool_names = {str(event.get("tool") or "") for event in tool_events}
        observed.update(tool_names & {"read", "write", "edit", "bash"})
        if any(event.get("operation") == "search" for event in tool_events):
            observed.add("search")
        if any(event.get("operation") == "package_build" for event in tool_events):
            observed.add("package_build")
        process_actions = {
            str(event.get("action") or "")
            for event in tool_events
            if event.get("tool") == "process"
        }
        for action, feature in {
            "start": "process_start",
            "poll": "process_poll",
            "write": "process_write",
            "interrupt": "process_interrupt",
            "terminate": "process_interrupt",
            "kill": "process_interrupt",
        }.items():
            if action in process_actions:
                observed.add(feature)
        if "spawn_subagent" in tool_names and any(
            event.get("tool") in {"wait_subagent", "get_subagent_result"} or event.get("tool") == "spawn_subagent"
            for event in tool_events
        ):
            observed.add("subagent")
        if any(event.get("reason_code") for event in tool_events):
            observed.add("guardrail")

        compactions = [event for event in events if event.get("event") == "compaction_end" and event.get("status") == "ok"]
        if compactions:
            observed.add("compaction")
        if any(event.get("trigger") in {"threshold", "overflow"} for event in compactions):
            observed.add("auto_compaction")
        if any(event.get("event") == "capability_granted" and event.get("status") == "ok" for event in events):
            observed.add("capability_grant")
        if any(
            event.get("event") == "model_selected"
            and event.get("provider") == "openrouter"
            and event.get("model") == "stepfun/step-3.7-flash"
            for event in events
        ):
            observed.add("provider_model")
        if any(event.get("event") == "extension_command" and event.get("status") == "ok" for event in events):
            observed.add("extensions")
        if max(
            (int(event.get("interrupt_count") or 0) for event in events if event.get("event") == "user_command_interrupt"),
            default=0,
        ) >= 2:
            observed.add("ctrl_c_escalation")
        if any(event.get("event") == "shutdown" and event.get("status") == "ok" for event in events):
            observed.add("shutdown")

        passed_ids = {str(result.get("scenario_id") or "") for result in results if result.get("status") == "passed"}
        if {"01-python-cli-feature", "02-python-async-race"} <= passed_ids:
            observed.add("tdd")
        if "17-failing-suite-diagnosis" in passed_ids:
            observed.add("debugging")
        if "03-python-parser-refactor" in passed_ids and "subagent" in observed:
            observed.add("review")
        if "21-release-packaging" in passed_ids:
            observed.add("package_build")
        if (base / "workspace/scenarios/03-python-parser-refactor/SKILL_APPLIED.txt").is_file():
            observed.add("skills")

        session_ids = tuple(sorted({str(result.get("session_id")) for result in results if result.get("session_id")}))
        session_paths = tuple(sorted({str(result.get("session_path")) for result in results if result.get("session_path")}))
        if len(results) == 21 and len(session_ids) == 1 and len(session_paths) == 1:
            observed.add("session_persistence")

        terminal_states = {"exited", "timed_out", "terminated", "failed"}
        nonterminal = tuple(
            sorted(
                str(event.get("process_id") or "unknown")
                for event in events
                if event.get("event") == "process_event" and event.get("process_state") not in terminal_states
            )
        )
        leaks = _secret_leaks(base)
        missing = tuple(sorted(REQUIRED_FEATURES - observed))
        return cls(
            observed_features=tuple(sorted(observed)),
            missing_features=missing,
            result_count=len(results),
            passed_count=sum(result.get("status") == "passed" for result in results),
            session_ids=session_ids,
            session_paths=session_paths,
            secret_leaks=leaks,
            nonterminal_processes=nonterminal,
        )


def _load_results(root: Path) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for path in sorted((root / "runs").glob("*/result.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            results.append(value)
    return results


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


def _secret_leaks(root: Path) -> tuple[str, ...]:
    leaks: list[str] = []
    for relative in ("trace.jsonl", "conversation.jsonl", "terminal.log", "aggregate.json", "aggregate.md"):
        path = root / relative
        if path.is_file() and _SECRET.search(path.read_text(encoding="utf-8", errors="replace")):
            leaks.append(relative)
    return tuple(leaks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit a Travis234 live-21 artifact directory")
    parser.add_argument("artifact_dir")
    args = parser.parse_args(argv)
    audit = FeatureAudit.from_artifacts(args.artifact_dir)
    print(json.dumps({**asdict(audit), "passed": audit.passed}, indent=2))
    return 0 if audit.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["FeatureAudit", "REQUIRED_FEATURES", "main"]
