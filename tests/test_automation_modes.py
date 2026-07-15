from __future__ import annotations

import io
import json

import pytest

from travis.app import CodingApp
from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.coding_agent.automation import run_json_mode, run_print_mode, serialize_machine_value
from travis.coding_agent.rpc import RpcServer
from travis.tui import FakeTerminal, InteractiveMode, strip_ansi
from tests._provider_runtime import register_api_provider, reset_api_providers, reset_models


def setup_function() -> None:
    reset_api_providers()
    reset_models()


@pytest.fixture
def faux_app(tmp_path):
    register_api_provider(
        create_faux_provider(lambda model, context: text_response_events(model, "final answer"))
    )
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


def test_print_mode_outputs_only_final_text(faux_app) -> None:
    output = io.StringIO()

    code = run_print_mode(faux_app, "hello", output)

    assert code == 0
    assert output.getvalue() == "final answer\n"


def test_json_mode_emits_ordered_machine_events(faux_app) -> None:
    output = io.StringIO()

    code = run_json_mode(faux_app, "hello", output)
    lines = output.getvalue().splitlines()
    frames = [json.loads(line) for line in lines]

    assert code == 0
    assert [frame["type"] for frame in frames] == [
        "session",
        "message_start",
        "message_end",
        "result",
    ]
    assert frames[0]["schemaVersion"] == 1
    assert frames[-1]["stopReason"] == "stop"
    assert frames[-1]["text"] == "final answer"
    assert all("\x1b" not in line for line in lines)


def test_machine_serializer_camel_cases_and_drops_sensitive_transport_fields() -> None:
    serialized = serialize_machine_value(
        {
            "tool_call_id": "call-1",
            "nested_value": {"stop_reason": "stop"},
            "headers": {"authorization": "Bearer secret"},
            "api_key": "secret",
            "safe": "value",
        }
    )

    assert serialized == {
        "toolCallId": "call-1",
        "nestedValue": {"stopReason": "stop"},
        "safe": "value",
    }


def test_print_json_rpc_and_tui_share_the_same_final_session_result(tmp_path) -> None:
    register_api_provider(
        create_faux_provider(lambda model, context: text_response_events(model, "shared answer"))
    )
    apps: list[CodingApp] = []

    def make_app(name: str, *, tui: bool = False) -> CodingApp:
        path = tmp_path / name
        path.mkdir()
        app = CodingApp(
            cwd=str(path),
            model=faux_model(),
            enable_tui=tui,
            terminal=FakeTerminal(columns=120, rows=30) if tui else None,
            project_trust_override=False,
            session_path=str(path / "session.jsonl"),
        )
        apps.append(app)
        return app

    try:
        print_app = make_app("print")
        print_output = io.StringIO()
        assert run_print_mode(print_app, "same prompt", print_output) == 0

        json_app = make_app("json")
        json_output = io.StringIO()
        assert run_json_mode(json_app, "same prompt", json_output) == 0

        rpc_app = make_app("rpc")
        rpc_output = io.StringIO()
        rpc_input = io.StringIO(
            json.dumps({"id": "turn", "method": "prompt", "params": {"text": "same prompt"}})
            + "\n"
        )
        assert RpcServer(rpc_app, rpc_input, rpc_output).run() == 0

        tui_app = make_app("tui", tui=True)
        tui_inputs = iter(["same prompt", "/exit"])
        tui_mode = InteractiveMode(tui_app, input_fn=lambda prompt: next(tui_inputs))
        assert tui_mode.run() == 0

        assert [app.session.get_last_assistant_text() for app in apps] == ["shared answer"] * 4
        assert print_output.getvalue() == "shared answer\n"
        assert json.loads(json_output.getvalue().splitlines()[-1])["text"] == "shared answer"
        assert json.loads(rpc_output.getvalue().splitlines()[-1])["result"]["text"] == "shared answer"
        assert "shared answer" in strip_ansi("\n".join(tui_app.tui.render(120)))
    finally:
        for app in reversed(apps):
            app.close()
