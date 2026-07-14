#!/usr/bin/env python3
"""Refresh generated provider capabilities from authoritative provider metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from travis.ai.catalog_generation import apply_openrouter_capabilities  # noqa: E402

DEFAULT_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_OPENROUTER_MODELS_URL)
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument(
        "--catalog",
        type=Path,
        default=ROOT / "travis" / "ai" / "builtin_models.json",
    )
    args = parser.parse_args()

    request = urllib.request.Request(
        args.url,
        headers={"Accept": "application/json", "User-Agent": "travis234-catalog-generator"},
    )
    with urllib.request.urlopen(request, timeout=args.timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    refreshed, changed = apply_openrouter_capabilities(catalog, payload)
    args.catalog.write_text(
        json.dumps(refreshed, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(f"refreshed {changed} OpenRouter model capability records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
