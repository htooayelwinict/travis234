"""Direct characterization coverage for configured API-key auth callbacks."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

import pytest

from travis.ai.auth import ApiKeyAuth, AuthContext as DefaultAuthContext, AuthResult, ModelAuth
from travis.ai.auth.types import ApiKeyCredential, AuthContext
from travis.ai.types import Model
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent import model_registry as model_registry_module
from travis.coding_agent.model_registry import ModelRegistry


def _model(*, headers: dict[str, str] | None = None) -> Model:
    return Model(
        id="fixture-model",
        name="Fixture Model",
        api="faux",
        provider="fixture-provider",
        base_url="https://model.example.test/v1",
        headers=headers,
    )


def _login(callbacks: dict[str, object]) -> dict[str, object]:
    return callbacks


def _base_auth(
    events: list[tuple[object, ...]],
    result: object,
    *,
    awaitable: bool = False,
    error: RuntimeError | None = None,
    name: str = "Base login",
) -> ApiKeyAuth:
    def placeholder(
        model: Model,
        context: AuthContext,
        credential: ApiKeyCredential | None,
    ) -> AuthResult | None:
        del model, context, credential
        return None

    base = ApiKeyAuth(name=name, resolve=placeholder, login=_login)

    def resolve_awaitable(
        model: Model,
        context: AuthContext,
        credential: ApiKeyCredential | None,
    ):
        events.append(("base.resolve", model, context, credential))

        async def finish() -> object:
            events.append(("base.await",))
            if error is not None:
                raise error
            return result

        return finish()

    def resolve_sync(
        model: Model,
        context: AuthContext,
        credential: ApiKeyCredential | None,
    ):
        events.append(("base.resolve", model, context, credential))
        if error is not None:
            raise error
        return result

    object.__setattr__(base, "resolve", resolve_awaitable if awaitable else resolve_sync)
    return base


_FailurePoint = Literal[
    "credential_key",
    "configured_key",
    "provider_headers",
    "model_headers",
]


@dataclass
class _ResolutionSeam:
    events: list[tuple[object, ...]]
    credential_value: str | None = "credential-secret"
    configured_value: str = "configured-secret"
    provider_headers: dict[str, str] | None = None
    model_headers: dict[str, str] | None = None
    failure_at: _FailurePoint | None = None
    failure: RuntimeError = field(default_factory=lambda: RuntimeError("resolution failed"))
    env_arguments: list[Mapping[str, str] | None] = field(default_factory=list)
    header_arguments: list[object] = field(default_factory=list)

    def resolve_config_value(
        self,
        value: str,
        env: Mapping[str, str] | None = None,
        *,
        uncached: bool = False,
    ) -> str | None:
        self.events.append(("resolve_config_value", value, dict(env or {}), uncached))
        self.env_arguments.append(env)
        if self.failure_at == "credential_key":
            raise self.failure
        return self.credential_value

    def resolve_config_value_or_throw(
        self,
        value: str,
        description: str,
        env: Mapping[str, str] | None = None,
    ) -> str:
        self.events.append(("resolve_config_value_or_throw", value, description, dict(env or {})))
        self.env_arguments.append(env)
        if self.failure_at == "configured_key":
            raise self.failure
        return self.configured_value

    def resolve_headers_or_throw(
        self,
        headers: object,
        description: str,
        env: Mapping[str, str] | None = None,
    ) -> dict[str, str] | None:
        self.events.append(("resolve_headers_or_throw", headers, description, dict(env or {})))
        self.env_arguments.append(env)
        self.header_arguments.append(headers)
        if description.startswith('provider "'):
            if self.failure_at == "provider_headers":
                raise self.failure
            return dict(self.provider_headers) if self.provider_headers is not None else None
        if self.failure_at == "model_headers":
            raise self.failure
        return dict(self.model_headers) if self.model_headers is not None else None


def _install_seam(monkeypatch: pytest.MonkeyPatch, seam: _ResolutionSeam) -> None:
    monkeypatch.setattr(model_registry_module, "resolve_config_value", seam.resolve_config_value)
    monkeypatch.setattr(
        model_registry_module,
        "resolve_config_value_or_throw",
        seam.resolve_config_value_or_throw,
    )
    monkeypatch.setattr(
        model_registry_module,
        "resolve_headers_or_throw",
        seam.resolve_headers_or_throw,
    )


def _configured_auth(config: dict[str, object], base: ApiKeyAuth | None) -> ApiKeyAuth:
    registry = ModelRegistry.in_memory(AuthStorage.in_memory())
    return registry._configured_api_key_auth(config, base)


def test_absent_base_and_truly_empty_config_resolves_none_after_both_header_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []
    seam = _ResolutionSeam(events)
    _install_seam(monkeypatch, seam)
    config: dict[str, object] = {}
    model = _model()
    context = DefaultAuthContext()

    auth = _configured_auth(config, None)
    result = auth.resolve(model, context, None)

    assert auth.name == "Provider API key"
    assert auth.login is None
    assert result is None
    assert events == [
        (
            "resolve_headers_or_throw",
            None,
            'provider "fixture-provider"',
            {},
        ),
        (
            "resolve_headers_or_throw",
            None,
            'model "fixture-provider/fixture-model"',
            {},
        ),
    ]
    assert config == {}


def test_present_base_none_result_preserves_login_identity_and_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []
    seam = _ResolutionSeam(events)
    _install_seam(monkeypatch, seam)
    base = _base_auth(events, None)
    model = _model()
    context = DefaultAuthContext()

    auth = _configured_auth({}, base)
    result = auth.resolve(model, context, None)

    assert auth.name == "Base login"
    assert auth.login is base.login
    assert result is None
    assert events == [
        ("base.resolve", model, context, None),
        ("resolve_headers_or_throw", None, 'provider "fixture-provider"', {}),
        (
            "resolve_headers_or_throw",
            None,
            'model "fixture-provider/fixture-model"',
            {},
        ),
    ]


def test_sync_base_result_wins_key_and_merges_headers_source_base_url_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []
    provider_headers = {"X-Shared": "configured-provider", "X-Provider": "provider"}
    model_headers = {"X-Shared": "configured-model", "X-Model": "model"}
    seam = _ResolutionSeam(
        events,
        provider_headers=provider_headers,
        model_headers=model_headers,
    )
    _install_seam(monkeypatch, seam)
    base_result = AuthResult(
        auth=ModelAuth(
            api_key="base-secret",
            headers={"X-Shared": "base", "X-Base": "base"},
            base_url="https://base.example.test/v1",
        ),
        source="base source",
        env={"BASE_ONLY": "base", "SHARED": "base"},
    )
    base = _base_auth(events, base_result, name="Existing provider")
    config: dict[str, object] = {
        "name": "Configured provider",
        "apiKey": "$CONFIGURED_KEY",
        "headers": {"configured": "headers"},
        "authHeader": True,
    }
    credential: dict[str, object] = {
        "key": "$CREDENTIAL_KEY",
        "env": {"SHARED": "credential", "NUMBER": 7},
    }
    model = _model(headers={"model": "headers"})
    context = DefaultAuthContext()
    before = (
        copy.deepcopy(config),
        copy.deepcopy(credential),
        copy.deepcopy(model.headers),
        copy.deepcopy(base_result),
    )

    auth = _configured_auth(config, base)
    result = auth.resolve(model, context, credential)

    assert auth.name == "Configured provider"
    assert auth.login is base.login
    assert result == AuthResult(
        auth=ModelAuth(
            api_key="base-secret",
            headers={
                "X-Shared": "configured-model",
                "X-Base": "base",
                "X-Provider": "provider",
                "X-Model": "model",
                "Authorization": "Bearer base-secret",
            },
            base_url="https://base.example.test/v1",
        ),
        source="base source",
        env={"BASE_ONLY": "base", "SHARED": "credential", "NUMBER": "7"},
    )
    assert result is not None
    assert list((result.auth.headers or {}).items()) == [
        ("X-Shared", "configured-model"),
        ("X-Base", "base"),
        ("X-Provider", "provider"),
        ("X-Model", "model"),
        ("Authorization", "Bearer base-secret"),
    ]
    assert events == [
        ("base.resolve", model, context, credential),
        (
            "resolve_headers_or_throw",
            config["headers"],
            'provider "fixture-provider"',
            {"SHARED": "credential", "NUMBER": "7"},
        ),
        (
            "resolve_headers_or_throw",
            model.headers,
            'model "fixture-provider/fixture-model"',
            {"SHARED": "credential", "NUMBER": "7"},
        ),
    ]
    assert seam.header_arguments[0] is config["headers"]
    assert seam.header_arguments[1] is model.headers
    assert (config, credential, model.headers, base_result) == before


def test_awaitable_base_settles_before_uncached_credential_key_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []
    seam = _ResolutionSeam(events, credential_value="credential-secret")
    _install_seam(monkeypatch, seam)
    base_result = AuthResult(auth=ModelAuth(), source="async base")
    base = _base_auth(events, base_result, awaitable=True)
    credential: dict[str, object] = {
        "key": 123,
        "env": {1: 0, "EMPTY": "", "NONE": None, "FALSE": False},
    }
    model = _model()
    context = DefaultAuthContext()

    result = _configured_auth({"apiKey": "$CONFIGURED"}, base).resolve(
        model,
        context,
        credential,
    )

    expected_env = {"1": "0", "EMPTY": "", "NONE": "None", "FALSE": "False"}
    assert result == AuthResult(
        auth=ModelAuth(api_key="credential-secret"),
        source="async base",
        env=expected_env,
    )
    assert events == [
        ("base.resolve", model, context, credential),
        ("base.await",),
        ("resolve_config_value", "123", expected_env, True),
        (
            "resolve_headers_or_throw",
            None,
            'provider "fixture-provider"',
            expected_env,
        ),
        (
            "resolve_headers_or_throw",
            None,
            'model "fixture-provider/fixture-model"',
            expected_env,
        ),
    ]
    assert len(seam.env_arguments) == 3
    assert seam.env_arguments[0] is seam.env_arguments[1]
    assert seam.env_arguments[1] is seam.env_arguments[2]


def test_missing_credential_key_falls_through_to_throwing_configured_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []
    seam = _ResolutionSeam(
        events,
        credential_value=None,
        configured_value="configured-secret",
    )
    _install_seam(monkeypatch, seam)
    credential: dict[str, object] = {"key": "$STORED", "env": {"TOKEN": "value"}}
    model = _model()

    result = _configured_auth({"apiKey": "$CONFIGURED"}, None).resolve(
        model,
        DefaultAuthContext(),
        credential,
    )

    assert result == AuthResult(
        auth=ModelAuth(api_key="configured-secret"),
        source="provider config",
        env={"TOKEN": "value"},
    )
    assert events[:2] == [
        ("resolve_config_value", "$STORED", {"TOKEN": "value"}, True),
        (
            "resolve_config_value_or_throw",
            "$CONFIGURED",
            'API key for provider "fixture-provider"',
            {"TOKEN": "value"},
        ),
    ]


def test_non_auth_runtime_base_result_keeps_provider_config_result_nonempty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []
    seam = _ResolutionSeam(events)
    _install_seam(monkeypatch, seam)
    runtime_value = object()
    base = _base_auth(events, runtime_value)
    model = _model()
    context = DefaultAuthContext()

    result = _configured_auth({}, base).resolve(model, context, None)

    assert result == AuthResult(
        auth=ModelAuth(),
        source="provider config",
        env=None,
    )
    assert events[0] == ("base.resolve", model, context, None)


def test_falsey_base_key_blocks_fallback_but_is_preserved_without_auth_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []
    seam = _ResolutionSeam(events)
    _install_seam(monkeypatch, seam)
    base = _base_auth(
        events,
        AuthResult(auth=ModelAuth(api_key=""), source="falsey base", env={}),
    )

    result = _configured_auth({"apiKey": "$CONFIGURED"}, base).resolve(
        _model(),
        DefaultAuthContext(),
        {"key": "$CREDENTIAL"},
    )

    assert result == AuthResult(
        auth=ModelAuth(api_key=""),
        source="falsey base",
        env=None,
    )
    assert not any(event[0] in {"resolve_config_value", "resolve_config_value_or_throw"} for event in events)


def test_falsey_credential_key_is_skipped_and_nonstring_configured_key_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []
    seam = _ResolutionSeam(events)
    _install_seam(monkeypatch, seam)

    result = _configured_auth({"apiKey": 0, "name": 0}, None).resolve(
        _model(),
        DefaultAuthContext(),
        {"key": "", "env": []},
    )

    assert result is None
    assert not any(event[0] in {"resolve_config_value", "resolve_config_value_or_throw"} for event in events)


def test_empty_configured_key_is_resolved_and_empty_result_remains_non_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []
    seam = _ResolutionSeam(events, configured_value="")
    _install_seam(monkeypatch, seam)

    result = _configured_auth({"apiKey": "", "authHeader": 0, "auth_header": True}, None).resolve(
        _model(),
        DefaultAuthContext(),
        None,
    )

    assert result == AuthResult(
        auth=ModelAuth(api_key=""),
        source="provider config",
        env=None,
    )
    assert events[0] == (
        "resolve_config_value_or_throw",
        "",
        'API key for provider "fixture-provider"',
        {},
    )
    assert not any(event[0] == "resolve_config_value" for event in events)


def test_auth_header_missing_key_fails_only_after_provider_and_model_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []
    seam = _ResolutionSeam(
        events,
        provider_headers={"X-Provider": "provider"},
        model_headers={"X-Model": "model"},
    )
    _install_seam(monkeypatch, seam)
    model = _model(headers={"X-Input": "model"})

    with pytest.raises(RuntimeError) as raised:
        _configured_auth({"headers": {"X-Input": "provider"}, "authHeader": True}, None).resolve(
            model,
            DefaultAuthContext(),
            None,
        )

    assert str(raised.value) == 'No API key found for "fixture-provider"'
    assert events == [
        (
            "resolve_headers_or_throw",
            {"X-Input": "provider"},
            'provider "fixture-provider"',
            {},
        ),
        (
            "resolve_headers_or_throw",
            model.headers,
            'model "fixture-provider/fixture-model"',
            {},
        ),
    ]


@pytest.mark.parametrize(
    ("failure_at", "expected_prefix"),
    [
        ("credential_key", ["resolve_config_value"]),
        ("configured_key", ["resolve_config_value_or_throw"]),
        ("provider_headers", ["resolve_headers_or_throw"]),
        ("model_headers", ["resolve_headers_or_throw", "resolve_headers_or_throw"]),
    ],
)
def test_resolution_exceptions_propagate_with_exact_call_cutoff(
    monkeypatch: pytest.MonkeyPatch,
    failure_at: _FailurePoint,
    expected_prefix: list[str],
) -> None:
    events: list[tuple[object, ...]] = []
    failure = RuntimeError(f"failure:{failure_at}")
    seam = _ResolutionSeam(
        events,
        credential_value=None,
        failure_at=failure_at,
        failure=failure,
    )
    _install_seam(monkeypatch, seam)
    credential: ApiKeyCredential | None = {"key": "$CREDENTIAL"} if failure_at == "credential_key" else None
    config: dict[str, object] = {"apiKey": "$CONFIGURED"} if failure_at == "configured_key" else {}

    with pytest.raises(RuntimeError) as raised:
        _configured_auth(config, None).resolve(_model(), DefaultAuthContext(), credential)

    assert raised.value is failure
    assert [str(event[0]) for event in events] == expected_prefix


@pytest.mark.parametrize("awaitable", [False, True])
def test_base_resolver_exception_propagates_before_configuration_calls(
    monkeypatch: pytest.MonkeyPatch,
    awaitable: bool,
) -> None:
    events: list[tuple[object, ...]] = []
    seam = _ResolutionSeam(events)
    _install_seam(monkeypatch, seam)
    failure = RuntimeError("base failed")
    base = _base_auth(events, None, awaitable=awaitable, error=failure)
    model = _model()
    context = DefaultAuthContext()

    with pytest.raises(RuntimeError) as raised:
        _configured_auth({"apiKey": "$CONFIGURED"}, base).resolve(
            model,
            context,
            None,
        )

    assert raised.value is failure
    expected: list[tuple[object, ...]] = [("base.resolve", model, context, None)]
    if awaitable:
        expected.append(("base.await",))
    assert events == expected
