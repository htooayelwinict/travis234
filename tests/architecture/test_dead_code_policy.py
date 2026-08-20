"""Repository policy for proven-dead and deliberately dormant owners."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import travis.ai as ai_package
import travis.ai.types as ai_types
from travis.ai.providers.transport_families.chat_completions import (
    ChatCompletionsTransport,
)

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_ROOT = ROOT / "travis"
POLICY_PATH = Path(__file__).resolve()
ARTIFACT_GC_PATH = ROOT / "travis/coding_agent/artifact_gc.py"
RETENTION_DOCUMENT = ROOT / "docs/architecture/artifact-retention.md"


def _symbol_definitions_and_references(symbol: str) -> list[str]:
    matches: list[str] = []
    roots = (ROOT / "travis", ROOT / "tests", ROOT / "scripts", ROOT / "packages")
    for source_root in roots:
        for path in source_root.rglob("*.py"):
            if path.resolve() == POLICY_PATH:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if any(
                isinstance(node, (ast.Name, ast.Attribute, ast.ClassDef, ast.FunctionDef))
                and getattr(node, "id", getattr(node, "attr", getattr(node, "name", None)))
                == symbol
                for node in ast.walk(tree)
            ):
                matches.append(path.relative_to(ROOT).as_posix())
    return sorted(matches)


def test_proven_dead_ai_symbols_have_no_code_owner() -> None:
    assert not (ROOT / "travis/ai/oauth.py").exists()
    assert _symbol_definitions_and_references("oauth_credential_is_expired") == []
    assert _symbol_definitions_and_references("ModelRegistryLike") == []


def test_public_provider_response_is_retained_as_a_compatibility_contract() -> None:
    assert ai_package.ProviderResponse is ai_types.ProviderResponse
    assert ai_types.ProviderResponse.__module__ == "travis.ai.types"


def test_chat_transport_drops_dead_named_option_but_accepts_future_options() -> None:
    parameters = inspect.signature(ChatCompletionsTransport.build_kwargs).parameters

    assert "openrouter_min_coding_score" not in parameters
    assert any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def test_artifact_collection_is_documented_explicit_dormant_maintenance() -> None:
    module = ast.parse(
        ARTIFACT_GC_PATH.read_text(encoding="utf-8"),
        filename=str(ARTIFACT_GC_PATH),
    )
    module_docstring = ast.get_docstring(module) or ""
    document = RETENTION_DOCUMENT.read_text(encoding="utf-8")

    assert "docs/architecture/artifact-retention.md" in module_docstring
    assert "nonautomatic" in document.casefold()
    assert "dry run" in document.casefold()
    assert "fails closed" in document.casefold()
    assert "maintenance lock" in document.casefold()


def test_artifact_collector_has_no_automatic_runtime_caller_or_configuration() -> None:
    references: list[str] = []
    for path in PRODUCTION_ROOT.rglob("*.py"):
        if path == ARTIFACT_GC_PATH:
            continue
        source = path.read_text(encoding="utf-8")
        if "ArtifactGarbageCollector" in source or "artifact_gc" in source:
            references.append(path.relative_to(ROOT).as_posix())

    assert references == []

