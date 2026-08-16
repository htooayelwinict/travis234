from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from travis.agent.types import AbortSignal, AgentToolResult
from travis.coding_agent.eval_trace import SecretRedactor
from travis.coding_agent.policy.approval import (
    ApprovalResponse,
    SessionGrantSet,
    ToolApprovalRequest,
)
from travis.coding_agent.policy.engine import ToolPolicyEngine, argument_fingerprint
from travis.coding_agent.policy.types import ToolPolicySettings
from travis.coding_agent.tools.types import ToolDefinition


def _tool(
    effects: frozenset[str],
    *,
    name: str = "fixture",
    policy_context=None,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        label=name,
        description="",
        parameters={},
        execute=lambda *_args, **_kwargs: AgentToolResult(content=[]),
        effects=effects,  # type: ignore[arg-type]
        policy_context=policy_context,
    )


def _engine(
    mode: str,
    auto: frozenset[str] = frozenset({"read"}),
    **kwargs,
) -> ToolPolicyEngine:
    return ToolPolicyEngine(
        ToolPolicySettings(mode=mode, auto_allow_effects=auto),  # type: ignore[arg-type]
        **kwargs,
    )


@pytest.mark.parametrize(
    ("mode", "effects", "allow", "reason"),
    [
        ("disabled", frozenset(), True, "policy_disabled"),
        ("disabled", frozenset({"write"}), True, "policy_disabled"),
        ("audit", frozenset({"read"}), True, "auto_allowed"),
        ("audit", frozenset({"write"}), True, "approval_required"),
        ("audit", frozenset(), True, "undeclared_effects"),
        ("enforce", frozenset({"read"}), True, "auto_allowed"),
        ("enforce", frozenset({"write"}), False, "approval_required"),
        ("enforce", frozenset(), False, "undeclared_effects"),
    ],
)
def test_policy_decision_table(
    mode: str,
    effects: frozenset[str],
    allow: bool,
    reason: str,
) -> None:
    decision = _engine(mode).evaluate(_tool(effects), {"secret": "raw-value"})

    assert decision.allow is allow
    assert decision.reason_code == reason
    assert "raw-value" not in repr(decision)


def test_session_grant_matches_exact_tool_and_effect_set() -> None:
    grants = SessionGrantSet()
    grants.add("fixture", frozenset({"write"}))
    engine = _engine("enforce", grants=grants)

    assert engine.evaluate(_tool(frozenset({"write"})), {}).reason_code == "session_grant"
    assert engine.evaluate(_tool(frozenset({"write"}), name="other"), {}).reason_code == "approval_required"
    assert engine.evaluate(_tool(frozenset({"write", "execute"})), {}).reason_code == "approval_required"


@dataclass
class RecordingBroker:
    response: ApprovalResponse

    def __post_init__(self) -> None:
        self.requests: list[ToolApprovalRequest] = []

    async def request(self, request: ToolApprovalRequest, signal: AbortSignal | None) -> ApprovalResponse:
        self.requests.append(request)
        return self.response


@pytest.mark.parametrize(
    ("scope", "allow", "reason"),
    [
        ("once", True, "approval_required"),
        ("session", True, "session_grant"),
        ("deny", False, "approval_denied"),
    ],
)
def test_authorize_shapes_broker_responses(scope: str, allow: bool, reason: str) -> None:
    broker = RecordingBroker(ApprovalResponse(scope=scope))  # type: ignore[arg-type]
    engine = _engine("enforce", broker=broker)

    first = asyncio.run(engine.authorize(_tool(frozenset({"write"})), {"path": "one"}))

    assert first.allow is allow
    assert first.reason_code == reason
    assert len(broker.requests) == 1
    if scope == "session":
        second = asyncio.run(engine.authorize(_tool(frozenset({"write"})), {"path": "two"}))
        assert second.allow is True
        assert second.reason_code == "session_grant"
        assert len(broker.requests) == 1


def test_enforce_without_broker_denies_instead_of_waiting() -> None:
    decision = asyncio.run(
        _engine("enforce").authorize(_tool(frozenset({"write"})), {})
    )

    assert decision.allow is False
    assert decision.reason_code == "approval_unavailable"


def test_authorize_cancelled_before_request_never_calls_broker() -> None:
    broker = RecordingBroker(ApprovalResponse(scope="once"))
    signal = AbortSignal()
    signal.abort()

    decision = asyncio.run(
        _engine("enforce", broker=broker).authorize(
            _tool(frozenset({"write"})), {}, signal=signal
        )
    )

    assert decision.allow is False
    assert decision.reason_code == "approval_cancelled"
    assert broker.requests == []


def test_authorize_cancelled_while_broker_resolves_fails_closed() -> None:
    signal = AbortSignal()

    class CancellingBroker:
        async def request(self, request, broker_signal):
            assert broker_signal is signal
            signal.abort()
            return ApprovalResponse(scope="once")

    decision = asyncio.run(
        _engine("enforce", broker=CancellingBroker()).authorize(
            _tool(frozenset({"write"})), {}, signal=signal
        )
    )

    assert decision.allow is False
    assert decision.reason_code == "approval_cancelled"


def test_failing_broker_is_unavailable_and_does_not_leak_error() -> None:
    class FailingBroker:
        async def request(self, request, signal):
            raise RuntimeError("credential-value-never-render")

    decision = asyncio.run(
        _engine("enforce", broker=FailingBroker()).authorize(
            _tool(frozenset({"write"})), {"raw": "credential-value-never-render"}
        )
    )

    assert decision.allow is False
    assert decision.reason_code == "approval_unavailable"
    assert "credential-value" not in repr(decision)


def test_invalid_broker_response_fails_closed() -> None:
    class InvalidBroker:
        async def request(self, request, signal):
            return object()

    decision = asyncio.run(
        _engine("enforce", broker=InvalidBroker()).authorize(
            _tool(frozenset({"write"})), {}
        )
    )

    assert decision.allow is False
    assert decision.reason_code == "approval_unavailable"


def test_argument_fingerprint_is_canonical_sha256_without_raw_values() -> None:
    first = argument_fingerprint({"b": 2, "a": [1, "credential-value-never-render"]})
    second = argument_fingerprint({"a": [1, "credential-value-never-render"], "b": 2})
    non_json = argument_fingerprint({"value": object()})

    assert first == second
    assert len(first) == 64
    assert len(non_json) == 64
    assert "credential-value" not in first


def test_request_context_is_allowlisted_redacted_and_bounded() -> None:
    secret = "credential-value-never-render"
    broker = RecordingBroker(ApprovalResponse(scope="deny"))
    engine = _engine(
        "enforce",
        broker=broker,
        redactor=SecretRedactor([secret]),
    )
    tool = _tool(
        frozenset({"network"}),
        policy_context=lambda _args: {
            "action": "query",
            "server": "x" * 1000 + secret,
            "headers": {"Authorization": f"Bearer {secret}"},
            "rawCommand": f"curl {secret}",
        },
    )

    asyncio.run(engine.authorize(tool, {"token": secret}))

    request = broker.requests[0]
    rendered = repr(request.safe_context)
    assert set(request.safe_context) == {"action", "server"}
    assert secret not in rendered
    assert "Authorization" not in rendered
    assert "curl" not in rendered
    assert len(rendered.encode("utf-8")) <= 512
    assert request.reason_code == "approval_required"
    assert len(request.argument_fingerprint) == 64


def test_policy_context_failure_yields_empty_context_without_blocking() -> None:
    broker = RecordingBroker(ApprovalResponse(scope="once"))

    def fail(_arguments):
        raise RuntimeError("context failed")

    decision = asyncio.run(
        _engine("enforce", broker=broker).authorize(
            _tool(frozenset({"write"}), policy_context=fail), {}
        )
    )

    assert decision.allow is True
    assert dict(broker.requests[0].safe_context) == {}
