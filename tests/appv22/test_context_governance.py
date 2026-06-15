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
    assert guarded[1]["name"] == "context_guard_compaction"
    assert guarded[1]["compaction"]["fallback"] == "last_resort"
    assert guarded[-1]["content"] == "u" * 5000


def test_gateway_guard_does_not_mutate_input_messages() -> None:
    messages = [
        {"role": "system", "content": "s"},
        {"role": "tool", "tool_result_id": "toolres_old", "content": "x" * 9000},
        {"role": "user", "content": "continue"},
    ]

    GatewayContextGuard(max_chars=10_000, threshold=0.85).guard(messages)

    assert messages[1]["content"] == "x" * 9000


def test_gateway_guard_compacts_oversized_middle_context_without_verbose_tools() -> None:
    messages = [
        {"role": "system", "content": "edge-start"},
        {"role": "user", "content": "middle user constraint: keep responses auditable " + "u" * 4000},
        {"role": "assistant", "content": "middle assistant rationale: preserve reversible state " + "a" * 4000},
        {"role": "tool", "tool_result_id": "toolres_small", "content": "short evidence"},
        {"role": "user", "content": "edge-end"},
    ]

    guarded = GatewayContextGuard(max_chars=1_000, threshold=0.85).guard(messages)

    assert guarded[0] == messages[0]
    assert guarded[-1] == messages[-1]
    assert guarded is not messages
    assert messages[1]["content"].startswith("middle user constraint")
    assert estimate_chars(guarded) <= 1_000
    assert any(message.get("name") == "context_guard_compaction" for message in guarded[1:-1])


def test_gateway_guard_returns_minimal_compacted_output_when_rich_compaction_is_oversized() -> None:
    messages = [
        {"role": "system", "content": "edge-start"},
        {"role": "user", "content": "constraint: " + "u" * 600},
        {"role": "assistant", "content": "rationale: " + "a" * 600},
        {"role": "user", "content": "edge-end"},
    ]

    guarded = GatewayContextGuard(max_chars=350, threshold=0.85).guard(messages)

    assert guarded[0] == messages[0]
    assert guarded[-1] == messages[-1]
    assert len(guarded) == 3
    assert guarded[1]["name"] == "context_guard_compaction"
    assert guarded[1]["content"] == "Middle context compacted by GatewayContextGuard: 2 messages."
    assert estimate_chars(guarded) <= 350


def test_gateway_guard_last_resort_does_not_return_oversized_original_middle_context() -> None:
    messages = [
        {"role": "system", "content": "s" * 300},
        {"role": "user", "content": "middle user constraint: " + "u" * 4000},
        {"role": "assistant", "content": "middle assistant rationale: " + "a" * 4000},
        {"role": "user", "content": "u" * 300},
    ]

    guarded = GatewayContextGuard(max_chars=400, threshold=0.85).guard(messages)

    assert guarded[0] == messages[0]
    assert guarded[-1] == messages[-1]
    assert len(guarded) == 3
    assert guarded[1]["name"] == "context_guard_compaction"
    assert guarded[1]["compaction"]["fallback"] == "last_resort"
    assert "middle user constraint" not in str(guarded)
    assert "middle assistant rationale" not in str(guarded)


def test_gateway_guard_last_resort_after_verbose_tool_pruning_drops_original_middle_context() -> None:
    messages = [
        {"role": "system", "content": "s" * 300},
        {"role": "tool", "tool_result_id": "toolres_verbose", "content": "x" * 5000},
        {"role": "user", "content": "middle non-tool constraint: " + "u" * 4000},
        {"role": "assistant", "content": "middle non-tool rationale: " + "a" * 4000},
        {"role": "user", "content": "u" * 300},
    ]

    guarded = GatewayContextGuard(max_chars=400, threshold=0.85).guard(messages)

    assert guarded[0] == messages[0]
    assert guarded[-1] == messages[-1]
    assert len(guarded) == 3
    assert guarded[1]["name"] == "context_guard_compaction"
    assert guarded[1]["compaction"]["fallback"] == "last_resort"
    assert "middle non-tool constraint" not in str(guarded)
    assert "middle non-tool rationale" not in str(guarded)
    assert "[pruned verbose tool result:toolres_verbose]" not in str(guarded)


def test_structured_summary_merges_previous_summary_and_evidence_refs() -> None:
    summary = structured_summary(
        [
            {"role": "assistant", "content": "decision: observe"},
            {"role": "tool", "tool_result_id": "toolres_1", "content": "result"},
        ],
        {
            "goals": ["existing goal"],
            "decisions": ["decision: prior", "decision: observe"],
            "progress": ["done"],
            "open_risks": ["risk"],
            "evidence_refs": ["toolres_0", "toolres_1"],
        },
    )

    assert summary == {
        "goals": ["existing goal"],
        "decisions": ["decision: prior", "decision: observe"],
        "progress": ["done", "toolres_1: result"],
        "open_risks": ["risk"],
        "evidence_refs": ["toolres_0", "toolres_1"],
    }


def test_structured_summary_preserves_middle_constraints_and_assistant_context() -> None:
    summary = structured_summary(
        [
            {"role": "user", "content": "constraint: preserve Task 4 threshold semantics"},
            {"role": "assistant", "content": "rationale: keep first and last messages intact"},
            {"role": "tool", "tool_result_id": "toolres_small", "content": "short useful evidence"},
        ],
        {"goals": ["ship context governance hardening"]},
    )

    assert set(summary) == {"goals", "decisions", "progress", "open_risks", "evidence_refs"}
    assert "constraint: preserve Task 4 threshold semantics" in summary["goals"]
    assert "rationale: keep first and last messages intact" in summary["progress"]
    assert "toolres_small: short useful evidence" in summary["progress"]
    assert summary["evidence_refs"] == ["toolres_small"]


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


def test_agent_compressor_preserves_empty_tool_skill_observation_contract() -> None:
    observation_contract = {
        "evidence_refs": ("world://web_research/latest",),
        "evidence_kinds": ("web_research.search",),
        "preferred_tool_id": "web_research.search",
    }
    messages = [
        {
            "role": "system",
            "name": "provider_identity",
            "content": "x",
            "payload": {"identity": "x"},
        },
        {
            "role": "system",
            "name": "provider_context_section",
            "section": "skills",
            "content": "skills: oversized",
            "payload": [
                {
                    "skill_id": "web_research.search",
                    "extension_id": "web_research",
                    "summary": "Search the web",
                    "tool_ids": (),
                    "observation_contract": observation_contract,
                }
            ],
        },
        {"role": "user", "name": "user_goal", "content": "research this"},
    ]

    compacted = AgentContextCompressor(max_chars=900, threshold=0.1).compress(
        messages, previous_summary={}
    )

    skills_message = next(message for message in compacted if message.get("section") == "skills")
    assert skills_message["payload"][0]["skill_id"] == "web_research.search"
    assert skills_message["payload"][0]["tool_ids"] == ()
    assert skills_message["payload"][0]["observation_contract"] == observation_contract


def test_agent_compressor_summary_preserves_middle_constraints_and_notes() -> None:
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "instruction: never mutate caller-owned messages"},
        {"role": "assistant", "content": "rationale: summary must retain audit context"},
        {"role": "tool", "tool_result_id": "toolres_small", "content": "non-verbose evidence"},
        {"role": "assistant", "content": "background filler " * 100},
        {"role": "user", "content": "continue"},
    ]

    compacted = AgentContextCompressor(max_chars=1_600, threshold=0.50).compress(messages, previous_summary={})
    summary = compacted[1]["summary"]

    assert set(summary) == {"goals", "decisions", "progress", "open_risks", "evidence_refs"}
    assert "instruction: never mutate caller-owned messages" in summary["goals"]
    assert "rationale: summary must retain audit context" in summary["progress"]
    assert "toolres_small: non-verbose evidence" in summary["progress"]


def test_agent_compressor_shrinks_oversized_summary_to_budget() -> None:
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "instruction: " + "u" * 1000},
        {"role": "assistant", "content": "rationale: " + "a" * 1000},
        {"role": "tool", "tool_result_id": "toolres_small", "content": "e" * 1000},
        {"role": "user", "content": "continue"},
    ]
    previous_summary = {
        "goals": ["g" * 1000],
        "decisions": ["d" * 1000],
        "progress": ["p" * 1000],
        "open_risks": ["r" * 1000],
        "evidence_refs": ["toolres_previous"],
    }

    compacted = AgentContextCompressor(max_chars=900, threshold=0.50).compress(
        messages, previous_summary=previous_summary
    )

    assert compacted[0] == messages[0]
    assert compacted[-1] == messages[-1]
    assert compacted[1]["name"] == "context_summary"
    assert set(compacted[1]["summary"]) == {"goals", "decisions", "progress", "open_risks", "evidence_refs"}
    assert estimate_chars(compacted) <= 450
    assert messages[1]["content"] == "instruction: " + "u" * 1000
    assert previous_summary["goals"] == ["g" * 1000]


def test_agent_compressor_bounded_summary_prefers_recent_facts_over_older_previous_summary() -> None:
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "constraint: preserve current hard budget evidence"},
        {"role": "tool", "tool_result_id": "toolres_current", "content": "current evidence survives"},
        {"role": "assistant", "content": "background filler " * 200},
        {"role": "user", "content": "continue"},
    ]
    previous_summary = {
        "goals": [
            *[f"older previous goal {index}" for index in range(12)],
        ],
        "decisions": [],
        "progress": [
            *[f"older previous progress {index}" for index in range(12)],
        ],
        "open_risks": [],
        "evidence_refs": [
            *[f"toolres_old_{index}" for index in range(12)],
        ],
    }

    compacted = AgentContextCompressor(max_chars=1_800, threshold=0.50).compress(
        messages, previous_summary=previous_summary
    )
    summary = compacted[1]["summary"]

    assert set(summary) == {"goals", "decisions", "progress", "open_risks", "evidence_refs"}
    assert "constraint: preserve current hard budget evidence" in summary["goals"]
    assert "toolres_current: current evidence survives" in summary["progress"]
    assert "toolres_current" in summary["evidence_refs"]
    assert "older previous goal 0" not in summary["goals"]
    assert "older previous progress 0" not in summary["progress"]
    assert "toolres_old_0" not in summary["evidence_refs"]


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
