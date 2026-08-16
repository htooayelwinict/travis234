"""Capability records and diagnostics projected from loaded resources."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from travis.coding_agent.capabilities import (
    CapabilityDiagnostic,
    CapabilityKind,
    CapabilityRecord,
    CapabilitySource,
)
from travis.coding_agent.package_manager import PackageDiagnostic
from travis.coding_agent.skills import ResourceDiagnostic
from travis.coding_agent.source_info import SourceInfo


class ResourceContentView(Protocol):
    skills_result: dict[str, list[object]]
    prompts_result: dict[str, list[object]]
    themes_result: dict[str, list[object]]
    agents_files: tuple[dict[str, str], ...]
    agent_role_records: tuple[CapabilityRecord, ...]
    agent_role_diagnostics: tuple[CapabilityDiagnostic, ...]


class ExtensionResultView(Protocol):
    result: dict[str, object]


def content_capability_records(
    content: ResourceContentView,
) -> tuple[CapabilityRecord, ...]:
    records: list[CapabilityRecord] = []
    for kind, result_name, result in (
        (CapabilityKind.SKILL, "skills", content.skills_result),
        (CapabilityKind.PROMPT_TEMPLATE, "prompts", content.prompts_result),
        (CapabilityKind.THEME, "themes", content.themes_result),
    ):
        for value in result[result_name]:
            source_info = value.source_info
            records.append(CapabilityRecord(
                kind,
                value.name,
                value,
                _capability_source(source_info),
            ))
    for context_file in content.agents_files:
        path = str(Path(context_file["path"]).expanduser().resolve())
        records.append(CapabilityRecord(
            CapabilityKind.CONTEXT_FILE,
            path,
            context_file,
            CapabilitySource("default-resources", path),
        ))
    records.extend(content.agent_role_records)
    return tuple(records)


def content_capability_diagnostics(
    content: ResourceContentView,
) -> tuple[CapabilityDiagnostic, ...]:
    diagnostics: list[CapabilityDiagnostic] = []
    for result in (
        content.skills_result,
        content.prompts_result,
        content.themes_result,
    ):
        for item in result["diagnostics"]:
            if not isinstance(item, ResourceDiagnostic):
                continue
            collision = item.type == "collision"
            diagnostics.append(CapabilityDiagnostic(
                "collision" if collision else "warning",
                "default-resources",
                "resource_collision" if collision else "resource_warning",
                item.message,
                CapabilitySource("default-resources", item.path),
            ))
    diagnostics.extend(content.agent_role_diagnostics)
    return tuple(diagnostics)


def extension_capability_records(
    extensions: ExtensionResultView,
) -> tuple[CapabilityRecord, ...]:
    records: list[CapabilityRecord] = []
    entries = extensions.result.get("extensions")
    if not isinstance(entries, list):
        return ()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        path = str(entry["path"])
        records.append(CapabilityRecord(
            CapabilityKind.EXTENSION,
            path,
            entry,
            CapabilitySource("default-resources", path),
        ))
    return tuple(records)


def extension_capability_diagnostics(
    extensions: ExtensionResultView,
) -> tuple[CapabilityDiagnostic, ...]:
    diagnostics: list[CapabilityDiagnostic] = []
    entries = extensions.result.get("errors")
    if not isinstance(entries, list):
        return ()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        diagnostics.append(CapabilityDiagnostic(
            "error",
            "default-resources",
            "extension_load_failed",
            str(entry.get("error") or "extension load failed"),
            CapabilitySource(
                "default-resources",
                str(path) if isinstance(path, str) else None,
            ),
        ))
    return tuple(diagnostics)


def package_capability_diagnostics(
    entries: tuple[object, ...],
) -> tuple[CapabilityDiagnostic, ...]:
    return tuple(
        CapabilityDiagnostic(
            "error" if entry.type == "error" else "warning",
            "default-resources",
            "package_resolution_warning",
            entry.message,
            CapabilitySource("default-resources", entry.source),
        )
        for entry in entries
        if isinstance(entry, PackageDiagnostic)
    )


def _capability_source(source_info: SourceInfo) -> CapabilitySource:
    return CapabilitySource(
        "default-resources",
        source_info.path,
        source_info.source,
        source_info.scope,
        source_info.origin,
    )


__all__ = [
    "content_capability_diagnostics",
    "content_capability_records",
    "extension_capability_diagnostics",
    "extension_capability_records",
    "package_capability_diagnostics",
]
