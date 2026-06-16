from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from appv22.context.compressor import AgentContextCompressor
from appv22.context.gateway_guard import GatewayContextGuard
from appv22.context.harness import ContextHarness
from appv22.context.prompt_builder import PromptBuilder
from appv22.context.selector import ContextSelector
from appv22.extensions.registry import ExtensionRegistry
from appv22.tools.broker import ToolBroker
from appv22.tools.definitions import ToolDefinition
from appv22.tools.registry import ToolRegistry


@dataclass
class AppV22Services:
    root_path: Path
    provider: object
    extension_registry: ExtensionRegistry
    tool_registry: ToolRegistry
    broker: ToolBroker
    context_selector: ContextSelector
    prompt_builder: PromptBuilder
    gateway_guard: GatewayContextGuard
    compressor: AgentContextCompressor
    context_harness: ContextHarness | None = None


def create_appv22_services(*, root_path, provider, extensions) -> AppV22Services:
    root = Path(root_path)
    extension_registry = ExtensionRegistry()
    tool_registry = ToolRegistry()
    for extension in extensions:
        extension_registry.register(extension)
        register_tools = getattr(extension, "register_tools", None)
        if callable(register_tools):
            register_tools(tool_registry)
    _normalize_enveloped_tool_result_schemas(tool_registry)
    context_selector = ContextSelector(tool_registry=tool_registry)
    prompt_builder = PromptBuilder()
    gateway_guard = GatewayContextGuard(max_chars=120_000)
    compressor = AgentContextCompressor(max_chars=120_000)
    context_harness = ContextHarness(
        context_selector=context_selector,
        prompt_builder=prompt_builder,
        compressor=compressor,
        gateway_guard=gateway_guard,
        tool_registry=tool_registry,
    )
    return AppV22Services(
        root,
        provider,
        extension_registry,
        tool_registry,
        ToolBroker(registry=tool_registry, root_path=root),
        context_selector,
        prompt_builder,
        gateway_guard,
        compressor,
        context_harness,
    )


def _normalize_enveloped_tool_result_schemas(tool_registry: ToolRegistry) -> None:
    definitions = getattr(tool_registry, "_definitions", {})
    for tool_id, definition in list(definitions.items()):
        schema = _mutable_json_like(definition.result_schema)
        required = [name for name in schema.get("required", ()) if name != "status"]
        properties = _mutable_json_like(schema.get("properties", {}))
        properties.pop("status", None)
        schema["required"] = required
        schema["properties"] = properties
        definitions[tool_id] = ToolDefinition(
            definition.tool_id,
            definition.category,
            definition.risk_level,
            definition.argument_schema,
            schema,
            definition.trust,
            definition.guidance,
            freshness=definition.freshness,
            invalidated_by_mutation=definition.invalidated_by_mutation,
        )


def _mutable_json_like(value):
    if isinstance(value, dict) or hasattr(value, "items"):
        return {key: _mutable_json_like(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_mutable_json_like(item) for item in value]
    return value
