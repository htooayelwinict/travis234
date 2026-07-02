"""Tool-argument validation. Port of pi/packages/ai/src/utils/validation.ts (subset)."""

from __future__ import annotations

import copy
import json
import math
from typing import Any


class ToolValidationError(ValueError):
    """Raised when tool-call arguments do not match the tool's JSON schema."""


def validate_tool_arguments(tool: Any, tool_call: Any) -> dict[str, Any]:
    """Validate tool_call.arguments against tool.parameters; return parsed args.

    Mirrors pi's validateToolArguments: raises on invalid, returns the value on
    success. `tool.parameters` is a JSON-schema dict.
    """
    args = getattr(tool_call, "arguments", None)
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise _tool_validation_error(tool, tool_call, f"{_tool_name(tool, tool_call)}: expected object")
    schema = getattr(tool, "parameters", None) or {}
    coerced = _coerce_with_json_schema(copy.deepcopy(args), schema)
    try:
        _validate_value(coerced, schema, path=_tool_name(tool, tool_call))
    except ToolValidationError as error:
        raise _tool_validation_error(tool, tool_call, str(error)) from error
    return coerced


def _tool_name(tool: Any, tool_call: Any) -> str:
    name = getattr(tool_call, "name", None) or getattr(tool, "name", None)
    return str(name or "?")


def _tool_validation_error(tool: Any, tool_call: Any, error: str) -> ToolValidationError:
    tool_name = _tool_name(tool, tool_call)
    arguments = getattr(tool_call, "arguments", None)
    received = _format_received_arguments(tool_name, arguments)
    return ToolValidationError(
        f'Validation failed for tool "{tool_name}":\n'
        f"  - {error}\n\n"
        f"Received arguments:\n{received}"
    )


def _format_received_arguments(tool_name: str, arguments: Any) -> str:
    try:
        return json.dumps(arguments, ensure_ascii=False, indent=2, default=str)
    except TypeError:
        return str(arguments)


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _schema_types(schema: dict[str, Any]) -> list[str]:
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return [schema_type]
    if isinstance(schema_type, list):
        return [item for item in schema_type if isinstance(item, str)]
    return []


def _matches_json_type(value: Any, schema_type: str) -> bool:
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "null":
        return value is None
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "object":
        return isinstance(value, dict)
    return False


def _coerce_primitive_by_type(value: Any, schema_type: str) -> Any:
    if schema_type == "number":
        if value is None:
            return 0
        if isinstance(value, str) and value.strip():
            try:
                parsed = float(value)
            except ValueError:
                return value
            if math.isfinite(parsed):
                return int(parsed) if parsed.is_integer() else parsed
        if isinstance(value, bool):
            return 1 if value else 0
        return value
    if schema_type == "integer":
        if value is None:
            return 0
        if isinstance(value, str) and value.strip():
            try:
                parsed = float(value)
            except ValueError:
                return value
            if math.isfinite(parsed) and parsed.is_integer():
                return int(parsed)
        if isinstance(value, bool):
            return 1 if value else 0
        return value
    if schema_type == "boolean":
        if value is None:
            return False
        if isinstance(value, str):
            if value == "true":
                return True
            if value == "false":
                return False
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if value == 1:
                return True
            if value == 0:
                return False
        return value
    if schema_type == "string":
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        return value
    if schema_type == "null":
        if value in ("", 0, False):
            return None
        return value
    return value


def _coerce_with_union_schema(value: Any, schemas: list[dict[str, Any]]) -> Any:
    for schema in schemas:
        candidate = _coerce_with_json_schema(copy.deepcopy(value), schema)
        if _is_valid_value(candidate, schema):
            return candidate
    return value


def _coerce_with_json_schema(value: Any, schema: Any) -> Any:
    if not isinstance(schema, dict):
        return value
    next_value = value

    for nested in schema.get("allOf") or []:
        if isinstance(nested, dict):
            next_value = _coerce_with_json_schema(next_value, nested)

    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        next_value = _coerce_with_union_schema(next_value, [item for item in any_of if isinstance(item, dict)])

    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        next_value = _coerce_with_union_schema(next_value, [item for item in one_of if isinstance(item, dict)])

    schema_types = _schema_types(schema)
    matches_union_member = len(schema_types) > 1 and any(_matches_json_type(next_value, item) for item in schema_types)
    if schema_types and not matches_union_member:
        for schema_type in schema_types:
            candidate = _coerce_primitive_by_type(next_value, schema_type)
            if candidate is not next_value:
                next_value = candidate
                break

    if "object" in schema_types or "properties" in schema:
        if _is_record(next_value):
            properties = schema.get("properties") or {}
            if isinstance(properties, dict):
                for key, property_schema in properties.items():
                    if key in next_value:
                        next_value[key] = _coerce_with_json_schema(next_value[key], property_schema)
                additional = schema.get("additionalProperties")
                if isinstance(additional, dict):
                    for key, item in list(next_value.items()):
                        if key not in properties:
                            next_value[key] = _coerce_with_json_schema(item, additional)

    if "array" in schema_types and isinstance(next_value, list):
        items = schema.get("items")
        if isinstance(items, list):
            for index, item_schema in enumerate(items):
                if index < len(next_value):
                    next_value[index] = _coerce_with_json_schema(next_value[index], item_schema)
        elif isinstance(items, dict):
            for index, item in enumerate(list(next_value)):
                next_value[index] = _coerce_with_json_schema(item, items)

    return next_value


def _is_valid_value(value: Any, schema: dict[str, Any]) -> bool:
    try:
        _validate_value(value, schema, path="value")
    except ToolValidationError:
        return False
    return True


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> None:
    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for nested in all_of:
            if isinstance(nested, dict):
                _validate_value(value, nested, path)
    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any_of:
        if not any(isinstance(nested, dict) and _is_valid_value(value, nested) for nested in any_of):
            raise ToolValidationError(f"{path}: expected anyOf match")
    one_of = schema.get("oneOf")
    if isinstance(one_of, list) and one_of:
        matches = sum(1 for nested in one_of if isinstance(nested, dict) and _is_valid_value(value, nested))
        if matches != 1:
            raise ToolValidationError(f"{path}: expected oneOf match")

    schema_types = _schema_types(schema)
    if "object" in schema_types or "properties" in schema:
        if not isinstance(value, dict):
            raise ToolValidationError(f"{path}: expected object")
        required = schema.get("required") or []
        for key in required:
            if key not in value:
                raise ToolValidationError(f"{path}: missing required property '{key}'")
        properties = schema.get("properties") or {}
        for key, sub_value in value.items():
            if key in properties:
                _validate_value(sub_value, properties[key], f"{path}.{key}")
            elif schema.get("additionalProperties") is False:
                raise ToolValidationError(f"{path}.{key}: unexpected property")
        return
    if "array" in schema_types:
        if not isinstance(value, list):
            raise ToolValidationError(f"{path}: expected array")
        min_items = schema.get("minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            raise ToolValidationError(f"{path}: expected array length >= {min_items}")
        max_items = schema.get("maxItems")
        if isinstance(max_items, int) and len(value) > max_items:
            raise ToolValidationError(f"{path}: expected array length <= {max_items}")
        items = schema.get("items")
        if isinstance(items, list):
            for index, item_schema in enumerate(items):
                if index < len(value):
                    _validate_value(value[index], item_schema, f"{path}[{index}]")
        elif isinstance(items, dict):
            for index, item in enumerate(value):
                _validate_value(item, items, f"{path}[{index}]")
        return

    if len(schema_types) > 1:
        if any(_is_valid_primitive(value, schema_type) for schema_type in schema_types):
            return
        raise ToolValidationError(f"{path}: expected {' or '.join(schema_types)}")
    schema_type = schema_types[0] if schema_types else None
    if schema_type == "string" and not isinstance(value, str):
        raise ToolValidationError(f"{path}: expected string")
    if schema_type == "string":
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            raise ToolValidationError(f"{path}: expected string length >= {min_length}")
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            raise ToolValidationError(f"{path}: expected string length <= {max_length}")
    if schema_type == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise ToolValidationError(f"{path}: expected integer")
    if schema_type == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        raise ToolValidationError(f"{path}: expected number")
    if schema_type == "boolean" and not isinstance(value, bool):
        raise ToolValidationError(f"{path}: expected boolean")
    if schema_type == "null" and value is not None:
        raise ToolValidationError(f"{path}: expected null")


def _is_valid_primitive(value: Any, schema_type: str) -> bool:
    try:
        _validate_value(value, {"type": schema_type}, "value")
    except ToolValidationError:
        return False
    return True
