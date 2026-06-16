from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "appV2.2"))

from appv22.context.compressor import AgentContextCompressor


def test_dual_compaction_preserves_skill_prompt_instructions():
    messages = [
        {"role": "system", "content": "runtime contract"},
        {
            "role": "system",
            "name": "provider_context_section",
            "section": "skills",
            "payload": [
                {
                    "skill_id": "demo.web_research",
                    "extension_id": "demo",
                    "summary": "Research public sources.",
                    "tool_ids": ("demo.search",),
                    "observation_contract": {"evidence_refs": ("world://search/latest",)},
                    "instructions": (
                        "Use the skill prompt as the domain adapter.",
                        "Rehydrate exact evidence before final claims.",
                    ),
                }
            ],
            "content": "skills: verbose",
        },
        {"role": "tool", "content": "x" * 5000, "tool_result_id": "tool_1"},
        {"role": "user", "content": "continue"},
    ]

    compacted = AgentContextCompressor(max_chars=1600, threshold=0.10).compress(
        messages,
        previous_summary={},
    )

    skill_sections = [
        message for message in compacted
        if message.get("name") == "provider_context_section" and message.get("section") == "skills"
    ]
    assert skill_sections
    assert skill_sections[0]["payload"][0]["instructions"] == (
        "Use the skill prompt as the domain adapter.",
        "Rehydrate exact evidence before final claims.",
    )


def test_dual_compaction_preserves_world_evidence_refs():
    messages = [
        {"role": "system", "content": "runtime contract"},
        {
            "role": "system",
            "name": "provider_context_section",
            "section": "world",
            "payload": {
                "world_refs": {
                    "world://repo_snapshot/latest": {
                        "ref_id": "world://repo_snapshot/latest",
                        "kind": "file_management.repo_snapshot",
                        "summary": "repo snapshot with office evidence",
                        "payload": {
                            "files": ["docs/context.md"],
                            "text_previews": {"docs/context.md": "office lease hybrid vendor budget owner risk"},
                            "directories": ["docs"],
                            "errors": [],
                        },
                    }
                }
            },
            "content": "world: verbose",
        },
        {"role": "tool", "content": "x" * 5000, "tool_result_id": "tool_1"},
        {"role": "user", "content": "continue"},
    ]

    compacted = AgentContextCompressor(max_chars=1800, threshold=0.10).compress(
        messages,
        previous_summary={},
    )

    world_sections = [
        message for message in compacted
        if message.get("name") == "provider_context_section" and message.get("section") == "world"
    ]
    summaries = [message for message in compacted if message.get("name") == "context_summary"]
    assert world_sections
    assert world_sections[0]["payload"]["world_refs"]["world://repo_snapshot/latest"]["kind"] == (
        "file_management.repo_snapshot"
    )
    assert world_sections[0]["payload"]["world_refs"]["world://repo_snapshot/latest"]["payload"]["text_previews"] == {
        "docs/context.md": "office lease hybrid vendor budget owner risk"
    }
    assert summaries
    assert "world://repo_snapshot/latest" in summaries[0]["summary"]["evidence_refs"]
    assert "Available evidence_refs: world://repo_snapshot/latest" in summaries[0]["content"]
    assert "file_management.repo_snapshot" in summaries[0]["content"]


def test_dual_compaction_preserves_unknown_world_payload_generically():
    messages = [
        {"role": "system", "content": "runtime contract"},
        {
            "role": "system",
            "name": "provider_context_section",
            "section": "world",
            "payload": {
                "world_refs": {
                    "world://web_research.search/abc": {
                        "ref_id": "world://web_research.search/abc",
                        "kind": "web_research.search",
                        "summary": "search results for vendor risk",
                        "payload": {
                            "query": "vendor risk",
                            "results": [
                                {
                                    "title": "BadgeCo risk review",
                                    "url": "https://example.test/badgeco",
                                    "snippet": "BadgeCo missed delivery twice.",
                                }
                            ],
                        },
                    }
                }
            },
            "content": "world: verbose",
        },
        {"role": "tool", "content": "x" * 5000, "tool_result_id": "tool_1"},
        {"role": "user", "content": "continue"},
    ]

    compacted = AgentContextCompressor(max_chars=1800, threshold=0.10).compress(
        messages,
        previous_summary={},
    )

    world_section = next(
        message for message in compacted
        if message.get("name") == "provider_context_section" and message.get("section") == "world"
    )
    ref = world_section["payload"]["world_refs"]["world://web_research.search/abc"]
    assert ref["kind"] == "web_research.search"
    assert ref["payload"]["query"] == "vendor risk"
    assert "BadgeCo risk review" in str(ref["payload"])
