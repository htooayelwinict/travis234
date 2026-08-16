from __future__ import annotations

import asyncio
import threading
import time

from tests._support_tui import CodingApp, FakeTerminal, faux_model
from travis.agent.types import AbortSignal
from travis.coding_agent.policy import ToolApprovalRequest
from travis.tui.interactive_mode import InteractiveMode
from travis.tui.interactive_tool_approval import InteractiveToolApprovalBroker


def _request(*, tool: str = "write", context: dict[str, str] | None = None) -> ToolApprovalRequest:
    return ToolApprovalRequest(
        tool_name=tool,
        effects=frozenset({"write"}),
        argument_fingerprint="a" * 64,
        safe_context=context or {"action": "write", "target": "safe.txt"},
        reason_code="approval_required",
    )


def _mode(tmp_path):
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), terminal=FakeTerminal(), enable_tui=True)
    return app, InteractiveMode(app)


def _start_request(broker, request=None, signal=None):
    result: dict[str, object] = {}
    pending_before = broker.pending_count

    def run() -> None:
        result["response"] = asyncio.run(broker.request(request or _request(), signal))

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 2
    while (
        broker.pending_count <= pending_before
        and thread.is_alive()
        and time.monotonic() < deadline
    ):
        time.sleep(0.005)
    return thread, result


def test_interactive_mode_injects_app_owned_broker_into_session(tmp_path) -> None:
    app, mode = _mode(tmp_path)

    assert isinstance(mode.tool_approval_broker, InteractiveToolApprovalBroker)
    assert app.session._tool_policy_engine.broker is mode.tool_approval_broker


def test_broker_renders_sanitized_request_and_maps_all_choices(tmp_path) -> None:
    _app, mode = _mode(tmp_path)
    broker = mode.tool_approval_broker
    seen: list[tuple[str, tuple[str, ...], int]] = []
    choices = iter(["allow once", "allow for session", "deny"])

    def select(title, options, dialog_options=None, *, kind="select"):
        seen.append((title, tuple(options), threading.get_ident()))
        return next(choices)

    mode._runtime.prompt_extension_select = select
    responses = []
    for _index in range(3):
        thread, result = _start_request(broker)
        mode.tui.drain_dispatcher()
        thread.join(timeout=2)
        assert not thread.is_alive()
        responses.append(result["response"].scope)

    assert responses == ["once", "session", "deny"]
    assert all(item[1] == ("allow once", "allow for session", "deny") for item in seen)
    assert all(item[2] == mode.tui.dispatcher.owner_thread_id for item in seen)
    rendered = "\n".join(item[0] for item in seen)
    assert "write" in rendered
    assert "safe.txt" in rendered
    assert "aaaaaaaaaaaa" in rendered
    assert "raw-argument-never-render" not in rendered


def test_simultaneous_requests_are_presented_in_arrival_order(tmp_path) -> None:
    _app, mode = _mode(tmp_path)
    broker = mode.tool_approval_broker
    presented: list[str] = []

    def select(title, options, dialog_options=None, *, kind="select"):
        presented.append(title)
        return "allow once"

    mode._runtime.prompt_extension_select = select
    first_thread, first = _start_request(broker, _request(tool="first"))
    second_thread, second = _start_request(broker, _request(tool="second"))

    mode.tui.drain_dispatcher()
    first_thread.join(timeout=2)
    second_thread.join(timeout=2)

    assert ["first" in presented[0], "second" in presented[1]] == [True, True]
    assert first["response"].scope == "once"
    assert second["response"].scope == "once"


def test_cancellation_before_display_returns_deny_without_ui(tmp_path) -> None:
    _app, mode = _mode(tmp_path)
    signal = AbortSignal()
    signal.abort()
    displayed: list[str] = []
    mode._runtime.prompt_extension_select = lambda title, *_args, **_kwargs: displayed.append(title)

    response = asyncio.run(mode.tool_approval_broker.request(_request(), signal))

    assert response.scope == "deny"
    assert displayed == []


def test_cancellation_while_displayed_closes_prompt_and_denies(tmp_path) -> None:
    _app, mode = _mode(tmp_path)
    broker = mode.tool_approval_broker
    signal = AbortSignal()
    displayed = threading.Event()

    def select(_title, _choices, options=None, *, kind="select"):
        displayed.set()
        cancel_event = options["cancelEvent"]
        assert cancel_event.wait(timeout=2)
        return None

    mode._runtime.prompt_extension_select = select
    thread, result = _start_request(broker, signal=signal)
    canceller = threading.Thread(target=lambda: (displayed.wait(timeout=2), signal.abort()))
    canceller.start()
    mode.tui.drain_dispatcher()
    thread.join(timeout=2)
    canceller.join(timeout=2)

    assert not thread.is_alive()
    assert result["response"].scope == "deny"


def test_child_context_is_labeled_without_goal_or_arguments(tmp_path) -> None:
    _app, mode = _mode(tmp_path)
    child = mode.tool_approval_broker.for_child("reviewer", "task-123")
    titles: list[str] = []
    mode._runtime.prompt_extension_select = (
        lambda title, *_args, **_kwargs: titles.append(title) or "deny"
    )

    thread, _result = _start_request(
        child,
        _request(context={"action": "write", "target": "review.txt"}),
    )
    mode.tui.drain_dispatcher()
    thread.join(timeout=2)

    assert "reviewer" in titles[0]
    assert "task-123" in titles[0]
    assert "goal" not in titles[0].lower()


def test_shutdown_denies_parent_and_child_requests_without_display(tmp_path) -> None:
    _app, mode = _mode(tmp_path)
    broker = mode.tool_approval_broker
    parent_thread, parent = _start_request(broker, _request(tool="parent"))
    child_thread, child = _start_request(
        broker.for_child("worker", "task-456"),
        _request(tool="child"),
    )

    broker.shutdown()
    parent_thread.join(timeout=2)
    child_thread.join(timeout=2)

    assert not parent_thread.is_alive()
    assert not child_thread.is_alive()
    assert parent["response"].scope == "deny"
    assert child["response"].scope == "deny"
    assert broker.pending_count == 0
