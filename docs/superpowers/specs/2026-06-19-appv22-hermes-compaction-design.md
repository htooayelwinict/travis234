# Sub-projects 4 & 5: hermes dual-pass + timing compaction Design

Date: 2026-06-19
Status: Implemented
Parent: `2026-06-19-appv22-pi-hermes-parity-decomposition.md`
Reference: `hermes-agent/agent/context_compressor.py`, `conversation_compression.py`,
`turn_context.py`, `conversation_loop.py`

## Goal

Port hermes's compaction design into `appV2.2/appv22/compaction/` operating on
`appv22.ai` `Message` objects (no external imports).

## 4: Dual-pass (`compaction/compressor.py`)

`ContextCompressor.compress` runs two passes (port of hermes `ContextCompressor`):

- **Pass 1 `prune_old_tool_results`** (deterministic, no LLM): dedup identical
  tool outputs (keep newest), summarize old tool results before the protected
  tail to a 1-line note, strip non-text blocks, truncate huge tool-call arguments.
- **Pass 2 `generate_summary`** (LLM via injected `summarizer`): serialize the
  token-budgeted "middle" window into a structured template (`## Goal`,
  `## Completed Actions`, `## Active State`, `## Key Decisions`, `## Relevant
  Files`, `## Remaining Work`); **iterative-update** prompt when `_previous_summary`
  exists, else **from-scratch**. Deterministic static fallback when no summarizer.
- Head (`protect_first_n`) + token-budgeted tail (`_find_tail_start`, min
  `protect_last_n`) protected; middle replaced by one `SUMMARY_PREFIX` user message.
- **Anti-thrash**: `should_compress` returns False below `threshold_tokens` or after
  two consecutive <10%-effective passes (`_ineffective_compression_count`).

## 5: Timing (`compaction/timing.py`)

`CompactionManager` wires the compressor into the four hermes trigger phases:

1. **preflight** `maybe_compress_preflight` — rough `estimate_tokens`; defers when
   `awaiting_real_usage_after_compression` (avoids thrash right after a compaction).
2. **post-response** `maybe_compress_post_response(prompt_tokens)` — real provider
   prompt tokens; `-1` sentinel ("just compacted") treated as 0.
3. **overflow recovery** `recover_overflow` — `force=True`, bounded by
   `max_overflow_attempts`.
4. **manual** `compress_manual` — `force=True`, clears the summary-failure cooldown.

Summary-failure **cooldown** (`SUMMARY_FAILURE_COOLDOWN_SECONDS`, injectable clock)
suppresses auto-compaction after a summarizer exception; `force` bypasses it.

`SessionLineage` mints rotated session ids with `parent_session_id` chaining and
`end_reason="compression"` (port of the hermes session-rotation lineage).

## Integration

Standalone + tested. Wiring into the agent loop is via `transform_context` (the
preflight phase) and an overflow handler around `stream_simple`; that wiring lands
with the runtime swap in the capstone integration.

## Tests

`tests/test_compaction.py` (6): prune dedup/summarize/arg-truncation, should_compress
+ anti-thrash, head/summary/tail assembly, iterative update, token estimate.
`tests/test_compaction_timing.py` (6): preflight + defer, post-response sentinel,
real-token compress, overflow force + bounded, manual force clears cooldown,
session lineage rotation.
