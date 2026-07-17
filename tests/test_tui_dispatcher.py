from __future__ import annotations

import threading

import pytest

from travis.tui.dispatcher import UiDispatcher


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_dispatcher_serializes_and_coalesces_render_burst() -> None:
    clock = FakeClock()
    active = 0
    max_active = 0
    renders: list[bool] = []

    def render(force: bool = False) -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        renders.append(force)
        active -= 1

    dispatcher = UiDispatcher(render=render, clock=clock, render_interval=0.016)
    threads = [
        threading.Thread(target=lambda: [dispatcher.request_render() for _ in range(100)])
        for _ in range(20)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)

    dispatcher.drain()

    assert max_active == 1
    assert renders == [False]


def test_dispatcher_force_dominates_queued_normal_render() -> None:
    clock = FakeClock()
    renders: list[bool] = []
    dispatcher = UiDispatcher(render=lambda force=False: renders.append(force), clock=clock)

    producer = threading.Thread(
        target=lambda: (dispatcher.request_render(), dispatcher.request_render(force=True))
    )
    producer.start()
    producer.join(timeout=1)
    dispatcher.drain()

    assert renders == [True]


def test_dispatcher_waits_for_interval_before_second_render() -> None:
    clock = FakeClock()
    renders: list[bool] = []
    dispatcher = UiDispatcher(render=lambda force=False: renders.append(force), clock=clock, render_interval=0.016)

    dispatcher.request_render()
    dispatcher.request_render()
    assert renders == [False]

    clock.advance(0.015)
    dispatcher.drain()
    assert renders == [False]
    assert dispatcher.time_until_next_work(1.0) == pytest.approx(0.001)

    clock.advance(0.001)
    dispatcher.drain()
    assert renders == [False, False]


def test_dispatcher_posts_state_callbacks_on_owner_thread() -> None:
    dispatcher = UiDispatcher(render=lambda force=False: None)
    owner = threading.get_ident()
    observed: list[int] = []
    producer = threading.Thread(target=lambda: dispatcher.post(lambda: observed.append(threading.get_ident())))
    producer.start()
    producer.join(timeout=1)

    assert observed == []
    assert dispatcher.drain() == 1
    assert observed == [owner]


def test_dispatcher_does_not_reenter_drain_when_callback_requests_render() -> None:
    dispatcher = UiDispatcher(render=lambda force=False: None, render_interval=0)
    observed: list[int] = []
    for index in range(2000):
        dispatcher.post(lambda index=index: (observed.append(index), dispatcher.request_render()))

    applied = dispatcher.drain()

    assert applied == 2000
    assert observed == list(range(2000))


def test_dispatcher_explicit_nested_drain_services_modal_input() -> None:
    dispatcher = UiDispatcher(render=lambda force=False: None)
    observed: list[str] = []

    def open_modal() -> None:
        dispatcher.post(lambda: observed.append("modal input"))
        assert dispatcher.drain() == 1
        assert observed == ["modal input"]

    dispatcher.post(open_modal)

    assert dispatcher.drain() == 1


def test_dispatcher_runs_scheduled_callbacks_at_deadline_in_insertion_order() -> None:
    clock = FakeClock()
    observed: list[str] = []
    dispatcher = UiDispatcher(render=lambda force=False: None, clock=clock)
    dispatcher.call_later(0.25, lambda: observed.append("first"))
    dispatcher.call_later(0.25, lambda: observed.append("second"))

    assert dispatcher.time_until_next_work(1.0) == pytest.approx(0.25)
    assert dispatcher.drain() == 0
    assert observed == []

    clock.advance(0.25)

    assert dispatcher.drain() == 2
    assert observed == ["first", "second"]


def test_dispatcher_cancelled_scheduled_callback_is_idempotent() -> None:
    clock = FakeClock()
    observed: list[str] = []
    dispatcher = UiDispatcher(render=lambda force=False: None, clock=clock)
    handle = dispatcher.call_later(0.1, lambda: observed.append("late"))

    handle.cancel()
    handle.cancel()
    clock.advance(0.1)

    assert dispatcher.drain() == 0
    assert observed == []
    assert dispatcher.time_until_next_work(0.5) == pytest.approx(0.5)


def test_scheduled_callback_can_request_one_coalesced_render() -> None:
    clock = FakeClock()
    renders: list[bool] = []
    dispatcher = UiDispatcher(
        render=lambda force=False: renders.append(force),
        clock=clock,
        render_interval=0,
    )
    dispatcher.call_later(0.1, lambda: dispatcher.request_render())
    clock.advance(0.1)

    dispatcher.drain()

    assert renders == [False]


def test_scheduled_callbacks_run_on_the_owner_thread() -> None:
    clock = FakeClock()
    owner = threading.get_ident()
    observed: list[int] = []
    dispatcher = UiDispatcher(render=lambda force=False: None, clock=clock)
    dispatcher.call_later(0.1, lambda: observed.append(threading.get_ident()))
    clock.advance(0.1)

    dispatcher.drain()

    assert observed == [owner]


def test_dispatcher_rejects_drain_from_non_owner() -> None:
    dispatcher = UiDispatcher(render=lambda force=False: None)
    errors: list[BaseException] = []
    thread = threading.Thread(target=lambda: _capture_error(dispatcher.drain, errors))
    thread.start()
    thread.join(timeout=1)

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)


def _capture_error(callback, errors: list[BaseException]) -> None:
    try:
        callback()
    except BaseException as error:  # noqa: BLE001
        errors.append(error)
