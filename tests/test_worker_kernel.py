import pytest

from app.schemas import Plan, PlanStep, Result, Task
from app.worker_kernel.registry import WorkerRegistry, build_default_registry
from app.worker_kernel.runtime import WorkerKernelRuntime


def test_worker_kernel_direct_plan_executes() -> None:
    plan = Plan(
        plan_id="plan_req_direct",
        request_id="req_direct",
        planner="direct",
        objective="Answer a question",
        strategy="direct_answer",
        steps=[
            PlanStep(
                step_id="step-direct",
                worker_type="direct_worker",
                instruction="Answer directly",
                output_artifacts=["direct_answer"],
                max_tool_calls=0,
                max_model_calls=1,
                permissions={"read_files": False, "write_files": False, "run_commands": False},
            )
        ],
        budget={"max_tool_calls": 0, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
    )

    result = WorkerKernelRuntime().run(plan)

    assert result.status == "completed"
    assert result.errors == []
    assert any((a.get("id") or a.get("artifact_id")) == "direct_answer" for a in result.artifacts)
    assert result.usage.get("model_calls", 0) >= 0


def test_worker_kernel_code_flow_executes() -> None:
    plan = Plan(
        plan_id="plan_req_code",
        request_id="req_code",
        planner="code",
        objective="Fix code",
        strategy="observe_then_patch",
        steps=[
            PlanStep(
                step_id="observe_target",
                worker_type="repo_worker",
                instruction="Inspect target",
                output_artifacts=["target_observation"],
                max_tool_calls=4,
                max_model_calls=1,
                permissions={"read_files": True, "write_files": False, "run_commands": False},
            ),
            PlanStep(
                step_id="patch_target",
                worker_type="code_worker",
                instruction="Apply patch",
                input_artifacts=["target_observation"],
                output_artifacts=["patch_result"],
                max_tool_calls=6,
                max_model_calls=1,
                permissions={"read_files": True, "write_files": True, "run_commands": False},
            ),
            PlanStep(
                step_id="verify_patch",
                worker_type="verify_worker",
                instruction="Verify patch",
                input_artifacts=["patch_result"],
                output_artifacts=["verification_result"],
                max_tool_calls=3,
                max_model_calls=0,
                permissions={"read_files": True, "write_files": False, "run_commands": True},
            ),
        ],
        budget={"max_tool_calls": 13, "max_model_calls": 3, "max_workers": 3, "max_retries": 0},
    )

    result = WorkerKernelRuntime().run(plan)

    assert result.status == "completed"
    artifact_ids = {a.get("id") or a.get("artifact_id") for a in result.artifacts}
    assert "patch_result" in artifact_ids
    assert "verification_result" in artifact_ids


def test_worker_kernel_web_research_flow_executes() -> None:
    plan = Plan(
        plan_id="plan_req_web_research",
        request_id="req_web_research",
        planner="research",
        objective="Compare external algorithm references",
        strategy="web_research_then_summarize",
        steps=[
            PlanStep(
                step_id="research_external_sources",
                worker_type="web_research_worker",
                phase="RESEARCH",
                mode="observe_only",
                task_id="external_research",
                instruction="Collect comparable algorithm references and summarize differences.",
                output_artifacts=["web_research_notes"],
                max_tool_calls=4,
                max_model_calls=1,
                permissions={"read_files": False, "write_files": False, "run_commands": True},
            )
        ],
        budget={"max_tool_calls": 4, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
        execution_pattern="research_finalize",
        global_invariants=["no_file_writes_for_web_research"],
    )

    result = WorkerKernelRuntime().run(plan)

    assert result.status == "completed"
    artifact_ids = {a.get("id") or a.get("artifact_id") for a in result.artifacts}
    assert "web_research_notes" in artifact_ids


def test_budget_rejection_before_dispatch() -> None:
    class CountingWorker:
        worker_type = "direct_worker"
        runs = 0

        def run(self, task: Task) -> Result:  # pragma: no cover - must not execute
            type(self).runs += 1
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="unexpected",
                usage={"tool_calls": 0, "model_calls": 0},
            )

    registry = WorkerRegistry()
    registry.register(CountingWorker())

    plan = Plan(
        plan_id="plan_req_overflow",
        request_id="req_overflow",
        planner="direct",
        objective="Overflow budget",
        strategy="direct_answer",
        steps=[
            PlanStep(
                step_id="step-1",
                worker_type="direct_worker",
                instruction="first",
                max_tool_calls=2,
                max_model_calls=1,
            ),
            PlanStep(
                step_id="step-2",
                worker_type="direct_worker",
                instruction="second",
                max_tool_calls=2,
                max_model_calls=1,
            ),
        ],
        budget={"max_tool_calls": 2, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "budget_exceeded"
    assert CountingWorker.runs == 0


def test_budget_rejection_after_overbudget_worker_result() -> None:
    class OverBudgetWorker:
        worker_type = "direct_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="over budget",
                artifacts=[{"id": "direct_answer", "content": "x"}],
                usage={
                    "tool_calls": task.max_tool_calls + 200,
                    "model_calls": task.max_model_calls,
                },
            )

    registry = WorkerRegistry()
    registry.register(OverBudgetWorker())

    plan = Plan(
        plan_id="plan_req_post_budget",
        request_id="req_post_budget",
        planner="direct",
        objective="Trigger post-result budget gate",
        strategy="direct_answer",
        steps=[
            PlanStep(
                step_id="step-over",
                worker_type="direct_worker",
                instruction="answer",
                output_artifacts=["direct_answer"],
                max_tool_calls=1,
                max_model_calls=1,
            )
        ],
        budget={"max_tool_calls": 1, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "budget_exceeded"
    assert result.errors
    assert "budget" in result.summary.lower() or "budget" in result.errors[0].lower()


def test_invalid_plan_handling() -> None:
    empty_plan = Plan(
        plan_id="plan_req_invalid_1",
        request_id="req_invalid_1",
        planner="fallback",
        objective="Invalid",
        strategy="observe_first",
        steps=[],
        budget={"max_tool_calls": 3, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
    )

    with pytest.raises(ValueError, match="at least one"):
        WorkerKernelRuntime().run(empty_plan)

    malformed_budget_plan = Plan(
        plan_id="plan_req_invalid_2",
        request_id="req_invalid_2",
        planner="fallback",
        objective="Invalid",
        strategy="observe_first",
        steps=[
            PlanStep(
                step_id="bad-step",
                worker_type="direct_worker",
                instruction="invalid",
                max_tool_calls=-1,
                max_model_calls=0,
            )
        ],
        budget={"max_tool_calls": 1, "max_model_calls": 0, "max_workers": 1, "max_retries": 0},
    )

    with pytest.raises(ValueError, match="max_tool_calls"):
        WorkerKernelRuntime().run(malformed_budget_plan)


def test_unknown_worker_handling() -> None:
    plan = Plan(
        plan_id="plan_req_unknown",
        request_id="req_unknown",
        planner="fallback",
        objective="Unknown worker",
        strategy="observe_first",
        steps=[
            PlanStep(
                step_id="step-unknown",
                worker_type="unknown_worker",
                instruction="do unknown thing",
                max_tool_calls=1,
                max_model_calls=1,
            )
        ],
        budget={"max_tool_calls": 2, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
    )

    with pytest.raises(ValueError, match="Unknown worker_type"):
        WorkerKernelRuntime(registry=build_default_registry()).run(plan)


def test_task_compiler_propagates_phase_mode_task_id_metadata() -> None:
    class MetadataCaptureWorker:
        worker_type = "direct_worker"
        last_metadata: dict | None = None

        def run(self, task: Task) -> Result:
            type(self).last_metadata = task.metadata
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="metadata captured",
                artifacts=[{"id": "direct_answer", "content": "ok"}],
                usage={"tool_calls": 0, "model_calls": 0},
            )

    registry = WorkerRegistry()
    registry.register(MetadataCaptureWorker())

    plan = Plan(
        plan_id="plan_req_phase_meta",
        request_id="req_phase_meta",
        planner="llm_planner",
        objective="Capture phase metadata",
        strategy="phase_metadata",
        execution_pattern="discover",
        global_invariants=["observe_before_mutate"],
        steps=[
            PlanStep(
                step_id="discover_scope",
                worker_type="direct_worker",
                phase="DISCOVER",
                mode="observe_only",
                task_id="task_a",
                instruction="collect scope",
                output_artifacts=["direct_answer"],
                max_tool_calls=0,
                max_model_calls=0,
                permissions={"read_files": True, "write_files": False, "run_commands": False},
            )
        ],
        budget={"max_tool_calls": 0, "max_model_calls": 0, "max_workers": 1, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "completed"
    assert MetadataCaptureWorker.last_metadata == {
        "phase": "DISCOVER",
        "mode": "observe_only",
        "task_id": "task_a",
    }
