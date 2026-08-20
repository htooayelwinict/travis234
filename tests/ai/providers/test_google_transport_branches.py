"""Direct branch characterizations for Google request translation."""

from __future__ import annotations

from travis.ai.providers.transport_families.google import _google_contents
from travis.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    TextContent,
    ThinkingContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
)


def _model(*, model_id: str = "gemini-3-flash") -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="google-generative-ai",
        provider="google",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        reasoning=True,
        input=["text", "image"],
    )


def _assistant(
    content: list[TextContent | ThinkingContent | ToolCall],
    model: Model,
    *,
    provider: str | None = None,
) -> AssistantMessage:
    return AssistantMessage(
        content=content,
        api=model.api,
        provider=provider or model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="toolUse",
    )


def test_google_contents_preserve_all_roles_signatures_and_modern_tool_images() -> None:
    model = _model()
    assistant = _assistant(
        [
            TextContent(text="answer", text_signature="dGV4dA=="),
            ThinkingContent(thinking="reason", thinking_signature="dGhpbms="),
            ToolCall(
                id="call-1",
                name="look",
                arguments={"path": "a.png"},
                thought_signature="dG9vbA==",
            ),
        ],
        model,
    )
    image_result = ToolResultMessage(
        tool_call_id="call-1",
        tool_name="look",
        content=[
            TextContent(text="caption"),
            ImageContent(data="aW1hZ2U=", mime_type="image/png"),
        ],
        is_error=False,
    )
    error_result = ToolResultMessage(
        tool_call_id="call-2",
        tool_name="read",
        content=[TextContent(text="failed")],
        is_error=True,
    )
    context = Context(
        messages=[
            UserMessage(content="work"),
            UserMessage(
                content=[
                    TextContent(text="look"),
                    ImageContent(data="aW1hZ2U=", mime_type="image/png"),
                ]
            ),
            assistant,
            image_result,
            error_result,
        ]
    )

    contents = _google_contents(context, model)

    assert contents == [
        {"role": "user", "parts": [{"text": "work"}]},
        {
            "role": "user",
            "parts": [
                {"text": "look"},
                {"inlineData": {"mimeType": "image/png", "data": "aW1hZ2U="}},
            ],
        },
        {
            "role": "model",
            "parts": [
                {"text": "answer", "thoughtSignature": "dGV4dA=="},
                {
                    "text": "reason",
                    "thought": True,
                    "thoughtSignature": "dGhpbms=",
                },
                {
                    "functionCall": {"name": "look", "args": {"path": "a.png"}},
                    "thoughtSignature": "dG9vbA==",
                },
            ],
        },
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "look",
                        "response": {"output": "caption"},
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": "aW1hZ2U=",
                                }
                            }
                        ],
                    }
                },
                {
                    "functionResponse": {
                        "name": "read",
                        "response": {"error": "failed"},
                    }
                },
            ],
        },
    ]


def test_foreign_assistant_drops_native_signature_metadata() -> None:
    model = _model()
    assistant = _assistant(
        [
            TextContent(text="answer", text_signature="dGV4dA=="),
            ThinkingContent(thinking="reason", thinking_signature="dGhpbms="),
            ToolCall(
                id="call-1",
                name="read",
                arguments={},
                thought_signature="dG9vbA==",
            ),
        ],
        model,
        provider="foreign",
    )
    result = ToolResultMessage(
        tool_call_id="call-1",
        tool_name="read",
        content=[TextContent(text="done")],
        is_error=False,
    )

    contents = _google_contents(Context(messages=[assistant, result]), model)

    assert contents[0] == {
        "role": "model",
        "parts": [
            {"text": "answer"},
            {"text": "reason"},
            {"functionCall": {"name": "read", "args": {}}},
        ],
    }


def test_required_tool_ids_are_normalized_and_replayed_on_results() -> None:
    model = _model(model_id="claude-sonnet")
    assistant = _assistant(
        [ToolCall(id="call|invalid:id", name="read", arguments={})],
        model,
        provider="foreign",
    )
    result = ToolResultMessage(
        tool_call_id="call|invalid:id",
        tool_name="read",
        content=[],
        is_error=False,
    )

    contents = _google_contents(Context(messages=[assistant, result]), model)

    assert contents == [
        {
            "role": "model",
            "parts": [
                {
                    "functionCall": {
                        "name": "read",
                        "args": {},
                        "id": "call_invalid_id",
                    }
                }
            ],
        },
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "read",
                        "response": {"output": ""},
                        "id": "call_invalid_id",
                    }
                }
            ],
        },
    ]


def test_legacy_model_places_tool_images_in_followup_user_content() -> None:
    model = _model(model_id="gemini-2.5-flash")
    result = ToolResultMessage(
        tool_call_id="call-1",
        tool_name="look",
        content=[ImageContent(data="aW1hZ2U=", mime_type="image/png")],
        is_error=False,
    )

    contents = _google_contents(Context(messages=[result]), model)

    assert contents == [
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "look",
                        "response": {"output": "(see attached image)"},
                    }
                }
            ],
        },
        {
            "role": "user",
            "parts": [
                {"text": "Tool result image:"},
                {"inlineData": {"mimeType": "image/png", "data": "aW1hZ2U="}},
            ],
        },
    ]
