from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from appv22.context.compressor import _compact_world_ref_payload
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
            state.context_summary = self._normalized_context_summary(context_summary)
        return self._run_state(state)

    def _run_state(self, state: AgentState) -> dict:
        self.events = []
        self._apply(state, RuntimeEvent("AgentStarted", {"status": "started"}))

        for turn_index in range(self.max_turns):
            resolved = self.services.extension_registry.resolve_active(state)
            state.active_extension_ids = list(resolved.extension_ids)
            state.active_skill_ids = [card.skill_id for card in resolved.skill_cards]

            selected = self.services.context_selector.select(state, resolved, pre_turn_mode=state.mode)
            prompt = self.services.prompt_builder.build(state, selected)
            provider_prompt = self._provider_bound_prompt(state, prompt)

            try:
                decision = self._decide_with_provider_retries(state, provider_prompt)
                decision = self._repair_decision_shape(decision, resolved)
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
            if self._should_complete_observation_only(state, resolved):
                self._complete_from_tool_loop(state, reason="observation_only_completed")
            elif self._has_completed_non_observe_tool(state):
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
            if "tool_call" in payload or not resolved.tool_ids:
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
                open_risk=(
                    "Malformed tool_call decision was missing payload.tool_id; "
                    "the next decision must call one selected tool using payload.tool_id and payload.arguments, "
                    "or finalize only if existing evidence already proves completion."
                ),
            )
            return

        if tool_id.lower() in {"none", "null", "no_tool", "no-op", "noop"}:
            if self._has_completed_non_observe_tool(state):
                self._complete_from_tool_loop(state, reason="tool_loop_completed")
            elif self._should_complete_observation_only(state, resolved):
                self._complete_from_tool_loop(state, reason="observation_only_completed")
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
            if self._is_observe_tool(tool_id):
                if self._should_complete_observation_only(state, resolved):
                    self._record_runtime_guidance(
                        state,
                        progress="Observation evidence already exists and the request is observation-only; completed without replaying broad observation.",
                    )
                    self._complete_from_tool_loop(state, reason="observation_only_completed")
                    return
                self._apply(state, RuntimeEvent("ModeChanged", {"mode": "THINK"}))
                self._record_runtime_guidance(
                    state,
                    progress=(
                        "Observation already satisfied by durable evidence_refs; duplicate observe tool call suppressed. "
                        "Continue reasoning from existing evidence, rehydrate exact evidence only if needed, "
                        "or call a selected action tool when ready."
                    ),
                )
                return
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
            active_tool_ids=resolved.tool_ids,
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
        self._record_tool_result(state, result)
        return result

    def _record_tool_result(self, state: AgentState, result: dict[str, Any]) -> None:
        event_type = "ToolCallCompleted" if result["status"] == "completed" else "ToolCallDenied"
        self._apply(state, RuntimeEvent(event_type, result))
        if result["status"] == "completed":
            arguments = result.get("arguments") if isinstance(result.get("arguments"), dict) else {}
            self._apply(
                state,
                RuntimeEvent(
                    "WorldRefAdded",
                    {
                        "ref_id": result.get("payload_ref") or f"world://{result['tool_id']}/latest",
                        "kind": result["tool_id"],
                        "arguments": deepcopy(arguments),
                        "payload": result["payload"],
                        "summary": f"{result['tool_id']} result",
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

    def _repair_decision_shape(self, decision, resolved):
        payload = getattr(decision, "payload", None)
        if getattr(decision, "kind", None) != "tool_call" and isinstance(payload, dict):
            tool_id = payload.get("tool_id")
            if isinstance(tool_id, str) and tool_id in resolved.tool_ids:
                return RuntimeDecision(
                    kind="tool_call",
                    reason=str(getattr(decision, "reason", "") or "repaired selected tool payload"),
                    payload=deepcopy(payload),
                    evidence_refs=list(getattr(decision, "evidence_refs", []) or []),
                    decision_id=getattr(decision, "decision_id", None),
                )
        return decision

    def _provider_bound_prompt(self, state: AgentState, prompt: dict) -> dict:
        prompt = deepcopy(prompt)
        prompt["tool_definitions"] = self._selected_tool_definitions(prompt)
        messages = self._provider_prompt_messages(state, prompt)
        compressed = self.services.compressor.compress(messages, previous_summary=state.context_summary)
        context_summary = self._summary_from_messages(compressed)
        if context_summary is not None and context_summary != state.context_summary:
            self._apply(state, RuntimeEvent("ContextSummaryUpdated", self._merge_context_summaries(state.context_summary, context_summary)))
        compressed = self.services.gateway_guard.guard(compressed)
        provider_prompt = self._prompt_from_governed_messages(compressed)
        provider_prompt["state"]["context_summary"] = self._normalized_context_summary(
            self._merge_context_summaries(state.context_summary, provider_prompt["state"].get("context_summary", {}))
        )
        if not provider_prompt.get("skills"):
            provider_prompt["skills"] = deepcopy(prompt.get("skills", []))
        if not provider_prompt.get("tools"):
            provider_prompt["tools"] = list(prompt.get("tools", [])) if isinstance(prompt.get("tools"), list) else []
        if not provider_prompt.get("tool_definitions"):
            provider_prompt["tool_definitions"] = deepcopy(prompt.get("tool_definitions", []))
        if not provider_prompt["selection"].get("selected_tools"):
            provider_prompt["selection"] = deepcopy(prompt.get("selection", provider_prompt["selection"]))
        if not provider_prompt["world"].get("world_refs") and state.world_refs:
            provider_prompt["world"]["world_refs"] = self._compact_world_refs_for_prompt(state.world_refs)
        provider_prompt["messages"] = compressed
        return provider_prompt

    def _provider_prompt_messages(self, state: AgentState, prompt: dict) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "name": "provider_identity",
                "content": prompt.get("system", {}).get("identity", "AppV2.2 provider context"),
                "payload": deepcopy(prompt.get("system", {})),
            }
        ]
        for section in ("agent", "state", "skills", "tools", "tool_definitions", "world", "selection"):
            payload = deepcopy(prompt.get(section, {} if section not in {"skills", "tools", "tool_definitions"} else []))
            messages.append(
                {
                    "role": "system",
                    "name": "provider_context_section",
                    "section": section,
                    "content": f"{section}: {json.dumps(payload, sort_keys=True, default=str)}",
                    "payload": payload,
                }
            )
        messages.append({"role": "user", "name": "active_user_request", "content": _active_request_text(state)})
        return messages

    def _prompt_from_governed_messages(self, messages: list[dict[str, Any]]) -> dict:
        provider_prompt: dict[str, Any] = {
            "system": {},
            "agent": {},
            "state": {"mode": None, "context_summary": {}},
            "skills": [],
            "tools": [],
            "tool_definitions": [],
            "world": {"world_refs": {}},
            "selection": {"selected_tools": [], "selected_skills": [], "active_extensions": [], "available_tools": []},
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

    def _selected_tool_definitions(self, prompt: dict) -> list[dict[str, Any]]:
        selection = prompt.get("selection") if isinstance(prompt.get("selection"), dict) else {}
        selected_tools = selection.get("selected_tools")
        if not isinstance(selected_tools, list):
            return []
        definitions: list[dict[str, Any]] = []
        for tool_id in selected_tools:
            if not isinstance(tool_id, str):
                continue
            definition = self.services.tool_registry.definition(tool_id)
            if definition is None:
                continue
            definitions.append(
                {
                    "tool_id": definition.tool_id,
                    "category": definition.category,
                    "risk_level": definition.risk_level,
                    "argument_schema": self._mutable_json_like(definition.argument_schema),
                    "result_schema": self._mutable_json_like(definition.result_schema),
                    "trust": definition.trust,
                    "guidance": definition.guidance,
                }
            )
        return definitions

    def _mutable_json_like(self, value: Any) -> Any:
        if isinstance(value, dict) or hasattr(value, "items"):
            return {key: self._mutable_json_like(item) for key, item in value.items()}
        if isinstance(value, tuple | list):
            return [self._mutable_json_like(item) for item in value]
        return value

    def _summary_from_messages(self, messages: list[dict]) -> dict | None:
        for message in messages:
            summary = message.get("summary")
            if isinstance(summary, dict):
                return deepcopy(summary)
        return None

    def _normalized_context_summary(self, summary: Any) -> dict[str, list[Any]]:
        source = summary if isinstance(summary, dict) else {}
        return {
            "goals": list(source.get("goals", [])) if isinstance(source.get("goals", []), list) else [],
            "decisions": list(source.get("decisions", [])) if isinstance(source.get("decisions", []), list) else [],
            "progress": list(source.get("progress", [])) if isinstance(source.get("progress", []), list) else [],
            "open_risks": list(source.get("open_risks", [])) if isinstance(source.get("open_risks", []), list) else [],
            "evidence_refs": list(source.get("evidence_refs", [])) if isinstance(source.get("evidence_refs", []), list) else [],
        }

    def _merge_context_summaries(self, base: Any, overlay: Any) -> dict[str, list[Any]]:
        merged = self._normalized_context_summary(base)
        incoming = self._normalized_context_summary(overlay)
        for key, values in incoming.items():
            target = merged.setdefault(key, [])
            for value in values:
                if value not in target:
                    target.append(value)
        return merged

    def _compact_world_refs_for_prompt(self, world_refs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        compacted: dict[str, dict[str, Any]] = {}
        for ref_id, ref in world_refs.items():
            if not isinstance(ref_id, str) or not isinstance(ref, dict):
                continue
            compacted[ref_id] = {
                "ref_id": ref.get("ref_id", ref_id),
                "kind": ref.get("kind"),
                "arguments": deepcopy(ref.get("arguments", {})) if isinstance(ref.get("arguments"), dict) else {},
                "summary": str(ref.get("summary", ""))[:240],
            }
            payload = ref.get("payload")
            if isinstance(payload, dict):
                compacted[ref_id]["payload"] = _compact_world_ref_payload(payload)
        return compacted

    def _apply(self, state: AgentState, event: RuntimeEvent) -> None:
        self.events.append(event)
        apply_event(state, event)
        if self.event_sink is not None:
            try:
                self.event_sink(event.to_dict())
            except Exception:  # noqa: BLE001 - UI sinks must not break the agent loop.
                pass

    def _record_runtime_guidance(self, state: AgentState, *, progress: str | None = None, open_risk: str | None = None) -> None:
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
            if evidence_refs and any(ref in state.world_refs for ref in evidence_refs):
                return True
            evidence_kinds = getattr(contract, "evidence_kinds", ())
            if evidence_kinds and any(ref.get("kind") in evidence_kinds for ref in state.world_refs.values() if isinstance(ref, dict)):
                return True
        return False

    def _tool_call_evidence_already_exists(self, state: AgentState, tool_id: str, arguments: Any) -> bool:
        if not isinstance(arguments, dict):
            return False
        if self.services.tool_registry.definition(tool_id) is None:
            return False
        for world_ref in state.world_refs.values():
            if not isinstance(world_ref, dict) or world_ref.get("kind") != tool_id:
                continue
            existing_arguments = world_ref.get("arguments")
            if isinstance(existing_arguments, dict) and existing_arguments == arguments:
                return True
        return False

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
        definition = self.services.tool_registry.definition(named_tool_ids[0])
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
        definition = self.services.tool_registry.definition(tool_id)
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

    def _should_complete_observation_only(self, state: AgentState, resolved) -> bool:
        goal = _active_request_text(state).lower()
        no_workspace_change_requested = any(
            marker in goal
            for marker in (
                "do not mutate",
                "don't mutate",
                "no mutation",
                "do not change files",
                "don't change files",
                "do not write",
                "don't write",
            )
        )
        pause_after_evidence = "pause after" in goal or "after you have enough evidence" in goal
        inspect_only = "inspect" in goal or "observe" in goal or "evidence" in goal
        return (
            no_workspace_change_requested
            and (pause_after_evidence or inspect_only)
            and self._observation_contract_satisfied(state, resolved)
        )

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
                    "tool_results": list(state.tool_results.values()),
                    "assistant_message": assistant_message,
                },
            ),
        )

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
            "tool_results": list(state.tool_results.values()),
            **extra,
        }
