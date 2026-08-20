from __future__ import annotations

import ast
import json
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


def _provider_module_name(path: Path, provider_root: Path) -> str:
    relative = path.relative_to(provider_root).with_suffix("")
    parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
    return ".".join(("travis", "ai", "providers", *parts))


def _provider_import_graph(provider_root: Path) -> dict[str, set[str]]:
    paths = tuple(sorted(provider_root.rglob("*.py")))
    modules = {_provider_module_name(path, provider_root): path for path in paths}
    graph: dict[str, set[str]] = {module: set() for module in modules}
    for module, path in modules.items():
        for imported in _imported_modules(path):
            if imported in modules and imported != module:
                graph[module].add(imported)
    return graph


def _strongly_connected_components(graph: dict[str, set[str]]) -> list[set[str]]:
    index = 0
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[set[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for neighbor in sorted(graph[node]):
            if neighbor not in indexes:
                visit(neighbor)
                lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
            elif neighbor in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[neighbor])
        if lowlinks[node] != indexes[node]:
            return
        component: set[str] = set()
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.add(member)
            if member == node:
                break
        components.append(component)

    for node in sorted(graph):
        if node not in indexes:
            visit(node)
    return components


def test_provider_module_import_graph_has_no_cycles() -> None:
    provider_root = ROOT / "travis" / "ai" / "providers"
    graph = _provider_import_graph(provider_root)

    cycles = [component for component in _strongly_connected_components(graph) if len(component) > 1]

    assert cycles == []


def test_transport_compatibility_module_is_bounded_and_declaration_free() -> None:
    path = ROOT / "travis" / "ai" / "providers" / "transports.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    assert len(path.read_text(encoding="utf-8").splitlines()) < 300
    assert not any(isinstance(node, (ast.ClassDef, ast.AsyncFunctionDef)) for node in tree.body)
    assert [node.name for node in tree.body if isinstance(node, ast.FunctionDef)] == []


def test_every_migrated_provider_owner_is_in_the_monotonic_pyright_scope() -> None:
    config = json.loads((ROOT / "pyrightconfig.json").read_text(encoding="utf-8"))
    included = set(config["include"])
    expected = {
        "travis/ai/providers/base.py",
        "travis/ai/providers/transport_families/__init__.py",
        "travis/ai/providers/transport_families/_shared.py",
        "travis/ai/providers/transport_families/anthropic.py",
        "travis/ai/providers/transport_families/azure_responses.py",
        "travis/ai/providers/transport_families/bedrock.py",
        "travis/ai/providers/transport_families/chat_completions.py",
        "travis/ai/providers/transport_families/google.py",
        "travis/ai/providers/transport_families/mistral.py",
        "travis/ai/providers/transport_families/responses.py",
        "travis/ai/providers/transport_families/unsupported.py",
        "travis/ai/providers/transport_registry.py",
        "travis/ai/providers/transports.py",
    }

    assert expected <= included
