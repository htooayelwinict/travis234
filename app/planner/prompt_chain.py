"""LLM-backed planner prompt chain with deterministic validation."""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from app.artifact_aliases import canonical_artifact_catalog, canonical_artifact_id, canonicalize_plan_artifact_ids
from app.planner.contracts import ALLOWED_MODES, PlannerModelClient, PlannerValidationError, WORKER_CATALOG, WRITE_SCOPE_ARTIFACTS
from app.planner.validator import PlannerPlanValidator
from app.schemas import Envelope, Plan, ReplanRequest


class PlannerPromptChainError(RuntimeError):
    """Raised when planner prompt-chain generation fails."""


_DIRECT_SUPPORT_GLOBAL_INVARIANTS = ("no_tools", "no_file_access", "answer_from_user_input_only")
_DIRECT_SUPPORT_INSTRUCTION = (
    "Known facts: User needs direct support from the provided input only. "
    "Unknowns: Missing details from context_needed or ambiguity, if any. "
    "Do now: Ask concise clarifying questions if needed and provide immediate harmless guidance. "
    "Do not do: Do not use tools, files, commands, or invent unsupported provider-specific facts. "
    "Output: direct_guidance with safe next steps or clarification."
)
_INSTRUCTION_CONTEXT_LABELS = ("Known facts:", "Unknowns:", "Do now:", "Do not do:", "Output:")
_INSTRUCTION_CONTEXT_REPAIR_GOAL = (
    "Rewrite only the instruction text as needed so the context block leads the instruction and remains compact, "
    "accurate, and step-specific."
)


def _direct_support_plan_template() -> dict[str, Any]:
    return {
        "plan_id": "plan_<request_id>_direct_support",
        "request_id": "<request_id>",
        "planner": "direct_support_planner",
        "objective": "Provide direct support without repository, file, command, or worker-runtime side effects.",
        "strategy": "phase_aware_direct_support",
        "execution_pattern": "finalize",
        "global_invariants": list(_DIRECT_SUPPORT_GLOBAL_INVARIANTS),
        "steps": [
            {
                "step_id": "direct_support_response",
                "worker_type": "direct_worker",
                "phase": "FINALIZE",
                "mode": "summarize_only",
                "task_id": "direct_support",
                "instruction": _DIRECT_SUPPORT_INSTRUCTION,
                "input_artifacts": [],
                "output_artifacts": ["direct_guidance"],
                "max_tool_calls": 0,
                "max_model_calls": 1,
                "permissions": {
                    "read_files": False,
                    "write_files": False,
                    "run_commands": False,
                    "web_research": False,
                },
            }
        ],
        "budget": {"max_tool_calls": 0, "max_model_calls": 1, "max_workers": 1, "max_retries": 0},
        "success_criteria": [
            "User receives a direct response with appropriate clarification or safe immediate guidance."
        ],
        "metadata": {"archetype": "direct_support"},
    }


def _instruction_context_block(*, repair: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {"required_prefix_labels": list(_INSTRUCTION_CONTEXT_LABELS)}
    if repair:
        block["repair_goal"] = _INSTRUCTION_CONTEXT_REPAIR_GOAL
        return block

    block.update(
        {
            "field_definitions": {
                "Known facts": "Essential envelope facts plus prior artifact names needed by the worker.",
                "Unknowns": "Missing details, ambiguity, or evidence gaps; use none when none.",
                "Do now": "One primary action for this step and only this step.",
                "Do not do": "Scope, safety, permission, evidence, and mutation boundaries.",
                "Output": "Expected output artifact names and success signal.",
            },
            "direct_support_example": "Known facts: MRT card is not working and commute is tomorrow. Unknowns: city/network, card type, exact error, balance/top-up status. Do now: Ask focused questions and give safe legal backup commute steps. Do not do: Do not use tools/files or suggest fare evasion. Output: direct_guidance with immediate troubleshooting and backup options.",
            "mutation_example": "Known facts: root_cause_evidence, mutation_scope, rollback_plan, and fix_design are available. Unknowns: none unless evidence_gap is material. Do now: Apply the scoped fix only within mutation_scope. Do not do: Do not write outside scope or claim success without verification. Output: change_summary and rollback_patch.",
        }
    )
    return block


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

    def replan(self, *, envelope: Envelope, current_plan: Plan, replan_request: ReplanRequest) -> Plan:
        schema = Plan.model_json_schema()
        response = self._model_client.complete_json(
            stage="replan_plan",
            prompt=self._replan_prompt(
                envelope=envelope,
                current_plan=current_plan,
                replan_request=replan_request,
                schema=schema,
            ),
            schema=schema,
        )

        try:
            plan, budget_auto_aligned = self._parse_and_validate(
                envelope=envelope,
                response=response,
                initial_artifact_ids=self._carryover_artifact_ids(replan_request),
            )
            diagnostics = self._build_diagnostics(
                mode="completed",
                stages=["replan_plan", "validate_plan"],
                model_calls=1,
                repair_attempted=False,
                validation_errors=[],
                resolved_validation_errors=[],
                budget_auto_aligned=budget_auto_aligned,
                envelope=envelope,
            )
            diagnostics.update(
                {
                    "replan": True,
                    "parent_plan_id": current_plan.plan_id,
                    "failed_step_id": replan_request.failed_step_id,
                }
            )
            return self._with_metadata(plan, diagnostics)
        except (ValidationError, PlannerValidationError) as draft_exc:
            validation_errors = self._serialize_validation_errors(draft_exc)

        repair_response = self._model_client.complete_json(
            stage="repair_replan_plan_1",
            prompt=self._repair_replan_prompt(
                envelope=envelope,
                current_plan=current_plan,
                replan_request=replan_request,
                draft_response=response,
                validation_errors=validation_errors,
                schema=schema,
            ),
            schema=schema,
        )

        try:
            repaired_plan, budget_auto_aligned = self._parse_and_validate(
                envelope=envelope,
                response=repair_response,
                initial_artifact_ids=self._carryover_artifact_ids(replan_request),
            )
            diagnostics = self._build_diagnostics(
                mode="repaired",
                stages=["replan_plan", "validate_plan", "repair_replan_plan_1", "validate_plan"],
                model_calls=2,
                repair_attempted=True,
                validation_errors=[],
                resolved_validation_errors=validation_errors,
                budget_auto_aligned=budget_auto_aligned,
                envelope=envelope,
            )
            diagnostics.update(
                {
                    "replan": True,
                    "parent_plan_id": current_plan.plan_id,
                    "failed_step_id": replan_request.failed_step_id,
                }
            )
            return self._with_metadata(repaired_plan, diagnostics)
        except (ValidationError, PlannerValidationError) as repair_exc:
            repair_errors = self._serialize_validation_errors(repair_exc)
            diagnostics = self._build_diagnostics(
                mode="failed",
                stages=["replan_plan", "validate_plan", "repair_replan_plan_1", "validate_plan"],
                model_calls=2,
                repair_attempted=True,
                validation_errors=repair_errors,
                resolved_validation_errors=validation_errors,
                budget_auto_aligned=False,
                envelope=envelope,
            )
            diagnostics.update(
                {
                    "replan": True,
                    "parent_plan_id": current_plan.plan_id,
                    "failed_step_id": replan_request.failed_step_id,
                }
            )
            raise PlannerPromptChainError(
                f"planner replan prompt chain failed after repair: {json.dumps(diagnostics, sort_keys=True)}"
            ) from repair_exc

    def _parse_and_validate(
        self,
        *,
        envelope: Envelope,
        response: str,
        initial_artifact_ids: list[str] | None = None,
    ) -> tuple[Plan, bool]:
        plan = Plan.model_validate_json(response)
        plan, _ = canonicalize_plan_artifact_ids(plan)
        normalized_plan, budget_auto_aligned = self._normalize_budget(plan)
        validated = self._validator.validate(
            envelope,
            normalized_plan,
            initial_artifact_ids=initial_artifact_ids,
        )
        return validated, budget_auto_aligned

    def _draft_prompt(self, *, envelope: Envelope, schema: dict[str, Any]) -> str:
        payload = {
            "task": "Create a safe execution plan JSON.",
            "instructions": [
                "Return only JSON matching the plan schema exactly.",
                "Do not add markdown or prose outside JSON.",
                "Use only worker types in worker_catalog.",
                "Set plan.planner to a planner identity, never to a worker type from worker_catalog.",
                "All newly generated plans must be phase-aware; schema-optional phase/mode/task_id/execution_pattern/global_invariants fields are backward compatibility only.",
                "Do not output null or omitted step.phase, step.mode, step.task_id, plan.execution_pattern, or empty plan.global_invariants in new plans.",
                "For low-complexity, non-mutation support, conceptual, or clarification requests with no code/file/infra/runtime action, emit the phase-aware direct_support archetype instead of a discovery/research/mutation worker flow.",
                "For direct_support, follow direct_support_plan_template exactly and customize only plan_id, request_id, objective, instruction, and success_criteria.",
                "Never use direct_support when the envelope requests code fixes, debugging, mutation, rollback, verification after change, repo/project work, file operations, deployment, security investigation, data isolation work, or other runtime action.",
                "Use canonical phases: DISCOVER, ANALYZE, RESEARCH, DESIGN, MUTATE, VERIFY, FINALIZE.",
                "Use only allowed_modes for step.mode; keep semantic meaning in step.phase, not mode.",
                "Map phases to modes exactly: DISCOVER/ANALYZE/RESEARCH=observe_only, DESIGN=plan_only, MUTATE=bounded_mutation, VERIFY=verify_only, FINALIZE=summarize_only.",
                "For phase-aware plans, populate each step.phase and each step.mode.",
                "Use step.task_id to group multi-task work; for single-task plans use a stable non-empty task_id.",
                "If any step has phase/mode/task_id, set top-level plan.execution_pattern to a non-empty snake_case phase sequence such as discover_analyze_design_mutate_verify_finalize.",
                "If any step has phase/mode/task_id, set top-level plan.global_invariants to a non-empty list of explicit safety invariants.",
                "Every generated step.instruction must start with a compact instruction context block using exactly these labels in this order: Known facts:, Unknowns:, Do now:, Do not do:, Output:.",
                "Keep instruction context blocks short but self-contained so a worker can act safely without hidden envelope context.",
                "Treat envelope.literal_contract as authoritative exact text. Copy exact JSON keys, paths, filenames, symbols, and artifact ids into relevant step instructions and success criteria.",
                "Use artifact_contract_catalog canonical ids for known runtime artifacts; do not invent near-aliases such as manifest_update_result or moved_item_records in new plans.",
                "Never introduce generated placeholders like [ADDRESS], [FIELD], or [PATH] unless that exact placeholder appeared in the user's raw input.",
                "For direct_support instructions, include user-visible facts, missing details, immediate guidance/clarifying action, no-tool/no-file boundaries, and direct_guidance output.",
                "For mutation-sensitive instructions, include mutation_scope, rollback_plan, evidence/design artifacts, no writes outside scope, and required change/rollback/verification outputs where relevant.",
                "For greenfield project scaffolding, bulk file creation, file moves, deletes, manifests, README/Dockerfile/config/test creation, or workspace cleanup, prefer filesystem_worker for the MUTATE step.",
                "For existing source-code logic changes inside already identified code files, prefer code_worker for the MUTATE step.",
                "For multi-file scaffolding or workspace cleanup, plan so filesystem_worker can use batch tools such as write_many_files instead of many repeated primitive write calls.",
                "When runtime/toolchain capability detection is needed, use infra_worker and expect a runtime_capabilities artifact rather than shell-chained version commands.",
                "Every step.input_artifacts entry must be produced by an earlier step.output_artifacts.",
                "Never copy envelope.artifacts into step.input_artifacts; envelope.artifacts are semantic planning hints only, not runtime artifacts.",
                "Plan budget must cover all step max_tool_calls/max_model_calls and step count.",
                "Plan budget must include max_tool_calls, max_model_calls, max_workers, and max_retries.",
                "If any step permission read_files, write_files, run_commands, or web_research is true, set that step.max_tool_calls to a positive integer.",
                "Use max_tool_calls=0 only for direct no-tool/no-file/no-command/no-web steps.",
                "Treat envelope artifacts as search hints unless they are explicit paths.",
                "Do not treat artifact names like API, dashboard, policy module, pipeline, component, or service as writable paths.",
                "DISCOVER may output candidate paths/locations only; do not use those artifacts directly as write scope.",
                "For any mutation plan, DESIGN must convert discovered candidates into a narrow mutation_scope artifact before mutation.",
                "For any mutation plan, DESIGN must output mutation_scope, rollback_plan, and verification_plan or test_plan before any write step.",
                "DESIGN should also output fix_design, patch_design, or change_design when the mutation needs a concrete fix design.",
                "DESIGN may also output allowed_write_paths, writable_targets, patch_scope, or dependency_artifacts when useful.",
                "If write_files=true appears in any step, include prior read-only discovery when constraints/context require discovery.",
                "If write_files=true appears in any step, include a later verify_worker step.",
                "Any write_files=true step must restrict writes with permissions.write_paths or permissions.write_paths_from_artifacts.",
                "When using write_paths_from_artifacts, reference only DESIGN-produced write-scope artifacts named mutation_scope, allowed_write_paths, writable_targets, or patch_scope.",
                "MUTATE must use mode bounded_mutation, set read_files=true and write_files=true, use code_worker or filesystem_worker, consume mutation_scope and rollback_plan, and scope writes with permissions.write_paths_from_artifacts.",
                "MUTATE must also consume evidence/design context such as root_cause_evidence, evidence_artifacts, classification_report, manifest_update_plan, moved_items_plan, analysis_evidence, fix_design, patch_design, or change_design.",
                "MUTATE must output change_summary and should also output rollback_patch, rollback_artifact, or revert_instructions when a rollback patch is available.",
                "For phase-aware plans, every step.permissions must explicitly include boolean read_files, write_files, run_commands, and web_research keys.",
                "For high-complexity mutating plans, split target discovery, risk/evidence collection, and change design into separate pre-mutation steps when those contexts are required by the envelope.",
                "If envelope context/constraints require evidence, produce evidence artifacts before mutation and pass them into mutation.",
                "If required evidence cannot be collected, produce an evidence_gap artifact and stop or replan before mutation.",
                "If envelope context/constraints require dependency verification, produce dependency artifacts before mutation and pass them into mutation.",
                "If dependency verification fails or is inconclusive, stop or replan before mutation.",
                "If envelope risks/constraints request external research, web comparisons, or source discovery, include a RESEARCH step using web_research_worker with write_files=false and explicit source/evidence output artifacts.",
                "Verification after mutation must consume change_summary, a write-scope artifact, and evidence/design context artifacts such as classification_report, manifest_update_record, moved_items_record, verification_plan, analysis_evidence, or root_cause_evidence.",
                "Verification after mutation should check intent/design match, scope containment, focused check results, and rollback availability.",
                "Verification after mutation must output verification/test artifacts.",
                "FINALIZE steps must output a final_report, final_summary, or equivalent final artifact.",
                "For phase-aware mutating plans, include FINALIZE after VERIFY.",
                "Low confidence or high ambiguity should favor observe-first/discovery-first sequencing.",
                "Do not combine discovery, evidence collection, design, mutation, and verification into one overloaded worker step.",
                "Worker steps should have one primary responsibility.",
            ],
            "permission_semantics": {
                "read_files": "May inspect repository and files.",
                "write_files": "May mutate files. Only safe on code_worker or filesystem_worker with explicit write scope.",
                "run_commands": "May execute shell/test commands.",
                "web_research": "May use web_research_worker for external research, comparison, and source discovery without file writes.",
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
                "verification_context": "VERIFY must consume change_summary, write scope, and evidence/design context artifacts for mutation plans.",
                "low_confidence": "Low confidence or high ambiguity should favor observe-first/discovery-first sequencing.",
                "single_responsibility_steps": "Avoid overloaded worker steps that mix discovery, analysis, design, mutation, and verification.",
            },
            "plan_archetypes": {
                "direct_support": {
                    "when": [
                        "envelope complexity is low",
                        "no mutation/file/repo/tool/runtime work is requested",
                        "no code, infra, security, data isolation, deployment, or debugging action is requested",
                        "request is real-world help, conceptual explanation, casual help, or clarification-first support",
                    ],
                    "never_when": [
                        "risks include mutation_requested or file_mutation",
                        "intents include code.fix, code.debug, infra.debug, deploy, security investigation, or workflow execution",
                        "domains include code or security with requested change/diagnosis",
                        "user asks to identify root cause and apply/design a safe fix with rollback or verification",
                    ],
                    "shape": "Use direct_support_plan_template exactly.",
                    "instruction_requirements": [
                        "Ask concise clarifying questions when context_needed or ambiguity is non-empty.",
                        "Include immediate safe guidance when the user has urgency or can take harmless next steps.",
                        "Do not invent provider-specific facts that are not present in the envelope.",
                    ],
                }
            },
            "direct_support_plan_template": _direct_support_plan_template(),
            "artifact_mapping_rules": {
                "envelope.artifacts": "Semantic hints for planning and wording only; never valid as step.input_artifacts by themselves.",
                "step.output_artifacts": "Runtime artifacts produced by earlier steps.",
                "step.input_artifacts": "Runtime artifacts from earlier step.output_artifacts only; direct_support plans should use an empty list.",
            },
            "instruction_context_block": _instruction_context_block(),
            "phase_model": {
                "DISCOVER": {"default_mode": "observe_only", "worker_types": ["repo_worker", "infra_worker", "research_worker"]},
                "ANALYZE": {"default_mode": "observe_only", "worker_types": ["research_worker", "web_research_worker", "infra_worker", "repo_worker"]},
                "RESEARCH": {"default_mode": "observe_only", "worker_types": ["research_worker", "web_research_worker", "repo_worker"]},
                "DESIGN": {"default_mode": "plan_only", "worker_types": ["research_worker", "code_worker", "filesystem_worker", "infra_worker"]},
                "MUTATE": {"default_mode": "bounded_mutation", "worker_types": ["code_worker", "filesystem_worker"]},
                "VERIFY": {"default_mode": "verify_only", "worker_types": ["verify_worker"]},
                "FINALIZE": {"default_mode": "summarize_only", "worker_types": ["verify_worker", "direct_worker", "research_worker"]},
            },
            "worker_catalog": WORKER_CATALOG,
            "artifact_contract_catalog": canonical_artifact_catalog(),
            "envelope": envelope.model_dump(mode="json"),
            "literal_contract": envelope.model_dump(mode="json").get("literal_contract", []),
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
                "Ensure plan.planner is a planner identity and is not any worker type from worker_catalog.",
                "All repaired plans must be phase-aware; do not keep null or omitted phase/mode/task_id/execution_pattern/global_invariants fields.",
                "For low-complexity, non-mutation support, conceptual, or clarification requests with no code/file/infra/runtime action, repair into the phase-aware direct_support archetype instead of inventing a discovery/research/mutation flow.",
                "Never repair a code/debug/fix/rollback/verification/runtime-action request into direct_support; repair it as a worker plan instead.",
                "If validation_errors mention missing phase/mode/task_id/execution_pattern for a low-complexity non-mutation support request, replace the invalid plan with direct_support_plan_template; do not preserve the invalid nullable step shape.",
                "Ensure canonical step.phase values and populated step.mode/task_id for phase-aware plans.",
                "Use only allowed_modes for step.mode; do not invent semantic mode names like discovery, analysis, scope_design, apply_patch, standard, or concurrency_check.",
                "Map phases to modes exactly: DISCOVER/ANALYZE/RESEARCH=observe_only, DESIGN=plan_only, MUTATE=bounded_mutation, VERIFY=verify_only, FINALIZE=summarize_only.",
                "Repair every missing, weak, or non-leading instruction context block so each step.instruction starts with exactly these labels in order: Known facts:, Unknowns:, Do now:, Do not do:, Output:.",
                "When repairing instruction context blocks, preserve valid plan shape while adding essential facts, unknowns, action, prohibitions, and expected output artifacts.",
                "Treat envelope.literal_contract as authoritative exact text. Preserve exact JSON keys, paths, filenames, symbols, and artifact ids in relevant instructions and success criteria.",
                "Use artifact_contract_catalog canonical ids for known runtime artifacts; repair near-aliases such as manifest_update_result to manifest_update_record and moved_item_records to moved_items_record.",
                "Remove generated placeholders like [ADDRESS], [FIELD], or [PATH] unless that exact placeholder appeared in the user's raw input.",
                "For mutation-sensitive repaired instructions, mention mutation_scope, rollback_plan, evidence/design context, no writes outside scope, and change/rollback/verification outputs where relevant.",
                "For greenfield scaffolding, batch file creation, file moves/deletes, manifests, README/Dockerfile/config/test creation, or workspace cleanup, prefer filesystem_worker for MUTATE.",
                "For multi-file scaffolding or workspace cleanup, repair toward filesystem_worker with batch tools such as write_many_files rather than repeated primitive writes.",
                "For runtime/toolchain capability detection, use infra_worker outputs such as runtime_capabilities instead of shell-chained command probes.",
                "If validation_errors mention plan.execution_pattern, add a non-empty top-level execution_pattern field; do not leave it null, empty, or omitted.",
                "If validation_errors mention plan.global_invariants, add a non-empty top-level global_invariants array; do not leave it empty or omitted.",
                "For any phase-aware plan, top-level execution_pattern and global_invariants are required even though the JSON schema allows compatibility omissions.",
                "Fix phase order regressions by changing the plan JSON, not by dropping phase metadata.",
                "Ensure artifact dependencies reference prior outputs.",
                "Never use envelope.artifacts as step.input_artifacts unless a previous step explicitly output the same artifact id.",
                "If repairing a direct_support plan, use input_artifacts=[] and output_artifacts=[\"direct_guidance\"].",
                "Ensure budget covers step totals.",
                "Ensure budget includes max_tool_calls, max_model_calls, max_workers, and max_retries.",
                "If any step permission read_files, write_files, run_commands, or web_research is true, set that step.max_tool_calls to a positive integer.",
                "Use max_tool_calls=0 only for direct no-tool/no-file/no-command/no-web steps.",
                "Ensure discovery-before-mutation and verify-after-write policies.",
                "Ensure mutating phase-aware plans include FINALIZE after VERIFY.",
                "Ensure DISCOVER outputs candidate paths only and DESIGN converts them into mutation_scope before mutation.",
                "Do not output mutation_scope, allowed_write_paths, writable_targets, or patch_scope from DISCOVER/ANALYZE/RESEARCH; those are DESIGN outputs only.",
                "Ensure DESIGN outputs mutation_scope, rollback_plan, and verification_plan or test_plan before mutation, and MUTATE consumes needed design/evidence context.",
                "Ensure write_paths_from_artifacts references only DESIGN-produced write-scope artifacts, not broad DISCOVER artifacts.",
                "Ensure MUTATE sets permissions.read_files=true and permissions.write_files=true and uses code_worker or filesystem_worker.",
                "Ensure MUTATE consumes evidence/design context such as root_cause_evidence, evidence_artifacts, classification_report, manifest_update_plan, moved_items_plan, analysis_evidence, fix_design, patch_design, or change_design.",
                "Ensure MUTATE outputs change_summary and rollback_patch, rollback_artifact, or revert_instructions when available.",
                "Ensure VERIFY consumes change_summary, a write-scope artifact, and evidence/design context artifacts such as classification_report, manifest_update_record, moved_items_record, verification_plan, analysis_evidence, or root_cause_evidence.",
                "Ensure VERIFY checks intent/design match, scope containment, focused check results, and rollback availability.",
                "Ensure FINALIZE outputs a final_report, final_summary, or equivalent final artifact.",
                "For high-complexity mutation plans, split dependency discovery and evidence collection into separate pre-mutation steps.",
                "Ensure mutation consumes required evidence and dependency artifacts when requested by envelope context/constraints.",
                "If dependency_manifest is required, produce a dependency_manifest/dependency_evidence artifact before mutation and pass it into MUTATE.",
                "If validation_errors or envelope context indicate missing external research/web comparison coverage, add or repair a RESEARCH step using web_research_worker with explicit source/evidence outputs.",
                "Ensure write steps are path-scoped and output rollback/revert artifacts.",
                "Ensure every phase-aware step has explicit boolean read_files/write_files/run_commands/web_research permissions.",
            ],
            "validation_errors": validation_errors,
            "previous_response": draft_response[:8000],
            "envelope": envelope.model_dump(mode="json"),
            "literal_contract": envelope.model_dump(mode="json").get("literal_contract", []),
            "allowed_modes": ALLOWED_MODES,
            "write_scope_artifacts": WRITE_SCOPE_ARTIFACTS,
            "direct_support_archetype": "Use direct_support_plan_template exactly.",
            "direct_support_plan_template": _direct_support_plan_template(),
            "worker_catalog": WORKER_CATALOG,
            "artifact_contract_catalog": canonical_artifact_catalog(),
            "instruction_context_block": _instruction_context_block(repair=True),
            "plan_schema": schema,
        }
        return json.dumps(payload, sort_keys=True)

    def _replan_prompt(
        self,
        *,
        envelope: Envelope,
        current_plan: Plan,
        replan_request: ReplanRequest,
        schema: dict[str, Any],
    ) -> str:
        payload = {
            "task": "Create a fixed new full execution plan JSON after a worker requested replan.",
            "instructions": [
                "Return only JSON matching the existing Plan schema exactly.",
                "Return a full replacement Plan, not a patch, not a continuation fragment, and not prose.",
                "The new Plan must be internally valid under the replan-aware planner validator.",
                "replan_request.carryover_artifacts are trusted completed runtime artifacts seeded into the replacement run.",
                "You may reference carryover artifact ids directly in step.input_artifacts without reproducing them.",
                "Prefer carryover artifacts over rehydrating old context when they are sufficient for the recovery step.",
                "Do not reference partial_artifacts or failed_step_artifacts as completed truth; use them only as failure evidence.",
                "Preserve the original envelope objective unless the replan reason proves the old approach is impossible.",
                "Use only worker types in worker_catalog.",
                "Use canonical phases: DISCOVER, ANALYZE, RESEARCH, DESIGN, MUTATE, VERIFY, FINALIZE.",
                "Use allowed_modes exactly: DISCOVER/ANALYZE/RESEARCH=observe_only, DESIGN=plan_only, MUTATE=bounded_mutation, VERIFY=verify_only, FINALIZE=summarize_only.",
                "Every step.instruction must start with labels in this order: Known facts:, Unknowns:, Do now:, Do not do:, Output:.",
                "Treat envelope.literal_contract as authoritative exact text. Preserve exact JSON keys, paths, filenames, symbols, and artifact ids in relevant instructions and success criteria.",
                "Use artifact_contract_catalog canonical ids for known runtime artifacts; do not invent near-aliases such as manifest_update_result or moved_item_records.",
                "Never introduce generated placeholders like [ADDRESS], [FIELD], or [PATH] unless that exact placeholder appeared in the user's raw input.",
                "Plan around the failed step and reason; reduce repeated work only if the new plan remains self-contained.",
                "Treat replan_request.completed_step_ids as authoritative execution history for which previous steps completed successfully.",
                "Do not include replan_request.failed_step_id in completed work unless it also appears in completed_step_ids.",
                "Add an early read-only re-observation step only when carryover artifacts are insufficient or stale.",
                "Every step.input_artifacts entry must be produced by an earlier step.output_artifacts in this new plan or be present in carryover_artifact_ids.",
                "Every phase-aware step.permissions must explicitly include boolean read_files, write_files, run_commands, and web_research keys.",
                "If any step permission read_files, write_files, run_commands, or web_research is true, set that step.max_tool_calls to a positive integer.",
                "Use max_tool_calls=0 only for direct no-tool/no-file/no-command/no-web steps.",
                "For any mutation plan, DESIGN must output mutation_scope, rollback_plan, and verification_plan or test_plan before MUTATE.",
                "MUTATE must use code_worker or filesystem_worker, consume mutation_scope, rollback_plan, and evidence/design context, must scope writes with write_paths_from_artifacts, and must output change_summary plus rollback/revert artifact.",
                "For multi-file scaffolding or workspace cleanup, prefer filesystem_worker and batch write tools such as write_many_files.",
                "Any write_files=true step must be followed by VERIFY, and VERIFY must consume change_summary, write scope, and evidence/design context.",
                "Include FINALIZE after VERIFY for mutating phase-aware plans.",
                "Plan budget must cover all step max_tool_calls/max_model_calls and worker count.",
            ],
            "envelope": envelope.model_dump(mode="json"),
            "literal_contract": envelope.model_dump(mode="json").get("literal_contract", []),
            "current_plan": current_plan.model_dump(mode="json"),
            "replan_request": replan_request.model_dump(mode="json"),
            "carryover_artifact_ids": self._carryover_artifact_ids(replan_request),
            "carryover_artifact_cards": self._artifact_cards(replan_request.carryover_artifacts),
            "allowed_modes": ALLOWED_MODES,
            "write_scope_artifacts": WRITE_SCOPE_ARTIFACTS,
            "worker_catalog": WORKER_CATALOG,
            "artifact_contract_catalog": canonical_artifact_catalog(),
            "instruction_context_block": _instruction_context_block(),
            "plan_schema": schema,
        }
        return json.dumps(payload, sort_keys=True)

    def _repair_replan_prompt(
        self,
        *,
        envelope: Envelope,
        current_plan: Plan,
        replan_request: ReplanRequest,
        draft_response: str,
        validation_errors: list[dict[str, Any]],
        schema: dict[str, Any],
    ) -> str:
        payload = {
            "task": "Repair the invalid replan output into a full replacement Plan JSON.",
            "instructions": [
                "Return only repaired JSON matching the existing Plan schema.",
                "Do not return a patch or partial continuation.",
                "The repaired plan may reference replan_request.carryover_artifacts directly by id as step.input_artifacts.",
                "Do not reference partial_artifacts or failed_step_artifacts as completed truth.",
                "Ensure the repaired plan passes the replan-aware planner validator.",
                "Use only worker types in worker_catalog.",
                "Ensure phase/mode/task_id/execution_pattern/global_invariants are populated for phase-aware plans.",
                "Ensure every step.permissions includes boolean read_files/write_files/run_commands/web_research.",
                "Preserve envelope.literal_contract exact JSON keys, paths, filenames, symbols, and artifact ids; remove generated placeholders not present in raw input.",
                "Use artifact_contract_catalog canonical ids for known runtime artifacts; repair near-aliases such as manifest_update_result to manifest_update_record and moved_item_records to moved_items_record.",
                "If any step permission read_files, write_files, run_commands, or web_research is true, set that step.max_tool_calls to a positive integer.",
                "Treat replan_request.completed_step_ids as the authoritative list of previous steps that completed successfully.",
                "Ensure budget covers step totals and includes max_tool_calls, max_model_calls, max_workers, and max_retries.",
                "Ensure artifact dependencies reference earlier outputs from this repaired plan or carryover_artifact_ids.",
                "Ensure mutation plans include discovery/design/mutate/verify/finalize safety contracts.",
                "Ensure multi-file scaffolding or workspace cleanup plans use filesystem_worker with batch-friendly scoped write artifacts.",
            ],
            "validation_errors": validation_errors,
            "previous_response": draft_response[:8000],
            "envelope": envelope.model_dump(mode="json"),
            "literal_contract": envelope.model_dump(mode="json").get("literal_contract", []),
            "current_plan": current_plan.model_dump(mode="json"),
            "replan_request": replan_request.model_dump(mode="json"),
            "carryover_artifact_ids": self._carryover_artifact_ids(replan_request),
            "carryover_artifact_cards": self._artifact_cards(replan_request.carryover_artifacts),
            "allowed_modes": ALLOWED_MODES,
            "write_scope_artifacts": WRITE_SCOPE_ARTIFACTS,
            "worker_catalog": WORKER_CATALOG,
            "artifact_contract_catalog": canonical_artifact_catalog(),
            "instruction_context_block": _instruction_context_block(repair=True),
            "plan_schema": schema,
        }
        return json.dumps(payload, sort_keys=True)

    def _carryover_artifact_ids(self, replan_request: ReplanRequest) -> list[str]:
        return sorted({canonical_artifact_id(artifact.id) for artifact in replan_request.carryover_artifacts})

    def _artifact_cards(self, artifacts: list[Any]) -> list[dict[str, Any]]:
        cards = []
        for artifact in artifacts:
            content = artifact.content
            if isinstance(content, str):
                preview = content[:500]
            else:
                preview = json.dumps(content, sort_keys=True, default=str)[:500]
            cards.append(
                {
                    "id": canonical_artifact_id(artifact.id),
                    "kind": artifact.kind,
                    "producer": artifact.producer,
                    "step_id": artifact.step_id,
                    "trust_level": artifact.trust_level,
                    "content_preview": preview,
                }
            )
        return cards

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
        normalized_steps = []
        adjusted_steps = False
        for step in plan.steps:
            if step.max_tool_calls == 0 and self._step_requires_tool_budget(step.permissions):
                step = step.model_copy(update={"max_tool_calls": 1})
                adjusted_steps = True
            normalized_steps.append(step)

        if adjusted_steps:
            plan = plan.model_copy(update={"steps": normalized_steps})

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

        adjusted = adjusted or adjusted_steps
        if not adjusted:
            return plan, False
        return plan.model_copy(update={"budget": budget}), True

    def _step_requires_tool_budget(self, permissions: dict[str, Any]) -> bool:
        return any(bool(permissions.get(key, False)) for key in ("read_files", "write_files", "run_commands", "web_research"))

    def _coerce_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
