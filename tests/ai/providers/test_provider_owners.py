from __future__ import annotations

import builtins
import dataclasses

import pytest

from travis.ai.providers.faux import faux_model
from travis.ai.types import Context, UserMessage


@pytest.mark.parametrize(
    ("api_mode", "expected"),
    [
        ("openai-completions", True),
        ("chat_completions", True),
        ("bedrock_converse", True),
        ("future-provider", False),
        ("", False),
    ],
)
def test_profile_transport_availability_uses_leaf_mode_facts(
    monkeypatch: pytest.MonkeyPatch,
    api_mode: str,
    expected: bool,
) -> None:
    from travis.ai.providers.provider_profiles import ProviderProfile

    profile = ProviderProfile(name="fixture", api_mode=api_mode)
    original_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object) -> object:
        assert name not in {
            "travis.ai.providers.transport_registry",
            "travis.ai.providers.transports",
        }
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    assert profile.transport_available is expected


def test_message_translation_owner_matches_canonical_shape() -> None:
    from travis.ai.providers.message_translation import translate_messages

    messages, tools = translate_messages(Context(messages=[UserMessage(content="hello")]), faux_model())

    assert messages == [{"role": "user", "content": "hello"}]
    assert tools is None


def test_prepared_provider_request_is_frozen() -> None:
    from travis.ai.providers.provider_request import PreparedProviderRequest

    request = PreparedProviderRequest(
        url="https://provider.test/v1/chat/completions",
        headers={},
        body={},
        timeout_seconds=30,
        api_mode="chat-completions",
        decoder=lambda _lines: iter(()),
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        request.url = "changed"
