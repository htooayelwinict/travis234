from __future__ import annotations

from typing import get_args

import pytest

from travis.coding_agent.operations import (
    EffectRecord,
    OperationRecord,
    OperationRegister,
    OperationSnapshot,
    ReplayPolicy,
    RuntimeLease,
    UsageLedgerEntry,
    validate_effect_transition,
    validate_operation_transition,
)


OP_ID = "op_" + "a" * 32
EFFECT_ID = "effect_" + "b" * 32
RUNTIME_ID = "c" * 32
FINGERPRINT = "d" * 64


def test_replay_policy_has_no_safe_value_in_first_release() -> None:
    assert get_args(ReplayPolicy) == ("never",)


def test_operation_values_serialize_canonical_states() -> None:
    operation = OperationRecord(
        operation_id=OP_ID,
        runtime_id=RUNTIME_ID,
        session_fingerprint=FINGERPRINT,
        kind="turn",
        state="running",
        program_counter=2,
        created_at_ms=10,
        updated_at_ms=12,
    )
    effect = EffectRecord(
        effect_id=EFFECT_ID,
        operation_id=OP_ID,
        runtime_id=RUNTIME_ID,
        ordinal=1,
        kind="tool",
        name="read",
        fingerprint=FINGERPRINT,
        state="intent",
        replay_policy="never",
        created_at_ms=11,
    )
    snapshot = OperationSnapshot(operation, (), (effect,), ())

    assert operation.as_dict()["state"] == "running"
    assert effect.as_dict()["replayPolicy"] == "never"
    assert snapshot.effects == (effect,)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("operation_id", "op_NOT_HEX"),
        ("runtime_id", "short"),
        ("session_fingerprint", "A" * 64),
        ("kind", "turn with spaces"),
        ("state", "unknown"),
        ("program_counter", -1),
        ("created_at_ms", -1),
    ],
)
def test_operation_record_rejects_invalid_fields(field: str, value: object) -> None:
    values: dict[str, object] = {
        "operation_id": OP_ID,
        "runtime_id": RUNTIME_ID,
        "session_fingerprint": FINGERPRINT,
        "kind": "turn",
        "state": "running",
        "program_counter": 0,
        "created_at_ms": 1,
        "updated_at_ms": 1,
    }
    values[field] = value

    with pytest.raises((TypeError, ValueError)):
        OperationRecord(**values)


def test_effect_and_runtime_values_are_strict() -> None:
    with pytest.raises(ValueError, match="effect id"):
        EffectRecord(
            "effect_bad", OP_ID, RUNTIME_ID, 0, "tool", "read", FINGERPRINT,
            "intent", "never", 1,
        )
    with pytest.raises(ValueError, match="replay"):
        EffectRecord(
            EFFECT_ID, OP_ID, RUNTIME_ID, 0, "tool", "read", FINGERPRINT,
            "intent", "safe", 1,
        )
    with pytest.raises(ValueError, match="pid"):
        RuntimeLease(RUNTIME_ID, 0, 1.0, 1, 1)

    named = EffectRecord(
        EFFECT_ID,
        OP_ID,
        RUNTIME_ID,
        1,
        "tool",
        "extension-probe",
        FINGERPRINT,
        "intent",
        "never",
        1,
        effect_classes=("write", "network"),
    )
    assert named.effect_classes == ("write", "network")


def test_register_values_are_redacted_bounded_and_defensively_frozen() -> None:
    source = {
        "phase": "provider_intent",
        "nested": [{"apiToken": "DO_NOT_STORE", "count": 2}],
    }
    register = OperationRegister(OP_ID, "phase", source, 2, 10)
    source["phase"] = "mutated"
    source["nested"][0]["count"] = 99

    encoded = register.as_dict()["value"]
    assert encoded == {
        "nested": [{"apiToken": "[redacted]", "count": 2}],
        "phase": "provider_intent",
    }
    with pytest.raises(TypeError):
        register.value["phase"] = "cannot mutate"  # type: ignore[index]
    with pytest.raises(ValueError, match="16 KiB"):
        OperationRegister(OP_ID, "large", "x" * (17 * 1024), 1, 1)
    with pytest.raises(TypeError, match="JSON"):
        OperationRegister(OP_ID, "bad", {"set": {1}}, 1, 1)


def test_usage_rejects_negative_counters_and_cost() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        UsageLedgerEntry(
            OP_ID, FINGERPRINT, "openrouter", "model", -1, 0, 0, 0, 0.0, 1
        )
    with pytest.raises(ValueError, match="non-negative"):
        UsageLedgerEntry(
            OP_ID, FINGERPRINT, "openrouter", "model", 1, 0, 0, 0, -0.1, 1
        )


def test_state_transitions_are_terminal_and_idempotent() -> None:
    assert validate_operation_transition("running", "settled") == "settled"
    assert validate_operation_transition("failed", "failed") == "failed"
    assert validate_effect_transition("intent", "uncertain") == "uncertain"
    with pytest.raises(ValueError, match="transition"):
        validate_operation_transition("settled", "failed")
    with pytest.raises(ValueError, match="transition"):
        validate_effect_transition("failed", "settled")
