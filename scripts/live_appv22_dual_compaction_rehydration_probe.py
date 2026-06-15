from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
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
from appv22.context.compressor import AgentContextCompressor
from appv22.context.gateway_guard import GatewayContextGuard
from appv22.extensions.file_management.extension import FileManagementExtension
from appv22.providers.appv2_env import create_appv22_provider_from_appv2_env
from appv22.runtime.services import create_appv22_services

DEFAULT_REPORT_DIR = ROOT / "plan"
DEFAULT_REPO = ROOT / "live_appv22_dual_compaction_rehydration_repo.appv2-env"
DEFAULT_PROMPT = (
    "This workspace has a lot of noisy incoming files. First inspect the repository. "
    "If the previous repository observation is compacted into a context summary and you need exact details, "
    "use the available tool to rehydrate the repository map before deciding what to do. "
    "Do not mutate files in this probe; pause after you have enough evidence."
)
SENTINEL_NAME = ".appv22-dual-compaction-rehydration-probe"
SENTINEL_VALUE = "owned-by-live-appv22-dual-compaction-rehydration-probe\n"
RAW_MARKER = "RAW_LIVE_DUAL_COMPACTION_SENTINEL"
TOOL_NAME_MAP = {
    "repo_snapshot": "file_management.repo_snapshot",
    "read_file": "file_management.read_file",
    "write_file": "file_management.write_file",
    "move_file": "file_management.move_file",
    "copy_file": "file_management.copy_file",
    "delete_file": "file_management.delete_file",
    "mkdir": "file_management.mkdir",
    "list_files": "file_management.list_files",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dotenv", default=str(ROOT / ".env"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--max-turns", type=int, default=4)
    parser.add_argument("--file-count", type=int, default=140)
    parser.add_argument("--run-timeout-seconds", type=int, default=180)
    parser.add_argument("--worker-timeout", type=int, default=60)
    parser.add_argument("--max-tokens", type=int, default=1800)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    configure_llm_env(args)
    repo = seed_repo(args.repo, file_count=args.file_count)
    provider: RecordingProvider | None = None
    try:
        with bounded_probe_run(args.run_timeout_seconds):
            provider = RecordingProvider(
                create_appv22_provider_from_appv2_env(
                    dotenv_path=args.dotenv,
                    tool_name_map=TOOL_NAME_MAP,
                )
            )
            services = create_appv22_services(
                root_path=repo,
                provider=provider,
                extensions=[FileManagementExtension()],
            )
            services.gateway_guard = GatewayContextGuard(max_chars=80_000, threshold=1.0)
            services.compressor = AgentContextCompressor(max_chars=2_800, threshold=0.50)
            result = AppV22AgentRuntime(root_path=repo, services=services, max_turns=args.max_turns).run(args.prompt)
    except ProbeTimeoutError as exc:
        result = {"status": "failed", "reason": "probe_timeout", "events": [], "error": str(exc)}

    report = build_report(repo=repo, result=result, provider=provider, prompt=args.prompt)
    output_path = args.output or DEFAULT_REPORT_DIR / "live-appv22-dual-compaction-rehydration-probe.appv2-env.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": report["status"],
                "reason": report["reason"],
                "provider": report["provider"],
                "model": report["model"],
                "costs": report["costs"],
                "proof": report["proof"],
                "output_path": str(output_path),
            },
            sort_keys=True,
        )
    )
    proof = report["proof"]
    return (
        0
        if proof["dual_compaction_carried"]
        and proof["rehydration_attempted"]
        and proof["no_reobserve_after_summary_evidence"]
        else 1
    )


def configure_llm_env(args: argparse.Namespace) -> None:
    os.environ["APPV2_WORKER_LLM_ENABLED"] = "true"
    os.environ["APPV2_WORKER_LLM_TIMEOUT_SECONDS"] = str(args.worker_timeout)
    os.environ["APPV2_WORKER_LLM_TEMPERATURE"] = str(args.temperature)
    os.environ["APPV2_WORKER_LLM_TOP_P"] = str(args.top_p)
    os.environ["APPV2_WORKER_LLM_SEED"] = str(args.seed)
    os.environ["APPV2_WORKER_LLM_RESPONSE_FORMAT"] = "json_schema"
    os.environ["APPV2_WORKER_LLM_MAX_TOKENS"] = str(args.max_tokens)


class RecordingProvider:
    def __init__(self, delegate: Any) -> None:
        if hasattr(delegate, "delegate"):
            delegate.delegate = RecordingRawDelegate(delegate.delegate)
        self.delegate = delegate
        self.provider_id = f"{getattr(delegate, 'provider_id', type(delegate).__name__)}-recording"
        self.prompts: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.raw_decisions: list[dict[str, Any]] = getattr(getattr(delegate, "delegate", None), "decisions", [])

    def decide(self, prompt: dict[str, Any]) -> Any:
        self.prompts.append(prompt)
        decision = self.delegate.decide(prompt)
        decision_dict = decision.to_dict() if hasattr(decision, "to_dict") else {
            "kind": getattr(decision, "kind", None),
            "reason": getattr(decision, "reason", ""),
            "payload": getattr(decision, "payload", {}),
            "evidence_refs": getattr(decision, "evidence_refs", []),
        }
        self.decisions.append(decision_dict)
        return decision


class RecordingRawDelegate:
    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.provider_id = getattr(delegate, "provider_id", type(delegate).__name__)
        self.decisions: list[dict[str, Any]] = []

    def decide(self, prompt: dict[str, Any]) -> Any:
        decision = self.delegate.decide(prompt)
        decision_dict = decision.to_dict() if hasattr(decision, "to_dict") else {
            "kind": getattr(decision, "kind", None),
            "reason": getattr(decision, "reason", ""),
            "payload": getattr(decision, "payload", {}),
            "evidence_refs": getattr(decision, "evidence_refs", []),
        }
        self.decisions.append(decision_dict)
        return decision

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)


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


def seed_repo(repo: Path, *, file_count: int) -> Path:
    if repo.exists():
        sentinel = repo / SENTINEL_NAME
        if not sentinel.is_file() or sentinel.read_text(encoding="utf-8") != SENTINEL_VALUE:
            raise RuntimeError(f"refusing to delete non-probe-owned directory: {repo}")
        shutil.rmtree(repo)

    files = {
        SENTINEL_NAME: SENTINEL_VALUE,
        "README.md": "# Dual Compaction Live Probe\n",
        "docs/keep.md": "Existing documentation that should not be mutated.\n",
        "src/app.py": "print('protected app file')\n",
        "tests/test_smoke.py": "def test_smoke():\n    assert True\n",
    }
    for index in range(file_count):
        files[f"incoming/{RAW_MARKER}_{index:03d}_workspace_note.md"] = f"temporary incoming note {index}\n"

    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return repo


def build_report(*, repo: Path, result: dict[str, Any], provider: RecordingProvider | None, prompt: str) -> dict[str, Any]:
    events = result.get("events", [])
    prompts = provider.prompts if provider is not None else []
    decisions = provider.decisions if provider is not None else []
    prompt_matrix = [_prompt_row(index=index, prompt=provider_prompt) for index, provider_prompt in enumerate(prompts, start=1)]
    event_matrix = [_event_row(index=index, event=event) for index, event in enumerate(events, start=1)]
    decision_matrix = [
        {
            "turn": index,
            "kind": decision.get("kind"),
            "reason": decision.get("reason"),
            "tool_id": (decision.get("payload") or {}).get("tool_id"),
            "evidence_refs": decision.get("evidence_refs") or [],
        }
        for index, decision in enumerate(decisions, start=1)
    ]
    tool_events = [row for row in event_matrix if row["event_type"] == "ToolCallCompleted"]
    proof = {
        "raw_context_removed_after_first_observation": any(
            row["turn"] > 1 and not row["raw_marker_visible"] and row["world_ref_count"] == 0 for row in prompt_matrix
        ),
        "summary_visible_after_compaction": any(
            row["turn"] > 1 and row["context_summary_visible"] for row in prompt_matrix
        ),
        "summary_carries_evidence": any(row["turn"] > 1 and row["summary_evidence_ref_count"] > 0 for row in prompt_matrix),
        "dual_compaction_carried": False,
        "rehydration_attempted": any(
            decision["kind"] == "tool_call"
            and decision["tool_id"] == "file_management.repo_snapshot"
            and _prompt_summary_evidence_ref_count(prompt_matrix, decision["turn"]) == 0
            for decision in decision_matrix
        ),
        "no_reobserve_after_summary_evidence": any(
            row["summary_evidence_ref_count"] > 0
            and not _decision_is_repo_snapshot_tool_call(decision_matrix, row["turn"])
            for row in prompt_matrix
        ),
        "raw_marker_leaked_after_compaction": any(row["turn"] > 1 and row["raw_marker_visible"] for row in prompt_matrix),
    }
    proof["dual_compaction_carried"] = (
        proof["raw_context_removed_after_first_observation"]
        and proof["summary_visible_after_compaction"]
        and proof["summary_carries_evidence"]
        and not proof["raw_marker_leaked_after_compaction"]
    )
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "user_prompt": prompt,
        "provider": getattr(provider, "provider_id", None),
        "model": os.environ.get("APPV2_WORKER_LLM_MODEL"),
        "parameters": {
            "temperature": os.environ.get("APPV2_WORKER_LLM_TEMPERATURE"),
            "top_p": os.environ.get("APPV2_WORKER_LLM_TOP_P"),
            "seed": os.environ.get("APPV2_WORKER_LLM_SEED"),
            "response_format": os.environ.get("APPV2_WORKER_LLM_RESPONSE_FORMAT"),
            "max_tokens": os.environ.get("APPV2_WORKER_LLM_MAX_TOKENS"),
        },
        "costs": _costs(provider),
        "proof": proof,
        "prompt_matrix": prompt_matrix,
        "decision_matrix": decision_matrix,
        "raw_model_decision_matrix": [
            {
                "turn": index,
                "kind": decision.get("kind"),
                "reason": decision.get("reason"),
                "tool_name": (decision.get("payload") or {}).get("tool_name"),
                "tool_id": (decision.get("payload") or {}).get("tool_id"),
                "evidence_refs": decision.get("evidence_refs") or [],
            }
            for index, decision in enumerate(provider.raw_decisions if provider is not None else [], start=1)
        ],
        "event_matrix": event_matrix,
        "file_count": len([path for path in repo.rglob("*") if path.is_file()]),
    }


def _prompt_summary_evidence_ref_count(prompt_matrix: list[dict[str, Any]], turn: int) -> int:
    for row in prompt_matrix:
        if row["turn"] == turn:
            return int(row["summary_evidence_ref_count"])
    return 0


def _decision_is_repo_snapshot_tool_call(decision_matrix: list[dict[str, Any]], turn: int) -> bool:
    for row in decision_matrix:
        if row["turn"] != turn:
            continue
        return row["kind"] == "tool_call" and row["tool_id"] == "file_management.repo_snapshot"
    return False


def _prompt_row(*, index: int, prompt: dict[str, Any]) -> dict[str, Any]:
    serialized = json.dumps(prompt, sort_keys=True, default=str)
    messages = prompt.get("messages") if isinstance(prompt.get("messages"), list) else []
    summary_messages = [message for message in messages if isinstance(message, dict) and message.get("name") == "context_summary"]
    summary_evidence_refs = [
        ref
        for message in summary_messages
        for ref in ((message.get("summary") or {}).get("evidence_refs") or [])
        if isinstance(message.get("summary"), dict)
    ]
    world = prompt.get("world") if isinstance(prompt.get("world"), dict) else {}
    world_refs = world.get("world_refs") if isinstance(world, dict) and isinstance(world.get("world_refs"), dict) else {}
    return {
        "turn": index,
        "char_size": len(serialized),
        "message_count": len(messages),
        "raw_marker_visible": RAW_MARKER in serialized,
        "world_ref_count": len(world_refs),
        "context_summary_visible": bool(summary_messages),
        "summary_evidence_ref_count": len(summary_evidence_refs),
        "selected_tools": (prompt.get("selection") or {}).get("selected_tools", []),
    }


def _event_row(*, index: int, event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return {
        "index": index,
        "event_type": event.get("event_type"),
        "decision_kind": payload.get("kind"),
        "tool_id": payload.get("tool_id"),
        "reason": payload.get("reason"),
        "summary_evidence_refs": payload.get("evidence_refs") if event.get("event_type") == "ContextSummaryUpdated" else None,
    }


def _costs(provider: RecordingProvider | None) -> dict[str, Any]:
    candidates = [
        ("provider.delegate.delegate.client.usage_snapshot", getattr(getattr(getattr(provider, "delegate", None), "delegate", None), "client", None)),
        ("provider.delegate.client.usage_snapshot", getattr(getattr(provider, "delegate", None), "client", None)),
        ("provider.delegate.usage_snapshot", getattr(provider, "delegate", None)),
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
    return {"available": False, "source": None, "model_calls": None, "total_tokens": None, "cost": None}


if __name__ == "__main__":
    raise SystemExit(main())
