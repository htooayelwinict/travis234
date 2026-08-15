"""Safety shaping for explicit memory content and recall output."""

from __future__ import annotations

import re
from collections.abc import Iterable

from travis.coding_agent.eval_trace import SecretRedactor
from travis.coding_agent.memory.types import MemoryFact


UNTRUSTED_MEMORY_START = "[Untrusted memory data]"
UNTRUSTED_MEMORY_END = "[/Untrusted memory data]"
_CREDENTIAL_SHAPE = re.compile(
    r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|"
    r"private[_-]?key|credential|cookie)\s*[:=]\s*\S{8,}",
    re.IGNORECASE,
)


def contains_sensitive_memory(content: str, redactor: SecretRedactor) -> bool:
    return (
        redactor.contains_secret(content)
        or redactor.redact_text(content) != content
        or _CREDENTIAL_SHAPE.search(content) is not None
    )


def render_untrusted_facts(facts: Iterable[MemoryFact]) -> str:
    blocks: list[str] = []
    for fact in facts:
        tags = ", ".join(fact.tags) if fact.tags else "-"
        blocks.append(
            "\n".join(
                (
                    UNTRUSTED_MEMORY_START,
                    f"id={fact.memory_id} scope={fact.scope} tags={tags}",
                    fact.content,
                    UNTRUSTED_MEMORY_END,
                )
            )
        )
    return "\n\n".join(blocks)


__all__ = [
    "UNTRUSTED_MEMORY_END",
    "UNTRUSTED_MEMORY_START",
    "contains_sensitive_memory",
    "render_untrusted_facts",
]
