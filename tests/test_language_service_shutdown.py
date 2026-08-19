from __future__ import annotations

from types import SimpleNamespace

import pytest

from travis.coding_agent.agent_session_runtime import AgentSessionRuntime


class _Extensions:
    def has_handlers(self, _event: str) -> bool:
        return False


class _LanguageServices:
    def __init__(self, events: list[str], name: str) -> None:
        self.events = events
        self.name = name
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1
        self.events.append(f"{self.name}:language-services")


class _FailingLanguageServices(_LanguageServices):
    async def close(self) -> None:
        await super().close()
        raise RuntimeError("language-service close failed")


class _Session:
    def __init__(self, events: list[str], name: str, *, fail_dispose: bool = False) -> None:
        self.events = events
        self.name = name
        self.cwd = "/workspace"
        self.session_path = f"/tmp/{name}.jsonl"
        self.extension_runner = _Extensions()
        self._language_services = _LanguageServices(events, name)
        self.fail_dispose = fail_dispose

    def dispose(self) -> None:
        self.events.append(f"{self.name}:session")
        if self.fail_dispose:
            raise RuntimeError("dispose failed")

    def shutdown(self, *args: object, **kwargs: object) -> None:
        return None

    def create_branched_session(self, leaf_id: str, path: str | None = None) -> str:
        raise AssertionError("not used by language-service shutdown tests")

    def emit_deferred_session_start(self) -> None:
        self.events.append(f"{self.name}:start")

    def get_session_entry(self, entry_id: str) -> dict[str, object] | None:
        return None

    def get_session_leaf_id(self) -> str | None:
        return None


def test_runtime_dispose_closes_language_services_before_session() -> None:
    events: list[str] = []
    session = _Session(events, "current")
    runtime = AgentSessionRuntime(session, {}, lambda _options: session)

    runtime.dispose()

    assert events == ["current:language-services", "current:session"]
    assert session._language_services.close_calls == 1


def test_runtime_dispose_continues_session_cleanup_after_language_service_error() -> None:
    events: list[str] = []
    session = _Session(events, "current")
    session._language_services = _FailingLanguageServices(events, "current")
    runtime = AgentSessionRuntime(session, {}, lambda _options: session)

    with pytest.raises(RuntimeError, match="language-service close failed"):
        runtime.dispose()

    assert events == ["current:language-services", "current:session"]


def test_session_switch_closes_old_language_services_before_rebinding() -> None:
    events: list[str] = []
    current = _Session(events, "current")
    replacement = _Session(events, "replacement")
    runtime = AgentSessionRuntime(current, {}, lambda _options: {"session": replacement})
    runtime.set_rebind_session(lambda _session: events.append("rebind"))

    result = runtime.new_session({"session_path": replacement.session_path})

    assert result == {"cancelled": False}
    assert events == [
        "current:language-services",
        "current:session",
        "rebind",
        "replacement:start",
    ]
    assert replacement._language_services.close_calls == 0


def test_failed_switch_teardown_also_closes_replacement_language_services() -> None:
    events: list[str] = []
    current = _Session(events, "current", fail_dispose=True)
    replacement = _Session(events, "replacement")
    runtime = AgentSessionRuntime(current, {}, lambda _options: {"session": replacement})

    with pytest.raises(RuntimeError, match="dispose failed"):
        runtime.new_session({"session_path": replacement.session_path})

    assert events == [
        "current:language-services",
        "current:session",
        "replacement:language-services",
        "replacement:session",
    ]


def test_application_close_disposes_session_before_process_owner() -> None:
    from travis.app import CodingApp

    events: list[str] = []
    app = object.__new__(CodingApp)
    app._closed = False
    app._unbind_session = lambda: events.append("unbind")
    app.session_runtime = SimpleNamespace(dispose=lambda: events.append("session-runtime"))
    app.process_service = SimpleNamespace(close=lambda: events.append("process-service"))
    app.process_completion_store = SimpleNamespace(close=lambda: events.append("completion-store"))
    app.session_catalog = SimpleNamespace(close=lambda: events.append("session-catalog"))

    app.close()

    assert events.index("session-runtime") < events.index("process-service")
