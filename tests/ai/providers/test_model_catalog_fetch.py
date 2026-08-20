from __future__ import annotations

import json
import urllib.request
from io import BytesIO

import pytest


class _Response:
    def __init__(self, body: bytes, *, url: str = "https://provider.test/models") -> None:
        self._body = BytesIO(body)
        self._url = url
        self.read_sizes: list[int] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def geturl(self) -> str:
        return self._url

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self._body.read(size)


@pytest.mark.parametrize(
    "url",
    [
        "https://provider.test/models",
        "http://localhost:8080/models",
        "http://127.0.0.1:8080/models",
        "http://[::1]:8080/models",
    ],
)
def test_model_catalog_refresh_url_policy_accepts_https_and_loopback_http(url: str) -> None:
    from travis.ai.providers.model_catalog_fetch import validate_model_catalog_url

    assert validate_model_catalog_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://provider.test/models",
        "file:///private/models.json",
        "data:application/json,[]",
        "ftp://provider.test/models",
        "//provider.test/models",
    ],
)
def test_model_catalog_refresh_url_policy_rejects_unsafe_schemes_before_io(
    monkeypatch,
    url: str,
) -> None:
    from travis.ai.providers import model_catalog_fetch
    from travis.ai.providers.model_catalog_fetch import ModelCatalogURLPolicyError
    from travis.ai.providers.provider_profiles import ProviderProfile

    with pytest.raises(ModelCatalogURLPolicyError, match="model catalog URL"):
        model_catalog_fetch.validate_model_catalog_url(url)

    opened = False

    def fail_if_opened(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("unsafe URL reached I/O")

    monkeypatch.setattr(model_catalog_fetch, "_open_request", fail_if_opened)
    assert ProviderProfile(name="fixture", models_url=url).fetch_models() is None
    assert not opened


def test_model_catalog_refresh_rejects_unsafe_effective_redirect_before_read(monkeypatch) -> None:
    from travis.ai.providers import model_catalog_fetch

    response = _Response(b'[{"id":"never-read"}]', url="http://remote.test/models")
    monkeypatch.setattr(model_catalog_fetch, "_open_request", lambda *_args, **_kwargs: response)

    assert model_catalog_fetch.fetch_model_catalog(
        provider_name="fixture",
        url="https://provider.test/models",
    ) is None
    assert response.read_sizes == []


def test_model_catalog_refresh_redirect_handler_rejects_unsafe_target_before_following() -> None:
    from travis.ai.providers.model_catalog_fetch import (
        ModelCatalogURLPolicyError,
        _ValidatedRedirectHandler,
    )

    handler = _ValidatedRedirectHandler()
    request = urllib.request.Request("https://provider.test/models")

    with pytest.raises(ModelCatalogURLPolicyError, match="model catalog URL"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "http://remote.test/models",
        )


def test_model_catalog_refresh_aborts_after_size_limit_without_parsing(monkeypatch) -> None:
    from travis.ai.providers import model_catalog_fetch

    response = _Response(b"[" + b" " * model_catalog_fetch.MAX_MODEL_CATALOG_BYTES + b"]")
    monkeypatch.setattr(model_catalog_fetch, "_open_request", lambda *_args, **_kwargs: response)

    assert model_catalog_fetch.fetch_model_catalog(
        provider_name="fixture",
        url="https://provider.test/models",
    ) is None
    assert all(size <= model_catalog_fetch.MODEL_CATALOG_READ_CHUNK_BYTES for size in response.read_sizes)
    assert sum(response.read_sizes) <= model_catalog_fetch.MAX_MODEL_CATALOG_BYTES + 1


@pytest.mark.parametrize(
    "body",
    [
        b"not-json SECRET_BODY",
        json.dumps({"models": [{"id": "wrong-shape"}]}).encode(),
        json.dumps({"data": {"id": "wrong-shape"}}).encode(),
    ],
)
def test_model_catalog_refresh_malformed_or_nonlist_payload_returns_none_without_body_leak(
    monkeypatch,
    caplog: pytest.LogCaptureFixture,
    body: bytes,
) -> None:
    from travis.ai.providers import model_catalog_fetch

    caplog.set_level("DEBUG", logger=model_catalog_fetch.__name__)
    monkeypatch.setattr(
        model_catalog_fetch,
        "_open_request",
        lambda *_args, **_kwargs: _Response(body),
    )

    assert model_catalog_fetch.fetch_model_catalog(
        provider_name="fixture",
        url="https://provider.test/models",
    ) is None
    assert "SECRET_BODY" not in caplog.text
    assert "model catalog fetch failed for fixture" in caplog.text


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ([{"id": "one"}, {"id": 2}, {"name": "missing"}], ["one"]),
        ({"data": [{"id": "one"}, {"id": "two"}]}, ["one", "two"]),
    ],
)
def test_model_catalog_refresh_accepts_current_list_and_data_shapes(
    monkeypatch,
    payload: object,
    expected: list[str],
) -> None:
    from travis.ai.providers import model_catalog_fetch

    monkeypatch.setattr(
        model_catalog_fetch,
        "_open_request",
        lambda *_args, **_kwargs: _Response(json.dumps(payload).encode()),
    )

    assert model_catalog_fetch.fetch_model_catalog(
        provider_name="fixture",
        url="https://provider.test/models",
        api_key="fixture-secret",
        headers={"x-fixture": "yes"},
    ) == expected
