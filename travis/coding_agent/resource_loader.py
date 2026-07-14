"""Resource loader subset ported from Travis coding-agent resource-loader."""

from __future__ import annotations

from collections.abc import Callable
import hashlib
import inspect
import json
import sys
from types import ModuleType
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from travis.agent.async_utils import resolve, run_sync
from travis.coding_agent.event_bus import EventBusController, create_event_bus
from travis.coding_agent.extensions import ExtensionRunner
from travis.coding_agent.object_utils import settings_value as _settings_value
from travis.coding_agent.settings_manager import SettingsManager
from travis.coding_agent.source_info import SourceInfo, create_synthetic_source_info

CONFIG_DIR_NAME = ".travis234"
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







@dataclass
class PromptTemplate:
    name: str
    description: str
    content: str
    source_info: SourceInfo
    file_path: str
    argument_hint: str | None = None





@dataclass
class Theme:
    name: str
    colors: dict[str, object]
    vars: dict[str, object]
    source_path: str
    source_info: SourceInfo




class DefaultPackageManager:
    """Local package/resource resolver subset of the runtime's DefaultPackageManager."""

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
        manifest = _read_package_manifest(package_root)
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
        pairs: list[tuple[Path, str, str]] = [(global_base, "user", "auto")]
        if self.project_trusted:
            pairs.append((project_base, "project", "auto"))
        for base, scope, source in pairs:
            metadata = {"source": source, "scope": scope, "origin": "top-level", "baseDir": str(base)}
            for resource_type in _RESOURCE_TYPES:
                paths = _collect_resource_files(base / resource_type, resource_type)
                target = getattr(resolved, resource_type)
                for path in paths:
                    target.append(ResolvedResource(path=str(path), enabled=True, metadata=metadata))

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


def _nearest_git_root(start: Path) -> Path | None:
    """Return the active checkout/worktree boundary, if one exists."""

    current = start
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


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


class DefaultResourceLoader:
    """Small reloadable resource cache matching the Travis ResourceLoader surface."""

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
        self.agent_dir = str(Path(agent_dir).expanduser().resolve()) if agent_dir else str(Path.home() / ".travis234" / "agent")
        self.settings_manager = settings_manager or SettingsManager.create(self.cwd, self.agent_dir)
        self.event_bus = event_bus or create_event_bus()
        self.no_context_files = no_context_files
        self.project_trusted = (
            _settings_value(self.settings_manager, "is_project_trusted")
            if project_trusted is None
            else project_trusted
        )
        self.project_trusted = True if self.project_trusted is None else bool(self.project_trusted)
        self.system_prompt_source = system_prompt
        self.append_system_prompt_source = append_system_prompt
        self._explicit_extension_paths = list(additional_extension_paths or [])
        self.additional_extension_paths = _settings_list(
            self.settings_manager,
            "get_extension_paths",
        ) + self._explicit_extension_paths
        self.extension_factories = list(extension_factories or [])
        self.no_extensions = no_extensions
        self.extensions_override = extensions_override
        self._explicit_package_paths = list(package_paths or [])
        self._explicit_skill_paths = list(additional_skill_paths or [])
        self._explicit_prompt_paths = list(additional_prompt_template_paths or [])
        self._explicit_theme_paths = list(additional_theme_paths or [])
        self.package_paths = _settings_package_paths(self.settings_manager) + self._explicit_package_paths
        self.additional_skill_paths = _settings_list(self.settings_manager, "get_skill_paths") + self._explicit_skill_paths
        self.additional_prompt_template_paths = _settings_list(
            self.settings_manager,
            "get_prompt_template_paths",
        ) + self._explicit_prompt_paths
        self.additional_theme_paths = _settings_list(
            self.settings_manager,
            "get_theme_paths",
        ) + self._explicit_theme_paths
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
        self._extension_reload_generation = 0
        self._extension_module_names: list[str] = []

    def get_extensions(self) -> dict[str, object]:
        return self.extensions_result


    def get_skills(self) -> dict[str, list[object]]:
        return self.skills_result


    def get_prompts(self) -> dict[str, list[object]]:
        return self.prompts_result


    def get_themes(self) -> dict[str, list[object]]:
        return self.themes_result


    def get_agents_files(self) -> dict[str, list[dict[str, str]]]:
        return {"agentsFiles": self.agents_files}


    def get_system_prompt(self) -> str | None:
        return self.system_prompt


    def get_append_system_prompt(self) -> list[str]:
        return self.append_system_prompt


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


    def reload(self, options: object | None = None) -> None:
        del options
        reload_settings = getattr(self.settings_manager, "reload", None)
        if callable(reload_settings):
            reload_settings()
        self.additional_extension_paths = _settings_list(
            self.settings_manager,
            "get_extension_paths",
        ) + self._explicit_extension_paths
        self.package_paths = _settings_package_paths(self.settings_manager) + self._explicit_package_paths
        self.additional_skill_paths = _settings_list(
            self.settings_manager,
            "get_skill_paths",
        ) + self._explicit_skill_paths
        self.additional_prompt_template_paths = _settings_list(
            self.settings_manager,
            "get_prompt_template_paths",
        ) + self._explicit_prompt_paths
        self.additional_theme_paths = _settings_list(
            self.settings_manager,
            "get_theme_paths",
        ) + self._explicit_theme_paths
        self.package_manager.package_paths = list(self.package_paths)
        resolved_paths = self.package_manager.resolve()
        skill_paths = [resource.path for resource in resolved_paths.skills if resource.enabled]
        prompt_paths = [resource.path for resource in resolved_paths.prompts if resource.enabled]
        theme_paths = [resource.path for resource in resolved_paths.themes if resource.enabled]
        metadata_by_path = {
            str(Path(resource.path).expanduser().resolve()): resource.metadata
            for resources in (resolved_paths.skills, resolved_paths.prompts, resolved_paths.themes)
            for resource in resources
        }
        extension_paths = [resource.path for resource in resolved_paths.extensions if resource.enabled]
        self._update_extensions(extension_paths)
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

    def _update_extensions(self, discovered_paths: list[str] | None = None) -> None:
        runtime = ExtensionRunner(cwd=self.cwd)
        errors: list[dict[str, str]] = []
        loaded: list[dict[str, str]] = []
        for module_name in self._extension_module_names:
            sys.modules.pop(module_name, None)
        self._extension_module_names = []
        self._extension_reload_generation += 1

        extension_files: list[Path] = []
        if not self.no_extensions:
            seen: set[str] = set()
            for path_text in [*(discovered_paths or []), *self.additional_extension_paths]:
                path = _resolve_path(path_text, self.cwd)
                if not path.exists():
                    errors.append({"path": str(path), "error": f"Extension path does not exist: {path}"})
                    continue
                for extension_file in _collect_resource_files(path, "extensions"):
                    resolved = str(extension_file.resolve())
                    if resolved not in seen:
                        seen.add(resolved)
                        extension_files.append(extension_file.resolve())

        for extension_file in extension_files:
            extension_path = str(extension_file)
            try:
                module = self._load_extension_module(extension_file)
                factory = getattr(module, "extension", None)
                if not callable(factory):
                    raise RuntimeError("Extension module must export callable extension(travis)")
                self._run_extension_factory(runtime, factory, extension_path)
                loaded.append({"path": extension_path})
            except Exception as error:  # noqa: BLE001 - extension load failures are diagnostics.
                errors.append({"path": extension_path, "error": str(error)})

        factories = [] if self.no_extensions else self.extension_factories
        for index, factory in enumerate(factories, start=1):
            extension_path = f"<inline:{index}>"
            try:
                self._run_extension_factory(runtime, factory, extension_path)
                loaded.append({"path": extension_path})
            except Exception as error:  # noqa: BLE001 - Travis records extension load errors as diagnostics.
                errors.append({"path": extension_path, "error": str(error)})
        result = {"extensions": loaded, "errors": errors, "runtime": runtime}
        self.extensions_result = self.extensions_override(result) if self.extensions_override else result

    def _load_extension_module(self, path: Path) -> ModuleType:
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]
        module_name = f"_travis234_extension_{digest}_{self._extension_reload_generation}"
        module = ModuleType(module_name)
        module.__file__ = str(path)
        module.__package__ = module_name
        module.__path__ = [str(path.parent)]  # type: ignore[attr-defined]
        sys.modules[module_name] = module
        self._extension_module_names.append(module_name)
        source = path.read_text(encoding="utf-8")
        exec(compile(source, str(path), "exec"), module.__dict__)  # noqa: S102 - trusted extension execution.
        return module

    @staticmethod
    def _run_extension_factory(
        runtime: ExtensionRunner,
        factory: Callable[[ExtensionRunner], object],
        extension_path: str,
    ) -> None:
        pending_start = len(runtime.pending_provider_registrations)
        runtime._loading_extension_path = extension_path  # noqa: SLF001
        try:
            result = factory(runtime)
            if inspect.isawaitable(result):
                run_sync(resolve(result))
        finally:
            runtime._loading_extension_path = None  # noqa: SLF001
        for pending_index in range(pending_start, len(runtime._pending_provider_registrations)):  # noqa: SLF001
            name, config, _old_path = runtime._pending_provider_registrations[pending_index]  # noqa: SLF001
            runtime._pending_provider_registrations[pending_index] = (name, config, extension_path)  # noqa: SLF001

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


def _settings_list(settings_manager: object, *names: str) -> list[str]:
    value = _settings_value(settings_manager, *names)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


def _settings_package_paths(settings_manager: object) -> list[str]:
    value = _settings_value(settings_manager, "get_packages")
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


def _read_package_manifest(package_root: Path) -> dict[str, list[str]] | None:
    package_json = package_root / "package.json"
    if not package_json.exists():
        return None
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    manifest = data.get("travis")
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
        if resource_type == "extensions" and path.suffix == ".py":
            return [path.resolve()]
        if resource_type == "skills" and path.suffix == ".md":
            return [path.resolve()]
        if resource_type == "prompts" and path.suffix == ".md":
            return [path.resolve()]
        if resource_type == "themes" and path.suffix == ".json":
            return [path.resolve()]
        return []
    if resource_type == "extensions":
        package_entry = path / "__init__.py"
        if package_entry.is_file():
            return [package_entry.resolve()]
        index_entry = path / "index.py"
        if index_entry.is_file():
            return [index_entry.resolve()]
        extension_paths: list[Path] = []
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            if child.name.startswith(".") or child.name in {"__pycache__", "node_modules"}:
                continue
            if child.is_file() and child.suffix == ".py":
                extension_paths.append(child.resolve())
            elif child.is_dir():
                extension_paths.extend(_collect_resource_files(child, resource_type))
        return extension_paths
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
