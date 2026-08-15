from __future__ import annotations

import json

import pytest

from travis.ai.model_resolver import ScopedModel
from travis.ai.types import Model
from travis.coding_agent.model_roles import ModelRoleRouter


def _model(
    provider: str,
    model_id: str,
    *,
    inputs: tuple[str, ...] = ("text",),
    reasoning: bool = True,
) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider=provider,
        base_url="https://example.invalid/v1",
        reasoning=reasoning,
        input=list(inputs),
        context_window=128_000,
        max_tokens=8_192,
        headers={"Authorization": "secret-test-key"},
    )


class _Registry:
    def __init__(
        self,
        models: list[Model],
        *,
        selectable: list[Model] | None = None,
    ) -> None:
        self.models = list(models)
        self.selectable = {
            (model.provider, model.id)
            for model in (self.models if selectable is None else selectable)
        }

    def get_all(self) -> list[Model]:
        return list(self.models)

    def is_selectable(self, model: Model) -> bool:
        return (model.provider, model.id) in self.selectable


class _Settings:
    def __init__(
        self,
        roles: dict[str, str] | None = None,
        sources: dict[str, str] | None = None,
    ) -> None:
        self.roles = dict(roles or {})
        self.sources = dict(sources or {})

    def get_model_role(self, role: str) -> str | None:
        return self.roles.get(role)

    def get_model_role_source(self, role: str) -> str | None:
        return self.sources.get(role)


def test_explicit_override_wins_and_emits_sanitized_trace() -> None:
    primary = _model("primary", "main")
    worker = _model("worker", "cheap")
    override = _model("override", "focused")
    events: list[dict[str, object]] = []
    router = ModelRoleRouter(
        _Registry([primary, worker, override]),
        _Settings({"worker": "worker/cheap"}, {"worker": "global"}),
        ScopedModel(primary, "medium"),
        event_sink=events.append,
    )

    result = router.resolve("worker", override=ScopedModel(override, "high"))

    assert result.available is True
    assert result.scoped_model == ScopedModel(override, "high")
    assert result.source == "call_override"
    assert result.selected_role == "worker"
    assert result.fallback_trace[-1].outcome == "selected"
    assert events == [result.as_event()]
    assert "secret-test-key" not in json.dumps(events)


def test_session_binding_precedes_settings_and_returns_a_defensive_scope() -> None:
    primary = _model("primary", "main")
    bound = _model("roles", "bound")
    configured = _model("roles", "configured")
    router = ModelRoleRouter(
        _Registry([primary, bound, configured]),
        _Settings({"worker": "roles/configured"}, {"worker": "project"}),
        ScopedModel(primary, "off"),
        session_bindings={"worker": ScopedModel(bound, "low")},
    )

    first = router.resolve("worker")
    assert first.scoped_model == ScopedModel(bound, "low")
    assert first.source == "session"

    assert first.scoped_model is not None
    first.scoped_model.thinking_level = "high"
    assert router.resolve("worker").scoped_model == ScopedModel(bound, "low")


def test_reviewer_falls_back_to_configured_worker_before_primary() -> None:
    primary = _model("primary", "main")
    worker = _model("worker", "cheap")
    router = ModelRoleRouter(
        _Registry([primary, worker]),
        _Settings({"worker": "worker/cheap:low"}, {"worker": "project"}),
        ScopedModel(primary, "medium"),
    )

    result = router.resolve("reviewer")

    assert result.selected_role == "worker"
    assert result.source == "project"
    assert result.scoped_model == ScopedModel(worker, "low")
    assert [(step.role, step.outcome) for step in result.fallback_trace] == [
        ("reviewer", "missing"),
        ("worker", "selected"),
    ]


def test_active_primary_switch_updates_only_implicit_fallback() -> None:
    first = _model("primary", "first")
    second = _model("primary", "second")
    explicit_worker = _model("worker", "fixed")
    router = ModelRoleRouter(
        _Registry([first, second, explicit_worker]),
        _Settings({"worker": "worker/fixed"}, {"worker": "global"}),
        ScopedModel(first, "low"),
    )

    before = router.resolve("compression")
    router.set_primary(second, "high")
    after = router.resolve("compression")
    worker = router.resolve("worker")

    assert before.scoped_model == ScopedModel(first, "low")
    assert before.source == "active_primary"
    assert after.scoped_model == ScopedModel(second, "high")
    assert worker.scoped_model == ScopedModel(explicit_worker, None)


def test_vision_rejects_incompatible_candidates_and_returns_unavailable() -> None:
    text_primary = _model("primary", "text-only")
    text_vision = _model("configured", "also-text")
    router = ModelRoleRouter(
        _Registry([text_primary, text_vision]),
        _Settings({"vision": "configured/also-text"}, {"vision": "global"}),
        ScopedModel(text_primary, "off"),
    )

    result = router.resolve("vision")

    assert result.available is False
    assert result.scoped_model is None
    assert result.source == "unavailable"
    assert [step.outcome for step in result.fallback_trace] == [
        "incompatible",
        "incompatible",
    ]


def test_unselectable_configured_model_falls_back_to_primary() -> None:
    primary = _model("primary", "main")
    worker = _model("roles", "worker")
    router = ModelRoleRouter(
        _Registry([primary, worker], selectable=[primary]),
        _Settings({"worker": "roles/worker"}, {"worker": "global"}),
        ScopedModel(primary, "medium"),
    )

    result = router.resolve("worker")

    assert result.scoped_model == ScopedModel(primary, "medium")
    assert result.source == "active_primary"
    assert [step.outcome for step in result.fallback_trace] == [
        "unavailable",
        "selected",
    ]


def test_selector_preserves_literal_colon_id_before_parsing_thinking_suffix() -> None:
    primary = _model("primary", "main")
    literal = _model("openrouter", "qwen/qwen3-coder:exacto")
    settings = _Settings(
        {"worker": "openrouter/qwen/qwen3-coder:exacto"},
        {"worker": "global"},
    )
    router = ModelRoleRouter(
        _Registry([primary, literal]),
        settings,
        ScopedModel(primary, "off"),
    )

    exact = router.resolve("worker")
    settings.roles["worker"] = "openrouter/qwen/qwen3-coder:exacto:high"
    with_thinking = router.resolve("worker")

    assert exact.scoped_model == ScopedModel(literal, None)
    assert with_thinking.scoped_model == ScopedModel(literal, "high")


def test_missing_selector_is_traced_before_primary_fallback() -> None:
    primary = _model("primary", "main")
    router = ModelRoleRouter(
        _Registry([primary]),
        _Settings(),
        ScopedModel(primary, "off"),
    )

    result = router.resolve("compression")

    assert [(step.role, step.source, step.outcome) for step in result.fallback_trace] == [
        ("compression", "settings", "missing"),
        ("primary", "active_primary", "selected"),
    ]


def test_selector_override_is_catalog_checked() -> None:
    primary = _model("primary", "main")
    worker = _model("roles", "worker")
    router = ModelRoleRouter(
        _Registry([primary, worker], selectable=[primary]),
        _Settings(),
        ScopedModel(primary, "off"),
    )

    result = router.resolve("worker", selector_override="roles/worker:high")

    assert result.scoped_model == ScopedModel(primary, "off")
    assert result.fallback_trace[0].source == "call_override"
    assert result.fallback_trace[0].outcome == "unavailable"


def test_required_inputs_apply_to_trusted_bindings() -> None:
    primary = _model("primary", "text-only")
    bound = _model("roles", "also-text")
    router = ModelRoleRouter(
        _Registry([primary, bound]),
        _Settings(),
        ScopedModel(primary, "off"),
        session_bindings={"vision": ScopedModel(bound, "low")},
    )

    result = router.resolve("vision")

    assert result.available is False
    assert [step.source for step in result.fallback_trace] == [
        "session",
        "active_primary",
    ]
    assert all(step.outcome == "incompatible" for step in result.fallback_trace)


def test_router_rejects_unknown_roles_and_conflicting_overrides() -> None:
    primary = _model("primary", "main")
    router = ModelRoleRouter(
        _Registry([primary]),
        _Settings(),
        ScopedModel(primary, "off"),
    )

    with pytest.raises(ValueError, match="Unknown model role"):
        router.resolve("unknown")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="only one model override"):
        router.resolve(
            "worker",
            override=ScopedModel(primary, "off"),
            selector_override="primary/main",
        )
