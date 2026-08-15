from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from tests._provider_runtime import register_api_provider, reset_api_providers
from travis.agent.types import AgentToolResult
from travis.ai.providers.faux import (
    create_faux_provider,
    faux_model,
    text_response_events,
    tool_call_response_events,
)
from travis.ai.providers._shared import blank_assistant_message
from travis.ai.types import (
    AssistantMessage,
    DoneEvent,
    StartEvent,
    TextContent,
    ToolCall,
    ToolResultMessage,
    ToolcallEndEvent,
    ToolcallStartEvent,
    empty_usage,
    now_ms,
)
from travis.coding_agent import AgentSession, ExtensionRunner, SettingsManager
from travis.coding_agent.operations import EffectHandle
from travis.coding_agent.policy import argument_fingerprint
from travis.coding_agent.tools.types import ToolDefinition


def setup_function() -> None:
    reset_api_providers()


@dataclass
class _Intent:
    handle: EffectHandle
    kind: str
    name: str
    fingerprint: str
    effect_classes: tuple[str, ...]


class _RecordingCoordinator:
    enabled = True

    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []
        self.intents: list[_Intent] = []
        self.settlements: list[tuple[EffectHandle, str]] = []
        self.fail_settlement = False
        self.closed = False

    def begin_effect(
        self,
        kind: str,
        name: str,
        fingerprint: str,
        effect_classes: tuple[str, ...] = (),
    ) -> EffectHandle:
        index = len(self.intents) + 1
        handle = EffectHandle("op_" + "a" * 32, f"effect_{index:032x}")
        self.intents.append(
            _Intent(handle, kind, name, fingerprint, effect_classes)
        )
        self.events.append("journal_intent")
        return handle

    def settle_effect(self, handle: EffectHandle, outcome_code: str) -> bool:
        self.events.append("journal_settlement")
        if self.fail_settlement:
            raise RuntimeError("journal settlement failed")
        self.settlements.append((handle, outcome_code))
        return True

    def close(self) -> None:
        self.closed = True


class _Runtime:
    def __init__(self, coordinator: _RecordingCoordinator) -> None:
        self.coordinator = coordinator

    def for_session(self, _session_id, *, diagnostic_sink=None):
        del diagnostic_sink
        return self.coordinator


def _settings(mode: str = "audit") -> SettingsManager:
    return SettingsManager.in_memory(
        {"toolPolicy": {"mode": mode, "autoAllowEffects": ["read"]}}
    )


def _tool(
    execute,
    *,
    name: str = "probe",
    effects: frozenset[str] = frozenset({"network", "write"}),
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        label=name,
        description="",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        execute=execute,
        effects=effects,  # type: ignore[arg-type]
    )


def _two_turn_provider(tool_name: str, arguments: dict[str, object]):
    calls = 0

    def script(model, _context):
        nonlocal calls
        calls += 1
        if calls == 1:
            return tool_call_response_events(
                model, tool_name, arguments, call_id="call-operation"
            )
        return text_response_events(model, "done")

    return create_faux_provider(script)


def _multi_tool_events(model, calls: list[tuple[str, str, dict[str, object]]]):
    partial = blank_assistant_message(model)
    partial.content = [
        ToolCall(id=call_id, name=name, arguments=args)
        for call_id, name, args in calls
    ]
    events: list[object] = [StartEvent(partial=partial)]
    for index, tool_call in enumerate(partial.content):
        events.append(ToolcallStartEvent(content_index=index, partial=partial))
        events.append(
            ToolcallEndEvent(
                content_index=index,
                tool_call=tool_call,
                partial=partial,
            )
        )
    final = AssistantMessage(
        content=[
            ToolCall(id=call_id, name=name, arguments=args)
            for call_id, name, args in calls
        ],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="toolUse",
        timestamp=now_ms(),
    )
    events.append(DoneEvent(reason="toolUse", message=final))
    return events


def test_tool_effect_exact_order_uses_mutated_arguments_and_sorted_effects(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    coordinator = _RecordingCoordinator(events)
    runner = ExtensionRunner()

    def mutate(event) -> None:
        events.append("extension_before")
        event["input"]["path"] = "after"

    def mutate_result(_event) -> None:
        events.append("extension_after")

    def execute(_call_id, args, *_args, **_kwargs):
        events.append("tool_execute")
        return AgentToolResult(
            content=[TextContent(text=f"executed:{args['path']}")], details={}
        )

    runner.on("tool_call", mutate)
    runner.on("tool_result", mutate_result)
    register_api_provider(_two_turn_provider("probe", {"path": "before"}))
    session_path = tmp_path / "session.jsonl"
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        tool_definitions=[_tool(execute)],
        extension_runner=runner,
        settings_manager=_settings(),
        tool_policy_event_sink=lambda event: events.append("policy_allow")
        if event["allow"]
        else None,
        operation_runtime=_Runtime(coordinator),
        session_path=str(session_path),
    )

    def observe(event) -> None:
        message = getattr(event, "message", None)
        if event.type == "message_end" and isinstance(message, ToolResultMessage):
            assert '"role":"toolResult"' in session_path.read_text(encoding="utf-8")
            events.append("result_persisted")

    session.subscribe(observe)
    session.prompt("run")

    assert events == [
        "extension_before",
        "policy_allow",
        "journal_intent",
        "tool_execute",
        "journal_settlement",
        "extension_after",
        "result_persisted",
    ]
    assert coordinator.intents[0].kind == "tool"
    assert coordinator.intents[0].name == "probe"
    assert coordinator.intents[0].fingerprint == argument_fingerprint(
        {"path": "after"}
    )
    assert coordinator.intents[0].effect_classes == ("write", "network")
    assert coordinator.settlements[0][1] == "ok"
    session.dispose()


@pytest.mark.parametrize("denial", ["extension", "policy"])
def test_denied_tool_call_creates_no_effect(tmp_path: Path, denial: str) -> None:
    coordinator = _RecordingCoordinator()
    runner = ExtensionRunner()
    if denial == "extension":
        runner.on("tool_call", lambda _event: {"block": True, "reason": "denied"})
    register_api_provider(_two_turn_provider("probe", {"path": "blocked"}))
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        tool_definitions=[_tool(lambda *_args, **_kwargs: None)],
        extension_runner=runner,
        settings_manager=_settings("enforce" if denial == "policy" else "audit"),
        operation_runtime=_Runtime(coordinator),
    )

    session.prompt("run")

    assert coordinator.intents == []
    assert coordinator.settlements == []
    session.dispose()


@pytest.mark.parametrize(
    ("behavior", "expected"),
    [("error", "tool_error"), ("cancel", "cancelled")],
)
def test_tool_failure_and_observed_cancellation_settle_with_bounded_outcome(
    tmp_path: Path, behavior: str, expected: str
) -> None:
    coordinator = _RecordingCoordinator()

    def execute(_call_id, _arguments, signal=None, *_args, **_kwargs):
        if behavior == "error":
            raise RuntimeError("tool failed")
        signal.abort()
        return AgentToolResult(content=[TextContent(text="cancelled")], details={})

    register_api_provider(_two_turn_provider("probe", {}))
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        tool_definitions=[_tool(execute)],
        settings_manager=_settings(),
        operation_runtime=_Runtime(coordinator),
    )

    session.prompt("run")

    assert coordinator.settlements[0][1] == expected
    session.dispose()


def test_parallel_tool_calls_keep_independent_handles_and_source_result_order(
    tmp_path: Path,
) -> None:
    coordinator = _RecordingCoordinator()
    calls = 0

    def script(model, _context):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _multi_tool_events(
                model,
                [
                    ("call-first", "probe", {"path": "first"}),
                    ("call-second", "probe", {"path": "second"}),
                ],
            )
        return text_response_events(model, "done")

    def execute(call_id, _arguments, *_args, **_kwargs):
        return AgentToolResult(content=[TextContent(text=call_id)], details={})

    register_api_provider(create_faux_provider(script))
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        tool_definitions=[_tool(execute)],
        settings_manager=_settings(),
        operation_runtime=_Runtime(coordinator),
    )

    session.prompt("run")

    assert len(coordinator.intents) == 2
    assert {item.handle for item in coordinator.intents} == {
        handle for handle, _outcome in coordinator.settlements
    }
    results = [
        message
        for message in session.messages
        if isinstance(message, ToolResultMessage)
    ]
    assert [message.tool_call_id for message in results] == [
        "call-first",
        "call-second",
    ]
    session.dispose()


def test_settlement_failure_does_not_change_tool_result_or_skip_extensions(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    coordinator = _RecordingCoordinator(events)
    coordinator.fail_settlement = True
    runner = ExtensionRunner()
    runner.on("tool_result", lambda _event: events.append("extension_after"))

    def execute(*_args, **_kwargs):
        return AgentToolResult(
            content=[TextContent(text="original")], details={"stable": True}
        )

    register_api_provider(_two_turn_provider("probe", {}))
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        tool_definitions=[_tool(execute)],
        extension_runner=runner,
        settings_manager=_settings(),
        operation_runtime=_Runtime(coordinator),
    )

    session.prompt("run")

    result = next(
        message for message in session.messages if isinstance(message, ToolResultMessage)
    )
    assert result.content == [TextContent(text="original")]
    assert result.details == {"stable": True}
    assert result.is_error is False
    assert events[-2:] == ["journal_settlement", "extension_after"]
    session.dispose()
