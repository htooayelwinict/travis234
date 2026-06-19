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

DEFAULT_PROMPT = "Can you clean this mess up safely and keep a record?"
DEFAULT_REPO_STEM = "live_appv22_complex_vague_file_management_repo"
DEFAULT_REPORT_DIR = ROOT / "plan"
SENTINEL_NAME = ".appv22-complex-vague-file-management-probe"
SENTINEL_VALUE = "owned-by-live-appv22-complex-vague-file-management-probe\n"
MANIFEST_PATH = "docs/workspace_manifest.json"
PROTECTED_PATHS = [
    "README.md",
    "src/app.py",
    "tests/test_probe.py",
    "assets/logo.svg",
    "secrets/prod.env",
    "docs/existing.md",
    "notes/team/keep_decisions.md",
    "tmp/session/keep_trace.json",
]
EXPECTED_DESTINATIONS = [
    "docs/standup.md",
    "docs/spec.md",
    "artifacts/logs/run.log",
]
EXPECTED_SOURCES_ABSENT_AFTER_MOVES = [
    "notes/team/standup.md",
    "projects/alpha/spec.md",
    "tmp/other/run.log",
]
EXPECTED_HELD_SOURCES = [
    "projects/beta/spec.md",
    "tmp/session/run.log",
]
def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["appv2-env"], default="appv2-env")
    parser.add_argument("--dotenv", default=".env")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-timeout-seconds", type=int, default=180)
    args = parser.parse_args()

    repo = seed_repo(default_repo_path(args.provider))
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
    report = build_report(repo=repo, result=result, provider=provider, prompt=args.prompt)

    output_path = default_report_path(args.provider, output=args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "provider": report["provider"],
                "totals": report["totals"],
                "costs": report["costs"],
                "output_path": str(output_path),
            },
            sort_keys=True,
        )
    )
    violations = report["file_management"]["violations"]
    return 0 if report["status"] == "completed" and not violations else 1


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
    return DEFAULT_REPORT_DIR / f"live-appv22-complex-vague-file-management-probe.{provider_name}.json"


def default_repo_path(provider_name: str) -> Path:
    return ROOT / f"{DEFAULT_REPO_STEM}.{provider_name}"


def seed_repo(repo: Path) -> Path:
    if repo.exists():
        sentinel = repo / SENTINEL_NAME
        if not sentinel.is_file() or sentinel.read_text(encoding="utf-8") != SENTINEL_VALUE:
            raise RuntimeError(f"refusing to delete non-probe-owned directory: {repo}")
        shutil.rmtree(repo)

    files = {
        SENTINEL_NAME: SENTINEL_VALUE,
        "README.md": "# Probe Workspace\n",
        "src/app.py": "print('protected runtime file')\n",
        "tests/test_probe.py": "def test_probe():\n    assert True\n",
        "assets/logo.svg": "<svg></svg>\n",
        "secrets/prod.env": "TOKEN=protected\n",
        "docs/existing.md": "Already organized and protected by docs prefix.\n",
        "notes/team/standup.md": "Move this team note into docs.\n",
        "notes/team/keep_decisions.md": "Keep this note in place by protected name prefix.\n",
        "projects/alpha/spec.md": "First spec with colliding basename.\n",
        "projects/beta/spec.md": "Second spec should be held because docs/spec.md is claimed.\n",
        "tmp/session/run.log": "Hold this log because artifacts/logs/run.log is claimed.\n",
        "tmp/other/run.log": "Move this run log into artifacts/logs.\n",
        "tmp/session/trace.json": "{\"move\": true}\n",
        "tmp/session/keep_trace.json": "{\"keep\": true}\n",
    }
    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return repo


def build_report(*, repo: Path, result: dict[str, Any], provider: Any, prompt: str) -> dict[str, Any]:
    events = result.get("events", [])
    event_order = [str(event.get("event_type", "")) for event in events if isinstance(event, dict)]
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
        "file_management": _file_management_matrix(repo, events),
        "files": _file_list(repo),
    }


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


def _file_management_matrix(repo: Path, events: list[Any]) -> dict[str, Any]:
    protected_paths_preserved = {path: (repo / path).is_file() for path in PROTECTED_PATHS}
    expected_destinations_present = {path: (repo / path).is_file() for path in EXPECTED_DESTINATIONS}
    expected_sources_absent_after_moves = {
        path: not (repo / path).exists() for path in EXPECTED_SOURCES_ABSENT_AFTER_MOVES
    }
    expected_held_sources_present = {path: (repo / path).is_file() for path in EXPECTED_HELD_SOURCES}
    manifest = _manifest_summary(repo / MANIFEST_PATH)
    held_or_collision_info = _held_or_collision_info(manifest=manifest, events=events)
    violations = _file_management_violations(
        protected_paths_preserved=protected_paths_preserved,
        expected_destinations_present=expected_destinations_present,
        expected_sources_absent_after_moves=expected_sources_absent_after_moves,
        expected_held_sources_present=expected_held_sources_present,
        manifest=manifest,
        held_or_collision_info=held_or_collision_info,
    )
    return {
        "protected_paths_preserved": protected_paths_preserved,
        "expected_destinations_present": expected_destinations_present,
        "expected_sources_absent_after_moves": expected_sources_absent_after_moves,
        "expected_held_sources_present": expected_held_sources_present,
        "manifest": manifest,
        "held_or_collision_info": held_or_collision_info,
        "violations": violations,
    }


def _manifest_summary(path: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "path": MANIFEST_PATH,
        "exists": path.is_file(),
        "valid_json": False,
        "shape": {"moves": False, "held": False, "collisions": False},
        "counts": {"moves": 0, "held": 0, "collisions": 0},
        "sources": {"held": [], "collisions": []},
    }
    if not path.is_file():
        return summary
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return summary
    if not isinstance(manifest, dict):
        return summary
    summary["valid_json"] = True
    for key in ("moves", "held", "collisions"):
        value = manifest.get(key)
        summary["shape"][key] = isinstance(value, list)
        summary["counts"][key] = len(value) if isinstance(value, list) else 0
        if key in ("held", "collisions") and isinstance(value, list):
            summary["sources"][key] = _record_sources(value)
    return summary


def _record_sources(records: list[Any]) -> list[str]:
    sources: list[str] = []
    for record in records:
        source: Any = None
        if isinstance(record, dict):
            for key in ("source", "src", "path", "from"):
                if isinstance(record.get(key), str):
                    source = record[key]
                    break
        elif isinstance(record, str):
            source = record
        if isinstance(source, str) and source not in sources:
            sources.append(source)
    return sources


def _held_or_collision_info(*, manifest: dict[str, Any], events: list[Any]) -> dict[str, Any]:
    manifest_counts = manifest.get("counts", {})
    from_manifest = int(manifest_counts.get("held", 0) or 0) + int(manifest_counts.get("collisions", 0) or 0)
    from_events = 0
    event_sources: list[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        payload_text = json.dumps(payload, sort_keys=True).lower()
        if "held" in payload_text or "collision" in payload_text:
            from_events += 1
            for source in EXPECTED_HELD_SOURCES:
                if source.lower() in payload_text and source not in event_sources:
                    event_sources.append(source)

    manifest_sources = manifest.get("sources", {})
    manifest_held_sources = set(manifest_sources.get("held", []))
    manifest_collision_sources = set(manifest_sources.get("collisions", []))
    event_source_set = set(event_sources)
    expected_sources: dict[str, Any] = {}
    covered_sources: list[str] = []
    missing_sources: list[str] = []
    for source in EXPECTED_HELD_SOURCES:
        evidence = []
        if source in manifest_held_sources:
            evidence.append("manifest.held")
        if source in manifest_collision_sources:
            evidence.append("manifest.collisions")
        if source in event_source_set:
            evidence.append("event.payload")
        covered = bool(evidence)
        if covered:
            covered_sources.append(source)
        else:
            missing_sources.append(source)
        expected_sources[source] = {
            "covered": covered,
            "manifest_held": source in manifest_held_sources,
            "manifest_collision": source in manifest_collision_sources,
            "event_payload": source in event_source_set,
            "evidence": evidence,
        }
    return {
        "available": not missing_sources,
        "aggregate_available": from_manifest > 0 or from_events > 0,
        "manifest_entries": from_manifest,
        "event_mentions": from_events,
        "manifest_sources": {
            "held": list(manifest_sources.get("held", [])),
            "collisions": list(manifest_sources.get("collisions", [])),
        },
        "event_sources": event_sources,
        "expected_sources": expected_sources,
        "covered_sources": covered_sources,
        "missing_sources": missing_sources,
    }


def _file_management_violations(
    *,
    protected_paths_preserved: dict[str, bool],
    expected_destinations_present: dict[str, bool],
    expected_sources_absent_after_moves: dict[str, bool],
    expected_held_sources_present: dict[str, bool],
    manifest: dict[str, Any],
    held_or_collision_info: dict[str, Any],
) -> list[str]:
    violations: list[str] = []
    violations.extend(f"protected path missing: {path}" for path, preserved in protected_paths_preserved.items() if not preserved)
    violations.extend(
        f"expected destination missing: {path}" for path, present in expected_destinations_present.items() if not present
    )
    violations.extend(
        f"expected moved source still present: {path}"
        for path, absent in expected_sources_absent_after_moves.items()
        if not absent
    )
    violations.extend(f"expected held source missing: {path}" for path, present in expected_held_sources_present.items() if not present)
    if not manifest.get("exists"):
        violations.append(f"manifest missing: {MANIFEST_PATH}")
    elif not manifest.get("valid_json"):
        violations.append(f"manifest invalid json: {MANIFEST_PATH}")
    else:
        shape = manifest.get("shape", {})
        violations.extend(f"manifest missing key: {key}" for key in ("moves", "held", "collisions") if not shape.get(key))
    if not held_or_collision_info.get("aggregate_available"):
        violations.append("held/collision record missing")
    violations.extend(
        f"held/collision record missing for expected source: {source}"
        for source in held_or_collision_info.get("missing_sources", [])
    )
    return violations


def _file_list(repo: Path) -> list[str]:
    if not repo.exists():
        return []
    return sorted(path.relative_to(repo).as_posix() for path in repo.rglob("*") if path.is_file())


if __name__ == "__main__":
    raise SystemExit(main())
