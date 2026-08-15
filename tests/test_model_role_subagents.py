from __future__ import annotations

from pathlib import Path

import pytest

from travis.ai.event_stream import create_assistant_message_event_stream
from travis.ai.providers.faux import text_response_events
from travis.ai.types import Model
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.settings_manager import SettingsManager
from travis.coding_agent.subagents import SubagentTask


def _model(provider: str, model_id: str, *, inputs: tuple[str, ...] = ("text",)) -> Model:
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


def _completed_stream(model, context, options=None):
    del context, options
    events = create_assistant_message_event_stream()
    for event in text_response_events(model, "child complete"):
        events.push(event)
    return events


def _capture_child_factory(parent: AgentSession, captured: dict[str, object]):
    def factory(**kwargs):
        captured.update(kwargs)
        kwargs.setdefault("model_registry", parent.model_registry)
        kwargs.setdefault("settings_manager", SettingsManager.in_memory())
        return AgentSession(**kwargs)

    return factory


@pytest.fixture
def routed_session(tmp_path: Path):
    primary = _model("roles", "primary")
    worker = _model("roles", "worker")
    review = _model("roles", "review")
    registry = _registry_for(primary, worker, review)
    session = AgentSession(
        cwd=str(tmp_path),
        model=primary,
        model_registry=registry,
        settings_manager=SettingsManager.in_memory(),
        stream_fn=_completed_stream,
        thinking_level="high",
    )
    try:
        yield session, {"primary": primary, "worker": worker, "review": review}
    finally:
        session.shutdown()


def test_internal_reviewer_uses_reviewer_role_model_and_thinking(routed_session) -> None:
    session, models = routed_session
    captured: dict[str, object] = {}
    session._session_factory = _capture_child_factory(session, captured)
    session.settings_manager.set_model_role("reviewer", "roles/review:high")
    task = SubagentTask(role="reviewer", goal="review", cwd=session.cwd)

    result = session._run_internal_subagent(task)

    assert result.status == "completed"
    assert captured["model"] is models["review"]
    assert captured["thinking_level"] == "high"


def test_internal_nonreviewer_uses_worker_and_explicit_reasoning_wins(routed_session) -> None:
    session, models = routed_session
    captured: dict[str, object] = {}
    session._session_factory = _capture_child_factory(session, captured)
    session.settings_manager.set_model_role("worker", "roles/worker:low")
    task = SubagentTask(
        role="explorer",
        goal="inspect",
        cwd=session.cwd,
        reasoning="medium",
    )

    result = session._run_internal_subagent(task)

    assert result.status == "completed"
    assert captured["model"] is models["worker"]
    assert captured["thinking_level"] == "medium"


def test_internal_reviewer_falls_back_to_worker_role(routed_session) -> None:
    session, models = routed_session
    captured: dict[str, object] = {}
    session._session_factory = _capture_child_factory(session, captured)
    session.settings_manager.set_model_role("worker", "roles/worker:low")

    result = session._run_internal_subagent(
        SubagentTask(role="reviewer", goal="review", cwd=session.cwd)
    )

    assert result.status == "completed"
    assert captured["model"] is models["worker"]
    assert captured["thinking_level"] == "low"


def test_trusted_task_model_selector_overrides_role_setting(routed_session) -> None:
    session, models = routed_session
    captured: dict[str, object] = {}
    session._session_factory = _capture_child_factory(session, captured)
    session.settings_manager.set_model_role("worker", "roles/worker:low")

    result = session._run_internal_subagent(
        SubagentTask(
            role="explorer",
            goal="inspect",
            cwd=session.cwd,
            model="roles/review:medium",
        )
    )

    assert result.status == "completed"
    assert captured["model"] is models["review"]
    assert captured["thinking_level"] == "medium"


def test_internal_worker_falls_back_to_active_primary(routed_session) -> None:
    session, models = routed_session
    captured: dict[str, object] = {}
    session._session_factory = _capture_child_factory(session, captured)

    result = session._run_internal_subagent(
        SubagentTask(role="explorer", goal="inspect", cwd=session.cwd)
    )

    assert result.status == "completed"
    assert captured["model"] is models["primary"]
    assert captured["thinking_level"] == "high"


def test_task_builder_preserves_missing_reasoning_for_role_resolution(routed_session) -> None:
    session, _models = routed_session

    task = session._build_subagent_task("reviewer", "inspect")

    assert task.reasoning is None


def test_unavailable_internal_route_returns_bounded_failure_without_child(tmp_path: Path) -> None:
    image_only = _model("roles", "image-only", inputs=("image",))
    registry = _registry_for(image_only)
    session = AgentSession(
        cwd=str(tmp_path),
        model=image_only,
        model_registry=registry,
        settings_manager=SettingsManager.in_memory(),
        stream_fn=_completed_stream,
    )
    called = False

    def unexpected_factory(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("child factory must not run")

    session._session_factory = unexpected_factory
    try:
        result = session._run_internal_subagent(
            SubagentTask(role="explorer", goal="inspect", cwd=session.cwd)
        )

        assert result.status == "failed"
        assert result.summary == "No text-capable model is available for the worker role."
        assert result.errors == ["model role unavailable: worker"]
        assert called is False
    finally:
        session.shutdown()
