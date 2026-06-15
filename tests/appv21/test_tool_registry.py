import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.1"))

from appv21.tools.definitions import ToolCategory, ToolDefinition, ToolResultEnvelope
from appv21.tools.registry import ToolRegistry


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


def test_registry_denies_unknown_tools() -> None:
    registry = ToolRegistry()
    assert registry.validate_call("missing", {}) == ["unknown_tool:missing"]


def test_registry_validates_required_arguments() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="read_file",
            category=ToolCategory.INSPECT,
            argument_schema={
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
            result_schema={"type": "object"},
        )
    )

    assert registry.validate_call("read_file", {}) == ["missing_argument:path"]
    assert registry.validate_call("read_file", {"path": "README.md", "extra": True}) == ["unknown_argument:extra"]
    assert registry.validate_call("read_file", {"path": "README.md"}) == []


def test_registry_validation_is_isolated_from_schema_mutation() -> None:
    registry = ToolRegistry()
    definition = ToolDefinition(
        name="read_file",
        category=ToolCategory.INSPECT,
        argument_schema={
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
            "additionalProperties": False,
        },
        result_schema={"type": "object"},
    )
    registry.register(definition)

    definition.argument_schema["required"] = []
    definition.argument_schema["additionalProperties"] = True
    definition.argument_schema["properties"]["path"]["type"] = "object"
    returned = registry.get("read_file")
    assert returned is not None
    returned.argument_schema["required"] = []
    listed = registry.list()[0]
    listed.argument_schema["additionalProperties"] = True

    assert registry.validate_call("read_file", {}) == ["missing_argument:path"]
    assert registry.validate_call("read_file", {"path": "README.md", "extra": True}) == ["unknown_argument:extra"]
    assert registry.validate_call("read_file", {"path": {"bad": True}}) == ["invalid_argument_type:path:string"]
