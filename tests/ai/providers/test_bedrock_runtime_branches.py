"""Direct branch characterizations for the Bedrock runtime adapter."""

from __future__ import annotations

from types import SimpleNamespace

import boto3
from botocore import config as botocore_config
import pytest

from travis.ai.env_config import ModelConfig
from travis.ai.providers import travis_env as travis_env_module
from travis.ai.providers.provider_request import PreparedProviderRequest
from travis.ai.providers.travis_env import TravisProvider
from travis.ai.types import Model


class _FakeConfig:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = dict(kwargs)


class _FakeEvents:
    def __init__(self) -> None:
        self.registrations: list[tuple[str, object]] = []

    def register(self, event_name: str, callback: object) -> None:
        self.registrations.append((event_name, callback))


class _FakeClient:
    def __init__(self, response: object, timeline: list[str] | None = None) -> None:
        self.response = response
        self.timeline = timeline
        self.meta = SimpleNamespace(events=_FakeEvents())
        self.calls: list[dict[str, object]] = []
        self.signed_headers: dict[str, str] = {}

    def converse_stream(self, **kwargs: object) -> object:
        self.calls.append(dict(kwargs))
        if self.timeline is not None:
            self.timeline.append("request")
        http_request = SimpleNamespace(headers=self.signed_headers)
        for _event_name, callback in self.meta.events.registrations:
            if callable(callback):
                callback(http_request, ignored=True)
        return self.response


class _FakeBotoRuntime:
    def __init__(self, response: object, timeline: list[str] | None = None) -> None:
        self.client = _FakeClient(response, timeline)
        self.client_parameters: dict[str, object] | None = None

    def create_client(
        self,
        service_name: str,
        *,
        region_name: str,
        endpoint_url: str,
        config: object,
    ) -> _FakeClient:
        self.client_parameters = {
            "service_name": service_name,
            "region_name": region_name,
            "endpoint_url": endpoint_url,
            "config": config,
        }
        return self.client


class _ClosableEventStream:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _EventSink:
    def __init__(self, timeline: list[str] | None = None) -> None:
        self.events: list[object] = []
        self.timeline = timeline

    def push(self, event: object) -> None:
        if self.timeline is not None:
            self.timeline.append(f"push:{event}")
        self.events.append(event)


class _AbortOnSecondCheck:
    def __init__(self) -> None:
        self.checks = 0

    @property
    def aborted(self) -> bool:
        self.checks += 1
        return self.checks > 1


def _provider() -> TravisProvider:
    return TravisProvider(
        ModelConfig(
            enabled=True,
            api_key=None,
            model="fixture-model",
            base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
            timeout_seconds=60,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            provider="amazon-bedrock",
        )
    )


def _model(model_id: str = "fixture-model") -> Model:
    return Model(
        id=model_id,
        name="Fixture Model",
        api="bedrock-converse-stream",
        provider="amazon-bedrock",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
    )


def _request(
    *,
    url: str = "https://bedrock-runtime.us-east-1.amazonaws.com/model/fixture-model/converse-stream",
    headers: dict[str, str] | None = None,
    body: dict[str, object] | None = None,
    timeout_seconds: float | None = None,
) -> PreparedProviderRequest:
    return PreparedProviderRequest(
        url=url,
        headers=headers or {},
        body=body or {},
        timeout_seconds=timeout_seconds,
        api_mode="bedrock_converse_stream",
        decoder=lambda _lines: iter(()),
    )


def _install_runtime(
    monkeypatch: pytest.MonkeyPatch,
    response: object,
    timeline: list[str] | None = None,
) -> _FakeBotoRuntime:
    runtime = _FakeBotoRuntime(response, timeline)
    monkeypatch.setattr(boto3, "client", runtime.create_client)
    monkeypatch.setattr(botocore_config, "Config", _FakeConfig)
    return runtime


def test_bedrock_runtime_preserves_config_headers_callback_order_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    timeline: list[str] = []
    event_stream = _ClosableEventStream()
    runtime = _install_runtime(
        monkeypatch,
        {
            "ResponseMetadata": {
                "HTTPStatusCode": "201",
                "HTTPHeaders": {"x-request-id": "request-1"},
            },
            "stream": event_stream,
        },
        timeline,
    )
    parsed_inputs: list[tuple[object, Model]] = []

    def parse_events(events: object, model: Model):
        parsed_inputs.append((events, model))
        yield "first"
        yield "second"

    monkeypatch.setattr(travis_env_module, "_parse_bedrock_events", parse_events)
    callback_payloads: list[tuple[dict[str, object], Model]] = []

    def on_response(payload: dict[str, object], model: Model) -> None:
        timeline.append("response")
        callback_payloads.append((payload, model))

    model = _model("arn:aws:bedrock:eu-west-2:123456789012:inference-profile/example")
    request = _request(
        url="https://bedrock-runtime.us-west-1.amazonaws.com/model/example/converse-stream",
        headers={
            "Authorization": "filtered",
            "content-type": "application/json",
            "Host": "filtered",
            "X-Amz-Security-Token": "filtered",
            "X-Custom": "custom-value",
            "x-trace": "trace-value",
        },
        body={"messages": [{"role": "user", "content": [{"text": "work"}]}]},
        timeout_seconds=12.5,
    )
    sink = _EventSink(timeline)

    _provider()._run_bedrock(
        sink,
        model,
        SimpleNamespace(region=" ", on_response=on_response, signal=SimpleNamespace(aborted=False)),
        request,
    )

    assert runtime.client_parameters is not None
    assert runtime.client_parameters["service_name"] == "bedrock-runtime"
    assert runtime.client_parameters["region_name"] == "eu-west-2"
    assert runtime.client_parameters["endpoint_url"] == (
        "https://bedrock-runtime.us-west-1.amazonaws.com"
    )
    config = runtime.client_parameters["config"]
    assert isinstance(config, _FakeConfig)
    assert config.kwargs == {
        "retries": {"max_attempts": 2, "mode": "standard"},
        "connect_timeout": 12.5,
        "read_timeout": 12.5,
    }
    assert runtime.client.meta.events.registrations[0][0] == (
        "before-sign.bedrock-runtime.ConverseStream"
    )
    assert runtime.client.signed_headers == {
        "X-Custom": "custom-value",
        "x-trace": "trace-value",
    }
    assert runtime.client.calls == [
        {
            "modelId": model.id,
            "messages": [{"role": "user", "content": [{"text": "work"}]}],
        }
    ]
    assert callback_payloads == [
        (
            {"status": 201, "headers": {"x-request-id": "request-1"}},
            model,
        )
    ]
    assert parsed_inputs == [(event_stream, model)]
    assert sink.events == ["first", "second"]
    assert timeline == ["request", "response", "push:first", "push:second"]
    assert event_stream.closed is True


@pytest.mark.parametrize(
    ("option_region", "environment", "model_id", "url", "expected_region"),
    [
        (
            " ap-south-1 ",
            {"AWS_REGION": "ca-central-1"},
            "arn:aws:bedrock:eu-west-2:123:model/example",
            "https://bedrock-runtime.us-west-1.amazonaws.com/model/example/converse-stream",
            "ap-south-1",
        ),
        (
            None,
            {"AWS_REGION": "ca-central-1", "AWS_DEFAULT_REGION": "eu-central-1"},
            "fixture-model",
            "https://gateway.example/model/example/converse-stream",
            "ca-central-1",
        ),
        (
            None,
            {"AWS_DEFAULT_REGION": "eu-central-1"},
            "fixture-model",
            "https://gateway.example/model/example/converse-stream",
            "eu-central-1",
        ),
        (
            None,
            {},
            "arn:aws:bedrock:eu-west-3:123:model/example",
            "https://gateway.example/model/example/converse-stream",
            "eu-west-3",
        ),
        (
            None,
            {},
            "fixture-model",
            "https://bedrock-runtime-fips.us-gov-west-1.amazonaws.com.cn/model/example/converse-stream",
            "us-gov-west-1",
        ),
        (
            None,
            {},
            "fixture-model",
            "https://gateway.example/model/example/converse-stream",
            "us-east-1",
        ),
    ],
)
def test_bedrock_runtime_region_precedence_and_timeout_omission(
    monkeypatch: pytest.MonkeyPatch,
    option_region: str | None,
    environment: dict[str, str],
    model_id: str,
    url: str,
    expected_region: str,
) -> None:
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    event_stream = object()
    runtime = _install_runtime(monkeypatch, {"stream": event_stream})

    def no_events(events: object, _model: Model):
        assert events is event_stream
        return iter(())

    monkeypatch.setattr(travis_env_module, "_parse_bedrock_events", no_events)

    _provider()._run_bedrock(
        _EventSink(),
        _model(model_id),
        SimpleNamespace(region=option_region),
        _request(url=url),
    )

    assert runtime.client_parameters is not None
    assert runtime.client_parameters["region_name"] == expected_region
    assert runtime.client_parameters["endpoint_url"] == url.rsplit("/model/", 1)[0]
    config = runtime.client_parameters["config"]
    assert isinstance(config, _FakeConfig)
    assert config.kwargs == {"retries": {"max_attempts": 2, "mode": "standard"}}
    assert runtime.client.meta.events.registrations == []


def test_bedrock_response_metadata_defaults_before_missing_stream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _install_runtime(
        monkeypatch,
        {"ResponseMetadata": {"HTTPStatusCode": 0, "HTTPHeaders": []}},
    )
    payloads: list[dict[str, object]] = []

    with pytest.raises(
        RuntimeError,
        match="Bedrock ConverseStream returned no event stream",
    ):
        _provider()._run_bedrock(
            _EventSink(),
            _model(),
            SimpleNamespace(on_response=lambda payload, _model: payloads.append(payload)),
            _request(),
        )

    assert payloads == [{"status": 200, "headers": {}}]
    assert runtime.client.calls == [{"modelId": "fixture-model"}]


def test_bedrock_non_mapping_response_without_options_raises_no_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_runtime(monkeypatch, object())

    with pytest.raises(
        RuntimeError,
        match="Bedrock ConverseStream returned no event stream",
    ):
        _provider()._run_bedrock(
            _EventSink(),
            _model(),
            None,
            _request(),
        )


def test_bedrock_abort_closes_event_stream_after_prior_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event_stream = _ClosableEventStream()
    _install_runtime(monkeypatch, {"stream": event_stream})

    def two_events(_events: object, _model: Model):
        yield "first"
        yield "second"

    monkeypatch.setattr(travis_env_module, "_parse_bedrock_events", two_events)
    sink = _EventSink()

    with pytest.raises(RuntimeError, match="Operation aborted"):
        _provider()._run_bedrock(
            sink,
            _model(),
            SimpleNamespace(signal=_AbortOnSecondCheck()),
            _request(),
        )

    assert sink.events == ["first"]
    assert event_stream.closed is True
