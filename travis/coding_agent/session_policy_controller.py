"""Extension ownership for coding-session tool hooks.

Travis does not guess whether a tool call is useful, mutating, repetitive, or
worthy of another model turn.  Administrative policy belongs in extensions or
the execution boundary.  This bridge only applies extension hook results.
"""

from __future__ import annotations

from travis.agent.types import AfterToolCallResult, BeforeToolCallResult
from travis.coding_agent.policy import argument_fingerprint
from travis.coding_agent.policy.types import TOOL_EFFECT_ORDER
from travis.coding_agent.session_types import _MALFORMED_STREAM_RECOVERY_PREFIX


def _is_internal_steering_user_message(text: str | None) -> bool:
    prompt = (text or "").lstrip()
    return prompt.startswith(
        (
            "[System: Your previous tool call ",
            _MALFORMED_STREAM_RECOVERY_PREFIX,
        )
    )


class SessionPolicyController:
    """Apply ``tool_call`` and ``tool_result`` extension hooks."""

    async def _before_tool_call(self, context, signal=None) -> BeforeToolCallResult | None:
        if self._extension_runner.has_handlers("tool_call"):
            # Pass the validated object itself. Mutations are visible to later
            # handlers, policy, and tool execution, and are intentionally not revalidated.
            result = await self._extension_runner.async_emit_tool_call(
                {
                    "type": "tool_call",
                    "toolName": context.tool_call.name,
                    "toolCallId": context.tool_call.id,
                    "input": context.args,
                }
            )
            if result and result.get("block", False):
                reason = result.get("reason")
                return BeforeToolCallResult(
                    block=True,
                    reason=str(reason) if reason is not None else None,
                )

        definition = self.get_tool_definition(context.tool_call.name)
        if definition is None:
            return None
        decision = await self._tool_policy_engine.authorize(
            definition,
            context.args,
            signal=signal,
        )
        self._emit_tool_policy_decision(decision)
        if decision.allow:
            self._journal_tool_intent(context, decision.effects)
            return None
        return BeforeToolCallResult(
            block=True,
            reason=f"Tool policy denied {definition.name} ({decision.reason_code})",
        )

    async def _after_tool_call(self, context, signal=None) -> AfterToolCallResult | None:
        self._settle_tool_effect(context, signal)
        if not self._extension_runner.has_handlers("tool_result"):
            return None
        result = await self._extension_runner.async_emit_tool_result(
            {
                "type": "tool_result",
                "toolName": context.tool_call.name,
                "toolCallId": context.tool_call.id,
                "input": context.args,
                "content": context.result.content,
                "details": context.result.details,
                "isError": context.is_error,
            }
        )
        if not result:
            return None
        content = result.get("content")
        details = result.get("details")
        raw_is_error = result.get("isError")
        if content is None and details is None and raw_is_error is None:
            return None
        return AfterToolCallResult(
            content=content,
            details=details,
            is_error=bool(raw_is_error) if raw_is_error is not None else None,
        )

    def _journal_tool_intent(self, context, effects) -> None:
        coordinator = self.operation_coordinator
        try:
            handle = coordinator.begin_effect(
                "tool",
                context.tool_call.name,
                argument_fingerprint(context.args),
                tuple(effect for effect in TOOL_EFFECT_ORDER if effect in effects),
            )
        except Exception:
            self._disable_operation_journal()
            return
        if handle is None:
            return
        with self._operation_tool_effects_lock:
            self._operation_tool_effects[context.tool_call.id] = handle

    def _settle_tool_effect(self, context, signal) -> None:
        call_id = context.tool_call.id
        with self._operation_tool_effects_lock:
            handle = self._operation_tool_effects.get(call_id)
        if handle is None:
            return
        outcome_code = (
            "cancelled"
            if signal is not None and signal.aborted
            else "tool_error"
            if context.is_error
            else "ok"
        )
        try:
            self.operation_coordinator.settle_effect(handle, outcome_code)
        except Exception:
            self._disable_operation_journal()
        finally:
            with self._operation_tool_effects_lock:
                if self._operation_tool_effects.get(call_id) is handle:
                    self._operation_tool_effects.pop(call_id, None)

    def _disable_operation_journal(self) -> None:
        try:
            self.operation_coordinator.disable("journal_unavailable")
        except Exception:
            pass


__all__ = ("SessionPolicyController", "_is_internal_steering_user_message")
