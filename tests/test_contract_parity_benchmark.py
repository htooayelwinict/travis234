from __future__ import annotations

import json
import math
from pathlib import Path
import subprocess
import sys

import pytest

from benchmarks.contract_parity_hotpaths import (
    DECISION_CANDIDATE_REJECTED,
    DECISION_PACKAGING_REVIEW,
    DECISION_RETAIN_PYTHON,
    build_seeded_inputs,
    decide_native_gate,
    measure_case,
    run_benchmarks,
    summarize_samples,
)


ROOT = Path(__file__).parents[1]
BENCHMARK = ROOT / "benchmarks" / "contract_parity_hotpaths.py"


def _baseline(*, wall_share: float = 0.05) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "hotpaths": {
            "policy_decision": {
                "samplesSeconds": [0.02, 0.02, 0.02],
                "medianSeconds": 0.02,
                "coefficientOfVariation": 0.0,
                "wallShare": wall_share,
                "correctnessPassed": True,
            }
        },
    }


def _candidate(
    samples: list[float] | None = None,
    *,
    correctness: bool = True,
    conformance: bool = True,
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "target": "policy_decision",
        "samplesSeconds": samples or [0.01, 0.01, 0.01],
        "correctnessPassed": correctness,
        "conformancePassed": conformance,
    }


def test_missing_candidate_retains_python() -> None:
    assert decide_native_gate(_baseline(), None) == DECISION_RETAIN_PYTHON


def test_candidate_advances_at_every_exact_boundary() -> None:
    candidate = _candidate([0.0085, 0.0115])

    assert summarize_samples(candidate["samplesSeconds"])["coefficientOfVariation"] == pytest.approx(0.15)
    assert decide_native_gate(_baseline(wall_share=0.05), candidate) == DECISION_PACKAGING_REVIEW


@pytest.mark.parametrize(
    ("baseline", "candidate"),
    [
        (_baseline(wall_share=0.049999), _candidate()),
        (_baseline(), _candidate([0.010001, 0.010001, 0.010001])),
        (_baseline(), _candidate([0.0084, 0.0116])),
        (_baseline(), _candidate(correctness=False)),
        (_baseline(), _candidate(conformance=False)),
    ],
)
def test_candidate_below_any_gate_is_rejected(
    baseline: dict[str, object], candidate: dict[str, object]
) -> None:
    assert decide_native_gate(baseline, candidate) == DECISION_CANDIDATE_REJECTED


@pytest.mark.parametrize(
    "samples",
    [[], [0.0], [-1.0], [math.nan], [math.inf], ["0.1"], [True]],
)
def test_invalid_candidate_samples_are_rejected(samples: list[object]) -> None:
    candidate = _candidate()
    candidate["samplesSeconds"] = samples

    assert decide_native_gate(_baseline(), candidate) == DECISION_CANDIDATE_REJECTED


def test_unknown_candidate_target_and_invalid_baseline_are_rejected() -> None:
    unknown = _candidate()
    unknown["target"] = "not_a_hotpath"
    invalid_baseline = _baseline()
    invalid_baseline["hotpaths"]["policy_decision"]["samplesSeconds"] = [math.nan]

    assert decide_native_gate(_baseline(), unknown) == DECISION_CANDIDATE_REJECTED
    assert decide_native_gate(invalid_baseline, _candidate()) == DECISION_CANDIDATE_REJECTED


def test_measure_case_excludes_warmups_from_samples() -> None:
    invocations: list[int] = []
    clock_values = iter([1.0, 1.2, 2.0, 2.3])

    summary = measure_case(
        "probe",
        lambda: invocations.append(len(invocations)),
        rounds=2,
        warmups=3,
        clock=lambda: next(clock_values),
    )

    assert len(invocations) == 5
    assert summary["samplesSeconds"] == pytest.approx([0.2, 0.3])
    assert summary["medianSeconds"] == pytest.approx(0.25)
    assert summary["correctnessPassed"] is True


def test_seeded_inputs_are_repeatable_and_seed_sensitive() -> None:
    first = build_seeded_inputs(234)
    again = build_seeded_inputs(234)
    changed = build_seeded_inputs(235)

    assert first == again
    assert first["digest"] != changed["digest"]
    assert first["artifactBytes"] == changed["artifactBytes"]


def test_baseline_report_has_deterministic_bounded_schema(tmp_path: Path) -> None:
    report = run_benchmarks(rounds=2, warmups=1, seed=234, temporary_root=tmp_path)

    assert report["schemaVersion"] == 1
    assert report["decision"] == DECISION_RETAIN_PYTHON
    assert report["seed"] == 234
    assert report["rounds"] == 2
    assert report["warmups"] == 1
    assert set(report["environment"]) == {"python", "platform", "machine", "commit"}
    assert set(report["hotpaths"]) == {
        "artifact_verification",
        "policy_decision",
        "lsp_frame_parsing",
        "supervisor_snapshot",
        "operation_journal_write",
        "memory_recall",
        "mcp_result_conversion",
    }
    assert report["nativeModulesImported"] == []
    assert report["mixedWorkflow"]["correctnessPassed"] is True
    assert {
        name: item["workUnits"] for name, item in report["hotpaths"].items()
    } == {
        "artifact_verification": 1,
        "policy_decision": 256,
        "lsp_frame_parsing": 32,
        "supervisor_snapshot": 128,
        "operation_journal_write": 8,
        "memory_recall": 64,
        "mcp_result_conversion": 32,
    }
    assert all(
        len(item["samplesSeconds"]) == 2
        and item["medianSeconds"] > 0
        and item["coefficientOfVariation"] >= 0
        and item["wallShare"] >= 0
        and item["correctnessPassed"] is True
        for item in report["hotpaths"].values()
    )
    encoded = json.dumps(report, sort_keys=True, allow_nan=False)
    assert str(ROOT) not in encoded
    assert str(tmp_path) not in encoded


def test_json_cli_runs_without_optional_native_imports_or_writes(tmp_path: Path) -> None:
    before = set(tmp_path.iterdir())
    result = subprocess.run(
        [
            sys.executable,
            str(BENCHMARK),
            "--rounds",
            "1",
            "--warmups",
            "0",
            "--seed",
            "234",
            "--json",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
        env={"PATH": str(Path(sys.executable).parent)},
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["decision"] == DECISION_RETAIN_PYTHON
    assert payload["nativeModulesImported"] == []
    assert set(tmp_path.iterdir()) == before
