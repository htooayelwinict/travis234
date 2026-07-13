import json

from travis.ai.providers.transports import ChatCompletionsTransport


def test_chat_transport_preserves_small_historical_write_content_without_replay_instruction() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_write",
                    "type": "function",
                    "function": {
                        "name": "write",
                        "arguments": json.dumps({"path": "notes.md", "content": "# Notes\n"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_write",
            "name": "write",
            "content": "Successfully wrote 8 bytes to notes.md",
        },
    ]

    converted = ChatCompletionsTransport().convert_messages(messages, model="qwen/qwen3-coder-next")

    assert json.loads(converted[0]["tool_calls"][0]["function"]["arguments"]) == {
        "path": "notes.md",
        "content": "# Notes\n",
    }
    assert converted[1]["content"] == "Successfully wrote 8 bytes to notes.md"
    assert "do not repeat" not in converted[1]["content"]
    assert "retry" not in converted[1]["content"].lower()


def test_chat_transport_failed_write_replay_stays_neutral_without_retry_instruction() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_bad_write",
                    "type": "function",
                    "function": {
                        "name": "write",
                        "arguments": json.dumps({"path": "test_calc.py"}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_bad_write",
            "name": "write",
            "content": "Tool argument validation failed for write: missing required field content.",
        },
    ]

    converted = ChatCompletionsTransport().convert_messages(messages, model="qwen/qwen3-coder-next")

    assert converted[0]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert converted[1]["content"] == "Tool argument validation failed for write: missing required field content."
    assert "do not repeat" not in converted[1]["content"]
    assert "retry" not in converted[1]["content"].lower()
