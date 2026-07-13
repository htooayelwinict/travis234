from __future__ import annotations

from tests._support_ai_travis_env_provider import *  # noqa: F403


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


def test_parse_sse_waits_for_requested_usage_chunk_after_finish_reason() -> None:
    lines = [
        _sse({"choices": [{"delta": {"content": "Done"}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        _sse(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 17,
                    "completion_tokens": 4,
                    "total_tokens": 21,
                },
            }
        ),
        "data: [DONE]",
    ]

    events = list(parse_sse_chunks(lines, _model(), wait_for_usage_after_finish=True))

    assert events[-1].type == "done"
    assert events[-1].message.content[0].text == "Done"
    assert events[-1].message.usage.input == 17
    assert events[-1].message.usage.output == 4
    assert events[-1].message.usage.total_tokens == 21

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

def test_parse_sse_maps_travis234_finish_reasons() -> None:
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

def test_convert_messages_preserves_historical_tool_validation_recovery_result() -> None:
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

    assert messages == [{
        "role": "tool",
        "tool_call_id": "write-empty",
        "name": "write",
        "content": verbose_validation_error,
    }]

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

def test_parse_sse_chunks_preserves_unrepairable_finished_tool_call_arguments_for_validation_like_travis234() -> None:
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

def test_parse_sse_chunks_retains_malformed_finished_mutating_call_for_safe_recovery() -> None:
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
    assert events[-1].message.response_id is None
    assert events[-1].message.diagnostics == [
        {
            "code": "malformed_streamed_tool_call_arguments",
            "tool_names": ["write"],
            "finish_reason": "tool_calls",
        }
    ]
    tool_calls = [block for block in events[-1].message.content if isinstance(block, ToolCall)]
    assert [(call.name, call.arguments) for call in tool_calls] == [
        ("write", {"path": "BROKEN.md"})
    ]

def test_parse_sse_chunks_retains_incomplete_tool_call_without_finish_reason_for_safe_recovery() -> None:
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
    assert events[-1].message.response_id is None
    assert events[-1].message.diagnostics == [
        {
            "code": "partial_stream_tool_calls",
            "tool_names": ["write"],
            "finish_reason": None,
        }
    ]
    tool_calls = [block for block in events[-1].message.content if isinstance(block, ToolCall)]
    assert [(call.name, call.arguments) for call in tool_calls] == [
        ("write", {"path": "x.md"})
    ]
