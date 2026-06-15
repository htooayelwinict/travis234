from __future__ import annotations

from copy import deepcopy
from typing import Any

from appv22.context.budget import estimate_chars
from appv22.context.summaries import structured_summary


class AgentContextCompressor:
    def __init__(self, *, max_chars: int, threshold: float = 0.50) -> None:
        self.max_chars = max_chars
        self.threshold = threshold

    def compress(
        self,
        messages: list[dict[str, Any]],
        *,
        previous_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        copied = deepcopy(messages)
        if estimate_chars(copied) <= int(self.max_chars * self.threshold):
            return copied
        if not copied:
            return copied

        head = copied[:1]
        tail = copied[-1:] if len(copied) > 1 else []
        middle = copied[1:-1] if len(copied) > 1 else []
        for message in middle:
            if message.get("role") == "tool" and len(str(message.get("content", ""))) > 1000:
                message["content"] = f"[pruned verbose tool result:{message.get('tool_result_id', 'unknown')}]"

        return [
            *head,
            {
                "role": "system",
                "name": "context_summary",
                "content": "Structured context summary injected.",
                "summary": structured_summary(middle, deepcopy(previous_summary)),
            },
            *tail,
        ]
