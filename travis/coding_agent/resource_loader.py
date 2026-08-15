"""Resource loader subset ported from Travis coding-agent resource-loader."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType

from travis.agent.async_utils import run_sync
from travis.coding_agent.config import get_agent_dir
from travis.coding_agent.event_bus import EventBusController, create_event_bus
from travis.coding_agent.extensions import ExtensionRunner
from travis.coding_agent.object_utils import settings_value as _settings_value
from travis.coding_agent.package_manager import DefaultPackageManager
from travis.coding_agent.prompt_templates import (
    load_prompt_templates as _load_prompt_templates_runtime,
)
from travis.coding_agent.project_trust import (
    ProjectTrustContext,
    ProjectTrustStore,
    resolve_project_trust,
)
from travis.coding_agent.resource_candidates import (
    ResourceContentCandidate,
    ResourceContentRequest,
    build_resource_content,
    extend_resource_content,
    load_context_file_from_dir,
    load_project_context_files,
    load_themes,
    resource_paths as _resource_paths,
)
from travis.coding_agent.resource_extensions import (
    ExtensionLoadRequest,
    ExtensionRuntimeLease,
    create_empty_extension_runtime,
    load_extension_runtime,
)
from travis.coding_agent.settings_manager import SettingsManager
from travis.coding_agent.skills import (
    ResourceDiagnostic,
    Skill,
    format_skills_for_prompt as _format_skills_for_prompt_runtime,
    load_skills as _load_skills_runtime,
)


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
        self.agent_dir = str(Path(agent_dir or get_agent_dir()).expanduser().resolve())
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

        self._extension_lease = create_empty_extension_runtime(
            self.cwd, self.event_bus
        )
        self._pretrust_extension_lease: ExtensionRuntimeLease | None = None
        self.extensions_result = self._extension_lease.result
        self.skills_result: dict[str, list[object]] = {"skills": [], "diagnostics": []}
        self.prompts_result: dict[str, list[object]] = {"prompts": [], "diagnostics": []}
        self.themes_result: dict[str, list[object]] = {"themes": [], "diagnostics": []}
        self.agents_files: list[dict[str, str]] = []
        self.system_prompt: str | None = None
        self.append_system_prompt: list[str] = []
        self.last_skill_paths: list[str] = []
        self.last_prompt_paths: list[str] = []
        self.last_theme_paths: list[str] = []
        self._content_candidate = ResourceContentCandidate(
            skills_result=self.skills_result,
            prompts_result=self.prompts_result,
            themes_result=self.themes_result,
            agents_files=(),
            system_prompt=None,
            append_system_prompt=(),
            package_diagnostics=(),
            skill_paths=(),
            prompt_paths=(),
            theme_paths=(),
            metadata_by_path=MappingProxyType({}),
        )
        self._extension_reload_generation = 0

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
        candidate = extend_resource_content(
            self._content_candidate,
            cwd=self.cwd,
            skill_paths=tuple(_resource_paths(paths.get("skillPaths", []))),
            prompt_paths=tuple(_resource_paths(paths.get("promptPaths", []))),
            theme_paths=tuple(_resource_paths(paths.get("themePaths", []))),
            skills_override=self.skills_override,
            prompts_override=self.prompts_override,
            themes_override=self.themes_override,
        )
        self._apply_content_candidate(candidate)


    def load_project_trust_extensions(self) -> dict[str, object]:
        """Load only resources allowed to participate in trust resolution."""

        self._set_project_trusted(False)
        self._reload_settings_and_configured_paths()
        resolved_paths = self.package_manager.resolve()
        self.package_diagnostics = list(resolved_paths.diagnostics)
        extension_paths = [resource.path for resource in resolved_paths.extensions if resource.enabled]
        self._update_extensions(extension_paths, apply_override=False)
        self._pretrust_extension_lease = self._extension_lease
        return self.extensions_result

    def reload(self, options: Mapping[str, object] | None = None) -> None:
        self.complete_reload(options)

    def complete_reload(
        self,
        options: Mapping[str, object] | None = None,
        *,
        pretrust_extensions: dict[str, object] | None = None,
    ) -> None:
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

        if trust_override is None:
            if pretrust_extensions is None:
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
        extension_paths = [resource.path for resource in resolved_paths.extensions if resource.enabled]
        self._update_extensions(extension_paths, preloaded_result=pretrust_extensions)
        self._pretrust_extension_lease = None
        candidate = build_resource_content(
            ResourceContentRequest(
                cwd=self.cwd,
                agent_dir=self.agent_dir,
                project_trusted=self.project_trusted,
                resolved_paths=resolved_paths,
                additional_skill_paths=tuple(self.additional_skill_paths),
                additional_prompt_paths=tuple(
                    self.additional_prompt_template_paths
                ),
                additional_theme_paths=tuple(self.additional_theme_paths),
                no_context_files=self.no_context_files,
                no_skills=self.no_skills,
                no_prompt_templates=self.no_prompt_templates,
                no_themes=self.no_themes,
                system_prompt_source=self.system_prompt_source,
                append_system_prompt_source=(
                    tuple(self.append_system_prompt_source)
                    if self.append_system_prompt_source is not None
                    else None
                ),
                agents_files_override=self.agents_files_override,
                skills_override=self.skills_override,
                prompts_override=self.prompts_override,
                themes_override=self.themes_override,
                system_prompt_override=self.system_prompt_override,
                append_system_prompt_override=self.append_system_prompt_override,
            )
        )
        self._apply_content_candidate(candidate)

    def _apply_content_candidate(self, candidate: ResourceContentCandidate) -> None:
        self._content_candidate = candidate
        self.skills_result = candidate.skills_result
        self.prompts_result = candidate.prompts_result
        self.themes_result = candidate.themes_result
        self.agents_files = list(candidate.agents_files)
        self.system_prompt = candidate.system_prompt
        self.append_system_prompt = list(candidate.append_system_prompt)
        self.package_diagnostics = list(candidate.package_diagnostics)
        self.last_skill_paths = list(candidate.skill_paths)
        self.last_prompt_paths = list(candidate.prompt_paths)
        self.last_theme_paths = list(candidate.theme_paths)

    def _update_extensions(
        self,
        discovered_paths: list[str] | None = None,
        *,
        preloaded_result: dict[str, object] | None = None,
        apply_override: bool = True,
    ) -> None:
        preloaded: ExtensionRuntimeLease | None = None
        if preloaded_result is not None:
            preloaded = self._pretrust_extension_lease
            if preloaded is None or preloaded.result is not preloaded_result:
                raise ValueError(
                    "pretrust_extensions did not originate from this loader"
                )

        self._extension_reload_generation += 1
        request = ExtensionLoadRequest(
            cwd=self.cwd,
            event_bus=self.event_bus,
            discovered_paths=tuple(discovered_paths or ()),
            additional_paths=tuple(self.additional_extension_paths),
            factories=tuple(self.extension_factories),
            no_extensions=self.no_extensions,
            generation=self._extension_reload_generation,
            apply_override=apply_override,
            override=self.extensions_override,
        )
        candidate = load_extension_runtime(request, preloaded=preloaded)
        replaced = self._extension_lease
        self._extension_lease = candidate
        self.extensions_result = candidate.result
        if replaced is not candidate:
            replaced.release()

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


def load_skills_from_dir(options: dict[str, object]) -> dict[str, list[object]]:
    directory = str(options.get("dir") or options.get("path") or "")
    cwd = str(options.get("cwd") or Path(directory).parent or ".")
    metadata_by_path = options.get("metadataByPath") or options.get("metadata_by_path")
    return load_skills(
        [directory],
        cwd=cwd,
        metadata_by_path=(
            metadata_by_path if isinstance(metadata_by_path, dict) else None
        ),
    )


# Compatibility imports: callers keep the historical resource_loader surface,
# while focused modules own parsing, validation, and ignored traversal.
load_skills = _load_skills_runtime
load_prompt_templates = _load_prompt_templates_runtime
format_skills_for_prompt = _format_skills_for_prompt_runtime
