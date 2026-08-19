from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_provider_leaf_modules_do_not_depend_on_runtime_or_concrete_transports() -> None:
    provider_root = ROOT / "travis" / "ai" / "providers"
    forbidden_prefixes = (
        "travis.application",
        "travis.coding_agent",
        "travis.session",
        "travis.tui",
        "travis.ai.providers.catalog",
        "travis.ai.providers.provider_request",
        "travis.ai.providers.transport_families",
        "travis.ai.providers.transport_registry",
        "travis.ai.providers.transports",
    )
    failures: list[str] = []
    for filename in ("provider_contracts.py", "provider_modes.py", "provider_profiles.py"):
        path = provider_root / filename
        assert path.is_file(), f"missing leaf provider owner: {path.relative_to(ROOT)}"
        for module in sorted(_imported_modules(path)):
            if module.startswith(forbidden_prefixes):
                failures.append(f"{filename}: {module}")

    assert failures == []


def test_provider_consumers_do_not_access_registry_privates() -> None:
    root = ROOT / "travis"
    forbidden = ("._models", "._registered_providers", "._fallback_api_key", "_DEFAULT_API_PROVIDER_REGISTRY")
    failures: list[str] = []
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if path.name in {"model_registry.py", "stream.py"} or relative == Path("ai/models.py"):
            continue
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                failures.append(f"{relative}: {token}")
    assert failures == []


def test_transport_registry_owns_mode_mapping_and_unsupported_family() -> None:
    provider_root = ROOT / "travis" / "ai" / "providers"
    registry = provider_root / "transport_registry.py"
    unsupported = provider_root / "transport_families" / "unsupported.py"

    assert registry.is_file()
    assert unsupported.is_file()
    assert "class UnsupportedTransport" in unsupported.read_text(encoding="utf-8")
    transports_text = (provider_root / "transports.py").read_text(encoding="utf-8")
    assert "_REGISTRY =" not in transports_text
    assert "_API_ALIASES =" not in transports_text


def test_extracted_transport_families_do_not_import_compatibility_module() -> None:
    family_root = ROOT / "travis" / "ai" / "providers" / "transport_families"
    failures: list[str] = []
    for filename in (
        "anthropic.py",
        "azure_responses.py",
        "bedrock.py",
        "chat_completions.py",
        "google.py",
        "mistral.py",
        "responses.py",
    ):
        path = family_root / filename
        assert path.is_file(), f"missing transport family owner: {path.relative_to(ROOT)}"
        if "travis.ai.providers.transports" in _imported_modules(path):
            failures.append(filename)

    assert failures == []
