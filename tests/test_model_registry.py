from __future__ import annotations

from dataclasses import replace

from travis.ai.types import Model
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.model_registry import ModelRegistry


def _model(provider: str, model_id: str, *, name: str | None = None) -> Model:
    return Model(
        id=model_id,
        name=name or model_id,
        api="openai-completions",
        provider=provider,
        base_url="https://example.invalid",
    )


def test_registry_public_model_operations() -> None:
    registry = ModelRegistry.in_memory(AuthStorage.in_memory())
    registry.replace_all([])
    first = _model("p", "m", name="First")
    second = replace(first, name="Second")

    assert registry.register_model(first) is True
    assert registry.register_model(first) is False
    assert registry.snapshot() == (first,)
    assert registry.replace_model(second) is first
    assert registry.snapshot() == (second,)
    assert registry.remove_model("p", "m") is second
    assert registry.snapshot() == ()


def test_replace_all_copies_input_and_preserves_order() -> None:
    registry = ModelRegistry.in_memory(AuthStorage.in_memory())
    models = [_model("p", "a"), _model("p", "b")]

    registry.replace_all(models)
    models.clear()

    assert [model.id for model in registry.snapshot()] == ["a", "b"]
