from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from app.decompressor.runtime import DecompressorRuntime
from app.planner.runtime import PlannerRuntime
from app.schemas import Result, Task
from app.worker_kernel.runtime import WorkerKernelRuntime

PROMPT = (
    "Run a release-readiness and reliability audit for a subscription billing platform rollout: "
    "review system-design.md and the repository implementation for invoice generation, tax calculation, "
    "payment retries with deduplication, and dead-letter recovery. "
    "Then compare repository behavior with authoritative industry guidance on idempotency, retry backoff, "
    "auditability, and ordering guarantees. If defects are concrete, propose minimal scoped changes, "
    "a rollback path, and targeted verification plus post-change operational checks."
)


class PlanAwareMockWorker:
    def __init__(self, worker_type: str, trigger_step_id: str | None, run_log: list[dict[str, Any]]) -> None:
        self.worker_type = worker_type
        self.trigger_step_id = trigger_step_id
        self.run_log = run_log
        self._replan_emitted = False

    def _input_ids(self, task: Task) -> list[str]:
        output: list[str] = []
        for artifact in task.input_artifacts:
            artifact_id = artifact.get("id") or artifact.get("artifact_id")
            if artifact_id:
                output.append(str(artifact_id))
        return output

    def _log(self, task: Task, result: Result) -> None:
        self.run_log.append(
            {
                "worker_type": task.worker_type,
                "step_id": task.step_id,
                "status": result.status,
                "phase": task.metadata.get("phase"),
                "mode": task.metadata.get("mode"),
                "task_id": task.metadata.get("task_id"),
                "input_artifacts": self._input_ids(task),
                "expected_outputs": list(task.expected_outputs),
                "produced_artifacts": [artifact.get("id") for artifact in result.artifacts],
                "summary": result.summary,
                "recommended_action": result.metadata.get("recommended_action"),
                "metadata": result.metadata,
            }
        )

    def run(self, task: Task) -> Result:
        step_id = task.step_id

        if step_id == self.trigger_step_id and not self._replan_emitted:
            self._replan_emitted = True
            issue_snapshot = {
                "failed_step_id": step_id,
                "signal_type": "planner_level",
                "input_artifact_ids": self._input_ids(task),
                "issue_class": "planner_level",
                "signals": ["verification_contract_mismatch", "evidence_gap", "scope_ambiguity"],
                "task_metadata": dict(task.metadata),
                "permissions": dict(task.permissions),
                "instruction_excerpt": task.instruction[:1200],
            }
            result = Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="needs_replan",
                summary=(
                    "Planner-level issue: verification criteria and mutation outputs are misaligned; "
                    "re-planning is required before final verification."
                ),
                artifacts=[
                    {
                        "id": "planner_issue_snapshot",
                        "producer_step_id": step_id,
                        "worker_type": self.worker_type,
                        "content": issue_snapshot,
                    }
                ],
                usage={"tool_calls": 1, "model_calls": 0},
                metadata={
                    "mocked": True,
                    "replan_level": "planner",
                    "issue_class": "planner_level",
                    "recommended_action": (
                        "Return a full fixed plan and re-run from a verification-safe sequence."
                    ),
                },
            )
            self._log(task, result)
            return result

        artifacts = [
            {
                "id": output_id,
                "producer_step_id": step_id,
                "worker_type": self.worker_type,
                "content": {
                    "output_id": output_id,
                    "step_id": step_id,
                    "derived_inputs": self._input_ids(task),
                    "worker_type": self.worker_type,
                    "phase": task.metadata.get("phase"),
                    "mode": task.metadata.get("mode"),
                },
            }
            for output_id in task.expected_outputs
        ]

        result = Result(
            run_id=task.run_id,
            producer=self.worker_type,
            status="completed",
            summary=f"Mock {self.worker_type} completed plan outputs for {step_id}.",
            artifacts=artifacts,
            usage={
                "tool_calls": max(0, min(task.max_tool_calls, 2)),
                "model_calls": max(0, min(task.max_model_calls, 1)),
            },
            metadata={
                "mocked": True,
                "phase": task.metadata.get("phase"),
                "mode": task.metadata.get("mode"),
                "task_id": task.metadata.get("task_id"),
                "input_artifact_ids": self._input_ids(task),
            },
        )
        self._log(task, result)
        return result


class MockWorkerRegistry:
    def __init__(self, trigger_step_id: str | None) -> None:
        self.run_log: list[dict[str, Any]] = []
        self._workers: dict[str, PlanAwareMockWorker] = {
            "direct_worker": PlanAwareMockWorker("direct_worker", trigger_step_id, self.run_log),
            "repo_worker": PlanAwareMockWorker("repo_worker", trigger_step_id, self.run_log),
            "code_worker": PlanAwareMockWorker("code_worker", trigger_step_id, self.run_log),
            "research_worker": PlanAwareMockWorker("research_worker", trigger_step_id, self.run_log),
            "web_research_worker": PlanAwareMockWorker("web_research_worker", trigger_step_id, self.run_log),
            "infra_worker": PlanAwareMockWorker("infra_worker", trigger_step_id, self.run_log),
            "verify_worker": PlanAwareMockWorker("verify_worker", trigger_step_id, self.run_log),
        }

    def get(self, worker_type: str):
        if worker_type not in self._workers:
            raise ValueError(f"Unknown worker_type: {worker_type}")
        return self._workers[worker_type]


def pick_replan_step(plan) -> str | None:
    # Force replan on first verify stage step, otherwise mutate stage, otherwise second step.
    for step in plan.steps:
        if (step.phase or "").upper() == "VERIFY" or step.worker_type == "verify_worker":
            return step.step_id
    for step in plan.steps:
        if (step.phase or "").upper() == "MUTATE" or step.worker_type == "code_worker":
            return step.step_id
    if len(plan.steps) > 1:
        return plan.steps[1].step_id
    if plan.steps:
        return plan.steps[0].step_id
    return None


def main() -> None:
    overall_start = perf_counter()
    start = datetime.now(UTC)

    print(f"PROMPT={PROMPT}", flush=True)
    print(f"START={start.isoformat()}", flush=True)

    decompressor_start = perf_counter()
    decompressor = DecompressorRuntime.from_env('.env')
    print("DECOMPRESSOR_INIT_OK", flush=True)
    envelope = decompressor.run(PROMPT)
    print(
        f"DECOMPRESSOR_DONE req={envelope.request_id} elapsed_ms={(perf_counter()-decompressor_start)*1000:.2f}",
        flush=True,
    )

    planner_start = perf_counter()
    planner = PlannerRuntime.from_env('.env', fallback_on_error=False)
    print("PLANNER_RUNTIME_INIT_OK", flush=True)
    initial_plan = planner.run(envelope)
    print(
        f"PLANNER_DONE plan_id={initial_plan.plan_id} steps={len(initial_plan.steps)} elapsed_ms={(perf_counter()-planner_start)*1000:.2f}",
        flush=True,
    )

    replan_step = pick_replan_step(initial_plan)
    registry = MockWorkerRegistry(trigger_step_id=replan_step)
    worker_kernel = WorkerKernelRuntime(registry=registry, planner_runtime=planner, allow_replan=True)

    worker_start = perf_counter()
    result = worker_kernel.run(initial_plan, envelope=envelope)
    print(
        f"WORKER_KERNEL_DONE status={result.status} elapsed_ms={(perf_counter()-worker_start)*1000:.2f} artifact_count={len(result.artifacts)}",
        flush=True,
    )

    replan_payload = None
    replanned_plan = None
    if isinstance(result.metadata, dict):
        replan_block = result.metadata.get("replan")
        if isinstance(replan_block, dict):
            replan_payload = replan_block.get("request")
            replanned_plan = replan_block.get("replacement_plan")

    worker_status_counts = Counter(entry["status"] for entry in registry.run_log)

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "workflow": "decompressor -> planner -> worker mock -> worker replan request -> planner replan -> rerun",
        "elapsed_ms": round((perf_counter() - overall_start) * 1000, 3),
        "prompt": PROMPT,
        "plan_intent": "complex_scenario_billing_reliability_audit",
        "selected_replan_step": replan_step,
        "decompressor": {
            "request_id": envelope.request_id,
            "envelope": envelope.model_dump(mode="json"),
        },
        "planner": {
            "initial_plan": initial_plan.model_dump(mode="json"),
            "planner_runtime_mode": (initial_plan.metadata.get("planner_runtime") or {}).get("mode"),
        },
        "worker_mock_runs": registry.run_log,
        "worker_status_counts": dict(worker_status_counts),
        "replan": {
            "requested": replan_payload is not None,
            "request": replan_payload,
            "replanned_plan": replanned_plan,
            "replan_depth": (result.metadata.get("replan", {}).get("depth") if isinstance(result.metadata, dict) else None),
        },
        "worker_kernel": {
            "run_status": result.status,
            "run_summary": result.summary,
            "metadata": result.metadata,
            "usage": result.usage,
            "errors": result.errors,
            "warnings": result.warnings,
        },
        "final_worker_kernel_result": result.model_dump(mode="json"),
    }

    out_path = Path("plan") / f"finale-qa-test-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    print(f"OUTPUT_PATH={out_path}")
    print(
        json.dumps(
            {
                "result_status": result.status,
                "replan_requested": payload["replan"]["requested"],
                "replanned_plan_id": (replanned_plan or {}).get("plan_id") if isinstance(replanned_plan, dict) else None,
                "selected_replan_step": replan_step,
                "worker_calls": len(registry.run_log),
                "status_counts": dict(worker_status_counts),
            },
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
