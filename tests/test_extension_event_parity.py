from __future__ import annotations

import asyncio
from pathlib import Path

from tests._provider_runtime import register_api_provider
from travis.ai.model_resolver import ScopedModel
from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.ai.types import ImageContent, TextContent
from travis.coding_agent import AgentSession
from travis.coding_agent.extensions import (
    PINNED_PI_EXTENSION_EVENTS,
    ExtensionRunner,
)
from travis.coding_agent.event_bus import create_event_bus
from travis.coding_agent.resource_loader import DefaultResourceLoader


def test_extension_runner_declares_all_pinned_pi_events() -> None:
    assert set(ExtensionRunner.supported_event_types()) == set(PINNED_PI_EXTENSION_EVENTS)


def test_session_state_owners_emit_missing_pi_selection_events(tmp_path: Path) -> None:
    runner = ExtensionRunner(cwd=str(tmp_path))
    events: list[dict[str, object]] = []
    for event_type in (
        "session_info_changed",
        "model_select",
        "thinking_level_select",
    ):
        runner.on(event_type, lambda event, _ctx, events=events: events.append(dict(event)))
    initial = faux_model()
    initial.reasoning = True
    selected = faux_model()
    selected.id = "selected"
    selected.reasoning = True
    session = AgentSession(
        cwd=str(tmp_path),
        model=initial,
        extension_runner=runner,
    )

    session.set_session_name("release work")
    session.set_model(selected)
    session.set_thinking_level("medium")

    assert [event["type"] for event in events] == [
        "session_info_changed",
        "model_select",
        "thinking_level_select",
    ]
    assert events[0]["previousName"] is None
    assert events[0]["name"] == "release work"
    assert events[1]["previousModel"].id == initial.id
    assert events[1]["model"].id == selected.id
    assert events[2]["previousThinkingLevel"] == "off"
    assert events[2]["thinkingLevel"] == "medium"
    assert events[2]["previousLevel"] == "off"
    assert events[2]["level"] == "medium"
    assert events[1]["source"] == "set"


def test_model_select_reports_cycle_and_restore_sources(tmp_path: Path) -> None:
    register_api_provider(
        create_faux_provider(lambda model, context: text_response_events(model, "unused"))
    )
    runner = ExtensionRunner(cwd=str(tmp_path))
    events: list[dict[str, object]] = []
    runner.on("model_select", lambda event, _ctx: events.append(dict(event)))
    first = faux_model()
    first.id = "first"
    second = faux_model()
    second.id = "second"
    restored = faux_model()
    restored.id = "restored"
    session = AgentSession(
        cwd=str(tmp_path),
        model=first,
        extension_runner=runner,
        scoped_models=[ScopedModel(model=first), ScopedModel(model=second)],
    )

    session.cycle_model()
    session.set_model(restored, source="restore")

    assert [event["source"] for event in events] == ["cycle", "restore"]


def test_input_returns_continue_when_unchanged() -> None:
    runner = ExtensionRunner()
    runner.on("input", lambda _event, _ctx: None)

    assert runner.emit_input("unchanged") == {"action": "continue"}


def test_prompt_propagates_rpc_input_source_without_running_model(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    seen: list[dict[str, object]] = []
    runner.on("input", lambda event, _ctx: seen.append(dict(event)))
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)
    session.agent.state.is_streaming = True

    session.prompt("queued", streaming_behavior="steer", input_source="rpc")

    assert seen[0]["source"] == "rpc"
    assert session.get_steering_messages() == ["queued"]


def test_before_agent_start_context_sees_chained_system_prompt() -> None:
    runner = ExtensionRunner()
    seen: list[tuple[str, str]] = []
    runner.bind_core({}, {"getSystemPrompt": lambda: "session prompt"})

    def first(event, context):
        seen.append((event["systemPrompt"], context.get_system_prompt()))
        return {"systemPrompt": "first replacement"}

    def second(event, context):
        seen.append((event["systemPrompt"], context.get_system_prompt()))
        return {"systemPrompt": "second replacement"}

    runner.on("before_agent_start", first)
    runner.on("before_agent_start", second)

    result = runner.emit_before_agent_start("hello", None, "initial prompt")

    assert seen == [
        ("initial prompt", "initial prompt"),
        ("first replacement", "first replacement"),
    ]
    assert result == {"systemPrompt": "second replacement"}


def test_system_prompt_override_is_scoped_only_to_before_agent_start() -> None:
    runner = ExtensionRunner()
    seen: list[tuple[str, str]] = []
    runner.bind_core({}, {"getSystemPrompt": lambda: "session prompt"})
    runner.on(
        "message_end",
        lambda _event, context: seen.append(("message_end", context.get_system_prompt())),
    )
    runner.on(
        "tool_result",
        lambda _event, context: seen.append(("tool_result", context.get_system_prompt())),
    )

    runner.emit_message_end({"type": "message_end", "message": object()})
    runner.emit_tool_result(
        {
            "type": "tool_result",
            "content": [],
            "details": None,
            "isError": False,
        }
    )

    assert seen == [
        ("message_end", "session prompt"),
        ("tool_result", "session prompt"),
    ]


def test_extension_user_message_preserves_images_and_skips_command_expansion(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    input_events: list[dict[str, object]] = []
    command_calls: list[str] = []
    runner.on("input", lambda event, _ctx: input_events.append(dict(event)))
    runner.register_command(
        "probe",
        {"handler": lambda args, _ctx: command_calls.append(args)},
    )
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), extension_runner=runner)
    session.agent.state.is_streaming = True
    image = ImageContent(data="aW1hZ2U=", mime_type="image/png")

    runner.send_user_message(
        [TextContent(text="/probe payload"), image],
        {"deliverAs": "steer"},
    )

    assert command_calls == []
    assert input_events[0]["source"] == "extension"
    assert input_events[0]["images"] == [image]
    assert session.get_steering_messages() == ["/probe payload"]


def test_duplicate_commands_receive_stable_invocation_names() -> None:
    runner = ExtensionRunner()
    first = lambda *_args: "first"
    second = lambda *_args: "second"
    third = lambda *_args: "third"

    runner.register_command("review", {"handler": first})
    runner.register_command("review", {"handler": second})
    runner.register_command("review", {"handler": third})

    assert [command.name for command in runner.get_all_registered_commands()] == [
        "review",
        "review:1",
        "review:2",
    ]
    assert runner.get_registered_command("review").handler is first
    assert runner.get_registered_command("review:1").handler is second
    assert runner.get_registered_command("review:2").handler is third


def test_shared_event_bus_detaches_discarded_runtime_subscriptions(tmp_path: Path) -> None:
    seen: list[str] = []

    def receiver(travis: ExtensionRunner) -> None:
        travis.events.on("parity-ping", lambda value: seen.append(str(value)))

    def publisher(travis: ExtensionRunner) -> None:
        travis.register_command(
            "publish",
            {"handler": lambda *_args: travis.events.emit("parity-ping", "received")},
        )

    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        project_trusted=True,
        extension_factories=[receiver, publisher],
    )
    loader.reload()
    first_runtime = loader.get_extensions()["runtime"]
    first_runtime.get_registered_command("publish").handler()

    loader.reload()
    second_runtime = loader.get_extensions()["runtime"]
    second_runtime.get_registered_command("publish").handler()

    assert first_runtime.events is second_runtime.events
    assert seen == ["received", "received"]


def test_event_bus_observes_async_handler_failure(capsys) -> None:
    bus = create_event_bus()

    async def explode(_value) -> None:
        await asyncio.sleep(0)
        raise RuntimeError("async bus probe")

    async def scenario() -> None:
        bus.on("probe", explode)
        bus.emit("probe", None)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(scenario())

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Event handler error (probe): async bus probe" in captured.err
