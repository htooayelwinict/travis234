"""Runtime-first AppV2.1 agent harness."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from appv21.runtime.decisions import RuntimeDecision
from appv21.runtime.pause import create_pause
from appv21.runtime.reducer import reduce_events
from appv21.runtime.services import AppV21RuntimeServices, create_appv21_runtime_services
from appv21.state.events import RuntimeEvent
from appv21.state.models import AgentState, Artifact, RequestEnvelope


class AppV21AgentRuntime:
    def __init__(self, *, root_path: str | Path, services: AppV21RuntimeServices | None = None, max_turns: int = 12) -> None:
        self.services = services or create_appv21_runtime_services(root_path=root_path)
        self.root_path = self.services.root_path
        self.max_turns = max_turns
        self.broker = self.services.broker
        self.observer = self.services.observer
        self.decomposer = self.services.decomposer
        self.planner = self.services.planner
        self.skills = self.services.skills
        self.verifier = self.services.verifier
        self.context = self.services.context
        self.artifact_validator = self.services.artifact_validator
        self.decision_validator = self.services.decision_validator
        self.state_machine = self.services.state_machine
        self.store = self.services.event_store
        self._paused_states: dict[str, AgentState] = {}

    def run(self, user_goal: str, *, constraints: list[str] | None = None) -> dict:
        request = RequestEnvelope(
            request_id=f"req_{uuid4().hex}",
            user_goal=user_goal,
            root_path=str(self.root_path),
            constraints=list(constraints or []),
        )
        state = AgentState(session_id=f"sess_{uuid4().hex}", run_id=f"run_{uuid4().hex}", request=request)
        self._apply(
            state,
            [
                RuntimeEvent(
                    "UserMessageReceived",
                    {
                        "content": user_goal,
                        "request_id": request.request_id,
                        "root_path": request.root_path,
                        "constraints": request.constraints,
                    },
                )
            ],
        )
        self.state_machine.reset_progress()
        return self._run_loop(state)

    def resume(self, pause_id: str, user_input: dict) -> dict:
        state = self._paused_states.pop(pause_id, None)
        if state is None:
            state = self._rehydrate_paused_state(pause_id)
        if state is None:
            return {"status": "failed", "reason": "pause_not_found", "details": {"pause_id": pause_id}, "events": self.store.to_dicts()}
        pending_mutation = self._pending_high_risk_mutation_decision(state, pause_id)
        self._apply(state, [RuntimeEvent("PauseResolved", {"pause_id": pause_id, "user_input": user_input})])
        if pending_mutation is not None:
            approval = self._resume_approval(user_input, pending_mutation)
            self._apply(state, [RuntimeEvent("HumanInputReceived", {"pause_id": pause_id, **approval})])
            if not approval["approved"]:
                return self._fail(state, "high_risk_mutation_rejected", {"pause_id": pause_id})
            self._apply(state, [RuntimeEvent("RunResumed", {"pause_id": pause_id, "mode": "ACT"})])
            self._apply_mutation_intent(state, pending_mutation, allow_high_risk=True)
            if state.terminal:
                return {**(state.result or {}), "events": self.store.to_dicts()}
            return self._run_loop(state)
        self._apply(state, [RuntimeEvent("RunResumed", {"pause_id": pause_id, "mode": "THINK"})])
        return self._run_loop(state)

    def _run_loop(self, state: AgentState) -> dict:
        rejected_fingerprints: dict[str, int] = {}
        for turn_index in range(self.max_turns):
            decision, rejection = self.run_turn(state, turn_index=turn_index)
            if rejection is not None:
                if rejection.startswith(("invalid_transition:", "invalid_mode:")):
                    return self._fail(state, "invalid_transition", {"decision": decision.to_dict(), "reason": rejection})
                fingerprint = f"{decision.kind}:{rejection}"
                rejected_fingerprints[fingerprint] = rejected_fingerprints.get(fingerprint, 0) + 1
                if rejected_fingerprints[fingerprint] >= 3:
                    return self._fail(state, "repeated_rejected_decision", {"decision": decision.to_dict(), "reason": rejection})
                continue
            if state.terminal:
                return {**(state.result or {}), "events": self.store.to_dicts()}

        return self._fail(state, "max_turns_exceeded", {"max_turns": self.max_turns})

    def run_turn(self, state: AgentState, *, turn_index: int = 0) -> tuple[RuntimeDecision, str | None]:
        mode_before_prompt = state.mode
        prompt_payload = self._build_prompt_payload(state)
        decision = self.services.provider.decide(prompt_payload)
        self._apply(state, [RuntimeEvent("DecisionProposed", {"turn_index": turn_index, **decision.to_dict()})])

        transition_rejection = self.state_machine.validate_transition(mode_before_prompt, decision)
        if transition_rejection is not None:
            self._restore_mode_after_rejection(state, mode_before_prompt)
            self._apply(state, [RuntimeEvent("DecisionRejected", {"decision_id": decision.decision_id, "reason": transition_rejection})])
            return decision, transition_rejection

        validation_issues = self.decision_validator.validate(decision, state)
        rejection = validation_issues[0] if validation_issues else None
        if rejection is not None:
            self._restore_mode_after_rejection(state, mode_before_prompt)
            self._apply(state, [RuntimeEvent("DecisionRejected", {"decision_id": decision.decision_id, "reason": rejection})])
            return decision, rejection

        progress_before = self._progress_snapshot(state)
        had_latest_repo_snapshot = "world://repo_snapshot/latest" in state.world.refs
        self.route_decision(state, decision)
        if decision.kind == "observe" and had_latest_repo_snapshot:
            changed = False
        else:
            changed = progress_before != self._progress_snapshot(state)
        progress_rejection = self.state_machine.record_progress(decision, changed=changed)
        if progress_rejection is not None:
            self._apply(
                state,
                [
                    RuntimeEvent(
                        "LoopProgressRejected",
                        {"decision_id": decision.decision_id, "reason": progress_rejection},
                    )
                ],
            )
            self._fail(state, "repeated_loop", {"decision": decision.to_dict(), "reason": progress_rejection})
        return decision, None

    def _restore_mode_after_rejection(self, state: AgentState, mode_before_prompt: str) -> None:
        if state.mode != mode_before_prompt:
            self._apply(state, [RuntimeEvent("ModeChanged", {"mode": mode_before_prompt})])

    def _progress_snapshot(self, state: AgentState) -> tuple[Any, ...]:
        return (
            tuple(state.world.refs),
            repr(state.plan),
            tuple(state.world.mutation_receipts),
            tuple(state.world.verification_receipts),
            tuple(state.world.artifacts),
            state.terminal,
            repr(state.result),
        )

    def _build_prompt_payload(self, state: AgentState) -> dict[str, Any]:
        self._apply(state, [RuntimeEvent("ModeChanged", {"mode": "THINK"})])
        active_skills = self.skills.active_skills(state)
        turn_context = self.context.build_turn_context(state)
        decomposition = self.decomposer.decompose(state.request)
        turn_context["decomposition"] = decomposition
        prompt_payload = self.services.prompt_builder.build(
            state=state,
            turn_context=turn_context,
            active_skills=active_skills,
            tool_specs=self.broker.tool_specs(),
        )
        prompt_payload["decomposition"] = decomposition
        self._apply(
            state,
            [
                RuntimeEvent(
                    "PromptContextPrepared",
                    {
                        "sections": sorted(prompt_payload),
                        "tool_count": len(prompt_payload["tools"]),
                        "skill_count": len(active_skills),
                        "model": self.services.model_registry.for_role("agent").__dict__,
                    },
                )
            ],
        )
        return prompt_payload

    def route_decision(self, state: AgentState, decision: RuntimeDecision) -> None:
        if decision.kind == "observe":
            self._apply(state, [RuntimeEvent("ModeChanged", {"mode": "OBSERVE"})])
            self._hook(state, "before_observe", decision.payload)
            self._record_tool_result(state, self.broker.execute_tool_call("repo_snapshot", {}))
            self._hook(state, "after_observe", decision.payload)
            return

        if decision.kind in {"tool_call", "read_file"}:
            self._apply(state, [RuntimeEvent("ModeChanged", {"mode": "OBSERVE"})])
            tool_name = str(decision.payload.get("tool_name") or decision.payload.get("tool") or ("read_file" if decision.kind == "read_file" else ""))
            raw_arguments = decision.payload.get("arguments") if "arguments" in decision.payload else decision.payload.get("params")
            arguments = raw_arguments if isinstance(raw_arguments, dict) else {}
            if decision.kind == "read_file" and "path" in decision.payload:
                arguments = {"path": decision.payload["path"]}
            result = self.broker.execute_tool_call(tool_name, arguments)
            self._record_tool_result(state, result)
            return

        if decision.kind == "plan":
            self._apply(state, [RuntimeEvent("ModeChanged", {"mode": "PLAN"})])
            self._hook(state, "before_plan", decision.payload)
            self._ensure_planner_repo_snapshot_ref(state)
            plan = self.planner.plan_next(state)
            if plan.get("needs_observation"):
                self._apply(state, [RuntimeEvent("DecisionRejected", {"decision_id": decision.decision_id, "reason": "plan_requires_observation"})])
                return
            self._hook(state, "after_plan", {"operation_count": len((plan.get("mutation_intent") or {}).get("operations") or [])})
            self._apply(
                state,
                [
                    RuntimeEvent(
                        "PlanAccepted",
                        {
                            "intent": plan.get("intent", "runtime plan"),
                            "steps": plan.get("steps", []),
                            "current_step": "ACT",
                            "unknowns": plan.get("unknowns", []),
                            "runtime_plan": plan,
                        },
                    )
                ],
            )
            return

        if decision.kind == "mutation_intent":
            self._apply_mutation_intent(state, decision)
            return

        if decision.kind == "verify":
            self._apply(state, [RuntimeEvent("ModeChanged", {"mode": "VERIFY"})])
            self._hook(state, "before_verify", decision.payload)
            verification = self.verifier.verify(root_path=self.root_path, verification_intent=decision.payload)
            verification_id = f"verify_{uuid4().hex}"
            self._apply(state, [RuntimeEvent("VerificationRecorded", {"verification_id": verification_id, **verification})])
            self._hook(state, "after_verify", {"verification_id": verification_id, "status": verification["status"]})
            if verification["status"] != "passed":
                self._fail(state, "verification_failed", verification)
            return

        if decision.kind == "compact":
            self._apply(state, [RuntimeEvent("ModeChanged", {"mode": "COMPACT"})])
            compacted = self.context.maybe_compact(state)
            if compacted:
                self._apply(state, compacted)
            else:
                self._apply(state, [RuntimeEvent("ContextCompactionRejected", {"reason": "below_threshold"})])
            return

        if decision.kind == "pause":
            pause = create_pause(
                pause_type=str(decision.payload.get("pause_type") or "runtime_pause"),
                summary=decision.reason,
                options=list(decision.payload.get("options") or []),
            )
            self._paused_states[pause.pause_id] = state
            self._apply(state, [RuntimeEvent("PauseRequested", pause.__dict__)])
            self._apply(state, [RuntimeEvent("RunPaused", {"status": "paused", "pause_id": pause.pause_id, "reason": decision.reason})])
            return

        if decision.kind == "finalize":
            self._finalize(state, payload=decision.payload)
            return

        self._apply(state, [RuntimeEvent("DecisionRejected", {"decision_id": decision.decision_id, "reason": f"unsupported_decision:{decision.kind}"})])

    def _finalize(self, state: AgentState, *, payload: dict | None = None) -> None:
        payload = payload or {}
        latest_receipt_id = next(reversed(state.world.mutation_receipts), None)
        latest_verification_id = next(reversed(state.world.verification_receipts), None)
        if latest_verification_id is None and payload.get("explicit_noop"):
            latest_verification_id = f"verify_{uuid4().hex}"
            self._apply(
                state,
                [
                    RuntimeEvent(
                        "VerificationRecorded",
                        {
                            "verification_id": latest_verification_id,
                            "status": "passed",
                            "checks": [{"name": "explicit_noop", "passed": True}],
                        },
                    )
                ],
            )
        if latest_verification_id is None:
            self._fail(state, "finalize_without_verification")
            return
        if "final_summary" not in state.world.artifacts:
            artifact = Artifact(
                artifact_id="final_summary",
                kind="final_report",
                content={
                    "status": "completed",
                    "intent": state.plan.intent if state.plan is not None else "runtime decision loop",
                    "active_skills": self.skills.active_skills(state),
                    "mutation_receipt_id": latest_receipt_id,
                    "verification_id": latest_verification_id,
                },
                producer="appv21_runtime",
                trust="runtime_verified",
                lifecycle="runtime_verified",
                evidence_refs=[ref for ref in [latest_receipt_id, latest_verification_id] if ref is not None],
            )
            issues = self.artifact_validator.validate(artifact, state)
            if issues:
                self._fail(state, "artifact_validation_failed", {"issues": issues})
                return
            self._apply(state, [RuntimeEvent("ArtifactAccepted", artifact.__dict__)])
        self._apply(state, self.context.maybe_compact(state))
        self._hook(state, "finalize", {"artifact_id": "final_summary"})
        result = {
            "status": "completed",
            "run_id": state.run_id,
            "summary": "AppV2.1 runtime-first decision loop completed.",
            "artifact_ids": list(state.world.artifacts),
            "mutation_receipts": list(state.world.mutation_receipts),
            "verification_receipts": list(state.world.verification_receipts),
        }
        self._apply(state, [RuntimeEvent("RunCompleted", result)])

    _route_decision = route_decision

    def _apply_mutation_intent(self, state: AgentState, decision: RuntimeDecision, *, allow_high_risk: bool = False) -> None:
        operations = decision.payload.get("operations") or []
        if not operations:
            self._apply(state, [RuntimeEvent("DecisionRejected", {"decision_id": decision.decision_id, "reason": "mutation_intent_has_no_operations"})])
            return
        risk = self.broker.classify_mutation_risk(operations)
        if risk["requires_human"] and not allow_high_risk:
            pause = create_pause(
                pause_type="high_risk_mutation",
                summary="High-risk mutation requires human approval before runtime may issue or apply a lease.",
                options=[
                    {
                        "label": "Approve mutation",
                        "value": "approve",
                        "risk": risk,
                        "operation_batch_id": str(decision.payload.get("operation_batch_id") or "appv21_operations"),
                    },
                    {"label": "Reject mutation", "value": "reject"},
                ],
            )
            self._paused_states[pause.pause_id] = state
            self._apply(state, [RuntimeEvent("PauseRequested", pause.__dict__)])
            self._apply(
                state,
                [
                    RuntimeEvent(
                        "RunPaused",
                        {
                            "status": "paused",
                            "pause_id": pause.pause_id,
                            "reason": "high_risk_mutation_requires_human_approval",
                            "risk": risk,
                            "pending_mutation_intent": {
                                "decision_id": decision.decision_id,
                                "reason": decision.reason,
                                "payload": decision.payload,
                                "evidence_refs": decision.evidence_refs,
                            },
                        },
                    )
                ],
            )
            return
        intent_errors = self.broker.validate_mutation_intent(operations)
        if intent_errors:
            self._apply(
                state,
                [
                    RuntimeEvent(
                        "ToolCallDenied",
                        {
                            "tool_result_id": f"toolres_{uuid4().hex}",
                            "tool_name": "derive_mutation_lease",
                            "status": "denied",
                            "trust": "runtime_owned",
                            "payload": {"errors": intent_errors},
                            "prompt_summary": {"error_count": len(intent_errors)},
                            "evidence_refs": [],
                        },
                    )
                ],
            )
            self._fail(state, "mutation_denied", {"errors": intent_errors})
            return
        self._apply(state, [RuntimeEvent("ModeChanged", {"mode": "ACT"})])
        self._hook(state, "before_mutation", {"operation_count": len(operations)})
        lease = self.broker.derive_mutation_lease(
            operation_batch_id=str(decision.payload.get("operation_batch_id") or "appv21_operations"),
            operations=operations,
        )
        if allow_high_risk:
            lease.requires_human = False
        self._apply(state, [RuntimeEvent("MutationLeaseIssued", lease.__dict__)])
        receipt = self.broker.apply_mutation_lease(lease)
        self._apply(state, [RuntimeEvent("MutationApplied", receipt.__dict__)])
        self._hook(state, "after_mutation", {"receipt_id": receipt.receipt_id, "status": receipt.status})
        if receipt.status != "applied":
            self._fail(state, "mutation_failed", {"errors": receipt.errors})

    def _pending_high_risk_mutation_decision(self, state: AgentState, pause_id: str) -> RuntimeDecision | None:
        result = state.result or {}
        if result.get("pause_id") != pause_id or result.get("reason") != "high_risk_mutation_requires_human_approval":
            return None
        pending = result.get("pending_mutation_intent")
        if not isinstance(pending, dict):
            return None
        return RuntimeDecision(
            kind="mutation_intent",
            reason=str(pending.get("reason") or "approved high-risk mutation"),
            payload=dict(pending.get("payload") or {}),
            evidence_refs=list(pending.get("evidence_refs") or []),
            decision_id=str(pending.get("decision_id") or f"decision_{uuid4().hex}"),
        )

    def _resume_approval(self, user_input: dict, decision: RuntimeDecision) -> dict[str, Any]:
        operation_batch_id = str(decision.payload.get("operation_batch_id") or "appv21_operations")
        approval = user_input.get("approval")
        approved = set(user_input) == {"approval"} and approval == f"approve:{operation_batch_id}"
        return {
            "input_type": "high_risk_mutation_approval",
            "operation_batch_id": operation_batch_id,
            "value": approval,
            "approved": approved,
        }

    def _apply(self, state: AgentState, events: list[RuntimeEvent]) -> None:
        self.store.extend(events)
        for event in events:
            self.services.event_bus.publish(event)
            self.services.session_store.append_event(session_id=state.session_id, run_id=state.run_id, event=event)
        reduce_events(state, events)

    def _record_tool_result(self, state: AgentState, result: dict[str, Any]) -> None:
        if result["status"] == "denied":
            self._apply(state, [RuntimeEvent("ToolCallDenied", result)])
            return
        self._apply(state, [RuntimeEvent("ToolCallCompleted", result)])
        ref_events = [
            RuntimeEvent(
                "WorldRefAdded",
                {
                    "ref_id": f"world://tool_result/{result['tool_result_id']}",
                    "kind": "tool_result",
                    "summary": json_summary(result.get("prompt_summary") or {}),
                    "payload": result,
                    "trust": result.get("trust", "runtime_observed"),
                },
            )
        ]
        if result.get("tool_name") == "repo_snapshot":
            ref_events.append(
                RuntimeEvent(
                    "WorldRefAdded",
                    {
                        "ref_id": "world://repo_snapshot/latest",
                        "kind": "repo_snapshot",
                        "summary": json_summary(result.get("prompt_summary") or {}),
                        "payload": dict(result.get("payload") or {}),
                        "trust": result.get("trust", "runtime_observed"),
                    },
                )
            )
        self._apply(state, ref_events)

    def _rehydrate_paused_state(self, pause_id: str) -> AgentState | None:
        rows = self.services.session_store.read_all()
        paused_row_index: int | None = None
        session_id: str | None = None
        run_id: str | None = None
        for index, row in enumerate(rows):
            payload = row.get("payload") or {}
            if row.get("event_type") == "RunPaused" and payload.get("pause_id") == pause_id:
                paused_row_index = index
                session_id = str(row.get("session_id") or "")
                run_id = str(row.get("run_id") or "")
            elif row.get("event_type") == "PauseResolved" and payload.get("pause_id") == pause_id:
                paused_row_index = None
                session_id = None
                run_id = None
        if paused_row_index is None or not session_id or not run_id:
            return None

        run_rows = [row for row in rows[: paused_row_index + 1] if row.get("session_id") == session_id and row.get("run_id") == run_id]
        first_user = next((row for row in run_rows if row.get("event_type") == "UserMessageReceived"), None)
        request_payload = dict((first_user or {}).get("payload") or {})
        request = RequestEnvelope(
            request_id=str(request_payload.get("request_id") or f"req_rehydrated_{run_id}"),
            user_goal=str(request_payload.get("content") or ""),
            root_path=str(request_payload.get("root_path") or self.root_path),
            constraints=list(request_payload.get("constraints") or []),
        )
        state = AgentState(session_id=session_id, run_id=run_id, request=request)
        events = self.services.session_store.events_for_run(session_id=session_id, run_id=run_id)[: len(run_rows)]
        self.store.extend(events)
        reduce_events(state, events)
        if not state.pauses or state.pauses[-1].pause_id != pause_id or state.result is None or state.result.get("status") != "paused":
            return None
        return state

    def _ensure_planner_repo_snapshot_ref(self, state: AgentState) -> None:
        if "world://repo_snapshot/latest" in state.world.refs:
            return
        repo_tool_refs = [
            ref
            for ref in state.world.refs.values()
            if ref.kind == "tool_result" and ref.payload.get("tool_name") == "repo_snapshot" and isinstance(ref.payload.get("payload"), dict)
        ]
        if not repo_tool_refs:
            return
        latest = repo_tool_refs[-1]
        self._apply(
            state,
            [
                RuntimeEvent(
                    "WorldRefAdded",
                    {
                        "ref_id": "world://repo_snapshot/latest",
                        "kind": "repo_snapshot",
                        "summary": latest.summary,
                        "payload": dict(latest.payload["payload"]),
                        "trust": latest.trust,
                    },
                )
            ],
        )

    def _hook(self, state: AgentState, hook: str, payload: dict) -> None:
        self._apply(state, self.services.extension_runner.run_hook(hook, state, payload))

    def _fail(self, state: AgentState, reason: str, details: dict | None = None) -> dict:
        result = {"status": "failed", "reason": reason, "details": details or {}}
        self._apply(state, [RuntimeEvent("RunFailed", result)])
        return {**(state.result or result), "events": self.store.to_dicts()}


def json_summary(payload: dict[str, Any]) -> str:
    parts = [f"{key}={payload[key]}" for key in sorted(payload)[:4]]
    return ", ".join(parts) if parts else "tool result"
