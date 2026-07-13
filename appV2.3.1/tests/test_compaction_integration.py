from __future__ import annotations

import threading
from pathlib import Path

from appv231.ai.event_stream import create_assistant_message_event_stream
from appv231.ai.providers.faux import faux_model
from appv231.ai.providers.faux import text_response_events
from appv231.ai.types import UserMessage, now_ms
from appv231.app import CodingApp
from appv231.coding_agent import AgentSession
from appv231.compaction import CompactionManager, ContextCompressor


def _large_messages(prefix: str, count: int = 12) -> list[UserMessage]:
    return [
        UserMessage(content=f"{prefix} message {index} " + ("x" * 80), timestamp=now_ms() + index)
        for index in range(count)
    ]


def _session_with_compaction(path: Path, prompts: list[str]) -> AgentSession:
    def summarize(prompt: str) -> str:
        prompts.append(prompt)
        return f"summary-{len(prompts)}"

    return AgentSession(
        cwd=str(path.parent),
        model=faux_model(),
        session_path=str(path),
        compaction_manager=CompactionManager(
            ContextCompressor(context_length=40, protect_first_n=1, protect_last_n=1),
            summarizer=summarize,
        ),
    )


def _append_messages(session: AgentSession, messages: list[UserMessage]) -> None:
    session.agent.state.messages.extend(messages)
    assert session._session_store is not None
    for message in messages:
        session._session_store.append_message(message)


def test_second_persisted_compaction_receives_previous_summary(tmp_path: Path) -> None:
    prompts: list[str] = []
    session_path = tmp_path / "session.jsonl"
    first = _session_with_compaction(session_path, prompts)
    _append_messages(first, _large_messages("first"))

    first_status = first.compact()
    assert first_status.compressed is True
    assert first.messages[0].role == "compactionSummary"

    reloaded = _session_with_compaction(session_path, prompts)
    assert reloaded.messages[0].role == "compactionSummary"
    _append_messages(reloaded, _large_messages("second"))

    second_status = reloaded.compact()

    assert second_status.compressed is True
    assert len(prompts) == 2
    assert "summary-1" in prompts[1]


def test_automatic_preflight_compaction_receives_persisted_summary(tmp_path: Path) -> None:
    prompts: list[str] = []
    session_path = tmp_path / "auto-session.jsonl"

    def summarize(prompt: str) -> str:
        prompts.append(prompt)
        return f"auto-summary-{len(prompts)}"

    first = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        context_length=40,
        summarizer=summarize,
        enable_tui=False,
        session_path=str(session_path),
    )
    _append_messages(first.session, _large_messages("first-auto"))
    first._transform_context(first.session.messages)
    assert first.session.messages[0].role == "compactionSummary"

    reloaded = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        context_length=40,
        summarizer=summarize,
        enable_tui=False,
        session_path=str(session_path),
    )
    _append_messages(reloaded.session, _large_messages("second-auto"))
    reloaded._transform_context(reloaded.session.messages)

    assert len(prompts) == 2
    assert "auto-summary-1" in prompts[1]


def test_manual_compaction_aborts_and_waits_for_active_turn(tmp_path: Path) -> None:
    session = _session_with_compaction(tmp_path / "active.jsonl", [])
    _append_messages(session, _large_messages("seed"))
    stream_started = threading.Event()
    release_stream = threading.Event()

    def blocking_stream(model, context, options):
        stream = create_assistant_message_event_stream()
        events = text_response_events(model, "done")
        stream.push(type(events[0])(partial=events[0].partial))
        stream_started.set()

        def finish() -> None:
            release_stream.wait(timeout=2)
            for event in events[1:]:
                stream.push(event)

        threading.Thread(target=finish, daemon=True).start()
        return stream

    turn = threading.Thread(target=lambda: session.prompt("active", stream_fn=blocking_stream))
    turn.start()
    assert stream_started.wait(timeout=2)

    compact_error: list[BaseException] = []

    def compact() -> None:
        try:
            session.compact()
        except BaseException as error:  # noqa: BLE001
            compact_error.append(error)

    compaction = threading.Thread(target=compact)
    compaction.start()
    compaction.join(timeout=2)
    release_stream.set()
    turn.join(timeout=2)

    assert session.agent.signal.aborted is True
    assert not compaction.is_alive()
    assert not turn.is_alive()
    assert compact_error == []
