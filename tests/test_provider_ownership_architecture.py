from __future__ import annotations

from pathlib import Path


def test_provider_consumers_do_not_access_registry_privates() -> None:
    root = Path(__file__).parents[1] / "travis"
    forbidden = ("._models", "._registered_providers", "._fallback_api_key", "_DEFAULT_API_PROVIDER_REGISTRY")
    failures: list[str] = []
    for path in root.rglob("*.py"):
        if path.name in {"model_registry.py", "stream.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                failures.append(f"{path.relative_to(root)}: {token}")
    assert failures == []
