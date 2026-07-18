"""Content adaptation for extension-originated user messages."""

from __future__ import annotations

from collections.abc import Callable

from travis.agent.types import AgentMessage
from travis.ai.types import ImageContent, TextContent


def send_extension_user_message(
    prompt: Callable[..., list[AgentMessage] | None],
    content: str | list[TextContent | ImageContent],
    options: dict | None = None,
) -> list[AgentMessage] | None:
    """Preserve image blocks while bypassing user-only prompt expansion."""

    options = options or {}
    if isinstance(content, str):
        text = content
        images: list[ImageContent] | None = None
    else:
        text = "\n".join(part.text for part in content if isinstance(part, TextContent))
        selected_images = [part for part in content if isinstance(part, ImageContent)]
        images = selected_images or None
    deliver_as = options.get("deliverAs", options.get("deliver_as"))
    return prompt(
        text,
        streaming_behavior=str(deliver_as) if deliver_as is not None else None,
        images=images,
        expand_prompt_templates=False,
        input_source="extension",
    )


__all__ = ("send_extension_user_message",)
