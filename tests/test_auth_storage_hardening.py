from __future__ import annotations

import json
import threading
import time

import pytest

from travis.ai.auth import ModelAuth, OAuthAuth, ProviderAuth
from travis.ai.models import Models, Provider, ProviderStreams
from travis.ai.types import Model
from travis.coding_agent.auth_storage import AuthStorage, AuthStorageError


def test_set_fails_closed_when_auth_file_is_malformed(tmp_path) -> None:
    path = tmp_path / "auth.json"
    path.write_text("{malformed", encoding="utf-8")
    storage = AuthStorage.create(path)

    with pytest.raises(AuthStorageError, match="malformed"):
        storage.set("openrouter", {"type": "api_key", "key": "secret"})

    assert storage.get("openrouter") is None
    assert path.read_text(encoding="utf-8") == "{malformed"


def test_expired_oauth_refreshes_once_and_persists(tmp_path) -> None:
    path = tmp_path / "auth.json"
    path.write_text(
        json.dumps(
            {
                "example": {
                    "type": "oauth",
                    "access": "stale",
                    "refresh": "refresh-token",
                    "expires": 1,
                }
            }
        ),
        encoding="utf-8",
    )
    refresh_calls = 0

    def refresh_token(credential):
        nonlocal refresh_calls
        refresh_calls += 1
        time.sleep(0.01)
        return {
            "access": "fresh",
            "refresh": credential["refresh"],
            "expires": 4_102_444_800_000,
        }

    storage = AuthStorage.create(path)
    model = Model(
        id="example-model",
        name="Example",
        api="faux",
        provider="example",
        base_url="https://example.invalid/v1",
    )
    unused_stream = lambda *_args, **_kwargs: None
    runtime = Models(credentials=storage)
    runtime.set_provider(
        Provider(
            id="example",
            auth=ProviderAuth(
                oauth=OAuthAuth(
                    name="Example",
                    login=lambda _callbacks: {},
                    refresh=refresh_token,
                    to_auth=lambda credential: ModelAuth(api_key=str(credential["access"])),
                )
            ),
            models=[model],
            api=ProviderStreams(stream=unused_stream, stream_simple=unused_stream),
        )
    )
    results: list[str | None] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(
                runtime.get_auth(model).auth.api_key  # type: ignore[union-attr]
            )
        )
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == ["fresh"] * 8
    assert refresh_calls == 1
    assert json.loads(path.read_text(encoding="utf-8"))["example"]["access"] == "fresh"


def test_auth_status_configured_matches_has_auth(monkeypatch) -> None:
    storage = AuthStorage.in_memory()
    storage.set_runtime_api_key("runtime", "secret")

    assert storage.has_auth("runtime") is True
    assert storage.get_auth_status("runtime") == {
        "configured": True,
        "source": "runtime",
        "label": "--api-key",
    }
