"""Direct characterization coverage for the RPC line dispatcher."""

from __future__ import annotations

import io
import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import pytest

from travis.coding_agent import rpc as rpc_module
from travis.coding_agent.rpc import RpcServer


@dataclass
class _Model:
    provider: str
    id: str


class _Registry:
    def __init__(self, events: list[tuple[object, ...]]) -> None:
        self.events = events
        self.model = _Model("fixture", "known")
        self.error: RuntimeError | None = None

    def find(self, provider: str, model_id: str) -> _Model | None:
        self.events.append(("find", provider, model_id))
        if self.error is not None:
            raise self.error
        if (provider, model_id) == (self.model.provider, self.model.id):
            return self.model
        return None


class _Agent:
    def __init__(self, events: list[tuple[object, ...]]) -> None:
        self.events = events
        self.server: RpcServer | None = None

    def abort(self) -> None:
        server = self.server
        assert server is not None
        assert isinstance(server, _TestRpcServer)
        self.events.append(("abort", server.active_id, server.abort_requested))


class _Session:
    def __init__(self, events: list[tuple[object, ...]]) -> None:
        self.events = events
        self.session_id = "session-1"
        self.cwd = "/workspace"
        self.model = _Model("fixture", "initial")
        self.thinking_level = "medium"
        self.messages: list[object] = ["one", "two"]
        self.agent = _Agent(events)
        self.continue_result: list[object] = []
        self.compact_result: object = {"compressed": True}

    def continue_(self) -> list[object]:
        self.events.append(("continue",))
        return self.continue_result

    def set_model(self, model: _Model) -> None:
        self.events.append(("set_model", model.provider, model.id))
        self.model = model

    def set_thinking_level(self, level: str) -> None:
        self.events.append(("set_thinking", level))
        self.thinking_level = f"effective-{level}"

    def compact(self, *, focus: str | None, deep: bool) -> object:
        self.events.append(("compact", focus, deep))
        return self.compact_result


class _App:
    def __init__(self, events: list[tuple[object, ...]]) -> None:
        self.events = events
        self.session = _Session(events)
        self.model_registry = _Registry(events)
        self.turn_result: list[object] = []
        self.turn_started: threading.Event | None = None
        self.turn_release: threading.Event | None = None

    def run_turn(self, text: str, **kwargs: object) -> list[object]:
        self.events.append(("run_turn", text, dict(kwargs)))
        if self.turn_started is not None:
            self.turn_started.set()
        if self.turn_release is not None:
            assert self.turn_release.wait(timeout=2)
        return self.turn_result


class _RecordingOutput(io.StringIO):
    def __init__(self, events: list[tuple[object, ...]]) -> None:
        super().__init__()
        self.events = events
        self.server: RpcServer | None = None

    def write(self, value: str) -> int:
        closed = self.server.closed if isinstance(self.server, _TestRpcServer) else None
        self.events.append(("write", closed, value))
        return super().write(value)

    def flush(self) -> None:
        closed = self.server.closed if isinstance(self.server, _TestRpcServer) else None
        self.events.append(("flush", closed))
        super().flush()


class _RaisingLock:
    def __enter__(self) -> None:
        raise RuntimeError("lock failed")

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback


class _TestRpcServer(RpcServer):
    def handle_line(self, line: str) -> None:
        self._handle_line(line)

    @property
    def active_id(self) -> object | None:
        return self._active_id

    @property
    def abort_requested(self) -> bool:
        return self._abort_requested

    @property
    def active_thread(self) -> threading.Thread | None:
        return self._active_thread

    @property
    def workers(self) -> list[threading.Thread]:
        return self._workers

    @property
    def closed(self) -> bool:
        return self._closed

    def set_active_id(self, request_id: object | None) -> None:
        self._active_id = request_id

    def replace_lock(self, lock: object) -> None:
        object.__setattr__(self, "_lock", lock)

    def reset_closed(self) -> None:
        self._closed = False


def _server() -> tuple[_TestRpcServer, _App, _RecordingOutput, list[tuple[object, ...]]]:
    events: list[tuple[object, ...]] = []
    app = _App(events)
    output = _RecordingOutput(events)
    server = _TestRpcServer(app, io.StringIO(), output)
    output.server = server
    app.session.agent.server = server
    return server, app, output, events


def _handle(server: _TestRpcServer, request: object) -> None:
    server.handle_line(json.dumps(request, separators=(",", ":")))


def _frames(output: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in output.getvalue().splitlines()]


def _join_workers(server: _TestRpcServer) -> None:
    for worker in server.workers:
        worker.join(timeout=2)
        assert not worker.is_alive()


@pytest.mark.parametrize("line", ["{", "not json", "[1,"])
def test_parse_errors_write_the_exact_null_id_error(line: str) -> None:
    server, _app, output, _events = _server()

    server.handle_line(line)

    assert _frames(output) == [
        {
            "id": None,
            "error": {"code": "parse_error", "message": "Invalid JSON frame"},
        }
    ]


@pytest.mark.parametrize("payload", [None, [], "request", 7, False])
def test_non_object_requests_write_the_exact_invalid_request(payload: object) -> None:
    server, _app, output, _events = _server()

    _handle(server, payload)

    assert _frames(output) == [
        {
            "id": None,
            "error": {
                "code": "invalid_request",
                "message": "Request must be an object",
            },
        }
    ]


@pytest.mark.parametrize(
    ("payload", "response_id"),
    [
        ({"method": "get_state"}, None),
        ({"id": "kept", "method": 7}, "kept"),
        ({"id": False, "method": None}, False),
    ],
)
def test_id_presence_and_method_type_validation_are_exact(payload: dict[str, object], response_id: object) -> None:
    server, _app, output, _events = _server()

    _handle(server, payload)

    assert _frames(output) == [
        {
            "id": response_id,
            "error": {
                "code": "invalid_request",
                "message": "Request requires id and method",
            },
        }
    ]


@pytest.mark.parametrize("request_id", [None, False, 0, "", [], {"nested": [1]}])
def test_present_json_compatible_id_values_are_preserved(request_id: object) -> None:
    server, _app, output, _events = _server()

    _handle(server, {"id": request_id, "method": "get_state"})

    assert _frames(output) == [
        {
            "id": request_id,
            "result": {
                "busy": False,
                "sessionId": "session-1",
                "cwd": "/workspace",
                "model": {"provider": "fixture", "id": "initial"},
                "thinkingLevel": "medium",
                "messageCount": 2,
            },
        }
    ]


@pytest.mark.parametrize("params", [None, [], "params", 1, False])
def test_params_must_be_an_object_but_absence_defaults_to_empty(params: object) -> None:
    server, _app, output, _events = _server()
    request = {"id": "bad", "method": "get_state", "params": params}

    _handle(server, request)

    assert _frames(output) == [
        {
            "id": "bad",
            "error": {
                "code": "invalid_params",
                "message": "params must be an object",
            },
        }
    ]


def test_unknown_method_error_is_exact() -> None:
    server, _app, output, _events = _server()

    _handle(server, {"id": ["odd"], "method": "missing"})

    assert _frames(output) == [
        {
            "id": ["odd"],
            "error": {
                "code": "unknown_method",
                "message": "Unknown method: missing",
            },
        }
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"method": "prompt", "params": {"text": "ignored"}},
        {"id": "bad-method", "method": 3},
        {"id": "bad-params", "method": "prompt", "params": []},
        {"id": "unknown", "method": "missing"},
    ],
)
def test_envelope_and_unknown_method_validation_precede_busy(payload: dict[str, object]) -> None:
    server, _app, output, _events = _server()
    server.set_active_id("active")

    _handle(server, payload)

    assert _frames(output)[0]["error"] != {
        "code": "busy_session",
        "message": "Another request owns the active turn",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"id": "prompt", "method": "prompt", "params": {"text": "hello"}},
        {"id": "continue", "method": "continue"},
        {"id": "model", "method": "set_model", "params": {"provider": "fixture", "id": "known"}},
        {"id": "thinking", "method": "set_thinking", "params": {"level": "high"}},
        {"id": "compact", "method": "compact"},
        {"id": "close", "method": "close"},
    ],
)
def test_busy_blocks_exactly_the_mutating_methods(payload: dict[str, object]) -> None:
    server, _app, output, events = _server()
    server.set_active_id("active")

    _handle(server, payload)

    assert _frames(output) == [
        {
            "id": payload["id"],
            "error": {
                "code": "busy_session",
                "message": "Another request owns the active turn",
            },
        }
    ]
    assert not any(event[0] in {"run_turn", "continue", "find", "compact"} for event in events)
    assert server.closed is False


def test_method_specific_validation_is_not_reached_while_busy() -> None:
    server, _app, output, _events = _server()
    server.set_active_id("active")

    _handle(server, {"id": "prompt", "method": "prompt", "params": {"text": 3}})

    assert _frames(output)[0]["error"] == {
        "code": "busy_session",
        "message": "Another request owns the active turn",
    }


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({}, "prompt requires string params.text"),
        ({"text": None}, "prompt requires string params.text"),
        ({"text": 7}, "prompt requires string params.text"),
        ({"text": "ok", "images": None}, "prompt params.images must be an array of paths"),
        ({"text": "ok", "images": "image.png"}, "prompt params.images must be an array of paths"),
        ({"text": "ok", "images": ["one.png", 2]}, "prompt params.images must be an array of paths"),
    ],
)
def test_prompt_validation_errors_are_exact(params: dict[str, object], message: str) -> None:
    server, _app, output, _events = _server()

    _handle(server, {"id": "prompt", "method": "prompt", "params": params})

    assert _frames(output) == [
        {
            "id": "prompt",
            "error": {"code": "invalid_params", "message": message},
        }
    ]


@pytest.mark.parametrize(
    ("params", "expected_kwargs"),
    [
        ({"text": "hello"}, {"input_source": "rpc"}),
        ({"text": "hello", "images": []}, {"input_source": "rpc"}),
        (
            {"text": "hello", "images": ["one.png", "two.png"]},
            {"image_paths": ["one.png", "two.png"], "input_source": "rpc"},
        ),
    ],
)
def test_prompt_uses_the_exact_empty_and_present_image_call_signatures(
    params: dict[str, object], expected_kwargs: dict[str, object]
) -> None:
    server, _app, output, events = _server()

    _handle(server, {"id": "prompt", "method": "prompt", "params": params})
    _join_workers(server)

    assert events[0] == ("run_turn", "hello", expected_kwargs)
    assert _frames(output) == [{"id": "prompt", "result": {"stopReason": "stop", "text": ""}}]


def test_prompt_starts_named_daemon_thread_and_owns_active_turn_until_completion() -> None:
    server, app, output, _events = _server()
    app.turn_started = threading.Event()
    app.turn_release = threading.Event()

    _handle(server, {"id": ["turn"], "method": "prompt", "params": {"text": "wait"}})

    assert app.turn_started.wait(timeout=2)
    assert server.active_id == ["turn"]
    assert server.abort_requested is False
    worker = server.active_thread
    assert worker is not None
    assert worker.name == "travis-rpc-turn"
    assert worker.daemon is True
    assert server.workers == [worker]

    app.turn_release.set()
    _join_workers(server)

    assert server.active_id is None
    assert server.active_thread is None
    assert server.abort_requested is False
    assert _frames(output)[-1] == {
        "id": ["turn"],
        "result": {"stopReason": "stop", "text": ""},
    }


@pytest.mark.parametrize(
    ("method", "message"),
    [
        ("continue", "continue does not accept params"),
        ("get_state", "get_state does not accept params"),
        ("close", "close does not accept params"),
    ],
)
def test_no_param_methods_reject_every_nonempty_mapping(method: str, message: str) -> None:
    server, _app, output, _events = _server()

    _handle(server, {"id": method, "method": method, "params": {"extra": False}})

    assert _frames(output) == [{"id": method, "error": {"code": "invalid_params", "message": message}}]


def test_continue_defaults_params_and_starts_the_session_operation() -> None:
    server, _app, output, events = _server()

    _handle(server, {"id": "continue", "method": "continue"})
    _join_workers(server)

    assert events[0] == ("continue",)
    assert _frames(output) == [{"id": "continue", "result": {"stopReason": "stop", "text": ""}}]


def test_get_state_defaults_params_and_remains_available_while_busy() -> None:
    server, _app, output, _events = _server()
    server.set_active_id("turn")

    _handle(server, {"id": "state", "method": "get_state"})

    assert _frames(output) == [
        {
            "id": "state",
            "result": {
                "busy": True,
                "sessionId": "session-1",
                "cwd": "/workspace",
                "model": {"provider": "fixture", "id": "initial"},
                "thinkingLevel": "medium",
                "messageCount": 2,
            },
        }
    ]


@pytest.mark.parametrize(("active_id", "aborted"), [(None, False), ("turn", True)])
def test_abort_is_available_and_orders_state_agent_then_result(active_id: object, aborted: bool) -> None:
    server, _app, output, events = _server()
    server.set_active_id(active_id)

    _handle(server, {"id": "abort", "method": "abort", "params": {"ignored": True}})

    if aborted:
        assert events[0] == ("abort", "turn", True)
    else:
        assert not any(event[0] == "abort" for event in events)
    assert _frames(output) == [{"id": "abort", "result": {"aborted": aborted}}]


def test_close_writes_success_before_marking_the_server_closed() -> None:
    server, _app, output, events = _server()

    _handle(server, {"id": None, "method": "close", "params": {}})

    assert _frames(output) == [{"id": None, "result": {"closed": True}}]
    assert events[-2][0:2] == ("write", False)
    assert events[-1] == ("flush", False)
    assert server.closed is True


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({}, "set_model requires string provider and id"),
        ({"provider": 2, "id": "known"}, "set_model requires string provider and id"),
        ({"provider": "fixture", "id": 2}, "set_model requires string provider and id"),
        ({"provider": "fixture", "id": "missing"}, "Unknown model: fixture/missing"),
    ],
)
def test_set_model_validation_errors_are_exact(params: dict[str, object], message: str) -> None:
    server, _app, output, _events = _server()

    _handle(server, {"id": "model", "method": "set_model", "params": params})

    assert _frames(output) == [{"id": "model", "error": {"code": "invalid_params", "message": message}}]


@pytest.mark.parametrize(
    ("params", "expected_id"),
    [
        ({"provider": "fixture", "id": "known"}, "known"),
        ({"provider": "fixture", "model": "known"}, "known"),
        ({"provider": "fixture", "id": "known", "model": "ignored"}, "known"),
    ],
)
def test_set_model_resolution_mutation_and_result_order(params: dict[str, object], expected_id: str) -> None:
    server, _app, output, events = _server()

    _handle(server, {"id": "model", "method": "set_model", "params": params})

    assert events[0:2] == [
        ("find", "fixture", expected_id),
        ("set_model", "fixture", expected_id),
    ]
    assert _frames(output) == [{"id": "model", "result": {"provider": "fixture", "id": expected_id}}]


@pytest.mark.parametrize("level", [None, 3, False, ["high"]])
def test_set_thinking_requires_a_string(level: object) -> None:
    server, _app, output, _events = _server()

    _handle(server, {"id": "thinking", "method": "set_thinking", "params": {"level": level}})

    assert _frames(output) == [
        {
            "id": "thinking",
            "error": {
                "code": "invalid_params",
                "message": "set_thinking requires string level",
            },
        }
    ]


def test_set_thinking_writes_the_effective_level_after_mutation() -> None:
    server, _app, output, events = _server()

    _handle(server, {"id": "thinking", "method": "set_thinking", "params": {"level": "high"}})

    assert events[0] == ("set_thinking", "high")
    assert _frames(output) == [{"id": "thinking", "result": {"level": "effective-high"}}]


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"focus": 3}, "compact focus must be a string"),
        ({"deep": None}, "compact deep must be a boolean"),
        ({"deep": 1}, "compact deep must be a boolean"),
    ],
)
def test_compact_validation_errors_are_exact(params: dict[str, object], message: str) -> None:
    server, _app, output, _events = _server()

    _handle(server, {"id": "compact", "method": "compact", "params": params})

    assert _frames(output) == [{"id": "compact", "error": {"code": "invalid_params", "message": message}}]


@pytest.mark.parametrize(
    ("params", "expected_call"),
    [
        ({}, ("compact", None, False)),
        ({"focus": None, "deep": False}, ("compact", None, False)),
        ({"focus": "retain tools", "deep": True}, ("compact", "retain tools", True)),
    ],
)
def test_compact_delegates_and_serializes_the_result(
    params: dict[str, object], expected_call: tuple[object, ...]
) -> None:
    server, app, output, events = _server()
    app.session.compact_result = {"snake_key": "value"}

    _handle(server, {"id": "compact", "method": "compact", "params": params})

    assert events[0] == expected_call
    assert _frames(output) == [{"id": "compact", "result": {"compaction": {"snakeKey": "value"}}}]


def test_invalid_params_and_unexpected_dispatch_exceptions_have_distinct_errors() -> None:
    invalid_server, _app, invalid_output, _events = _server()
    error_server, error_app, error_output, error_events = _server()
    error_app.model_registry.error = RuntimeError("private detail")

    _handle(invalid_server, {"id": "bad", "method": "prompt", "params": {}})
    _handle(
        error_server,
        {"id": "error", "method": "set_model", "params": {"provider": "fixture", "id": "known"}},
    )

    assert _frames(invalid_output) == [
        {
            "id": "bad",
            "error": {
                "code": "invalid_params",
                "message": "prompt requires string params.text",
            },
        }
    ]
    assert error_events[0] == ("find", "fixture", "known")
    assert _frames(error_output) == [
        {
            "id": "error",
            "error": {"code": "internal_error", "message": "Request failed"},
        }
    ]


def test_unexpected_json_loads_exception_propagates_without_a_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, _app, output, _events = _server()

    def fail_loads(line: str) -> object:
        del line
        raise TypeError("decoder failed")

    monkeypatch.setattr(rpc_module.json, "loads", fail_loads)

    with pytest.raises(TypeError, match="decoder failed"):
        server.handle_line("{}")
    assert output.getvalue() == ""


def test_unexpected_busy_lock_exception_propagates_without_a_frame() -> None:
    server, _app, output, _events = _server()
    server.replace_lock(_RaisingLock())

    with pytest.raises(RuntimeError, match="lock failed"):
        _handle(server, {"id": "state", "method": "get_state"})
    assert output.getvalue() == ""


def test_request_inputs_are_not_mutated() -> None:
    server, _app, output, _events = _server()
    request: dict[str, object] = {
        "id": {"nested": [1, None]},
        "method": "compact",
        "params": {"focus": "keep", "deep": True, "extra": {"value": False}},
    }
    before = json.loads(json.dumps(request))

    _handle(server, request)

    assert request == before
    assert _frames(output)[0]["id"] == {"nested": [1, None]}


def test_dispatch_order_covers_all_eight_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    server, _app, _output, events = _server()
    calls: list[tuple[str, object, object]] = []

    def record_start(request_id: object, operation: Callable[[], object]) -> None:
        calls.append(("start", request_id, operation))

    def record_abort(request_id: object) -> None:
        calls.append(("abort", request_id, None))

    def record_model(request_id: object, params: Mapping[str, object]) -> None:
        calls.append(("set_model", request_id, params))

    def record_thinking(request_id: object, params: Mapping[str, object]) -> None:
        calls.append(("set_thinking", request_id, params))

    def record_compact(request_id: object, params: Mapping[str, object]) -> None:
        calls.append(("compact", request_id, params))

    monkeypatch.setattr(server, "_start_turn", record_start)
    monkeypatch.setattr(server, "_handle_abort", record_abort)
    monkeypatch.setattr(server, "_handle_set_model", record_model)
    monkeypatch.setattr(server, "_handle_set_thinking", record_thinking)
    monkeypatch.setattr(server, "_handle_compact", record_compact)

    requests = [
        {"id": "prompt", "method": "prompt", "params": {"text": "hello"}},
        {"id": "continue", "method": "continue"},
        {"id": "abort", "method": "abort"},
        {"id": "state", "method": "get_state"},
        {"id": "model", "method": "set_model", "params": {"provider": "fixture", "id": "known"}},
        {"id": "thinking", "method": "set_thinking", "params": {"level": "high"}},
        {"id": "compact", "method": "compact"},
        {"id": "close", "method": "close"},
    ]

    for request in requests:
        _handle(server, request)
        server.reset_closed()

    assert [call[0:2] for call in calls] == [
        ("start", "prompt"),
        ("start", "continue"),
        ("abort", "abort"),
        ("set_model", "model"),
        ("set_thinking", "thinking"),
        ("compact", "compact"),
    ]
    assert all(event[0] in {"write", "flush"} for event in events)
