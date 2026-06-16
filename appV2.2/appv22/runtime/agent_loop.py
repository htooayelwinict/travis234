from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from appv22.runtime.reducer import apply_event
from appv22.runtime.services import AppV22Services
from appv22.state.events import RuntimeEvent
from appv22.state.models import AgentState, RequestEnvelope


class AppV22AgentRuntime:
    def __init__(self, *, root_path: str | Path, services: AppV22Services, max_turns: int = 12) -> None:
        self.root_path = Path(root_path)
        self.services = services
        self.max_turns = max_turns
        self.events: list[RuntimeEvent] = []

    def run(self, user_goal: str) -> dict:
        state = AgentState(
            f"sess_{uuid4().hex}",
            f"run_{uuid4().hex}",
            RequestEnvelope(f"req_{uuid4().hex}", user_goal, str(self.root_path)),
        )
        for turn_index in range(self.max_turns):
            resolved = self.services.extension_registry.resolve_active(state)
            state.active_extension_ids = list(resolved.extension_ids)
            state.active_skill_ids = [card.skill_id for card in resolved.skill_cards]
            selected = self.services.context_selector.select(state, resolved, pre_turn_mode=state.mode)
            prompt = self.services.prompt_builder.build(state, selected)
            provider_prompt = self._provider_bound_prompt(state, prompt)
            try:
                decision = self.services.provider.decide(provider_prompt)
                self._apply(state, RuntimeEvent("DecisionProposed", {"turn_index": turn_index, **decision.to_dict()}))
                self._route(state, decision, resolved)
            except Exception as exc:
                self._apply(
                    state,
                    RuntimeEvent(
                        "RunFailed",
                        {
                            "status": "failed",
                            "reason": "runtime_loop_error",
                            "error_type": type(exc).__name__,
                            "message": str(exc),
                        },
                    ),
                )
            if state.terminal:
                return {**state.result, "events": [event.to_dict() for event in self.events]}
        self._apply(state, RuntimeEvent("RunFailed", {"status": "failed", "reason": "max_turns_exceeded"}))
        return {**state.result, "events": [event.to_dict() for event in self.events]}

    def _route(self, state, decision, resolved) -> None:
        if decision.kind == "tool_call":
            tool_id = decision.payload.get("tool_id") if isinstance(decision.payload, dict) else None
            preferred_observation_tool_id = self._preferred_observation_tool_id(resolved)
            if (
                self._observation_contract_satisfied(state, resolved)
                and not self._is_explicit_rehydration_request(decision)
                and (
                not isinstance(tool_id, str)
                or not tool_id
                or tool_id == preferred_observation_tool_id
                )
            ):
                self._record_runtime_guidance(
                    state,
                    progress=(
                        "Observation already satisfied by durable evidence_refs; proceed in PLAN mode. "
                        "Do not repeat broad observation. Emit proposed_artifact.path and "
                        "proposed_artifact.content or a concrete mutation_intent.operations payload."
                    ),
                )
                self._apply(state, RuntimeEvent("ModeChanged", {"mode": "PLAN"}))
                return
            if not isinstance(tool_id, str) or not tool_id:
                repaired_tool_id = preferred_observation_tool_id
                if repaired_tool_id:
                    tool_id = repaired_tool_id
                    decision_payload = deepcopy(decision.payload) if isinstance(decision.payload, dict) else {}
                    decision_payload["tool_id"] = tool_id
                    decision_payload.setdefault("arguments", {})
                    decision = type(decision)(
                        kind=decision.kind,
                        reason=f"{decision.reason} Runtime repaired missing tool_id from active tools.",
                        payload=decision_payload,
                        evidence_refs=list(decision.evidence_refs),
                        decision_id=decision.decision_id,
                    )
                else:
                    self._apply(
                        state,
                        RuntimeEvent(
                            "RunFailed",
                            {
                                "status": "failed",
                                "reason": "malformed_tool_call",
                                "message": "tool_call decision missing tool_id",
                                "payload": deepcopy(decision.payload),
                            },
                        ),
                    )
                    return
            if not isinstance(tool_id, str) or not tool_id:
                self._apply(
                    state,
                    RuntimeEvent(
                        "RunFailed",
                        {
                            "status": "failed",
                            "reason": "malformed_tool_call",
                            "message": "tool_call decision missing tool_id",
                            "payload": deepcopy(decision.payload),
                        },
                    ),
                )
                return
            self._apply(state, RuntimeEvent("ModeChanged", {"mode": "OBSERVE"}))
            result = self.services.broker.execute(
                tool_id,
                decision.payload.get("arguments", {}),
                active_tool_ids=resolved.tool_ids,
            )
            event_type = "ToolCallCompleted" if result["status"] == "completed" else "ToolCallDenied"
            self._apply(state, RuntimeEvent(event_type, result))
            if result["status"] == "completed":
                self._apply(
                    state,
                    RuntimeEvent(
                        "WorldRefAdded",
                        {
                            "ref_id": result.get("payload_ref") or f"world://{result['tool_id']}/latest",
                            "kind": result["tool_id"],
                            "payload": result["payload"],
                            "summary": f"{result['tool_id']} result",
                        },
                    ),
                )
            return
        if decision.kind == "plan":
            self._apply(state, RuntimeEvent("ModeChanged", {"mode": "PLAN"}))
            planner_id = self._single(resolved.planner_ids, "planner")
            planner = self.services.capability_registry.planner(planner_id)
            plan = self._plan_with_model_payload(planner, state, decision.payload)
            if plan is None:
                self._record_runtime_guidance(
                    state,
                    open_risk=(
                        "Plan was non-executable. Produce a repaired plan with proposed_artifact.path "
                        "and proposed_artifact.content, or mutation_intent.operations. Cite existing evidence_refs."
                    ),
                )
                return
            self._apply(state, RuntimeEvent("PlanAccepted", plan))
            return
        if decision.kind == "mutation_intent":
            self._apply(state, RuntimeEvent("ModeChanged", {"mode": "ACT"}))
            policy_id = self._active_capability_id(
                state.runtime_plan.get("mutation_policy_id"),
                resolved.mutation_policy_ids,
                "mutation_policy",
            )
            executor_id = self._active_capability_id(
                state.runtime_plan.get("mutation_executor_id"),
                resolved.mutation_executor_ids,
                "mutation_executor",
            )
            policy = self.services.capability_registry.mutation_policy(policy_id)
            executor = self.services.capability_registry.mutation_executor(executor_id)
            mutation_intent = decision.payload if decision.payload.get("operations") else state.runtime_plan.get("mutation_intent", {})
            operations = mutation_intent["operations"]
            errors = policy.validate(operations, root_path=self.root_path)
            if errors:
                self._apply(
                    state,
                    RuntimeEvent(
                        "RunFailed",
                        {"status": "failed", "reason": "mutation_denied", "errors": errors},
                    ),
                )
                return
            lease_id = f"lease_{uuid4().hex}"
            self._apply(
                state,
                RuntimeEvent(
                    "MutationLeaseIssued",
                    {
                        "lease_id": lease_id,
                        "operation_batch_id": mutation_intent["operation_batch_id"],
                        "allowed_operations": operations,
                    },
                ),
            )
            applied = executor.apply(operations, root_path=self.root_path)
            if applied.get("status") not in {"applied", "completed"}:
                self._apply(
                    state,
                    RuntimeEvent(
                        "RunFailed",
                        {
                            "status": "failed",
                            "reason": "mutation_apply_failed",
                            "errors": applied.get("errors", []),
                        },
                    ),
                )
                return
            self._apply(
                state,
                RuntimeEvent(
                    "MutationApplied",
                    {
                        "receipt_id": f"mut_{mutation_intent['operation_batch_id']}",
                        "lease_id": lease_id,
                        "operations": operations,
                        **applied,
                    },
                ),
            )
            return
        if decision.kind in {"verify", "finalize"}:
            verifier_id = self._active_capability_id(
                state.runtime_plan.get("verifier_id"),
                resolved.verifier_ids,
                "verifier",
            )
            verifier = self.services.capability_registry.verifier(verifier_id)
            verification = verifier.verify(
                root_path=self.root_path,
                verification_intent=state.runtime_plan["verification_intent"],
            )
            verification_id = f"verify_{uuid4().hex}"
            self._apply(
                state,
                RuntimeEvent("VerificationRecorded", {"verification_id": verification_id, **verification}),
            )
            if verification["status"] != "passed":
                self._apply(state, RuntimeEvent("RunFailed", {"status": "failed", "reason": "verification_failed"}))
                return
            self._apply(
                state,
                RuntimeEvent(
                    "RunCompleted",
                    {
                        "status": "completed",
                        "mutation_receipts": list(state.mutation_receipts.values()),
                        "verification_receipts": list(state.verification_receipts.values()),
                    },
                ),
            )
            return
        if decision.kind == "pause":
            self._apply(state, RuntimeEvent("RunFailed", {"status": "failed", "reason": "paused"}))
            return
        if decision.kind == "compact":
            self._apply(state, RuntimeEvent("ModeChanged", {"mode": "COMPACT"}))
            return
        self._apply(state, RuntimeEvent("RunFailed", {"status": "failed", "reason": "unsupported_decision"}))

    def _apply(self, state, event: RuntimeEvent) -> None:
        self.events.append(event)
        apply_event(state, event)

    def _plan_with_model_payload(self, planner, state, payload: dict) -> dict:
        try:
            return planner.plan(state, decision_payload=payload)
        except ValueError as exc:
            if str(exc) == "model_authored_plan_required":
                return None
            raise
        except TypeError:
            return planner.plan(state)

    def _provider_bound_prompt(self, state, prompt: dict) -> dict:
        messages = self._provider_prompt_messages(state, prompt)
        guarded = self.services.gateway_guard.guard(messages)
        compressed = self.services.compressor.compress(guarded, previous_summary=state.context_summary)
        context_summary = self._summary_from_messages(compressed)
        if context_summary is not None and context_summary != state.context_summary:
            self._apply(state, RuntimeEvent("ContextSummaryUpdated", context_summary))
        provider_prompt = self._prompt_from_governed_messages(compressed)
        provider_prompt["messages"] = compressed
        return provider_prompt

    def _provider_prompt_messages(self, state, prompt: dict) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "name": "provider_identity",
                "content": prompt.get("system", {}).get("identity", "AppV2.2 provider context"),
                "payload": deepcopy(prompt.get("system", {})),
            }
        ]
        for section in ("agent", "state", "skills", "tools", "world", "selection"):
            payload = deepcopy(prompt.get(section, {} if section not in {"skills", "tools"} else []))
            messages.append(
                {
                    "role": "system",
                    "name": "provider_context_section",
                    "section": section,
                    "content": self._section_content(section, payload),
                    "payload": payload,
                }
            )
        messages.append(
            {
                "role": "user",
                "name": "user_goal",
                "content": state.request.user_goal,
            }
        )
        return messages

    def _section_content(self, section: str, payload: Any) -> str:
        return f"{section}: {json.dumps(payload, sort_keys=True, default=str)}"

    def _prompt_from_governed_messages(self, messages: list[dict[str, Any]]) -> dict:
        provider_prompt: dict[str, Any] = {
            "system": {},
            "agent": {},
            "state": {
                "mode": None,
                "runtime_plan": {},
                "mutation_receipts": {},
                "verification_receipts": {},
                "context_summary": {},
            },
            "skills": [],
            "tools": [],
            "world": {"world_refs": {}},
            "selection": {
                "selected_tools": [],
                "selected_skills": [],
                "active_extensions": [],
                "available_tools": [],
            },
        }
        for message in messages:
            if message.get("name") == "provider_identity" and isinstance(message.get("payload"), dict):
                provider_prompt["system"] = deepcopy(message["payload"])
                continue
            if message.get("name") != "provider_context_section":
                continue
            section = message.get("section")
            if section not in provider_prompt:
                continue
            payload = message.get("payload")
            if isinstance(payload, dict | list):
                provider_prompt[section] = deepcopy(payload)
        return provider_prompt

    def _summary_from_messages(self, messages: list[dict]) -> dict | None:
        for message in messages:
            summary = message.get("summary")
            if isinstance(summary, dict):
                return deepcopy(summary)
        return None

    def _active_capability_id(self, planned_id: str | None, active_ids: tuple[str, ...], label: str) -> str:
        if planned_id:
            if planned_id not in active_ids:
                raise ValueError(f"inactive {label}: {planned_id}")
            return planned_id
        return self._single(active_ids, label)

    def _preferred_observation_tool_id(self, resolved) -> str | None:
        for card in resolved.skill_cards:
            contract = getattr(card, "observation_contract", None)
            preferred = getattr(contract, "preferred_tool_id", None)
            if isinstance(preferred, str) and preferred in resolved.tool_ids:
                return preferred
        return resolved.tool_ids[0] if resolved.tool_ids else None

    def _observation_contract_satisfied(self, state, resolved) -> bool:
        for card in resolved.skill_cards:
            contract = getattr(card, "observation_contract", None)
            if contract is None:
                continue
            evidence_refs = getattr(contract, "evidence_refs", ())
            if evidence_refs and any(ref in state.world_refs for ref in evidence_refs):
                return True
            evidence_kinds = getattr(contract, "evidence_kinds", ())
            if evidence_kinds and any(
                world_ref.get("kind") in evidence_kinds
                for world_ref in state.world_refs.values()
                if isinstance(world_ref, dict)
            ):
                return True
        return False

    def _is_explicit_rehydration_request(self, decision) -> bool:
        payload_text = json.dumps(decision.payload, sort_keys=True, default=str).lower()
        reason = str(getattr(decision, "reason", "")).lower()
        text = f"{reason} {payload_text}"
        return "rehydrat" in text or "recover exact" in text or "exact workspace evidence" in text

    def _record_runtime_guidance(
        self,
        state,
        *,
        progress: str | None = None,
        open_risk: str | None = None,
    ) -> None:
        summary = deepcopy(state.context_summary)
        evidence_refs = summary.setdefault("evidence_refs", [])
        for ref_id in state.world_refs:
            if ref_id not in evidence_refs:
                evidence_refs.append(ref_id)
        if progress:
            progress_items = summary.setdefault("progress", [])
            if progress not in progress_items:
                progress_items.append(progress)
        if open_risk:
            open_risks = summary.setdefault("open_risks", [])
            if open_risk not in open_risks:
                open_risks.append(open_risk)
        self._apply(state, RuntimeEvent("ContextSummaryUpdated", summary))

    def _single(self, values: tuple[str, ...], label: str) -> str:
        if len(values) != 1:
            raise ValueError(f"expected exactly one active {label}, got {len(values)}")
        return values[0]
