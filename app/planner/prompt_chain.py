"""LLM-backed planner prompt chain with deterministic validation."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.planner.contracts import ALLOWED_MODES, PlannerModelClient, PlannerValidationError, WORKER_CATALOG, WRITE_SCOPE_ARTIFACTS
from app.planner.validator import PlannerPlanValidator
from app.schemas import Envelope, Plan


class PlannerPromptChainError(RuntimeError):
    """Raised when planner prompt-chain generation fails."""


class LLMPlanCompiler:
    """Compile a validated plan from an envelope using draft+repair stages."""

    def __init__(
        self,
        *,
        model_client: PlannerModelClient,
        validator: PlannerPlanValidator | None = None,
    ) -> None:
        self._model_client = model_client
        self._validator = validator or PlannerPlanValidator()

    def run(self, envelope: Envelope) -> Plan:
        schema = Plan.model_json_schema()
        draft_prompt = self._draft_prompt(envelope=envelope, schema=schema)
        draft_response = self._model_client.complete_json(
            stage="draft_plan",
            prompt=draft_prompt,
            schema=schema,
        )

        try:
            plan, budget_auto_aligned = self._parse_and_validate(
                envelope=envelope,
                response=draft_response,
            )
            diagnostics = self._build_diagnostics(
                mode="completed",
                stages=["draft_plan", "validate_plan"],
                model_calls=1,
                repair_attempted=False,
                validation_errors=[],
                resolved_validation_errors=[],
                budget_auto_aligned=budget_auto_aligned,
                envelope=envelope,
            )
            return self._with_metadata(plan, diagnostics)
        except (ValidationError, PlannerValidationError) as draft_exc:
            validation_errors = self._serialize_validation_errors(draft_exc)

        repair_response = self._model_client.complete_json(
            stage="repair_plan_1",
            prompt=self._repair_prompt(
                envelope=envelope,
                schema=schema,
                draft_response=draft_response,
                validation_errors=validation_errors,
            ),
            schema=schema,
        )

        try:
            repaired_plan, budget_auto_aligned = self._parse_and_validate(
                envelope=envelope,
                response=repair_response,
            )
            diagnostics = self._build_diagnostics(
                mode="repaired",
                stages=["draft_plan", "validate_plan", "repair_plan_1", "validate_plan"],
                model_calls=2,
                repair_attempted=True,
                validation_errors=[],
                resolved_validation_errors=validation_errors,
                budget_auto_aligned=budget_auto_aligned,
                envelope=envelope,
            )
            return self._with_metadata(repaired_plan, diagnostics)
        except (ValidationError, PlannerValidationError) as repair_exc:
            repair_errors = self._serialize_validation_errors(repair_exc)

        final_repair_response = self._model_client.complete_json(
            stage="repair_plan_2",
            prompt=self._repair_prompt(
                envelope=envelope,
                schema=schema,
                draft_response=repair_response,
                validation_errors=repair_errors,
            ),
            schema=schema,
        )

        try:
            final_repaired_plan, budget_auto_aligned = self._parse_and_validate(
                envelope=envelope,
                response=final_repair_response,
            )
            diagnostics = self._build_diagnostics(
                mode="repaired",
                stages=["draft_plan", "validate_plan", "repair_plan_1", "validate_plan", "repair_plan_2", "validate_plan"],
                model_calls=3,
                repair_attempted=True,
                validation_errors=[],
                resolved_validation_errors=[*validation_errors, *repair_errors],
                budget_auto_aligned=budget_auto_aligned,
                envelope=envelope,
            )
            return self._with_metadata(final_repaired_plan, diagnostics)
        except (ValidationError, PlannerValidationError) as final_repair_exc:
            final_repair_errors = self._serialize_validation_errors(final_repair_exc)
            diagnostics = self._build_diagnostics(
                mode="failed",
                stages=["draft_plan", "validate_plan", "repair_plan_1", "validate_plan", "repair_plan_2", "validate_plan"],
                model_calls=3,
                repair_attempted=True,
                validation_errors=final_repair_errors,
                resolved_validation_errors=[*validation_errors, *repair_errors],
                budget_auto_aligned=False,
                envelope=envelope,
            )
            raise PlannerPromptChainError(
                f"planner prompt chain failed after repair: {json.dumps(diagnostics, sort_keys=True)}"
            ) from final_repair_exc

    def _parse_and_validate(self, *, envelope: Envelope, response: str) -> tuple[Plan, bool]:
        plan = Plan.model_validate_json(response)
        normalized_plan, budget_auto_aligned = self._normalize_budget(plan)
        validated = self._validator.validate(envelope, normalized_plan)
        return validated, budget_auto_aligned

    def _draft_prompt(self, *, envelope: Envelope, schema: dict[str, Any]) -> str:
        payload = {
            "task": "Create a safe execution plan JSON.",
            "instructions": [
                "Return only JSON matching the plan schema exactly.",
                "Do not add markdown or prose outside JSON.",
                "Use only worker types in worker_catalog.",
                "Use canonical phases: DISCOVER, ANALYZE, RESEARCH, DESIGN, MUTATE, VERIFY, FINALIZE.",
                "Use only allowed_modes for step.mode; keep semantic meaning in step.phase, not mode.",
                "Map phases to modes exactly: DISCOVER/ANALYZE/RESEARCH=observe_only, DESIGN=plan_only, MUTATE=bounded_mutation, VERIFY=verify_only, FINALIZE=summarize_only.",
                "For phase-aware plans, populate each step.phase and each step.mode.",
                "Use step.task_id to group multi-task work; for single-task plans use a stable non-empty task_id.",
                "Set plan.execution_pattern to summarize phase flow (for example: discover_analyze_design_mutate_verify_finalize).",
                "Set plan.global_invariants to explicit safety invariants.",
                "Every input_artifact must be produced by an earlier step output_artifacts.",
                "Plan budget must cover all step max_tool_calls/max_model_calls and step count.",
                "Plan budget must include max_tool_calls, max_model_calls, max_workers, and max_retries.",
                "Treat envelope artifacts as search hints unless they are explicit paths.",
                "Do not treat artifact names like API, dashboard, policy module, pipeline, component, or service as writable paths.",
                "DISCOVER may output candidate paths/locations only; do not use those artifacts directly as write scope.",
                "For any mutation plan, DESIGN must convert discovered candidates into a narrow mutation_scope artifact before mutation.",
                "For any mutation plan, DESIGN must output both mutation_scope and rollback_plan before any write step.",
                "DESIGN may also output allowed_write_paths, writable_targets, patch_scope, verification_plan, or dependency_artifacts when useful.",
                "If write_files=true appears in any step, include prior read-only discovery when constraints/context require discovery.",
                "If write_files=true appears in any step, include a later verify_worker step.",
                "Any write_files=true step must restrict writes with permissions.write_paths or permissions.write_paths_from_artifacts.",
                "When using write_paths_from_artifacts, reference only DESIGN-produced write-scope artifacts named mutation_scope, allowed_write_paths, writable_targets, or patch_scope.",
                "MUTATE must use mode bounded_mutation, set write_files=true, consume mutation_scope and rollback_plan, and scope writes with permissions.write_paths_from_artifacts.",
                "MUTATE must output change_summary and should also output rollback_patch, rollback_artifact, or revert_instructions when a rollback patch is available.",
                "For phase-aware plans, every step.permissions must explicitly include boolean read_files, write_files, and run_commands keys.",
                "For high-complexity mutating plans, split target discovery, risk/evidence collection, and change design into separate pre-mutation steps when those contexts are required by the envelope.",
                "If envelope context/constraints require evidence, produce evidence artifacts before mutation and pass them into mutation.",
                "If required evidence cannot be collected, produce an evidence_gap artifact and stop or replan before mutation.",
                "If envelope context/constraints require dependency verification, produce dependency artifacts before mutation and pass them into mutation.",
                "If dependency verification fails or is inconclusive, stop or replan before mutation.",
                "Verification after mutation must consume change_summary, a write-scope artifact, and evidence/root-cause artifacts.",
                "Verification after mutation should check root-cause match, scope containment, focused check results, and rollback availability.",
                "Verification after mutation must output verification/test artifacts.",
                "FINALIZE steps must output a final_report, final_summary, or equivalent final artifact.",
                "For phase-aware mutating plans, include FINALIZE after VERIFY.",
                "Low confidence or high ambiguity should favor observe-first/discovery-first sequencing.",
                "Do not combine discovery, evidence collection, design, mutation, and verification into one overloaded worker step.",
                "Worker steps should have one primary responsibility.",
            ],
            "permission_semantics": {
                "read_files": "May inspect repository and files.",
                "write_files": "May mutate files. Only safe on code_worker.",
                "run_commands": "May execute shell or test commands.",
            },
            "allowed_modes": ALLOWED_MODES,
            "write_scope_artifacts": WRITE_SCOPE_ARTIFACTS,
            "safety_policies": {
                "discovery_before_mutation": "Do not mutate before target/dependency/performance/context is established when required.",
                "verify_after_write": "Any file write requires a later verify_worker step.",
                "phase_order": "For each task_id, phases should progress in canonical order without backtracking.",
                "finalize_after_verify": "Mutating phase-aware plans should end with FINALIZE after VERIFY.",
                "evidence_required": "Do not claim fixes or improvements without evidence collection when requested.",
                "evidence_gap_handling": "If evidence is required but unavailable, stop or replan rather than inventing evidence.",
                "dependency_before_mutation": "Confirm required dependencies before mutation and include dependency artifacts as mutation input.",
                "mode_contract": "Mode is a small runtime enum only: observe_only, plan_only, bounded_mutation, verify_only, summarize_only. Phase carries semantic meaning.",
                "path_scoped_writes": "Write steps must be scoped to DESIGN-produced write-scope artifacts via write_paths_from_artifacts.",
                "candidate_paths_are_not_write_scope": "DISCOVER artifacts such as target_files, candidate_paths, repo_inventory, manifests, and source locations are candidates only; DESIGN must narrow them into mutation_scope, allowed_write_paths, writable_targets, or patch_scope before mutation.",
                "artifact_names_are_not_paths": "Envelope artifacts are semantic hints unless explicitly resolved into file paths by discovery.",
                "rollback_before_write": "DESIGN must produce rollback_plan before mutation, and MUTATE must consume it.",
                "rollback_required": "Write steps must produce change_summary and rollback/revert artifacts when available.",
                "verification_context": "VERIFY must consume change_summary, write scope, and evidence/root-cause artifacts for mutation plans.",
                "low_confidence": "Low confidence or high ambiguity should favor observe-first/discovery-first sequencing.",
                "single_responsibility_steps": "Avoid overloaded worker steps that mix discovery, analysis, design, mutation, and verification.",
            },
            "phase_model": {
                "DISCOVER": {"default_mode": "observe_only", "worker_types": ["repo_worker", "infra_worker", "research_worker"]},
                "ANALYZE": {"default_mode": "observe_only", "worker_types": ["research_worker", "infra_worker", "repo_worker"]},
                "RESEARCH": {"default_mode": "observe_only", "worker_types": ["research_worker", "repo_worker"]},
                "DESIGN": {"default_mode": "plan_only", "worker_types": ["research_worker", "code_worker", "infra_worker"]},
                "MUTATE": {"default_mode": "bounded_mutation", "worker_types": ["code_worker"]},
                "VERIFY": {"default_mode": "verify_only", "worker_types": ["verify_worker"]},
                "FINALIZE": {"default_mode": "summarize_only", "worker_types": ["verify_worker", "direct_worker", "research_worker"]},
            },
            "worker_catalog": WORKER_CATALOG,
            "envelope": envelope.model_dump(mode="json"),
            "plan_schema": schema,
        }
        return json.dumps(payload, sort_keys=True)

    def _repair_prompt(
        self,
        *,
        envelope: Envelope,
        schema: dict[str, Any],
        draft_response: str,
        validation_errors: list[dict[str, Any]],
    ) -> str:
        payload = {
            "task": "Repair the invalid plan JSON so it passes schema and safety validation.",
            "instructions": [
                "Return only repaired JSON.",
                "Use only worker types in worker_catalog.",
                "Ensure canonical step.phase values and populated step.mode/task_id for phase-aware plans.",
                "Use only allowed_modes for step.mode; do not invent semantic mode names like discovery, analysis, scope_design, apply_patch, standard, or concurrency_check.",
                "Map phases to modes exactly: DISCOVER/ANALYZE/RESEARCH=observe_only, DESIGN=plan_only, MUTATE=bounded_mutation, VERIFY=verify_only, FINALIZE=summarize_only.",
                "Ensure plan.execution_pattern and plan.global_invariants are populated for phase-aware plans.",
                "Fix phase order regressions by changing the plan JSON, not by dropping phase metadata.",
                "Ensure artifact dependencies reference prior outputs.",
                "Ensure budget covers step totals.",
                "Ensure budget includes max_tool_calls, max_model_calls, max_workers, and max_retries.",
                "Ensure discovery-before-mutation and verify-after-write policies.",
                "Ensure mutating phase-aware plans include FINALIZE after VERIFY.",
                "Ensure DISCOVER outputs candidate paths only and DESIGN converts them into mutation_scope before mutation.",
                "Do not output mutation_scope, allowed_write_paths, writable_targets, or patch_scope from DISCOVER/ANALYZE/RESEARCH; those are DESIGN outputs only.",
                "Ensure DESIGN outputs both mutation_scope and rollback_plan before mutation, and MUTATE consumes both.",
                "Ensure write_paths_from_artifacts references only DESIGN-produced write-scope artifacts, not broad DISCOVER artifacts.",
                "Ensure MUTATE outputs change_summary and rollback_patch, rollback_artifact, or revert_instructions when available.",
                "Ensure VERIFY consumes change_summary, a write-scope artifact, and evidence/root-cause artifacts.",
                "Ensure VERIFY checks root-cause match, scope containment, focused check results, and rollback availability.",
                "Ensure FINALIZE outputs a final_report, final_summary, or equivalent final artifact.",
                "For high-complexity mutation plans, split dependency discovery and evidence collection into separate pre-mutation steps.",
                "Ensure mutation consumes required evidence and dependency artifacts when requested by envelope context/constraints.",
                "If dependency_manifest is required, produce a dependency_manifest/dependency_evidence artifact before mutation and pass it into MUTATE.",
                "Ensure write steps are path-scoped and output rollback/revert artifacts.",
                "Ensure every phase-aware step has explicit boolean read_files/write_files/run_commands permissions.",
            ],
            "validation_errors": validation_errors,
            "previous_response": draft_response[:8000],
            "envelope": envelope.model_dump(mode="json"),
            "allowed_modes": ALLOWED_MODES,
            "write_scope_artifacts": WRITE_SCOPE_ARTIFACTS,
            "worker_catalog": WORKER_CATALOG,
            "plan_schema": schema,
        }
        return json.dumps(payload, sort_keys=True)

    def _serialize_validation_errors(self, error: ValidationError | PlannerValidationError) -> list[dict[str, Any]]:
        if isinstance(error, ValidationError):
            return [
                {
                    "type": err.get("type"),
                    "loc": err.get("loc"),
                    "msg": err.get("msg"),
                }
                for err in error.errors(include_input=False)
            ]
        return [{"type": "planner_validation", "msg": msg} for msg in error.errors]

    def _build_diagnostics(
        self,
        *,
        mode: str,
        stages: list[str],
        model_calls: int,
        repair_attempted: bool,
        validation_errors: list[dict[str, Any]],
        resolved_validation_errors: list[dict[str, Any]],
        budget_auto_aligned: bool,
        envelope: Envelope,
    ) -> dict[str, Any]:
        return {
            "mode": mode,
            "stages": stages,
            "model_calls": model_calls,
            "repair_attempted": repair_attempted,
            "validation_errors": validation_errors,
            "resolved_validation_errors": resolved_validation_errors,
            "budget_auto_aligned": budget_auto_aligned,
            "envelope_input_type": envelope.input_type,
            "envelope_complexity_hint": envelope.complexity_hint,
        }

    def _with_metadata(self, plan: Plan, diagnostics: dict[str, Any]) -> Plan:
        metadata = dict(plan.metadata)
        metadata["llm_planner"] = diagnostics
        return plan.model_copy(update={"metadata": metadata})

    def _normalize_budget(self, plan: Plan) -> tuple[Plan, bool]:
        budget = dict(plan.budget or {})
        required_tools = sum(step.max_tool_calls for step in plan.steps)
        required_models = sum(step.max_model_calls for step in plan.steps)
        required_workers = len(plan.steps)

        adjusted = False

        normalized_tools = self._coerce_int(budget.get("max_tool_calls"))
        if normalized_tools is None or normalized_tools < required_tools:
            budget["max_tool_calls"] = required_tools
            adjusted = True
        else:
            budget["max_tool_calls"] = normalized_tools

        normalized_models = self._coerce_int(budget.get("max_model_calls"))
        if normalized_models is None or normalized_models < required_models:
            budget["max_model_calls"] = required_models
            adjusted = True
        else:
            budget["max_model_calls"] = normalized_models

        normalized_workers = self._coerce_int(budget.get("max_workers"))
        if normalized_workers is None or normalized_workers < required_workers:
            budget["max_workers"] = required_workers
            adjusted = True
        else:
            budget["max_workers"] = normalized_workers

        normalized_retries = self._coerce_int(budget.get("max_retries"))
        if normalized_retries is None:
            budget["max_retries"] = 0
            adjusted = True
        else:
            budget["max_retries"] = max(0, normalized_retries)

        if not adjusted:
            return plan, False
        return plan.model_copy(update={"budget": budget}), True

    def _coerce_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
