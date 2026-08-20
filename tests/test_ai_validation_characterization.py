from __future__ import annotations

from dataclasses import dataclass

import pytest

from travis.ai.validation import (
    ToolValidationError,
    _coerce_primitive_by_type,
    _coerce_with_json_schema,
    _validate_value,
    validate_tool_arguments,
)


@dataclass(slots=True)
class _Tool:
    name: str
    parameters: dict[str, object]


@dataclass(slots=True)
class _ToolCall:
    arguments: object


@pytest.mark.parametrize(
    ("schema_type", "value", "expected", "expected_type"),
    [
        ("number", None, 0, int),
        ("number", "12", 12, int),
        ("number", "12.0", 12, int),
        ("number", "12.5", 12.5, float),
        ("number", "1e2", 100, int),
        ("number", True, 1, int),
        ("number", False, 0, int),
        ("integer", None, 0, int),
        ("integer", "12", 12, int),
        ("integer", "12.0", 12, int),
        ("integer", True, 1, int),
        ("integer", False, 0, int),
        ("boolean", None, False, bool),
        ("boolean", "true", True, bool),
        ("boolean", "false", False, bool),
        ("boolean", 1, True, bool),
        ("boolean", 1.0, True, bool),
        ("boolean", 0, False, bool),
        ("boolean", 0.0, False, bool),
        ("string", None, "", str),
        ("string", True, "true", str),
        ("string", False, "false", str),
        ("string", 12, "12", str),
        ("string", 12.5, "12.5", str),
        ("null", "", None, type(None)),
        ("null", 0, None, type(None)),
        ("null", 0.0, None, type(None)),
        ("null", False, None, type(None)),
    ],
)
def test_primitive_coercion_preserves_conversion_values_and_runtime_types(
    schema_type: str,
    value: object,
    expected: object,
    expected_type: type[object],
) -> None:
    result = _coerce_primitive_by_type(value, schema_type)

    assert result == expected
    assert type(result) is expected_type


@pytest.mark.parametrize(
    ("schema_type", "value"),
    [
        ("number", ""),
        ("number", "   "),
        ("number", "not-a-number"),
        ("number", "nan"),
        ("number", "NaN"),
        ("number", "inf"),
        ("number", "-Infinity"),
        ("integer", "12.5"),
        ("integer", "nan"),
        ("integer", "Infinity"),
        ("boolean", "True"),
        ("boolean", "FALSE"),
        ("boolean", 2),
        ("string", "already-text"),
        ("null", "0"),
        ("unknown", "unchanged"),
    ],
)
def test_primitive_coercion_returns_the_original_value_when_no_branch_converts(
    schema_type: str,
    value: object,
) -> None:
    assert _coerce_primitive_by_type(value, schema_type) is value


def test_primitive_coercion_preserves_existing_numeric_identity() -> None:
    integer = int("1000000000000000000000000000001")
    decimal = float("12.25")
    nonfinite = float("nan")

    assert _coerce_primitive_by_type(integer, "number") is integer
    assert _coerce_primitive_by_type(decimal, "number") is decimal
    assert _coerce_primitive_by_type(nonfinite, "number") is nonfinite


def test_all_of_coercion_applies_nested_schemas_in_declared_order() -> None:
    assert (
        _coerce_with_json_schema(
            None,
            {"allOf": [{"type": "string"}, {"type": "boolean"}]},
        )
        == ""
    )
    assert (
        _coerce_with_json_schema(
            None,
            {"allOf": [{"type": "boolean"}, {"type": "string"}]},
        )
        == "false"
    )


@pytest.mark.parametrize("keyword", ["anyOf", "oneOf"])
def test_union_schema_coercion_uses_the_first_valid_nested_schema(keyword: str) -> None:
    assert (
        _coerce_with_json_schema(
            "7",
            {keyword: [{"type": "integer"}, {"type": "string"}]},
        )
        == 7
    )
    result = _coerce_with_json_schema(
        "7",
        {keyword: [{"type": "string"}, {"type": "integer"}]},
    )

    assert result == "7"
    assert type(result) is str


@pytest.mark.parametrize("keyword", ["anyOf", "oneOf"])
def test_union_schema_returns_original_identity_when_no_nested_schema_validates(
    keyword: str,
) -> None:
    value = {"unchanged": object()}

    result = _coerce_with_json_schema(
        value,
        {keyword: [{"type": "integer"}, {"type": "boolean"}]},
    )

    assert result is value


def test_union_type_preserves_an_already_matching_member_before_coercion() -> None:
    value = "7".join(("", ""))

    result = _coerce_with_json_schema(
        value,
        {"type": ["integer", "string"]},
    )

    assert result is value


def test_union_type_coerces_unmatched_value_by_declared_type_order() -> None:
    assert _coerce_with_json_schema(None, {"type": ["integer", "string"]}) == 0
    assert _coerce_with_json_schema(None, {"type": ["string", "integer"]}) == ""


def test_object_properties_and_additional_property_schema_coerce_in_place() -> None:
    value: dict[str, object] = {
        "enabled": "true",
        "known": "2",
        "attempts": "3.0",
    }
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "known": {"type": "integer"},
        },
        "additionalProperties": {"type": "number"},
    }

    result = _coerce_with_json_schema(value, schema)

    assert result is value
    assert value == {"enabled": "true", "known": 2, "attempts": 3}


def test_properties_keyword_coerces_objects_without_an_explicit_object_type() -> None:
    value: dict[str, object] = {"count": "4"}

    result = _coerce_with_json_schema(
        value,
        {"properties": {"count": {"type": "integer"}}},
    )

    assert result is value
    assert value == {"count": 4}


def test_tuple_array_coercion_uses_schema_and_index_order_and_leaves_extras() -> None:
    value: list[object] = ["2", "false", 3, "extra"]

    result = _coerce_with_json_schema(
        value,
        {
            "type": "array",
            "items": [
                {"type": "integer"},
                {"type": "boolean"},
                {"type": "string"},
            ],
        },
    )

    assert result is value
    assert value == [2, False, "3", "extra"]


def test_homogeneous_array_coercion_mutates_each_existing_item_in_place() -> None:
    value: list[object] = ["1", "2.0", "3"]

    result = _coerce_with_json_schema(
        value,
        {"type": "array", "items": {"type": "integer"}},
    )

    assert result is value
    assert value == [1, 2, 3]


def test_items_without_array_type_do_not_trigger_compatibility_coercion() -> None:
    value: list[object] = ["1"]

    result = _coerce_with_json_schema(value, {"items": {"type": "integer"}})

    assert result is value
    assert value == ["1"]


def test_non_mapping_schema_returns_the_original_value_identity() -> None:
    value = {"count": "1"}

    assert _coerce_with_json_schema(value, ["not", "a", "schema"]) is value


@pytest.mark.parametrize(
    ("schema", "arguments", "expected"),
    [
        (
            {
                "type": "object",
                "properties": {"value": {"allOf": [{"type": "number"}, {"type": "integer"}]}},
                "required": ["value"],
            },
            {"value": "2.0"},
            {"value": 2},
        ),
        (
            {
                "type": "object",
                "properties": {
                    "value": {"anyOf": [{"type": "integer"}, {"type": "string"}]},
                },
                "required": ["value"],
            },
            {"value": "7"},
            {"value": 7},
        ),
        (
            {
                "type": "object",
                "properties": {
                    "value": {"oneOf": [{"type": "integer"}, {"type": "string"}]},
                },
                "required": ["value"],
            },
            {"value": "8"},
            {"value": 8},
        ),
        (
            {
                "type": "object",
                "properties": {
                    "known": {"type": "integer"},
                },
                "additionalProperties": {"type": "boolean"},
            },
            {"known": "2", "enabled": "true"},
            {"known": 2, "enabled": True},
        ),
        (
            {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "label": {"type": "string"},
                    "empty": {"type": "null"},
                },
            },
            {"enabled": "false", "label": True, "empty": 0},
            {"enabled": False, "label": "true", "empty": None},
        ),
        (
            {
                "type": "object",
                "properties": {
                    "values": {"type": "array", "items": {"type": "number"}},
                },
            },
            {"values": ["1", "2.5"]},
            {"values": [1, 2.5]},
        ),
        (
            {
                "type": "object",
                "properties": {
                    "value": {"type": ["integer", "string"]},
                },
            },
            {"value": "9"},
            {"value": "9"},
        ),
        (
            {
                "$schema": "http://json-schema.org/draft-07/schema#",
                "type": "object",
                "properties": {
                    "values": {
                        "type": "array",
                        "items": [{"type": "integer"}, {"type": "boolean"}],
                    },
                },
            },
            {"values": ["3", "false", "extra"]},
            {"values": [3, False, "extra"]},
        ),
    ],
)
def test_public_validation_preserves_composite_coercion_results(
    schema: dict[str, object],
    arguments: dict[str, object],
    expected: dict[str, object],
) -> None:
    assert validate_tool_arguments(_Tool("check", schema), _ToolCall(arguments)) == expected


def test_public_validation_deep_copies_nested_arguments_without_mutating_caller_state() -> None:
    nested: dict[str, object] = {"count": "2"}
    values: list[object] = ["3", "4"]
    arguments: dict[str, object] = {"nested": nested, "values": values}
    tool = _Tool(
        "copy",
        {
            "type": "object",
            "properties": {
                "nested": {
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                },
                "values": {"type": "array", "items": {"type": "integer"}},
            },
        },
    )

    result = validate_tool_arguments(tool, _ToolCall(arguments))

    assert result == {"nested": {"count": 2}, "values": [3, 4]}
    assert result is not arguments
    assert result["nested"] is not nested
    assert result["values"] is not values
    assert arguments == {"nested": {"count": "2"}, "values": ["3", "4"]}


@pytest.mark.parametrize(
    ("schema", "arguments", "expected"),
    [
        (
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
            {},
            'Validation failed for tool "check":\n'
            "  - check: missing required property 'name'\n\n"
            "Received arguments:\n{}",
        ),
        (
            {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "additionalProperties": False,
            },
            {"name": "ok", "extra": 1},
            'Validation failed for tool "check":\n'
            "  - check: Additional properties are not allowed ('extra' was unexpected)\n\n"
            "Received arguments:\n{\n"
            '  "name": "ok",\n'
            '  "extra": 1\n'
            "}",
        ),
        (
            {
                "type": "object",
                "properties": {"items": {"type": "array", "minItems": 2}},
                "required": ["items"],
            },
            {"items": ["one"]},
            'Validation failed for tool "check":\n'
            "  - check.items: expected array length >= 2\n\n"
            "Received arguments:\n{\n"
            '  "items": [\n'
            '    "one"\n'
            "  ]\n"
            "}",
        ),
        (
            {
                "type": "object",
                "properties": {"items": {"type": "array", "maxItems": 1}},
                "required": ["items"],
            },
            {"items": ["one", "two"]},
            'Validation failed for tool "check":\n'
            "  - check.items: expected array length <= 1\n\n"
            "Received arguments:\n{\n"
            '  "items": [\n'
            '    "one",\n'
            '    "two"\n'
            "  ]\n"
            "}",
        ),
        (
            {
                "type": "object",
                "properties": {"name": {"type": "string", "minLength": 3}},
                "required": ["name"],
            },
            {"name": "x"},
            'Validation failed for tool "check":\n'
            "  - check.name: 'x' is too short\n\n"
            "Received arguments:\n{\n"
            '  "name": "x"\n'
            "}",
        ),
        (
            {
                "type": "object",
                "properties": {"name": {"type": "string", "maxLength": 3}},
                "required": ["name"],
            },
            {"name": "long"},
            'Validation failed for tool "check":\n'
            "  - check.name: 'long' is too long\n\n"
            "Received arguments:\n{\n"
            '  "name": "long"\n'
            "}",
        ),
        (
            {
                "type": "object",
                "properties": {"value": {"type": "number"}},
                "required": ["value"],
            },
            {"value": "NaN"},
            'Validation failed for tool "check":\n'
            "  - check.value: expected number\n\n"
            "Received arguments:\n{\n"
            '  "value": "NaN"\n'
            "}",
        ),
        (
            {
                "type": "object",
                "properties": {"value": {"type": ["integer", "null"]}},
                "required": ["value"],
            },
            {"value": "bad"},
            'Validation failed for tool "check":\n'
            "  - check.value: expected integer or null\n\n"
            "Received arguments:\n{\n"
            '  "value": "bad"\n'
            "}",
        ),
    ],
)
def test_public_validation_error_text_is_exact(
    schema: dict[str, object],
    arguments: dict[str, object],
    expected: str,
) -> None:
    with pytest.raises(ToolValidationError) as error:
        validate_tool_arguments(_Tool("check", schema), _ToolCall(arguments))

    assert str(error.value) == expected


@pytest.mark.parametrize(
    ("value", "schema"),
    [
        ("abc", {"allOf": [{"type": "string", "minLength": 2}, {"type": "string", "maxLength": 4}]}),
        (1, {"anyOf": [{"type": "string"}, {"type": "integer"}]}),
        ("one", {"oneOf": [{"type": "string"}, {"type": "integer"}]}),
        ({"name": "ok"}, {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
        ([1, 2], {"type": "array", "items": {"type": "integer"}, "minItems": 1, "maxItems": 2}),
        ([1, "two"], {"type": "array", "items": [{"type": "integer"}, {"type": "string"}]}),
        (None, {"type": ["string", "null"]}),
        (False, {"type": "boolean"}),
    ],
)
def test_legacy_validate_value_accepts_each_supported_shape(
    value: object,
    schema: dict[str, object],
) -> None:
    _validate_value(value, schema, "tool")


@pytest.mark.parametrize(
    ("value", "schema", "expected"),
    [
        (
            "x",
            {"allOf": [{"type": "string", "minLength": 2}, {"type": "string", "maxLength": 4}]},
            "tool: expected string length >= 2",
        ),
        ([], {"anyOf": [{"type": "string"}, {"type": "integer"}]}, "tool: expected anyOf match"),
        (1, {"oneOf": [{"type": "integer"}, {"type": "number"}]}, "tool: expected oneOf match"),
        ([], {"oneOf": [{"type": "integer"}, {"type": "string"}]}, "tool: expected oneOf match"),
        ([], {"type": "object"}, "tool: expected object"),
        ({}, {"type": "object", "required": ["second", "first"]}, "tool: missing required property 'second'"),
        (
            {"second": 2, "first": 1},
            {
                "type": "object",
                "properties": {"first": {"type": "string"}, "second": {"type": "string"}},
            },
            "tool.second: expected string",
        ),
        (
            {"known": "ok", "z": 1, "a": 2},
            {
                "type": "object",
                "properties": {"known": {"type": "string"}},
                "additionalProperties": False,
            },
            "tool.z: unexpected property",
        ),
        ({}, {"type": "array"}, "tool: expected array"),
        ([], {"type": "array", "minItems": 1, "items": {"type": "string"}}, "tool: expected array length >= 1"),
        (["one", "two"], {"type": "array", "maxItems": 1}, "tool: expected array length <= 1"),
        ([1, 2], {"type": "array", "items": [{"type": "string"}, {"type": "integer"}]}, "tool[0]: expected string"),
        ([1, "bad"], {"type": "array", "items": {"type": "integer"}}, "tool[1]: expected integer"),
        ([], {"type": ["integer", "null"]}, "tool: expected integer or null"),
        (1, {"type": "string"}, "tool: expected string"),
        ("x", {"type": "string", "minLength": 2}, "tool: expected string length >= 2"),
        ("long", {"type": "string", "maxLength": 3}, "tool: expected string length <= 3"),
        (True, {"type": "integer"}, "tool: expected integer"),
        (True, {"type": "number"}, "tool: expected number"),
        (0, {"type": "boolean"}, "tool: expected boolean"),
        (False, {"type": "null"}, "tool: expected null"),
    ],
)
def test_legacy_validate_value_error_text_and_iteration_order_are_exact(
    value: object,
    schema: dict[str, object],
    expected: str,
) -> None:
    with pytest.raises(ToolValidationError) as error:
        _validate_value(value, schema, "tool")

    assert str(error.value) == expected
