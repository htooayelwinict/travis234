"""Strict, immutable agent-role resource definitions."""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from travis.coding_agent.capabilities import (
    CapabilityDiagnostic,
    CapabilityKind,
    CapabilityRecord,
    CapabilitySnapshot,
    CapabilitySource,
)
from travis.coding_agent.policy import TOOL_EFFECT_ORDER, ToolEffect

ModelRole = Literal["worker", "reviewer"]
ArtifactPolicy = Literal["none", "declared", "declared_and_trace"]

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_FIELDS = {
    "name",
    "description",
    "modelRole",
    "allowedTools",
    "allowedEffects",
    "canSpawn",
    "maxDepth",
    "skills",
    "context",
    "resultSchema",
    "defaultTimeoutSeconds",
    "artifactPolicy",
}
_SCHEMA_MAX_BYTES = 64 * 1024
_SCHEMA_MAX_DEPTH = 32


@dataclass(frozen=True)
class AgentRoleDefinition:
    name: str
    description: str
    model_role: ModelRole
    allowed_tools: tuple[str, ...] | None
    allowed_effects: tuple[ToolEffect, ...] | None
    can_spawn: bool
    max_depth: int
    skills: tuple[str, ...]
    context: tuple[str, ...]
    result_schema: dict[str, object] | None
    default_timeout_seconds: int
    artifact_policy: ArtifactPolicy
    source: CapabilitySource

    @classmethod
    def from_mapping(
        cls,
        mapping: Mapping[str, object],
        *,
        source: CapabilitySource,
    ) -> AgentRoleDefinition:
        if not isinstance(mapping, Mapping):
            raise TypeError("agent role must be an object")
        unknown = sorted(set(mapping).difference(_FIELDS))
        if unknown:
            raise ValueError(f"unknown agent role fields: {', '.join(unknown)}")
        name = mapping.get("name")
        if not isinstance(name, str) or _NAME_PATTERN.fullmatch(name) is None:
            raise ValueError("agent role name must match [a-z][a-z0-9_-]{0,63}")
        description = mapping.get("description", "")
        if not isinstance(description, str):
            raise TypeError("agent role description must be a string")
        model_role = mapping.get("modelRole", "worker")
        if model_role not in ("worker", "reviewer"):
            raise ValueError("agent role modelRole must be worker or reviewer")
        allowed_tools = _optional_string_tuple(mapping.get("allowedTools"), "allowedTools")
        raw_effects = _optional_string_tuple(mapping.get("allowedEffects"), "allowedEffects")
        if raw_effects is None:
            allowed_effects = None
        else:
            unknown_effects = set(raw_effects).difference(TOOL_EFFECT_ORDER)
            if unknown_effects:
                raise ValueError(f"agent role contains unknown effect: {sorted(unknown_effects)[0]}")
            allowed_effects = tuple(
                effect for effect in TOOL_EFFECT_ORDER if effect in raw_effects
            )
        can_spawn = mapping.get("canSpawn", False)
        if not isinstance(can_spawn, bool):
            raise TypeError("agent role canSpawn must be a boolean")
        max_depth = mapping.get("maxDepth", 1)
        if isinstance(max_depth, bool) or not isinstance(max_depth, int):
            raise TypeError("agent role maxDepth must be an integer")
        if max_depth not in (0, 1):
            raise ValueError("agent role maxDepth must be 0 or 1")
        skills = _relative_paths(mapping.get("skills", []), "skills")
        context = _relative_paths(mapping.get("context", []), "context")
        timeout = mapping.get("defaultTimeoutSeconds", 1800)
        if isinstance(timeout, bool) or not isinstance(timeout, int):
            raise TypeError("agent role timeout must be an integer")
        if not 1 <= timeout <= 3600:
            raise ValueError("agent role timeout must be between 1 and 3600 seconds")
        policy = mapping.get("artifactPolicy", "none")
        if policy not in ("none", "declared", "declared_and_trace"):
            raise ValueError(
                "agent role artifactPolicy must be none, declared, or declared_and_trace"
            )
        schema = _validated_schema(mapping.get("resultSchema"))
        return cls(
            name=name,
            description=description,
            model_role=model_role,  # type: ignore[arg-type]
            allowed_tools=allowed_tools,
            allowed_effects=allowed_effects,
            can_spawn=can_spawn,
            max_depth=max_depth,
            skills=skills,
            context=context,
            result_schema=schema,
            default_timeout_seconds=timeout,
            artifact_policy=policy,  # type: ignore[arg-type]
            source=source,
        )


class AgentRoleRegistry:
    """Typed projection of agent-role winners from a capability snapshot."""

    def __init__(self, snapshot: CapabilitySnapshot) -> None:
        self._snapshot = snapshot

    def get(self, name: str) -> AgentRoleDefinition | None:
        winner = self._snapshot.resolve(CapabilityKind.AGENT_ROLE, name).winner
        return winner.value if winner and isinstance(winner.value, AgentRoleDefinition) else None

    def list(self) -> tuple[AgentRoleDefinition, ...]:
        roles = (
            record.value
            for record in self._snapshot.records(CapabilityKind.AGENT_ROLE)
        )
        return tuple(sorted(
            (role for role in roles if isinstance(role, AgentRoleDefinition)),
            key=lambda role: role.name,
        ))


def load_agent_roles(
    paths: tuple[str, ...] | list[str],
    *,
    metadata_by_path: Mapping[str, dict[str, object]] | None = None,
) -> tuple[tuple[CapabilityRecord, ...], tuple[CapabilityDiagnostic, ...]]:
    records: list[CapabilityRecord] = []
    diagnostics: list[CapabilityDiagnostic] = []
    for path_text in paths:
        path = Path(path_text).expanduser().resolve()
        source = _source_for_path(path, metadata_by_path)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            role = AgentRoleDefinition.from_mapping(raw, source=source)
        except (OSError, json.JSONDecodeError, TypeError, ValueError, SchemaError) as error:
            diagnostics.append(CapabilityDiagnostic(
                "error",
                "default-resources",
                "invalid_agent_role",
                f"invalid agent role {path.name}: {type(error).__name__}",
                source,
            ))
            continue
        records.append(CapabilityRecord(
            CapabilityKind.AGENT_ROLE,
            role.name,
            role,
            source,
            priority=_scope_priority(source.scope),
        ))
    return tuple(records), tuple(diagnostics)


def _optional_string_tuple(value: object, field: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    return _string_tuple(value, field)


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item for item in value
    ):
        raise TypeError(f"agent role {field} must be a list of non-empty strings")
    if len(set(value)) != len(value):
        raise ValueError(f"agent role {field} entries must be unique")
    return tuple(value)


def _relative_paths(value: object, field: str) -> tuple[str, ...]:
    entries = _string_tuple(value, field)
    for entry in entries:
        candidate = PurePosixPath(entry.replace("\\", "/"))
        if candidate.is_absolute():
            raise ValueError(f"agent role {field} paths must be relative")
        if ".." in candidate.parts:
            raise ValueError(f"agent role {field} path cannot escape its source")
    return entries


def _validated_schema(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError("agent role resultSchema must be an object")
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    if len(encoded) > _SCHEMA_MAX_BYTES:
        raise ValueError("agent role result schema exceeds 64 KiB")
    if _depth(value) > _SCHEMA_MAX_DEPTH:
        raise ValueError("agent role result schema exceeds 32 levels")
    try:
        Draft202012Validator.check_schema(value)
    except SchemaError as error:
        raise ValueError("agent role result schema is invalid") from error
    return copy.deepcopy(value)


def _depth(value: object) -> int:
    if isinstance(value, dict):
        return 1 + max((_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_depth(item) for item in value), default=0)
    return 0


def _source_for_path(
    path: Path, metadata_by_path: Mapping[str, dict[str, object]] | None
) -> CapabilitySource:
    metadata = metadata_by_path.get(str(path)) if metadata_by_path else None
    if metadata is None and metadata_by_path:
        for root, candidate in metadata_by_path.items():
            try:
                path.relative_to(Path(root).resolve())
            except ValueError:
                continue
            metadata = candidate
            break
    metadata = metadata or {}
    scope = str(metadata.get("scope", "temporary"))
    if scope == "user":
        scope = "global"
    return CapabilitySource(
        "default-resources",
        str(path),
        str(metadata.get("source", "local")),
        scope,
        str(metadata.get("origin", "top-level")),
    )


def _scope_priority(scope: str) -> int:
    return {"project": 30, "temporary": 20, "global": 10, "user": 10}.get(scope, 0)


__all__ = ["AgentRoleDefinition", "AgentRoleRegistry", "load_agent_roles"]
