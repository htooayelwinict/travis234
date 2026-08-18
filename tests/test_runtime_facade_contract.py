from __future__ import annotations

from travis.coding_agent.agent_session import AgentSession
from travis.runtime_facade import RuntimeFacade
from travis.tui.interactive_mode import InteractiveMode


class _Runtime:
    value = "runtime"

    def action(self) -> str:
        return "done"


class _Facade(RuntimeFacade):
    def __init__(self) -> None:
        object.__setattr__(self, "_runtime", _Runtime())


def test_runtime_facade_forwards_get_set_dir_and_overrides() -> None:
    facade = _Facade()

    assert facade.value == "runtime"
    assert facade.action() == "done"
    assert "action" in dir(facade)

    facade.value = "changed"
    facade.action = lambda: "override"

    assert facade._runtime.value == "changed"
    assert facade.action() == "override"


class _AgentSessionRuntimeProbe:
    def __init__(self) -> None:
        self.dispose_calls = 0
        self.shutdown_reasons: list[str] = []
        self._memory_store = None
        self.operation_coordinator = _CloseProbe()
        self._owns_operation_runtime = False

    def dispose(self) -> None:
        self.dispose_calls += 1

    def shutdown(self, reason: str = "quit") -> None:
        self.shutdown_reasons.append(reason)


class _CloseProbe:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


def test_agent_session_explicit_lifecycle_methods_delegate_to_runtime() -> None:
    runtime = _AgentSessionRuntimeProbe()
    facade = object.__new__(AgentSession)
    object.__setattr__(facade, "_runtime", runtime)

    facade.shutdown(reason="replacement")

    assert runtime.shutdown_reasons == ["replacement"]
    assert runtime.operation_coordinator.close_calls == 1


def test_interactive_mode_preserves_dynamic_runtime_overrides() -> None:
    runtime = _Runtime()
    facade = object.__new__(InteractiveMode)
    object.__setattr__(facade, "_runtime", runtime)

    assert facade.action() == "done"

    facade.action = lambda: "interactive override"

    assert runtime.action() == "interactive override"
    assert facade.action() == "interactive override"
