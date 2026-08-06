from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import sync_builtin_model_catalog


def _record(provider: str, model_id: str, *, context_window: int = 32_000) -> dict:
    return {
        "id": model_id,
        "name": model_id,
        "api": "openai-completions",
        "provider": provider,
        "baseUrl": f"https://{provider}.invalid/v1",
        "reasoning": False,
        "input": ["text"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": context_window,
        "maxTokens": 4_096,
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_pi_check_mode_reports_drift_without_writing(tmp_path: Path, capsys) -> None:
    catalog_path = tmp_path / "catalog.json"
    pi_path = tmp_path / "pi.json"
    current = {"direct": {"m": _record("direct", "m")}}
    reference = {"direct": {"m": _record("direct", "m", context_window=64_000)}}
    _write_json(catalog_path, current)
    _write_json(pi_path, reference)
    before = catalog_path.read_bytes()

    result = sync_builtin_model_catalog.main(
        [
            "--catalog",
            str(catalog_path),
            "--pi-catalog",
            str(pi_path),
            "--check",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert result == 0
    assert catalog_path.read_bytes() == before
    assert report[0]["kind"] == "field_difference"
    assert report[0]["field"] == "contextWindow"


def test_pi_apply_mode_changes_only_manifest_scope(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.json"
    pi_path = tmp_path / "pi.json"
    promotions_path = tmp_path / "promotions.json"
    current = {
        "direct": {
            "keep": _record("direct", "keep"),
            "retire": _record("direct", "retire"),
        }
    }
    reference = {
        "direct": {"keep": _record("direct", "keep", context_window=64_000)}
    }
    promotions = {
        "piCommit": "bde81c84405514c8b0f57c34405c152fb129c0ce",
        "promotions": [
            {
                "action": "retire",
                "provider": "direct",
                "model": "retire",
                "reason": "retired upstream",
                "evidence": "https://provider.invalid/deprecations",
            }
        ],
    }
    _write_json(catalog_path, current)
    catalog_path.chmod(0o640)
    _write_json(pi_path, reference)
    _write_json(promotions_path, promotions)

    result = sync_builtin_model_catalog.main(
        [
            "--catalog",
            str(catalog_path),
            "--pi-catalog",
            str(pi_path),
            "--promotions",
            str(promotions_path),
        ]
    )

    updated = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert result == 0
    assert catalog_path.stat().st_mode & 0o777 == 0o640
    assert updated["direct"]["keep"] == current["direct"]["keep"]
    assert "retire" not in updated["direct"]


def test_pi_apply_failure_leaves_catalog_bytes_unchanged(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.json"
    pi_path = tmp_path / "pi.json"
    promotions_path = tmp_path / "promotions.json"
    _write_json(catalog_path, {"direct": {"m": _record("direct", "m")}})
    _write_json(pi_path, [])
    _write_json(
        promotions_path,
        {
            "piCommit": "bde81c84405514c8b0f57c34405c152fb129c0ce",
            "promotions": [
                {
                    "action": "retire",
                    "provider": "direct",
                    "model": "m",
                    "reason": "retired upstream",
                    "evidence": "https://provider.invalid/deprecations",
                }
            ],
        },
    )
    before = catalog_path.read_bytes()

    with pytest.raises(ValueError, match="catalog"):
        sync_builtin_model_catalog.main(
            [
                "--catalog",
                str(catalog_path),
                "--pi-catalog",
                str(pi_path),
                "--promotions",
                str(promotions_path),
            ]
        )

    assert catalog_path.read_bytes() == before
