"""Immutable, content-free contracts for observe-only operation journaling."""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, Mapping

OperationMode = Literal["disabled", "observe"]
ReplayPolicy = Literal["never"]
OperationState = Literal["running", "settled", "failed", "cancelled", "uncertain"]
EffectState = Literal["intent", "settled", "failed", "cancelled", "uncertain"]

OPERATION_STATES = frozenset({"running", "settled", "failed", "cancelled", "uncertain"})
EFFECT_STATES = frozenset({"intent", "settled", "failed", "cancelled", "uncertain"})
_OPERATION_ID = re.compile(r"^op_[0-9a-f]{32}$")
_EFFECT_ID = re.compile(r"^effect_[0-9a-f]{32}$")
_RUNTIME_ID = re.compile(r"^[0-9a-f]{32}$")
_FINGERPRINT = re.compile(r"^[0-9a-f]{64}$")
_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SENSITIVE_KEY = re.compile(
    r"(?:auth|cookie|credential|password|secret|token|api[_-]?key)", re.IGNORECASE
)
_REGISTER_MAX_BYTES = 16 * 1024
_REGISTER_MAX_DEPTH = 32


def _require_pattern(value: object, pattern: re.Pattern[str], label: str) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"invalid {label}")
    return value


def _require_nonnegative_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _require_timestamp(value: object, label: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    return _require_nonnegative_int(value, label)


def _require_code(value: object, label: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    return _require_pattern(value, _CODE, label)


def _sanitize_json(value: object, *, depth: int = 0, key: str | None = None) -> object:
    if depth > _REGISTER_MAX_DEPTH:
        raise ValueError("operation register exceeds 32 levels")
    if key is not None and _SENSITIVE_KEY.search(key):
        return "[redacted]"
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError("operation register values must be finite JSON values")
        return value
    if isinstance(value, list):
        return [_sanitize_json(item, depth=depth + 1) for item in value]
    if isinstance(value, Mapping):
        sanitized: dict[str, object] = {}
        for raw_key, item in value.items():
            if not isinstance(raw_key, str):
                raise TypeError("operation register values must use JSON object keys")
            sanitized[raw_key] = _sanitize_json(
                item, depth=depth + 1, key=raw_key
            )
        return {name: sanitized[name] for name in sorted(sanitized)}
    raise TypeError("operation register values must be JSON values")


def _freeze_json(value: object) -> object:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return copy.deepcopy(value)


def validate_operation_transition(
    current: OperationState, requested: OperationState
) -> OperationState:
    if current not in OPERATION_STATES or requested not in OPERATION_STATES:
        raise ValueError("unknown operation state transition")
    if requested == current:
        return requested
    if current != "running" or requested == "running":
        raise ValueError(f"invalid operation state transition: {current} -> {requested}")
    return requested


def validate_effect_transition(current: EffectState, requested: EffectState) -> EffectState:
    if current not in EFFECT_STATES or requested not in EFFECT_STATES:
        raise ValueError("unknown effect state transition")
    if requested == current:
        return requested
    if current != "intent" or requested == "intent":
        raise ValueError(f"invalid effect state transition: {current} -> {requested}")
    return requested


@dataclass(frozen=True)
class RuntimeLease:
    runtime_id: str
    pid: int
    process_create_time: float
    started_at_ms: int
    heartbeat_at_ms: int
    closed_at_ms: int | None = None

    def __post_init__(self) -> None:
        _require_pattern(self.runtime_id, _RUNTIME_ID, "runtime id")
        if isinstance(self.pid, bool) or not isinstance(self.pid, int) or self.pid <= 0:
            raise ValueError("runtime pid must be a positive integer")
        if (
            isinstance(self.process_create_time, bool)
            or not isinstance(self.process_create_time, (int, float))
            or not math.isfinite(self.process_create_time)
            or self.process_create_time < 0
        ):
            raise ValueError("process creation time must be non-negative and finite")
        started = _require_timestamp(self.started_at_ms, "started_at_ms")
        heartbeat = _require_timestamp(self.heartbeat_at_ms, "heartbeat_at_ms")
        closed = _require_timestamp(self.closed_at_ms, "closed_at_ms", optional=True)
        if heartbeat < started or (closed is not None and closed < started):
            raise ValueError("runtime timestamps must be monotonic")


@dataclass(frozen=True)
class OperationRecord:
    operation_id: str
    runtime_id: str
    session_fingerprint: str
    kind: str
    state: OperationState
    program_counter: int
    created_at_ms: int
    updated_at_ms: int
    settled_at_ms: int | None = None
    outcome_code: str | None = None

    def __post_init__(self) -> None:
        _require_pattern(self.operation_id, _OPERATION_ID, "operation id")
        _require_pattern(self.runtime_id, _RUNTIME_ID, "runtime id")
        _require_pattern(self.session_fingerprint, _FINGERPRINT, "session fingerprint")
        _require_code(self.kind, "operation kind")
        if self.state not in OPERATION_STATES:
            raise ValueError("unknown operation state")
        _require_nonnegative_int(self.program_counter, "program counter")
        created = _require_timestamp(self.created_at_ms, "created_at_ms")
        updated = _require_timestamp(self.updated_at_ms, "updated_at_ms")
        settled = _require_timestamp(self.settled_at_ms, "settled_at_ms", optional=True)
        _require_code(self.outcome_code, "outcome code", optional=True)
        if updated < created or (settled is not None and settled < created):
            raise ValueError("operation timestamps must be monotonic")

    def as_dict(self) -> dict[str, object]:
        return {
            "operationId": self.operation_id,
            "runtimeId": self.runtime_id,
            "sessionFingerprint": self.session_fingerprint,
            "kind": self.kind,
            "state": self.state,
            "programCounter": self.program_counter,
            "createdAtMs": self.created_at_ms,
            "updatedAtMs": self.updated_at_ms,
            "settledAtMs": self.settled_at_ms,
            "outcomeCode": self.outcome_code,
        }


@dataclass(frozen=True)
class OperationRegister:
    operation_id: str
    key: str
    value: object
    program_counter: int
    updated_at_ms: int

    def __post_init__(self) -> None:
        _require_pattern(self.operation_id, _OPERATION_ID, "operation id")
        _require_code(self.key, "register key")
        _require_nonnegative_int(self.program_counter, "program counter")
        _require_timestamp(self.updated_at_ms, "updated_at_ms")
        sanitized = _sanitize_json(self.value, key=self.key)
        try:
            encoded = json.dumps(
                sanitized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise TypeError("operation register values must be JSON serializable") from error
        if len(encoded) > _REGISTER_MAX_BYTES:
            raise ValueError("operation register value exceeds 16 KiB")
        object.__setattr__(self, "value", _freeze_json(sanitized))

    def as_dict(self) -> dict[str, object]:
        return {
            "operationId": self.operation_id,
            "key": self.key,
            "value": _thaw_json(self.value),
            "programCounter": self.program_counter,
            "updatedAtMs": self.updated_at_ms,
        }


@dataclass(frozen=True)
class EffectRecord:
    effect_id: str
    operation_id: str
    runtime_id: str
    ordinal: int
    kind: str
    name: str
    fingerprint: str
    state: EffectState
    replay_policy: ReplayPolicy
    created_at_ms: int
    settled_at_ms: int | None = None
    outcome_code: str | None = None

    def __post_init__(self) -> None:
        _require_pattern(self.effect_id, _EFFECT_ID, "effect id")
        _require_pattern(self.operation_id, _OPERATION_ID, "operation id")
        _require_pattern(self.runtime_id, _RUNTIME_ID, "runtime id")
        _require_nonnegative_int(self.ordinal, "effect ordinal")
        _require_code(self.kind, "effect kind")
        _require_code(self.name, "effect name")
        _require_pattern(self.fingerprint, _FINGERPRINT, "effect fingerprint")
        if self.state not in EFFECT_STATES:
            raise ValueError("unknown effect state")
        if self.replay_policy != "never":
            raise ValueError("unknown replay policy")
        created = _require_timestamp(self.created_at_ms, "created_at_ms")
        settled = _require_timestamp(self.settled_at_ms, "settled_at_ms", optional=True)
        _require_code(self.outcome_code, "outcome code", optional=True)
        if settled is not None and settled < created:
            raise ValueError("effect timestamps must be monotonic")

    def as_dict(self) -> dict[str, object]:
        return {
            "effectId": self.effect_id,
            "operationId": self.operation_id,
            "runtimeId": self.runtime_id,
            "ordinal": self.ordinal,
            "kind": self.kind,
            "name": self.name,
            "fingerprint": self.fingerprint,
            "state": self.state,
            "replayPolicy": self.replay_policy,
            "createdAtMs": self.created_at_ms,
            "settledAtMs": self.settled_at_ms,
            "outcomeCode": self.outcome_code,
        }


@dataclass(frozen=True)
class UsageLedgerEntry:
    operation_id: str
    source_key: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost: float
    created_at_ms: int

    def __post_init__(self) -> None:
        _require_pattern(self.operation_id, _OPERATION_ID, "operation id")
        _require_pattern(self.source_key, _FINGERPRINT, "usage source key")
        if not isinstance(self.provider, str) or not self.provider or len(self.provider) > 256:
            raise ValueError("provider must be a bounded string")
        if not isinstance(self.model, str) or not self.model or len(self.model) > 512:
            raise ValueError("model must be a bounded string")
        for name in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        ):
            _require_nonnegative_int(getattr(self, name), name)
        if (
            isinstance(self.cost, bool)
            or not isinstance(self.cost, (int, float))
            or not math.isfinite(self.cost)
            or self.cost < 0
        ):
            raise ValueError("usage cost must be non-negative and finite")
        _require_timestamp(self.created_at_ms, "created_at_ms")


@dataclass(frozen=True)
class OperationSnapshot:
    operation: OperationRecord
    registers: tuple[OperationRegister, ...]
    effects: tuple[EffectRecord, ...]
    usage: tuple[UsageLedgerEntry, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "registers", tuple(self.registers))
        object.__setattr__(self, "effects", tuple(self.effects))
        object.__setattr__(self, "usage", tuple(self.usage))


__all__ = [
    "EFFECT_STATES",
    "OPERATION_STATES",
    "EffectRecord",
    "EffectState",
    "OperationMode",
    "OperationRecord",
    "OperationRegister",
    "OperationSnapshot",
    "OperationState",
    "ReplayPolicy",
    "RuntimeLease",
    "UsageLedgerEntry",
    "validate_effect_transition",
    "validate_operation_transition",
]
