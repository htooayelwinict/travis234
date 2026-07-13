"""Focused extensions ownership for coding sessions."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Callable, Mapping, Optional

from travis.agent.agent import Agent
from travis.agent.types import AbortSignal
from travis.agent.types import AfterToolCallResult
from travis.agent.types import AgentContext
from travis.agent.types import AgentLoopTurnUpdate
from travis.agent.types import AgentTool
from travis.agent.types import AgentToolResult
from travis.agent.types import AgentMessage
from travis.agent.types import BeforeToolCallResult
from travis.agent.types import MessageEndEvent, MessageStartEvent
from travis.coding_agent.policies.tool_guardrails import (
    ToolCallGuardrailConfig,
    ToolCallGuardrailController,
    ToolGuardrailDecision,
    ToolLoopPolicy,
    append_toolguard_guidance,
    classify_tool_failure,
    toolguard_synthetic_result,
)
from travis.coding_agent.policies.iteration_limit import coding_iteration_limit_message
from travis.coding_agent.policies.package_consent import PackageMutationPolicy
from travis.coding_agent.policies.pipeline import PolicyPipeline
from travis.coding_agent.policies.types import (
    Allow,
    Block,
    CodingPolicyEvent,
    CodingTurnContext,
    RequireConsent,
    ToolCallView,
    TurnCapabilities,
)
from travis.ai.model_resolver import ScopedModel
from travis.ai.models import (
    clamp_thinking_level,
    get_supported_thinking_levels,
)
from travis.ai.types import AssistantMessage, Cost, ImageContent, Message, Model, TextContent, UserMessage, now_ms
from travis.ai.types import ToolCall, ToolResultMessage, Usage
from travis.compaction.compressor import LEGACY_SUMMARY_PREFIX, SUMMARY_END_MARKER, SUMMARY_PREFIX, estimate_tokens
from travis.compaction.timing import CompactionManager
from travis.coding_agent.branch_summarization import generate_branch_summary
from travis.coding_agent.artifacts import ArtifactRegistry
from travis.coding_agent.compaction_adapter import (
    SessionCompactionAdapter,
    compaction_summary_with_details,
)
from travis.coding_agent.compaction_coordinator import (
    CompactionCoordinator,
    CompactionTransactionCoordinator,
)
from travis.coding_agent.capabilities import CapabilityViolation, WorkspaceCapability
from travis.coding_agent.config import get_packaged_context_paths
from travis.coding_agent.extensions import ExtensionRunner, emit_session_shutdown_event
from travis.coding_agent.execution_backend import select_execution_backend
from travis.coding_agent.mailbox import CodingTurnMailbox, MailboxKind
from travis.coding_agent.message_utils import (
    bash_execution_text as _bash_execution_to_text,
    last_assistant_message as _last_assistant_message,
    user_message_text as _text_from_user_message_content,
)
from travis.coding_agent.object_utils import settings_value as _settings_value
from travis.coding_agent.process_context import ProcessContextResolver
from travis.coding_agent.processes.local import create_local_process_transport
from travis.coding_agent.processes.service import ProcessSessionService
from travis.coding_agent.processes.types import ProcessOwner
from travis.coding_agent.provider_control_plane import ProviderControlPlane
from travis.coding_agent.resource_loader import DefaultResourceLoader
from travis.coding_agent.session_index import SessionIndex
from travis.coding_agent.session_store import (
    BashExecutionMessage,
    BranchSummaryMessage,
    CustomMessage,
    SessionStore,
    deserialize_message,
)
from travis.coding_agent.settings_manager import SettingsManager
from travis.coding_agent.source_info import SourceInfo, create_synthetic_source_info
from travis.coding_agent.system_prompt import BuildSystemPromptOptions, build_system_prompt
from travis.coding_agent.subagents import (
    CallableSubagentBackend,
    CodexExecBackend,
    SubagentResult,
    SubagentSupervisor,
    SubagentTask,
)
from travis.coding_agent.tools import create_all_tool_definitions
from travis.coding_agent.tools.bash import BashExecOptions, BashOperations, create_local_bash_operations, get_shell_env
from travis.coding_agent.tools.output_spool import OutputSpool
from travis.coding_agent.tools.process import PROCESS_ACTIONS, create_process_tool_definition, prepare_process_arguments
from travis.coding_agent.tools.types import (
    ToolContext,
    ToolDefinition,
    create_tool_definition_from_agent_tool,
    wrap_tool_definition,
)

from travis.coding_agent.session_types import ExtensionCommandContext, ExtensionCompactionResult
from travis.coding_agent.subagent_trace import _message_content_text, _public_subagent_result_details

def _extract_compaction_result_summary(messages: list[Message]) -> str:
    for message in messages:
        text = _message_content_text(getattr(message, "content", ""))
        if text.startswith(SUMMARY_PREFIX) or text.startswith(LEGACY_SUMMARY_PREFIX):
            for prefix in (SUMMARY_PREFIX, LEGACY_SUMMARY_PREFIX):
                if text.startswith(prefix):
                    text = text[len(prefix) :]
                    break
            marker_index = text.find(SUMMARY_END_MARKER)
            if marker_index >= 0:
                text = text[:marker_index]
            return text.strip()
    return ""


def _validate_extension_provider_config(provider_name: str, config: dict) -> None:
    stream_simple = config.get("streamSimple") or config.get("stream_simple")
    if callable(stream_simple) and not config.get("api"):
        raise RuntimeError(f'Provider {provider_name}: "api" is required when registering streamSimple.')

    model_configs = config.get("models")
    if not isinstance(model_configs, list) or not model_configs:
        return

    if not config.get("baseUrl") and not config.get("base_url"):
        raise RuntimeError(f'Provider {provider_name}: "baseUrl" is required when defining models.')
    has_oauth = "oauth" in config and config.get("oauth") is not None
    if not config.get("apiKey") and not config.get("api_key") and not has_oauth:
        raise RuntimeError(f'Provider {provider_name}: "apiKey" or "oauth" is required when defining models.')

    for model_config in model_configs:
        if not isinstance(model_config, dict):
            continue
        if not model_config.get("api") and not config.get("api"):
            raise RuntimeError(f'Provider {provider_name}, model {model_config.get("id")}: no "api" specified.')


def _apply_provider_config_to_model(model: Model, config: dict) -> Model:
    updates = {}
    base_url = config.get("baseUrl", config.get("base_url"))
    if base_url is not None:
        updates["base_url"] = str(base_url)
    api = config.get("api")
    if api is not None:
        updates["api"] = str(api)
    return replace(model, **updates) if updates else model


def _model_from_provider_config(provider: str, provider_config: dict, model_config: dict) -> Model:
    api = str(model_config.get("api") or provider_config.get("api") or provider)
    base_url = str(
        model_config.get("baseUrl")
        or model_config.get("base_url")
        or provider_config.get("baseUrl")
        or provider_config.get("base_url")
        or ""
    )
    return Model(
        id=str(model_config["id"]),
        name=str(model_config.get("name") or model_config["id"]),
        api=api,
        provider=provider,
        base_url=base_url,
        reasoning=bool(model_config.get("reasoning", False)),
        thinking_level_map=model_config.get("thinkingLevelMap") or model_config.get("thinking_level_map"),
        input=list(model_config.get("input") or ["text"]),
        cost=_cost_from_provider_model_config(model_config.get("cost")),
        context_window=int(model_config.get("contextWindow") or model_config.get("context_window") or 0),
        max_tokens=int(model_config.get("maxTokens") or model_config.get("max_tokens") or 0),
    )


def _cost_from_provider_model_config(cost: object) -> Cost:
    if not isinstance(cost, dict):
        return Cost()
    return Cost(
        input=float(cost.get("input", 0.0)),
        output=float(cost.get("output", 0.0)),
        cache_read=float(cost.get("cacheRead", cost.get("cache_read", 0.0))),
        cache_write=float(cost.get("cacheWrite", cost.get("cache_write", 0.0))),
    )


def _replace_message_in_place(target: AgentMessage, replacement: AgentMessage) -> None:
    if target is replacement:
        return
    if hasattr(target, "__dict__") and hasattr(replacement, "__dict__"):
        target.__dict__.clear()
        target.__dict__.update(replacement.__dict__)

def _tool_info(definition: ToolDefinition, source_info: SourceInfo) -> dict:
    prompt_guidelines = list(definition.prompt_guidelines)
    source_info_dict = source_info.to_dict()
    return {
        "name": definition.name,
        "description": definition.description,
        "parameters": definition.parameters,
        "promptGuidelines": prompt_guidelines,
        "prompt_guidelines": prompt_guidelines,
        "sourceInfo": source_info_dict,
        "source_info": source_info_dict,
    }


def _has_binding(bindings: dict[str, object], *names: str) -> bool:
    return any(name in bindings for name in names)


def _binding_value(bindings: dict[str, object], *names: str) -> object | None:
    for name in names:
        if name in bindings:
            return bindings[name]
    return None


def _extension_resource_path(entry: dict[str, str]) -> dict[str, object]:
    extension_path = entry.get("extensionPath", "<python-extension>")
    extension_name = extension_path.strip("<>").split(":", 1)[-1] if extension_path.startswith("<") else extension_path
    return {
        "path": entry["path"],
        "metadata": {
            "source": f"extension:{extension_name}",
            "scope": "temporary",
            "origin": "top-level",
        },
    }

class SessionExtensionController:
    """Owns a focused AgentSession runtime concern."""

    def bind_extensions(self, bindings: dict[str, object] | None = None) -> None:
        bindings = bindings or {}
        if _has_binding(bindings, "uiContext", "ui_context"):
            self._extension_ui_context = _binding_value(bindings, "uiContext", "ui_context")
        if _has_binding(bindings, "mode"):
            self._extension_mode = str(_binding_value(bindings, "mode") or "print")
        if _has_binding(bindings, "commandContextActions", "command_context_actions"):
            self._extension_command_context_actions = _binding_value(
                bindings,
                "commandContextActions",
                "command_context_actions",
            )
        if _has_binding(bindings, "abortHandler", "abort_handler"):
            abort_handler = _binding_value(bindings, "abortHandler", "abort_handler")
            self._extension_abort_handler = abort_handler if callable(abort_handler) else None
        if _has_binding(bindings, "shutdownHandler", "shutdown_handler"):
            shutdown_handler = _binding_value(bindings, "shutdownHandler", "shutdown_handler")
            self._extension_shutdown_handler = shutdown_handler if callable(shutdown_handler) else None
        if _has_binding(bindings, "onError", "on_error"):
            error_listener = _binding_value(bindings, "onError", "on_error")
            self._extension_error_listener = error_listener if callable(error_listener) else None
        self._apply_extension_bindings()
        self._extensions_bound = True
        self._defer_session_start = False
        self._extension_runner.emit(self._session_start_event)
        reason = "reload" if self._session_start_event.get("reason") == "reload" else "startup"
        if self._extend_resources_from_extensions(reason):
            self.set_active_tools_by_name(self.get_active_tool_names())

    def _apply_extension_bindings(self) -> None:
        self._extension_runner.set_ui_context(self._extension_ui_context, self._extension_mode)
        self._extension_runner.bind_command_context(self._extension_command_context_actions)
        self._extension_runner.set_abort_handler(self._extension_abort_handler)
        self._extension_runner.set_shutdown_handler(self._extension_shutdown_handler)
        if self._extension_error_unsubscribe is not None:
            self._extension_error_unsubscribe()
            self._extension_error_unsubscribe = None
        if self._extension_error_listener is not None:
            self._extension_error_unsubscribe = self._extension_runner.on_error(self._extension_error_listener)

    def reload(self) -> None:
        previous_flag_values = self._extension_runner.get_flag_values()
        emit_session_shutdown_event(self._extension_runner, {"type": "session_shutdown", "reason": "reload"})
        self._resource_loader.reload()
        for name, value in previous_flag_values.items():
            self._extension_runner.set_flag_value(name, value)
        self._refresh_resource_prompt_inputs()
        self.refresh_tools(include_all_extension_tools=True)
        self._apply_extension_bindings()
        if self._extensions_bound or self._extension_error_listener is not None:
            self._extension_runner.emit({"type": "session_start", "reason": "reload"})
            if self._extend_resources_from_extensions("reload"):
                self.set_active_tools_by_name(self.get_active_tool_names())

    def dispose(self) -> None:
        self._turn_mailbox.close()
        try:
            self.agent.abort()
        except Exception:
            pass
        if self._extension_error_unsubscribe is not None:
            self._extension_error_unsubscribe()
            self._extension_error_unsubscribe = None
        unsubscribe = getattr(self, "_unsubscribe_agent", None)
        if unsubscribe:
            unsubscribe()
            self._unsubscribe_agent = None
        self._event_listeners = []
        for registration in self._extension_provider_registrations.values():
            registration.close()
        self._extension_provider_registrations.clear()
        self._artifacts.close(remove_files=True)

    def _try_execute_extension_command(self, text: str) -> list[AgentMessage] | None:
        parsed = self._parse_extension_command(text)
        if parsed is None:
            return None
        command, args = parsed
        def run_command(_signal: AbortSignal):
            try:
                return command.handler(args, self._extension_command_context())
            except TypeError:
                return command.handler(args)

        result = self._with_command_abort_signal(run_command)
        return result if isinstance(result, list) else []

    def _parse_extension_command(self, text: str):
        if not text.startswith("/"):
            return None
        command_text = text[1:]
        if not command_text:
            return None
        command_name, separator, args = command_text.partition(" ")
        command = self._extension_runner.get_registered_command(command_name)
        if command is None:
            return None
        return command, args if separator else ""

    def _raise_if_extension_command(self, text: str) -> None:
        parsed = self._parse_extension_command(text)
        if parsed is None:
            return
        command, _args = parsed
        raise RuntimeError(
            f'Extension command "/{command.name}" cannot be queued. Use prompt() or execute the command when not streaming.'
        )

    def _extension_command_context(self) -> ExtensionCommandContext:
        return ExtensionCommandContext(
            cwd=self.cwd,
            _get_system_prompt=lambda: self.system_prompt,
            _get_system_prompt_options=lambda: self._system_prompt_options_snapshot(),
            _send_message=self.send_custom_message,
            _send_user_message=self._extension_send_user_message,
            _append_entry=self.append_custom_entry,
            _set_session_name=self.set_session_name,
            _get_session_name=lambda: self.session_name,
            _get_active_tools=self.get_active_tool_names,
            _get_all_tools=self.get_all_tools,
            _set_active_tools=self.set_active_tools_by_name,
            _get_commands=self._extension_command_infos,
            _get_thinking_level=lambda: self.thinking_level,
            _set_thinking_level=self.set_thinking_level,
            _set_model=self._extension_set_model,
            _set_label=self.set_label,
            _exec=self._extension_exec,
            _wait_for_idle=self._extension_wait_for_idle,
            _get_signal=self._current_abort_signal,
            _compact=self._extension_compact,
            _spawn_subagent=self._extension_spawn_subagent,
            _list_subagents=lambda: self.subagents.list_tasks(),
            _get_subagent_result=self._extension_get_subagent_result,
            _cancel_subagent=self._extension_cancel_subagent,
        )

    def create_replaced_session_context(self) -> ExtensionCommandContext:
        return self._extension_command_context()

    def _extension_command_infos(self) -> list[dict]:
        return [
            {"name": command.name, "description": command.description}
            for command in self._extension_runner.get_all_registered_commands()
        ]

    def _register_builtin_subagent_commands(self) -> None:
        self._extension_runner.register_command(
            "agents",
            {
                "description": "List delegated subagents and their status",
                "handler": self._agents_command,
            },
        )
        self._extension_runner.register_command(
            "delegate",
            {
                "description": "Delegate a bounded task: /delegate <role> <task>",
                "handler": self._delegate_command,
            },
        )
        self._extension_runner.register_command(
            "cancel-agent",
            {
                "description": "Cancel a delegated subagent: /cancel-agent <task-id> [reason]",
                "handler": self._cancel_agent_command,
            },
        )

    def _agents_command(self, args: str = "", _ctx: object | None = None) -> list[AgentMessage]:
        tasks = self.subagents.list_tasks()
        if not tasks:
            content = "No subagents have been spawned in this session."
        else:
            lines = ["Subagents:"]
            for task in tasks:
                lines.append(
                    f"- {task['taskId']} [{task['backend']}] {task['role']}: {task['status']} - {task['goal']}"
                )
            content = "\n".join(lines)
        return self.send_custom_message({"customType": "subagent", "content": content, "display": True}, {"transient": True})

    def _delegate_command(self, args: str = "", _ctx: object | None = None) -> list[AgentMessage]:
        backend = "internal"
        remaining = args.strip()
        if remaining.startswith("--backend "):
            _, _, rest = remaining.partition(" ")
            backend, _, remaining = rest.partition(" ")
            backend = backend.strip() or "internal"
            remaining = remaining.strip()
        role, separator, goal = remaining.partition(" ")
        if not separator or not role.strip() or not goal.strip():
            return self.send_custom_message(
                {
                    "customType": "subagent",
                    "content": "Usage: /delegate [--backend codex|internal] <role> <task>",
                    "display": True,
                }
            )
        result = self._with_command_abort_signal(
            lambda signal: self._spawn_and_wait_for_subagent(
                role.strip(),
                goal.strip(),
                {"backend": backend},
                signal=signal,
            )
        )
        return self.send_custom_message(
            {
                "customType": "subagent",
                "content": self._format_subagent_result(result),
                "display": True,
                "details": _public_subagent_result_details(result),
            }
        )

    def _cancel_agent_command(self, args: str = "", _ctx: object | None = None) -> list[AgentMessage]:
        task_id, _separator, reason = args.strip().partition(" ")
        if not task_id:
            return self.send_custom_message(
                {
                    "customType": "subagent",
                    "content": "Usage: /cancel-agent <task-id> [reason]",
                    "display": True,
                },
                {"transient": True},
            )
        try:
            result = self.subagents.cancel(task_id, reason.strip() or "Cancelled by user.")
        except KeyError as error:
            message = str(error.args[0]) if error.args else str(error)
            return self.send_custom_message(
                {
                    "customType": "subagent",
                    "content": message,
                    "display": True,
                },
                {"transient": True},
            )
        return self.send_custom_message(
            {
                "customType": "subagent",
                "content": self._format_subagent_result(result),
                "display": True,
                "details": _public_subagent_result_details(result),
            },
            {"transient": True},
        )

    def _bind_extension_core(self) -> None:
        self._extension_runner._cwd = self.cwd
        self._extension_runner.bind_core(
            {
                "sendMessage": self.send_custom_message,
                "sendUserMessage": self._extension_send_user_message,
                "appendEntry": self.append_custom_entry,
                "setSessionName": self.set_session_name,
                "getSessionName": lambda: self.session_name,
                "setLabel": self.set_label,
                "getActiveTools": self.get_active_tool_names,
                "getAllTools": self.get_all_tools,
                "setActiveTools": self.set_active_tools_by_name,
                "refreshTools": self.refresh_tools,
                "getCommands": self._extension_command_infos,
                "setModel": self._extension_set_model,
                "getThinkingLevel": lambda: self.thinking_level,
                "setThinkingLevel": self.set_thinking_level,
                "spawnSubagent": self._extension_spawn_subagent,
                "listSubagents": lambda: self.subagents.list_tasks(),
                "getSubagentResult": self._extension_get_subagent_result,
                "cancelSubagent": self._extension_cancel_subagent,
            },
            {
                "getModel": lambda: self.model,
                "isIdle": lambda: not self.is_streaming,
                "isProjectTrusted": lambda: True,
                "getSignal": self._current_abort_signal,
                "abort": self._extension_abort,
                "hasPendingMessages": lambda: self.pending_message_count > 0,
                "shutdown": self._extension_shutdown,
                "getContextUsage": self.get_context_usage,
                "compact": self._extension_compact,
                "getSystemPrompt": lambda: self.system_prompt,
                "getSystemPromptOptions": self._system_prompt_options_snapshot,
            },
        )

    def _extension_spawn_subagent(self, role: str, goal: str, options: dict | None = None) -> dict:
        return self._with_command_abort_signal(
            lambda signal: _public_subagent_result_details(
                self._spawn_and_wait_for_subagent(role, goal, options, signal=signal)
            )
        )

    def _current_abort_signal(self) -> AbortSignal:
        if self.is_streaming or self._command_signal is not None:
            return self.agent.signal
        return self.agent.reset_abort_signal()

    def _with_command_abort_signal(self, callback: Callable[[AbortSignal], object]):
        signal = self._current_abort_signal()
        owns_signal = not self.is_streaming and self._command_signal is None
        if owns_signal:
            self._command_signal = signal
        try:
            return callback(signal)
        finally:
            if owns_signal and self._command_signal is signal:
                self._command_signal = None

    def _extension_get_subagent_result(self, task_id: str) -> dict | None:
        result = self.subagents.get_result(task_id)
        return _public_subagent_result_details(result) if result is not None else None

    def _extension_cancel_subagent(self, task_id: str, reason: str | None = None) -> dict:
        return _public_subagent_result_details(self.subagents.cancel(task_id, reason or "Cancelled by user."))

    def _extension_abort(self) -> None:
        if self._extension_abort_handler is not None:
            self._extension_abort_handler()
            return
        self.agent.abort()

    def _extension_shutdown(self) -> None:
        if self._extension_shutdown_handler is not None:
            self._extension_shutdown_handler()

    def shutdown(self, reason: str = "quit", target_session_file: str | None = None) -> None:
        self._turn_mailbox.close()
        self.subagents.shutdown(wait=False, reason="Session shutdown.")
        event: dict[str, object] = {"type": "session_shutdown", "reason": reason}
        if target_session_file is not None:
            event["targetSessionFile"] = target_session_file
        emit_session_shutdown_event(self._extension_runner, event)
        if self._extension_error_unsubscribe is not None:
            self._extension_error_unsubscribe()
            self._extension_error_unsubscribe = None
        for registration in self._extension_provider_registrations.values():
            registration.close()
        self._extension_provider_registrations.clear()
        self._artifacts.close(remove_files=True)

    def set_label(self, entry_id: str, label: str | None) -> None:
        if self._session_store is None:
            return
        self._session_store.append_label_change(entry_id, label)

    def _extension_wait_for_idle(self) -> None:
        self.agent.wait_for_idle()

    def _extension_set_model(self, model: Model) -> bool:
        self.set_model(model)
        return True

    def _extension_exec(self, command: str, args: list[str], options: dict | None = None) -> dict:
        options = options or {}
        cwd = str(options.get("cwd") or self.cwd)
        timeout = options.get("timeout")
        env = get_shell_env()
        try:
            completed = subprocess.run(
                [command, *args],
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=timeout / 1000 if isinstance(timeout, (int, float)) else None,
                check=False,
            )
            return {
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "code": completed.returncode,
                "killed": False,
            }
        except subprocess.TimeoutExpired as error:
            return {
                "stdout": error.stdout or "",
                "stderr": error.stderr or "",
                "code": -1,
                "killed": True,
            }

    def _extension_compact(self, options: dict | None = None) -> ExtensionCompactionResult | None:
        options = options or {}
        custom_instructions = options.get("customInstructions")
        on_complete = options.get("onComplete")
        on_error = options.get("onError")
        before_messages = list(self.messages)
        try:
            status = self.compact(str(custom_instructions) if custom_instructions is not None else None)
            result = ExtensionCompactionResult(
                summary=status.summary or _extract_compaction_result_summary(status.messages),
                first_kept_entry_id=status.first_kept_entry_id or "",
                tokens_before=status.tokens_before or estimate_tokens(before_messages),
                details={"status": status},
            )
        except Exception as error:  # noqa: BLE001 - preserves the established callback-based extension compaction failure.
            if callable(on_error):
                on_error(error)
                return None
            raise
        if callable(on_complete):
            on_complete(result)
        return result

    def _extension_send_user_message(
        self,
        content: str | list[TextContent | ImageContent],
        options: dict | None = None,
    ) -> list[AgentMessage] | None:
        options = options or {}
        text = _text_from_user_message_content(content)
        deliver_as = options.get("deliverAs", options.get("deliver_as"))
        if deliver_as == "steer":
            self.steer(text)
            return None
        if deliver_as == "followUp" or deliver_as == "follow_up":
            self.follow_up(text)
            return None
        return self.prompt(text)

    def _system_prompt_options_snapshot(self) -> BuildSystemPromptOptions:
        active_tool_names = self.get_active_tool_names()
        selected_definitions = [
            self._tool_definition_by_name[name]
            for name in active_tool_names
            if name in self._tool_definition_by_name
        ]
        snippets = {definition.name: definition.prompt_snippet for definition in selected_definitions if definition.prompt_snippet}
        guidelines: list[str] = []
        for definition in selected_definitions:
            guidelines.extend(definition.prompt_guidelines)
        return BuildSystemPromptOptions(
            cwd=self.cwd,
            custom_prompt=self._custom_prompt,
            selected_tools=[definition.name for definition in selected_definitions],
            tool_snippets=snippets,
            prompt_guidelines=guidelines,
            append_system_prompt=self._append_system_prompt,
            context_files=list(self._context_files),
            skills=self._resource_loader.get_skills()["skills"] if self._resource_loader else [],
        )

    def _register_extension_provider(self, name: str, config: dict) -> None:
        _validate_extension_provider_config(name, config)
        previous_registration = self._extension_provider_registrations.pop(name, None)
        if previous_registration is not None:
            previous_registration.close()
        self._extension_provider_registrations[name] = self.provider_control_plane.register_extension(
            f"extension:{name}",
            {"provider": name, **config},
        )

        current = self.agent.state.model
        if current.provider != name:
            return
        self._extension_provider_original_models.setdefault(name, current)
        updated = _apply_provider_config_to_model(current, config)
        if updated != current:
            self.agent.state.model = updated

    def _unregister_extension_provider(self, name: str) -> None:
        registration = self._extension_provider_registrations.pop(name, None)
        if registration is not None:
            registration.close()
        original = self._extension_provider_original_models.pop(name, None)
        if original is not None and self.agent.state.model.provider == name:
            self.agent.state.model = original

__all__ = (
    'SessionExtensionController',
    '_apply_provider_config_to_model',
    '_binding_value',
    '_cost_from_provider_model_config',
    '_extension_resource_path',
    '_extract_compaction_result_summary',
    '_has_binding',
    '_model_from_provider_config',
    '_replace_message_in_place',
    '_tool_info',
    '_validate_extension_provider_config',
)
