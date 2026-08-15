from __future__ import annotations

from dataclasses import dataclass

from tests._provider_runtime import register_api_provider, reset_api_providers
from travis.agent.types import AbortSignal, AgentToolResult
from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events, tool_call_response_events
from travis.ai.types import TextContent, ToolResultMessage
from travis.coding_agent import AgentSession, ExtensionRunner, SettingsManager
from travis.coding_agent.policy import ApprovalResponse, ToolApprovalRequest, argument_fingerprint
from travis.coding_agent.tools.types import ToolDefinition


def setup_function() -> None:
    reset_api_providers()


@dataclass
class Broker:
    scope: str = "once"

    def __post_init__(self) -> None:
        self.requests: list[ToolApprovalRequest] = []

    async def request(self, request: ToolApprovalRequest, signal: AbortSignal | None) -> ApprovalResponse:
        self.requests.append(request)
        return ApprovalResponse(scope=self.scope)  # type: ignore[arg-type]


def _settings(mode: str) -> SettingsManager:
    return SettingsManager.in_memory(
        {"toolPolicy": {"mode": mode, "autoAllowEffects": ["read"]}}
    )


def _tool(executions: list[dict[str, object]], *, effects=frozenset({"write"})) -> ToolDefinition:
    def execute(_tool_call_id, args, signal=None, on_update=None, ctx=None):
        executions.append(dict(args))
        return AgentToolResult(
            content=[TextContent(text=f"executed:{args.get('path')}")],
            details={"stable": True},
        )

    return ToolDefinition(
        name="probe",
        label="probe",
        description="",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        execute=execute,
        effects=effects,
        policy_context=lambda args: {"action": "probe", "target": args.get("path")},
    )


def _two_turn_provider(arguments: dict[str, object]):
    calls = 0

    def script(model, _context):
        nonlocal calls
        calls += 1
        if calls == 1:
            return tool_call_response_events(model, "probe", arguments, call_id="call-policy")
        return text_response_events(model, "done")

    return create_faux_provider(script)


def test_policy_observes_extension_mutated_arguments(tmp_path) -> None:
    executions: list[dict[str, object]] = []
    broker = Broker()
    runner = ExtensionRunner()
    decisions: list[dict[str, object]] = []

    def mutate(event):
        event["input"]["path"] = "after"

    runner.on("tool_call", mutate)
    register_api_provider(_two_turn_provider({"path": "before"}))
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        tool_definitions=[_tool(executions)],
        extension_runner=runner,
        settings_manager=_settings("enforce"),
        tool_approval_broker=broker,
        tool_policy_event_sink=decisions.append,
    )

    session.prompt("run")

    assert executions == [{"path": "after"}]
    assert broker.requests[0].argument_fingerprint == argument_fingerprint({"path": "after"})
    assert broker.requests[0].safe_context == {"action": "probe", "target": "after"}
    assert decisions == [
        {
            "type": "tool_policy_decision",
            "tool": "probe",
            "effects": ["write"],
            "mode": "enforce",
            "allow": True,
            "reason_code": "approval_required",
        }
    ]


def test_extension_denial_short_circuits_policy(tmp_path) -> None:
    executions: list[dict[str, object]] = []
    broker = Broker()
    runner = ExtensionRunner()
    decisions: list[dict[str, object]] = []
    runner.on("tool_call", lambda _event: {"block": True, "reason": "extension denied"})
    register_api_provider(_two_turn_provider({"path": "blocked"}))
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        tool_definitions=[_tool(executions)],
        extension_runner=runner,
        settings_manager=_settings("enforce"),
        tool_approval_broker=broker,
        tool_policy_event_sink=decisions.append,
    )

    session.prompt("run")

    assert executions == []
    assert broker.requests == []
    assert decisions == []


def test_audit_mode_preserves_exact_result_when_decision_sink_fails(tmp_path) -> None:
    executions: list[dict[str, object]] = []
    register_api_provider(_two_turn_provider({"path": "audit"}))

    def failing_sink(_event):
        raise RuntimeError("diagnostic unavailable")

    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        tool_definitions=[_tool(executions)],
        settings_manager=_settings("audit"),
        tool_policy_event_sink=failing_sink,
    )

    session.prompt("run")

    result = next(message for message in session.messages if isinstance(message, ToolResultMessage))
    assert executions == [{"path": "audit"}]
    assert result.content == [TextContent(text="executed:audit")]
    assert result.details == {"stable": True}
    assert result.is_error is False


def test_enforce_denial_never_calls_execute_and_emits_sanitized_event(tmp_path) -> None:
    executions: list[dict[str, object]] = []
    decisions: list[dict[str, object]] = []
    register_api_provider(_two_turn_provider({"path": "credential-value-never-render"}))
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        tool_definitions=[_tool(executions)],
        settings_manager=_settings("enforce"),
        tool_policy_event_sink=decisions.append,
    )

    session.prompt("run")

    result = next(message for message in session.messages if isinstance(message, ToolResultMessage))
    assert executions == []
    assert result.is_error is True
    assert "approval_unavailable" in result.content[0].text
    assert "credential-value" not in repr(decisions)
    assert decisions[0]["allow"] is False
    assert decisions[0]["reason_code"] == "approval_unavailable"
