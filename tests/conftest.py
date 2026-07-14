from __future__ import annotations

import pytest

from tests import _provider_runtime
from travis.agent.agent import Agent
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.model_registry import ModelRegistry


@pytest.fixture(autouse=True)
def isolated_model_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(tmp_path / "agent"))
    registry = ModelRegistry.in_memory(AuthStorage.in_memory())
    _provider_runtime.use_registry(registry)

    def create_registry(auth_storage, models_json_path=None, *, provider_config=None):
        # Direct AgentSession tests intentionally share the per-test fake runtime.
        # Factories given an on-disk model catalog retain production construction
        # semantics so auth, CLI, and SDK ownership boundaries are exercised.
        if models_json_path is None:
            return registry
        return ModelRegistry(
            auth_storage,
            models_json_path,
            provider_config=provider_config,
        )

    monkeypatch.setattr(ModelRegistry, "create", staticmethod(create_registry))

    original_ensure_model = ModelRegistry.ensure_model

    def ensure_model(self, model):
        original_ensure_model(self, model)
        _provider_runtime.bind_model(self, model)

    monkeypatch.setattr(ModelRegistry, "ensure_model", ensure_model)

    original_agent_init = Agent.__init__

    def agent_init(self, *args, **kwargs):
        kwargs.setdefault("stream_fn", _provider_runtime.stream_simple)
        original_agent_init(self, *args, **kwargs)

    monkeypatch.setattr(Agent, "__init__", agent_init)
    yield registry
    _provider_runtime.use_registry(None)
