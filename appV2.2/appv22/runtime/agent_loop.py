from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from appv22.context.freshness import is_world_ref_fresh
from appv22.context.summary_hygiene import (
    drop_unavailable_tool_risks,
    is_durable_blocker,
    is_turn_local_repair_risk,
    normalized_context_summary,
    resolve_tool_risks_after_success,
    resolve_tool_risks_from_world_refs,
    strip_turn_local_repair_risks,
)
from appv22.runtime.decisions import KNOWN_DECISION_KINDS, RuntimeDecision
from appv22.runtime.reducer import apply_event
from appv22.runtime.services import AppV22Services
from appv22.state.events import RuntimeEvent
from appv22.state.models import AgentState, RequestEnvelope


def _active_request_text(state: AgentState) -> str:
    return state.request.active_user_request or state.request.user_goal


class AppV22AgentRuntime:
    """Pi-style agent loop with Hermes-style context governance.

    The runtime is intentionally one loop:
    user goal -> model decision -> optional tool execution -> tool result context -> next model decision.

    Planning is model reasoning inside the turn. The runtime does not compile hidden actions.
    Workspace changes happen only through selected tools.
    """

    def __init__(
        self,
        *,
        root_path: str | Path,
        services: AppV22Services,
        max_turns: int = 12,
        provider_retry_attempts: int = 1,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.root_path = Path(root_path)
        self.services = services
        self.max_turns = max_turns
        self.provider_retry_attempts = max(0, provider_retry_attempts)
        self.event_sink = event_sink
        self.events: list[RuntimeEvent] = []

    def run(
        self,
        user_goal: str,
        *,
        active_user_request: str | None = None,
        ui_context: dict[str, Any] | None = None,
    ) -> dict:
        state = AgentState(
            f"sess_{uuid4().hex}",
            f"run_{uuid4().hex}",
            RequestEnvelope(
                f"req_{uuid4().hex}",
                user_goal,
                str(self.root_path),
                active_user_request=active_user_request or user_goal,
                ui_context=dict(ui_context or {}),
            ),
        )
        return self._run_state(state)

    def continue_run(
        self,
        previous_result: dict[str, Any],
        user_goal: str,
        *,
        active_user_request: str | None = None,
        ui_context: dict[str, Any] | None = None,
    ) -> dict:
        state = AgentState(
            str(previous_result.get("session_id") or f"sess_{uuid4().hex}"),
            f"run_{uuid4().hex}",
            RequestEnvelope(
                f"req_{uuid4().hex}",
                user_goal,
                str(self.root_path),
                active_user_request=active_user_request or user_goal,
                ui_context=dict(ui_context or {}),
            ),
        )
        world_refs = previous_result.get("world_refs")
        if isinstance(world_refs, dict):
            state.world_refs = deepcopy(world_refs)
        context_summary = previous_result.get("context_summary")
        if isinstance(context_summary, dict):
            state.context_summary = resolve_tool_risks_from_world_refs(context_summary, state.world_refs)
        return self._run_state(state)

    def _run_state(self, state: AgentState) -> dict:
        self.events = []
        self._apply(state, RuntimeEvent("AgentStarted", {"status": "started"}))

        for turn_index in range(self.max_turns):
            resolved = self.services.extension_registry.resolve_active(state)
            state.active_extension_ids = list(resolved.extension_ids)
            state.active_skill_ids = [card.skill_id for card in resolved.skill_cards]
            state.context_summary = resolve_tool_risks_from_world_refs(
                strip_turn_local_repair_risks(drop_unavailable_tool_risks(state.context_summary, resolved.tool_ids)),
                state.world_refs,
            )

            if self.services.context_harness is None:
                raise RuntimeError("context_harness_not_configured")
            packet = self.services.context_harness.prepare_turn(state, resolved, pre_turn_mode=state.mode)
            if packet.context_summary_update is not None and packet.context_summary_update != state.context_summary:
                self._apply(
                    state,
                    RuntimeEvent(
                        "ContextSummaryUpdated",
                        self._merge_context_summaries(state.context_summary, packet.context_summary_update),
                    ),
                )
            provider_prompt = packet.provider_prompt
            selected_tool_ids = provider_prompt.get("selection", {}).get("selected_tools", [])
            state.active_tool_ids = [tool_id for tool_id in selected_tool_ids if isinstance(tool_id, str)]

            try:
                decision = self._decide_with_provider_retries(state, provider_prompt)
                decision = self._repair_decision_shape(decision, state.active_tool_ids)
                self._validate_decision(decision)
                self._apply(state, RuntimeEvent("DecisionProposed", self._decision_event_payload(turn_index, decision)))
                self._route(state, decision, resolved)
            except Exception as exc:  # noqa: BLE001 - runtime converts loop errors into terminal state.
                self._apply(
                    state,
                    RuntimeEvent(
                        "RunFailed",
                        self._fail_payload(
                            state,
                            reason="runtime_loop_error",
                            error_type="runtime_exception",
                            message="runtime loop failed before a safe decision was available",
                        ),
                    ),
                )
            if state.terminal:
                return {**state.result, "events": [event.to_dict() for event in self.events]}

        self._apply(state, RuntimeEvent("RunFailed", self._fail_payload(state, reason="max_turns_exceeded")))
        return {**state.result, "events": [event.to_dict() for event in self.events]}

    def _decide_with_provider_retries(self, state: AgentState, provider_prompt: dict) -> Any:
        attempts = self.provider_retry_attempts + 1
        last_error: Exception | None = None
        for attempt_index in range(attempts):
            try:
                return self.services.provider.decide(provider_prompt)
            except Exception as exc:  # noqa: BLE001 - provider failures are sanitized before entering state.
                last_error = exc
                self._apply(
                    state,
                    RuntimeEvent(
                        "ProviderCallFailed",
                        {
                            "status": "failed",
                            "reason": "provider_decision_error",
                            "attempt": attempt_index + 1,
                            "will_retry": attempt_index + 1 < attempts,
                        },
                    ),
                )
        if last_error is not None:
            raise last_error
        raise RuntimeError("provider_decision_error")

    def _route(self, state: AgentState, decision, resolved) -> None:
        if decision.kind == "tool_call":
            self._handle_tool_call(state, decision, resolved)
            return

        if decision.kind == "finalize":
            self._complete_from_tool_loop(
                state,
                reason="tool_loop_completed",
                assistant_message=self._assistant_message_from_decision(decision),
            )
            return

        if decision.kind == "pause":
            if self._has_completed_non_observe_tool(state):
                self._complete_from_tool_loop(state, reason="tool_loop_completed")
            else:
                self._apply(state, RuntimeEvent("RunFailed", self._fail_payload(state, reason="paused")))
            return

        if decision.kind == "compact":
            unresolved_feedback = self._latest_unresolved_tool_feedback(state)
            if unresolved_feedback is not None:
                guidance_messages = self._tool_result_guidance(resolved, unresolved_feedback)
                self._record_tool_recovery_guidance(state, guidance_messages)
                self._record_runtime_guidance(
                    state,
                    open_risk=(
                        "Recent tool feedback remains unresolved; compacting again is not an action. "
                        "The next decision should call a selected tool with corrected arguments, choose another selected tool, "
                        "or finalize only if existing evidence already proves completion. "
                        f"Last tool feedback: {unresolved_feedback.get('tool_id')} was {unresolved_feedback.get('status')}."
                    ),
                )
                return
            self._record_runtime_guidance(state, progress="Model requested compaction; Hermes context transform remains active.")
            return

        self._apply(state, RuntimeEvent("RunFailed", self._fail_payload(state, reason="unsupported_decision")))

    def _handle_tool_call(self, state: AgentState, decision, resolved) -> None:
        payload = decision.payload if isinstance(decision.payload, dict) else {}
        tool_id = payload.get("tool_id")
        arguments = payload.get("arguments", {})

        if not isinstance(tool_id, str) or not tool_id:
            if "tool_call" in payload or not state.active_tool_ids:
                if not state.active_tool_ids and self._complete_from_latest_observe_result(state):
                    return
                self._apply(
                    state,
                    RuntimeEvent(
                        "RunFailed",
                        self._fail_payload(
                            state,
                            reason="malformed_tool_call",
                            message="tool_call decision missing tool_id",
                            payload=deepcopy(payload),
                        ),
                    ),
                )
                return
            self._apply(state, RuntimeEvent("ModeChanged", {"mode": "THINK"}))
            self._record_runtime_guidance(
                state,
                turn_feedback=(
                    "Malformed tool_call decision was missing payload.tool_id; "
                    "treated as turn-local provider repair feedback. Continue from selected tools or existing evidence."
                ),
                )
            return

        if not state.active_tool_ids and self._complete_from_latest_observe_result(state):
            return

        if tool_id.lower() in {"none", "null", "no_tool", "no-op", "noop"}:
            if self._has_completed_non_observe_tool(state):
                self._complete_from_tool_loop(state, reason="tool_loop_completed")
            else:
                self._apply(
                    state,
                    RuntimeEvent(
                        "RunFailed",
                        self._fail_payload(
                            state,
                            reason="invalid_tool_call",
                            message=f"invalid tool_id: {tool_id}",
                        ),
                    ),
                )
            return

        if self._tool_call_evidence_already_exists(state, tool_id, arguments):
            if not self._is_observe_tool(tool_id):
                self._record_runtime_guidance(
                    state,
                    progress="Duplicate completed tool call suppressed; existing tool result already proves the requested action.",
                )
                self._complete_from_tool_loop(state, reason="tool_loop_completed")
                return

        existing_denial = self._existing_tool_call_denial(state, tool_id, arguments)
        if existing_denial is not None:
            self._apply(state, RuntimeEvent("ModeChanged", {"mode": "THINK"}))
            guidance_messages = self._tool_result_guidance(resolved, existing_denial)
            self._apply_named_recovery_tool_mode(state, resolved, tool_id, guidance_messages)
            self._record_named_recovery_tool_guidance(state, resolved, tool_id, guidance_messages)
            guidance_detail = f" {' '.join(guidance_messages)}" if guidance_messages else ""
            self._record_runtime_guidance(
                state,
                open_risk=(
                    f"Tool request denied previously for {tool_id} with the same arguments; "
                    "do not retry with identical arguments. "
                    "If extension or payload guidance gives corrected arguments, call the selected tool again only with corrected arguments. "
                    "Otherwise continue from public evidence or choose another selected tool."
                    f"{guidance_detail}"
                ),
            )
            return

        is_observe_tool = self._is_observe_tool(tool_id)
        self._apply(state, RuntimeEvent("ModeChanged", {"mode": "OBSERVE" if is_observe_tool else "ACT"}))
        pre_tool_result = self._before_tool_call(state, resolved, tool_id, arguments)
        if pre_tool_result is not None:
            result = self._tool_call_denied_by_extension(tool_id, arguments, pre_tool_result)
            self._record_tool_result(state, result)
            self._redact_denied_argument_values_from_world_refs(state, arguments)
            self._apply(state, RuntimeEvent("ModeChanged", {"mode": "THINK"}))
            guidance_messages = self._tool_result_guidance(resolved, result)
            self._apply_named_recovery_tool_mode(state, resolved, tool_id, guidance_messages)
            self._record_tool_recovery_guidance(state, guidance_messages)
            self._record_named_recovery_tool_guidance(state, resolved, tool_id, guidance_messages)
            self._record_tool_payload_error_guidance(state, result)
            reason = str(pre_tool_result.get("reason") or "extension_pre_tool_block")
            self._record_runtime_guidance(
                state,
                open_risk=(
                    f"{tool_id} request was denied before execution by extension policy ({reason}); "
                    "This denied pre-tool attempt already satisfies any instruction to exercise a guard or blocked-call path. "
                    "Do not retry the same blocked call. Continue from public evidence, call the selected tool with corrected arguments, "
                    "or choose another selected tool."
                ),
            )
            return
        result = self._execute_tool_call(
            state,
            resolved,
            tool_id=tool_id,
            arguments=arguments,
            active_tool_ids=state.active_tool_ids,
        )
        if is_observe_tool and result.get("status") == "completed":
            self._apply(state, RuntimeEvent("ModeChanged", {"mode": "THINK"}))
        elif result.get("status") in {"denied", "failed"}:
            self._apply(state, RuntimeEvent("ModeChanged", {"mode": "THINK"}))
            guidance_messages = self._tool_result_guidance(resolved, result)
            self._apply_named_recovery_tool_mode(state, resolved, tool_id, guidance_messages)
            self._record_tool_recovery_guidance(state, guidance_messages)
            self._record_named_recovery_tool_guidance(state, resolved, tool_id, guidance_messages)
            self._record_tool_payload_error_guidance(state, result)
            payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
            if result.get("status") == "failed" and payload.get("retryable") is True:
                default_guidance = "treat that retryable failure as evidence and follow extension/tool guidance for recovery."
            elif result.get("status") == "failed":
                default_guidance = "treat that failure as evidence and continue without repeating the same failed call."
            else:
                default_guidance = "treat that denial as evidence and continue without retrying the same denied call."
            argument_keys = sorted(arguments.keys()) if isinstance(arguments, dict) else []
            self._record_runtime_guidance(
                state,
                open_risk=(
                    f"{tool_id} request was {result.get('status')} for argument keys "
                    f"{argument_keys}; "
                    f"{default_guidance}"
                ),
            )
    def _execute_tool_call(self, state: AgentState, resolved, *, tool_id: str, arguments: Any, active_tool_ids) -> dict:
        if not isinstance(arguments, dict):
            arguments = {}
        result = self.services.broker.execute(
            tool_id,
            arguments,
            active_tool_ids=active_tool_ids,
            request_context={
                "request_id": state.request.request_id,
                "user_goal": state.request.user_goal,
                "active_user_request": state.request.active_user_request,
                "ui_context": deepcopy(state.request.ui_context),
                "mode": state.mode,
            },
        )
        result["arguments"] = deepcopy(arguments)
        result = self._after_tool_call(state, resolved, result)
        if self.services.context_harness is not None:
            self.services.context_harness.record_tool_result(state, result)
        self._record_tool_result(state, result)
        return result

    def _record_tool_result(self, state: AgentState, result: dict[str, Any]) -> None:
        event_type = {
            "completed": "ToolCallCompleted",
            "failed": "ToolCallFailed",
        }.get(str(result.get("status")), "ToolCallDenied")
        self._apply(state, RuntimeEvent(event_type, result))
        if result["status"] == "completed":
            resolved_summary = resolve_tool_risks_after_success(state.context_summary, str(result.get("tool_id") or ""))
            if resolved_summary != self._normalized_context_summary(state.context_summary):
                self._apply(state, RuntimeEvent("ContextSummaryUpdated", resolved_summary))
            arguments = result.get("arguments") if isinstance(result.get("arguments"), dict) else {}
            definition = self._tool_definition(str(result.get("tool_id") or ""))
            ref_id = result.get("payload_ref")
            if not isinstance(ref_id, str) or not ref_id:
                return
            self._apply(
                state,
                RuntimeEvent(
                    "WorldRefAdded",
                    {
                        "ref_id": ref_id,
                        "kind": result["tool_id"],
                        "arguments": deepcopy(arguments),
                        "payload": result["payload"],
                        "summary": f"{result['tool_id']} result",
                        "request_id": state.request.request_id,
                        "run_id": state.run_id,
                        "mutation_seq": state.mutation_seq,
                        "freshness": getattr(definition, "freshness", "stable") if definition is not None else "stable",
                    },
                ),
            )

    def _before_tool_call(self, state: AgentState, resolved, tool_id: str, arguments: Any) -> dict[str, Any] | None:
        safe_arguments = deepcopy(arguments) if isinstance(arguments, dict) else {}
        return self.services.extension_registry.before_tool_call(resolved.extension_ids, state, tool_id, safe_arguments)

    def _after_tool_call(self, state: AgentState, resolved, result: dict[str, Any]) -> dict[str, Any]:
        transformed = self.services.extension_registry.after_tool_call(resolved.extension_ids, state, result)
        if not isinstance(transformed, dict) or not transformed:
            return result
        transformed.setdefault("tool_result_id", result.get("tool_result_id"))
        transformed.setdefault("tool_id", result.get("tool_id"))
        transformed.setdefault("status", result.get("status"))
        transformed.setdefault("payload", deepcopy(result.get("payload", {})))
        transformed.setdefault("payload_ref", result.get("payload_ref", ""))
        transformed.setdefault("evidence_refs", deepcopy(result.get("evidence_refs", [])))
        transformed.setdefault("arguments", deepcopy(result.get("arguments", {})))
        transformed = self._validate_after_tool_result(transformed)
        return transformed

    def _validate_after_tool_result(self, result: dict[str, Any]) -> dict[str, Any]:
        if result.get("status") != "completed":
            return result
        tool_id = result.get("tool_id")
        payload = result.get("payload")
        if not isinstance(tool_id, str) or not isinstance(payload, dict):
            return self._failed_after_tool_result(result, ["malformed_after_tool_result"])
        errors = self.services.broker.validate_result_payload(tool_id, payload)
        if not errors:
            return result
        return self._failed_after_tool_result(result, errors)

    def _failed_after_tool_result(self, result: dict[str, Any], errors: list[str]) -> dict[str, Any]:
        failed = dict(result)
        failed["status"] = "failed"
        failed["payload"] = {"errors": [str(error) for error in errors], "reason": "after_tool_result_schema_invalid"}
        failed["payload_ref"] = ""
        failed["evidence_refs"] = []
        return failed

    def _tool_call_denied_by_extension(self, tool_id: str, arguments: Any, pre_tool_result: dict[str, Any]) -> dict[str, Any]:
        errors = pre_tool_result.get("errors")
        if not isinstance(errors, list) or not errors:
            errors = ["extension_pre_tool_block"]
        payload = {
            "errors": [str(error) for error in errors],
            "reason": str(pre_tool_result.get("reason") or "extension_pre_tool_block"),
        }
        safe_payload = pre_tool_result.get("payload")
        if isinstance(safe_payload, dict):
            payload.update(deepcopy(safe_payload))
        return {
            "tool_result_id": f"toolres_pre_{uuid4().hex[:12]}",
            "tool_id": tool_id,
            "status": "denied",
            "payload": payload,
            "payload_ref": "",
            "evidence_refs": [],
            "arguments": deepcopy(arguments) if isinstance(arguments, dict) else {},
        }

    def _validate_decision(self, decision) -> None:
        if getattr(decision, "kind", None) not in KNOWN_DECISION_KINDS:
            raise ValueError("provider returned unsupported decision kind")

    def _decision_event_payload(self, turn_index: int, decision) -> dict[str, Any]:
        """Durable telemetry must not persist raw model rationale or argument content.

        The raw decision remains in memory for routing. Events are durable context and can
        be reported, compacted, or carried across turns, so they use a safe envelope like
        Pi/Hermes-style loop telemetry rather than storing model thoughts verbatim.
        """

        payload = getattr(decision, "payload", None)
        safe_payload: dict[str, Any] = {"shape": type(payload).__name__}
        if isinstance(payload, dict):
            tool_id = payload.get("tool_id")
            arguments = payload.get("arguments")
            safe_payload = {
                "shape": "dict",
                "has_tool_id": isinstance(tool_id, str) and bool(tool_id),
                "tool_id": tool_id if isinstance(tool_id, str) else None,
                "argument_keys": sorted(arguments.keys()) if isinstance(arguments, dict) else [],
            }
        return {
            "turn_index": turn_index,
            "decision_id": getattr(decision, "decision_id", None),
            "kind": getattr(decision, "kind", None),
            "reason": "model_decision",
            "payload": safe_payload,
            "evidence_refs": list(getattr(decision, "evidence_refs", []) or []),
        }

    def _repair_decision_shape(self, decision, active_tool_ids):
        payload = getattr(decision, "payload", None)
        if getattr(decision, "kind", None) != "tool_call" and isinstance(payload, dict):
            tool_id = payload.get("tool_id")
            if isinstance(tool_id, str) and tool_id in active_tool_ids:
                return RuntimeDecision(
                    kind="tool_call",
                    reason=str(getattr(decision, "reason", "") or "repaired selected tool payload"),
                    payload=deepcopy(payload),
                    evidence_refs=list(getattr(decision, "evidence_refs", []) or []),
                    decision_id=getattr(decision, "decision_id", None),
                )
        return decision

    def _normalized_context_summary(self, summary: Any) -> dict[str, list[Any]]:
        return normalized_context_summary(summary)

    def _merge_context_summaries(self, base: Any, overlay: Any) -> dict[str, list[Any]]:
        merged = self._normalized_context_summary(base)
        incoming = self._normalized_context_summary(overlay)
        for key, values in incoming.items():
            target = merged.setdefault(key, [])
            for value in values:
                if value not in target:
                    target.append(value)
        return merged

    def _usage_payload(self, state: AgentState) -> dict[str, Any]:
        if self.services.context_harness is not None:
            payload = self.services.context_harness.usage_snapshot(state)
        else:
            payload = {"context": {"model_calls": len(state.context_metrics), "model_call_contexts": deepcopy(state.context_metrics)}}
        provider_usage = self._provider_usage_snapshot()
        if provider_usage:
            payload["provider"] = provider_usage
        return payload

    def _provider_usage_snapshot(self) -> dict[str, Any]:
        usage_snapshot = getattr(self.services.provider, "usage_snapshot", None)
        if not callable(usage_snapshot):
            return {}
        try:
            snapshot = usage_snapshot(reset=False)
        except Exception:  # noqa: BLE001 - usage telemetry must not break runtime completion.
            return {}
        return deepcopy(snapshot) if isinstance(snapshot, dict) else {}

    def _apply(self, state: AgentState, event: RuntimeEvent) -> None:
        self.events.append(event)
        apply_event(state, event)
        if self.event_sink is not None:
            try:
                self.event_sink(event.to_dict())
            except Exception:  # noqa: BLE001 - UI sinks must not break the agent loop.
                pass

    def _record_runtime_guidance(
        self,
        state: AgentState,
        *,
        progress: str | None = None,
        open_risk: str | None = None,
        blocker: str | None = None,
        turn_feedback: str | None = None,
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
            if is_turn_local_repair_risk(open_risk) or not is_durable_blocker(open_risk):
                turn_feedback = open_risk
            else:
                blocker = open_risk
        if blocker:
            blockers = summary.setdefault("blockers", [])
            if blocker not in blockers:
                blockers.append(blocker)
        if turn_feedback and turn_feedback not in state.turn_feedback:
            state.turn_feedback.append(turn_feedback)
        self._apply(state, RuntimeEvent("ContextSummaryUpdated", self._normalized_context_summary(summary)))

    def _record_tool_recovery_guidance(self, state: AgentState, guidance_messages: tuple[str, ...]) -> None:
        for message in guidance_messages:
            if isinstance(message, str) and message.strip():
                self._record_runtime_guidance(state, open_risk=message.strip())

    def _record_tool_payload_error_guidance(self, state: AgentState, result: dict[str, Any]) -> None:
        payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
        errors = payload.get("errors")
        if not isinstance(errors, list):
            return
        tool_id = result.get("tool_id")
        if not isinstance(tool_id, str) or not tool_id:
            return
        for error in errors[:6]:
            text = str(error).strip()
            if text:
                self._record_runtime_guidance(state, open_risk=f"{tool_id} reported error: {text}")

    def _redact_denied_argument_values_from_world_refs(self, state: AgentState, arguments: Any) -> None:
        if not isinstance(arguments, dict):
            return
        denied_values = [
            value
            for value in arguments.values()
            if isinstance(value, str) and len(value.strip()) >= 8
        ]
        if not denied_values:
            return
        for ref in state.world_refs.values():
            if isinstance(ref, dict):
                self._redact_strings_in_place(ref, denied_values)

    def _redact_strings_in_place(self, value: Any, denied_values: list[str]) -> Any:
        if isinstance(value, dict):
            for key, item in list(value.items()):
                value[key] = self._redact_strings_in_place(item, denied_values)
            return value
        if isinstance(value, list):
            for index, item in enumerate(value):
                value[index] = self._redact_strings_in_place(item, denied_values)
            return value
        if isinstance(value, str):
            redacted = value
            for denied in denied_values:
                if denied and denied in redacted:
                    redacted = redacted.replace(denied, "[redacted denied argument]")
            return redacted
        return value

    def _preferred_observation_tool_id(self, resolved) -> str | None:
        for card in resolved.skill_cards:
            contract = getattr(card, "observation_contract", None)
            preferred = getattr(contract, "preferred_tool_id", None)
            if isinstance(preferred, str) and preferred in resolved.tool_ids:
                return preferred
        for tool_id in resolved.tool_ids:
            if self._is_observe_tool(tool_id):
                return tool_id
        return resolved.tool_ids[0] if resolved.tool_ids else None

    def _observation_contract_satisfied(self, state: AgentState, resolved) -> bool:
        for card in resolved.skill_cards:
            contract = getattr(card, "observation_contract", None)
            if contract is None:
                continue
            evidence_refs = getattr(contract, "evidence_refs", ())
            if evidence_refs and any(
                ref in state.world_refs and self._world_ref_fresh_for_tool(state, state.world_refs[ref])
                for ref in evidence_refs
            ):
                return True
            evidence_kinds = getattr(contract, "evidence_kinds", ())
            if evidence_kinds and any(
                ref.get("kind") in evidence_kinds and self._world_ref_fresh_for_tool(state, ref)
                for ref in state.world_refs.values()
                if isinstance(ref, dict)
            ):
                return True
        return False

    def _tool_call_evidence_already_exists(self, state: AgentState, tool_id: str, arguments: Any) -> bool:
        if not isinstance(arguments, dict):
            return False
        if self._tool_definition(tool_id) is None:
            return False
        for world_ref in state.world_refs.values():
            if not isinstance(world_ref, dict) or world_ref.get("kind") != tool_id:
                continue
            existing_arguments = world_ref.get("arguments")
            if isinstance(existing_arguments, dict) and existing_arguments == arguments:
                definition = self._tool_definition(tool_id)
                if definition is not None and definition.category == "observe" and not self._world_ref_has_usable_payload(world_ref):
                    return False
                if not self._world_ref_fresh_for_tool(state, world_ref):
                    return False
                return True
        return False

    def _tool_definition(self, tool_id: str):
        definition = getattr(self.services.tool_registry, "definition", None)
        if not callable(definition):
            return None
        return definition(tool_id)

    def _world_ref_fresh_for_tool(self, state: AgentState, world_ref: dict[str, Any]) -> bool:
        tool_id = world_ref.get("kind")
        if not isinstance(tool_id, str):
            return False
        definition = self._tool_definition(tool_id)
        if definition is None:
            return True
        return is_world_ref_fresh(state, world_ref, definition)

    @staticmethod
    def _world_ref_has_usable_payload(world_ref: dict[str, Any]) -> bool:
        payload = world_ref.get("payload")
        if not isinstance(payload, dict) or not payload:
            return False
        kind = world_ref.get("kind")
        if kind == "file_management.repo_snapshot":
            return isinstance(payload.get("files"), list) or isinstance(payload.get("directories"), list)
        if kind == "file_management.read_file":
            return isinstance(payload.get("content"), str)
        return True

    def _existing_tool_call_denial(self, state: AgentState, tool_id: str, arguments: Any) -> dict[str, Any] | None:
        if not isinstance(arguments, dict):
            arguments = {}
        for result in state.tool_results.values():
            if not isinstance(result, dict):
                continue
            if result.get("tool_id") != tool_id or result.get("status") not in {"denied", "failed"}:
                continue
            existing_arguments = result.get("arguments")
            if isinstance(existing_arguments, dict) and existing_arguments == arguments:
                payload = result.get("payload") if isinstance(result.get("payload"), dict) else {}
                if result.get("status") == "failed" and payload.get("retryable") is True:
                    continue
                return result
        return None

    def _latest_unresolved_tool_feedback(self, state: AgentState) -> dict[str, Any] | None:
        for result in reversed(list(state.tool_results.values())):
            if not isinstance(result, dict):
                continue
            status = result.get("status")
            if status in {"denied", "failed"}:
                return result
            if status == "completed":
                return None
        return None

    def _tool_result_guidance(self, resolved, result: dict[str, Any]) -> tuple[str, ...]:
        return self.services.extension_registry.tool_result_guidance(resolved.extension_ids, result)

    def _apply_named_recovery_tool_mode(self, state: AgentState, resolved, current_tool_id: str, guidance: tuple[str, ...]) -> None:
        named_tool_ids = self._named_recovery_tool_ids(resolved, current_tool_id, guidance)
        if not named_tool_ids:
            return
        definition = self._tool_definition(named_tool_ids[0])
        mode = "OBSERVE" if definition is not None and definition.category == "observe" else "ACT"
        self._apply(state, RuntimeEvent("ModeChanged", {"mode": mode}))

    def _record_named_recovery_tool_guidance(
        self,
        state: AgentState,
        resolved,
        current_tool_id: str,
        guidance: tuple[str, ...],
    ) -> None:
        named_tool_ids = self._named_recovery_tool_ids(resolved, current_tool_id, guidance)
        if not named_tool_ids:
            return
        next_tool_id = named_tool_ids[0]
        if next_tool_id == current_tool_id:
            self._record_runtime_guidance(
                state,
                open_risk=(
                    f"Recovery guidance names selected tool {next_tool_id}; "
                    f"the next decision must be a tool_call to {next_tool_id} with corrected arguments instead of repeating previously denied arguments."
                ),
            )
            return
        self._record_runtime_guidance(
            state,
            open_risk=(
                f"Recovery guidance names selected tool {next_tool_id}; "
                f"the next decision should call {next_tool_id} instead of retrying {current_tool_id}."
            ),
        )

    @staticmethod
    def _named_guidance_tool_ids(resolved, guidance: tuple[str, ...]) -> list[str]:
        text = " ".join(str(item) for item in guidance)
        return [
            tool_id
            for tool_id in resolved.tool_ids
            if isinstance(tool_id, str) and tool_id in text
        ]

    @staticmethod
    def _named_recovery_tool_ids(resolved, current_tool_id: str, guidance: tuple[str, ...]) -> list[str]:
        text = " ".join(str(item) for item in guidance)
        named = [
            tool_id
            for tool_id in resolved.tool_ids
            if isinstance(tool_id, str) and tool_id in text
        ]
        alternates = [tool_id for tool_id in named if tool_id != current_tool_id]
        current = [tool_id for tool_id in named if tool_id == current_tool_id]
        return alternates + current

    def _is_observe_tool(self, tool_id: str) -> bool:
        definition = self._tool_definition(tool_id)
        return definition is not None and definition.category == "observe"

    def _has_completed_non_observe_tool(self, state: AgentState) -> bool:
        for world_ref in state.world_refs.values():
            if not isinstance(world_ref, dict):
                continue
            kind = world_ref.get("kind")
            if isinstance(kind, str) and not self._is_observe_tool(kind):
                return True
        return False

    def _active_tool_id_mentioned(self, decision, active_tool_ids) -> str | None:
        text = f"{getattr(decision, 'reason', '')} {json.dumps(getattr(decision, 'payload', {}), sort_keys=True, default=str)}"
        for tool_id in active_tool_ids:
            if isinstance(tool_id, str) and tool_id and tool_id in text:
                return tool_id
        return None

    def _complete_from_tool_loop(self, state: AgentState, *, reason: str, assistant_message: str = "") -> None:
        self._apply(
            state,
            RuntimeEvent(
                "RunCompleted",
                {
                    "status": "completed",
                    "reason": reason,
                    "session_id": state.session_id,
                    "run_id": state.run_id,
                    "evidence_refs": list(state.world_refs.keys()),
                    "world_refs": deepcopy(state.world_refs),
                    "context_summary": self._normalized_context_summary(state.context_summary),
                    "turn_feedback": list(state.turn_feedback),
                    "tool_results": list(state.tool_results.values()),
                    "usage": self._usage_payload(state),
                    "assistant_message": assistant_message,
                },
            ),
        )

    def _complete_from_latest_observe_result(self, state: AgentState) -> bool:
        for result in reversed(list(state.tool_results.values())):
            if not isinstance(result, dict) or result.get("status") != "completed":
                continue
            tool_id = result.get("tool_id")
            if not isinstance(tool_id, str) or not self._is_observe_tool(tool_id):
                continue
            payload = result.get("payload")
            if isinstance(payload, dict):
                message = json.dumps(payload, indent=2, sort_keys=True, default=str)[:4000]
            else:
                message = str(payload or "Completed observe result is available.")[:4000]
            self._complete_from_tool_loop(state, reason="tool_loop_completed", assistant_message=message)
            return True
        return False

    def _assistant_message_from_decision(self, decision) -> str:
        payload = getattr(decision, "payload", None)
        if isinstance(payload, dict):
            for key in ("message", "answer", "summary", "final_message"):
                value = payload.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()[:4000]
        reason = getattr(decision, "reason", "")
        if isinstance(reason, str) and reason and reason != "model_decision":
            return reason.strip()[:4000]
        return ""

    def _fail_payload(self, state: AgentState, *, reason: str, **extra: Any) -> dict[str, Any]:
        return {
            "status": "failed",
            "reason": reason,
            "session_id": state.session_id,
            "run_id": state.run_id,
            "evidence_refs": list(state.world_refs.keys()),
            "world_refs": deepcopy(state.world_refs),
            "context_summary": self._normalized_context_summary(state.context_summary),
            "turn_feedback": list(state.turn_feedback),
            "tool_results": list(state.tool_results.values()),
            "usage": self._usage_payload(state),
            **extra,
        }
