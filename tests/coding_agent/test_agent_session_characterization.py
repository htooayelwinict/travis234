from __future__ import annotations

from pathlib import Path

from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.ai.stream import register_api_provider, reset_api_providers
from travis.ai.types import AssistantMessage, TextContent, UserMessage
from travis.coding_agent.agent_session import AgentSession


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
    ]
    assert [entry["type"] for entry in session.session_entries] == ["message", "message"]
