from __future__ import annotations

import pytest

from travis.compaction.policy import (
    CompactionPolicyInput,
    calculate_compaction_budget,
)
from travis.compaction.compressor import ContextCompressor


@pytest.mark.parametrize(
    ("context_window", "max_output", "expected_ratio"),
    [
        (128_000, 8_192, 0.75),
        (256_000, 8_192, 0.75),
        (1_048_576, 8_192, 0.50),
    ],
)
def test_hermes_threshold_bands(
    context_window: int,
    max_output: int,
    expected_ratio: float,
) -> None:
    budget = calculate_compaction_budget(
        CompactionPolicyInput(
            context_window=context_window,
            max_output_tokens=max_output,
        )
    )

    effective = context_window - max_output
    assert budget.trigger_tokens == int(effective * expected_ratio)
    assert budget.tail_target_tokens == int(budget.trigger_tokens * 0.20)
    assert budget.tail_soft_ceiling_tokens == int(budget.tail_target_tokens * 1.5)
    assert budget.threshold_ratio == expected_ratio


def test_below_64k_route_uses_reachable_small_window_fallback() -> None:
    budget = calculate_compaction_budget(
        CompactionPolicyInput(context_window=32_000, max_output_tokens=4_096)
    )

    assert budget.effective_input_tokens == 27_904
    assert budget.trigger_tokens == int(27_904 * 0.85)
    assert 0 < budget.trigger_tokens < budget.effective_input_tokens
    assert budget.reason == "small_window_fallback"


def test_invalid_output_reservation_falls_back_to_full_window() -> None:
    budget = calculate_compaction_budget(
        CompactionPolicyInput(context_window=128_000, max_output_tokens=128_000)
    )

    assert budget.effective_input_tokens == 128_000
    assert budget.trigger_tokens == int(128_000 * 0.75)


def test_threshold_and_summary_ratios_are_safely_clamped() -> None:
    budget = calculate_compaction_budget(
        CompactionPolicyInput(
            context_window=1_048_576,
            threshold_ratio=2.0,
            summary_target_ratio=0.01,
        )
    )

    assert budget.threshold_ratio == 0.95
    assert budget.tail_target_tokens == int(budget.trigger_tokens * 0.10)


def test_smaller_aux_model_lowers_trigger_before_overflow() -> None:
    budget = calculate_compaction_budget(
        CompactionPolicyInput(
            context_window=1_048_576,
            max_output_tokens=8_192,
            summarizer_context_window=128_000,
        )
    )

    assert budget.trigger_tokens < 128_000
    assert budget.reason == "auxiliary_model_capacity"


def test_policy_rejects_non_positive_context_windows() -> None:
    with pytest.raises(ValueError, match="context_window"):
        calculate_compaction_budget(CompactionPolicyInput(context_window=0))


def test_compressor_recalculates_budget_without_discarding_summary_history() -> None:
    compressor = ContextCompressor(
        context_length=1_048_576,
        max_tokens=8_192,
    )
    compressor._previous_summary = "durable handoff"  # noqa: SLF001 - lifecycle regression
    compressor.compression_count = 2

    compressor.update_context_window(
        256_000,
        max_tokens=8_192,
        model="openrouter/example/small",
    )

    assert compressor.active_budget.trigger_tokens == int((256_000 - 8_192) * 0.75)
    assert compressor.threshold_tokens == compressor.active_budget.trigger_tokens
    assert compressor.tail_token_budget == compressor.active_budget.tail_target_tokens
    assert compressor.max_summary_tokens == compressor.active_budget.summary_max_tokens
    assert compressor._previous_summary == "durable handoff"  # noqa: SLF001
    assert compressor.compression_count == 2
