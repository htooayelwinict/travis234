"""Direct branch characterizations for chat message sanitization."""

from __future__ import annotations

from travis.ai.providers.transport_families.chat_completions import ChatCompletionsTransport


def test_clean_messages_keep_the_original_zero_copy_list() -> None:
    messages = [{"role": "user", "content": "hello"}]

    converted = ChatCompletionsTransport().convert_messages(messages, model="gpt-5")

    assert converted is messages


def test_internal_message_and_tool_fields_are_removed_from_a_deep_copy() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "answer",
            "codex_reasoning_items": [{"type": "reasoning"}],
            "codex_message_items": [{"type": "message"}],
            "tool_name": "read",
            "timestamp": 123,
            "_private": "internal",
            "tool_calls": [
                {
                    "id": "call-1",
                    "call_id": "provider-call",
                    "response_item_id": "item-1",
                    "extra_content": {"thought_signature": "opaque"},
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        }
    ]

    converted = ChatCompletionsTransport().convert_messages(messages, model="gpt-5")

    assert converted == [
        {
            "role": "assistant",
            "content": "answer",
            "tool_calls": [
                {
                    "id": "call-1",
                    "function": {"name": "read", "arguments": "{}"},
                }
            ],
        }
    ]
    assert converted is not messages
    assert messages[0]["tool_calls"][0]["extra_content"] == {
        "thought_signature": "opaque"
    }


def test_gemini_and_gemma_models_preserve_tool_thought_signatures() -> None:
    for model in ("gemini-2.5-pro", "gemma-3"):
        messages = [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "call_id": "provider-call",
                        "extra_content": {"thought_signature": "opaque"},
                    }
                ],
            }
        ]

        converted = ChatCompletionsTransport().convert_messages(messages, model=model)

        assert converted == [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "extra_content": {"thought_signature": "opaque"},
                    }
                ],
            }
        ]
