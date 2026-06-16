from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


def _freeze_json_like(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze_json_like(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze_json_like(item) for item in value)
    if isinstance(value, set | frozenset):
        return tuple(_freeze_json_like(item) for item in sorted(value, key=repr))
    return deepcopy(value)


@dataclass(frozen=True)
class ToolDefinition:
    tool_id: str
    category: str
    risk_level: str
    argument_schema: Mapping[str, Any]
    result_schema: Mapping[str, Any]
    trust: str
    guidance: str
    freshness: str = "stable"
    invalidated_by_mutation: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "argument_schema", _freeze_json_like(self.argument_schema))
        object.__setattr__(self, "result_schema", _freeze_json_like(self.result_schema))
