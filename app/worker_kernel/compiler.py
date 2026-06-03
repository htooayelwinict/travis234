"""Compiles plan steps into worker tasks."""

from __future__ import annotations

from pydantic import ValidationError

from app.schemas import (
    ArtifactPayload,
    Envelope,
    MutationScope,
    PermissionSet,
    Plan,
    PlanStep,
    Task,
    resolve_mutation_scope_proposal,
)


class MissingInputArtifacts(Exception):
    """Raised when a plan step references artifacts the kernel does not have."""

    def __init__(self, *, step_id: str, missing_artifacts: list[str]) -> None:
        self.step_id = step_id
        self.missing_artifacts = missing_artifacts
        super().__init__(
            f"step {step_id} is missing required input artifacts: {', '.join(missing_artifacts)}"
        )


class InvalidWriteScope(Exception):
    """Raised when a mutation task cannot be given a safe write scope."""

    def __init__(self, *, step_id: str, message: str, metadata: dict[str, object] | None = None) -> None:
        self.step_id = step_id
        self.metadata = metadata or {}
        super().__init__(message)


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

        permissions = step.permissions
        if step.permissions.write_files:
            permissions, write_scope = self._resolve_write_scope(
                step=step,
                input_artifacts=input_artifacts,
            )
            task_metadata["write_scope"] = write_scope.model_dump(mode="json")

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
            permissions=permissions,
            metadata=task_metadata,
        )

    def _resolve_write_scope(
        self,
        *,
        step: PlanStep,
        input_artifacts: list[ArtifactPayload],
    ) -> tuple[PermissionSet, MutationScope]:
        source_artifact_ids: list[str] = []
        explicit_paths = list(step.permissions.write_paths)
        artifact_lookup = {artifact.id: artifact for artifact in input_artifacts}

        scopes: list[MutationScope] = []
        for artifact_id in step.permissions.write_paths_from_artifacts:
            artifact = artifact_lookup.get(artifact_id)
            if artifact is None:
                continue
            try:
                scope = resolve_mutation_scope_proposal(artifact.content, source_artifact_id=artifact_id)
            except ValidationError as exc:
                validation_errors = _json_safe_validation_errors(exc)
                raise InvalidWriteScope(
                    step_id=step.step_id,
                    message=f"invalid mutation write scope artifact {artifact_id}: {validation_errors[0]['msg']}",
                    metadata={"artifact_id": artifact_id, "validation_errors": validation_errors},
                ) from exc
            scopes.append(scope)
            source_artifact_ids.append(artifact_id)

        try:
            merged_scope = self._merge_write_scopes(
                explicit_paths=explicit_paths,
                scopes=scopes,
                source_artifact_ids=source_artifact_ids,
            )
        except ValidationError as exc:
            validation_errors = _json_safe_validation_errors(exc)
            raise InvalidWriteScope(
                step_id=step.step_id,
                message=f"invalid mutation write scope for step {step.step_id}: {validation_errors[0]['msg']}",
                metadata={"validation_errors": validation_errors, "source_artifact_ids": source_artifact_ids},
            ) from exc

        final_paths = merged_scope.write_scope_paths
        permissions = PermissionSet.model_validate(
            {
                **step.permissions.as_dict(),
                "write_paths": final_paths,
                "write_paths_from_artifacts": [],
            }
        )
        return permissions, merged_scope

    def _merge_write_scopes(
        self,
        *,
        explicit_paths: list[str],
        scopes: list[MutationScope],
        source_artifact_ids: list[str],
    ) -> MutationScope:
        target_paths = list(explicit_paths)
        test_paths: list[str] = []
        forbidden_paths: list[str] = []
        forbidden_globs: list[str] = []
        reasons: list[str] = []
        max_files = len(explicit_paths)
        for scope in scopes:
            target_paths.extend(scope.target_paths)
            test_paths.extend(scope.test_paths)
            forbidden_paths.extend(scope.forbidden_paths)
            forbidden_globs.extend(scope.forbidden_globs)
            if scope.reason:
                reasons.append(scope.reason)
            max_files += scope.max_files

        max_files = max(1, max_files, len(set(target_paths)))

        return MutationScope.model_validate(
            {
                "target_paths": target_paths,
                "test_paths": test_paths,
                "forbidden_paths": forbidden_paths,
                "forbidden_globs": forbidden_globs,
                "reason": "; ".join(reasons) or "derived from explicit write paths",
                "max_files": max_files,
                "metadata": {
                    "resolver": "mutation_scope_proposal_v1",
                    "source_artifact_ids": source_artifact_ids,
                },
            }
        )


def _json_safe_validation_errors(exc: ValidationError) -> list[dict[str, object]]:
    try:
        return exc.errors(include_context=False)
    except TypeError:
        errors = exc.errors()
    safe_errors: list[dict[str, object]] = []
    for error in errors:
        safe = dict(error)
        safe.pop("ctx", None)
        safe_errors.append(safe)
    return safe_errors
