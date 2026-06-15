import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.1"))

from appv21.tools.definitions import ToolCategory, ToolDefinition, ToolResultEnvelope


def test_tool_definition_requires_name_category_and_schema() -> None:
    definition = ToolDefinition(
        name="repo_snapshot",
        category=ToolCategory.OBSERVE,
        argument_schema={"type": "object", "additionalProperties": False, "properties": {}},
        result_schema={"type": "object"},
        risk_level="low",
    )

    assert definition.name == "repo_snapshot"
    assert definition.category.value == "observe"


def test_tool_result_envelope_uses_payload_ref() -> None:
    envelope = ToolResultEnvelope(
        tool_result_id="toolres_1",
        tool_name="repo_snapshot",
        status="completed",
        trust="runtime_observed",
        payload_ref="world://tool_result/toolres_1",
        prompt_summary={"file_count": 1},
        evidence_refs=[],
        artifacts=[],
    )

    assert envelope.payload_ref == "world://tool_result/toolres_1"
    serialized = envelope.to_dict()
    assert serialized["payload_ref"] == "world://tool_result/toolres_1"

    serialized["prompt_summary"]["file_count"] = 99
    serialized["evidence_refs"].append("world://unexpected")
    serialized["artifacts"].append({"artifact_id": "unexpected"})

    assert envelope.prompt_summary == {"file_count": 1}
    assert envelope.evidence_refs == []
    assert envelope.artifacts == []
