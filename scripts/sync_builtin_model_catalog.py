#!/usr/bin/env python3
"""Refresh generated provider capabilities from authoritative provider metadata."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from travis.ai.catalog_generation import (  # noqa: E402
    apply_openrouter_capabilities,
    apply_pi_promotions,
    catalog_drift_to_dict,
    compare_pi_catalogs,
    load_promotion_set,
    validate_catalog,
)

DEFAULT_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def _write_catalog_atomic(path: Path, payload: dict[str, object]) -> None:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = path.stat().st_mode & 0o777 if path.exists() else None
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if existing_mode is not None:
            temporary.chmod(existing_mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_OPENROUTER_MODELS_URL)
    parser.add_argument(
        "--capacity-fixture",
        type=Path,
        help="Use a normalized {model: {contextWindow, maxTokens}} fixture instead of the network",
    )
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=ROOT / "travis" / "ai" / "builtin_models.json",
    )
    parser.add_argument("--pi-catalog", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--promotions", type=Path)
    args = parser.parse_args(argv)

    if args.check and args.pi_catalog is None:
        parser.error("--check requires --pi-catalog")
    if args.promotions is not None and args.pi_catalog is None:
        parser.error("--promotions requires --pi-catalog")
    if args.check and args.promotions is not None:
        parser.error("--check cannot be combined with --promotions")

    catalog = validate_catalog(json.loads(args.catalog.read_text(encoding="utf-8")))
    if args.pi_catalog is not None:
        reference = validate_catalog(
            json.loads(args.pi_catalog.read_text(encoding="utf-8"))
        )
        if args.check:
            report = [
                catalog_drift_to_dict(item)
                for item in compare_pi_catalogs(catalog, reference)
            ]
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
            return 0
        if args.promotions is None:
            parser.error("Pi apply mode requires --promotions")
        promotion_set = load_promotion_set(
            json.loads(args.promotions.read_text(encoding="utf-8"))
        )
        updated, changed = apply_pi_promotions(catalog, reference, promotion_set)
        _write_catalog_atomic(args.catalog, updated)
        print(json.dumps({"changed": list(changed)}, sort_keys=True))
        return 0

    if args.capacity_fixture is not None:
        fixture = json.loads(args.capacity_fixture.read_text(encoding="utf-8"))
        if not isinstance(fixture, dict):
            raise ValueError("capacity fixture must contain an object")
        payload = {
            "data": [
                {
                    "id": model_id,
                    "top_provider": {
                        "context_length": values.get("contextWindow"),
                        "max_completion_tokens": values.get("maxTokens"),
                    },
                }
                for model_id, values in fixture.items()
                if isinstance(model_id, str) and isinstance(values, dict)
            ]
        }
    else:
        request = urllib.request.Request(
            args.url,
            headers={"Accept": "application/json", "User-Agent": "travis234-catalog-generator"},
        )
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))

    refreshed, changed = apply_openrouter_capabilities(catalog, payload)
    _write_catalog_atomic(args.catalog, refreshed)
    print(f"refreshed {changed} OpenRouter model capability records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
