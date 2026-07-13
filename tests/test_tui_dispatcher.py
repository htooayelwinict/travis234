from __future__ import annotations

import threading

import pytest

from travis.tui.dispatcher import UiDispatcher
from travis.tui.model_loader import ModelCatalogLoader


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


def test_dispatcher_rejects_drain_from_non_owner() -> None:
    dispatcher = UiDispatcher(render=lambda force=False: None)
    errors: list[BaseException] = []
    thread = threading.Thread(target=lambda: _capture_error(dispatcher.drain, errors))
    thread.start()
    thread.join(timeout=1)

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)


def test_model_catalog_loader_ignores_stale_results() -> None:
    dispatcher = UiDispatcher(render=lambda force=False: None)
    release_old = threading.Event()
    observed: list[list[str]] = []

    def discover(query: str | None) -> list[str]:
        if query == "old":
            release_old.wait(timeout=1)
        return [str(query)]

    loader = ModelCatalogLoader(discover=discover, post=dispatcher.post)
    old = loader.load("old", lambda models, error: observed.append(models))
    current = loader.load("new", lambda models, error: observed.append(models))
    release_old.set()
    old.result(timeout=1)
    current.result(timeout=1)
    dispatcher.drain()
    loader.close()

    assert observed == [["new"]]


def test_model_catalog_loader_cancel_suppresses_completion() -> None:
    dispatcher = UiDispatcher(render=lambda force=False: None)
    release = threading.Event()
    observed: list[list[str]] = []
    loader = ModelCatalogLoader(
        discover=lambda query: (release.wait(timeout=1), [str(query)])[1],
        post=dispatcher.post,
    )
    future = loader.load("cancelled", lambda models, error: observed.append(models))
    loader.cancel()
    release.set()
    future.result(timeout=1)
    dispatcher.drain()
    loader.close()

    assert observed == []


def _capture_error(callback, errors: list[BaseException]) -> None:
    try:
        callback()
    except BaseException as error:  # noqa: BLE001
        errors.append(error)
