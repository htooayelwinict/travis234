"""Focused tooling ownership for coding sessions."""

from __future__ import annotations

import os
from pathlib import Path

from travis.agent.types import (
    AgentTool,
)
from travis.coding_agent.extensions import (
    wrap_registered_tool,
)
from travis.coding_agent.object_utils import settings_value as _settings_value
from travis.coding_agent.processes.local import create_local_process_transport
from travis.coding_agent.session_extensions import _extension_resource_path, _tool_info
from travis.coding_agent.session_ports import SessionControllerPort, SessionPortBoundController
from travis.coding_agent.session_types import _DEFAULT_ACTIVE_TOOL_NAMES
from travis.coding_agent.system_prompt import BuildSystemPromptOptions, build_system_prompt
from travis.coding_agent.tools.types import (
    ToolDefinition,
)


class SessionToolController(SessionPortBoundController[SessionControllerPort]):
    """Owns a focused AgentSession runtime concern."""

    def _default_subagent_log_dir(self, *, session_path: str | None, session_id: str | None) -> str:
        namespace = session_id or (Path(session_path).stem if session_path else "ephemeral")
        safe_namespace = "".join(character if character.isalnum() or character in {"-", "_"} else "_" for character in namespace)
        base_dir = Path(session_path).parent if session_path else Path(self.cwd) / ".travis234"
        return str(base_dir / "subagents" / (safe_namespace or "ephemeral"))

    def _is_allowed_tool(self, name: str) -> bool:
        return (
            self._allowed_tool_names is None or name in self._allowed_tool_names
        ) and name not in self._excluded_tool_names

    def _settings_shell_command_prefix(self) -> str | None:
        return _settings_value(
            self.settings_manager,
            "getShellCommandPrefix",
            "get_shell_command_prefix",
            "shellCommandPrefix",
            "shell_command_prefix",
        )

    def _default_active_tool_names(self) -> list[str]:
        names = list(_DEFAULT_ACTIVE_TOOL_NAMES)
        if self.process_service is not None and self._is_allowed_tool("process"):
            names.insert(2, "process")
        if getattr(self, "_language_services", None) is not None:
            names.append("lsp")
        if getattr(self, "_memory_tool_runtime", None) is not None:
            names.append("memory")
        return names

    def _settings_shell_path(self) -> str | None:
        return _settings_value(
            self.settings_manager,
            "getShellPath",
            "get_shell_path",
            "shellPath",
            "shell_path",
        )

    def _skill_read_access(self) -> dict[str, list[str]]:
        if self._resource_loader is None:
            return {"roots": [], "files": []}
        roots: list[str] = []
        files: list[str] = []
        for skill in self._resource_loader.get_skills()["skills"]:
            file_path = getattr(skill, "file_path", None) or getattr(skill, "filePath", None)
            base_dir = getattr(skill, "base_dir", None) or getattr(skill, "baseDir", None)
            if not isinstance(file_path, str) or not file_path:
                continue
            if os.path.basename(file_path) == "SKILL.md" and isinstance(base_dir, str) and base_dir:
                roots.append(base_dir)
            else:
                files.append(file_path)
        return {"roots": roots, "files": files}

    def _builtin_tool_options(self) -> dict[str, dict[str, object]]:
        auto_resize_images = _settings_value(
            self.settings_manager,
            "getImageAutoResize",
            "get_image_auto_resize",
            "imageAutoResize",
            "image_auto_resize",
        )
        bash_options: dict[str, object] = {
            "command_prefix": self._settings_shell_command_prefix(),
            "shell_path": self._settings_shell_path(),
            "artifacts": self._artifacts,
            "backend": self.execution_backend,
        }
        if self.process_service is not None and self.process_owner is not None and self._is_allowed_tool("process"):
            bash_options.update(
                {
                    "process_service": self.process_service,
                    "process_owner": self.process_owner,
                    "launch_session_id": self.session_id or None,
                    "transport_factory": lambda request: create_local_process_transport(
                        request,
                        self.execution_backend,
                    ),
                }
            )
        return {
            "read": {
                "auto_resize_images": True if auto_resize_images is None else bool(auto_resize_images),
                "workspace": self._workspace,
                "artifacts": self._artifacts,
            },
            "edit": {"workspace": self._workspace},
            "write": {"workspace": self._workspace},
            "grep": {"workspace": self._workspace},
            "find": {"workspace": self._workspace},
            "ls": {"workspace": self._workspace},
            "bash": bash_options,
            "tmux": {"workspace": self._workspace},
        }

    def _refresh_tool_registry(self) -> None:
        definition_by_name = dict(self._base_definition_by_name)
        source_info_by_name = dict(self._base_source_info_by_name)
        tool_by_name = dict(self._base_tool_by_name)

        for registered in self._extension_runner.get_all_registered_tools():
            definition_by_name[registered.definition.name] = registered.definition
            source_info_by_name[registered.definition.name] = registered.source_info
            tool_by_name[registered.definition.name] = wrap_registered_tool(
                registered,
                self._extension_runner,
            )

        self._tool_definition_by_name = {
            name: definition for name, definition in definition_by_name.items() if self._is_allowed_tool(name)
        }
        self._tool_source_info_by_name = {
            name: source_info
            for name, source_info in source_info_by_name.items()
            if name in self._tool_definition_by_name
        }
        self._tool_by_name = {tool.name: tool for tool in tool_by_name.values() if tool.name in self._tool_definition_by_name}

    def refresh_tools(self, *, include_all_extension_tools: bool = False) -> None:
        previous_active = self.get_active_tool_names() if hasattr(self, "agent") else []
        self._refresh_tool_registry()
        if not hasattr(self, "agent"):
            return
        next_active = list(previous_active)
        if include_all_extension_tools:
            for registered in self._extension_runner.get_all_registered_tools():
                name = registered.definition.name
                if self._is_allowed_tool(name) and name not in next_active:
                    next_active.append(name)
        self.set_active_tools_by_name(next_active)

    def _build_system_prompt(self, selected_tool_names: list[str]) -> str:
        selected_definitions = [
            self._tool_definition_by_name[name]
            for name in selected_tool_names
            if name in self._tool_definition_by_name
        ]
        snippets = {d.name: d.prompt_snippet for d in selected_definitions if d.prompt_snippet}
        guidelines: list[str] = []
        for definition in selected_definitions:
            guidelines.extend(definition.prompt_guidelines)
        return build_system_prompt(
            BuildSystemPromptOptions(
                cwd=self.cwd,
                custom_prompt=self._custom_prompt,
                selected_tools=[d.name for d in selected_definitions],
                tool_snippets=snippets,
                prompt_guidelines=guidelines,
                append_system_prompt=self._append_system_prompt,
                context_files=self._context_files,
                skills=self._resource_loader.get_skills()["skills"] if self._resource_loader else [],
            )
        )

    def _refresh_resource_prompt_inputs(self) -> None:
        append_prompts = self._resource_loader.get_append_system_prompt()
        self._custom_prompt = self._resource_loader.get_system_prompt()
        self._append_system_prompt = "\n\n".join(append_prompts) if append_prompts else None
        agents_files = self._resource_loader.get_agents_files()["agentsFiles"]
        self._context_files = [(entry["path"], entry["content"]) for entry in agents_files]

    def _extend_resources_from_extensions(self, reason: str) -> bool:
        if not self._extension_runner.has_handlers("resources_discover"):
            return False
        discovered = self._extension_runner.emit_resources_discover(self.cwd, reason)
        if not any(discovered.values()):
            return False
        self._resource_loader.extend_resources(
            {
                "skillPaths": [_extension_resource_path(entry) for entry in discovered["skillPaths"]],
                "promptPaths": [_extension_resource_path(entry) for entry in discovered["promptPaths"]],
                "themePaths": [_extension_resource_path(entry) for entry in discovered["themePaths"]],
            }
        )
        self._refresh_resource_prompt_inputs()
        return True

    def reload_resources(self, reason: str = "reload") -> None:
        self._resource_loader.reload()
        self._refresh_resource_prompt_inputs()
        self._extend_resources_from_extensions(reason)
        self.set_active_tools_by_name(self.get_active_tool_names())

    def get_active_tool_names(self) -> list[str]:
        return [tool.name for tool in self.agent.state.tools]

    def get_all_tools(self) -> list[dict]:
        return [
            _tool_info(definition, self._tool_source_info_by_name[definition.name])
            for definition in self._tool_definition_by_name.values()
        ]

    def get_known_tool_names(self) -> list[str]:
        """Return the unfiltered registry used to validate CLI selections."""

        names = list(self._base_definition_by_name)
        memory_settings = getattr(self, "_memory_settings", None)
        if getattr(memory_settings, "enabled", False) and "memory" not in names:
            names.append("memory")
        for registered in self._extension_runner.get_all_registered_tools():
            if registered.definition.name not in names:
                names.append(registered.definition.name)
        return names

    def get_tool_definition(self, name: str) -> ToolDefinition | None:
        return self._tool_definition_by_name.get(name)

    def set_active_tools_by_name(self, tool_names: list[str]) -> None:
        tools: list[AgentTool] = []
        valid_tool_names: list[str] = []
        for name in tool_names:
            tool = self._tool_by_name.get(name)
            if tool is None:
                continue
            tools.append(tool)
            valid_tool_names.append(name)
        self.agent.state.tools = tools
        self.system_prompt = self._build_system_prompt(valid_tool_names)
        self.agent.state.system_prompt = self.system_prompt

__all__ = (
    'SessionToolController',
)
