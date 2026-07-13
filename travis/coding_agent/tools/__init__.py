"""Tool factories + registry."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from travis.agent.types import AgentTool
from travis.coding_agent.tools.bash import (
    BashOperations,
    BashSpawnContext,
    BashSpawnHook,
    create_bash_tool,
    create_bash_tool_definition,
    create_local_bash_operations,
)
from travis.coding_agent.tools.edit import create_edit_tool, create_edit_tool_definition
from travis.coding_agent.tools.file_mutation_queue import with_file_mutation_queue
from travis.coding_agent.tools.find import create_find_tool, create_find_tool_definition
from travis.coding_agent.tools.grep import create_grep_tool, create_grep_tool_definition
from travis.coding_agent.tools.ls import create_ls_tool, create_ls_tool_definition
from travis.coding_agent.tools.process import create_process_tool, create_process_tool_definition
from travis.coding_agent.tools.read import create_read_tool, create_read_tool_definition
from travis.coding_agent.tools.truncate import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    TruncationResult,
    format_size,
    truncate_head,
    truncate_line,
    truncate_tail,
)
from travis.coding_agent.tools.types import ToolDefinition
from travis.coding_agent.tools.write import create_write_tool, create_write_tool_definition

ToolName = Literal["read", "bash", "edit", "write", "grep", "find", "ls"]

all_tool_names: list[str] = ["read", "bash", "edit", "write", "grep", "find", "ls"]
allToolNames: set[str] = set(all_tool_names)

_DEFINITION_FACTORIES = {
    "read": create_read_tool_definition,
    "bash": create_bash_tool_definition,
    "edit": create_edit_tool_definition,
    "write": create_write_tool_definition,
    "grep": create_grep_tool_definition,
    "find": create_find_tool_definition,
    "ls": create_ls_tool_definition,
}

_TOOL_FACTORIES = {
    "read": create_read_tool,
    "bash": create_bash_tool,
    "edit": create_edit_tool,
    "write": create_write_tool,
    "grep": create_grep_tool,
    "find": create_find_tool,
    "ls": create_ls_tool,
}


_OPTION_ALIASES = {
    "bash": {
        "commandPrefix": "command_prefix",
        "shellPath": "shell_path",
        "spawnHook": "spawn_hook",
    },
    "read": {
        "autoResizeImages": "auto_resize_images",
        "imageResizer": "image_resizer",
    },
}

_SUPPORTED_DEFINITION_OPTIONS = {
    "read": {"operations", "auto_resize_images", "image_resizer", "workspace", "artifacts"},
    "bash": {
        "operations",
        "command_prefix",
        "shell_path",
        "spawn_hook",
        "artifacts",
        "backend",
        "process_service",
        "process_owner",
        "transport_factory",
    },
    "edit": {"workspace"},
    "write": {"operations", "workspace"},
    "grep": {"operations", "workspace"},
    "find": {"operations", "workspace"},
    "ls": {"operations", "workspace"},
}

_SUPPORTED_TOOL_OPTIONS = {
    **_SUPPORTED_DEFINITION_OPTIONS,
    "read": {"operations", "auto_resize_images", "image_resizer", "model", "workspace", "artifacts"},
}


def _tool_options(options: Mapping[str, object] | None, name: str, supported: set[str]) -> dict[str, object]:
    if not isinstance(options, Mapping):
        return {}
    raw = options.get(name)
    if not isinstance(raw, Mapping):
        return {}
    normalized = dict(raw)
    for source, target in _OPTION_ALIASES.get(name, {}).items():
        if source in normalized and target not in normalized:
            normalized[target] = normalized[source]
    return {key: value for key, value in normalized.items() if key in supported}


def create_tool_definition(name: str, cwd: str, options: Mapping[str, object] | None = None) -> ToolDefinition:
    return _DEFINITION_FACTORIES[name](cwd, **_tool_options(options, name, _SUPPORTED_DEFINITION_OPTIONS[name]))


def create_tool(name: str, cwd: str, options: Mapping[str, object] | None = None) -> AgentTool:
    return _TOOL_FACTORIES[name](cwd, **_tool_options(options, name, _SUPPORTED_TOOL_OPTIONS[name]))


def create_coding_tools(cwd: str, options: Mapping[str, object] | None = None) -> list[AgentTool]:
    return [create_tool(n, cwd, options) for n in ("read", "bash", "edit", "write")]


def create_read_only_tools(cwd: str, options: Mapping[str, object] | None = None) -> list[AgentTool]:
    return [create_tool(n, cwd, options) for n in ("read", "grep", "find", "ls")]


def create_all_tools(cwd: str, options: Mapping[str, object] | None = None) -> list[AgentTool]:
    return [create_tool(name, cwd, options) for name in all_tool_names]


def create_coding_tool_definitions(cwd: str, options: Mapping[str, object] | None = None) -> list[ToolDefinition]:
    return [create_tool_definition(n, cwd, options) for n in ("read", "bash", "edit", "write")]


def create_read_only_tool_definitions(cwd: str, options: Mapping[str, object] | None = None) -> list[ToolDefinition]:
    return [create_tool_definition(n, cwd, options) for n in ("read", "grep", "find", "ls")]


def create_all_tool_definitions(cwd: str, options: Mapping[str, object] | None = None) -> list[ToolDefinition]:
    return [create_tool_definition(n, cwd, options) for n in all_tool_names]


def create_all_tools_map(cwd: str, options: Mapping[str, object] | None = None) -> dict[str, AgentTool]:
    return {name: create_tool(name, cwd, options) for name in all_tool_names}


def create_all_tool_definitions_map(cwd: str, options: Mapping[str, object] | None = None) -> dict[str, ToolDefinition]:
    return {name: create_tool_definition(name, cwd, options) for name in all_tool_names}


createReadTool = create_read_tool
createReadToolDefinition = create_read_tool_definition
createBashTool = create_bash_tool
createBashToolDefinition = create_bash_tool_definition
createLocalBashOperations = create_local_bash_operations
createProcessTool = create_process_tool
createProcessToolDefinition = create_process_tool_definition
createEditTool = create_edit_tool
createEditToolDefinition = create_edit_tool_definition
createWriteTool = create_write_tool
createWriteToolDefinition = create_write_tool_definition
createGrepTool = create_grep_tool
createGrepToolDefinition = create_grep_tool_definition
createFindTool = create_find_tool
createFindToolDefinition = create_find_tool_definition
createLsTool = create_ls_tool
createLsToolDefinition = create_ls_tool_definition
createTool = create_tool
createToolDefinition = create_tool_definition
createCodingTools = create_coding_tools
createCodingToolDefinitions = create_coding_tool_definitions
createReadOnlyTools = create_read_only_tools
createReadOnlyToolDefinitions = create_read_only_tool_definitions
createAllTools = create_all_tools_map
createAllToolDefinitions = create_all_tool_definitions_map
formatSize = format_size
truncateHead = truncate_head
truncateLine = truncate_line
truncateTail = truncate_tail
withFileMutationQueue = with_file_mutation_queue
