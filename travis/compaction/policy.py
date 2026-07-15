"""Pure context-compaction budget calculation.

The threshold bands mirror the pinned Hermes behavior while keeping route
capacity decisions independent from transcript rewriting and persistence.
"""

from __future__ import annotations

from dataclasses import dataclass

MINIMUM_CONTEXT_TOKENS = 64_000
SMALL_CONTEXT_WINDOW_LIMIT = 512_000
SMALL_CONTEXT_THRESHOLD_RATIO = 0.75
MINIMUM_WINDOW_TRIGGER_RATIO = 0.85
SUMMARY_TOKENS_CEILING = 10_000
SUMMARY_PROMPT_RESERVE_TOKENS = 4_096


@dataclass(frozen=True)
class CompactionPolicyInput:
    context_window: int
    max_output_tokens: int = 0
    model_id: str = ""
    threshold_ratio: float = 0.50
    summary_target_ratio: float = 0.20
    summarizer_context_window: int | None = None
    summarizer_max_output_tokens: int = 0


@dataclass(frozen=True)
class CompactionBudget:
    effective_input_tokens: int
    trigger_tokens: int
    tail_target_tokens: int
    tail_soft_ceiling_tokens: int
    summary_max_tokens: int
    threshold_ratio: float
    reason: str


def calculate_compaction_budget(policy_input: CompactionPolicyInput) -> CompactionBudget:
    """Calculate a reachable, output-reserved compaction budget."""

    context_window = int(policy_input.context_window)
    if context_window <= 0:
        raise ValueError("context_window must be positive")

    max_output = _positive_int(policy_input.max_output_tokens)
    effective_input = context_window - max_output
    if effective_input <= 0:
        effective_input = context_window

    configured_ratio = _clamp(float(policy_input.threshold_ratio), 0.10, 0.95)
    threshold_ratio = configured_ratio
    reason = "configured_threshold"
    if context_window < SMALL_CONTEXT_WINDOW_LIMIT:
        threshold_ratio = max(threshold_ratio, SMALL_CONTEXT_THRESHOLD_RATIO)
        if threshold_ratio != configured_ratio:
            reason = "small_context_floor"

    percentage_trigger = int(effective_input * threshold_ratio)
    trigger = max(percentage_trigger, MINIMUM_CONTEXT_TOKENS)
    if trigger >= effective_input:
        trigger = max(
            1,
            min(int(effective_input * MINIMUM_WINDOW_TRIGGER_RATIO), effective_input - 1),
        )
        reason = "small_window_fallback"

    summary_max = max(1, min(int(context_window * 0.05), SUMMARY_TOKENS_CEILING))
    summarizer_window = _positive_int(policy_input.summarizer_context_window)
    if summarizer_window:
        summarizer_output = _positive_int(policy_input.summarizer_max_output_tokens) or summary_max
        maximum_summary_input = max(
            1,
            summarizer_window - summarizer_output - SUMMARY_PROMPT_RESERVE_TOKENS,
        )
        if maximum_summary_input < trigger:
            trigger = maximum_summary_input
            reason = "auxiliary_model_capacity"

    summary_ratio = _clamp(float(policy_input.summary_target_ratio), 0.10, 0.80)
    tail_target = max(1, int(trigger * summary_ratio))
    tail_soft_ceiling = max(tail_target, int(tail_target * 1.5))

    return CompactionBudget(
        effective_input_tokens=effective_input,
        trigger_tokens=trigger,
        tail_target_tokens=tail_target,
        tail_soft_ceiling_tokens=tail_soft_ceiling,
        summary_max_tokens=summary_max,
        threshold_ratio=threshold_ratio,
        reason=reason,
    )


def _positive_int(value: object) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


__all__ = [
    "CompactionBudget",
    "CompactionPolicyInput",
    "calculate_compaction_budget",
]
