"""Deterministic tool policy decisions and the async approval edge."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence

from travis.agent.types import AbortSignal
from travis.coding_agent.eval_trace import SecretRedactor
from travis.coding_agent.policy.approval import (
    ApprovalResponse,
    SessionGrantSet,
    ToolApprovalBroker,
    ToolApprovalRequest,
)
from travis.coding_agent.policy.types import ToolPolicyDecision, ToolPolicySettings
from travis.coding_agent.tools.types import ToolDefinition

_SAFE_CONTEXT_KEYS = frozenset(
    {
        "action",
        "target",
        "server",
        "operation",
        "role",
        "backend",
        "executable",
        "commandFingerprint",
        "taskId",
        "childRole",
        "childTaskId",
    }
)
_SAFE_CONTEXT_BYTE_LIMIT = 384


def _canonical_json_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"$type": "float"}
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_json_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_canonical_json_value(item) for item in value]
    value_type = type(value)
    return {"$type": f"{value_type.__module__}.{value_type.__qualname__}"}


def argument_fingerprint(arguments: object) -> str:
    encoded = json.dumps(
        _canonical_json_value(arguments),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _truncate_utf8(value: str, byte_limit: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= byte_limit:
        return value
    suffix = "..."
    kept = encoded[: max(0, byte_limit - len(suffix))]
    while kept:
        try:
            return kept.decode("utf-8") + suffix
        except UnicodeDecodeError:
            kept = kept[:-1]
    return suffix[:byte_limit]


def _safe_policy_context(
    tool: ToolDefinition,
    arguments: dict[str, object],
    redactor: SecretRedactor,
) -> dict[str, str]:
    if tool.policy_context is None:
        return {}
    try:
        raw = tool.policy_context(arguments)
    except Exception:  # noqa: BLE001 - optional context must never block execution.
        return {}
    if not isinstance(raw, Mapping):
        return {}
    safe = {
        str(key): _truncate_utf8(redactor.redact_text(value), 160)
        for key, value in raw.items()
        if str(key) in _SAFE_CONTEXT_KEYS
    }
    while safe:
        encoded = json.dumps(safe, ensure_ascii=True, sort_keys=True).encode("utf-8")
        if len(encoded) <= _SAFE_CONTEXT_BYTE_LIMIT:
            return safe
        longest = max(safe, key=lambda key: len(safe[key].encode("utf-8")))
        current = safe[longest]
        next_limit = max(16, len(current.encode("utf-8")) - (len(encoded) - _SAFE_CONTEXT_BYTE_LIMIT))
        shortened = _truncate_utf8(current, next_limit)
        if shortened == current:
            safe.pop(longest)
        else:
            safe[longest] = shortened
    return {}


class ToolPolicyEngine:
    def __init__(
        self,
        settings: ToolPolicySettings,
        *,
        broker: ToolApprovalBroker | None = None,
        grants: SessionGrantSet | None = None,
        redactor: SecretRedactor | None = None,
    ) -> None:
        self.settings = settings
        self.broker = broker
        self.grants = grants or SessionGrantSet()
        self.redactor = redactor or SecretRedactor()

    def evaluate(self, tool: ToolDefinition, arguments: dict[str, object]) -> ToolPolicyDecision:
        del arguments
        effects = tool.effects
        mode = self.settings.mode
        if mode == "disabled":
            return self._decision(tool, True, "policy_disabled")
        if not effects:
            return self._decision(tool, mode == "audit", "undeclared_effects")
        if effects.issubset(self.settings.auto_allow_effects):
            return self._decision(tool, True, "auto_allowed")
        if self.grants.contains(tool.name, effects):
            return self._decision(tool, True, "session_grant")
        return self._decision(tool, mode == "audit", "approval_required")

    async def authorize(
        self,
        tool: ToolDefinition,
        arguments: dict[str, object],
        *,
        signal: AbortSignal | None = None,
    ) -> ToolPolicyDecision:
        decision = self.evaluate(tool, arguments)
        if decision.allow or decision.reason_code != "approval_required":
            return decision
        if signal is not None and signal.aborted:
            return self._decision(tool, False, "approval_cancelled")
        if self.broker is None:
            return self._decision(tool, False, "approval_unavailable")
        request = ToolApprovalRequest(
            tool_name=tool.name,
            effects=tool.effects,
            argument_fingerprint=argument_fingerprint(arguments),
            safe_context=_safe_policy_context(tool, arguments, self.redactor),
            reason_code="approval_required",
        )
        try:
            response = await self.broker.request(request, signal)
        except Exception:  # noqa: BLE001 - approval failures deny without leaking broker details.
            reason = "approval_cancelled" if signal is not None and signal.aborted else "approval_unavailable"
            return self._decision(tool, False, reason)
        if signal is not None and signal.aborted:
            return self._decision(tool, False, "approval_cancelled")
        if not isinstance(response, ApprovalResponse):
            return self._decision(tool, False, "approval_unavailable")
        if response.scope == "deny":
            return self._decision(tool, False, "approval_denied")
        if response.scope == "session":
            self.grants.add(tool.name, tool.effects)
            return self._decision(tool, True, "session_grant")
        if response.scope == "once":
            return self._decision(tool, True, "approval_required")
        return self._decision(tool, False, "approval_unavailable")

    def _decision(self, tool: ToolDefinition, allow: bool, reason_code: str) -> ToolPolicyDecision:
        return ToolPolicyDecision(
            tool_name=tool.name,
            effects=tool.effects,
            mode=self.settings.mode,
            allow=allow,
            reason_code=reason_code,
        )


__all__ = ["ToolPolicyEngine", "argument_fingerprint"]
