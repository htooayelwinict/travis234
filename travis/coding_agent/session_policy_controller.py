"""Focused policy controller ownership for coding sessions."""

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

from travis.coding_agent.session_types import _MALFORMED_STREAM_RECOVERY_PREFIX, _append_toolguard_content, _tool_result_text, _with_toolguard_details

_PROCESS_LIMIT_BASH_TURN_MARKERS = (
    "do not run any other command",
    "don't run any other command",
    "dont run any other command",
    "run any other command",
    "run no other command",
    "one command only",
    "single command only",
)
_PROCESS_LIMIT_GLOBAL_MARKERS = (
    "do not call any more tools",
    "don't call any more tools",
    "dont call any more tools",
    "stop after any tool failure",
    "stop after any failed tool",
    "stop after the first tool failure",
    "stop after the first failed tool",
)


def _is_internal_steering_user_message(text: str | None) -> bool:
    prompt = (text or "").lstrip()
    return prompt.startswith(
        (
            "[tool_guardrail_warning",
            "[user_process_limit]",
            "[System: Your previous tool call ",
            _MALFORMED_STREAM_RECOVERY_PREFIX,
        )
    )


def _user_message_has_process_limit(text: str | None) -> bool:
    prompt = (text or "").strip().lower()
    if not prompt:
        return False
    return any(marker in prompt for marker in _PROCESS_LIMIT_BASH_TURN_MARKERS)


def _user_process_limit_applies_to_tool(text: str | None, tool_name: str | None) -> bool:
    prompt = (text or "").strip().lower()
    if not prompt:
        return False
    if any(marker in prompt for marker in _PROCESS_LIMIT_GLOBAL_MARKERS):
        return True
    if (tool_name or "") != "bash":
        return False
    return _user_message_has_process_limit(prompt)


def _user_process_limit_steering_message(latest_user_message: str, tool_name: str) -> str:
    excerpt = " ".join((latest_user_message or "").split())
    if len(excerpt) > 220:
        excerpt = excerpt[:217].rstrip() + "..."
    tool = tool_name or "tool"
    return (
        "[user_process_limit] The latest user request explicitly limited attempts, retries, "
        f"or commands: \"{excerpt}\". You already executed {tool} and it returned an error. "
        "Do not call any more tools in this turn: no edit, write, append, bash, diagnostics, "
        "retries, reruns, or workaround fixes. Report the exact result and ask whether to continue."
    )


def _user_process_limit_tool_result_note(latest_user_message: str, tool_name: str) -> str:
    excerpt = " ".join((latest_user_message or "").split())
    if len(excerpt) > 220:
        excerpt = excerpt[:217].rstrip() + "..."
    tool = tool_name or "tool"
    return (
        "\n\n[User process limit: the latest request explicitly limited attempts, retries, "
        f"or commands: \"{excerpt}\". {tool} already returned an error. "
        "The runtime is stopping this turn without more tools so the agent reports this result "
        "instead of retrying or working around the limit.]"
    )


def _append_text_to_content(content, addition: str):
    blocks = list(content or [])
    if not blocks:
        return [TextContent(text=addition)]
    for index, block in enumerate(blocks):
        if isinstance(block, TextContent):
            blocks[index] = replace(block, text=block.text + addition)
            return blocks
        if getattr(block, "type", None) == "text":
            text = str(getattr(block, "text", ""))
            blocks[index] = TextContent(text=text + addition)
            return blocks
    blocks.append(TextContent(text=addition))
    return blocks


_MISSING_READ_ERROR_MARKERS = (
    "file not found",
    "no such file",
    "not found",
)

_OUTPUT_ARTIFACT_REQUEST_MARKERS = (
    "summarize",
    "summary",
    "report",
    "review",
    "notes",
    "checklist",
    "document",
    "write",
    "create",
    "generate",
    "save",
    "output",
)


def _path_mentioned_in_text(path: str, text: str) -> bool:
    needle = path.strip().lower()
    if not needle:
        return False
    normalized_needle = needle.replace("\\", "/")
    normalized_text = text.lower().replace("\\", "/")
    basename = normalized_needle.rsplit("/", 1)[-1]
    return normalized_needle in normalized_text or bool(basename and basename in normalized_text)


def _missing_read_output_artifact_note(
    tool_name: str,
    args,
    result_text: str,
    latest_user_message: str,
) -> str:
    if tool_name != "read" or not isinstance(args, Mapping):
        return ""
    path = args.get("path") or args.get("file_path")
    if not isinstance(path, str) or not path.strip():
        return ""
    lowered_result = (result_text or "").lower()
    if not any(marker in lowered_result for marker in _MISSING_READ_ERROR_MARKERS):
        return ""
    lowered_user = (latest_user_message or "").lower()
    if not _path_mentioned_in_text(path, lowered_user):
        return ""
    if not any(marker in lowered_user for marker in _OUTPUT_ARTIFACT_REQUEST_MARKERS):
        return ""
    return (
        f"\n\n[Recovery hint: read could not find {path}. The latest user request appears "
        "to name this path as an output artifact. If so, use write to create it with the "
        "requested summary/report/notes instead of treating the missing file as source "
        "content. If the user intended an existing input file, report the missing file directly.]"
    )


def _coerce_tool_guardrail_config(
    value: ToolCallGuardrailConfig | Mapping[str, object] | None,
) -> ToolCallGuardrailConfig:
    if isinstance(value, ToolCallGuardrailConfig):
        return value
    if isinstance(value, Mapping):
        return ToolCallGuardrailConfig.from_mapping(value)
    return ToolCallGuardrailConfig()

_WORKSPACE_PATH_ARG_TOOLS = {"read", "write", "edit", "grep", "find", "ls"}


def _tool_call_workspace_path_candidates(tool_name: str, args: dict) -> list[str]:
    if tool_name not in _WORKSPACE_PATH_ARG_TOOLS:
        return []
    candidates: list[str] = []
    for key in ("path", "file_path"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            candidates.append(value)
    return candidates

class SessionPolicyController:
    """Owns a focused AgentSession runtime concern."""

    def _workspace_scope_violation(self, context) -> ToolGuardrailDecision | None:
        tool_name = str(getattr(context.tool_call, "name", "") or "")
        args = context.args if isinstance(context.args, dict) else {}
        for raw_path in _tool_call_workspace_path_candidates(tool_name, args):
            violation = self._workspace_path_violation(tool_name, raw_path)
            if violation is not None:
                resolved_path, message = violation
                return self._tool_guardrails.workspace_scope_violation_decision(
                    tool_name,
                    args,
                    resolved_path,
                    message,
                )
        return None

    def _workspace_path_violation(
        self,
        tool_name: str,
        raw_path: str,
    ) -> tuple[str, str] | None:
        if tool_name == "read" and self._artifacts.resolve_read(raw_path) is not None:
            return None
        access = "read" if tool_name in {"read", "grep", "find", "ls"} else "execute" if tool_name == "bash" else "write"
        try:
            self._workspace.resolve(raw_path, access=access)
            return None
        except CapabilityViolation as error:
            resolved = str(error.resolved_path)
        return resolved, (
            f"Refusing {tool_name} outside the current working directory: {resolved}. "
            f"Current working directory is {self.cwd}."
        )

    def _before_tool_call(self, context, signal=None) -> BeforeToolCallResult | None:
        if self._process_limit_halt_message is not None:
            tool = getattr(context.tool_call, "name", None) or "tool"
            return BeforeToolCallResult(
                block=True,
                reason=(
                    "User process limit already stopped this turn after a failed limited command. "
                    f"Not executing {tool}; report the limited command result and ask whether to continue."
                ),
            )

        if self._extension_runner.has_handlers("tool_call"):
            result = self._extension_runner.emit_tool_call(
                {
                    "type": "tool_call",
                    "toolName": context.tool_call.name,
                    "toolCallId": context.tool_call.id,
                    "input": context.args,
                }
            )
            if result and result.get("block", False):
                return BeforeToolCallResult(
                    block=True,
                    reason=str(result.get("reason")) if result.get("reason") is not None else None,
                )

        workspace_violation = self._workspace_scope_violation(context)
        if workspace_violation is not None:
            if workspace_violation.should_halt:
                self._tool_guardrail_halt_decision = workspace_violation
            return BeforeToolCallResult(block=True, reason=toolguard_synthetic_result(workspace_violation))

        latest_user_message = self._latest_user_message_text(context.context)
        policy_call = ToolCallView(
            id=context.tool_call.id,
            name=context.tool_call.name,
            args=context.args if isinstance(context.args, Mapping) else {},
        )
        policy_context = CodingTurnContext(
            cwd=self.cwd,
            latest_user_message=latest_user_message,
            capabilities=self._turn_capabilities,
            tool_catalog=tuple(self.get_active_tool_names()),
            run_id=self._policy_run_id,
            turn_id=str(self._policy_turn_number),
        )
        policy_decision = self._policy_pipeline.evaluate(policy_call, policy_context)
        if not isinstance(policy_decision, Allow):
            if isinstance(policy_decision, Block) and self._tool_guardrails.halt_decision is not None:
                self._tool_guardrail_halt_decision = self._tool_guardrails.halt_decision
            self._emit(
                CodingPolicyEvent(
                    decision=policy_decision,
                    tool_call=policy_call,
                    run_id=policy_context.run_id,
                    turn_id=policy_context.turn_id,
                )
            )
            if isinstance(policy_decision, RequireConsent):
                reason = f"[{policy_decision.capability}] {policy_decision.reason}"
            else:
                reason = f"[{policy_decision.code}] {policy_decision.reason}"
            return BeforeToolCallResult(block=True, reason=reason)

        duplicate_bash_result = self._duplicate_bash_call_in_current_turn(context.tool_call.name, context.args)
        if duplicate_bash_result is not None:
            return duplicate_bash_result

        return None

    def _duplicate_bash_call_in_current_turn(self, tool_name: str, args) -> BeforeToolCallResult | None:
        if tool_name != "bash" or not isinstance(args, Mapping):
            return None
        command = args.get("command")
        if not isinstance(command, str):
            return None
        signature = command.strip()
        if not signature:
            return None
        if signature in self._bash_signatures_this_assistant_turn:
            return BeforeToolCallResult(
                block=True,
                reason=(
                    "Duplicate bash command in the same assistant turn. "
                    "Use the result already provided for this command instead of executing it again."
                ),
            )
        self._bash_signatures_this_assistant_turn.add(signature)
        return None

    def _after_tool_call(self, context, signal=None) -> AfterToolCallResult | None:
        content = context.result.content
        details = context.result.details
        is_error = context.is_error
        content_changed = False
        details_changed = False
        is_error_changed = False

        if self._extension_runner.has_handlers("tool_result"):
            result = self._extension_runner.emit_tool_result(
                {
                    "type": "tool_result",
                    "toolName": context.tool_call.name,
                    "toolCallId": context.tool_call.id,
                    "input": context.args,
                    "content": content,
                    "details": details,
                    "isError": is_error,
                }
            )
            if result:
                if result.get("content") is not None:
                    content = result.get("content")
                    content_changed = True
                if result.get("details") is not None:
                    details = result.get("details")
                    details_changed = True
                if result.get("isError") is not None:
                    is_error = bool(result.get("isError"))
                    is_error_changed = True

        result_text = _tool_result_text(content)
        if not is_error:
            detected_failure, _ = classify_tool_failure(context.tool_call.name, result_text)
            if detected_failure:
                is_error = True
                is_error_changed = True

        latest_user_message = ""
        if is_error:
            latest_user_message = self._latest_user_message_text(getattr(context, "context", None))
            missing_read_note = _missing_read_output_artifact_note(
                context.tool_call.name,
                context.args,
                result_text,
                latest_user_message,
            )
            if missing_read_note:
                content = _append_text_to_content(content, missing_read_note)
                content_changed = True
                result_text = _tool_result_text(content)

        decision = self._tool_guardrails.after_call(
            context.tool_call.name,
            context.args,
            result_text,
            failed=is_error,
        )
        if decision.action == "halt":
            content = _append_toolguard_content(content, decision)
            content_changed = True
        elif decision.action == "warn":
            content = _append_toolguard_content(content, decision)
            content_changed = True
            details = _with_toolguard_details(details, decision)
            details_changed = True
        if decision.should_halt:
            self._tool_guardrail_halt_decision = decision
            if not is_error:
                is_error = True
                is_error_changed = True

        process_limit_should_halt = False
        if is_error and not self._process_limit_recovery_steered:
            if not latest_user_message:
                latest_user_message = self._latest_user_message_text(getattr(context, "context", None))
            if _user_process_limit_applies_to_tool(latest_user_message, context.tool_call.name):
                self._process_limit_recovery_steered = True
                process_limit_should_halt = True
                content = _append_text_to_content(
                    content,
                    _user_process_limit_tool_result_note(latest_user_message, context.tool_call.name),
                )
                content_changed = True
                self._process_limit_halt_message = self._process_limit_controlled_halt_response(
                    context.tool_call.name
                )

        if not (content_changed or details_changed or is_error_changed or decision.should_halt or process_limit_should_halt):
            return None
        return AfterToolCallResult(
            content=content if content_changed else None,
            details=details if details_changed else None,
            is_error=is_error if is_error_changed else None,
            terminate=True if decision.should_halt or process_limit_should_halt else None,
        )

    def _should_stop_after_turn(self, context) -> bool:
        decision = self._tool_guardrail_halt_decision
        if decision is not None:
            if not self._tool_guardrail_halt_response_emitted:
                self._emit_toolguard_controlled_halt_response(context, decision)
            return True
        if self._process_limit_halt_message is None:
            return False
        if not self._process_limit_halt_response_emitted:
            self._emit_process_limit_controlled_halt_response(context)
        return True

    def _emit_process_limit_controlled_halt_response(self, context) -> None:
        self._process_limit_halt_response_emitted = True
        message = AssistantMessage(
            content=[TextContent(text=self._process_limit_halt_message or self._process_limit_controlled_halt_response("tool"))],
            api=self.model.api,
            provider=self.model.provider,
            model=self.model.id,
            usage=Usage(),
            stop_reason="stop",
        )
        context.context.messages.append(message)
        context.new_messages.append(message)

        start_event = MessageStartEvent(message=message)
        self.agent._process_event(start_event)
        self._handle_agent_event(start_event)

        end_event = MessageEndEvent(message=message)
        self.agent._process_event(end_event)
        self._handle_agent_event(end_event)

    def _process_limit_controlled_halt_response(self, tool_name: str) -> str:
        tool = tool_name or "tool"
        return (
            f"The single requested tool run failed in {tool}, so I stopped without calling more tools. "
            "The failing tool result above is the result of the requested run. "
            "Tell me whether to continue with fixes or another command."
        )

    def _emit_toolguard_controlled_halt_response(self, context, decision: ToolGuardrailDecision) -> None:
        self._tool_guardrail_halt_response_emitted = True
        message = AssistantMessage(
            content=[TextContent(text=self._toolguard_controlled_halt_response(decision))],
            api=self.model.api,
            provider=self.model.provider,
            model=self.model.id,
            usage=Usage(),
            stop_reason="stop",
        )
        context.context.messages.append(message)
        context.new_messages.append(message)

        start_event = MessageStartEvent(message=message)
        self.agent._process_event(start_event)
        self._handle_agent_event(start_event)

        end_event = MessageEndEvent(message=message)
        self.agent._process_event(end_event)
        self._handle_agent_event(end_event)

    def _toolguard_controlled_halt_response(self, decision: ToolGuardrailDecision) -> str:
        tool = decision.tool_name or "a tool"
        return (
            f"I stopped retrying {tool} because it hit the tool-call guardrail "
            f"({decision.code}) after {decision.count} repeated non-progressing "
            "attempts. The last tool result explains the blocker; the next step is "
            "to change strategy instead of repeating the same call."
        )

__all__ = (
    'SessionPolicyController',
    '_MISSING_READ_ERROR_MARKERS',
    '_OUTPUT_ARTIFACT_REQUEST_MARKERS',
    '_PROCESS_LIMIT_BASH_TURN_MARKERS',
    '_PROCESS_LIMIT_GLOBAL_MARKERS',
    '_WORKSPACE_PATH_ARG_TOOLS',
    '_append_text_to_content',
    '_coerce_tool_guardrail_config',
    '_is_internal_steering_user_message',
    '_missing_read_output_artifact_note',
    '_path_mentioned_in_text',
    '_tool_call_workspace_path_candidates',
    '_user_message_has_process_limit',
    '_user_process_limit_applies_to_tool',
    '_user_process_limit_steering_message',
    '_user_process_limit_tool_result_note',
)
