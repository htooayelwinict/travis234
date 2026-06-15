from __future__ import annotations

from copy import deepcopy
from typing import Any

from appv22.context.budget import estimate_chars


class GatewayContextGuard:
    def __init__(self, *, max_chars: int, threshold: float = 0.85) -> None:
        self.max_chars = max_chars
        self.threshold = threshold

    def guard(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        guarded = deepcopy(messages)
        if estimate_chars(guarded) <= int(self.max_chars * self.threshold):
            return guarded

        for message in guarded[1:-1]:
            if message.get("role") == "tool" and len(str(message.get("content", ""))) > 1000:
                message["content"] = f"[pruned verbose tool result:{message.get('tool_result_id', 'unknown')}]"
        return guarded
