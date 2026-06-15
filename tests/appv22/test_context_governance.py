from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.2"))

from appv22.context.budget import estimate_chars
from appv22.context.compressor import AgentContextCompressor
from appv22.context.gateway_guard import GatewayContextGuard
from appv22.context.summaries import structured_summary


def test_estimate_chars_uses_stable_json_shape() -> None:
    assert estimate_chars({"b": 1, "a": ["x"]}) == estimate_chars({"a": ["x"], "b": 1})


def test_gateway_guard_prunes_verbose_tool_payloads_at_85_percent() -> None:
    messages = [
        {"role": "system", "content": "s"},
        {"role": "tool", "tool_result_id": "toolres_old", "content": "x" * 9000},
        {"role": "user", "content": "continue"},
    ]

    guarded = GatewayContextGuard(max_chars=10_000, threshold=0.85).guard(messages)

    assert guarded[1]["content"] == "[pruned verbose tool result:toolres_old]"
    assert guarded[0]["content"] == "s"
    assert guarded[-1]["content"] == "continue"


def test_gateway_guard_does_not_prune_below_threshold() -> None:
    messages = [
        {"role": "system", "content": "s"},
        {"role": "tool", "tool_result_id": "toolres_small", "content": "x" * 1200},
        {"role": "user", "content": "continue"},
    ]

    guarded = GatewayContextGuard(max_chars=10_000, threshold=0.85).guard(messages)

    assert guarded == messages
    assert guarded is not messages
    assert guarded[1] is not messages[1]


def test_gateway_guard_preserves_system_and_user_edge_messages_even_when_verbose() -> None:
    messages = [
        {"role": "system", "content": "s" * 5000},
        {"role": "tool", "tool_result_id": "toolres_old", "content": "x" * 9000},
        {"role": "user", "content": "u" * 5000},
    ]

    guarded = GatewayContextGuard(max_chars=10_000, threshold=0.85).guard(messages)

    assert guarded[0]["content"] == "s" * 5000
    assert guarded[1]["content"] == "[pruned verbose tool result:toolres_old]"
    assert guarded[-1]["content"] == "u" * 5000


def test_gateway_guard_does_not_mutate_input_messages() -> None:
    messages = [
        {"role": "system", "content": "s"},
        {"role": "tool", "tool_result_id": "toolres_old", "content": "x" * 9000},
        {"role": "user", "content": "continue"},
    ]

    GatewayContextGuard(max_chars=10_000, threshold=0.85).guard(messages)

    assert messages[1]["content"] == "x" * 9000


def test_structured_summary_merges_previous_summary_and_evidence_refs() -> None:
    summary = structured_summary(
        [
            {"role": "assistant", "content": "decision: observe"},
            {"role": "tool", "tool_result_id": "toolres_1", "content": "result"},
        ],
        {"goals": ["existing goal"], "progress": ["done"], "open_risks": ["risk"]},
    )

    assert summary == {
        "goals": ["existing goal"],
        "decisions": ["decision: observe"],
        "progress": ["done"],
        "open_risks": ["risk"],
        "evidence_refs": ["toolres_1"],
    }


def test_agent_compressor_emits_structured_summary() -> None:
    messages = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "decision: observe"},
        {"role": "tool", "tool_result_id": "toolres_1", "content": "x" * 5000},
        {"role": "user", "content": "continue"},
    ]

    compacted = AgentContextCompressor(max_chars=8_000, threshold=0.50).compress(messages, previous_summary={})

    assert compacted[0]["role"] == "system"
    assert compacted[1]["name"] == "context_summary"
    assert set(compacted[1]["summary"]) == {"goals", "decisions", "progress", "open_risks", "evidence_refs"}
    assert compacted[-1]["content"] == "continue"


def test_agent_compressor_does_not_compress_below_threshold() -> None:
    messages = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "decision: observe"},
        {"role": "user", "content": "continue"},
    ]

    compacted = AgentContextCompressor(max_chars=8_000, threshold=0.50).compress(messages, previous_summary={})

    assert compacted == messages
    assert compacted is not messages
    assert compacted[1] is not messages[1]


def test_agent_compressor_does_not_mutate_input_messages_or_previous_summary() -> None:
    messages = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "decision: observe"},
        {"role": "tool", "tool_result_id": "toolres_1", "content": "x" * 5000},
        {"role": "user", "content": "continue"},
    ]
    previous_summary = {"goals": ["original"], "progress": []}

    compacted = AgentContextCompressor(max_chars=8_000, threshold=0.50).compress(
        messages, previous_summary=previous_summary
    )
    compacted[1]["summary"]["goals"].append("mutated")

    assert messages[2]["content"] == "x" * 5000
    assert previous_summary == {"goals": ["original"], "progress": []}
