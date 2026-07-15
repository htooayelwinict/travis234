from __future__ import annotations

from pathlib import Path

from travis.ai.providers.faux import faux_model
from travis.coding_agent import AgentSession
from travis.coding_agent.extensions import (
    PINNED_PI_EXTENSION_EVENTS,
    ExtensionRunner,
)
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
