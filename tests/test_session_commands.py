from __future__ import annotations

import threading

import pytest

from travis.coding_agent.session_commands import SessionCommandExecutor


def test_session_commands_execute_in_submission_order_on_one_owner_thread() -> None:
    executor = SessionCommandExecutor()
    observed: list[tuple[str, str]] = []
    futures = [
        executor.submit(name, lambda name=name: observed.append((name, threading.current_thread().name)))
        for name in ["turn", "compact", "model"]
    ]

    for future in futures:
        future.result(timeout=1)

    assert [name for name, _thread in observed] == ["turn", "compact", "model"]
    assert len({thread for _name, thread in observed}) == 1
    executor.close()


def test_session_command_exception_propagates_and_worker_continues() -> None:
    executor = SessionCommandExecutor()

    def fail() -> None:
        raise RuntimeError("command failed")

    failed = executor.submit("fail", fail)
    succeeded = executor.submit("next", lambda: "ok")

    with pytest.raises(RuntimeError, match="command failed"):
        failed.result(timeout=1)
    assert succeeded.result(timeout=1) == "ok"
    executor.close()


def test_cancelled_session_command_does_not_run() -> None:
    executor = SessionCommandExecutor()
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []
    first = executor.submit("active", lambda: (started.set(), release.wait(timeout=1)))
    assert started.wait(timeout=1)
    cancelled = executor.submit("cancelled", lambda: calls.append("cancelled"))

    assert cancelled.cancel() is True
    release.set()
    first.result(timeout=1)
    executor.close()

    assert calls == []


def test_close_waits_for_active_command_and_rejects_new_work() -> None:
    executor = SessionCommandExecutor()
    started = threading.Event()
    release = threading.Event()
    active = executor.submit("active", lambda: (started.set(), release.wait(timeout=1)))
    assert started.wait(timeout=1)
    closed = threading.Event()
    closer = threading.Thread(target=lambda: (executor.close(), closed.set()))
    closer.start()

    assert closed.wait(timeout=0.05) is False
    release.set()
    closer.join(timeout=1)
    active.result(timeout=1)

    assert closed.is_set()
    with pytest.raises(RuntimeError, match="closed"):
        executor.submit("late", lambda: None).result(timeout=1)
