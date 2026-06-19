from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import signal
import shutil
import sys
from pathlib import Path
from types import FrameType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "appV2.2"))

from appv22 import AppV22AgentRuntime
from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.providers.appv2_env import create_appv22_provider_from_appv2_env
from appv22.runtime.services import create_appv22_services

DEFAULT_PROMPT = (
    "Can you make a small useful record for whoever picks this up next? "
    "Put it somewhere sensible; I do not care about the exact filename."
)
DEFAULT_REPO_STEM = "live_appv22_vague_file_creation_repo"
DEFAULT_REPORT_DIR = ROOT / "plan"
SENTINEL_NAME = ".appv22-vague-file-creation-probe"
SENTINEL_VALUE = "owned-by-live-appv22-vague-file-creation-probe\n"
SEED_FILES = {
    SENTINEL_NAME: SENTINEL_VALUE,
    "README.md": "# File Creation Probe\n\nTiny workspace for a handoff-note creation task.\n",
    "src/app.py": "print('protected runtime file')\n",
    "docs/context.md": "Current project context: runtime probe workspace.\n",
    "notes/raw_brain_dump.txt": "remember to document next steps before handoff\n",
}
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["appv2-env"], default="appv2-env")
    parser.add_argument("--dotenv", default=".env")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    repo = seed_repo(default_repo_path(args.provider))
    before_files = _file_list(repo)
    provider: Any = None
    try:
        with bounded_probe_run(args.run_timeout_seconds):
            provider = create_provider(args.provider, dotenv_path=args.dotenv)
            services = create_appv22_services(
                root_path=repo,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            result = AppV22AgentRuntime(root_path=repo, services=services, max_turns=12).run(args.prompt)
    except ProbeTimeoutError as exc:
        result = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}

    report = build_report(repo=repo, before_files=before_files, result=result, provider=provider, prompt=args.prompt)
    output_path = default_report_path(args.provider, output=args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "reason": report["reason"],
                "provider": report["provider"],
                "totals": report["totals"],
                "costs": report["costs"],
                "creation": report["file_creation"]["summary"],
                "output_path": str(output_path),
            },
            sort_keys=True,
        )
    )
    return 0 if report["file_creation"]["summary"]["passed"] else 1


class ProbeTimeoutError(TimeoutError):
    pass


@contextmanager
def bounded_probe_run(timeout_seconds: int):
    if timeout_seconds <= 0:
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)

    def _raise_timeout(_signum: int, _frame: FrameType | None) -> None:
        raise ProbeTimeoutError(f"probe exceeded {timeout_seconds}s timeout")

    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.alarm(timeout_seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def create_provider(provider_name: str, *, dotenv_path: str):
    return create_appv22_provider_from_appv2_env(dotenv_path=dotenv_path)


def default_report_path(provider_name: str, *, output: Path | None = None) -> Path:
    if output is not None:
        return output
    return DEFAULT_REPORT_DIR / f"live-appv22-vague-file-creation-probe.{provider_name}.json"


def default_repo_path(provider_name: str) -> Path:
    return ROOT / f"{DEFAULT_REPO_STEM}.{provider_name}"


def seed_repo(repo: Path) -> Path:
    if repo.exists():
        sentinel = repo / SENTINEL_NAME
        if not sentinel.is_file() or sentinel.read_text(encoding="utf-8") != SENTINEL_VALUE:
            raise RuntimeError(f"refusing to delete non-probe-owned directory: {repo}")
        shutil.rmtree(repo)

    for relative, content in SEED_FILES.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return repo


def build_report(
    *,
    repo: Path,
    before_files: list[str],
    result: dict[str, Any],
    provider: Any,
    prompt: str,
) -> dict[str, Any]:
    events = result.get("events", [])
    event_order = [str(event.get("event_type", "")) for event in events if isinstance(event, dict)]
    after_files = _file_list(repo)
    created_files = sorted(path for path in after_files if path not in set(before_files))
    modified_files = _modified_seed_files(repo)
    decision_matrix = _decision_matrix(events)
    tool_matrix = _tool_matrix(events)
    loop_matrix = _loop_matrix(events)
    creation_matrix = _creation_matrix(repo, created_files, modified_files, events)
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "user_prompt": prompt,
        "provider": _provider_id(provider),
        "totals": {
            "events": len(events),
            "decisions": _count_events(event_order, "DecisionProposed"),
            "tool_calls": _count_events(event_order, "ToolCallCompleted", "ToolCallDenied"),
            "mutation_receipts": _count_events(event_order, "MutationApplied"),
            "verification_receipts": _count_events(event_order, "VerificationRecorded"),
        },
        "costs": _costs(provider),
        "event_order": event_order,
        "loop_matrix": loop_matrix,
        "decision_matrix": decision_matrix,
        "tool_matrix": tool_matrix,
        "file_creation": creation_matrix,
        "files": {
            "before": before_files,
            "after": after_files,
            "created": created_files,
            "modified_seed_files": modified_files,
        },
    }


def _creation_matrix(
    repo: Path,
    created_files: list[str],
    modified_files: list[str],
    events: list[Any],
) -> dict[str, Any]:
    candidate_paths = [
        path for path in created_files
        if path != SENTINEL_NAME and not path.endswith(".pyc") and "__pycache__/" not in path
    ]
    useful_candidates = [
        path for path in candidate_paths
        if Path(path).suffix.lower() in {".md", ".txt", ".json"} and _has_handoff_content(repo / path)
    ]
    mutation_payloads = [
        event.get("payload", {})
        for event in events
        if isinstance(event, dict) and event.get("event_type") == "MutationApplied"
    ]
    unsupported_write_errors = _payload_mentions(events, "unsupported_write_path")
    tool_blocked_errors = _payload_mentions(events, "tool_not_active") or _payload_mentions(events, "unknown_tool")
    return {
        "summary": {
            "passed": bool(useful_candidates),
            "new_file_count": len(candidate_paths),
            "useful_created_files": useful_candidates,
            "modified_seed_files": modified_files,
            "unsupported_write_errors": unsupported_write_errors,
            "tool_blocked_errors": tool_blocked_errors,
        },
        "created_file_checks": {
            path: {
                "exists": (repo / path).is_file(),
                "bytes": (repo / path).stat().st_size if (repo / path).is_file() else 0,
                "has_handoff_content": _has_handoff_content(repo / path),
            }
            for path in candidate_paths
        },
        "mutation_payloads": mutation_payloads,
    }


def _has_handoff_content(path: Path) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace").lower()
    return any(term in text for term in ("handoff", "next", "context", "record", "note", "todo"))


def _modified_seed_files(repo: Path) -> list[str]:
    modified: list[str] = []
    for relative, expected in SEED_FILES.items():
        path = repo / relative
        if path.is_file() and path.read_text(encoding="utf-8", errors="replace") != expected:
            modified.append(relative)
    return sorted(modified)


def _loop_matrix(events: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        row = {"event_type": event.get("event_type")}
        if "turn_index" in payload:
            row["turn_index"] = payload.get("turn_index")
        if "kind" in payload:
            row["decision_kind"] = payload.get("kind")
        if "mode" in payload:
            row["mode"] = payload.get("mode")
        if "tool_id" in payload:
            row["tool_id"] = payload.get("tool_id")
        if "reason" in payload:
            row["reason"] = payload.get("reason")
        rows.append(row)
    return rows


def _decision_matrix(events: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict) or event.get("event_type") != "DecisionProposed":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        rows.append(
            {
                "turn_index": payload.get("turn_index"),
                "kind": payload.get("kind"),
                "reason": payload.get("reason"),
                "payload": payload.get("payload"),
                "evidence_refs": payload.get("evidence_refs"),
            }
        )
    return rows


def _tool_matrix(events: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        if not isinstance(event, dict) or event.get("event_type") not in {"ToolCallCompleted", "ToolCallDenied"}:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        rows.append(
            {
                "event_type": event.get("event_type"),
                "tool_id": payload.get("tool_id"),
                "status": payload.get("status"),
                "errors": payload.get("errors", []),
            }
        )
    return rows


def _payload_mentions(events: list[Any], needle: str) -> bool:
    return needle in json.dumps(events, sort_keys=True, default=str)


def _count_events(event_order: list[str], *event_types: str) -> int:
    return sum(1 for event_type in event_order if event_type in event_types)


def _provider_id(provider: Any) -> str | None:
    if provider is None:
        return None
    return str(getattr(provider, "provider_id", type(provider).__name__))


def _costs(provider: Any) -> dict[str, Any]:
    candidates = [
        ("provider.usage_snapshot", provider),
        ("client.usage_snapshot", getattr(provider, "client", None)),
        ("delegate.usage_snapshot", getattr(provider, "delegate", None)),
        ("delegate.client.usage_snapshot", getattr(getattr(provider, "delegate", None), "client", None)),
    ]
    for source, candidate in candidates:
        usage_snapshot = getattr(candidate, "usage_snapshot", None)
        if callable(usage_snapshot):
            snapshot = usage_snapshot()
            if isinstance(snapshot, dict):
                return {
                    "available": True,
                    "source": source,
                    "model_calls": snapshot.get("model_calls"),
                    "total_tokens": snapshot.get("total_tokens"),
                    "cost": snapshot.get("cost"),
                }
    return {
        "available": False,
        "source": None,
        "model_calls": None,
        "total_tokens": None,
        "cost": None,
    }


def _file_list(repo: Path) -> list[str]:
    if not repo.exists():
        return []
    return sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*") if path.is_file())


if __name__ == "__main__":
    raise SystemExit(main())
