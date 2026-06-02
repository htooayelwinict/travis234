import pytest

from app.schemas import Envelope, Plan, PlanStep, ReplanRequest, Result, Task
from app.worker_kernel.registry import WorkerRegistry, build_default_registry
from app.worker_kernel.runtime import WorkerKernelRuntime


def _envelope() -> Envelope:
    return Envelope(
        request_id="req_replan",
        raw_input="research and fix code",
        normalized_input="Research the issue and apply a scoped code fix.",
        user_goal="Fix the code after evidence-based research.",
        input_type="research_backed_code_fix",
        intents=["research.lookup", "code.fix"],
        domains=["code", "research"],
        risks=["mutation_requested", "needs_verification"],
        artifacts=[{"name": "target", "type": "code"}],
        context_needed=["target_file"],
        constraints=["mutation_requires_verification"],
        complexity_hint="high",
        confidence=0.8,
    )


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


def test_worker_kernel_returns_needs_replan_without_planner_runtime() -> None:
    class ReplanWorker:
        worker_type = "mock_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="needs_replan",
                summary="missing evidence for mutation",
                artifacts=[{"id": "partial_evidence", "content": "insufficient"}],
                usage={"tool_calls": 1, "model_calls": 0},
                metadata={"recommended_action": "ask planner for a fresh evidence-first plan"},
            )

    registry = WorkerRegistry()
    registry.register(ReplanWorker())
    plan = Plan(
        plan_id="plan_req_replan",
        request_id="req_replan",
        planner="llm_planner",
        objective="Research and fix code",
        strategy="research_then_fix",
        steps=[
            PlanStep(
                step_id="research_step",
                worker_type="mock_worker",
                instruction="research evidence",
                output_artifacts=["partial_evidence"],
                max_tool_calls=2,
                max_model_calls=1,
            )
        ],
        budget={"max_tool_calls": 2, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    assert result.status == "needs_replan"
    assert result.metadata["replan_request"]["failed_step_id"] == "research_step"
    assert result.metadata["replan_request"]["completed_step_ids"] == []
    assert result.metadata["replan_request"]["recommended_action"] == "ask planner for a fresh evidence-first plan"


def test_worker_kernel_replan_request_tracks_completed_steps_without_artifacts() -> None:
    class CompletedWorker:
        worker_type = "completed_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="completed without artifacts",
                usage={"tool_calls": 0, "model_calls": 0},
            )

    class ReplanWorker:
        worker_type = "mock_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="needs_replan",
                summary="planner scope does not match discovered repo",
                usage={"tool_calls": 1, "model_calls": 0},
            )

    registry = WorkerRegistry()
    registry.register(CompletedWorker())
    registry.register(ReplanWorker())
    plan = Plan(
        plan_id="plan_req_replan",
        request_id="req_replan",
        planner="llm_planner",
        objective="Research and fix code",
        strategy="research_then_fix",
        steps=[
            PlanStep(
                step_id="discover_step",
                worker_type="completed_worker",
                instruction="discover context",
                output_artifacts=[],
                max_tool_calls=0,
                max_model_calls=0,
            ),
            PlanStep(
                step_id="research_step",
                worker_type="mock_worker",
                instruction="research evidence",
                output_artifacts=["partial_evidence"],
                max_tool_calls=2,
                max_model_calls=1,
            ),
        ],
        budget={"max_tool_calls": 2, "max_model_calls": 1, "max_workers": 2, "max_retries": 0},
    )

    result = WorkerKernelRuntime(registry=registry).run(plan)

    replan_request = result.metadata["replan_request"]
    assert result.status == "needs_replan"
    assert replan_request["completed_step_ids"] == ["discover_step"]
    assert replan_request["failed_step_id"] == "research_step"
    assert "research_step" not in replan_request["completed_step_ids"]


def test_worker_kernel_replans_with_fixed_new_plan() -> None:
    class DiscoverWorker:
        worker_type = "repo_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="discovered payout workflow and candidate files",
                artifacts=[
                    {
                        "id": "repo_inventory",
                        "content": {
                            "services": ["orchestrator", "webhook", "ledger"],
                            "candidate_paths": [
                                "app/worker_kernel/runtime.py",
                                "app/worker_kernel/dispatcher.py",
                            ],
                        },
                    }
                ],
                usage={"tool_calls": 1, "model_calls": 0},
            )

    class ResearchWorker:
        worker_type = "web_research_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="mapped authoritative guidance to control points",
                artifacts=[
                    {
                        "id": "guidance_control_matrix",
                        "content": {
                            "controls": ["idempotency", "retry_backoff", "deduplication"],
                            "sources": [
                                "https://example.org/idempotency",
                                "https://example.org/retry-backoff",
                            ],
                        },
                    }
                ],
                usage={"tool_calls": 1, "model_calls": 0},
            )

    class DesignWorker:
        worker_type = "research_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="defined scoped mutation and verification",
                artifacts=[
                    {
                        "id": "fix_design",
                        "content": {
                            "change": "tighten retry jitter bounds",
                            "rationale": "prevent retry burst on webhook timeout",
                        },
                    },
                    {
                        "id": "mutation_scope",
                        "content": {
                            "paths": ["app/worker_kernel/runtime.py"],
                            "line_hints": ["retry schedule branch"],
                        },
                    },
                    {
                        "id": "verification_plan",
                        "content": {
                            "checks": [
                                "idempotency invariant",
                                "retry backoff monotonicity",
                            ]
                        },
                    },
                ],
                usage={"tool_calls": 0, "model_calls": 1},
            )

    class MutateWorker:
        worker_type = "code_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="needs_replan",
                summary="mutation scope conflicts with new evidence from runtime path mapping",
                artifacts=[
                    {
                        "id": "planner_issue_snapshot",
                        "content": {
                            "issue_class": "planner_level",
                            "signal_type": "planner_level",
                            "signals": ["artifact_chain_gap", "scope_ambiguity"],
                            "failed_step_id": "mutate_step",
                            "input_artifact_ids": [
                                "fix_design",
                                "mutation_scope",
                                "verification_plan",
                            ],
                        },
                    }
                ],
                usage={"tool_calls": 1, "model_calls": 0},
                metadata={"recommended_action": "return a full fixed plan with a safer mutation boundary"},
            )

    class ReplacementWorker:
        worker_type = "direct_worker"

        def run(self, task: Task) -> Result:
            return Result(
                run_id=task.run_id,
                producer=self.worker_type,
                status="completed",
                summary="replacement plan completed",
                artifacts=[{"id": "final_report", "content": "fixed replacement plan result"}],
                usage={"tool_calls": 0, "model_calls": 1},
            )

    class FakePlannerRuntime:
        last_replan_request: ReplanRequest | None = None

        def replan(self, envelope: Envelope, current_plan: Plan, replan_request: ReplanRequest) -> Plan:
            type(self).last_replan_request = replan_request
            return Plan(
                plan_id="plan_req_replan_fixed",
                request_id=envelope.request_id,
                planner="llm_planner_replan",
                objective=current_plan.objective,
                strategy="fixed_new_plan",
                execution_pattern="finalize",
                global_invariants=["replacement_plan_uses_existing_schema"],
                steps=[
                    PlanStep(
                        step_id="finalize_fixed_plan",
                        worker_type="direct_worker",
                        phase="FINALIZE",
                        mode="summarize_only",
                        task_id="replan_recovery",
                        instruction="Known facts: A replacement plan was requested. Unknowns: none. Do now: finalize. Do not do: do not mutate. Output: final_report.",
                        output_artifacts=["final_report"],
                        max_tool_calls=0,
                        max_model_calls=1,
                        permissions={
                            "read_files": False,
                            "write_files": False,
                            "run_commands": False,
                            "web_research": False,
                        },
                    )
                ],
                budget={"max_tool_calls": 0, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
            )

    registry = WorkerRegistry()
    registry.register(DiscoverWorker())
    registry.register(ResearchWorker())
    registry.register(DesignWorker())
    registry.register(MutateWorker())
    registry.register(ReplacementWorker())
    initial_plan = Plan(
        plan_id="plan_req_replan",
        request_id="req_replan",
        planner="llm_planner",
        objective="Research and fix retry behavior",
        strategy="discover_research_design_mutate",
        steps=[
            PlanStep(
                step_id="discover_step",
                worker_type="repo_worker",
                instruction="discover context and candidate paths",
                output_artifacts=["repo_inventory"],
                max_tool_calls=2,
                max_model_calls=1,
            ),
            PlanStep(
                step_id="research_step",
                worker_type="web_research_worker",
                instruction="collect cited guidance for retry/idempotency",
                input_artifacts=["repo_inventory"],
                output_artifacts=["guidance_control_matrix"],
                max_tool_calls=2,
                max_model_calls=1,
            ),
            PlanStep(
                step_id="design_step",
                worker_type="research_worker",
                instruction="define fix design, mutation scope, and verification plan",
                input_artifacts=["repo_inventory", "guidance_control_matrix"],
                output_artifacts=["fix_design", "mutation_scope", "verification_plan"],
                max_tool_calls=1,
                max_model_calls=1,
            ),
            PlanStep(
                step_id="mutate_step",
                worker_type="code_worker",
                instruction="apply scoped mutation",
                input_artifacts=["fix_design", "mutation_scope", "verification_plan"],
                output_artifacts=["change_summary"],
                max_tool_calls=2,
                max_model_calls=0,
            ),
        ],
        budget={"max_tool_calls": 7, "max_model_calls": 3, "max_workers": 4, "max_retries": 0},
    )

    result = WorkerKernelRuntime(
        registry=registry,
        planner_runtime=FakePlannerRuntime(),
    ).run(initial_plan, envelope=_envelope())

    assert result.status == "completed"
    assert FakePlannerRuntime.last_replan_request is not None
    assert FakePlannerRuntime.last_replan_request.failed_step_id == "mutate_step"
    assert FakePlannerRuntime.last_replan_request.completed_step_ids == [
        "discover_step",
        "research_step",
        "design_step",
    ]
    assert FakePlannerRuntime.last_replan_request.recommended_action == (
        "return a full fixed plan with a safer mutation boundary"
    )
    assert any(
        a.get("id") == "mutation_scope"
        for a in FakePlannerRuntime.last_replan_request.completed_artifacts
    )
    assert result.metadata["replan"]["replacement_plan"]["plan_id"] == "plan_req_replan_fixed"
    assert result.artifacts[0]["id"] == "final_report"


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
