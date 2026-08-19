from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from travis.ai.providers.travis_env import parse_sse_chunks
from travis.ai.types import DoneEvent, Model, StartEvent, TextDeltaEvent, TextEndEvent, TextStartEvent


WIRE_FIXTURE_FILENAMES = {
    "anthropic.json",
    "azure_responses.json",
    "bedrock.json",
    "chat_completions.json",
    "codex_responses.json",
    "google.json",
    "mistral.json",
    "openai_responses.json",
}


def _valid_wire_fixture() -> dict[str, object]:
    return {
        "apiMode": "openai-completions",
        "endpointPath": "/chat/completions",
        "requestCases": [
            {
                "expectedBody": {"model": "fixture-model"},
                "expectedHeaders": {"authorization": "Bearer <API_KEY>"},
                "input": {"model": {"id": "fixture-model"}},
                "name": "request",
            }
        ],
        "responseCases": [
            {
                "expectedResponse": {"finishReason": "stop"},
                "input": {"response": "ok"},
                "name": "response",
            }
        ],
        "schemaVersion": 1,
        "streamCases": [
            {
                "expectedEvents": [{"type": "done"}],
                "input": {"lines": ["data: [DONE]"]},
                "name": "stream",
            }
        ],
    }


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda fixture: fixture.update(schemaVersion=2), "schema version"),
        (lambda fixture: fixture.update(requestCases=[]), "requestCases"),
        (
            lambda fixture: fixture["requestCases"][0]["expectedBody"].update(path="/Users/person/private"),
            "private path",
        ),
        (
            lambda fixture: fixture["requestCases"][0]["expectedHeaders"].update(
                authorization="Bearer actual-secret"
            ),
            "authorization",
        ),
        (
            lambda fixture: fixture["responseCases"][0]["input"].update(
                response="eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.signature"
            ),
            "token-like",
        ),
        (lambda fixture: fixture.update(updateFromEnvironment="TRAVIS_UPDATE_FIXTURES"), "update mode"),
    ],
)
def test_wire_fixture_validation_rejects_unsafe_or_nondeterministic_content(
    mutation,
    message: str,
) -> None:
    from tests.ai.providers.wire_fixtures import FixtureValidationError, validate_wire_fixture

    fixture = copy.deepcopy(_valid_wire_fixture())
    mutation(fixture)

    with pytest.raises(FixtureValidationError, match=message):
        validate_wire_fixture(fixture)


def test_wire_fixture_loader_requires_canonical_key_order(tmp_path: Path) -> None:
    from tests.ai.providers.wire_fixtures import FixtureValidationError, load_wire_fixture

    path = tmp_path / "unordered.json"
    fixture = _valid_wire_fixture()
    unordered = {"schemaVersion": fixture.pop("schemaVersion"), **fixture}
    path.write_text(json.dumps(unordered, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(FixtureValidationError, match="canonical JSON ordering"):
        load_wire_fixture(path)


def test_wire_fixture_loader_has_no_environment_controlled_update_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.ai.providers.wire_fixtures import canonical_fixture_json, load_wire_fixture

    path = tmp_path / "fixture.json"
    original = canonical_fixture_json(_valid_wire_fixture())
    path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("TRAVIS_UPDATE_PROVIDER_WIRE_FIXTURES", "1")

    assert load_wire_fixture(path) == _valid_wire_fixture()
    assert path.read_text(encoding="utf-8") == original


def test_sanitized_wire_fixtures_are_complete_and_match_request_contracts() -> None:
    from tests.ai.providers.wire_fixtures import (
        load_wire_fixture,
        render_request_case,
        wire_fixture_paths,
    )

    paths = wire_fixture_paths()
    assert {path.name for path in paths} == WIRE_FIXTURE_FILENAMES
    for path in paths:
        fixture = load_wire_fixture(path)
        for case in fixture["requestCases"]:
            actual = render_request_case(fixture, case)
            assert actual["endpointPath"] == case.get("expectedEndpointPath", fixture["endpointPath"]), (
                path.name,
                case["name"],
            )
            assert actual["headers"] == case["expectedHeaders"], (path.name, case["name"])
            assert actual["body"] == case["expectedBody"], (path.name, case["name"])


def test_sanitized_wire_fixtures_match_response_contracts() -> None:
    from tests.ai.providers.wire_fixtures import load_wire_fixture, render_response_case, wire_fixture_paths

    for path in wire_fixture_paths():
        fixture = load_wire_fixture(path)
        for case in fixture["responseCases"]:
            assert render_response_case(fixture, case) == case["expectedResponse"], (
                path.name,
                case["name"],
            )


def test_sanitized_wire_fixtures_match_stream_contracts() -> None:
    from tests.ai.providers.wire_fixtures import load_wire_fixture, render_stream_case, wire_fixture_paths

    for path in wire_fixture_paths():
        fixture = load_wire_fixture(path)
        for case in fixture["streamCases"]:
            assert render_stream_case(fixture, case) == case["expectedEvents"], (
                path.name,
                case["name"],
            )


def test_chat_stream_event_tuple_is_stable() -> None:
    model = Model(id="fixture", name="Fixture", api="openai-completions", provider="fixture", base_url="")
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": "hello"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]

    events = list(parse_sse_chunks(lines, model))

    assert [type(event) for event in events] == [StartEvent, TextStartEvent, TextDeltaEvent, TextEndEvent, DoneEvent]
    assert [event.type for event in events] == ["start", "text_start", "text_delta", "text_end", "done"]
    assert events[-1].message.content[0].text == "hello"
