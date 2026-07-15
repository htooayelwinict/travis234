"""Resource loader subset ported from Travis coding-agent resource-loader."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
import inspect
from contextlib import nullcontext
import json
import sys
from types import ModuleType
from pathlib import Path

from travis.agent.async_utils import resolve, run_sync
from travis.coding_agent.event_bus import EventBusController, create_event_bus
from travis.coding_agent.extensions import ExtensionRunner
from travis.coding_agent.object_utils import settings_value as _settings_value
from travis.coding_agent.package_manager import (
    DefaultPackageManager,
    ResolvedPaths,
    ResolvedResource,
)
from travis.coding_agent.prompt_templates import (
    load_prompt_templates as _load_prompt_templates_runtime,
)
from travis.coding_agent.project_trust import (
    ProjectTrustContext,
    ProjectTrustStore,
    resolve_project_trust,
)
from travis.coding_agent.settings_manager import SettingsManager
from travis.coding_agent.resource_discovery import collect_resource_files
from travis.coding_agent.skills import (
    ResourceDiagnostic,
    Skill,
    format_skills_for_prompt as _format_skills_for_prompt_runtime,
    load_skills as _load_skills_runtime,
)
from travis.coding_agent.source_info import SourceInfo, create_synthetic_source_info
from travis.coding_agent.themes import Theme

CONFIG_DIR_NAME = ".travis234"
_CONTEXT_FILE_NAMES = ("AGENTS.md", "AGENTS.MD", "CLAUDE.md", "CLAUDE.MD")

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
        offline: bool = False,
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
        settings_project_trusted = _settings_value(self.settings_manager, "is_project_trusted")
        settings_trust_resolved = bool(getattr(self.settings_manager, "project_trust_resolved", False))
        self._project_trust_override = (
            project_trusted
            if project_trusted is not None
            else bool(settings_project_trusted) if settings_trust_resolved else None
        )
        self.project_trusted = bool(self._project_trust_override)
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
        self.offline = bool(offline)
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
            settings_manager=self.settings_manager,
            offline=self.offline,
        )
        self.package_diagnostics: list[object] = []

        self.extensions_result: dict[str, object] = {
            "extensions": [],
            "errors": [],
            "runtime": ExtensionRunner(cwd=self.cwd, event_bus=self.event_bus),
        }
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


    def get_package_diagnostics(self) -> list[object]:
        return list(self.package_diagnostics)


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


    def load_project_trust_extensions(self) -> dict[str, object]:
        """Load only resources allowed to participate in trust resolution."""

        self._set_project_trusted(False)
        self._reload_settings_and_configured_paths()
        resolved_paths = self.package_manager.resolve()
        self.package_diagnostics = list(resolved_paths.diagnostics)
        extension_paths = [resource.path for resource in resolved_paths.extensions if resource.enabled]
        self._update_extensions(extension_paths, apply_override=False)
        return self.extensions_result

    def reload(self, options: Mapping[str, object] | None = None) -> None:
        resolved_options = dict(options or {})
        trust_override = _first_mapping_value(
            resolved_options,
            "projectTrustOverride",
            "project_trust_override",
        )
        if trust_override is not None and not isinstance(trust_override, bool):
            raise TypeError("project trust override must be true, false, or null")
        if trust_override is None:
            trust_override = self._project_trust_override

        pretrust_extensions: dict[str, object] | None = None
        if trust_override is None:
            pretrust_extensions = self.load_project_trust_extensions()
            context = _first_mapping_value(
                resolved_options,
                "projectTrustContext",
                "project_trust_context",
            )
            if context is None:
                context = ProjectTrustContext(has_ui=False, select=None)
            if not isinstance(context, ProjectTrustContext):
                raise TypeError("project trust context must be a ProjectTrustContext")
            trust_store = _first_mapping_value(resolved_options, "trustStore", "trust_store")
            if trust_store is None:
                trust_store = ProjectTrustStore(self.agent_dir)
            if not isinstance(trust_store, ProjectTrustStore):
                raise TypeError("trust store must be a ProjectTrustStore")
            get_default_project_trust = getattr(self.settings_manager, "get_default_project_trust", None)
            default_project_trust = get_default_project_trust() if callable(get_default_project_trust) else "ask"
            trusted = run_sync(
                resolve_project_trust(
                    cwd=self.cwd,
                    trust_store=trust_store,
                    context=context,
                    default_project_trust=default_project_trust,
                    extension_runner=pretrust_extensions.get("runtime"),
                )
            )
        else:
            trusted = trust_override

        self._set_project_trusted(bool(trusted))
        self._reload_all_resources(pretrust_extensions=pretrust_extensions)

    def _set_project_trusted(self, trusted: bool) -> None:
        self.project_trusted = trusted
        set_project_trusted = getattr(self.settings_manager, "set_project_trusted", None)
        if callable(set_project_trusted):
            set_project_trusted(trusted)
        self.package_manager.project_trusted = trusted

    def _reload_settings_and_configured_paths(self) -> None:
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

    def _reload_all_resources(self, *, pretrust_extensions: dict[str, object] | None = None) -> None:
        self._reload_settings_and_configured_paths()
        resolved_paths = self.package_manager.resolve()
        self.package_diagnostics = list(resolved_paths.diagnostics)
        skill_paths = [resource.path for resource in resolved_paths.skills if resource.enabled]
        prompt_paths = [resource.path for resource in resolved_paths.prompts if resource.enabled]
        theme_paths = [resource.path for resource in resolved_paths.themes if resource.enabled]
        metadata_by_path = {
            str(Path(resource.path).expanduser().resolve()): resource.metadata
            for resources in (resolved_paths.skills, resolved_paths.prompts, resolved_paths.themes)
            for resource in resources
        }
        extension_paths = [resource.path for resource in resolved_paths.extensions if resource.enabled]
        self._update_extensions(extension_paths, preloaded_result=pretrust_extensions)
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

    def _update_extensions(
        self,
        discovered_paths: list[str] | None = None,
        *,
        preloaded_result: dict[str, object] | None = None,
        apply_override: bool = True,
    ) -> None:
        if preloaded_result is None:
            previous_runtime = self.extensions_result.get("runtime")
            if isinstance(previous_runtime, ExtensionRunner):
                previous_runtime.dispose()
            runtime = ExtensionRunner(cwd=self.cwd, event_bus=self.event_bus)
            errors: list[dict[str, str]] = []
            loaded_by_path: dict[str, dict[str, str]] = {}
            inline_loaded: list[dict[str, str]] = []
            failed_paths: set[str] = set()
            for module_name in self._extension_module_names:
                sys.modules.pop(module_name, None)
            self._extension_module_names = []
            self._extension_reload_generation += 1
        else:
            runtime = preloaded_result.get("runtime")
            if not isinstance(runtime, ExtensionRunner):
                raise RuntimeError("Pre-trust extension load did not produce an extension runtime")
            errors = [dict(error) for error in preloaded_result.get("errors", []) if isinstance(error, dict)]
            preloaded = [entry for entry in preloaded_result.get("extensions", []) if isinstance(entry, dict)]
            loaded_by_path = {
                str(entry["path"]): dict(entry)
                for entry in preloaded
                if isinstance(entry.get("path"), str) and not str(entry["path"]).startswith("<inline:")
            }
            inline_loaded = [
                dict(entry)
                for entry in preloaded
                if isinstance(entry.get("path"), str) and str(entry["path"]).startswith("<inline:")
            ]
            failed_paths = {
                str(error["path"])
                for error in errors
                if isinstance(error.get("path"), str)
            }

        extension_files: list[Path] = []
        if not self.no_extensions:
            seen: set[str] = set()
            for path_text in [*(discovered_paths or []), *self.additional_extension_paths]:
                path = _resolve_path(path_text, self.cwd)
                if not path.exists():
                    if str(path) not in failed_paths:
                        errors.append({"path": str(path), "error": f"Extension path does not exist: {path}"})
                        failed_paths.add(str(path))
                    continue
                for extension_file in collect_resource_files(path, "extensions"):
                    resolved = str(extension_file.resolve())
                    if resolved not in seen:
                        seen.add(resolved)
                        extension_files.append(extension_file.resolve())

        for extension_file in extension_files:
            extension_path = str(extension_file)
            if extension_path in loaded_by_path or extension_path in failed_paths:
                continue
            try:
                module = self._load_extension_module(extension_file)
                factory = getattr(module, "extension", None)
                if not callable(factory):
                    raise RuntimeError("Extension module must export callable extension(travis)")
                self._run_extension_factory(runtime, factory, extension_path)
                loaded_by_path[extension_path] = {"path": extension_path}
            except Exception as error:  # noqa: BLE001 - extension load failures are diagnostics.
                errors.append({"path": extension_path, "error": str(error)})
                failed_paths.add(extension_path)

        if preloaded_result is None:
            factories = [] if self.no_extensions else self.extension_factories
            for index, factory in enumerate(factories, start=1):
                extension_path = f"<inline:{index}>"
                try:
                    self._run_extension_factory(runtime, factory, extension_path)
                    inline_loaded.append({"path": extension_path})
                except Exception as error:  # noqa: BLE001 - extension failures become diagnostics.
                    errors.append({"path": extension_path, "error": str(error)})
        loaded = [loaded_by_path[str(path)] for path in extension_files if str(path) in loaded_by_path]
        loaded.extend(inline_loaded)
        result = {"extensions": loaded, "errors": errors, "runtime": runtime}
        self.extensions_result = (
            self.extensions_override(result)
            if apply_override and self.extensions_override
            else result
        )

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
            owner_scope = getattr(runtime.events, "owner", None)
            scope = owner_scope(runtime._event_bus_owner) if callable(owner_scope) else nullcontext()  # noqa: SLF001
            with scope:
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


def _first_mapping_value(options: Mapping[str, object], *names: str) -> object | None:
    for name in names:
        if name in options:
            return options[name]
    return None


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


def load_skills_from_dir(options: dict[str, object]) -> dict[str, list[object]]:
    directory = str(options.get("dir") or options.get("path") or "")
    cwd = str(options.get("cwd") or Path(directory).parent or ".")
    metadata_by_path = options.get("metadataByPath") or options.get("metadata_by_path")
    return load_skills([directory], cwd=cwd, metadata_by_path=metadata_by_path if isinstance(metadata_by_path, dict) else None)
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
# Compatibility imports: callers keep the historical resource_loader surface,
# while focused modules own parsing, validation, and ignored traversal.
load_skills = _load_skills_runtime
load_prompt_templates = _load_prompt_templates_runtime
format_skills_for_prompt = _format_skills_for_prompt_runtime
