from __future__ import annotations

import json
from pathlib import Path

from travis.ai.model_resolver import ScopedModel
from travis.ai.types import Model
from travis.app import CodingApp
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.agent_session_services import (
    create_agent_session_from_services,
    create_agent_session_services,
)
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.settings_manager import SettingsManager


def _model(provider: str, model_id: str) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider=provider,
        base_url="https://example.invalid/v1",
        reasoning=True,
        input=["text", "image"],
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


class _EventTrace:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object] | None]] = []

    def write(self, event_type: str, fields: dict[str, object] | None = None) -> None:
        self.events.append((event_type, fields))


def test_session_primary_route_tracks_model_and_thinking_switches(tmp_path: Path) -> None:
    first = _model("roles", "first")
    second = _model("roles", "second")
    registry = _registry_for(first, second)
    session = AgentSession(
        cwd=str(tmp_path),
        model=first,
        model_registry=registry,
        thinking_level="low",
    )
    try:
        assert session.resolve_model_role("primary").scoped_model == ScopedModel(first, "low")

        session.set_model(second)
        session.set_thinking_level("high")

        assert session.resolve_model_role("primary").scoped_model == ScopedModel(second, "high")
    finally:
        session.shutdown()


def test_session_binding_survives_primary_switch_without_rewriting_setting(tmp_path: Path) -> None:
    primary = _model("roles", "primary")
    second = _model("roles", "second")
    compression = _model("roles", "compression")
    registry = _registry_for(primary, second, compression)
    settings = SettingsManager.in_memory({"modelRoles": {"worker": "roles/primary"}})
    session = AgentSession(
        cwd=str(tmp_path),
        model=primary,
        model_registry=registry,
        settings_manager=settings,
        model_role_bindings={"compression": ScopedModel(compression, "off")},
    )
    try:
        session.set_model(second)

        assert session.resolve_model_role("compression").scoped_model == ScopedModel(
            compression,
            "off",
        )
        assert settings.get_model_role("compression") is None
    finally:
        session.shutdown()


def test_session_role_resolution_does_not_append_jsonl_entries(tmp_path: Path) -> None:
    model = _model("roles", "primary")
    registry = _registry_for(model)
    session_path = tmp_path / "session.jsonl"
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        model_registry=registry,
        session_path=str(session_path),
    )
    try:
        before = session_path.read_text(encoding="utf-8")
        session.resolve_model_role("worker")
        after = session_path.read_text(encoding="utf-8")
        assert after == before
    finally:
        session.shutdown()


def test_sdk_factory_threads_role_bindings_and_event_sink(tmp_path: Path) -> None:
    primary = _model("roles", "primary")
    worker = _model("roles", "worker")
    registry = _registry_for(primary, worker)
    events: list[dict[str, object]] = []
    services = create_agent_session_services(
        {
            "cwd": str(tmp_path),
            "agentDir": str(tmp_path / "agent"),
            "authStorage": registry.auth_storage,
            "modelRegistry": registry,
            "settingsManager": SettingsManager.in_memory(),
        }
    )
    result = create_agent_session_from_services(
        {
            "services": services,
            "model": primary,
            "modelRoleBindings": {"worker": ScopedModel(worker, "medium")},
            "modelRoleEventSink": events.append,
        }
    )
    try:
        resolution = result.session.resolve_model_role("worker")
        assert resolution.scoped_model == ScopedModel(worker, "medium")
        assert events == [resolution.as_event()]
        assert "secret-test-key" not in json.dumps(events)
    finally:
        result.session.shutdown()


def test_app_rebinds_independent_routers_with_the_same_binding_values(tmp_path: Path) -> None:
    primary = _model("roles", "primary")
    worker = _model("roles", "worker")
    registry = _registry_for(primary, worker)
    trace = _EventTrace()
    app = CodingApp(
        cwd=str(tmp_path),
        model=primary,
        model_registry=registry,
        settings_manager=SettingsManager.in_memory(),
        model_role_bindings={"worker": ScopedModel(worker, "low")},
        event_trace=trace,
        agent_dir=str(tmp_path / "agent"),
        enable_tui=False,
    )
    try:
        first_router = app.session.model_role_router
        first = app.session.resolve_model_role("worker")
        app.new_session()
        second = app.session.resolve_model_role("worker")

        assert app.session.model_role_router is not first_router
        assert first.scoped_model == ScopedModel(worker, "low")
        assert second.scoped_model == ScopedModel(worker, "low")
        role_events = [
            event
            for event in trace.events
            if event[0] == "model_role_resolved"
            and event[1] is not None
            and event[1].get("role") == "worker"
        ]
        assert len(role_events) == 2
        assert "secret-test-key" not in json.dumps(role_events)
    finally:
        app.close()
