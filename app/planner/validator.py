"""Deterministic planner output validation."""

from __future__ import annotations

from app.planner.contracts import ALLOWED_MODES, ALLOWED_WORKER_TYPES, PlannerValidationError, WRITE_SCOPE_ARTIFACTS
from app.schemas import Envelope, Plan


DISCOVERY_CONTEXT_SIGNALS = {
    "target_file",
    "repo_tree",
    "scope_clarification",
}
DISCOVERY_CONSTRAINT_SIGNALS = {
    "target_locations_must_be_identified_before_mutation",
    "target_scope_must_be_identified_before_mutation",
}
WRITE_SCOPE_ARTIFACT_SIGNALS = set(WRITE_SCOPE_ARTIFACTS)
MUTATION_REVIEW_ARTIFACTS = {
    "rollback_patch",
    "rollback_artifact",
    "revert_instructions",
    "change_summary",
}
VERIFY_EVIDENCE_ARTIFACT_SIGNALS = {
    "evidence_artifacts",
    "root_cause_evidence",
    "root_cause_hypotheses",
    "analysis_evidence",
}
MUTATION_CONTEXT_ARTIFACT_SIGNALS = {
    "root_cause",
    "evidence",
    "fix_design",
    "patch_design",
    "change_design",
    "analysis_evidence",
}
DESIGN_VERIFICATION_ARTIFACTS = {
    "verification_plan",
    "test_plan",
}
PHASE_ORDER = (
    "DISCOVER",
    "ANALYZE",
    "RESEARCH",
    "DESIGN",
    "MUTATE",
    "VERIFY",
    "FINALIZE",
)
PHASE_INDEX = {phase: index for index, phase in enumerate(PHASE_ORDER)}
PHASE_MODES: dict[str, set[str]] = {
    "DISCOVER": {"observe_only"},
    "ANALYZE": {"observe_only"},
    "RESEARCH": {"observe_only"},
    "DESIGN": {"plan_only"},
    "MUTATE": {"bounded_mutation"},
    "VERIFY": {"verify_only"},
    "FINALIZE": {"summarize_only"},
}


class PlannerPlanValidator:
    """Validates generated plans before worker-kernel execution."""

    _WRITE_CAPABLE_WORKERS = {"code_worker"}

    def validate(self, envelope: Envelope, plan: Plan) -> Plan:
        errors: list[str] = []

        if plan.request_id != envelope.request_id:
            errors.append("plan.request_id must match envelope.request_id")

        if not (plan.plan_id or "").strip():
            errors.append("plan.plan_id must be non-empty")

        if plan.planner in ALLOWED_WORKER_TYPES:
            errors.append("plan.planner must not be a worker_type")

        if not plan.steps:
            errors.append("plan.steps must contain at least one step")

        step_ids = [step.step_id for step in plan.steps]
        if len(set(step_ids)) != len(step_ids):
            errors.append("plan.steps step_id values must be unique")

        phase_present = any(step.phase is not None for step in plan.steps)
        mode_present = any(step.mode is not None for step in plan.steps)
        task_id_present = any(step.task_id is not None for step in plan.steps)
        phase_contract_required = phase_present or bool((plan.execution_pattern or "").strip()) or bool(
            plan.global_invariants
        )
        mode_contract_required = phase_contract_required or mode_present
        task_contract_required = phase_contract_required or task_id_present

        if phase_contract_required and any(step.phase is None for step in plan.steps):
            errors.append("phase-aware plans must populate step.phase for every step")
        if mode_contract_required and any(step.mode is None for step in plan.steps):
            errors.append("phase-aware plans must populate step.mode for every step")
        if task_contract_required and any(step.task_id is None or not step.task_id.strip() for step in plan.steps):
            errors.append("phase-aware plans must populate non-empty step.task_id for every step")

        if phase_contract_required:
            if not (plan.execution_pattern or "").strip():
                errors.append("phase-aware plans must populate plan.execution_pattern")
            if not plan.global_invariants:
                errors.append("phase-aware plans must populate plan.global_invariants")

        for step in plan.steps:
            if step.worker_type not in ALLOWED_WORKER_TYPES:
                errors.append(f"unknown worker_type: {step.worker_type}")
            if step.max_tool_calls < 0:
                errors.append(f"step {step.step_id} max_tool_calls must be non-negative")
            if step.max_model_calls < 0:
                errors.append(f"step {step.step_id} max_model_calls must be non-negative")
            if step.phase is not None and step.phase not in PHASE_INDEX:
                errors.append(f"step {step.step_id} has invalid phase: {step.phase}")
            if step.mode is not None:
                if step.phase is None:
                    errors.append(f"step {step.step_id} has mode but no phase")
                elif not step.mode.strip():
                    errors.append(f"step {step.step_id} mode must be a non-empty string")
                elif step.mode not in ALLOWED_MODES:
                    allowed_modes = ", ".join(ALLOWED_MODES)
                    errors.append(f"step {step.step_id} mode must be one of: {allowed_modes}")
                elif step.phase in PHASE_MODES and step.mode not in PHASE_MODES[step.phase]:
                    allowed_modes = ", ".join(sorted(PHASE_MODES[step.phase]))
                    errors.append(f"step {step.step_id} phase {step.phase} must use mode: {allowed_modes}")
            if step.phase == "FINALIZE" and not step.output_artifacts:
                errors.append(f"step {step.step_id} phase FINALIZE must output a final artifact")
            missing_permission_keys = [
                key for key in ("read_files", "write_files", "run_commands", "web_research") if key not in step.permissions
            ]
            if missing_permission_keys:
                errors.append(
                    f"step {step.step_id} permissions must explicitly include read_files/write_files/run_commands/web_research keys with boolean values; missing keys: {', '.join(missing_permission_keys)}"
                )
            else:
                for key in ("read_files", "write_files", "run_commands", "web_research"):
                    if not isinstance(step.permissions.get(key), bool):
                        errors.append(f"step {step.step_id} permission {key} must be a boolean")

        required_tools = sum(step.max_tool_calls for step in plan.steps)
        required_models = sum(step.max_model_calls for step in plan.steps)
        max_tool_calls = int(plan.budget.get("max_tool_calls", 0) or 0)
        max_model_calls = int(plan.budget.get("max_model_calls", 0) or 0)
        max_workers = int(plan.budget.get("max_workers", 0) or 0)

        if max_tool_calls < required_tools:
            errors.append("plan budget max_tool_calls must cover sum of step max_tool_calls")
        if max_model_calls < required_models:
            errors.append("plan budget max_model_calls must cover sum of step max_model_calls")
        if max_workers < len(plan.steps):
            errors.append("plan budget max_workers must cover step count")

        produced: set[str] = set()
        produced_by: dict[str, int] = {}
        write_step_indexes: list[int] = []
        for index, step in enumerate(plan.steps):
            for artifact_id in step.input_artifacts:
                if artifact_id not in produced:
                    errors.append(
                        f"step {step.step_id} input_artifact '{artifact_id}' is not produced by an earlier step"
                    )

            write_files = bool(step.permissions.get("write_files", False))
            if write_files:
                write_step_indexes.append(index)
                if step.worker_type not in self._WRITE_CAPABLE_WORKERS:
                    errors.append(
                        f"step {step.step_id} requests write_files but worker_type {step.worker_type} is not write-capable"
                    )
                errors.extend(
                    self._write_scope_errors(
                        step_id=step.step_id,
                        permissions=step.permissions,
                        produced=produced,
                        produced_by=produced_by,
                        steps=plan.steps,
                    )
                )
                if step.phase is not None and step.phase != "MUTATE":
                    errors.append(f"step {step.step_id} writes files but phase is not MUTATE")
                if step.phase == "MUTATE":
                    errors.extend(self._mutate_contract_errors(step=step))
            elif step.phase == "MUTATE":
                errors.append(f"step {step.step_id} phase MUTATE must set permissions.write_files=true")

            for artifact_id in step.output_artifacts:
                produced.add(artifact_id)
                produced_by.setdefault(artifact_id, index)

        if write_step_indexes:
            first_write = min(write_step_indexes)
            last_write = max(write_step_indexes)
            post_mutation_verify_steps = [
                step for step in plan.steps[last_write + 1 :] if step.worker_type == "verify_worker" and step.phase != "FINALIZE"
            ]

            pre_write_rollback_artifacts = {
                artifact_id
                for artifact_id, producer_index in produced_by.items()
                if producer_index < first_write and self._artifact_matches(artifact_id, ("rollback", "revert"))
            }
            if not pre_write_rollback_artifacts:
                errors.append("mutation requires a rollback/revert artifact before write")
            elif not self._write_steps_consume_any_artifact(
                plan=plan,
                write_step_indexes=write_step_indexes,
                artifacts=pre_write_rollback_artifacts,
            ):
                errors.append("mutation must consume pre-write rollback/revert artifact")

            if self._requires_discovery_before_mutation(envelope):
                if not any(self._is_discovery_step(plan.steps[i].worker_type, plan.steps[i].permissions) for i in range(first_write)):
                    errors.append("mutation requires a prior read-only discovery step")

            if not post_mutation_verify_steps:
                errors.append("mutation requires a later verify_worker step")

            design_outputs = {
                artifact_id
                for index, step in enumerate(plan.steps[:first_write])
                if step.phase == "DESIGN"
                for artifact_id in step.output_artifacts
            }
            if "mutation_scope" not in design_outputs:
                errors.append("mutation requires prior DESIGN step output mutation_scope")
            if "rollback_plan" not in design_outputs:
                errors.append("mutation requires prior DESIGN step output rollback_plan")
            if not (design_outputs & DESIGN_VERIFICATION_ARTIFACTS):
                errors.append("mutation requires prior DESIGN step output verification_plan or test_plan")

            mutation_output_artifacts = {
                artifact_id for index in write_step_indexes for artifact_id in plan.steps[index].output_artifacts
            }
            mutation_write_scope_artifacts = {
                artifact_id
                for index in write_step_indexes
                for artifact_id in plan.steps[index].input_artifacts
                if self._artifact_matches(artifact_id, tuple(WRITE_SCOPE_ARTIFACT_SIGNALS))
            }
            mutation_evidence_artifacts = {
                artifact_id
                for index in write_step_indexes
                for artifact_id in plan.steps[index].input_artifacts
                if self._artifact_matches(artifact_id, ("evidence", "root_cause"))
            }
            verify_input_artifacts = {
                artifact_id for step in post_mutation_verify_steps for artifact_id in step.input_artifacts
            }

            if post_mutation_verify_steps:
                if "change_summary" not in verify_input_artifacts:
                    errors.append("verification after mutation must consume change_summary")
                if not (verify_input_artifacts & WRITE_SCOPE_ARTIFACT_SIGNALS):
                    errors.append("verification after mutation must consume a write-scope artifact")
                if not any(
                    artifact_id in VERIFY_EVIDENCE_ARTIFACT_SIGNALS
                    or self._artifact_matches(artifact_id, ("evidence", "root_cause"))
                    for artifact_id in verify_input_artifacts
                ):
                    errors.append("verification after mutation must consume evidence/root-cause artifacts")

            if mutation_output_artifacts and not (verify_input_artifacts & mutation_output_artifacts):
                errors.append("verification after mutation must consume mutation output artifacts")
            if mutation_write_scope_artifacts and not (verify_input_artifacts & mutation_write_scope_artifacts):
                errors.append("verification after mutation must consume write-scope artifacts used by mutation")

            if not any(
                step.worker_type == "verify_worker"
                and any(self._artifact_matches(artifact_id, ("verification", "test")) for artifact_id in step.output_artifacts)
                for step in post_mutation_verify_steps
            ):
                errors.append("verification after mutation must output verification/test artifacts")

        if phase_contract_required:
            self._validate_phase_progression(envelope=envelope, plan=plan, errors=errors)

        if errors:
            raise PlannerValidationError(errors)
        return plan

    def _requires_discovery_before_mutation(self, envelope: Envelope) -> bool:
        context = {self._normalize(value) for value in envelope.context_needed}
        constraints = {self._normalize(value) for value in envelope.constraints}
        return bool(context & DISCOVERY_CONTEXT_SIGNALS) or bool(constraints & DISCOVERY_CONSTRAINT_SIGNALS)

    def _is_discovery_step(self, worker_type: str, permissions: dict) -> bool:
        return worker_type in {"repo_worker", "research_worker", "web_research_worker", "infra_worker"} and not bool(
            permissions.get("write_files", False)
        )

    def _write_scope_errors(
        self,
        *,
        step_id: str,
        permissions: dict,
        produced: set[str],
        produced_by: dict[str, int],
        steps: list,
    ) -> list[str]:
        write_paths = permissions.get("write_paths")
        write_path_artifacts = permissions.get("write_paths_from_artifacts")

        if isinstance(write_paths, list) and any(self._is_specific_path(value) for value in write_paths):
            return []
        if isinstance(write_path_artifacts, list) and write_path_artifacts:
            missing = [artifact_id for artifact_id in write_path_artifacts if artifact_id not in produced]
            if not missing:
                invalid_scope_artifacts = []
                for artifact_id in write_path_artifacts:
                    producer_index = produced_by.get(artifact_id)
                    producer_phase = steps[producer_index].phase if producer_index is not None else None
                    if producer_phase != "DESIGN" or artifact_id not in WRITE_SCOPE_ARTIFACT_SIGNALS:
                        invalid_scope_artifacts.append(artifact_id)
                if invalid_scope_artifacts:
                    return [
                        f"step {step_id} write_paths_from_artifacts must reference DESIGN-produced write-scope artifacts: {', '.join(invalid_scope_artifacts)}"
                    ]
                return []
            return [
                f"step {step_id} write_paths_from_artifacts must reference earlier path artifacts: {', '.join(missing)}"
            ]
        return [f"step {step_id} with write_files must restrict writes by write_paths or write_paths_from_artifacts"]

    def _mutate_contract_errors(self, *, step) -> list[str]:
        errors: list[str] = []
        if step.mode != "bounded_mutation":
            errors.append(f"step {step.step_id} phase MUTATE must use mode bounded_mutation")
        if step.permissions.get("read_files") is not True:
            errors.append(f"step {step.step_id} phase MUTATE must set permissions.read_files=true")

        write_scope_inputs = [artifact_id for artifact_id in step.input_artifacts if artifact_id in WRITE_SCOPE_ARTIFACTS]
        if not write_scope_inputs:
            errors.append(f"step {step.step_id} phase MUTATE must consume a write-scope artifact")

        context_inputs = [
            artifact_id
            for artifact_id in step.input_artifacts
            if self._artifact_matches(artifact_id, tuple(MUTATION_CONTEXT_ARTIFACT_SIGNALS))
        ]
        if not context_inputs:
            errors.append(f"step {step.step_id} phase MUTATE must consume root-cause, evidence, or fix-design context")

        write_scope_refs = step.permissions.get("write_paths_from_artifacts")
        if not isinstance(write_scope_refs, list) or not (set(write_scope_refs) & WRITE_SCOPE_ARTIFACT_SIGNALS):
            errors.append(
                f"step {step.step_id} phase MUTATE must scope writes with write_paths_from_artifacts"
            )
        elif not (set(write_scope_refs) & set(write_scope_inputs)):
            errors.append(
                f"step {step.step_id} phase MUTATE must consume the write-scope artifact used by write_paths_from_artifacts"
            )

        if "change_summary" not in step.output_artifacts:
            errors.append(f"step {step.step_id} phase MUTATE must output change_summary")
        if not (set(step.output_artifacts) & MUTATION_REVIEW_ARTIFACTS):
            review_artifacts = ", ".join(sorted(MUTATION_REVIEW_ARTIFACTS))
            errors.append(f"step {step.step_id} phase MUTATE must output one of: {review_artifacts}")
        return errors

    def _is_specific_path(self, value: object) -> bool:
        if not isinstance(value, str):
            return False
        stripped = value.strip()
        if not stripped or stripped in {"*", ".", "./", "/"}:
            return False
        return "*" not in stripped

    def _write_steps_consume_any_artifact(
        self,
        *,
        plan: Plan,
        write_step_indexes: list[int],
        artifacts: set[str],
    ) -> bool:
        return any(
            artifact_id in artifacts
            for index in write_step_indexes
            for artifact_id in plan.steps[index].input_artifacts
        )

    def _artifact_matches(self, artifact_id: str, needles: tuple[str, ...]) -> bool:
        normalized = self._normalize(artifact_id)
        return any(needle in normalized for needle in needles)

    def _validate_phase_progression(self, *, envelope: Envelope, plan: Plan, errors: list[str]) -> None:
        phase_steps: list[tuple[int, str, bool]] = []
        for index, step in enumerate(plan.steps):
            phase = step.phase or ""
            write_files = bool(step.permissions.get("write_files", False))
            phase_steps.append((index, phase, write_files))

        previous_phase_index = -1
        finalize_seen = False
        first_mutate_index: int | None = None
        last_mutate_index: int | None = None

        for index, phase, write_files in phase_steps:
            phase_index = PHASE_INDEX.get(phase)
            if phase_index is None:
                continue

            if phase_index < previous_phase_index:
                errors.append(f"plan phase order regresses at step index {index}")
            previous_phase_index = phase_index

            if finalize_seen:
                errors.append("plan has steps after FINALIZE")
                break

            if phase == "FINALIZE":
                finalize_seen = True

            if phase == "MUTATE" or write_files:
                if first_mutate_index is None:
                    first_mutate_index = index
                last_mutate_index = index

        if first_mutate_index is None:
            return

        has_post_verify = any(
            phase == "VERIFY"
            for step_index, phase, _ in phase_steps
            if last_mutate_index is not None and step_index > last_mutate_index
        )
        if not has_post_verify:
            errors.append("plan requires VERIFY after MUTATE")

        has_post_finalize = any(
            phase == "FINALIZE"
            for step_index, phase, _ in phase_steps
            if last_mutate_index is not None and step_index > last_mutate_index
        )
        if not has_post_finalize:
            errors.append("plan requires FINALIZE after mutation flow")

    def _normalize(self, value: str) -> str:
        return value.lower().replace("-", "_").replace(" ", "_")
