from __future__ import annotations

from scripts.parity_contracts import HERMES_CONTRACTS, build_parity_report, validate_contracts


EXPECTED_HERMES_CONTRACTS = {
    "threshold_bands",
    "small_window_floor_fallback",
    "full_request_accounting",
    "prompt_only_provider_usage",
    "replay_tail_fields",
    "tail_budget",
    "protected_head_decay",
    "boundary_stripping",
    "cooldown",
    "fallback_wording",
    "auxiliary_capacity",
}


def test_hermes_manifest_is_complete_and_all_evidence_resolves() -> None:
    errors = validate_contracts(HERMES_CONTRACTS)

    assert errors == ()
    assert {entry.contract_id.removeprefix("hermes.compaction.") for entry in HERMES_CONTRACTS} == (
        EXPECTED_HERMES_CONTRACTS
    )


def test_hermes_contract_has_no_silent_divergence() -> None:
    divergences = [entry for entry in HERMES_CONTRACTS if entry.status == "divergence"]

    assert all(entry.reason and entry.safety_evidence for entry in divergences)
    assert validate_contracts(divergences, include_safety_evidence=True) == ()


def test_combined_report_counts_hermes_contracts() -> None:
    report = build_parity_report(pi_contracts=(), hermes_contracts=HERMES_CONTRACTS)

    assert report["summary"]["hermes"] == {
        "total": len(HERMES_CONTRACTS),
        "parity": len(HERMES_CONTRACTS),
        "divergence": 0,
        "invalid": 0,
    }
