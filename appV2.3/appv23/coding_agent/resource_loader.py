"""Resource loader subset ported from Pi coding-agent resource-loader."""

from __future__ import annotations

from collections.abc import Callable
import inspect
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from appv23.coding_agent.event_bus import EventBusController, create_event_bus
from appv23.coding_agent.extensions import ExtensionRunner
from appv23.coding_agent.settings_manager import SettingsManager
from appv23.coding_agent.source_info import SourceInfo, create_synthetic_source_info

CONFIG_DIR_NAME = ".pi"
_CONTEXT_FILE_NAMES = ("AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD")
_RESOURCE_TYPES = ("extensions", "skills", "prompts", "themes")


@dataclass
class ResourceDiagnostic:
    type: str
    message: str
    path: str
    collision: dict[str, object] | None = None


@dataclass
class ResolvedResource:
    path: str
    enabled: bool
    metadata: dict[str, object]


@dataclass
class ResolvedPaths:
    extensions: list[ResolvedResource] = field(default_factory=list)
    skills: list[ResolvedResource] = field(default_factory=list)
    prompts: list[ResolvedResource] = field(default_factory=list)
    themes: list[ResolvedResource] = field(default_factory=list)


@dataclass
class Skill:
    name: str
    description: str
    file_path: str
    base_dir: str
    source_info: SourceInfo
    disable_model_invocation: bool = False
    allowed_tools: tuple[str, ...] = ()

    @property
    def filePath(self) -> str:
        return self.file_path

    @property
    def baseDir(self) -> str:
        return self.base_dir

    @property
    def sourceInfo(self) -> SourceInfo:
        return self.source_info

    @property
    def disableModelInvocation(self) -> bool:
        return self.disable_model_invocation

    @property
    def allowedTools(self) -> tuple[str, ...]:
        return self.allowed_tools


@dataclass
class PromptTemplate:
    name: str
    description: str
    content: str
    source_info: SourceInfo
    file_path: str
    argument_hint: str | None = None

    @property
    def argumentHint(self) -> str | None:
        return self.argument_hint

    @property
    def sourceInfo(self) -> SourceInfo:
        return self.source_info

    @property
    def filePath(self) -> str:
        return self.file_path


@dataclass
class Theme:
    name: str
    colors: dict[str, object]
    vars: dict[str, object]
    source_path: str
    source_info: SourceInfo

    @property
    def sourcePath(self) -> str:
        return self.source_path

    @property
    def sourceInfo(self) -> SourceInfo:
        return self.source_info


class DefaultPackageManager:
    """Local package/resource resolver subset of Pi's DefaultPackageManager."""

    def __init__(
        self,
        *,
        cwd: str,
        agent_dir: str,
        package_paths: list[str] | None = None,
        project_trusted: bool = True,
    ) -> None:
        self.cwd = str(Path(cwd).expanduser().resolve())
        self.agent_dir = str(Path(agent_dir).expanduser().resolve())
        self.package_paths = list(package_paths or [])
        self.project_trusted = project_trusted

    def resolve(self) -> ResolvedPaths:
        resolved = ResolvedPaths()
        for package_path in self.package_paths:
            package_root = _resolve_path(package_path, self.cwd)
            if package_root.exists():
                self._collect_package_resources(package_root, resolved)
        self._add_auto_discovered_resources(resolved)
        return resolved

    def _collect_package_resources(self, package_root: Path, resolved: ResolvedPaths) -> None:
        manifest = _read_pi_manifest(package_root)
        metadata = {"source": "local", "scope": "temporary", "origin": "package", "baseDir": str(package_root)}
        for resource_type in _RESOURCE_TYPES:
            entries = manifest.get(resource_type) if manifest else None
            if entries is not None:
                paths = _collect_manifest_entries(package_root, entries, resource_type)
            else:
                paths = _collect_resource_files(package_root / resource_type, resource_type)
            target = getattr(resolved, resource_type)
            for path in paths:
                target.append(ResolvedResource(path=str(path), enabled=True, metadata=metadata))

    def _add_auto_discovered_resources(self, resolved: ResolvedPaths) -> None:
        global_base = Path(self.agent_dir)
        project_base = Path(self.cwd) / CONFIG_DIR_NAME
        user_agents_skills_dir = Path.home() / ".agents" / "skills"
        pairs: list[tuple[Path, str, str]] = [(global_base, "user", "auto")]
        if self.project_trusted:
            pairs.append((project_base, "project", "auto"))
        for base, scope, source in pairs:
            metadata = {"source": source, "scope": scope, "origin": "top-level", "baseDir": str(base)}
            resource_types = ("prompts", "themes") if base == global_base else ("skills", "prompts", "themes")
            for resource_type in resource_types:
                paths = _collect_resource_files(base / resource_type, resource_type)
                target = getattr(resolved, resource_type)
                for path in paths:
                    target.append(ResolvedResource(path=str(path), enabled=True, metadata=metadata))

        if self.project_trusted:
            for agents_skills_dir in _collect_ancestor_agents_skill_dirs(Path(self.cwd)):
                if agents_skills_dir.resolve() == user_agents_skills_dir.resolve():
                    continue
                metadata = {
                    "source": "auto",
                    "scope": "project",
                    "origin": "top-level",
                    "baseDir": str(agents_skills_dir.parent),
                }
                for path in _collect_resource_files(agents_skills_dir, "skills"):
                    resolved.skills.append(ResolvedResource(path=str(path), enabled=True, metadata=metadata))

        user_agents_metadata = {
            "source": "auto",
            "scope": "user",
            "origin": "top-level",
            "baseDir": str(user_agents_skills_dir.parent),
        }
        for path in _collect_resource_files(user_agents_skills_dir, "skills"):
            resolved.skills.append(ResolvedResource(path=str(path), enabled=True, metadata=user_agents_metadata))


def load_context_file_from_dir(directory: str | Path) -> dict[str, str] | None:
    base = Path(directory).expanduser().resolve()
    for name in _CONTEXT_FILE_NAMES:
        candidate = base / name
        if candidate.exists():
            try:
                return {"path": str(candidate), "content": candidate.read_text(encoding="utf-8")}
            except OSError:
                return None
    return None


def load_project_context_files(*, cwd: str, agent_dir: str) -> list[dict[str, str]]:
    resolved_cwd = Path(cwd).expanduser().resolve()
    resolved_agent_dir = Path(agent_dir).expanduser().resolve()
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

        parent = current_dir.parent
        if parent == current_dir:
            break
        current_dir = parent

    context_files.extend(ancestor_context_files)
    return context_files


class DefaultResourceLoader:
    """Small reloadable resource cache matching the Pi ResourceLoader surface."""

    def __init__(
        self,
        *,
        cwd: str,
        agent_dir: str | None = None,
        no_context_files: bool = False,
        project_trusted: bool | None = None,
        settings_manager: object | None = None,
        system_prompt: str | None = None,
        append_system_prompt: list[str] | None = None,
        event_bus: EventBusController | None = None,
        eventBus: EventBusController | None = None,
        additional_extension_paths: list[str] | None = None,
        extension_factories: list[Callable[[ExtensionRunner], object]] | None = None,
        no_extensions: bool = False,
        extensions_override: Callable[[dict[str, object]], dict[str, object]] | None = None,
        package_paths: list[str] | None = None,
        additional_skill_paths: list[str] | None = None,
        additional_prompt_template_paths: list[str] | None = None,
        additional_theme_paths: list[str] | None = None,
        no_skills: bool = False,
        no_prompt_templates: bool = False,
        no_themes: bool = False,
        agents_files_override: Callable[[dict[str, list[dict[str, str]]]], dict[str, list[dict[str, str]]]]
        | None = None,
        skills_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None = None,
        prompts_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None = None,
        themes_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None = None,
        system_prompt_override: Callable[[str | None], str | None] | None = None,
        append_system_prompt_override: Callable[[list[str]], list[str]] | None = None,
    ) -> None:
        self.cwd = str(Path(cwd).expanduser().resolve())
        self.agent_dir = str(Path(agent_dir).expanduser().resolve()) if agent_dir else str(Path.home() / ".pi" / "agent")
        self.settings_manager = settings_manager or SettingsManager.create(self.cwd, self.agent_dir)
        self.event_bus = event_bus or eventBus or create_event_bus()
        self.eventBus = self.event_bus
        self.no_context_files = no_context_files
        self.project_trusted = (
            _settings_value(self.settings_manager, "isProjectTrusted", "is_project_trusted")
            if project_trusted is None
            else project_trusted
        )
        self.project_trusted = True if self.project_trusted is None else bool(self.project_trusted)
        self.system_prompt_source = system_prompt
        self.append_system_prompt_source = append_system_prompt
        self.additional_extension_paths = list(additional_extension_paths or [])
        self.extension_factories = list(extension_factories or [])
        self.no_extensions = no_extensions
        self.extensions_override = extensions_override
        self.package_paths = _settings_package_paths(self.settings_manager) + list(package_paths or [])
        self.additional_skill_paths = _settings_list(self.settings_manager, "getSkillPaths") + list(
            additional_skill_paths or []
        )
        self.additional_prompt_template_paths = _settings_list(
            self.settings_manager,
            "getPromptTemplatePaths",
        ) + list(additional_prompt_template_paths or [])
        self.additional_theme_paths = _settings_list(self.settings_manager, "getThemePaths") + list(
            additional_theme_paths or []
        )
        self.no_skills = no_skills
        self.no_prompt_templates = no_prompt_templates
        self.no_themes = no_themes
        self.agents_files_override = agents_files_override
        self.skills_override = skills_override
        self.prompts_override = prompts_override
        self.themes_override = themes_override
        self.system_prompt_override = system_prompt_override
        self.append_system_prompt_override = append_system_prompt_override
        self.package_manager = DefaultPackageManager(
            cwd=self.cwd,
            agent_dir=self.agent_dir,
            package_paths=self.package_paths,
            project_trusted=self.project_trusted,
        )

        self.extensions_result: dict[str, object] = {"extensions": [], "errors": [], "runtime": ExtensionRunner(cwd=self.cwd)}
        self.skills_result: dict[str, list[object]] = {"skills": [], "diagnostics": []}
        self.prompts_result: dict[str, list[object]] = {"prompts": [], "diagnostics": []}
        self.themes_result: dict[str, list[object]] = {"themes": [], "diagnostics": []}
        self.agents_files: list[dict[str, str]] = []
        self.system_prompt: str | None = None
        self.append_system_prompt: list[str] = []
        self.last_skill_paths: list[str] = []
        self.last_prompt_paths: list[str] = []
        self.last_theme_paths: list[str] = []

    def get_extensions(self) -> dict[str, object]:
        return self.extensions_result

    getExtensions = get_extensions

    def get_skills(self) -> dict[str, list[object]]:
        return self.skills_result

    getSkills = get_skills

    def get_prompts(self) -> dict[str, list[object]]:
        return self.prompts_result

    getPrompts = get_prompts

    def get_themes(self) -> dict[str, list[object]]:
        return self.themes_result

    getThemes = get_themes

    def get_agents_files(self) -> dict[str, list[dict[str, str]]]:
        return {"agentsFiles": self.agents_files}

    getAgentsFiles = get_agents_files

    def get_system_prompt(self) -> str | None:
        return self.system_prompt

    getSystemPrompt = get_system_prompt

    def get_append_system_prompt(self) -> list[str]:
        return self.append_system_prompt

    getAppendSystemPrompt = get_append_system_prompt

    def extend_resources(self, paths: dict[str, list[dict[str, object]]]) -> None:
        self.last_skill_paths = _merge_paths(self.cwd, self.last_skill_paths, _resource_paths(paths.get("skillPaths", [])))
        self.last_prompt_paths = _merge_paths(
            self.cwd,
            self.last_prompt_paths,
            _resource_paths(paths.get("promptPaths", [])),
        )
        self.last_theme_paths = _merge_paths(self.cwd, self.last_theme_paths, _resource_paths(paths.get("themePaths", [])))
        self._update_skills_from_paths(self.last_skill_paths)
        self._update_prompts_from_paths(self.last_prompt_paths)
        self._update_themes_from_paths(self.last_theme_paths)

    extendResources = extend_resources

    def reload(self, options: object | None = None) -> None:
        del options
        resolved_paths = self.package_manager.resolve()
        skill_paths = [resource.path for resource in resolved_paths.skills if resource.enabled]
        prompt_paths = [resource.path for resource in resolved_paths.prompts if resource.enabled]
        theme_paths = [resource.path for resource in resolved_paths.themes if resource.enabled]
        metadata_by_path = {
            str(Path(resource.path).expanduser().resolve()): resource.metadata
            for resources in (resolved_paths.skills, resolved_paths.prompts, resolved_paths.themes)
            for resource in resources
        }
        self._update_extensions()
        self.last_skill_paths = _merge_paths(self.cwd, skill_paths, self.additional_skill_paths)
        self.last_prompt_paths = _merge_paths(self.cwd, prompt_paths, self.additional_prompt_template_paths)
        self.last_theme_paths = _merge_paths(self.cwd, theme_paths, self.additional_theme_paths)
        self._update_skills_from_paths(self.last_skill_paths, metadata_by_path)
        self._update_prompts_from_paths(self.last_prompt_paths, metadata_by_path)
        self._update_themes_from_paths(self.last_theme_paths, metadata_by_path)

        agents_files = {
            "agentsFiles": []
            if self.no_context_files
            else load_project_context_files(cwd=self.cwd, agent_dir=self.agent_dir)
        }
        resolved_agents_files = self.agents_files_override(agents_files) if self.agents_files_override else agents_files
        self.agents_files = list(resolved_agents_files["agentsFiles"])

        base_system_prompt = _resolve_prompt_input(
            self.system_prompt_source or self._discover_system_prompt_file(),
            cwd=self.cwd,
        )
        self.system_prompt = self.system_prompt_override(base_system_prompt) if self.system_prompt_override else base_system_prompt

        append_sources = self.append_system_prompt_source
        if append_sources is None:
            discovered_append = self._discover_append_system_prompt_file()
            append_sources = [discovered_append] if discovered_append else []
        base_append = [
            prompt
            for prompt in (_resolve_prompt_input(source, cwd=self.cwd) for source in append_sources)
            if prompt is not None
        ]
        self.append_system_prompt = (
            self.append_system_prompt_override(base_append) if self.append_system_prompt_override else base_append
        )

    def _update_extensions(self) -> None:
        runtime = ExtensionRunner(cwd=self.cwd)
        errors: list[dict[str, str]] = []
        if not self.no_extensions:
            for path_text in self.additional_extension_paths:
                path = Path(path_text).expanduser()
                if not path.is_absolute():
                    path = Path(self.cwd) / path
                if not path.exists():
                    errors.append({"path": str(path.resolve()), "error": f"Extension path does not exist: {path.resolve()}"})
        for index, factory in enumerate(self.extension_factories, start=1):
            extension_path = f"<inline:{index}>"
            try:
                pending_start = len(runtime.pending_provider_registrations)
                result = factory(runtime)
                if inspect.isawaitable(result):
                    raise RuntimeError("async extension factories are not supported by the Python runtime")
                for pending_index in range(pending_start, len(runtime._pending_provider_registrations)):  # noqa: SLF001
                    name, config, _old_path = runtime._pending_provider_registrations[pending_index]  # noqa: SLF001
                    runtime._pending_provider_registrations[pending_index] = (name, config, extension_path)  # noqa: SLF001
            except Exception as error:  # noqa: BLE001 - Pi records extension load errors as diagnostics.
                errors.append({"path": extension_path, "error": str(error)})
        result = {"extensions": [], "errors": errors, "runtime": runtime}
        self.extensions_result = self.extensions_override(result) if self.extensions_override else result

    def _update_skills_from_paths(self, skill_paths: list[str], metadata_by_path: dict[str, dict[str, object]] | None = None) -> None:
        if self.no_skills and not skill_paths:
            result: dict[str, list[object]] = {"skills": [], "diagnostics": []}
        else:
            result = load_skills(skill_paths, cwd=self.cwd, metadata_by_path=metadata_by_path)
        self.skills_result = self.skills_override(result) if self.skills_override else result

    def _update_prompts_from_paths(
        self,
        prompt_paths: list[str],
        metadata_by_path: dict[str, dict[str, object]] | None = None,
    ) -> None:
        if self.no_prompt_templates and not prompt_paths:
            result: dict[str, list[object]] = {"prompts": [], "diagnostics": []}
        else:
            result = load_prompt_templates(prompt_paths, cwd=self.cwd, metadata_by_path=metadata_by_path)
        self.prompts_result = self.prompts_override(result) if self.prompts_override else result

    def _update_themes_from_paths(
        self,
        theme_paths: list[str],
        metadata_by_path: dict[str, dict[str, object]] | None = None,
    ) -> None:
        if self.no_themes and not theme_paths:
            result: dict[str, list[object]] = {"themes": [], "diagnostics": []}
        else:
            result = load_themes(theme_paths, cwd=self.cwd, metadata_by_path=metadata_by_path)
        self.themes_result = self.themes_override(result) if self.themes_override else result

    def _discover_system_prompt_file(self) -> str | None:
        project_path = Path(self.cwd) / CONFIG_DIR_NAME / "SYSTEM.md"
        if self.project_trusted and project_path.exists():
            return str(project_path)

        global_path = Path(self.agent_dir) / "SYSTEM.md"
        if global_path.exists():
            return str(global_path)
        return None

    def _discover_append_system_prompt_file(self) -> str | None:
        project_path = Path(self.cwd) / CONFIG_DIR_NAME / "APPEND_SYSTEM.md"
        if self.project_trusted and project_path.exists():
            return str(project_path)

        global_path = Path(self.agent_dir) / "APPEND_SYSTEM.md"
        if global_path.exists():
            return str(global_path)
        return None


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


def _resource_paths(entries: list[dict[str, object]]) -> list[str]:
    paths: list[str] = []
    for entry in entries:
        path = entry.get("path")
        if isinstance(path, str):
            paths.append(path)
    return paths


def _settings_value(settings_manager: object, *names: str):
    for name in names:
        value = getattr(settings_manager, name, None)
        if callable(value):
            result = value()
            if result is not None:
                return result
        elif value is not None:
            return value
    return None


def _settings_list(settings_manager: object, *names: str) -> list[str]:
    value = _settings_value(settings_manager, *names)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _settings_package_paths(settings_manager: object) -> list[str]:
    value = _settings_value(settings_manager, "getPackages")
    if not isinstance(value, list):
        return []
    paths: list[str] = []
    for item in value:
        if isinstance(item, str):
            paths.append(item)
        elif isinstance(item, dict) and isinstance(item.get("source"), str):
            paths.append(str(item["source"]))
    return paths


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


def load_skills(
    skill_paths: list[str],
    *,
    cwd: str,
    metadata_by_path: dict[str, dict[str, object]] | None = None,
) -> dict[str, list[object]]:
    skills_by_name: dict[str, Skill] = {}
    diagnostics: list[ResourceDiagnostic] = []
    seen_real_paths: set[str] = set()
    for path_text in skill_paths:
        path = _resolve_path(path_text, cwd)
        paths = _collect_resource_files(path, "skills") if path.is_dir() else [path]
        if not path.exists():
            diagnostics.append(ResourceDiagnostic(type="warning", message="skill path does not exist", path=str(path)))
            continue
        for skill_file in paths:
            skill, skill_diagnostics = _load_skill_from_file(skill_file, metadata_by_path)
            diagnostics.extend(skill_diagnostics)
            if skill is None:
                continue
            real_path = str(skill_file.resolve())
            if real_path in seen_real_paths:
                continue
            existing = skills_by_name.get(skill.name)
            if existing:
                diagnostics.append(
                    ResourceDiagnostic(
                        type="collision",
                        message=f'name "{skill.name}" collision',
                        path=str(skill_file),
                        collision={
                            "resourceType": "skill",
                            "name": skill.name,
                            "winnerPath": existing.file_path,
                            "loserPath": str(skill_file),
                        },
                    )
                )
                continue
            skills_by_name[skill.name] = skill
            seen_real_paths.add(real_path)
    return {"skills": list(skills_by_name.values()), "diagnostics": diagnostics}


def load_skills_from_dir(options: dict[str, object]) -> dict[str, list[object]]:
    directory = str(options.get("dir") or options.get("path") or "")
    cwd = str(options.get("cwd") or Path(directory).parent or ".")
    metadata_by_path = options.get("metadataByPath") or options.get("metadata_by_path")
    return load_skills([directory], cwd=cwd, metadata_by_path=metadata_by_path if isinstance(metadata_by_path, dict) else None)


loadSkills = load_skills
loadSkillsFromDir = load_skills_from_dir


def load_prompt_templates(
    prompt_paths: list[str],
    *,
    cwd: str,
    metadata_by_path: dict[str, dict[str, object]] | None = None,
) -> dict[str, list[object]]:
    prompts: list[PromptTemplate] = []
    diagnostics: list[ResourceDiagnostic] = []
    seen_names: set[str] = set()
    for path_text in prompt_paths:
        path = _resolve_path(path_text, cwd)
        paths = _collect_resource_files(path, "prompts") if path.is_dir() else [path]
        for prompt_file in paths:
            if not prompt_file.exists() or prompt_file.suffix != ".md":
                continue
            try:
                raw = prompt_file.read_text(encoding="utf-8")
                frontmatter, body = _parse_frontmatter(raw)
                name = prompt_file.stem
                description = str(frontmatter.get("description") or "")
                if not description:
                    first_line = next((line.strip() for line in body.splitlines() if line.strip()), "")
                    description = first_line[:60] + ("..." if len(first_line) > 60 else "")
                if name in seen_names:
                    diagnostics.append(
                        ResourceDiagnostic(type="collision", message=f'name "{name}" collision', path=str(prompt_file))
                    )
                    continue
                seen_names.add(name)
                prompts.append(
                    PromptTemplate(
                        name=name,
                        description=description,
                        argument_hint=frontmatter.get("argument-hint"),
                        content=body,
                        source_info=_source_info_for_path(prompt_file, metadata_by_path),
                        file_path=str(prompt_file),
                    )
                )
            except OSError as error:
                diagnostics.append(ResourceDiagnostic(type="warning", message=str(error), path=str(prompt_file)))
    return {"prompts": prompts, "diagnostics": diagnostics}


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
        paths = _collect_resource_files(path, "themes") if path.is_dir() else [path]
        for theme_file in paths:
            if not theme_file.exists() or theme_file.suffix != ".json":
                continue
            try:
                data = json.loads(theme_file.read_text(encoding="utf-8"))
                name = str(data.get("name") or theme_file.stem)
                if name in seen_names:
                    diagnostics.append(
                        ResourceDiagnostic(type="collision", message=f'name "{name}" collision', path=str(theme_file))
                    )
                    continue
                seen_names.add(name)
                themes.append(
                    Theme(
                        name=name,
                        colors=dict(data.get("colors") or {}),
                        vars=dict(data.get("vars") or {}),
                        source_path=str(theme_file),
                        source_info=_source_info_for_path(theme_file, metadata_by_path),
                    )
                )
            except (OSError, json.JSONDecodeError) as error:
                diagnostics.append(ResourceDiagnostic(type="warning", message=str(error), path=str(theme_file)))
    return {"themes": themes, "diagnostics": diagnostics}


def _load_skill_from_file(
    file_path: Path,
    metadata_by_path: dict[str, dict[str, object]] | None,
) -> tuple[Skill | None, list[ResourceDiagnostic]]:
    diagnostics: list[ResourceDiagnostic] = []
    try:
        raw = file_path.read_text(encoding="utf-8")
    except OSError as error:
        return None, [ResourceDiagnostic(type="warning", message=str(error), path=str(file_path))]
    frontmatter, _body = _parse_frontmatter(raw)
    name = str(frontmatter.get("name") or file_path.parent.name)
    description = str(frontmatter.get("description") or "")
    if not description.strip():
        diagnostics.append(ResourceDiagnostic(type="warning", message="description is required", path=str(file_path)))
        return None, diagnostics
    if not _valid_skill_name(name):
        diagnostics.append(
            ResourceDiagnostic(
                type="warning",
                message="name contains invalid characters (must be lowercase a-z, 0-9, hyphens only)",
                path=str(file_path),
            )
        )
    return (
        Skill(
            name=name,
            description=description,
            file_path=str(file_path),
            base_dir=str(file_path.parent),
            source_info=_source_info_for_path(file_path, metadata_by_path),
            disable_model_invocation=frontmatter.get("disable-model-invocation") is True,
            allowed_tools=_parse_allowed_tools(frontmatter.get("allowed-tools")),
        ),
        diagnostics,
    )


def _parse_allowed_tools(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        raw_tools = value.replace(",", " ").split()
    elif isinstance(value, list):
        raw_tools = [item for item in value if isinstance(item, str)]
    else:
        return ()
    allowed_tools: list[str] = []
    seen: set[str] = set()
    for raw_tool in raw_tools:
        tool = raw_tool.strip()
        if not tool or tool in seen:
            continue
        seen.add(tool)
        allowed_tools.append(tool)
    return tuple(allowed_tools)


def format_skills_for_prompt(skills: list[Skill]) -> str:
    visible_skills = [skill for skill in skills if not skill.disable_model_invocation]
    if not visible_skills:
        return ""
    lines = [
        "",
        "",
        "The following skills provide specialized instructions for specific tasks.",
        "Use the read tool to load a skill's file when the task matches its description.",
        "When a skill file references a relative path, resolve it against the skill directory (parent of SKILL.md / dirname of the path) and use that absolute path in tool commands.",
        "",
        "<available_skills>",
    ]
    for skill in visible_skills:
        lines.extend(
            [
                "  <skill>",
                f"    <name>{_escape_xml(skill.name)}</name>",
                f"    <description>{_escape_xml(skill.description)}</description>",
                f"    <location>{_escape_xml(skill.file_path)}</location>",
                "  </skill>",
            ]
        )
    lines.append("</available_skills>")
    return "\n".join(lines)


formatSkillsForPrompt = format_skills_for_prompt


def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    if not raw.startswith("---"):
        return {}, raw
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, raw
    end_index = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}, raw
    data: dict[str, Any] = {}
    for line in lines[1:end_index]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip().strip('"').strip("'")
        if value.lower() == "true":
            parsed: Any = True
        elif value.lower() == "false":
            parsed = False
        else:
            parsed = value
        data[key.strip()] = parsed
    return data, "\n".join(lines[end_index + 1 :]).lstrip("\r\n").rstrip("\n")


def _read_pi_manifest(package_root: Path) -> dict[str, list[str]] | None:
    package_json = package_root / "package.json"
    if not package_json.exists():
        return None
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    manifest = data.get("pi")
    return manifest if isinstance(manifest, dict) else None


def _collect_manifest_entries(package_root: Path, entries: list[str], resource_type: str) -> list[Path]:
    paths: list[Path] = []
    for entry in entries:
        if not isinstance(entry, str) or entry.startswith("!"):
            continue
        paths.extend(_collect_resource_files(package_root / entry, resource_type))
    return paths


def _collect_resource_files(path: Path, resource_type: str) -> list[Path]:
    if not path.exists():
        return []
    if path.is_file():
        if resource_type == "skills" and path.suffix == ".md":
            return [path.resolve()]
        if resource_type == "prompts" and path.suffix == ".md":
            return [path.resolve()]
        if resource_type == "themes" and path.suffix == ".json":
            return [path.resolve()]
        return []
    if resource_type == "skills":
        skill_file = path / "SKILL.md"
        if skill_file.is_file():
            return [skill_file.resolve()]
        paths: list[Path] = []
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            if child.name.startswith(".") or child.name == "node_modules":
                continue
            if child.is_dir():
                paths.extend(_collect_resource_files(child, resource_type))
            elif child.suffix == ".md":
                paths.append(child.resolve())
        return paths
    suffix = ".md" if resource_type == "prompts" else ".json"
    return [child.resolve() for child in sorted(path.rglob(f"*{suffix}")) if child.is_file()]


def _collect_ancestor_agents_skill_dirs(cwd: Path) -> list[Path]:
    dirs: list[Path] = []
    current = cwd.resolve()
    while True:
        candidate = current / ".agents" / "skills"
        if candidate.is_dir():
            dirs.insert(0, candidate)
        if current.parent == current:
            break
        current = current.parent
    return dirs


def _resolve_path(path: str, cwd: str) -> Path:
    resolved = Path(path).expanduser()
    if not resolved.is_absolute():
        resolved = Path(cwd) / resolved
    return resolved.resolve()


def _source_info_for_path(path: Path, metadata_by_path: dict[str, dict[str, object]] | None) -> SourceInfo:
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
            base_dir=metadata.get("baseDir") if isinstance(metadata.get("baseDir"), str) else None,
        )
    return create_synthetic_source_info(str(path), source="local", base_dir=str(path.parent))


def _valid_skill_name(name: str) -> bool:
    if len(name) > 64 or name.startswith("-") or name.endswith("-") or "--" in name:
        return False
    return bool(name) and all(ch.islower() or ch.isdigit() or ch == "-" for ch in name)


def _escape_xml(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
