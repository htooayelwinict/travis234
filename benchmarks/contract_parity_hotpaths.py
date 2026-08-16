#!/usr/bin/env python3
"""Measure contract-parity hot paths and gate optional native candidates."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ADAPTER_ROOT = ROOT / "packages" / "travis234-mcp-adapter"
for import_root in (ROOT, ADAPTER_ROOT):
    value = str(import_root)
    if value not in sys.path:
        sys.path.insert(0, value)

from mcp.types import CallToolResult, TextContent as McpTextContent

from travis.coding_agent.artifact_store import DurableArtifactStore
from travis.coding_agent.language_services.jsonrpc import JsonRpcStdioClient
from travis.coding_agent.memory.store import MemoryStore
from travis.coding_agent.memory.types import MemorySettings
from travis.coding_agent.operations.store import OperationStore
from travis.coding_agent.policy import ToolPolicyEngine, ToolPolicySettings
from travis.coding_agent.subagent_supervision import SupervisorSnapshotStore
from travis.coding_agent.tools.types import ToolDefinition
from travis234_mcp_adapter.output_guard import SpillRegistry
from travis234_mcp_adapter.results import convert_call_result


DECISION_RETAIN_PYTHON = "retain_python"
DECISION_CANDIDATE_REJECTED = "candidate_rejected"
DECISION_PACKAGING_REVIEW = "candidate_requires_packaging_review"

MINIMUM_SPEEDUP = 2.0
MINIMUM_WALL_SHARE = 0.05
MAXIMUM_COEFFICIENT_OF_VARIATION = 0.15
DEFAULT_SEED = 234

_OPTIONAL_NATIVE_MODULES = (
    "msgspec",
    "numpy",
    "orjson",
    "rapidjson",
    "xxhash",
)
_WORK_UNITS = {
    "artifact_verification": 1,
    "policy_decision": 256,
    "lsp_frame_parsing": 32,
    "supervisor_snapshot": 128,
    "operation_journal_write": 8,
    "memory_recall": 64,
    "mcp_result_conversion": 32,
}


def _valid_sample(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and float(value) > 0
    )


def summarize_samples(samples: Sequence[float]) -> dict[str, object]:
    """Return recomputed timing statistics for finite positive samples."""

    values = list(samples)
    if not values or any(not _valid_sample(value) for value in values):
        raise ValueError("timing samples must be finite positive numbers")
    normalized = [float(value) for value in values]
    mean = statistics.fmean(normalized)
    deviation = statistics.pstdev(normalized) if len(normalized) > 1 else 0.0
    return {
        "samplesSeconds": normalized,
        "medianSeconds": statistics.median(normalized),
        "meanSeconds": mean,
        "coefficientOfVariation": deviation / mean,
    }


def measure_case(
    name: str,
    case: Callable[[], object],
    *,
    rounds: int,
    warmups: int,
    clock: Callable[[], float] = time.perf_counter,
) -> dict[str, object]:
    """Warm a case, then measure only the requested rounds."""

    del name
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        raise ValueError("rounds must be a positive integer")
    if isinstance(warmups, bool) or not isinstance(warmups, int) or warmups < 0:
        raise ValueError("warmups must be a non-negative integer")
    for _ in range(warmups):
        case()
    samples: list[float] = []
    for _ in range(rounds):
        started = clock()
        case()
        elapsed = clock() - started
        if not _valid_sample(elapsed):
            raise ValueError("benchmark clock produced an invalid duration")
        samples.append(float(elapsed))
    return {**summarize_samples(samples), "correctnessPassed": True}


def _timing_from(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    samples = value.get("samplesSeconds")
    if not isinstance(samples, Sequence) or isinstance(samples, (str, bytes, bytearray)):
        return None
    try:
        return summarize_samples(samples)
    except (TypeError, ValueError):
        return None


def decide_native_gate(
    baseline: object,
    candidate: object | None,
) -> str:
    """Apply the evidence threshold without importing or selecting a candidate."""

    if candidate is None:
        return DECISION_RETAIN_PYTHON
    if not isinstance(baseline, Mapping) or not isinstance(candidate, Mapping):
        return DECISION_CANDIDATE_REJECTED
    if baseline.get("schemaVersion") != 1 or candidate.get("schemaVersion") != 1:
        return DECISION_CANDIDATE_REJECTED
    target = candidate.get("target")
    hotpaths = baseline.get("hotpaths")
    if not isinstance(target, str) or not isinstance(hotpaths, Mapping):
        return DECISION_CANDIDATE_REJECTED
    baseline_case = hotpaths.get(target)
    if not isinstance(baseline_case, Mapping):
        return DECISION_CANDIDATE_REJECTED
    baseline_timing = _timing_from(baseline_case)
    candidate_timing = _timing_from(candidate)
    if baseline_timing is None or candidate_timing is None:
        return DECISION_CANDIDATE_REJECTED
    wall_share = baseline_case.get("wallShare")
    if not _valid_sample(wall_share):
        return DECISION_CANDIDATE_REJECTED
    if (
        baseline_case.get("correctnessPassed") is not True
        or candidate.get("correctnessPassed") is not True
        or candidate.get("conformancePassed") is not True
    ):
        return DECISION_CANDIDATE_REJECTED
    baseline_cv = float(baseline_timing["coefficientOfVariation"])
    candidate_cv = float(candidate_timing["coefficientOfVariation"])
    baseline_median = float(baseline_timing["medianSeconds"])
    candidate_median = float(candidate_timing["medianSeconds"])
    speedup = baseline_median / candidate_median
    epsilon = 1e-12
    if (
        float(wall_share) + epsilon < MINIMUM_WALL_SHARE
        or speedup + epsilon < MINIMUM_SPEEDUP
        or baseline_cv > MAXIMUM_COEFFICIENT_OF_VARIATION + epsilon
        or candidate_cv > MAXIMUM_COEFFICIENT_OF_VARIATION + epsilon
    ):
        return DECISION_CANDIDATE_REJECTED
    return DECISION_PACKAGING_REVIEW


def build_seeded_inputs(seed: int) -> dict[str, object]:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    generator = random.Random(seed)
    artifact = generator.randbytes(64 * 1024)
    return {
        "artifactPayload": artifact,
        "artifactBytes": len(artifact),
        "digest": hashlib.sha256(artifact).hexdigest(),
    }


@dataclass(frozen=True)
class _SnapshotTask:
    id: str
    role: str
    backend: str


class _BenchmarkCases:
    def __init__(self, root: Path, *, seed: int) -> None:
        inputs = build_seeded_inputs(seed)
        payload = inputs["artifactPayload"]
        assert isinstance(payload, bytes)
        self.input_digest = str(inputs["digest"])

        source = root / "artifact-source.bin"
        source.write_bytes(payload)
        self.artifact_store = DurableArtifactStore(root / "agent")
        self.artifact = self.artifact_store.promote(source)

        tool = ToolDefinition(
            name="benchmark_read",
            label="Benchmark read",
            description="Deterministic policy benchmark",
            parameters={"type": "object"},
            execute=lambda *_args, **_kwargs: None,
            effects=frozenset({"read"}),
        )
        self.policy_engine = ToolPolicyEngine(
            ToolPolicySettings(mode="enforce", auto_allow_effects=frozenset({"read"}))
        )
        self.policy_tool = tool

        self.lsp_frames = tuple(
            (
                f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body,
                index,
            )
            for index in range(1, 33)
            for body in [
                json.dumps(
                    {"jsonrpc": "2.0", "id": index, "result": {"index": index}},
                    separators=(",", ":"),
                ).encode("utf-8")
            ]
        )
        self.lsp_root = root

        self.snapshot_store = SupervisorSnapshotStore(capacity=128)
        for index in range(64):
            self.snapshot_store.publish(
                _SnapshotTask(f"task-{index}", "worker", "internal"),
                "running",
                started_at_ms=index + 1,
            )

        self.operation_store = OperationStore(root / "operations.sqlite3")
        self.runtime_id = "a" * 32
        self.operation_store.open_runtime(self.runtime_id, 1, 1.0, 1)
        self.operation = self.operation_store.create_operation(
            self.runtime_id, "b" * 64, "benchmark", 2
        )
        self.operation_step = 2

        self.memory_store = MemoryStore(
            root / "memory.sqlite3",
            settings=MemorySettings(enabled=True, recall_limit=20),
        )
        self.project_key = "c" * 64
        for index in range(64):
            self.memory_store.retain(
                f"contract parity benchmark fact {index}",
                tags=["benchmark", f"bucket-{index % 4}"],
                scope="project",
                project_key=self.project_key,
                provenance="agent_explicit",
                now_ms=index + 1,
            )

        self.spills = SpillRegistry(root / "spills")
        self.mcp_result = CallToolResult(
            content=[McpTextContent(text=f"result-{index}") for index in range(32)],
            structured_content={"count": 32, "ok": True},
            is_error=False,
        )

    def artifact_verification(self) -> None:
        verified = self.artifact_store.verify(self.artifact.digest)
        assert verified.stat().st_size == self.artifact.byte_size

    def policy_decision(self) -> None:
        for _ in range(256):
            decision = self.policy_engine.evaluate(self.policy_tool, {})
            assert decision.allow and decision.reason_code == "auto_allowed"

    async def _parse_lsp_frames(self) -> None:
        reader = asyncio.StreamReader()
        client = JsonRpcStdioClient("benchmark-unused", cwd=self.lsp_root)
        client._process = SimpleNamespace(stdout=reader, returncode=None)  # type: ignore[assignment]
        loop = asyncio.get_running_loop()
        pending = []
        for frame, request_id in self.lsp_frames:
            future: asyncio.Future[object] = loop.create_future()
            client._pending[request_id] = future
            pending.append((request_id, future))
            reader.feed_data(frame)
        reader.feed_eof()
        await client._reader_loop()
        assert all(future.result() == {"index": request_id} for request_id, future in pending)

    def lsp_frame_parsing(self) -> None:
        asyncio.run(self._parse_lsp_frames())

    def supervisor_snapshot(self) -> None:
        for _ in range(128):
            snapshot = self.snapshot_store.snapshot()
            assert snapshot.active_count == 64 and len(snapshot.tasks) == 64

    def operation_journal_write(self) -> None:
        for _ in range(_WORK_UNITS["operation_journal_write"]):
            self.operation_step += 1
            advanced = self.operation_store.advance(
                self.operation.operation_id,
                "benchmark",
                {"step": self.operation_step},
                self.operation_step,
            )
            assert advanced.program_counter == self.operation_step - 2

    def memory_recall(self) -> None:
        recalled = self.memory_store.recall(
            "contract benchmark",
            project_key=self.project_key,
            now_ms=10_000,
            limit=20,
        )
        assert len(recalled) == 20 and all("benchmark" in fact.content for fact in recalled)

    def mcp_result_conversion(self) -> None:
        converted = convert_call_result(self.mcp_result, self.spills)
        marker = converted.details["travis234Mcp"]
        assert marker["isError"] is False and marker["spilled"] is False
        assert len(converted.content) == 33

    def close(self) -> None:
        self.operation_store.close()
        self.memory_store.close()
        self.spills.cleanup()


def _commit() -> str:
    try:
        value = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
            timeout=2,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return value if len(value) == 40 and all(char in "0123456789abcdef" for char in value) else "unknown"


def _environment() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "platform": sys.platform,
        "machine": platform.machine() or "unknown",
        "commit": _commit(),
    }


def run_benchmarks(
    *,
    rounds: int,
    warmups: int,
    seed: int = DEFAULT_SEED,
    candidate: object | None = None,
    temporary_root: Path | None = None,
) -> dict[str, object]:
    """Run the bounded Python baseline and return a sanitized in-memory report."""

    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 1:
        raise ValueError("rounds must be a positive integer")
    if isinstance(warmups, bool) or not isinstance(warmups, int) or warmups < 0:
        raise ValueError("warmups must be a non-negative integer")
    with tempfile.TemporaryDirectory(
        prefix="travis234-native-gate-",
        dir=str(temporary_root) if temporary_root is not None else None,
    ) as raw_root:
        cases = _BenchmarkCases(Path(raw_root), seed=seed)
        case_functions: dict[str, Callable[[], object]] = {
            "artifact_verification": cases.artifact_verification,
            "policy_decision": cases.policy_decision,
            "lsp_frame_parsing": cases.lsp_frame_parsing,
            "supervisor_snapshot": cases.supervisor_snapshot,
            "operation_journal_write": cases.operation_journal_write,
            "memory_recall": cases.memory_recall,
            "mcp_result_conversion": cases.mcp_result_conversion,
        }
        try:
            hotpaths = {
                name: measure_case(name, case, rounds=rounds, warmups=warmups)
                for name, case in case_functions.items()
            }
            for name, timing in hotpaths.items():
                timing["workUnits"] = _WORK_UNITS[name]

            def mixed_workflow() -> None:
                for case in case_functions.values():
                    case()

            mixed = measure_case(
                "mixed_contract_workflow",
                mixed_workflow,
                rounds=rounds,
                warmups=warmups,
            )
            denominator = float(mixed["medianSeconds"])
            for timing in hotpaths.values():
                timing["wallShare"] = float(timing["medianSeconds"]) / denominator
            report: dict[str, object] = {
                "schemaVersion": 1,
                "seed": seed,
                "rounds": rounds,
                "warmups": warmups,
                "inputDigest": cases.input_digest,
                "environment": _environment(),
                "thresholds": {
                    "minimumSpeedup": MINIMUM_SPEEDUP,
                    "minimumWallShare": MINIMUM_WALL_SHARE,
                    "maximumCoefficientOfVariation": MAXIMUM_COEFFICIENT_OF_VARIATION,
                },
                "hotpaths": hotpaths,
                "mixedWorkflow": mixed,
                "nativeModulesImported": sorted(
                    name for name in _OPTIONAL_NATIVE_MODULES if name in sys.modules
                ),
            }
            report["decision"] = decide_native_gate(report, candidate)
            return report
        finally:
            cases.close()


def _candidate_payload(path: str | None) -> object | None:
    if path is None:
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("candidate timing JSON is unreadable") from error


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=7)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--candidate", help="Path to candidate timing JSON")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_benchmarks(
            rounds=args.rounds,
            warmups=args.warmups,
            seed=args.seed,
            candidate=_candidate_payload(args.candidate),
        )
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False))
    else:
        print(f"Native acceleration decision: {report['decision']}")
        for name, timing in report["hotpaths"].items():
            print(
                f"- {name}: {float(timing['medianSeconds']) * 1000:.3f} ms, "
                f"CV={float(timing['coefficientOfVariation']):.3f}, "
                f"share={float(timing['wallShare']):.3f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
