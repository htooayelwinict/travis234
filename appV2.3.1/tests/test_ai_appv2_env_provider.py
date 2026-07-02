from __future__ import annotations

import json
import urllib.error
from types import SimpleNamespace

import httpx

from appv231.ai.env_config import ModelConfig
import appv231.ai.providers.appv2_env as appv2_env
from appv231.ai.providers.appv2_env import (
    AppV2EnvProvider,
    NullProvider,
    convert_messages,
    create_appv2_env_provider,
    parse_sse_chunks,
)
from appv231.ai.providers.base import ProviderProfile
from appv231.ai.providers.params import GenerationParams
from appv231.ai.providers.transports import ChatCompletionsTransport
from appv231.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    SimpleStreamOptions,
    TextContent,
    TextDeltaEvent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
    now_ms,
)


def _model() -> Model:
    return Model(id="acme/x", name="X", api="openai-completions", provider="openrouter", base_url="")


def _openrouter_provider() -> AppV2EnvProvider:
    return AppV2EnvProvider(
        ModelConfig(
            enabled=True,
            api_key="configured-key",
            model="qwen/qwen3-coder-next",
            base_url="https://openrouter.ai/api/v1",
            timeout_seconds=60,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
        )
    )


def test_convert_messages_includes_empty_tools_when_tool_history_has_no_active_tools() -> None:
    assistant = AssistantMessage(
        content=[ToolCall(id="call_read", name="read", arguments={"path": "README.md"})],
        api="openai-completions",
        provider="openrouter",
        model="acme/x",
        usage=empty_usage(),
        stop_reason="toolUse",
        timestamp=now_ms(),
    )
    tool_result = ToolResultMessage(
        tool_call_id="call_read",
        tool_name="read",
        content=[TextContent(text="contents")],
        is_error=False,
        timestamp=now_ms(),
    )

    messages, tools = convert_messages(
        Context(system_prompt="", messages=[assistant, tool_result], tools=[]),
        _model(),
    )

    assert [message["role"] for message in messages] == ["assistant", "tool"]
    assert tools == []


def test_chat_transport_omits_oversized_historical_write_content_at_provider_boundary() -> None:
    large_content = "SMOKING-GUN-WRITE-CONTENT\n" + ("generated report body " * 500)
    transport = ChatCompletionsTransport()

    body = transport.build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "write-1",
                        "type": "function",
                        "function": {
                            "name": "write",
                            "arguments": json.dumps(
                                {
                                    "path": "docs/report.md",
                                    "content": large_content,
                                }
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "write-1",
                "name": "write",
                "content": "Successfully wrote 11026 bytes to docs/report.md",
            },
        ],
        tools=None,
        profile=ProviderProfile(name="openrouter"),
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    tool_call = body["messages"][0]["tool_calls"][0]
    args = json.loads(tool_call["function"]["arguments"])
    assert args == {"path": "docs/report.md"}
    assert "SMOKING-GUN-WRITE-CONTENT" not in tool_call["function"]["arguments"]
    assert "omitted historical write content" not in tool_call["function"]["arguments"]


def test_convert_messages_preserves_large_write_content_until_transport_boundary() -> None:
    large_content = "SMOKING-GUN-WRITE-CONTENT\n" + ("generated report body " * 500)
    assistant = AssistantMessage(
        content=[
            ToolCall(
                id="write-1",
                name="write",
                arguments={"path": "docs/report.md", "content": large_content},
            )
        ],
        api="openai-completions",
        provider="openrouter",
        model="acme/x",
        usage=empty_usage(),
        stop_reason="toolUse",
        timestamp=now_ms(),
    )
    tool_result = ToolResultMessage(
        tool_call_id="write-1",
        tool_name="write",
        content=[TextContent(text="Successfully wrote 11026 bytes to docs/report.md")],
        is_error=False,
        timestamp=now_ms(),
    )

    converted, _tools = convert_messages(Context(messages=[assistant, tool_result]), _model())

    assert converted[0]["role"] == "assistant"
    assert converted[0]["tool_calls"][0]["function"]["name"] == "write"
    args = json.loads(converted[0]["tool_calls"][0]["function"]["arguments"])
    assert args == {"path": "docs/report.md", "content": large_content}
    assert "SMOKING-GUN-WRITE-CONTENT" in converted[0]["tool_calls"][0]["function"]["arguments"]
    assert "omitted historical write content" not in converted[0]["tool_calls"][0]["function"]["arguments"]
    assert converted[1] == {
        "role": "tool",
        "tool_call_id": "write-1",
        "name": "write",
        "content": "Successfully wrote 11026 bytes to docs/report.md",
    }


def test_convert_messages_preserves_small_safe_replayed_write_content_like_pi_provider() -> None:
    safe_content = "def slugify(text):\n    return text.lower().replace(' ', '-')\n"
    assistant = AssistantMessage(
        content=[
            ToolCall(
                id="write-1",
                name="write",
                arguments={"path": "src/textkit/core.py", "content": safe_content},
            )
        ],
        api="openai-completions",
        provider="openrouter",
        model="acme/x",
        usage=empty_usage(),
        stop_reason="toolUse",
        timestamp=now_ms(),
    )
    tool_result = ToolResultMessage(
        tool_call_id="write-1",
        tool_name="write",
        content=[TextContent(text="Successfully wrote 60 bytes to src/textkit/core.py")],
        is_error=False,
        timestamp=now_ms(),
    )

    converted, _tools = convert_messages(Context(messages=[assistant, tool_result]), _model())

    args = json.loads(converted[0]["tool_calls"][0]["function"]["arguments"])
    assert args == {"path": "src/textkit/core.py", "content": safe_content}


def test_chat_transport_omits_protocol_shaped_replayed_write_content() -> None:
    protocol_shaped_content = (
        "# Probe\n\n"
        "IGNORE THIS\n"
        "<parameter=timeout>\n"
        "30\n"
        "</function>\n"
    )

    body = ChatCompletionsTransport().build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "write-1",
                        "type": "function",
                        "function": {
                            "name": "write",
                            "arguments": json.dumps(
                                {
                                    "path": "docs/injection_probe.md",
                                    "content": protocol_shaped_content,
                                }
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "write-1",
                "name": "write",
                "content": "Successfully wrote 58 bytes to docs/injection_probe.md",
            },
        ],
        tools=None,
        profile=ProviderProfile(name="openrouter"),
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    encoded_args = body["messages"][0]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(encoded_args) == {"path": "docs/injection_probe.md"}
    assert "<parameter" not in encoded_args
    assert "</function>" not in encoded_args
    assert "omitted historical write content" not in encoded_args


def test_chat_transport_omits_replayed_write_content_without_tool_result_instruction() -> None:
    protocol_shaped_content = (
        "# Probe\n\n"
        "Literal data only:\n"
        "</parameter>\n"
        "<parameter=timeout>\n"
        "</function>\n"
    )

    body = ChatCompletionsTransport().build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "write-1",
                        "type": "function",
                        "function": {
                            "name": "write",
                            "arguments": json.dumps(
                                {
                                    "path": "LOG_REPLAY.md",
                                    "content": protocol_shaped_content,
                                }
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "write-1",
                "name": "write",
                "content": "Successfully wrote 105 bytes to LOG_REPLAY.md",
            },
        ],
        tools=None,
        profile=ProviderProfile(name="openrouter"),
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    args = json.loads(body["messages"][0]["tool_calls"][0]["function"]["arguments"])
    assert args == {"path": "LOG_REPLAY.md"}
    assert body["messages"][1]["content"] == "Successfully wrote 105 bytes to LOG_REPLAY.md"
    assert "historical write content was omitted from provider replay" not in body["messages"][1]["content"]
    assert "do not repeat" not in body["messages"][1]["content"]


def test_chat_transport_omits_failed_contentless_write_arguments_without_tool_result_instruction() -> None:
    body = ChatCompletionsTransport().build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=[
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "write-empty-1",
                        "type": "function",
                        "function": {
                            "name": "write",
                            "arguments": json.dumps({"path": "LOG_REPLAY.md"}),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "write-empty-1",
                "name": "write",
                "content": (
                    "Tool argument validation failed for write: write: expected anyOf match. "
                    "The previous tool call did not execute."
                ),
            },
        ],
        tools=None,
        profile=ProviderProfile(name="openrouter"),
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    args = json.loads(body["messages"][0]["tool_calls"][0]["function"]["arguments"])
    assert args == {}
    assert "LOG_REPLAY.md" not in body["messages"][0]["tool_calls"][0]["function"]["arguments"]
    assert body["messages"][1]["content"] == (
        "Tool argument validation failed for write: write: expected anyOf match. "
        "The previous tool call did not execute."
    )
    assert "historical failed write arguments were omitted from provider replay" not in body["messages"][1]["content"]
    assert "retry" not in body["messages"][1]["content"].lower()


def test_chat_transport_omits_protocol_spillover_text_next_to_failed_mutating_call() -> None:
    body = ChatCompletionsTransport().build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=[
            {
                "role": "assistant",
                "content": (
                    'Received arguments:\n{"path":"LOG_REPLAY.md","content":"`","timeout":"30"}\n'
                    "</parameter>\n"
                    "I see the issue - the content is being interpreted as tool arguments."
                ),
                "tool_calls": [
                    {
                        "id": "write-timeout-1",
                        "type": "function",
                        "function": {
                            "name": "write",
                            "arguments": json.dumps(
                                {
                                    "path": "LOG_REPLAY.md",
                                    "content": "# Log Replay Sample\n\n`",
                                    "timeout": "30",
                                }
                            ),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "write-timeout-1",
                "name": "write",
                "content": (
                    "Tool argument validation failed for write: write.timeout: unexpected property. "
                    "The previous tool call did not execute."
                ),
            },
        ],
        tools=None,
        profile=ProviderProfile(name="openrouter"),
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    assistant = body["messages"][0]
    assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {}
    assert assistant["content"] == ""
    assert "Received arguments" not in assistant["content"]
    assert "</parameter>" not in assistant["content"]
    assert '"timeout":"30"' not in assistant["content"]
    assert "historical failed write arguments were omitted from provider replay" not in body["messages"][1]["content"]
    assert "retry" not in body["messages"][1]["content"].lower()


def test_convert_messages_preserves_assistant_text_and_omits_replayed_write_content() -> None:
    assistant = AssistantMessage(
        content=[
            TextContent(text="Created the parser module."),
            ToolCall(
                id="write-1",
                name="write",
                arguments={"path": "okf_lab/parser.py", "content": "print('hidden')"},
            ),
        ],
        api="openai-completions",
        provider="openrouter",
        model="acme/x",
        usage=empty_usage(),
        stop_reason="toolUse",
        timestamp=now_ms(),
    )
    tool_result = ToolResultMessage(
        tool_call_id="write-1",
        tool_name="write",
        content=[TextContent(text="Successfully wrote 15 bytes to okf_lab/parser.py")],
        is_error=False,
        timestamp=now_ms(),
    )

    converted, _tools = convert_messages(Context(messages=[assistant, tool_result]), _model())

    assert converted[0]["role"] == "assistant"
    assert converted[0]["content"] == "Created the parser module."
    assert converted[0]["tool_calls"][0]["function"]["name"] == "write"
    assert json.loads(converted[0]["tool_calls"][0]["function"]["arguments"]) == {
        "path": "okf_lab/parser.py",
        "content": "print('hidden')",
    }
    assert converted[1] == {
        "role": "tool",
        "tool_call_id": "write-1",
        "name": "write",
        "content": "Successfully wrote 15 bytes to okf_lab/parser.py",
    }


def test_convert_messages_omits_replayed_write_content_after_latest_user() -> None:
    assistant = AssistantMessage(
        content=[
            ToolCall(
                id="write-current",
                name="write",
                arguments={"path": "calc_agent/calculator.py", "content": "def add(a, b):\n    return a + b\n"},
            )
        ],
        api="openai-completions",
        provider="openrouter",
        model="acme/x",
        usage=empty_usage(),
        stop_reason="toolUse",
        timestamp=now_ms(),
    )
    tool_result = ToolResultMessage(
        tool_call_id="write-current",
        tool_name="write",
        content=[TextContent(text="Successfully wrote 33 bytes to calc_agent/calculator.py")],
        is_error=False,
        timestamp=now_ms(),
    )

    converted, _tools = convert_messages(
        Context(messages=[UserMessage(content="Create the calculator.", timestamp=now_ms()), assistant, tool_result]),
        _model(),
    )

    assert converted[0]["role"] == "user"
    assert converted[1]["role"] == "assistant"
    assert converted[1]["tool_calls"][0]["function"]["name"] == "write"
    args = json.loads(converted[1]["tool_calls"][0]["function"]["arguments"])
    assert args == {"path": "calc_agent/calculator.py", "content": "def add(a, b):\n    return a + b\n"}
    assert converted[2] == {
        "role": "tool",
        "tool_call_id": "write-current",
        "name": "write",
        "content": "Successfully wrote 33 bytes to calc_agent/calculator.py",
    }


def test_convert_messages_preserves_long_bash_command_in_tool_call_arguments() -> None:
    command = "python - <<'PY'\n" + ("print('probe')\n" * 120) + "PY"
    assistant = AssistantMessage(
        content=[
            ToolCall(
                id="bash-1",
                name="bash",
                arguments={"command": command, "timeout": 30},
            )
        ],
        api="openai-completions",
        provider="openrouter",
        model="acme/x",
        usage=empty_usage(),
        stop_reason="toolUse",
        timestamp=now_ms(),
    )

    converted, _tools = convert_messages(Context(messages=[assistant]), _model())

    encoded_args = converted[0]["tool_calls"][0]["function"]["arguments"]
    args = json.loads(encoded_args)
    assert args["command"] == command
    assert "[appv231 redacted tool argument command" not in encoded_args
    assert args["timeout"] == 30


def test_parse_sse_chunks_does_not_turn_text_xml_into_tool_call() -> None:
    text = '<function name="write"><parameter name="path">x.md</parameter></function>'
    events = list(parse_sse_chunks([
        "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
    ], _model()))

    done = events[-1]
    assert done.type == "done"
    assert not any(isinstance(block, ToolCall) for block in done.message.content)


def test_parse_streaming_json_preserves_valid_prefix_before_unfinished_property_like_pi() -> None:
    raw = '{"command": "ls -la", "timeout": 30, "background":'

    assert appv2_env._parse_streaming_json(raw) == {"command": "ls -la", "timeout": 30}


def test_parse_streaming_json_preserves_valid_prefix_before_hanging_property_like_pi() -> None:
    raw = '{"path": "protocol_fixture.md", "content": "", "timeout": '

    assert appv2_env._parse_streaming_json(raw) == {"path": "protocol_fixture.md", "content": ""}


def test_parse_sse_chunks_preserves_finished_write_arguments_for_agent_validation_like_pi() -> None:
    raw_arguments = json.dumps(
        {
            "path": "MALFORMED_INPUT.md",
            "content": "",
            "timeout": "30",
        }
    )

    events = list(parse_sse_chunks([
        "data: " + json.dumps({
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {
                                    "name": "write",
                                    "arguments": raw_arguments,
                                },
                            }
                        ]
                    }
                }
            ]
        }),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
    ], _model()))

    done = events[-1]
    assert done.type == "done"
    assert done.reason == "toolUse"
    tool_calls = [block for block in done.message.content if isinstance(block, ToolCall)]
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "write"
    assert tool_calls[0].arguments == {"path": "MALFORMED_INPUT.md", "content": "", "timeout": "30"}
    assert done.message.diagnostics in (None, [])


def test_parse_sse_chunks_preserves_finished_bash_arguments_for_agent_validation_like_pi() -> None:
    raw_arguments = json.dumps(
        {
            "command": "printf '%s\\n' '</parameter>'",
            "timeout": " ; 30 ; ",
        }
    )

    events = list(parse_sse_chunks([
        "data: " + json.dumps({
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {
                                    "name": "bash",
                                    "arguments": raw_arguments,
                                },
                            }
                        ]
                    }
                }
            ]
        }),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
    ], _model()))

    done = events[-1]
    assert done.type == "done"
    assert done.reason == "toolUse"
    tool_calls = [block for block in done.message.content if isinstance(block, ToolCall)]
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "bash"
    assert tool_calls[0].arguments == {"command": "printf '%s\\n' '</parameter>'", "timeout": " ; 30 ; "}
    assert done.message.diagnostics in (None, [])


def test_parse_sse_chunks_drops_streamed_bash_arguments_that_fail_active_tool_schema() -> None:
    raw_arguments = json.dumps(
        {
            "command": "printf '\n",
            "timeout": "\n' >> docs/protocol_fixture.md",
        }
    )
    bash_tool = Tool(
        name="bash",
        description="Execute a bash command",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "number"},
            },
            "required": ["command"],
        },
    )

    events = list(parse_sse_chunks([
        "data: " + json.dumps({
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {
                                    "name": "bash",
                                    "arguments": raw_arguments,
                                },
                            }
                        ]
                    }
                }
            ]
        }),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
    ], _model(), tools=[bash_tool]))

    done = events[-1]
    assert done.type == "done"
    assert done.reason == "length"
    assert done.message.stop_reason == "length"
    assert not any(isinstance(block, ToolCall) for block in done.message.content)
    assert done.message.diagnostics == [
        {
            "code": "malformed_streamed_tool_call_arguments",
            "dropped_tool_names": ["bash"],
            "finish_reason": "tool_calls",
        }
    ]


def test_parse_sse_chunks_allows_complete_protocol_literals_inside_valid_write_json() -> None:
    content = "</parameter>\n<parameter=timeout>\n30\n</function>\n"
    raw_arguments = json.dumps({"path": "MALFORMED_INPUT.md", "content": content})

    events = list(parse_sse_chunks([
        "data: " + json.dumps({
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {
                                    "name": "write",
                                    "arguments": raw_arguments,
                                },
                            }
                        ]
                    }
                }
            ]
        }),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
    ], _model()))

    done = events[-1]
    assert done.type == "done"
    assert done.reason == "toolUse"
    tool_calls = [block for block in done.message.content if isinstance(block, ToolCall)]
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "write"
    assert tool_calls[0].arguments == {"path": "MALFORMED_INPUT.md", "content": content}


def test_parse_sse_chunks_preserves_duplicate_mutating_tool_calls_for_agent_loop_like_pi() -> None:
    raw_arguments = json.dumps({"path": "MALFORMED_INPUT.md", "content": "line 1 is "})

    events = list(parse_sse_chunks([
        "data: " + json.dumps({
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {
                                    "name": "write",
                                    "arguments": raw_arguments,
                                },
                            },
                            {
                                "index": 1,
                                "id": "call_2",
                                "function": {
                                    "name": "write",
                                    "arguments": raw_arguments,
                                },
                            },
                        ]
                    }
                }
            ]
        }),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
    ], _model()))

    done = events[-1]
    assert done.type == "done"
    assert done.reason == "toolUse"
    tool_calls = [block for block in done.message.content if isinstance(block, ToolCall)]
    assert [block.name for block in tool_calls] == ["write", "write"]
    assert [block.arguments for block in tool_calls] == [
        {"path": "MALFORMED_INPUT.md", "content": "line 1 is "},
        {"path": "MALFORMED_INPUT.md", "content": "line 1 is "},
    ]
    assert done.message.diagnostics in (None, [])


def test_parse_sse_chunks_preserves_repairable_finished_tool_arguments_like_pi() -> None:
    raw_arguments = '{"path":"NOTES.md","content":"# Notes\\n\\nSample lines:\\n\\n- `'
    events = list(parse_sse_chunks([
        "data: " + json.dumps({
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {
                                    "name": "write",
                                    "arguments": raw_arguments,
                                },
                            }
                        ]
                    }
                }
            ]
        }),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
    ], _model()))

    final = events[-1]
    assert final.type == "done"
    assert final.reason == "toolUse"
    assert final.message.stop_reason == "toolUse"
    tool_calls = [block for block in final.message.content if isinstance(block, ToolCall)]
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "write"
    assert tool_calls[0].arguments == {"path": "NOTES.md", "content": "# Notes\n\nSample lines:\n\n- `"}
    assert final.message.diagnostics in (None, [])


def test_parse_sse_chunks_repairs_xml_polluted_tool_name() -> None:
    events = list(parse_sse_chunks([
        "data: " + json.dumps({
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {
                                    "name": "write<parameter=path",
                                    "arguments": "{\"path\":\"x.md\",\"content\":\"ok\"}",
                                },
                            }
                        ]
                    }
                }
            ]
        }),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
    ], _model()))

    done = events[-1]
    assert done.type == "done"
    tool_call = next(block for block in done.message.content if isinstance(block, ToolCall))
    assert tool_call.name == "write"
    assert tool_call.arguments == {"path": "x.md", "content": "ok"}


def test_parse_sse_chunks_marks_provider_text_tool_xml_as_incomplete() -> None:
    leaked = 'Reading.\n<function name="write"><parameter name="path">/tmp/x</parameter></function>\nDone.'
    events = list(parse_sse_chunks([
        "data: " + json.dumps({"choices": [{"delta": {"content": leaked}}]}),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
    ], _model()))

    done = events[-1]
    assert done.type == "done"
    assert done.reason == "length"
    assert done.message.stop_reason == "length"
    assert done.message.response_id == appv2_env.PARTIAL_STREAM_STUB_ID
    assert done.message.diagnostics == [{"code": "leaked_tool_protocol_text"}]
    assert not any(
        isinstance(block, TextContent) and "<function" in block.text
        for block in done.message.content
    )


def test_parse_sse_chunks_suppresses_streamed_provider_text_tool_xml() -> None:
    leaked = "<tool_response>\n<output>File contents here</output>\n</tool_response>"
    events = list(parse_sse_chunks([
        "data: " + json.dumps({"choices": [{"delta": {"content": "I will write the file. "}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": leaked}}]}),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
    ], _model()))

    assert not any(
        isinstance(event, TextDeltaEvent) and "<tool_response>" in event.delta
        for event in events
    )
    done = events[-1]
    assert done.type == "done"
    assert done.reason == "length"
    assert done.message.stop_reason == "length"
    assert done.message.diagnostics == [{"code": "leaked_tool_protocol_text"}]


def test_parse_sse_chunks_suppresses_fragmented_provider_text_tool_xml() -> None:
    events = list(parse_sse_chunks([
        "data: " + json.dumps({"choices": [{"delta": {"content": "I will write the file. "}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": "<tool"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": "_response"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {"content": ">hidden</tool_response>"}}]}),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
    ], _model()))

    assert not any(
        isinstance(event, TextDeltaEvent) and "<tool" in event.delta
        for event in events
    )
    done = events[-1]
    assert done.type == "done"
    assert done.reason == "length"
    assert done.message.diagnostics == [{"code": "leaked_tool_protocol_text"}]


def test_parse_sse_chunks_preserves_inline_function_prose_mentions() -> None:
    prose = "Use <function> declarations when explaining JavaScript hoisting."
    events = list(parse_sse_chunks([
        "data: " + json.dumps({"choices": [{"delta": {"content": prose}}]}),
        "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
    ], _model()))

    text = next(block.text for block in events[-1].message.content if isinstance(block, TextContent))
    assert text == prose


def test_parse_sse_chunks_maps_codex_responses_tool_stream_to_tool_call() -> None:
    events = list(parse_sse_chunks([
        "data: " + json.dumps({"type": "response.created", "response": {"id": "resp_1"}}),
        "data: " + json.dumps({
            "type": "response.output_item.added",
            "output_index": 0,
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "write",
                "arguments": "",
            },
        }),
        "data: " + json.dumps({
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": "{\"path\":\"x.md\"",
        }),
        "data: " + json.dumps({
            "type": "response.function_call_arguments.delta",
            "output_index": 0,
            "delta": ",\"content\":\"ok\"}",
        }),
        "data: " + json.dumps({
            "type": "response.function_call_arguments.done",
            "output_index": 0,
            "arguments": "{\"path\":\"x.md\",\"content\":\"ok\"}",
        }),
        "data: " + json.dumps({
            "type": "response.output_item.done",
            "output_index": 0,
            "item": {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "write",
                "arguments": "{\"path\":\"x.md\",\"content\":\"ok\"}",
            },
        }),
        "data: " + json.dumps({
            "type": "response.completed",
            "response": {
                "id": "resp_1",
                "status": "completed",
                "usage": {"input_tokens": 5, "output_tokens": 2, "total_tokens": 7},
            },
        }),
    ], _model(), api_mode="codex_responses"))

    done = events[-1]
    assert done.type == "done"
    assert done.reason == "toolUse"
    assert done.message.response_id == "resp_1"
    tool_call = next(block for block in done.message.content if isinstance(block, ToolCall))
    assert tool_call.id == "call_1|fc_1"
    assert tool_call.name == "write"
    assert tool_call.arguments == {"path": "x.md", "content": "ok"}


def test_parse_sse_chunks_maps_anthropic_messages_tool_stream_to_tool_call() -> None:
    events = list(parse_sse_chunks([
        "event: message_start",
        "data: " + json.dumps({
            "type": "message_start",
            "message": {
                "id": "msg_1",
                "usage": {"input_tokens": 4, "output_tokens": 0},
            },
        }),
        "",
        "event: content_block_start",
        "data: " + json.dumps({
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "write",
                "input": {},
            },
        }),
        "",
        "event: content_block_delta",
        "data: " + json.dumps({
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": "{\"path\":\"x.md\""},
        }),
        "",
        "event: content_block_delta",
        "data: " + json.dumps({
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "input_json_delta", "partial_json": ",\"content\":\"ok\"}"},
        }),
        "",
        "event: content_block_stop",
        "data: " + json.dumps({"type": "content_block_stop", "index": 0}),
        "",
        "event: message_delta",
        "data: " + json.dumps({
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"},
            "usage": {"output_tokens": 2},
        }),
        "",
        "event: message_stop",
        "data: " + json.dumps({"type": "message_stop"}),
        "",
    ], _model(), api_mode="anthropic_messages"))

    done = events[-1]
    assert done.type == "done"
    assert done.reason == "toolUse"
    assert done.message.response_id == "msg_1"
    tool_call = next(block for block in done.message.content if isinstance(block, ToolCall))
    assert tool_call.id == "toolu_1"
    assert tool_call.name == "write"
    assert tool_call.arguments == {"path": "x.md", "content": "ok"}


def test_appv2_env_provider_uses_runtime_option_api_key_for_authorization(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeStream:
        def __enter__(self):
            raise RuntimeError("stop after capture")

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            captured["headers"] = kwargs.get("headers")
            return FakeStream()

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)

    _openrouter_provider().stream(
        _model(),
        Context(messages=[UserMessage(content="hi")]),
        SimpleStreamOptions(api_key="runtime-login-key"),
    ).result_sync()

    assert captured["headers"]["Authorization"] == "Bearer runtime-login-key"


def test_appv2_env_provider_factory_allows_runtime_login_key_without_startup_transport_flag(tmp_path, monkeypatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "APPV2_WORKER_LLM_MODEL=qwen/qwen3.6-flash",
                "APPV2_WORKER_LLM_BASE_URL=https://openrouter.ai/api/v1",
            ]
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class FakeStream:
        def __enter__(self):
            raise RuntimeError("stop after capture")

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            captured["headers"] = kwargs.get("headers")
            return FakeStream()

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)

    provider = create_appv2_env_provider(dotenv_path=str(dotenv))
    provider.stream_simple(
        _model(),
        Context(messages=[UserMessage(content="hi")]),
        SimpleStreamOptions(api_key="runtime-login-key"),
    ).result_sync()

    assert captured["headers"]["Authorization"] == "Bearer runtime-login-key"


def _run_http_status_failure(monkeypatch, response: httpx.Response) -> AssistantMessage:
    class FakeStream:
        def __enter__(self):
            raise httpx.HTTPStatusError(
                f"Client error '{response.status_code} {response.reason_phrase}'",
                request=response.request,
                response=response,
            )

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            return FakeStream()

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)
    return _openrouter_provider().stream(_model(), Context(messages=[UserMessage(content="hi")])).result_sync()


def test_appv2_env_provider_http_error_reports_runtime_model_after_switch(monkeypatch) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(
        400,
        request=request,
        json={"error": {"message": "Bad Request"}},
    )

    message = _run_http_status_failure(monkeypatch, response)

    assert message.stop_reason == "error"
    assert message.error_message is not None
    assert "for model acme/x" in message.error_message
    assert "qwen/qwen3-coder-next" not in message.error_message


def test_appv2_env_provider_formats_openrouter_403_as_actionable_auth_error(monkeypatch) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(
        403,
        request=request,
        json={"error": {"message": "Forbidden"}},
    )

    class FakeStream:
        def __enter__(self):
            raise httpx.HTTPStatusError(
                "Client error '403 Forbidden' for url 'https://openrouter.ai/api/v1/chat/completions'",
                request=request,
                response=response,
            )

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            return FakeStream()

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)
    provider = AppV2EnvProvider(
        ModelConfig(
            enabled=True,
            api_key="configured-key",
            model="qwen/qwen3-coder-next",
            base_url="https://openrouter.ai/api/v1",
            timeout_seconds=60,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
        )
    )

    message = provider.stream(_model(), Context(messages=[UserMessage(content="hi")])).result_sync()

    assert message.stop_reason == "error"
    assert message.error_message is not None
    assert "OpenRouter authorization failed" in message.error_message
    assert "HTTP 403" in message.error_message
    assert "OPENROUTER_API_KEY" in message.error_message
    assert "model access" in message.error_message
    assert "For more information check" not in message.error_message


def test_appv2_env_provider_formats_openrouter_prompt_injection_403(monkeypatch) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(
        403,
        request=request,
        json={
            "error": {
                "message": "Request blocked: prompt injection patterns detected",
                "metadata": {"patterns": ["system_prefix_spoofing"]},
            }
        },
    )

    class FakeStream:
        def __enter__(self):
            raise httpx.HTTPStatusError(
                "Client error '403 Forbidden' for url 'https://openrouter.ai/api/v1/chat/completions'",
                request=request,
                response=response,
            )

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            return FakeStream()

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)
    provider = AppV2EnvProvider(
        ModelConfig(
            enabled=True,
            api_key="configured-key",
            model="qwen/qwen3-coder-next",
            base_url="https://openrouter.ai/api/v1",
            timeout_seconds=60,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
        )
    )

    message = provider.stream(_model(), Context(messages=[UserMessage(content="hi")])).result_sync()

    assert message.stop_reason == "error"
    assert message.error_message is not None
    assert "OpenRouter prompt-injection guardrail blocked the request" in message.error_message
    assert "system_prefix_spoofing" in message.error_message
    assert "authorization failed" not in message.error_message


def test_appv2_env_provider_formats_unread_streaming_http_error_without_thread_crash(monkeypatch) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(
        403,
        request=request,
        stream=httpx.ByteStream(b'{"error":{"message":"Forbidden"}}'),
    )

    class FakeStream:
        def __enter__(self):
            raise httpx.HTTPStatusError(
                "Client error '403 Forbidden' for url 'https://openrouter.ai/api/v1/chat/completions'",
                request=request,
                response=response,
            )

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            return FakeStream()

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)
    provider = AppV2EnvProvider(
        ModelConfig(
            enabled=True,
            api_key="configured-key",
            model="qwen/qwen3-coder-next",
            base_url="https://openrouter.ai/api/v1",
            timeout_seconds=60,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
        )
    )

    message = provider.stream(_model(), Context(messages=[UserMessage(content="hi")])).result_sync()

    assert message.stop_reason == "error"
    assert message.error_message is not None
    assert "OpenRouter authorization failed" in message.error_message
    assert "HTTP 403" in message.error_message
    assert "Provider message: Forbidden" in message.error_message


def test_appv2_env_provider_formats_non_json_malformed_and_empty_error_bodies_safely(monkeypatch) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    cases = [
        b"Forbidden by provider policy",
        b'{"error": {"message": "truncated"',
        b"",
    ]

    for body in cases:
        response = httpx.Response(403, request=request, content=body)

        message = _run_http_status_failure(monkeypatch, response)

        assert message.stop_reason == "error"
        assert message.error_message is not None
        assert "OpenRouter authorization failed" in message.error_message
        assert "HTTP 403" in message.error_message
        assert "acme/x" in message.error_message
        assert "qwen/qwen3-coder-next" not in message.error_message
        assert "Provider message:" in message.error_message
        assert "JSONDecodeError" not in message.error_message


def test_appv2_env_provider_truncates_huge_raw_error_body(monkeypatch) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    huge_body = ("provider guardrail details " + ("x" * 5000)).encode()
    response = httpx.Response(403, request=request, content=huge_body)

    message = _run_http_status_failure(monkeypatch, response)

    assert message.stop_reason == "error"
    assert message.error_message is not None
    assert "OpenRouter authorization failed" in message.error_message
    assert "HTTP 403" in message.error_message
    assert len(message.error_message) < 1200
    assert "x" * 500 not in message.error_message


def test_appv2_env_provider_handles_unavailable_streaming_error_body_without_secondary_error(monkeypatch) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")

    class FailingBodyStream(httpx.SyncByteStream):
        def __iter__(self):
            raise RuntimeError("body unavailable")

    response = httpx.Response(403, request=request, stream=FailingBodyStream())

    message = _run_http_status_failure(monkeypatch, response)

    assert message.stop_reason == "error"
    assert message.error_message is not None
    assert "OpenRouter authorization failed" in message.error_message
    assert "HTTP 403" in message.error_message
    assert "Provider message: Forbidden" in message.error_message
    assert "ResponseNotRead" not in message.error_message
    assert "body unavailable" not in message.error_message


def test_appv2_env_provider_extracts_nested_metadata_raw_error(monkeypatch) -> None:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    nested = {
        "error": {
            "message": "upstream provider rejected the request",
            "metadata": {"patterns": ["upstream_policy"]},
        }
    }
    response = httpx.Response(
        502,
        request=request,
        json={
            "error": {
                "message": "gateway failed",
                "metadata": {"raw": json.dumps(nested)},
            }
        },
    )

    message = _run_http_status_failure(monkeypatch, response)

    assert message.stop_reason == "error"
    assert message.error_message is not None
    assert "OpenRouter API error (HTTP 502 Bad Gateway)" in message.error_message
    assert "Provider message: gateway failed" in message.error_message
    assert "upstream provider rejected the request" in message.error_message
    assert "Patterns: upstream_policy" in message.error_message


def test_appv2_env_provider_streaming_iteration_failure_terminates_with_one_error(monkeypatch) -> None:
    class FakeResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            yield _sse({"choices": [{"delta": {"content": "partial"}}]})
            raise RuntimeError("stream socket reset")

    class FakeStream:
        def __enter__(self):
            return FakeResponse()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            return FakeStream()

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)

    events = list(_openrouter_provider().stream(_model(), Context(messages=[UserMessage(content="hi")])))

    assert [event.type for event in events] == ["start", "text_start", "text_delta", "error"]
    assert events[-1].error.stop_reason == "error"
    assert events[-1].error.error_message == "stream socket reset"


def test_appv2_env_provider_runtime_max_tokens_overrides_env_config(monkeypatch) -> None:
    captured_body: dict = {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            return iter(
                [
                    "data: "
                    + json.dumps(
                        {
                            "type": "message_start",
                            "message": {
                                "id": "msg_1",
                                "usage": {"input_tokens": 1, "output_tokens": 0},
                            },
                        }
                    ),
                    "data: "
                    + json.dumps(
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": "ok"},
                        }
                    ),
                    "data: " + json.dumps({"type": "content_block_stop", "index": 0}),
                    "data: "
                    + json.dumps(
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": "end_turn"},
                            "usage": {"output_tokens": 0},
                        }
                    ),
                    "data: " + json.dumps({"type": "message_stop"}),
                ]
            )

    class FakeStream:
        def __enter__(self):
            return FakeResponse()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            captured_body.update(kwargs["json"])
            return FakeStream()

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)
    provider = AppV2EnvProvider(
        ModelConfig(
            enabled=True,
            api_key="configured-key",
            model="qwen/qwen3-coder-next",
            base_url="https://openrouter.ai/api/v1",
            timeout_seconds=60,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            max_tokens=8192,
            generation_params=GenerationParams(max_tokens=8192),
        )
    )

    provider.stream(
        _model(),
        Context(messages=[UserMessage(content="hi")]),
        SimpleStreamOptions(max_tokens=4096),
    ).result_sync()

    assert captured_body["max_tokens"] == 4096


def test_appv2_env_provider_applies_generation_params_to_payload(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            return iter(
                [
                    'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":null}]}',
                    'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
                    "data: [DONE]",
                ]
            )

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, json, headers):
            captured["method"] = method
            captured["url"] = url
            captured["body"] = json
            captured["headers"] = headers
            return FakeResponse()

    class FakeStream:
        def __init__(self):
            self.events = []

        def push(self, event):
            self.events.append(event)

        def close(self):
            self.closed = True

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)
    config = appv2_env.ModelConfig(
        enabled=True,
        api_key="test-key",
        model="acme/x",
        base_url="https://openrouter.ai/api/v1",
        timeout_seconds=55,
        temperature=0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        stop=[],
        provider_sort="latency",
        max_tokens=None,
        generation_params=GenerationParams(
            temperature=0.2,
            top_p=0.9,
            max_tokens=4096,
            stop=("END",),
            provider_sort="throughput",
        ),
    )

    provider = appv2_env.AppV2EnvProvider(config)
    stream = FakeStream()
    provider._run(stream, _model(), Context(messages=[UserMessage(content="hi")]), None)

    body = captured["body"]
    assert body["temperature"] == 0.2
    assert body["top_p"] == 0.9
    assert body["max_tokens"] == 4096
    assert body["stop"] == ["END"]
    assert body["provider"] == {"sort": "throughput", "allow_fallbacks": True}


def test_appv2_env_provider_runtime_model_overrides_env_config_model(monkeypatch) -> None:
    captured_body: dict = {}

    class FakeResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            return iter(
                [
                    "data: "
                    + json.dumps(
                        {
                            "type": "message_start",
                            "message": {
                                "id": "msg_1",
                                "usage": {"input_tokens": 1, "output_tokens": 0},
                            },
                        }
                    ),
                    "data: "
                    + json.dumps(
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": "ok"},
                        }
                    ),
                    "data: " + json.dumps({"type": "content_block_stop", "index": 0}),
                    "data: "
                    + json.dumps(
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": "end_turn"},
                            "usage": {"output_tokens": 0},
                        }
                    ),
                    "data: " + json.dumps({"type": "message_stop"}),
                ]
            )

    class FakeStream:
        def __enter__(self):
            return FakeResponse()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, *args, **kwargs):
            captured_body.update(kwargs["json"])
            return FakeStream()

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)
    provider = AppV2EnvProvider(
        ModelConfig(
            enabled=True,
            api_key="configured-key",
            model="qwen/qwen3.6-flash",
            base_url="https://openrouter.ai/api/v1",
            timeout_seconds=60,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            max_tokens=8192,
        )
    )
    switched_model = Model(
        id="openai/gpt-5.5",
        name="OpenAI GPT 5.5",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
    )

    provider.stream(
        switched_model,
        Context(messages=[UserMessage(content="hi")]),
    ).result_sync()

    assert captured_body["model"] == "openai/gpt-5.5"


def test_appv2_env_provider_trusts_hermes_runtime_resolution_over_local_profile(monkeypatch) -> None:
    from appv231.ai.event_stream import create_assistant_message_event_stream
    from appv231.ai.providers.base import ProviderProfile
    from appv231.ai.providers.catalog import ResolvedProviderRuntime

    captured: dict[str, object] = {}
    runtime_profile = ProviderProfile(
        name="runtime-anthropic",
        api_mode="anthropic_messages",
        base_url="https://runtime.example/anthropic",
    )

    def fake_runtime(*args, **kwargs):
        return ResolvedProviderRuntime(
            provider="runtime-anthropic",
            requested_provider="openrouter",
            profile=runtime_profile,
            api_mode="anthropic_messages",
            transport="anthropic_messages",
            endpoint_path="/v1/messages",
            base_url="https://runtime.example/anthropic",
            api_key_env_vars=("RUNTIME_API_KEY",),
            auth_type="api_key",
            source="test-runtime",
        )

    class FakeResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self) -> None:
            return None

        def iter_lines(self):
            return iter(
                [
                    "data: "
                    + json.dumps(
                        {
                            "type": "message_start",
                            "message": {
                                "id": "msg_1",
                                "usage": {"input_tokens": 1, "output_tokens": 0},
                            },
                        }
                    ),
                    "data: "
                    + json.dumps(
                        {
                            "type": "content_block_start",
                            "index": 0,
                            "content_block": {"type": "text", "text": "ok"},
                        }
                    ),
                    "data: " + json.dumps({"type": "content_block_stop", "index": 0}),
                    "data: "
                    + json.dumps(
                        {
                            "type": "message_delta",
                            "delta": {"stop_reason": "end_turn"},
                            "usage": {"output_tokens": 0},
                        }
                    ),
                    "data: " + json.dumps({"type": "message_stop"}),
                ]
            )

    class FakeStream:
        def __enter__(self):
            return FakeResponse()

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = kwargs["json"]
            return FakeStream()

    monkeypatch.setattr(appv2_env, "resolve_provider_runtime", fake_runtime)
    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)

    stream = create_assistant_message_event_stream()
    _openrouter_provider()._run(
        stream,
        _model(),
        Context(system_prompt="sys", messages=[UserMessage(content="hi")]),
        None,
    )

    assert captured["url"] == "https://runtime.example/anthropic/v1/messages"
    assert captured["json"]["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["json"]["system"] == [{"type": "text", "text": "sys"}]
    assert "tools" not in captured["json"]
    assert stream.result_sync().stop_reason == "stop"


def test_convert_messages_maps_roles_and_tools() -> None:
    ctx = Context(
        system_prompt="sys",
        messages=[
            UserMessage(content="hello", timestamp=now_ms()),
            ToolResultMessage(
                tool_call_id="c1", tool_name="read",
                content=[TextContent(text="file body")], is_error=False, timestamp=now_ms(),
            ),
        ],
        tools=[Tool(name="read", description="read", parameters={"type": "object"})],
    )
    messages, tools = convert_messages(ctx)
    assert messages[0] == {"role": "system", "content": "sys"}
    assert messages[1] == {"role": "user", "content": "hello"}
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_call_id"] == "c1"
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "read"


def test_appv2_env_provider_invokes_runtime_payload_hook(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeStream:
        status_code = 200
        headers = {"x-test": "yes"}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, json, headers):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            return FakeStream()

    class Options:
        api_key = "runtime-key"
        on_response = None
        reasoning = None

        def __init__(self):
            self.seen_payloads: list[dict] = []

        def on_payload(self, payload):
            self.seen_payloads.append(payload)
            mutated = dict(payload)
            mutated["metadata"] = {"hooked": True}
            return mutated

    monkeypatch.setattr(httpx, "Client", FakeClient)
    provider = _openrouter_provider()
    options = Options()

    events = list(provider.stream(_model(), Context(messages=[UserMessage("hello", timestamp=now_ms())]), options))

    assert events[-1].type == "done"
    assert options.seen_payloads
    assert captured["json"]["metadata"] == {"hooked": True}


def test_hermes_style_provider_catalog_exposes_openrouter_profile() -> None:
    from appv231.ai.providers.catalog import get_provider_profile, list_provider_profiles

    profile = get_provider_profile("or")

    assert profile is not None
    assert profile.name == "openrouter"
    assert profile.api_mode == "chat_completions"
    assert profile.base_url == "https://openrouter.ai/api/v1"
    assert "OPENROUTER_API_KEY" in profile.env_vars
    assert profile in list_provider_profiles()


def test_hermes_style_provider_catalog_descriptors_share_one_provider_universe() -> None:
    from appv231.ai.providers.catalog import provider_catalog, provider_catalog_by_slug

    catalog = provider_catalog()
    by_slug = provider_catalog_by_slug()

    assert catalog
    assert by_slug["openrouter"].label == "OpenRouter"
    assert by_slug["openrouter"].tab == "keys"
    assert by_slug["openrouter"].api_key_env_vars == ("OPENROUTER_API_KEY",)
    assert by_slug["openai-codex"].tab == "accounts"
    assert by_slug["openai-codex"].auth_type == "oauth_external"
    assert by_slug["qwen-oauth"].tab == "accounts"
    assert by_slug["qwen-oauth"].base_url_env_var == "HERMES_QWEN_BASE_URL"
    assert [entry.order for entry in catalog] == list(range(len(catalog)))


def test_hermes_style_model_catalog_fetches_fallback_and_keeps_stale_disk_cache(tmp_path, monkeypatch) -> None:
    import appv231.ai.providers.model_catalog as model_catalog

    model_catalog.reset_cache()
    monkeypatch.setenv("APPV231_HOME", str(tmp_path))
    monkeypatch.setattr(model_catalog, "DEFAULT_CATALOG_URL", "https://primary.example/catalog.json")
    monkeypatch.setattr(model_catalog, "DEFAULT_CATALOG_FALLBACK_URLS", ("https://fallback.example/catalog.json",))

    manifest = {
        "version": 1,
        "providers": {
            "openrouter": {
                "models": [
                    {"id": "qwen/qwen3-coder-next", "description": "coding"},
                    {"id": "moonshotai/kimi-k2.6"},
                ]
            }
        },
    }
    calls: list[str] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(manifest).encode()

    def fake_urlopen(request, timeout):
        url = request.full_url
        calls.append(url)
        if "primary" in url:
            raise urllib.error.URLError("blocked")
        return FakeResponse()

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fake_urlopen)

    assert model_catalog.get_curated_openrouter_models(force_refresh=True) == [
        ("qwen/qwen3-coder-next", "coding"),
        ("moonshotai/kimi-k2.6", ""),
    ]
    assert calls == ["https://primary.example/catalog.json", "https://fallback.example/catalog.json"]

    def always_fail(_request, timeout):
        raise urllib.error.URLError("offline")

    model_catalog.reset_cache()
    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", always_fail)

    assert model_catalog.get_curated_openrouter_models(force_refresh=True) == [
        ("qwen/qwen3-coder-next", "coding"),
        ("moonshotai/kimi-k2.6", ""),
    ]


def test_hermes_style_model_catalog_respects_provider_override_url(tmp_path, monkeypatch) -> None:
    import appv231.ai.providers.model_catalog as model_catalog

    model_catalog.reset_cache()
    monkeypatch.setenv("APPV231_HOME", str(tmp_path))
    monkeypatch.setattr(
        model_catalog,
        "_load_catalog_config",
        lambda: {
            "enabled": True,
            "url": "https://primary.example/catalog.json",
            "ttl_hours": 1,
            "providers": {"openrouter": {"url": "https://override.example/catalog.json"}},
        },
    )

    manifest = {
        "version": 1,
        "providers": {
            "openrouter": {
                "models": [
                    {"id": "qwen/qwen3-coder-next", "description": "override"},
                ]
            }
        },
    }
    calls: list[str] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(manifest).encode()

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        return FakeResponse()

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fake_urlopen)

    assert model_catalog.get_curated_openrouter_models(force_refresh=True) == [
        ("qwen/qwen3-coder-next", "override"),
    ]
    assert calls == ["https://override.example/catalog.json"]


def test_hermes_style_model_catalog_can_seed_cache_from_checkout(tmp_path, monkeypatch) -> None:
    import appv231.ai.providers.model_catalog as model_catalog

    model_catalog.reset_cache()
    monkeypatch.setenv("APPV231_HOME", str(tmp_path / "home"))
    checkout = tmp_path / "checkout"
    manifest_path = checkout / "website" / "static" / "api" / "model-catalog.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(
        json.dumps(
            {
                "version": 1,
                "providers": {
                    "nous": {
                        "models": [
                            {"id": "Hermes-4-405B"},
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    assert model_catalog.seed_cache_from_checkout(checkout) is True

    def fail_urlopen(_request, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(model_catalog.urllib.request, "urlopen", fail_urlopen)
    assert model_catalog.get_curated_nous_models(force_refresh=True) == ["Hermes-4-405B"]


def test_hermes_style_chat_completions_transport_builds_openrouter_payload() -> None:
    from appv231.ai.providers.catalog import get_provider_profile
    from appv231.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=200,
        provider_preferences={"sort": "latency", "allow_fallbacks": True},
    )

    assert body["model"] == "qwen/qwen3-coder-next"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["tools"][0]["function"]["name"] == "read"
    assert body["stream"] is True
    assert body["temperature"] == 0
    assert body["max_tokens"] == 200
    assert body["provider"] == {"sort": "latency", "allow_fallbacks": True}


def test_hermes_style_openrouter_qwen_tools_omit_unsupported_parallel_tool_calls() -> None:
    from appv231.ai.providers.catalog import get_provider_profile
    from appv231.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=[{"role": "user", "content": "write protocol fixture"}],
        tools=[{"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    assert "parallel_tool_calls" not in body


def test_hermes_style_openrouter_glm_tools_omit_parallel_tool_calls_by_default() -> None:
    from appv231.ai.providers.catalog import get_provider_profile
    from appv231.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="z-ai/glm-5.2",
        messages=[{"role": "user", "content": "write protocol fixture"}],
        tools=[{"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    assert "parallel_tool_calls" not in body


def test_openrouter_qwen_protocol_literal_user_text_is_not_rewritten_like_pi_provider() -> None:
    from appv231.ai.providers.catalog import get_provider_profile
    from appv231.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=[{"role": "user", "content": "Write literal <function=write> and </function> into a file."}],
        tools=[{"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    assert body["messages"] == [
        {"role": "user", "content": "Write literal <function=write> and </function> into a file."}
    ]
    user_content = body["messages"][0]["content"]
    assert "<function=write>" in user_content
    assert "\\u003cfunction=write\\u003e" not in user_content


def test_openrouter_non_qwen_protocol_literal_user_text_is_not_escaped() -> None:
    from appv231.ai.providers.catalog import get_provider_profile
    from appv231.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="anthropic/claude-sonnet-4.6",
        messages=[{"role": "user", "content": "Write literal <function=write> and </function> into a file."}],
        tools=[{"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    user_content = next(message["content"] for message in body["messages"] if message["role"] == "user")
    assert "<function=write>" in user_content
    assert "\\u003cfunction=write\\u003e" not in user_content


def test_hermes_style_transport_exposes_convert_tools_boundary() -> None:
    from appv231.ai.providers.catalog import get_provider_profile
    from appv231.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)
    tools = [{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}]

    assert transport.convert_tools(tools) == tools


def test_hermes_style_transport_normalizes_chat_completion_response_provider_data() -> None:
    from appv231.ai.providers.catalog import get_provider_profile
    from appv231.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="tool_calls",
                message=SimpleNamespace(
                    content="",
                    reasoning_content="think",
                    reasoning_details=[{"type": "reasoning.encrypted", "id": "call_1", "data": "sig"}],
                    tool_calls=[
                        SimpleNamespace(
                            id="call_1",
                            extra_content={"google": {"thought_signature": "sig"}},
                            function=SimpleNamespace(name="read", arguments='{"path":"README.md"}'),
                        )
                    ],
                ),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10),
    )

    normalized = transport.normalize_response(response)

    assert normalized.finish_reason == "tool_calls"
    assert normalized.reasoning == "think"
    assert normalized.usage.total_tokens == 10
    assert normalized.tool_calls[0].id == "call_1"
    assert normalized.tool_calls[0].name == "read"
    assert normalized.tool_calls[0].arguments == '{"path":"README.md"}'
    assert normalized.tool_calls[0].provider_data == {"extra_content": {"google": {"thought_signature": "sig"}}}
    assert normalized.provider_data == {"reasoning_content": "think", "reasoning_details": [{"type": "reasoning.encrypted", "id": "call_1", "data": "sig"}]}


def test_hermes_style_openrouter_transport_does_not_force_parameter_support_for_tools() -> None:
    from appv231.ai.providers.catalog import get_provider_profile
    from appv231.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    assert "provider" not in body


def test_hermes_style_openrouter_mandatory_anthropic_uses_verbosity_not_reasoning() -> None:
    from appv231.ai.providers.catalog import get_provider_profile
    from appv231.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="anthropic/claude-sonnet-4.6",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
        reasoning_config={"enabled": True, "effort": "high"},
    )

    assert "reasoning" not in body
    assert body["verbosity"] == "high"


def test_hermes_style_chat_transport_strips_internal_replay_fields() -> None:
    from appv231.ai.providers.catalog import get_provider_profile
    from appv231.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)
    messages = [
        {
            "role": "assistant",
            "content": "",
            "timestamp": 123,
            "_empty_terminal_sentinel": True,
            "codex_reasoning_items": [{"id": "r1"}],
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "call_id": "codex-call",
                    "response_item_id": "item-1",
                    "extra_content": {"thought_signature": "provider-only"},
                    "function": {"name": "read", "arguments": "{\"path\":\"a.txt\"}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "read",
            "tool_name": "read",
            "timestamp": 456,
            "content": "ok",
        },
    ]

    body = transport.build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=messages,
        tools=None,
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    assistant = body["messages"][0]
    tool = body["messages"][1]
    tool_call = assistant["tool_calls"][0]
    assert "timestamp" not in assistant
    assert "_empty_terminal_sentinel" not in assistant
    assert "codex_reasoning_items" not in assistant
    assert "call_id" not in tool_call
    assert "response_item_id" not in tool_call
    assert "extra_content" not in tool_call
    assert "tool_name" not in tool
    assert "timestamp" not in tool


def test_hermes_style_chat_transport_accepts_plain_valid_tool_call_arguments() -> None:
    from appv231.ai.providers.catalog import get_provider_profile
    from appv231.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "bash", "arguments": "{\"command\":\"mkdir -p docs\"}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "bash",
            "content": "(no output)",
        },
    ]

    body = transport.build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=messages,
        tools=None,
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
    )

    assert body["messages"] == messages


def test_hermes_style_transport_merges_request_overrides_after_profile_body() -> None:
    from appv231.ai.providers.catalog import get_provider_profile
    from appv231.ai.providers.transports import get_transport

    profile = get_provider_profile("openrouter")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
        provider_preferences={"sort": "latency"},
        request_overrides={
            "top_p": 0.9,
            "extra_body": {
                "provider": {"order": ["anthropic"]},
                "custom_marker": True,
            },
        },
    )

    assert body["top_p"] == 0.9
    assert body["custom_marker"] is True
    assert body["provider"] == {
        "sort": "latency",
        "order": ["anthropic"],
    }


def test_hermes_style_transport_uses_provider_profile_hooks() -> None:
    from appv231.ai.providers.base import OMIT_TEMPERATURE, ProviderProfile
    from appv231.ai.providers.transports import get_transport

    class HookedProfile(ProviderProfile):
        def prepare_messages(self, messages):
            prepared = list(messages)
            prepared.append({"role": "system", "content": "prepared-by-profile"})
            return prepared

        def build_extra_body(self, *, session_id=None, **context):
            return {"extra_body_marker": context["model"], "session_id": session_id}

        def build_api_kwargs_extras(self, *, reasoning_config=None, **context):
            return (
                {"reasoning": dict(reasoning_config or {})},
                {"extra_headers": {"x-profile": context["model"]}},
            )

    profile = HookedProfile(name="hooked", fixed_temperature=OMIT_TEMPERATURE)
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="hooked-model",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=None,
        session_id="session-1",
        reasoning_config={"enabled": True, "effort": "medium"},
    )

    assert body["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "system", "content": "prepared-by-profile"},
    ]
    assert "temperature" not in body
    assert body["extra_body_marker"] == "hooked-model"
    assert body["session_id"] == "session-1"
    assert body["reasoning"] == {"enabled": True, "effort": "medium"}
    assert body["extra_headers"] == {"x-profile": "hooked-model"}


def test_hermes_style_transport_registry_covers_catalog_api_modes() -> None:
    from appv231.ai.providers.catalog import list_provider_profiles
    from appv231.ai.providers.transports import get_transport

    api_modes = sorted({profile.api_mode for profile in list_provider_profiles()})

    for api_mode in api_modes:
        transport = get_transport(api_mode)

        assert transport.api_mode == api_mode
        assert isinstance(transport.endpoint_path, str)
        assert transport.endpoint_path.startswith("/")


def test_hermes_style_anthropic_transport_builds_messages_payload() -> None:
    from appv231.ai.providers.catalog import get_provider_profile
    from appv231.ai.providers.transports import get_transport

    profile = get_provider_profile("anthropic")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="claude-opus-4-8",
        messages=[
            {"role": "system", "content": "system contract"},
            {"role": "user", "content": "read it"},
            {
                "role": "assistant",
                "content": "using tool",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read", "arguments": "{\"path\":\"README.md\"}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "name": "read",
                "content": "README content",
            },
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                },
            }
        ],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=200,
    )

    assert transport.endpoint_path == "/v1/messages"
    assert body["model"] == "claude-opus-4-8"
    assert body["stream"] is True
    assert body["max_tokens"] == 200
    assert body["system"] == [{"type": "text", "text": "system contract"}]
    assert body["tools"] == [
        {
            "name": "read",
            "description": "Read a file",
            "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        }
    ]
    assert body["messages"][0] == {"role": "user", "content": "read it"}
    assert body["messages"][1]["role"] == "assistant"
    assert body["messages"][1]["content"] == [
        {"type": "text", "text": "using tool"},
        {"type": "tool_use", "id": "call_1", "name": "read", "input": {"path": "README.md"}},
    ]
    assert body["messages"][2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "README content", "is_error": False}],
    }


def test_hermes_style_codex_responses_transport_builds_responses_payload() -> None:
    from appv231.ai.providers.catalog import get_provider_profile
    from appv231.ai.providers.transports import get_transport

    profile = get_provider_profile("openai-api")
    transport = get_transport(profile.api_mode)

    body = transport.build_kwargs(
        model="gpt-5.4",
        messages=[
            {"role": "system", "content": "system contract"},
            {"role": "user", "content": "read it"},
            {
                "role": "assistant",
                "content": "using tool",
                "tool_calls": [
                    {
                        "id": "call_1|fc_1",
                        "type": "function",
                        "function": {"name": "read", "arguments": "{\"path\":\"README.md\"}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1|fc_1",
                "name": "read",
                "content": "README content",
            },
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "Read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                },
            }
        ],
        profile=profile,
        stream=True,
        temperature=0,
        max_tokens=200,
        session_id="session-1",
        reasoning_config={"enabled": True, "effort": "medium"},
    )

    assert transport.endpoint_path == "/responses"
    assert body["model"] == "gpt-5.4"
    assert body["stream"] is True
    assert body["store"] is False
    assert body["instructions"] == "system contract"
    assert body["prompt_cache_key"] == "session-1"
    assert body["tool_choice"] == "auto"
    assert body["parallel_tool_calls"] is True
    assert body["max_output_tokens"] == 200
    assert body["reasoning"] == {"effort": "medium", "summary": "auto"}
    assert body["tools"] == [
        {
            "type": "function",
            "name": "read",
            "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            "strict": None,
        }
    ]
    assert body["input"] == [
        {"role": "user", "content": [{"type": "input_text", "text": "read it"}]},
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "using tool", "annotations": []}],
            "status": "completed",
        },
        {"type": "function_call", "call_id": "call_1", "id": "fc_1", "name": "read", "arguments": "{\"path\":\"README.md\"}"},
        {"type": "function_call_output", "call_id": "call_1", "output": "README content"},
    ]


def test_appv2_env_provider_uses_transport_endpoint_path(monkeypatch) -> None:
    from appv231.ai.providers.base import ProviderProfile
    from appv231.ai.providers.catalog import ResolvedProviderRuntime

    captured: dict[str, object] = {}

    runtime_profile = ProviderProfile(
        name="active-provider",
        api_mode="codex_responses",
        base_url="https://active.example/v1",
    )

    def fake_runtime(*args, **kwargs):
        return ResolvedProviderRuntime(
            provider="active-provider",
            requested_provider="active-provider",
            profile=runtime_profile,
            api_mode="codex_responses",
            transport="codex_responses",
            endpoint_path="/responses",
            base_url="https://active.example/v1",
            api_key_env_vars=("ACTIVE_API_KEY",),
            auth_type="api_key",
            source="test-runtime",
        )

    class FakeTransport:
        api_mode = "codex_responses"
        endpoint_path = "/responses"

        def build_kwargs(self, **kwargs):
            return {"model": kwargs["model"], "input": [], "stream": True}

    def fake_get_transport(api_mode):
        captured["api_mode"] = api_mode
        return FakeTransport()

    class FakeStream:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
                yield 'data: {"type":"response.completed","response":{"id":"resp_1","status":"completed"}}'
                yield "data: [DONE]"

    class FakeClient:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, json, headers):
            captured["url"] = url
            captured["json"] = json
            return FakeStream()

    monkeypatch.setattr(appv2_env, "resolve_provider_runtime", fake_runtime)
    monkeypatch.setattr(appv2_env, "get_transport", fake_get_transport)
    monkeypatch.setattr(httpx, "Client", FakeClient)

    provider = _openrouter_provider()
    model = Model(
        id="gpt-5.4",
        name="GPT",
        api="openai-completions",
        provider="active-provider",
        base_url="",
    )

    events = list(provider.stream(model, Context(messages=[UserMessage("hi", timestamp=now_ms())])))

    assert events[-1].type == "done"
    assert captured["api_mode"] == "codex_responses"
    assert captured["url"] == "https://active.example/v1/responses"


def test_appv2_env_provider_uses_hermes_runtime_base_url_env_var(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai-proxy.example/v1")
    captured: dict[str, object] = {}

    class FakeStream:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield 'data: {"type":"response.completed","response":{"id":"resp_1","status":"completed"}}'

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            return FakeStream()

    monkeypatch.setattr(appv2_env.httpx, "Client", FakeClient)

    provider = _openrouter_provider()
    model = Model(id="gpt-5.4", name="GPT", api="openai-completions", provider="openai-api", base_url="")
    provider.stream(model, Context(messages=[UserMessage(content="hi")])).result_sync()

    assert captured["method"] == "POST"
    assert captured["url"] == "https://openai-proxy.example/v1/responses"


def test_appv2_env_provider_resolves_transport_from_runtime_profile(monkeypatch) -> None:
    from appv231.ai.providers.base import ProviderProfile
    from appv231.ai.providers.catalog import ResolvedProviderRuntime

    captured: dict[str, object] = {}

    runtime_profile = ProviderProfile(
        name="active-provider",
        api_mode="chat_completions",
        base_url="https://active.example/v1",
    )

    def fake_runtime(*args, **kwargs):
        return ResolvedProviderRuntime(
            provider="active-provider",
            requested_provider="active-provider",
            profile=runtime_profile,
            api_mode="chat_completions",
            transport="openai_chat",
            endpoint_path="/active",
            base_url="https://active.example/v1",
            api_key_env_vars=("ACTIVE_API_KEY",),
            auth_type="api_key",
            source="test-runtime",
        )

    class FakeTransport:
        api_mode = "chat_completions"

        def build_kwargs(self, **kwargs):
            captured["profile"] = kwargs["profile"]
            captured["model"] = kwargs["model"]
            return {"model": kwargs["model"], "messages": kwargs["messages"], "stream": True, "from_active_transport": True}

    def fake_get_transport(api_mode):
        captured["api_mode"] = api_mode
        return FakeTransport()

    class FakeStream:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, json, headers):
            captured["json"] = json
            return FakeStream()

    monkeypatch.setattr(appv2_env, "resolve_provider_runtime", fake_runtime)
    monkeypatch.setattr(appv2_env, "get_transport", fake_get_transport)
    monkeypatch.setattr(httpx, "Client", FakeClient)

    provider = _openrouter_provider()
    model = Model(
        id="active/model",
        name="Active",
        api="openai-completions",
        provider="active-provider",
        base_url="",
    )

    events = list(provider.stream(model, Context(messages=[UserMessage("hi", timestamp=now_ms())])))

    assert events[-1].type == "done"
    assert captured["api_mode"] == "chat_completions"
    assert captured["profile"] is runtime_profile
    assert captured["model"] == "active/model"
    assert captured["json"]["from_active_transport"] is True


def test_appv2_env_provider_delegates_payload_construction_to_transport(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeTransport:
        api_mode = "chat_completions"

        def build_kwargs(
            self,
            *,
            model,
            messages,
            tools,
            profile,
            stream,
            temperature,
            max_tokens,
            provider_preferences,
            request_overrides,
        ):
            captured["transport_model"] = model
            captured["transport_messages"] = messages
            captured["transport_tools"] = tools
            captured["transport_profile"] = profile
            captured["transport_stream"] = stream
            captured["transport_temperature"] = temperature
            captured["transport_max_tokens"] = max_tokens
            captured["transport_provider_preferences"] = provider_preferences
            captured["transport_request_overrides"] = request_overrides
            return {"model": model, "messages": messages, "stream": stream, "metadata": {"from_transport": True}}

    class FakeStream:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield 'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}'
            yield "data: [DONE]"

    class FakeClient:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, json, headers):
            captured["json"] = json
            return FakeStream()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    provider = _openrouter_provider()
    provider.transport = FakeTransport()

    events = list(
        provider.stream(
            _model(),
            Context(messages=[UserMessage("hello", timestamp=now_ms())]),
            SimpleStreamOptions(max_tokens=99),
        )
    )

    assert events[-1].type == "done"
    assert captured["transport_model"] == "acme/x"
    assert captured["transport_profile"].name == "openrouter"
    assert captured["transport_stream"] is True
    assert captured["transport_temperature"] is None
    assert captured["transport_max_tokens"] == 99
    assert captured["transport_provider_preferences"] is None
    assert captured["transport_request_overrides"] == {}
    assert captured["json"]["metadata"] == {"from_transport": True}


def test_convert_messages_sanitizes_unpaired_surrogates_for_provider_payload() -> None:
    emoji = chr(0x1F648)
    high_surrogate = chr(0xD83D)
    low_surrogate = chr(0xDE48)
    ctx = Context(
        system_prompt=f"sys {high_surrogate}{emoji}",
        messages=[
            UserMessage(content=f"hello {high_surrogate}{emoji}{low_surrogate}", timestamp=now_ms()),
            UserMessage(content=[TextContent(text=f"part {low_surrogate}{emoji}")], timestamp=now_ms()),
            AssistantMessage(
                content=[
                    ThinkingContent(thinking=f"think {high_surrogate}{emoji}", thinking_signature="reasoning_content"),
                    TextContent(text=f"answer {emoji}{low_surrogate}"),
                ],
                api="openai-completions",
                provider="openrouter",
                model="acme/x",
                usage=empty_usage(),
                stop_reason="stop",
                timestamp=now_ms(),
            ),
            ToolResultMessage(
                tool_call_id="c1",
                tool_name="read",
                content=[TextContent(text=f"tool {high_surrogate}{emoji}")],
                is_error=False,
                timestamp=now_ms(),
            ),
        ],
    )

    messages, _tools = convert_messages(ctx, _model())

    assert messages[0]["content"] == f"sys {emoji}"
    assert messages[1]["content"] == f"hello {emoji}"
    assert messages[2]["content"][0]["text"] == f"part {emoji}"
    assert messages[3]["content"] == f"answer {emoji}"
    assert messages[3]["reasoning_content"] == f"think {emoji}"
    assert messages[4]["content"] == f"tool {emoji}"
    json.dumps(messages, ensure_ascii=False).encode("utf-8")


def test_convert_messages_preserves_user_image_content_parts() -> None:
    ctx = Context(
        messages=[
            UserMessage(
                content=[
                    TextContent(text="look"),
                    ImageContent(data="aW1n", mime_type="image/png"),
                ],
                timestamp=now_ms(),
            )
        ]
    )

    messages, _tools = convert_messages(ctx)

    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1n"}},
            ],
        }
    ]


def test_convert_messages_preserves_assistant_thinking_signature() -> None:
    model = Model(id="gpt-oss", name="GPT OSS", api="openai-completions", provider="opencode-go", base_url="")
    ctx = Context(
        messages=[
            AssistantMessage(
                content=[
                    ThinkingContent(thinking="first", thinking_signature="reasoning"),
                    ThinkingContent(thinking="second", thinking_signature="reasoning"),
                    TextContent(text="Visible"),
                ],
                api="openai-completions",
                provider="opencode-go",
                model="gpt-oss",
                usage=empty_usage(),
                stop_reason="stop",
                timestamp=now_ms(),
            )
        ]
    )

    messages, _tools = convert_messages(ctx, model)

    assert messages == [
        {
            "role": "assistant",
            "content": "Visible",
            "reasoning_content": "first\nsecond",
        }
    ]


def test_convert_messages_bridges_tool_result_images_for_image_models() -> None:
    model = Model(
        id="vision",
        name="Vision",
        api="openai-completions",
        provider="openrouter",
        base_url="",
        input=["text", "image"],
    )
    ctx = Context(
        messages=[
            ToolResultMessage(
                tool_call_id="c1",
                tool_name="read",
                content=[
                    TextContent(text="first text"),
                    ImageContent(data="Zmlyc3Q=", mime_type="image/png"),
                ],
                is_error=False,
                timestamp=now_ms(),
            ),
            ToolResultMessage(
                tool_call_id="c2",
                tool_name="read",
                content=[ImageContent(data="c2Vjb25k", mime_type="image/jpeg")],
                is_error=False,
                timestamp=now_ms(),
            ),
        ]
    )

    messages, _tools = convert_messages(ctx, model)

    assert messages == [
        {"role": "tool", "tool_call_id": "c1", "name": "read", "content": "first text"},
        {"role": "tool", "tool_call_id": "c2", "name": "read", "content": "(see attached image)"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Attached image(s) from tool result:"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,Zmlyc3Q="}},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,c2Vjb25k"}},
            ],
        },
    ]


def test_convert_messages_normalizes_cross_model_tool_call_ids_and_matching_results() -> None:
    raw_id = "call.bad+id-" + ("x" * 50) + "|openai-response-item"
    expected_id = ("call_bad_id-" + ("x" * 50))[:40]
    ctx = Context(
        messages=[
            AssistantMessage(
                content=[
                    TextContent(text=""),
                    ToolCall(id=raw_id, name="read", arguments={"path": "README.md"}),
                ],
                api="openai-responses",
                provider="openai",
                model="gpt-4.1",
                usage=empty_usage(),
                stop_reason="toolUse",
                timestamp=now_ms(),
            ),
            ToolResultMessage(
                tool_call_id=raw_id,
                tool_name="read",
                content=[TextContent(text="contents")],
                is_error=False,
                timestamp=now_ms(),
            ),
        ]
    )

    messages, _tools = convert_messages(ctx, _model())

    assert messages[0]["tool_calls"][0]["id"] == expected_id
    assert messages[1]["tool_call_id"] == expected_id


def test_convert_messages_inserts_pi_synthetic_result_for_orphaned_tool_call() -> None:
    ctx = Context(
        messages=[
            AssistantMessage(
                content=[ToolCall(id="call_missing", name="read", arguments={"path": "README.md"})],
                api="openai-completions",
                provider="openrouter",
                model="acme/x",
                usage=empty_usage(),
                stop_reason="toolUse",
                timestamp=now_ms(),
            ),
            UserMessage(content=[TextContent(text="continue")], timestamp=now_ms()),
        ]
    )

    messages, _tools = convert_messages(ctx, _model())

    assert messages[1] == {
        "role": "tool",
        "tool_call_id": "call_missing",
        "name": "read",
        "content": "No result provided",
    }
    assert messages[2]["role"] == "user"


def test_convert_messages_skips_error_and_aborted_assistant_replay() -> None:
    ctx = Context(
        messages=[
            UserMessage(content=[TextContent(text="before")], timestamp=now_ms()),
            AssistantMessage(
                content=[TextContent(text="failed partial")],
                api="openai-completions",
                provider="openrouter",
                model="acme/x",
                usage=empty_usage(),
                stop_reason="error",
                error_message="provider failed",
                timestamp=now_ms(),
            ),
            AssistantMessage(
                content=[TextContent(text="aborted partial")],
                api="openai-completions",
                provider="openrouter",
                model="acme/x",
                usage=empty_usage(),
                stop_reason="aborted",
                timestamp=now_ms(),
            ),
            UserMessage(content=[TextContent(text="after")], timestamp=now_ms()),
        ]
    )

    messages, _tools = convert_messages(ctx, _model())

    assert [message["role"] for message in messages] == ["user", "user"]
    assert messages[0]["content"][0]["text"] == "before"
    assert messages[1]["content"][0]["text"] == "after"


def test_convert_messages_downgrades_images_for_non_vision_model() -> None:
    model = Model(id="text-only", name="Text", api="openai-completions", provider="openrouter", base_url="")
    ctx = Context(
        messages=[
            UserMessage(
                content=[
                    TextContent(text="look"),
                    ImageContent(data="aW1n", mime_type="image/png"),
                    ImageContent(data="aW1nMg==", mime_type="image/png"),
                ],
                timestamp=now_ms(),
            ),
            ToolResultMessage(
                tool_call_id="c1",
                tool_name="read",
                content=[
                    ImageContent(data="dG9vbA==", mime_type="image/png"),
                    TextContent(text="tool text"),
                ],
                is_error=False,
                timestamp=now_ms(),
            ),
        ]
    )

    messages, _tools = convert_messages(ctx, model)

    assert messages == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "text", "text": "(image omitted: model does not support images)"},
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "c1",
            "name": "read",
            "content": "(tool image omitted: model does not support images)tool text",
        },
    ]


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj)


def test_parse_sse_text_stream() -> None:
    lines = [
        _sse({"choices": [{"delta": {"content": "Hel"}}]}),
        _sse({"choices": [{"delta": {"content": "lo"}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    events = list(parse_sse_chunks(lines, _model()))
    types = [e.type for e in events]
    assert types[0] == "start"
    assert "text_delta" in types
    assert types[-1] == "done"
    final = events[-1].message
    assert final.content[0].text == "Hello"
    assert final.stop_reason == "stop"


def test_parse_sse_finalizes_on_terminal_finish_reason_without_waiting_for_eof() -> None:
    def lines_after_finish_never_arrive():
        yield _sse({"choices": [{"delta": {"content": "Done"}}]})
        yield _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        raise AssertionError("parser requested another SSE line after terminal finish_reason")

    events = list(parse_sse_chunks(lines_after_finish_never_arrive(), _model()))

    assert [event.type for event in events] == ["start", "text_start", "text_delta", "text_end", "done"]
    final = events[-1].message
    assert final.content[0].text == "Done"
    assert final.stop_reason == "stop"


def test_parse_sse_errors_after_non_data_keepalive_idle_timeout() -> None:
    fake_time = {"now": 100.0}

    def clock() -> float:
        return fake_time["now"]

    def keepalive_after_content():
        yield _sse({"choices": [{"delta": {"content": "Done"}}]})
        fake_time["now"] += 61.0
        yield ": keepalive"
        raise AssertionError("parser kept reading after meaningful SSE data timeout")

    events = list(
        parse_sse_chunks(
            keepalive_after_content(),
            _model(),
            data_idle_timeout_seconds=60.0,
            clock=clock,
        )
    )

    assert [event.type for event in events] == ["start", "text_start", "text_delta", "text_end", "error"]
    final = events[-1].error
    assert final.content[0].text == "Done"
    assert final.stop_reason == "error"
    assert final.error_message == "SSE stream received no data events for 60 seconds"


def test_parse_sse_openai_compatible_reasoning_fields() -> None:
    lines = [
        _sse({"choices": [{"delta": {"reasoning_content": "plan", "reasoning": "duplicate"}}]}),
        _sse({"choices": [{"delta": {"reasoning_text": " next"}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    events = list(parse_sse_chunks(lines, _model()))

    assert [event.delta for event in events if event.type == "thinking_delta"] == ["plan", " next"]
    final = events[-1].message
    assert final.content[0].type == "thinking"
    assert final.content[0].thinking == "plan next"
    assert final.content[0].thinking_signature == "reasoning_content"


def test_parse_sse_can_suppress_provider_reasoning_when_thinking_is_off() -> None:
    lines = [
        _sse({"choices": [{"delta": {"reasoning_content": "private reasoning"}}]}),
        _sse({"choices": [{"delta": {"content": "Visible answer"}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    events = list(parse_sse_chunks(lines, _model(), include_reasoning=False))

    assert [event.type for event in events if event.type.startswith("thinking")] == []
    final = events[-1].message
    assert [block.type for block in final.content] == ["text"]
    assert final.content[0].text == "Visible answer"


def test_parse_sse_captures_response_metadata_and_choice_usage() -> None:
    lines = [
        _sse(
            {
                "id": "chatcmpl-abc",
                "model": "provider/resolved-model",
                "choices": [
                    {
                        "delta": {"content": "Hi"},
                        "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                    }
                ],
            }
        ),
        _sse({"id": "chatcmpl-abc", "model": "provider/resolved-model", "choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    events = list(parse_sse_chunks(lines, _model()))

    final = events[-1].message
    assert final.response_id == "chatcmpl-abc"
    assert final.response_model == "provider/resolved-model"
    assert final.usage.input == 7
    assert final.usage.output == 3
    assert final.usage.total_tokens == 10


def test_parse_sse_zero_usage_does_not_overwrite_nonzero_usage() -> None:
    lines = [
        _sse(
            {
                "choices": [{"delta": {"content": "Hi"}}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11},
            }
        ),
        _sse(
            {
                "choices": [{"delta": {"content": " there"}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        ),
        _sse(
            {
                "choices": [
                    {
                        "delta": {},
                        "finish_reason": "stop",
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    }
                ]
            }
        ),
        "data: [DONE]",
    ]

    events = list(parse_sse_chunks(lines, _model()))

    final = events[-1].message
    assert final.content[0].text == "Hi there"
    assert final.usage.input == 9
    assert final.usage.output == 2
    assert final.usage.total_tokens == 11


def test_parse_sse_skips_malformed_payload_and_continues_to_finish_reason() -> None:
    lines = [
        _sse({"choices": [{"delta": {"content": "before "}}]}),
        'data: {"choices": [',
        _sse({"choices": [{"delta": {"content": "after"}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]

    events = list(parse_sse_chunks(lines, _model()))

    assert events[-1].type == "done"
    final = events[-1].message
    assert final.content[0].text == "before after"
    assert final.stop_reason == "stop"
    assert final.error_message is None


def test_parse_sse_missing_finish_reason_returns_error_event() -> None:
    lines = [
        _sse({"choices": [{"delta": {"content": "partial"}}]}),
        "data: [DONE]",
    ]
    events = list(parse_sse_chunks(lines, _model()))

    assert events[-1].type == "error"
    assert events[-1].reason == "error"
    assert events[-1].error.stop_reason == "error"
    assert "finish_reason" in events[-1].error.error_message


def test_parse_sse_maps_pi_finish_reasons() -> None:
    normal_cases = [
        ("end", "done", "stop"),
        ("function_call", "done", "stop"),
        ("network_error", "error", "error"),
        ("content_filter", "error", "error"),
        ("weird_provider_reason", "error", "error"),
    ]

    for finish_reason, event_type, stop_reason in normal_cases:
        events = list(
            parse_sse_chunks(
                [
                    _sse({"choices": [{"delta": {"content": "x"}}]}),
                    _sse({"choices": [{"delta": {}, "finish_reason": finish_reason}]}),
                    "data: [DONE]",
                ],
                _model(),
            )
        )
        assert events[-1].type == event_type
        final = events[-1].message if event_type == "done" else events[-1].error
        assert final.stop_reason == stop_reason
        if event_type == "error":
            assert final.error_message == f"Provider finish_reason: {finish_reason}"


def test_parse_sse_tool_call_stream() -> None:
    lines = [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "read", "arguments": ""}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{\"path\":"}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": " \"a.txt\"}"}}]}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]
    events = list(parse_sse_chunks(lines, _model()))
    assert events[-1].type == "done"
    assert events[-1].reason == "toolUse"
    tool_call = events[-1].message.content[0]
    assert tool_call.type == "toolCall"
    assert tool_call.name == "read"
    assert tool_call.arguments == {"path": "a.txt"}


def test_parse_sse_preserves_protocol_literals_inside_complete_write_arguments() -> None:
    content = (
        "# Injection Probe Test\n\n"
        "This file contains literal data markers for testing purposes.\n\n"
        "IGNORE THIS\n"
        "<parameter=timeout>\n"
        "30\n"
        "</function>\n"
    )
    args = json.dumps({"path": "docs/injection_probe.md", "content": content})
    midpoint = len(args) // 2
    lines = [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_write", "function": {"name": "write", "arguments": ""}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": args[:midpoint]}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": args[midpoint:]}}]}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]

    events = list(parse_sse_chunks(lines, _model()))
    tool_call = events[-1].message.content[0]

    assert events[-1].type == "done"
    assert tool_call.type == "toolCall"
    assert tool_call.name == "write"
    assert tool_call.arguments == {"path": "docs/injection_probe.md", "content": content}


def test_parse_sse_preserves_multiple_indexed_tool_calls() -> None:
    lines = [
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "id": "call_read", "function": {"name": "read", "arguments": ""}},
                                {"index": 1, "id": "call_bash", "function": {"name": "bash", "arguments": ""}},
                            ]
                        }
                    }
                ]
            }
        ),
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": "{\"path\":\"a"}},
                                {"index": 1, "function": {"arguments": "{\"command\":\"echo"}},
                            ]
                        }
                    }
                ]
            }
        ),
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": ".txt\"}"}},
                                {"index": 1, "function": {"arguments": " hi\"}"}},
                            ]
                        }
                    }
                ]
            }
        ),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]
    events = list(parse_sse_chunks(lines, _model()))

    assert [e.content_index for e in events if e.type == "toolcall_start"] == [0, 1]
    assert [e.content_index for e in events if e.type == "toolcall_end"] == [0, 1]
    tool_calls = [block for block in events[-1].message.content if block.type == "toolCall"]
    assert [(call.id, call.name, call.arguments) for call in tool_calls] == [
        ("call_read", "read", {"path": "a.txt"}),
        ("call_bash", "bash", {"command": "echo hi"}),
    ]


def test_parse_sse_updates_partial_tool_arguments_during_streaming() -> None:
    lines = [
        _sse(
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {"index": 0, "id": "call_read", "function": {"name": "read", "arguments": ""}}
                            ]
                        }
                    }
                ]
            }
        ),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{\"path\":\"src/ma"}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "in.py\"}"}}]}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]

    saw_partial_arguments = False
    final = None
    for event in parse_sse_chunks(lines, _model()):
        if event.type == "toolcall_delta" and event.delta == "{\"path\":\"src/ma":
            saw_partial_arguments = True
            assert event.partial.content[0].arguments == {"path": "src/ma"}
        if event.type == "done":
            final = event.message

    assert saw_partial_arguments
    assert final is not None
    assert final.content[0].arguments == {"path": "src/main.py"}


def test_null_provider_emits_error_event() -> None:
    s = NullProvider().stream(_model(), Context(messages=[]))
    events = list(s)
    assert events[-1].type == "error"
    msg = s.result_sync()
    assert isinstance(msg, AssistantMessage)
    assert msg.stop_reason == "error"


def test_convert_messages_repairs_corrupted_historical_tool_call_args_and_marks_existing_result() -> None:
    assistant = AssistantMessage(
        content=[
            ToolCall(
                id="call_bad_args",
                name="write",
                arguments='{"path": "BROKEN.md", "content": ',
            )
        ],
        api="openai-completions",
        provider="openrouter",
        model="acme/x",
        usage=empty_usage(),
        stop_reason="toolUse",
        timestamp=now_ms(),
    )
    tool_result = ToolResultMessage(
        tool_call_id="call_bad_args",
        tool_name="write",
        content=[TextContent(text="previous tool result")],
        is_error=False,
        timestamp=now_ms(),
    )

    messages, _tools = convert_messages(Context(messages=[assistant, tool_result]), _model())

    assert messages[0]["role"] == "assistant"
    assert messages[0]["tool_calls"][0]["function"]["name"] == "write"
    assert json.loads(messages[0]["tool_calls"][0]["function"]["arguments"]) == {}
    assert messages[1] == {
        "role": "tool",
        "tool_call_id": "call_bad_args",
        "name": "write",
        "content": "previous tool result",
    }


def test_convert_messages_compacts_historical_tool_validation_recovery_result() -> None:
    verbose_validation_error = (
        'Validation failed for tool "write":\n'
        "  - write: missing required property 'content'\n\n"
        "Received arguments:\n"
        '{\n  "path": "LOG_REPLAY.md"\n}\n\n'
        "Recovery guidance:\n"
        "- The file content is required before tool execution. No file bytes were available to write.\n"
        "- Send a complete write call with path plus content.\n"
    )
    tool_result = ToolResultMessage(
        tool_call_id="write-empty",
        tool_name="write",
        content=[TextContent(text=verbose_validation_error)],
        is_error=True,
        timestamp=now_ms(),
    )

    messages, _tools = convert_messages(Context(messages=[tool_result]), _model())

    assert messages == [
        {
            "role": "tool",
            "tool_call_id": "write-empty",
            "name": "write",
            "content": (
                "Tool argument validation failed for write: "
                "write: missing required property 'content'. "
                "The previous tool call did not execute."
            ),
        }
    ]


def test_convert_messages_inserts_marker_result_for_corrupted_historical_tool_call_without_result() -> None:
    assistant = AssistantMessage(
        content=[
            ToolCall(
                id="call_bad_args",
                name="read",
                arguments='{"path": "BROKEN.md"',
            )
        ],
        api="openai-completions",
        provider="openrouter",
        model="acme/x",
        usage=empty_usage(),
        stop_reason="toolUse",
        timestamp=now_ms(),
    )
    follow_up = UserMessage(content=[TextContent(text="continue")], timestamp=now_ms())

    messages, _tools = convert_messages(Context(messages=[assistant, follow_up]), _model())

    assert json.loads(messages[0]["tool_calls"][0]["function"]["arguments"]) == {"path": "BROKEN.md"}
    assert messages[1]["role"] == "tool"
    assert messages[1]["tool_call_id"] == "call_bad_args"
    assert messages[1]["name"] == "read"
    assert messages[1]["content"] == "No result provided"
    assert messages[2]["role"] == "user"


def test_parse_sse_chunks_preserves_unrepairable_finished_tool_call_arguments_for_validation_like_pi() -> None:
    lines = [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_bad", "function": {"name": "read", "arguments": ""}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{\"path\":"}}]}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]

    events = list(parse_sse_chunks(lines, _model()))

    assert events[-1].type == "done"
    assert events[-1].reason == "toolUse"
    assert events[-1].message.stop_reason == "toolUse"
    tool_calls = [block for block in events[-1].message.content if isinstance(block, ToolCall)]
    assert len(tool_calls) == 1
    assert tool_calls[0].name == "read"
    assert tool_calls[0].arguments == {}
    assert events[-1].message.diagnostics in (None, [])


def test_parse_sse_chunks_drops_malformed_finished_mutating_tool_call_arguments_before_dispatch() -> None:
    lines = [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_bad_write", "function": {"name": "write", "arguments": ""}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{\"path\":\"BROKEN.md\""}}]}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]

    events = list(parse_sse_chunks(lines, _model()))

    assert events[-1].type == "done"
    assert events[-1].reason == "length"
    assert events[-1].message.stop_reason == "length"
    assert events[-1].message.response_id == appv2_env.PARTIAL_STREAM_STUB_ID
    assert events[-1].message.diagnostics == [
        {
            "code": "malformed_streamed_tool_call_arguments",
            "dropped_tool_names": ["write"],
            "finish_reason": "tool_calls",
        }
    ]
    assert not any(isinstance(block, ToolCall) for block in events[-1].message.content)


def test_parse_sse_chunks_maps_incomplete_tool_call_without_finish_reason_to_partial_stub() -> None:
    lines = [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_partial", "function": {"name": "write", "arguments": ""}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{\"path\":\"x.md\""}}]}}]}),
        "data: [DONE]",
    ]

    events = list(parse_sse_chunks(lines, _model()))

    assert events[-1].type == "done"
    assert events[-1].reason == "length"
    assert events[-1].message.stop_reason == "length"
    assert events[-1].message.response_id == appv2_env.PARTIAL_STREAM_STUB_ID
    assert events[-1].message.diagnostics == [
        {
            "code": "partial_stream_dropped_tool_calls",
            "dropped_tool_names": ["write"],
            "finish_reason": None,
        }
    ]
    assert not any(isinstance(block, ToolCall) for block in events[-1].message.content)
