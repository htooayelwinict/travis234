import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "appV2.1"))

from appv21 import AppV21AgentRuntime
from appv21.runtime.decisions import RuntimeDecision
from appv21.runtime.services import create_appv21_runtime_services
from appv21.tools.definitions import ToolCategory, ToolDefinition, ToolResultEnvelope
from appv21.tools.broker import ToolBroker
from appv21.tools.registry import ToolRegistry


class QueueProvider:
    provider_id = "queue"

    def __init__(self, decisions: list[RuntimeDecision]) -> None:
        self.decisions = decisions

    def decide(self, _prompt_payload: dict) -> RuntimeDecision:
        if not self.decisions:
            return RuntimeDecision(kind="finalize", reason="done", payload={"explicit_noop": True})
        return self.decisions.pop(0)


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


def test_broker_specs_are_registry_backed(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="repo_snapshot",
            category=ToolCategory.SEARCH,
            argument_schema={"type": "object", "properties": {}, "additionalProperties": False},
            result_schema={"type": "object", "properties": {"files": {"type": "array"}}},
            risk_level="medium",
            trust="custom_trust",
            guidance="custom guidance",
        )
    )
    broker = ToolBroker(root_path=tmp_path, registry=registry)

    assert broker.tool_specs() == [
        {
            "name": "repo_snapshot",
            "category": "search",
            "trust": "custom_trust",
            "guidance": "custom guidance",
            "argument_schema": {"type": "object", "properties": {}, "additionalProperties": False},
            "result_schema": {"type": "object", "properties": {"files": {"type": "array"}}},
            "risk_level": "medium",
        }
    ]


def test_broker_validate_tool_call_routes_through_registry(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# hello\n", encoding="utf-8")
    broker = ToolBroker(root_path=tmp_path)

    assert broker.validate_tool_call("missing", {}) == ["unknown_tool:missing"]
    assert broker.validate_tool_call("read_file", {}) == ["missing_argument:path"]
    assert broker.validate_tool_call("read_file", {"path": "README.md", "extra": True}) == ["unknown_argument:extra"]


def test_broker_custom_registry_metadata_without_handler_is_not_exposed_or_callable(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="inspect_manifest",
            category=ToolCategory.INSPECT,
            argument_schema={
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
            result_schema={"type": "object"},
            trust="custom_runtime_trust",
        )
    )
    broker = ToolBroker(root_path=tmp_path, registry=registry)

    assert broker.tool_specs() == []
    assert broker.tool_policy_for(None)["read_tools"] == []
    assert broker.validate_tool_call("inspect_manifest", {}) == ["missing_argument:path"]
    assert broker.validate_tool_call("inspect_manifest", {"path": "pyproject.toml"}) == [
        "unavailable_tool:inspect_manifest"
    ]


def test_broker_custom_registered_tool_with_handler_is_exposed_validates_and_executes(tmp_path: Path) -> None:
    registry = ToolRegistry()
    broker = ToolBroker(root_path=tmp_path, registry=registry)
    definition = ToolDefinition(
        name="inspect_manifest",
        category=ToolCategory.INSPECT,
        argument_schema={
            "type": "object",
            "required": ["path"],
            "properties": {"path": {"type": "string"}},
            "additionalProperties": False,
        },
        result_schema={"type": "object"},
        trust="custom_runtime_trust",
    )

    def inspect_manifest(arguments: dict) -> dict:
        path = arguments["path"]
        return {
            "status": "completed",
            "tool_name": "inspect_manifest",
            "path": path,
            "content": "manifest ok",
            "payload_ref": "handler://conflicting-payload-ref",
            "evidence_refs": ["handler://conflicting-evidence-ref"],
            "artifacts": [{"artifact_id": "handler-conflict"}],
            "prompt_summary": {"path": path, "preview": "manifest ok"},
        }

    broker.register_tool(definition, inspect_manifest)

    assert broker.tool_specs() == [
        {
            "name": "inspect_manifest",
            "category": "inspect",
            "trust": "custom_runtime_trust",
            "guidance": "",
            "argument_schema": {
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
            "result_schema": {"type": "object"},
            "risk_level": "low",
        }
    ]
    assert broker.validate_tool_call("inspect_manifest", {}) == ["missing_argument:path"]
    assert broker.validate_tool_call("inspect_manifest", {"path": "pyproject.toml", "extra": True}) == [
        "unknown_argument:extra"
    ]
    assert broker.validate_tool_call("inspect_manifest", {"path": "pyproject.toml"}) == []
    assert broker.tool_policy_for(None)["read_tools"] == ["inspect_manifest"]

    result = broker.execute_tool_call("inspect_manifest", {"path": "pyproject.toml"})

    assert result["status"] == "completed"
    assert result["trust"] == "custom_runtime_trust"
    assert result["payload_ref"].startswith("world://tool_payload/")
    assert result["payload"]["path"] == "pyproject.toml"
    assert result["payload"]["bytes"] == len("manifest ok".encode("utf-8"))
    assert "content" not in result["payload"]
    assert "preview" not in result["prompt_summary"]
    assert result["prompt_summary"] == {"path": "pyproject.toml", "bytes": len("manifest ok".encode("utf-8")), "line_count": 1}
    assert "payload_ref" not in result["payload"]
    assert "evidence_refs" not in result["payload"]
    assert "artifacts" not in result["payload"]


def test_tool_raw_payload_is_retained_by_ref_not_prompt(tmp_path: Path) -> None:
    distinctive_content = "DISTINCTIVE_SHORT_RAW_PAYLOAD_CONTENT\n"
    (tmp_path / "notes.txt").write_text(distinctive_content, encoding="utf-8")
    provider = QueueProvider(
        [
            RuntimeDecision(kind="tool_call", reason="read file", payload={"tool_name": "read_file", "arguments": {"path": "notes.txt"}}),
            RuntimeDecision(kind="finalize", reason="no-op verified", payload={"explicit_noop": True}),
        ]
    )
    services = create_appv21_runtime_services(root_path=tmp_path, provider=provider)

    result = AppV21AgentRuntime(root_path=tmp_path, services=services).run("Read notes.")

    completed = [event for event in result["events"] if event["event_type"] == "ToolCallCompleted" and event["payload"]["tool_name"] == "read_file"]
    assert completed
    envelope = completed[0]["payload"]
    payload_ref = envelope["payload_ref"]
    assert payload_ref.startswith("world://tool_payload/")
    assert services.evidence_store.get(payload_ref) == {
        "path": "notes.txt",
        "bytes": len(distinctive_content.encode("utf-8")),
        "content": distinctive_content,
    }
    assert "content" not in envelope.get("payload", {})
    assert envelope["prompt_summary"]["path"] == "notes.txt"
    assert envelope["prompt_summary"]["bytes"] == len(distinctive_content.encode("utf-8"))
    assert "preview" not in envelope["prompt_summary"]

    events_json = json.dumps(result["events"])
    assert distinctive_content not in events_json
    world_refs = [
        event["payload"]
        for event in result["events"]
        if event["event_type"] == "WorldRefAdded" and event["payload"].get("kind") == "tool_result"
    ]
    assert any(ref["payload"]["payload_ref"] == payload_ref for ref in world_refs)
    assert any(ref["payload"]["prompt_summary"]["path"] == "notes.txt" for ref in world_refs)


def test_runtime_denied_sensitive_read_has_no_payload_ref_or_evidence_payload(tmp_path: Path) -> None:
    secret_content = "OPENAI_API_KEY=sk-distinctive-secret\n"
    (tmp_path / ".env").write_text(secret_content, encoding="utf-8")
    provider = QueueProvider(
        [
            RuntimeDecision(kind="tool_call", reason="read env", payload={"tool_name": "read_file", "arguments": {"path": ".env"}}),
            RuntimeDecision(kind="finalize", reason="no-op verified", payload={"explicit_noop": True}),
        ]
    )
    services = create_appv21_runtime_services(root_path=tmp_path, provider=provider)

    result = AppV21AgentRuntime(root_path=tmp_path, services=services).run("Read env.")

    denied = [event for event in result["events"] if event["event_type"] == "ToolCallDenied" and event["payload"]["tool_name"] == "read_file"]
    assert denied
    denied_payload = denied[0]["payload"]
    assert denied_payload["payload_ref"] == ""
    assert services.evidence_store.get(denied_payload["tool_result_id"]) is None
    assert services.evidence_store.get(denied_payload["payload_ref"]) is None
    assert all(
        not (event["event_type"] == "WorldRefAdded" and event["payload"].get("kind") == "tool_result")
        for event in result["events"]
    )
    assert secret_content not in json.dumps(result["events"])
