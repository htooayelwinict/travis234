from __future__ import annotations

import dataclasses

import pytest

from travis.ai.providers.faux import faux_model
from travis.ai.types import Context, UserMessage


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
