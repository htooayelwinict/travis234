"""Compiles plan steps into worker tasks."""

from __future__ import annotations

from app.schemas import ArtifactPayload, Envelope, Plan, PlanStep, Task


class MissingInputArtifacts(Exception):
    """Raised when a plan step references artifacts the kernel does not have."""

    def __init__(self, *, step_id: str, missing_artifacts: list[str]) -> None:
        self.step_id = step_id
        self.missing_artifacts = missing_artifacts
        super().__init__(
            f"step {step_id} is missing required input artifacts: {', '.join(missing_artifacts)}"
        )


class TaskCompiler:
    def compile(
        self,
        run_id: str,
        step: PlanStep,
        artifact_store: dict[str, ArtifactPayload],
        *,
        plan: Plan | None = None,
        envelope: Envelope | None = None,
    ) -> Task:
        input_artifacts: list[ArtifactPayload] = []
        missing_artifacts: list[str] = []
        for artifact_id in step.input_artifacts:
            if artifact_id in artifact_store:
                input_artifacts.append(artifact_store[artifact_id])
            else:
                missing_artifacts.append(artifact_id)

        if missing_artifacts:
            raise MissingInputArtifacts(
                step_id=step.step_id,
                missing_artifacts=missing_artifacts,
            )

        task_metadata: dict[str, object] = {}
        if step.phase is not None:
            task_metadata["phase"] = step.phase
        if step.mode is not None:
            task_metadata["mode"] = step.mode
        if step.task_id is not None:
            task_metadata["task_id"] = step.task_id
        if plan is not None:
            task_metadata.update(
                {
                    "objective": plan.objective,
                    "strategy": plan.strategy,
                    "execution_pattern": plan.execution_pattern,
                    "global_invariants": plan.global_invariants,
                    "success_criteria": plan.success_criteria,
                    "plan_id": plan.plan_id,
                    "request_id": plan.request_id,
                }
            )
        if envelope is not None:
            task_metadata.update(
                {
                    "normalized_input": envelope.normalized_input,
                    "user_goal": envelope.user_goal,
                    "input_type": envelope.input_type,
                    "domains": envelope.domains,
                    "risks": envelope.risks,
                    "constraints": envelope.constraints,
                    "context_needed": envelope.context_needed,
                    "ambiguity": envelope.ambiguity,
                    "assumptions": envelope.assumptions,
                }
            )

        return Task(
            task_id=f"task_{step.step_id}",
            run_id=run_id,
            step_id=step.step_id,
            worker_type=step.worker_type,
            instruction=step.instruction,
            input_artifacts=input_artifacts,
            expected_outputs=step.output_artifacts,
            max_tool_calls=step.max_tool_calls,
            max_model_calls=step.max_model_calls,
            permissions=step.permissions,
            metadata=task_metadata,
        )
