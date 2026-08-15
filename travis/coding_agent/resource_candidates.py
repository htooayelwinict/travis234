"""Pure candidates for non-extension coding-agent resources."""
from __future__ import annotations
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from types import MappingProxyType
from typing import Literal
from travis.coding_agent.capabilities import (
    CapabilityDiagnostic,
    CapabilityKind,
    CapabilityLoadContext,
    CapabilityProviderResult,
    CapabilityRecord,
    CapabilitySource,
)
from travis.coding_agent.config import get_packaged_skills_path
from travis.coding_agent.agent_roles import load_agent_roles
from travis.coding_agent.package_manager import PackageDiagnostic, ResolvedPaths
from travis.coding_agent.prompt_templates import load_prompt_templates
from travis.coding_agent.resource_discovery import collect_resource_files
from travis.coding_agent.resource_extensions import (
    ExtensionLoadRequest,
    ExtensionRuntimeLease,
    load_extension_runtime,
)
from travis.coding_agent.skills import ResourceDiagnostic, load_skills
from travis.coding_agent.source_info import SourceInfo, create_synthetic_source_info
from travis.coding_agent.themes import Theme
CONFIG_DIR_NAME = ".travis234"
_CONTEXT_FILE_NAMES = ("AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD")
@dataclass(frozen=True)
class ResourceContentRequest:
    cwd: str
    agent_dir: str
    project_trusted: bool
    resolved_paths: ResolvedPaths
    additional_skill_paths: tuple[str, ...]
    additional_prompt_paths: tuple[str, ...]
    additional_theme_paths: tuple[str, ...]
    no_context_files: bool
    no_skills: bool
    no_prompt_templates: bool
    no_themes: bool
    system_prompt_source: str | None
    append_system_prompt_source: tuple[str, ...] | None
    agents_files_override: Callable[[dict[str, list[dict[str, str]]]], dict[str, list[dict[str, str]]]] | None
    skills_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None
    prompts_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None
    themes_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None
    system_prompt_override: Callable[[str | None], str | None] | None
    append_system_prompt_override: Callable[[list[str]], list[str]] | None
    additional_role_paths: tuple[str, ...] = ()
    no_agent_roles: bool = False
@dataclass(frozen=True)
class ResourceContentCandidate:
    skills_result: dict[str, list[object]]
    prompts_result: dict[str, list[object]]
    themes_result: dict[str, list[object]]
    agents_files: tuple[dict[str, str], ...]
    system_prompt: str | None
    append_system_prompt: tuple[str, ...]
    package_diagnostics: tuple[object, ...]
    skill_paths: tuple[str, ...]
    prompt_paths: tuple[str, ...]
    theme_paths: tuple[str, ...]
    metadata_by_path: Mapping[str, dict[str, object]]
    agent_role_records: tuple[CapabilityRecord, ...] = ()
    agent_role_diagnostics: tuple[CapabilityDiagnostic, ...] = ()
    role_paths: tuple[str, ...] = ()
@dataclass(frozen=True)
class ResourceLoadCandidate:
    extensions: ExtensionRuntimeLease
    content: ResourceContentCandidate
    records: tuple[CapabilityRecord, ...]
    diagnostics: tuple[CapabilityDiagnostic, ...]
    def close(self) -> None:
        self.extensions.release()
    @classmethod
    def empty(
        cls,
        extensions: ExtensionRuntimeLease,
    ) -> ResourceLoadCandidate:
        content = ResourceContentCandidate(
            skills_result={"skills": [], "diagnostics": []},
            prompts_result={"prompts": [], "diagnostics": []},
            themes_result={"themes": [], "diagnostics": []},
            agents_files=(),
            system_prompt=None,
            append_system_prompt=(),
            package_diagnostics=(),
            skill_paths=(),
            prompt_paths=(),
            theme_paths=(),
            metadata_by_path=MappingProxyType({}),
            agent_role_records=(),
            agent_role_diagnostics=(),
            role_paths=(),
        )
        return cls(
            extensions=extensions,
            content=content,
            records=(),
            diagnostics=(),
        )
@dataclass(frozen=True)
class ResourceLoadRequest:
    mode: Literal["full", "extend"]
    content_request: ResourceContentRequest | None
    extension_request: ExtensionLoadRequest | None
    preloaded_extensions: ExtensionRuntimeLease | None
    current: ResourceLoadCandidate | None
    cwd: str
    skill_paths: tuple[str, ...] = ()
    prompt_paths: tuple[str, ...] = ()
    theme_paths: tuple[str, ...] = ()
    skills_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None = None
    prompts_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None = None
    themes_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None = None
    @classmethod
    def full(
        cls,
        content_request: ResourceContentRequest,
        extension_request: ExtensionLoadRequest,
        *,
        preloaded_extensions: ExtensionRuntimeLease | None = None,
    ) -> ResourceLoadRequest:
        return cls(
            mode="full",
            content_request=content_request,
            extension_request=extension_request,
            preloaded_extensions=preloaded_extensions,
            current=None,
            cwd=content_request.cwd,
        )
    @classmethod
    def extend(
        cls,
        current: ResourceLoadCandidate,
        *,
        cwd: str,
        skill_paths: tuple[str, ...],
        prompt_paths: tuple[str, ...],
        theme_paths: tuple[str, ...],
        skills_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None,
        prompts_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None,
        themes_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None,
    ) -> ResourceLoadRequest:
        return cls(
            mode="extend",
            content_request=None,
            extension_request=None,
            preloaded_extensions=None,
            current=current,
            cwd=cwd,
            skill_paths=skill_paths,
            prompt_paths=prompt_paths,
            theme_paths=theme_paths,
            skills_override=skills_override,
            prompts_override=prompts_override,
            themes_override=themes_override,
        )
    def build(self) -> ResourceLoadCandidate:
        if self.mode == "full":
            if self.content_request is None or self.extension_request is None:
                raise TypeError("full resource load requires content and extensions")
            extensions = _build_extension_candidate(
                self.extension_request,
                self.preloaded_extensions,
            )
            try:
                content = build_resource_content(self.content_request)
            except Exception:
                extensions.release()
                raise
        else:
            if self.current is None:
                raise TypeError("extended resource load requires a current candidate")
            extensions = self.current.extensions.retain()
            try:
                content = extend_resource_content(
                    self.current.content,
                    cwd=self.cwd,
                    skill_paths=self.skill_paths,
                    prompt_paths=self.prompt_paths,
                    theme_paths=self.theme_paths,
                    skills_override=self.skills_override,
                    prompts_override=self.prompts_override,
                    themes_override=self.themes_override,
                )
            except Exception:
                extensions.release()
                raise
        records = (
            *_extension_capability_records(extensions),
            *content_capability_records(content),
        )
        diagnostics = (
            *_extension_capability_diagnostics(extensions),
            *content_capability_diagnostics(content),
            *_package_capability_diagnostics(content.package_diagnostics),
        )
        return ResourceLoadCandidate(
            extensions=extensions,
            content=content,
            records=records,
            diagnostics=diagnostics,
        )
class DefaultResourceCapabilityProvider:
    name = "default-resources"
    priority = 0
    def load(self, context: CapabilityLoadContext) -> CapabilityProviderResult:
        request = context.data.get("resource_request")
        if not isinstance(request, ResourceLoadRequest):
            raise TypeError("resource_request must be a ResourceLoadRequest")
        candidate = request.build()
        return CapabilityProviderResult(
            candidate.records,
            candidate.diagnostics,
            candidate,
            candidate.close,
        )
def build_resource_content(
    request: ResourceContentRequest,
) -> ResourceContentCandidate:
    metadata_by_path = {
        str(Path(resource.path).expanduser().resolve()): resource.metadata
        for resources in (
            request.resolved_paths.skills,
            request.resolved_paths.prompts,
            request.resolved_paths.themes,
            request.resolved_paths.roles,
        )
        for resource in resources
    }
    skill_paths = _merge_paths(
        request.cwd,
        [item.path for item in request.resolved_paths.skills if item.enabled],
        (
            [*request.additional_skill_paths, get_packaged_skills_path()]
            if not request.no_skills
            else list(request.additional_skill_paths)
        ),
    )
    prompt_paths = _merge_paths(
        request.cwd,
        [item.path for item in request.resolved_paths.prompts if item.enabled],
        list(request.additional_prompt_paths),
    )
    theme_paths = _merge_paths(
        request.cwd,
        [item.path for item in request.resolved_paths.themes if item.enabled],
        list(request.additional_theme_paths),
    )
    role_paths = _merge_paths(
        request.cwd,
        [item.path for item in request.resolved_paths.roles if item.enabled],
        list(request.additional_role_paths),
    )
    skills_result = _load_skills_result(
        request.cwd,
        skill_paths,
        metadata_by_path,
        no_resources=request.no_skills,
        override=request.skills_override,
    )
    prompts_result = _load_prompts_result(
        request.cwd,
        prompt_paths,
        metadata_by_path,
        no_resources=request.no_prompt_templates,
        override=request.prompts_override,
    )
    themes_result = _load_themes_result(
        request.cwd,
        theme_paths,
        metadata_by_path,
        no_resources=request.no_themes,
        override=request.themes_override,
    )
    role_records, role_diagnostics = (
        ((), ())
        if request.no_agent_roles and not role_paths
        else load_agent_roles(tuple(role_paths), metadata_by_path=metadata_by_path)
    )
    agents_result = {
        "agentsFiles": (
            []
            if request.no_context_files
            else load_project_context_files(
                cwd=request.cwd, agent_dir=request.agent_dir
            )
        )
    }
    if request.agents_files_override is not None:
        agents_result = request.agents_files_override(agents_result)
    agents_files = tuple(agents_result["agentsFiles"])
    system_source = request.system_prompt_source or _discover_system_prompt_file(
        request.cwd,
        request.agent_dir,
        project_trusted=request.project_trusted,
    )
    system_prompt = _resolve_prompt_input(system_source, cwd=request.cwd)
    if request.system_prompt_override is not None:
        system_prompt = request.system_prompt_override(system_prompt)
    append_sources = request.append_system_prompt_source
    if append_sources is None:
        discovered_append = _discover_append_system_prompt_file(
            request.cwd,
            request.agent_dir,
            project_trusted=request.project_trusted,
        )
        append_sources = (discovered_append,) if discovered_append else ()
    append_system_prompt = [
        prompt
        for prompt in (
            _resolve_prompt_input(source, cwd=request.cwd)
            for source in append_sources
        )
        if prompt is not None
    ]
    if request.append_system_prompt_override is not None:
        append_system_prompt = request.append_system_prompt_override(
            append_system_prompt
        )
    return ResourceContentCandidate(
        skills_result=skills_result,
        prompts_result=prompts_result,
        themes_result=themes_result,
        agents_files=agents_files,
        system_prompt=system_prompt,
        append_system_prompt=tuple(append_system_prompt),
        package_diagnostics=tuple(request.resolved_paths.diagnostics),
        skill_paths=tuple(skill_paths),
        prompt_paths=tuple(prompt_paths),
        theme_paths=tuple(theme_paths),
        metadata_by_path=MappingProxyType(dict(metadata_by_path)),
        agent_role_records=role_records,
        agent_role_diagnostics=role_diagnostics,
        role_paths=tuple(role_paths),
    )
def extend_resource_content(
    current: ResourceContentCandidate,
    *,
    cwd: str,
    skill_paths: tuple[str, ...],
    prompt_paths: tuple[str, ...],
    theme_paths: tuple[str, ...],
    skills_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None,
    prompts_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None,
    themes_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None,
) -> ResourceContentCandidate:
    merged_skills = _merge_paths(cwd, list(current.skill_paths), list(skill_paths))
    merged_prompts = _merge_paths(cwd, list(current.prompt_paths), list(prompt_paths))
    merged_themes = _merge_paths(cwd, list(current.theme_paths), list(theme_paths))
    metadata_by_path = dict(current.metadata_by_path)
    return ResourceContentCandidate(
        skills_result=_load_skills_result(
            cwd,
            merged_skills,
            metadata_by_path,
            no_resources=False,
            override=skills_override,
        ),
        prompts_result=_load_prompts_result(
            cwd,
            merged_prompts,
            metadata_by_path,
            no_resources=False,
            override=prompts_override,
        ),
        themes_result=_load_themes_result(
            cwd,
            merged_themes,
            metadata_by_path,
            no_resources=False,
            override=themes_override,
        ),
        agents_files=current.agents_files,
        system_prompt=current.system_prompt,
        append_system_prompt=current.append_system_prompt,
        package_diagnostics=current.package_diagnostics,
        skill_paths=tuple(merged_skills),
        prompt_paths=tuple(merged_prompts),
        theme_paths=tuple(merged_themes),
        metadata_by_path=current.metadata_by_path,
        agent_role_records=current.agent_role_records,
        agent_role_diagnostics=current.agent_role_diagnostics,
        role_paths=current.role_paths,
    )
def content_capability_records(
    content: ResourceContentCandidate,
) -> tuple[CapabilityRecord, ...]:
    records: list[CapabilityRecord] = []
    for kind, result_name, result in (
        (CapabilityKind.SKILL, "skills", content.skills_result),
        (CapabilityKind.PROMPT_TEMPLATE, "prompts", content.prompts_result),
        (CapabilityKind.THEME, "themes", content.themes_result),
    ):
        for value in result[result_name]:
            source_info = value.source_info
            records.append(
                CapabilityRecord(
                    kind,
                    value.name,
                    value,
                    _capability_source(source_info),
                )
            )
    for context_file in content.agents_files:
        path = str(Path(context_file["path"]).expanduser().resolve())
        records.append(
            CapabilityRecord(
                CapabilityKind.CONTEXT_FILE,
                path,
                context_file,
                CapabilitySource("default-resources", path),
            )
        )
    records.extend(content.agent_role_records)
    return tuple(records)
def content_capability_diagnostics(
    content: ResourceContentCandidate,
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
            diagnostics.append(
                CapabilityDiagnostic(
                    "collision" if collision else "warning",
                    "default-resources",
                    "resource_collision" if collision else "resource_warning",
                    item.message,
                    CapabilitySource("default-resources", item.path),
                )
            )
    diagnostics.extend(content.agent_role_diagnostics)
    return tuple(diagnostics)
def load_context_file_from_dir(directory: str | Path) -> dict[str, str] | None:
    base = Path(directory).expanduser().resolve()
    for name in _CONTEXT_FILE_NAMES:
        candidate = base / name
        if not candidate.is_file():
            continue
        try:
            return {
                "path": str(candidate),
                "content": candidate.read_text(encoding="utf-8"),
            }
        except OSError:
            continue
    return None
def load_project_context_files(*, cwd: str, agent_dir: str) -> list[dict[str, str]]:
    resolved_cwd = Path(cwd).expanduser().resolve()
    resolved_agent_dir = Path(agent_dir).expanduser().resolve()
    project_root = _nearest_git_root(resolved_cwd)
    context_files: list[dict[str, str]] = []
    seen_paths: set[str] = set()
    global_context = load_context_file_from_dir(resolved_agent_dir)
    if global_context:
        context_files.append(global_context)
        seen_paths.add(global_context["path"])
    ancestor_context_files: list[dict[str, str]] = []
    current_dir = resolved_cwd
    while True:
        context_file = load_context_file_from_dir(current_dir)
        if context_file and context_file["path"] not in seen_paths:
            ancestor_context_files.insert(0, context_file)
            seen_paths.add(context_file["path"])
        if project_root is not None and current_dir == project_root:
            break
        parent = current_dir.parent
        if parent == current_dir:
            break
        current_dir = parent
    context_files.extend(ancestor_context_files)
    return context_files
def load_themes(
    theme_paths: list[str],
    *,
    cwd: str,
    metadata_by_path: dict[str, dict[str, object]] | None = None,
) -> dict[str, list[object]]:
    themes: list[Theme] = []
    diagnostics: list[ResourceDiagnostic] = []
    seen_names: set[str] = set()
    for path_text in theme_paths:
        path = _resolve_path(path_text, cwd)
        paths = collect_resource_files(path, "themes") if path.is_dir() else [path]
        for theme_file in paths:
            if not theme_file.exists() or theme_file.suffix != ".json":
                continue
            try:
                data = json.loads(theme_file.read_text(encoding="utf-8"))
                name = str(data.get("name") or theme_file.stem)
                if name in seen_names:
                    diagnostics.append(
                        ResourceDiagnostic(
                            type="collision",
                            message=f'name "{name}" collision',
                            path=str(theme_file),
                        )
                    )
                    continue
                seen_names.add(name)
                themes.append(
                    Theme(
                        name=name,
                        colors=dict(data.get("colors") or {}),
                        vars=dict(data.get("vars") or {}),
                        source_path=str(theme_file),
                        source_info=_source_info_for_path(
                            theme_file, metadata_by_path
                        ),
                    )
                )
            except (OSError, json.JSONDecodeError) as error:
                diagnostics.append(
                    ResourceDiagnostic(
                        type="warning",
                        message=str(error),
                        path=str(theme_file),
                    )
                )
    return {"themes": themes, "diagnostics": diagnostics}
def resource_paths(entries: list[dict[str, object]]) -> list[str]:
    paths: list[str] = []
    for entry in entries:
        path = entry.get("path")
        if isinstance(path, str):
            paths.append(path)
    return paths
def merge_paths(cwd: str, primary: list[str], additional: list[str]) -> list[str]:
    return _merge_paths(cwd, primary, additional)
def _load_skills_result(
    cwd: str,
    paths: list[str],
    metadata_by_path: dict[str, dict[str, object]],
    *,
    no_resources: bool,
    override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None,
) -> dict[str, list[object]]:
    result = (
        {"skills": [], "diagnostics": []}
        if no_resources and not paths
        else load_skills(paths, cwd=cwd, metadata_by_path=metadata_by_path)
    )
    return override(result) if override else result
def _load_prompts_result(
    cwd: str,
    paths: list[str],
    metadata_by_path: dict[str, dict[str, object]],
    *,
    no_resources: bool,
    override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None,
) -> dict[str, list[object]]:
    result = (
        {"prompts": [], "diagnostics": []}
        if no_resources and not paths
        else load_prompt_templates(paths, cwd=cwd, metadata_by_path=metadata_by_path)
    )
    return override(result) if override else result
def _load_themes_result(
    cwd: str,
    paths: list[str],
    metadata_by_path: dict[str, dict[str, object]],
    *,
    no_resources: bool,
    override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None,
) -> dict[str, list[object]]:
    result = (
        {"themes": [], "diagnostics": []}
        if no_resources and not paths
        else load_themes(paths, cwd=cwd, metadata_by_path=metadata_by_path)
    )
    return override(result) if override else result
def _nearest_git_root(start: Path) -> Path | None:
    current = start
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent
def _discover_system_prompt_file(
    cwd: str,
    agent_dir: str,
    *,
    project_trusted: bool,
) -> str | None:
    project_path = Path(cwd) / CONFIG_DIR_NAME / "SYSTEM.md"
    if project_trusted and project_path.exists():
        return str(project_path)
    global_path = Path(agent_dir) / "SYSTEM.md"
    return str(global_path) if global_path.exists() else None
def _discover_append_system_prompt_file(
    cwd: str,
    agent_dir: str,
    *,
    project_trusted: bool,
) -> str | None:
    project_path = Path(cwd) / CONFIG_DIR_NAME / "APPEND_SYSTEM.md"
    if project_trusted and project_path.exists():
        return str(project_path)
    global_path = Path(agent_dir) / "APPEND_SYSTEM.md"
    return str(global_path) if global_path.exists() else None
def _resolve_prompt_input(source: str | None, *, cwd: str) -> str | None:
    if not source:
        return None
    path = Path(source).expanduser()
    if not path.is_absolute():
        path = Path(cwd) / path
    if path.exists():
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return source
    return source
def _merge_paths(cwd: str, primary: list[str], additional: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for path in [*primary, *additional]:
        resolved = Path(path).expanduser()
        if not resolved.is_absolute():
            resolved = Path(cwd) / resolved
        resolved_text = str(resolved.resolve())
        if resolved_text in seen:
            continue
        seen.add(resolved_text)
        merged.append(resolved_text)
    return merged
def _resolve_path(path: str, cwd: str) -> Path:
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = Path(cwd) / resolved
    return resolved.resolve()
def _source_info_for_path(
    path: Path,
    metadata_by_path: dict[str, dict[str, object]] | None,
) -> SourceInfo:
    resolved = str(path.resolve())
    metadata = metadata_by_path.get(resolved) if metadata_by_path else None
    if metadata is None and metadata_by_path:
        for source_path, source_metadata in metadata_by_path.items():
            source_root = Path(source_path).resolve()
            try:
                path.resolve().relative_to(source_root)
                metadata = source_metadata
                break
            except ValueError:
                continue
    if metadata:
        return create_synthetic_source_info(
            str(path),
            source=str(metadata.get("source", "local")),
            scope=str(metadata.get("scope", "temporary")),
            origin=str(metadata.get("origin", "top-level")),
            base_dir=(
                metadata.get("baseDir")
                if isinstance(metadata.get("baseDir"), str)
                else None
            ),
        )
    return create_synthetic_source_info(
        str(path), source="local", base_dir=str(path.parent)
    )
def _capability_source(source_info: SourceInfo) -> CapabilitySource:
    return CapabilitySource(
        "default-resources",
        source_info.path,
        source_info.source,
        source_info.scope,
        source_info.origin,
    )
def _build_extension_candidate(
    request: ExtensionLoadRequest,
    preloaded: ExtensionRuntimeLease | None,
) -> ExtensionRuntimeLease:
    if preloaded is None:
        return load_extension_runtime(request)
    retained = preloaded.retain()
    try:
        return load_extension_runtime(request, preloaded=retained)
    except Exception:
        retained.release()
        raise
def _extension_capability_records(
    extensions: ExtensionRuntimeLease,
) -> tuple[CapabilityRecord, ...]:
    records: list[CapabilityRecord] = []
    entries = extensions.result.get("extensions")
    if not isinstance(entries, list):
        return ()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        path = str(entry["path"])
        records.append(
            CapabilityRecord(
                CapabilityKind.EXTENSION,
                path,
                entry,
                CapabilitySource("default-resources", path),
            )
        )
    return tuple(records)
def _extension_capability_diagnostics(
    extensions: ExtensionRuntimeLease,
) -> tuple[CapabilityDiagnostic, ...]:
    diagnostics: list[CapabilityDiagnostic] = []
    entries = extensions.result.get("errors")
    if not isinstance(entries, list):
        return ()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        message = entry.get("error")
        diagnostics.append(
            CapabilityDiagnostic(
                "error",
                "default-resources",
                "extension_load_failed",
                str(message or "extension load failed"),
                CapabilitySource(
                    "default-resources",
                    str(path) if isinstance(path, str) else None,
                ),
            )
        )
    return tuple(diagnostics)
def _package_capability_diagnostics(
    entries: tuple[object, ...],
) -> tuple[CapabilityDiagnostic, ...]:
    diagnostics: list[CapabilityDiagnostic] = []
    for entry in entries:
        if not isinstance(entry, PackageDiagnostic):
            continue
        diagnostics.append(
            CapabilityDiagnostic(
                "error" if entry.type == "error" else "warning",
                "default-resources",
                "package_resolution_warning",
                entry.message,
                CapabilitySource("default-resources", entry.source),
            )
        )
    return tuple(diagnostics)
__all__ = [
    "DefaultResourceCapabilityProvider",
    "ResourceContentCandidate",
    "ResourceContentRequest",
    "ResourceLoadCandidate",
    "ResourceLoadRequest",
    "build_resource_content",
    "content_capability_diagnostics",
    "content_capability_records",
    "extend_resource_content",
    "load_context_file_from_dir",
    "load_project_context_files",
    "load_themes",
    "merge_paths",
    "resource_paths",
]
