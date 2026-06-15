"""Registry and lightweight argument validation for AppV2.1 tools."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from typing import Any

from appv21.tools.definitions import ToolDefinition


class ToolRegistry:
    """In-memory registry of tool definitions."""

    def __init__(self) -> None:
        self._definitions: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        self._definitions[definition.name] = self._copy_definition(definition)

    def get(self, name: str) -> ToolDefinition | None:
        definition = self._definitions.get(name)
        if definition is None:
            return None
        return self._copy_definition(definition)

    def list(self) -> list[ToolDefinition]:
        return [self._copy_definition(self._definitions[name]) for name in sorted(self._definitions)]

    def validate_call(self, tool_name: str, arguments: dict[str, Any]) -> list[str]:
        definition = self._definitions.get(tool_name)
        if definition is None:
            return [f"unknown_tool:{tool_name}"]

        schema = definition.argument_schema
        properties = schema.get("properties", {})
        issues: list[str] = []

        for key in sorted(schema.get("required", [])):
            if key not in arguments:
                issues.append(f"missing_argument:{key}")

        if schema.get("additionalProperties") is False:
            for key in sorted(arguments):
                if key not in properties:
                    issues.append(f"unknown_argument:{key}")

        for key in sorted(arguments):
            if key in properties:
                expected_type = properties[key].get("type")
                if expected_type in {"string", "object", "array"} and not self._matches_type(
                    arguments[key], expected_type
                ):
                    issues.append(f"invalid_argument_type:{key}:{expected_type}")

        return issues

    def _matches_type(self, value: Any, expected_type: str) -> bool:
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "object":
            return isinstance(value, dict)
        if expected_type == "array":
            return isinstance(value, list)
        return True

    def _copy_definition(self, definition: ToolDefinition) -> ToolDefinition:
        return replace(
            definition,
            argument_schema=deepcopy(definition.argument_schema),
            result_schema=deepcopy(definition.result_schema),
        )
