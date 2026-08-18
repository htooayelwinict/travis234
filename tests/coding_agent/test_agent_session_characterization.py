from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from tests._provider_runtime import register_api_provider, reset_api_providers
from travis.ai.types import AssistantMessage, TextContent, UserMessage
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.agent_session_runtime import AgentSessionRuntime
from travis.coding_agent.extensions import ExtensionRunner


def setup_function() -> None:
    reset_api_providers()


def test_text_turn_event_message_and_jsonl_order_is_stable(tmp_path: Path) -> None:
    register_api_provider(create_faux_provider(lambda model, _context: text_response_events(model, "reply")))
    session = AgentSession(cwd=str(tmp_path), model=faux_model(), session_path=str(tmp_path / "session.jsonl"))
    events: list[str] = []
    session.subscribe(lambda event: events.append(event.type if hasattr(event, "type") else event["type"]))

    result = session.prompt("hello")

    assert [type(message) for message in result] == [UserMessage, AssistantMessage]
    assert [block.text for block in result[-1].content if isinstance(block, TextContent)] == ["reply"]
    assert events == [
        "agent_start",
        "turn_start",
        "message_start",
        "message_end",
        "message_start",
        "message_update",
        "message_update",
        "message_update",
        "message_update",
        "message_end",
            "turn_end",
            "agent_end",
            "agent_settled",
        ]
    assert [entry["type"] for entry in session.session_entries] == ["message", "message"]


class _CloseProbe:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class _LifecycleRuntimeProbe:
    def __init__(self) -> None:
        self.dispose_calls = 0
        self.shutdown_calls = 0
        self._memory_store = _CloseProbe()
        self.operation_coordinator = _CloseProbe()
        self.operation_runtime = _CloseProbe()
        self._owns_operation_runtime = True

    def dispose(self) -> None:
        self.dispose_calls += 1

    def shutdown(self) -> None:
        self.shutdown_calls += 1


@pytest.mark.parametrize("lifecycle_method", ["dispose", "shutdown"])
def test_agent_session_lifecycle_closes_each_optional_owner_once(
    lifecycle_method: str,
) -> None:
    runtime = _LifecycleRuntimeProbe()
    session = object.__new__(AgentSession)
    object.__setattr__(session, "_runtime", runtime)

    getattr(session, lifecycle_method)()

    assert runtime.dispose_calls == (1 if lifecycle_method == "dispose" else 0)
    assert runtime.shutdown_calls == (1 if lifecycle_method == "shutdown" else 0)
    assert runtime._memory_store.close_calls == 1
    assert runtime.operation_coordinator.close_calls == 1
    assert runtime.operation_runtime.close_calls == 1


def test_cancelled_session_replacement_retains_active_session(tmp_path: Path) -> None:
    extension_runner = ExtensionRunner()
    extension_runner.on("session_before_switch", lambda _event: {"cancel": True})
    active = SimpleNamespace(
        cwd=str(tmp_path),
        session_path=str(tmp_path / "active.jsonl"),
        extension_runner=extension_runner,
    )
    candidate_requests: list[dict[str, object]] = []
    runtime = AgentSessionRuntime(
        active,
        {"cwd": str(tmp_path), "agentDir": str(tmp_path / "agent")},
        lambda options: candidate_requests.append(options),
    )

    result = runtime.new_session({"session_path": str(tmp_path / "candidate.jsonl")})

    assert result == {"cancelled": True}
    assert runtime.session is active
    assert candidate_requests == []


def test_failed_session_replacement_retains_active_session(tmp_path: Path) -> None:
    active = SimpleNamespace(
        cwd=str(tmp_path),
        session_path=str(tmp_path / "active.jsonl"),
        extension_runner=ExtensionRunner(),
    )

    def fail_candidate(_options: dict[str, object]) -> object:
        raise RuntimeError("candidate construction failed")

    runtime = AgentSessionRuntime(
        active,
        {"cwd": str(tmp_path), "agentDir": str(tmp_path / "agent")},
        fail_candidate,
    )

    with pytest.raises(RuntimeError, match="candidate construction failed"):
        runtime.new_session({"session_path": str(tmp_path / "candidate.jsonl")})

    assert runtime.session is active
