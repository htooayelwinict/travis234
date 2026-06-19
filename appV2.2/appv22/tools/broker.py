from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from itertools import count
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from appv22.tools.registry import ToolRegistry


_SAFE_STATUSES = frozenset({"completed", "failed", "denied"})
_SCHEMA_TYPES = frozenset({"object", "string", "integer", "number", "boolean", "array"})


class ToolBroker:
    def __init__(self, *, registry: ToolRegistry, root_path: str | Path) -> None:
        self.registry = registry
        self.root_path = Path(root_path).resolve()
        self._result_counter = count(1)

    def execute(
        self,
        tool_id: str,
        arguments: dict[str, Any],
        *,
        active_tool_ids: list[str] | tuple[str, ...] | set[str] | frozenset[str],
        request_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if tool_id not in set(active_tool_ids):
            return self._envelope(
                tool_id,
                "denied",
                {"errors": [f"inactive_tool:{tool_id}"]},
                create_ref=False,
            )

        definition = self.registry.definition(tool_id)
        handler = self.registry.handler(tool_id)
        if definition is None or handler is None:
            return self._envelope(
                tool_id,
                "denied",
                {"errors": [f"unknown_tool:{tool_id}"]},
                create_ref=False,
            )

        errors = _validate_value_against_schema(
            definition.argument_schema,
            arguments,
            missing_prefix="missing_argument",
            type_prefix="invalid_argument_type",
        )
        if errors:
            return self._envelope(tool_id, "denied", {"errors": errors}, create_ref=False)

        try:
            handler_context = {"root_path": self.root_path}
            if isinstance(request_context, dict):
                handler_context["request"] = deepcopy(request_context)
            handler_result = handler(deepcopy(arguments), handler_context)
        except Exception as exc:  # noqa: BLE001 - broker must not leak handler failures.
            return self._envelope(
                tool_id,
                "failed",
                {"errors": ["handler_exception"]},
                create_ref=False,
            )

        if not isinstance(handler_result, dict):
            return self._envelope(
                tool_id,
                "failed",
                {"errors": ["malformed_handler_result:expected_object"]},
                create_ref=False,
            )

        status = str(handler_result.get("status", "completed"))
        payload = {key: deepcopy(value) for key, value in handler_result.items() if key != "status"}

        if status not in _SAFE_STATUSES:
            payload_errors = list(payload.get("errors", ())) if isinstance(payload.get("errors"), list) else []
            payload_errors.insert(0, f"invalid_status:{status}")
            payload["errors"] = payload_errors
            status = "failed"

        if status == "completed":
            result_errors = _validate_value_against_schema(
                definition.result_schema,
                payload,
                missing_prefix="missing_result",
                type_prefix="invalid_result_type",
            )
            if result_errors:
                return self._envelope(
                    tool_id,
                    "failed",
                    {"errors": result_errors},
                    create_ref=False,
                )

        return self._envelope(
            tool_id,
            status,
            payload,
            definition=definition,
            create_ref=status == "completed",
            arguments=arguments,
        )

    def _envelope(
        self,
        tool_id: str,
        status: str,
        payload: dict[str, Any],
        *,
        definition=None,
        create_ref: bool,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result_id = f"toolres_{next(self._result_counter):06d}"
        payload_ref = self._payload_ref(tool_id, payload, definition=definition, arguments=arguments or {}) if create_ref else ""
        return {
            "tool_result_id": result_id,
            "tool_id": tool_id,
            "status": status,
            "payload": deepcopy(payload),
            "payload_ref": payload_ref,
            "evidence_refs": [payload_ref] if payload_ref else [],
        }

    def _payload_ref(self, tool_id: str, payload: dict[str, Any], *, definition=None, arguments: dict[str, Any]) -> str:
        if tool_id == "file_management.repo_snapshot":
            return "world://file_management.repo_snapshot/latest"
        stable = json.dumps(
            {"arguments": arguments, "payload": payload, "tool_id": tool_id},
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        digest = sha256(stable.encode("utf-8")).hexdigest()[:16]
        return f"world://{tool_id}/{digest}"

    def validate_result_payload(self, tool_id: str, payload: dict[str, Any]) -> list[str]:
        definition = self.registry.definition(tool_id)
        if definition is None:
            return [f"unknown_tool:{tool_id}"]
        return _validate_value_against_schema(
            definition.result_schema,
            payload,
            missing_prefix="missing_result",
            type_prefix="invalid_result_type",
        )


def _validate_value_against_schema(
    schema: Any,
    value: Any,
    *,
    missing_prefix: str,
    type_prefix: str,
) -> list[str]:
    if not isinstance(schema, Mapping):
        return []

    errors: list[str] = []
    schema_type = schema.get("type")
    if schema_type is not None and schema_type in _SCHEMA_TYPES:
        if not _matches_schema_type(value, schema_type):
            return [f"{type_prefix}:<root>:expected_{schema_type}"]

    if not isinstance(value, dict):
        if schema_type == "object":
            return [f"{type_prefix}:<root>:expected_object"]
        return errors

    for key in schema.get("required", ()):
        if key not in value:
            errors.append(f"{missing_prefix}:{key}")

    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return errors

    for key, property_schema in properties.items():
        if key not in value or not isinstance(property_schema, Mapping):
            continue
        errors.extend(
            _validate_property(
                property_schema,
                value[key],
                path=str(key),
                missing_prefix=missing_prefix,
                type_prefix=type_prefix,
            )
        )

    return errors


def _validate_property(
    schema: Mapping[str, Any],
    value: Any,
    *,
    path: str,
    missing_prefix: str,
    type_prefix: str,
) -> list[str]:
    errors: list[str] = []
    schema_type = schema.get("type")
    if schema_type in _SCHEMA_TYPES and not _matches_schema_type(value, schema_type):
        return [f"{type_prefix}:{path}:expected_{schema_type}"]

    if schema_type != "object" or not isinstance(value, dict):
        return errors

    for key in schema.get("required", ()):
        if key not in value:
            errors.append(f"{missing_prefix}:{path}.{key}")

    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return errors

    for key, property_schema in properties.items():
        if key not in value or not isinstance(property_schema, Mapping):
            continue
        errors.extend(
            _validate_property(
                property_schema,
                value[key],
                path=f"{path}.{key}",
                missing_prefix=missing_prefix,
                type_prefix=type_prefix,
            )
        )

    return errors


def _matches_schema_type(value: Any, schema_type: str) -> bool:
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "array":
        return isinstance(value, list)
    return True
