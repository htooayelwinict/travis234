from __future__ import annotations

import io
import json
import threading
from types import SimpleNamespace

import pytest

from travis.app import CodingApp
from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.coding_agent.rpc import RpcServer
from tests._provider_runtime import register_api_provider, reset_api_providers, reset_models


def setup_function() -> None:
    reset_api_providers()
    reset_models()


@pytest.fixture
def faux_app(tmp_path):
    register_api_provider(create_faux_provider(lambda model, context: text_response_events(model, "rpc answer")))
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        enable_tui=False,
        project_trust_override=False,
    )
    try:
        yield app
    finally:
        app.close()


def _run_rpc(app, frames: list[dict] | list[str]) -> list[dict]:
    lines = [frame if isinstance(frame, str) else json.dumps(frame) for frame in frames]
    input_stream = io.StringIO("\n".join(lines) + "\n")
    output = io.StringIO()
    assert RpcServer(app, input_stream, output).run() == 0
    return [json.loads(line) for line in output.getvalue().splitlines()]


def test_rpc_prompt_correlates_events_and_result(faux_app) -> None:
    frames = _run_rpc(
        faux_app,
        [{"id": "1", "method": "prompt", "params": {"text": "hello"}}],
    )

    assert any(frame.get("id") == "1" and "event" in frame for frame in frames)
    assert frames[-1] == {
        "id": "1",
        "result": {"stopReason": "stop", "text": "rpc answer"},
    }


@pytest.mark.parametrize(
    ("raw_frame", "code"),
    [
        ("{broken", "parse_error"),
        (json.dumps({"id": "1", "params": {}}), "invalid_request"),
        (json.dumps({"id": "1", "method": "missing"}), "unknown_method"),
        (json.dumps({"id": "1", "method": "prompt", "params": {"text": 3}}), "invalid_params"),
    ],
)
def test_rpc_errors_are_deterministic_and_do_not_include_tracebacks(faux_app, raw_frame: str, code: str) -> None:
    frames = _run_rpc(faux_app, [raw_frame])

    assert frames[0]["error"]["code"] == code
    assert "Traceback" not in json.dumps(frames)


def test_rpc_abort_is_accepted_while_prompt_owns_active_turn() -> None:
    started = threading.Event()
    aborted = threading.Event()

    class BlockingApp:
        def __init__(self) -> None:
            self.session = SimpleNamespace(
                session_id="rpc-session",
                cwd="/tmp/rpc",
                model=SimpleNamespace(provider="faux", id="blocking"),
                thinking_level="off",
                messages=[],
                agent=SimpleNamespace(abort=lambda: aborted.set()),
            )

        def run_turn(self, _prompt):
            started.set()
            assert aborted.wait(timeout=2)
            return []

    app = BlockingApp()
    input_stream = io.StringIO(
        "\n".join(
            [
                json.dumps({"id": "turn", "method": "prompt", "params": {"text": "wait"}}),
                json.dumps({"id": "busy", "method": "set_thinking", "params": {"level": "high"}}),
                json.dumps({"id": "abort", "method": "abort"}),
            ]
        )
        + "\n"
    )
    output = io.StringIO()

    assert RpcServer(app, input_stream, output).run() == 0
    frames = [json.loads(line) for line in output.getvalue().splitlines()]

    assert started.is_set()
    assert aborted.is_set()
    assert next(frame for frame in frames if frame.get("id") == "busy")["error"]["code"] == "busy_session"
    assert next(frame for frame in frames if frame.get("id") == "abort")["result"] == {"aborted": True}
    assert "result" in next(frame for frame in frames if frame.get("id") == "turn")
