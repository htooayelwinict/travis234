from __future__ import annotations

from pathlib import Path

import pytest

from travis.ai.event_stream import create_assistant_message_event_stream
from travis.ai.providers.faux import text_response_events, tool_call_response_events
from travis.ai.types import ImageContent, Model
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.settings_manager import SettingsManager


def _model(provider: str, model_id: str, *, inputs: tuple[str, ...]) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider=provider,
        base_url="https://example.invalid/v1",
        reasoning=True,
        input=list(inputs),
        context_window=128_000,
        max_tokens=8_192,
    )


def _registry_for(*models: Model) -> ModelRegistry:
    auth = AuthStorage.in_memory()
    registry = ModelRegistry.in_memory(auth)
    for model in models:
        registry.ensure_model(model)
        auth.set_runtime_api_key(model.provider, "secret-test-key")
    return registry


def _stream_of(events):
    stream = create_assistant_message_event_stream()
    for event in events:
        stream.push(event)
    return stream


def _capturing_stream(calls, response):
    def stream(model, context, options=None):
        calls.append((model, options, context))
        return _stream_of(text_response_events(model, response))

    return stream


@pytest.fixture
def vision_session(tmp_path: Path):
    primary = _model("roles", "primary", inputs=("text",))
    vision = _model("roles", "vision", inputs=("text", "image"))
    text_only = _model("roles", "text-only", inputs=("text",))
    registry = _registry_for(primary, vision, text_only)
    session = AgentSession(
        cwd=str(tmp_path),
        model=primary,
        model_registry=registry,
        settings_manager=SettingsManager.in_memory(),
    )
    try:
        yield session, {"primary": primary, "vision": vision, "text_only": text_only}
    finally:
        session.shutdown()


def test_image_turn_uses_configured_vision_model_without_switching_primary(
    vision_session,
) -> None:
    session, models = vision_session
    calls = []
    session._stream_fn = _capturing_stream(calls, "vision response")
    session.settings_manager.set_model_role("vision", "roles/vision:high")

    session.prompt(
        "inspect",
        images=[ImageContent(data="aW1hZ2U=", mime_type="image/png")],
    )

    assert calls[0][0] is models["vision"]
    assert calls[0][1].reasoning == "high"
    assert session.model is models["primary"]
    assert session.thinking_level == "off"
    assert session.settings_manager.get_model_role("vision") == "roles/vision:high"


def test_text_only_primary_and_vision_setting_fail_before_provider_request(
    vision_session,
) -> None:
    session, _models = vision_session
    calls = []
    session._stream_fn = _capturing_stream(calls, "must not run")
    session.settings_manager.set_model_role("vision", "roles/text-only")

    with pytest.raises(RuntimeError, match="No image-capable model"):
        session.prompt(
            "inspect",
            images=[ImageContent(data="aW1hZ2U=", mime_type="image/png")],
        )

    assert calls == []


def test_image_capable_primary_is_the_vision_fallback(tmp_path: Path) -> None:
    primary = _model("roles", "multimodal", inputs=("text", "image"))
    session = AgentSession(
        cwd=str(tmp_path),
        model=primary,
        model_registry=_registry_for(primary),
        settings_manager=SettingsManager.in_memory(),
    )
    calls = []
    session._stream_fn = _capturing_stream(calls, "primary response")
    try:
        session.prompt(
            "inspect",
            images=[ImageContent(data="aW1hZ2U=", mime_type="image/png")],
        )

        assert calls[0][0] is primary
        assert session.model is primary
    finally:
        session.shutdown()


def test_plain_text_turn_does_not_resolve_vision_role(tmp_path: Path) -> None:
    primary = _model("roles", "primary", inputs=("text",))
    vision = _model("roles", "vision", inputs=("text", "image"))
    events: list[dict[str, object]] = []
    session = AgentSession(
        cwd=str(tmp_path),
        model=primary,
        model_registry=_registry_for(primary, vision),
        settings_manager=SettingsManager.in_memory(
            {"modelRoles": {"vision": "roles/vision"}}
        ),
        model_role_event_sink=events.append,
    )
    calls = []
    session._stream_fn = _capturing_stream(calls, "text response")
    try:
        session.prompt("plain text")

        assert calls[0][0] is primary
        assert events == []
    finally:
        session.shutdown()


def test_tool_continuation_stays_on_selected_vision_model(
    vision_session,
    tmp_path: Path,
) -> None:
    session, models = vision_session
    target = tmp_path / "note.txt"
    target.write_text("evidence", encoding="utf-8")
    calls = []

    def stream(model, context, options=None):
        calls.append((model, options, context))
        if len(calls) == 1:
            return _stream_of(
                tool_call_response_events(
                    model,
                    "read",
                    {"path": str(target)},
                    call_id="vision-read",
                )
            )
        return _stream_of(text_response_events(model, "vision complete"))

    session._stream_fn = stream
    session.settings_manager.set_model_role("vision", "roles/vision:low")

    session.prompt(
        "inspect",
        images=[ImageContent(data="aW1hZ2U=", mime_type="image/png")],
    )

    assert [call[0] for call in calls] == [models["vision"], models["vision"]]
    assert [call[1].reasoning for call in calls] == ["low", "low"]


def test_queued_steering_image_upgrades_next_provider_call(
    vision_session,
    tmp_path: Path,
) -> None:
    session, models = vision_session
    target = tmp_path / "note.txt"
    target.write_text("evidence", encoding="utf-8")
    calls = []

    def stream(model, context, options=None):
        calls.append((model, options, context))
        if len(calls) == 1:
            session.steer(
                "also inspect this image",
                [ImageContent(data="aW1hZ2U=", mime_type="image/png")],
            )
            return _stream_of(
                tool_call_response_events(
                    model,
                    "read",
                    {"path": str(target)},
                    call_id="steering-read",
                )
            )
        return _stream_of(text_response_events(model, "vision complete"))

    session._stream_fn = stream
    session.settings_manager.set_model_role("vision", "roles/vision:medium")

    session.prompt("begin with text")

    assert [call[0] for call in calls] == [models["primary"], models["vision"]]
    assert calls[1][1].reasoning == "medium"
    assert session.model is models["primary"]
