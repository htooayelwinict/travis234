"""Extension ownership for coding-session tool hooks.

Travis does not guess whether a tool call is useful, mutating, repetitive, or
worthy of another model turn.  Administrative policy belongs in extensions or
the execution boundary.  This bridge only applies extension hook results.
"""

from __future__ import annotations

from travis.agent.types import AfterToolCallResult, BeforeToolCallResult
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
            return None
        return BeforeToolCallResult(
            block=True,
            reason=f"Tool policy denied {definition.name} ({decision.reason_code})",
        )

    async def _after_tool_call(self, context, signal=None) -> AfterToolCallResult | None:
        del signal
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


__all__ = ("SessionPolicyController", "_is_internal_steering_user_message")
