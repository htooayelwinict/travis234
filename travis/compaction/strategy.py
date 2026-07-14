"""Preparation policy for built-in compaction and optional extension interception."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from travis.ai.types import Message
from travis.compaction.compressor import ContextCompressor, estimate_tokens


@dataclass(frozen=True)
class CompactionPreparation:
    messages_to_summarize: list[Message]
    turn_prefix_messages: list[Message]
    previous_summary: str | None
    read_files: list[str]
    modified_files: list[str]
    tokens_before: int
    first_kept_entry_id: str
    context_window: int
    threshold_tokens: int

    def as_extension_event(self) -> dict[str, object]:
        return {
            "messagesToSummarize": list(self.messages_to_summarize),
            "turnPrefixMessages": list(self.turn_prefix_messages),
            "previousSummary": self.previous_summary,
            "fileOps": {
                "readFiles": list(self.read_files),
                "modifiedFiles": list(self.modified_files),
            },
            "tokensBefore": self.tokens_before,
            "firstKeptEntryId": self.first_kept_entry_id,
            "settings": {
                "contextWindow": self.context_window,
                "threshold": self.threshold_tokens,
            },
        }


def prepare_compaction(
    messages: Sequence[Message],
    compressor: ContextCompressor,
    context_entry_ids: Sequence[str],
    *,
    deep: bool = False,
) -> CompactionPreparation:
    source = list(messages)
    pruned = compressor.prune_old_tool_results(source)
    # Extension hooks must receive the same built-in durable boundary as the
    # built-in implementation: summarize every discarded message and choose
    # the kept suffix from the raw entries that persistence will restore.
    head_end = 0
    tail_start = compressor._find_tail_start(  # noqa: SLF001
        source,
        head_end,
        deep=deep,
        preserve_role_anchors=True,
        preserve_summary_anchor=False,
    )
    if tail_start <= head_end:
        emergency = compressor._oversized_protected_head_window(  # noqa: SLF001
            pruned,
            head_end,
            estimate_tokens(pruned),
            force=True,
        )
        if emergency is not None:
            head_end, tail_start = emergency

    middle = pruned[head_end:tail_start] if tail_start > head_end else []
    summary_index, previous_summary = compressor._find_latest_context_summary(  # noqa: SLF001
        pruned,
        0,
        tail_start,
    )
    if summary_index is not None:
        middle = pruned[max(head_end, summary_index + 1) : tail_start]
    if not previous_summary:
        previous_summary = getattr(compressor, "_previous_summary", None)
    read_files, modified_files = compressor._file_operations_for_summary(middle)  # noqa: SLF001
    first_kept_entry_id = (
        str(context_entry_ids[tail_start])
        if 0 <= tail_start < len(context_entry_ids)
        else ""
    )
    return CompactionPreparation(
        messages_to_summarize=middle,
        turn_prefix_messages=[],
        previous_summary=previous_summary or None,
        read_files=read_files,
        modified_files=modified_files,
        tokens_before=estimate_tokens(source),
        first_kept_entry_id=first_kept_entry_id,
        context_window=compressor.context_length,
        threshold_tokens=compressor.threshold_tokens,
    )


__all__ = ["CompactionPreparation", "prepare_compaction"]
