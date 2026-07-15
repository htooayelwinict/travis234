from __future__ import annotations

from travis.coding_agent.extensions import PINNED_PI_EXTENSION_EVENTS

from scripts.parity_contracts import PI_CONTRACTS, build_parity_report, validate_contracts


def test_pi_manifest_is_complete_and_all_evidence_resolves() -> None:
    errors = validate_contracts(PI_CONTRACTS)

    assert errors == ()
    assert len({entry.contract_id for entry in PI_CONTRACTS}) == len(PI_CONTRACTS)
    assert {entry.category for entry in PI_CONTRACTS} >= {
        "loop",
        "extension_event",
        "resource",
        "package",
        "cli",
        "session",
        "sdk",
    }


def test_pi_manifest_covers_every_pinned_extension_event_exactly_once() -> None:
    events = [
        entry.contract_id.removeprefix("pi.extension_event.")
        for entry in PI_CONTRACTS
        if entry.category == "extension_event"
    ]

    assert len(events) == 33
    assert set(events) == set(PINNED_PI_EXTENSION_EVENTS)


def test_pi_divergences_are_explicit_and_have_safety_evidence() -> None:
    divergences = [entry for entry in PI_CONTRACTS if entry.status == "divergence"]

    assert divergences
    assert all(entry.reason for entry in divergences)
    assert all(entry.safety_evidence for entry in divergences)
    assert validate_contracts(divergences, include_safety_evidence=True) == ()


def test_pi_report_is_machine_readable_and_has_no_unproved_entries() -> None:
    report = build_parity_report(pi_contracts=PI_CONTRACTS, hermes_contracts=())

    assert report["schema_version"] == 1
    assert report["summary"]["pi"]["total"] == len(PI_CONTRACTS)
    assert report["summary"]["pi"]["invalid"] == 0
    assert report["contracts"][0]["source"] == "pi"
