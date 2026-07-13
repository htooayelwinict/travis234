"""Focused subagents ownership for coding sessions."""

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

from travis.coding_agent.session_types import _CANCEL_SUBAGENT_SCHEMA, _DEFAULT_SUBAGENT_ALLOWED_TOOLS, _EXPAND_SUBAGENT_RESULT_SCHEMA, _LIST_SUBAGENTS_SCHEMA, _MODEL_SUBAGENT_SPAWN_LIMIT_PER_TURN, _SKILL_SUBAGENT_ALLOWED_TOOL_NAMES, _SPAWN_SUBAGENT_SCHEMA, _SUBAGENT_RESULT_SUMMARY_LIMIT, _TASK_ID_SCHEMA, _subagent_goal_requests_file_mutation
from travis.coding_agent.subagent_trace import _coerce_subagent_timeout_seconds, _expanded_subagent_result_details, _format_subagent_expansion, _model_subagent_timeout_seconds_arg, _optional_timeout_arg, _public_subagent_result_details, _reject_unexpected_args, _required_text_arg, _subagent_expansion_budget_arg, _subagent_expansion_offset_arg, _subagent_expansion_section_arg, _task_id_arg

class SessionSubagentController:
    """Owns a focused AgentSession runtime concern."""

    def _subagent_allowed_tools_for_role(self, role: str) -> tuple[str, ...]:
        if self._resource_loader is None:
            return _DEFAULT_SUBAGENT_ALLOWED_TOOLS
        for skill in self._resource_loader.get_skills()["skills"]:
            if getattr(skill, "name", None) != role:
                continue
            raw_allowed_tools = getattr(skill, "allowed_tools", None) or getattr(skill, "allowedTools", None) or ()
            tools: list[str] = []
            for tool in raw_allowed_tools:
                if tool not in _SKILL_SUBAGENT_ALLOWED_TOOL_NAMES or tool in tools:
                    continue
                tools.append(tool)
            if tools:
                if "read" not in tools:
                    tools.insert(0, "read")
                return tuple(tools)
        return _DEFAULT_SUBAGENT_ALLOWED_TOOLS

    @staticmethod
    def _normalize_subagent_role(role: str) -> str:
        normalized = re.sub(r"[\s_]+", "-", role.strip())
        normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
        return normalized

    def _build_subagent_task(self, role: str, goal: str, options: dict | None = None) -> SubagentTask:
        options = options or {}
        role = self._normalize_subagent_role(role)
        if "cwd" in options:
            raise ValueError("Subagent safety overrides are not supported: cwd")
        sandbox = options.get("sandbox")
        if sandbox is not None and sandbox != "read_only":
            raise ValueError("Subagent safety overrides are not supported: sandbox")
        allowed_tools = options.get("allowedTools", options.get("allowed_tools"))
        if allowed_tools is not None and tuple(allowed_tools) != _DEFAULT_SUBAGENT_ALLOWED_TOOLS:
            raise ValueError("Subagent safety overrides are not supported: allowedTools")
        timeout_value = options.get("timeoutSeconds", options.get("timeout_seconds"))
        task_options = {
            "role": role,
            "goal": goal,
            "cwd": str(options.get("cwd") or self.cwd),
            "backend": str(options.get("backend") or "internal"),
            "sandbox": str(options.get("sandbox") or "read_only"),
            "model": options.get("model"),
            "reasoning": options.get("reasoning", self.thinking_level),
            "context_pack": str(options.get("contextPack", options.get("context_pack", "")) or ""),
            "timeout_seconds": _coerce_subagent_timeout_seconds(timeout_value, default=1800),
            "allowed_tools": tuple(allowed_tools) if allowed_tools is not None else self._subagent_allowed_tools_for_role(role),
            "parent_session_id": self.session_id,
            "parent_turn_id": options.get("parentTurnId", options.get("parent_turn_id")),
        }
        return SubagentTask(**task_options)

    def _spawn_subagent_task(self, role: str, goal: str, options: dict | None = None) -> tuple[str, SubagentTask]:
        task = self._build_subagent_task(role, goal, options)
        task_id = self.subagents.spawn(task)
        return task_id, task

    def _spawn_and_wait_for_subagent(
        self,
        role: str,
        goal: str,
        options: dict | None = None,
        *,
        signal: AbortSignal | None = None,
    ) -> SubagentResult:
        task_id, task = self._spawn_subagent_task(role, goal, options)
        return self.subagents.wait(
            task_id,
            timeout=task.timeout_seconds + 1,
            signal=signal,
            cancel_reason="Cancelled by parent abort.",
        )

    def _create_subagent_tool_definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name="spawn_subagent",
                label="spawn_subagent",
                description=(
                    "Spawn a delegated child coding agent for a bounded task. Returns child task id, role, "
                    "status, summary, and lifecycle-visible result details. When the user delegates a file, "
                    "directory, report, or repo area, pass the user-provided target to the child without "
                    "using parent tools to read, find, list, grep, or resolve that target first."
                ),
                parameters=_SPAWN_SUBAGENT_SCHEMA,
                prompt_snippet="Delegate bounded review, research, or implementation tasks to child subagents.",
                prompt_guidelines=[
                    "Use spawn_subagent when the user asks for a subagent, child agent, reviewer, researcher, or parallel delegation.",
                    "Do not call parent read, bash, find, grep, or ls to inspect or resolve delegated targets before spawning.",
                    "Pass exact user-provided paths or names to the child; let the child gather and validate delegated evidence.",
                    "Report the returned taskId, role, status, and summary to the user.",
                ],
                execute=self._execute_spawn_subagent_tool,
            ),
            ToolDefinition(
                name="wait_subagent",
                label="wait_subagent",
                description="Wait for an existing subagent task to reach a terminal result.",
                parameters=_TASK_ID_SCHEMA,
                prompt_snippet="Wait for a delegated child task by task id.",
                execute=self._execute_wait_subagent_tool,
            ),
            ToolDefinition(
                name="list_subagents",
                label="list_subagents",
                description="List delegated subagents and their current statuses.",
                parameters=_LIST_SUBAGENTS_SCHEMA,
                prompt_snippet="Inspect active and completed child subagents.",
                execute=self._execute_list_subagents_tool,
            ),
            ToolDefinition(
                name="get_subagent_result",
                label="get_subagent_result",
                description="Return a completed subagent result if one is available.",
                parameters=_TASK_ID_SCHEMA,
                prompt_snippet="Fetch a child subagent result by task id without blocking indefinitely.",
                execute=self._execute_get_subagent_result_tool,
            ),
            ToolDefinition(
                name="expand_subagent_result",
                label="expand_subagent_result",
                description=(
                    "Return a bounded, paged expansion from a completed child result pack without rereading "
                    "the child-scoped files in the parent."
                ),
                parameters=_EXPAND_SUBAGENT_RESULT_SCHEMA,
                prompt_snippet="Expand a completed child result through the subagent boundary when its public summary is truncated.",
                prompt_guidelines=[
                    "Use expand_subagent_result when a child summary is truncated or too short and more child-owned detail is needed.",
                    "Prefer expand_subagent_result over parent read/bash/grep/find calls for files that were assigned to the child.",
                    "Use the smallest useful section and budget; page with offset when the expansion is still truncated.",
                ],
                execute=self._execute_expand_subagent_result_tool,
            ),
            ToolDefinition(
                name="cancel_subagent",
                label="cancel_subagent",
                description="Cancel a delegated subagent task by task id.",
                parameters=_CANCEL_SUBAGENT_SCHEMA,
                prompt_snippet="Cancel a child subagent that is no longer needed.",
                execute=self._execute_cancel_subagent_tool,
            ),
        ]

    def _execute_spawn_subagent_tool(self, _tool_call_id, args, signal=None, on_update=None, ctx=None) -> AgentToolResult:
        _reject_unexpected_args(
            args,
            {
                "role",
                "goal",
                "backend",
                "wait",
                "timeoutSeconds",
                "contextPack",
            },
        )
        role = _required_text_arg(args, "role")
        goal = _required_text_arg(args, "goal")
        context_pack = args.get("contextPack", "")
        self._reject_subagent_safety_override_text(role, goal, context_pack)
        if _subagent_goal_requests_file_mutation(goal):
            details = {
                "status": "blocked",
                "reason": "read_only_subagent_file_mutation_goal",
                "goal": goal,
                "allowedTools": list(_DEFAULT_SUBAGENT_ALLOWED_TOOLS),
            }
            return self._subagent_tool_result(
                "Subagents are read-only and cannot write, edit, create, delete, or save files. "
                "If Lewis requested a written artifact, spawn the child for inspection only, then the parent should write "
                "the requested file from the child summary. "
                "No subagent task was spawned and no taskId exists for wait_subagent, cancel_subagent, or expand_subagent_result.",
                details,
            )
        normalized_role = self._normalize_subagent_role(role)
        wait_for_result = args.get("wait", True)
        if not isinstance(wait_for_result, bool):
            raise ValueError("wait must be a boolean")
        options: dict[str, object] = {
            "timeoutSeconds": _model_subagent_timeout_seconds_arg(args),
        }
        if "backend" in args:
            options["backend"] = args["backend"]
        if "contextPack" in args:
            options["contextPack"] = context_pack
        spawn_signature = (
            normalized_role.lower(),
            re.sub(r"\s+", " ", goal.strip()).lower(),
            re.sub(r"\s+", " ", str(context_pack).strip()).lower(),
        )
        if spawn_signature in self._model_subagent_spawn_signatures_this_turn:
            details = {
                "status": "blocked",
                "reason": "duplicate_subagent_spawn_this_turn",
                "role": normalized_role,
                "spawnedThisTurn": self._model_subagents_spawned_this_turn,
            }
            return self._subagent_tool_result(
                "Subagent spawn blocked: this same role and goal already ran in this turn. "
                "Use the existing child result, summarize the blocker, or ask the user before retrying.",
                details,
            )
        if self._model_subagents_spawned_this_turn >= _MODEL_SUBAGENT_SPAWN_LIMIT_PER_TURN:
            details = {
                "status": "blocked",
                "reason": "subagent_spawn_limit_per_turn",
                "limit": _MODEL_SUBAGENT_SPAWN_LIMIT_PER_TURN,
                "spawnedThisTurn": self._model_subagents_spawned_this_turn,
            }
            return self._subagent_tool_result(
                "Subagent spawn blocked: already spawned "
                f"{_MODEL_SUBAGENT_SPAWN_LIMIT_PER_TURN} subagents in this turn. "
                "Summarize the existing child results and ask the user before launching another wave.",
                details,
            )
        task_id, task = self._spawn_subagent_task(normalized_role, goal, options)
        self._model_subagents_spawned_this_turn += 1
        self._model_subagent_spawn_signatures_this_turn.add(spawn_signature)
        if wait_for_result:
            result = self.subagents.wait(
                task_id,
                timeout=task.timeout_seconds + 1,
                signal=signal,
                cancel_reason="Cancelled by parent abort.",
            )
            return self._subagent_tool_result(self._format_subagent_result(result), _public_subagent_result_details(result))
        details = {
            "taskId": task_id,
            "role": task.role,
            "backend": task.backend,
            "status": "queued",
            "goal": task.goal,
        }
        return self._subagent_tool_result(
            f"Spawned subagent {task_id}\nrole: {task.role}\nstatus: queued\nsummary: waiting for result",
            details,
        )

    def _reject_subagent_safety_override_text(self, *values: object) -> None:
        text = "\n".join(str(value) for value in values if value is not None).lower()
        markers = (
            "cwd=",
            "cwd:",
            "sandbox=",
            "sandbox:",
            "allowedtools=",
            "allowedtools:",
            "allowedtools[",
            "allowed_tools=",
            "allowed_tools:",
            "allowed_tools[",
            "full_access",
            "danger-full-access",
            "workspace_write",
            "full access mode",
        )
        for marker in markers:
            if marker in text:
                raise ValueError("Subagent safety overrides are not supported: prompt text")

    def _execute_wait_subagent_tool(self, _tool_call_id, args, signal=None, on_update=None, ctx=None) -> AgentToolResult:
        task_id = _task_id_arg(args)
        timeout = _optional_timeout_arg(args)
        result = self.subagents.wait(
            task_id,
            timeout=timeout,
            signal=signal,
            cancel_reason="Cancelled by parent abort.",
        )
        return self._subagent_tool_result(self._format_subagent_result(result), _public_subagent_result_details(result))

    def _execute_list_subagents_tool(self, _tool_call_id, args, signal=None, on_update=None, ctx=None) -> AgentToolResult:
        tasks = self.subagents.list_tasks()
        if not tasks:
            return self._subagent_tool_result("No subagents have been spawned in this session.", {"tasks": []})
        lines = ["Subagents:"]
        for task in tasks:
            lines.append(f"- {task['taskId']} [{task['backend']}] {task['role']}: {task['status']} - {task['goal']}")
        return self._subagent_tool_result("\n".join(lines), {"tasks": tasks})

    def _execute_get_subagent_result_tool(self, _tool_call_id, args, signal=None, on_update=None, ctx=None) -> AgentToolResult:
        task_id = _task_id_arg(args)
        result = self.subagents.get_result(task_id)
        if result is None:
            return self._subagent_tool_result(f"No result is available for subagent {task_id}.", {"taskId": task_id})
        return self._subagent_tool_result(self._format_subagent_result(result), _public_subagent_result_details(result))

    def _execute_expand_subagent_result_tool(self, _tool_call_id, args, signal=None, on_update=None, ctx=None) -> AgentToolResult:
        _reject_unexpected_args(args, {"taskId", "section", "budget", "offset"})
        task_id = _task_id_arg(args)
        result = self.subagents.get_result(task_id)
        if result is None:
            return self._subagent_tool_result(
                f"No result is available for subagent {task_id}.",
                {"taskId": task_id, "status": "unavailable"},
            )
        section = _subagent_expansion_section_arg(args)
        budget = _subagent_expansion_budget_arg(args)
        offset = _subagent_expansion_offset_arg(args)
        details = _expanded_subagent_result_details(result, section=section, budget=budget, offset=offset)
        return self._subagent_tool_result(_format_subagent_expansion(details), details)

    def _execute_cancel_subagent_tool(self, _tool_call_id, args, signal=None, on_update=None, ctx=None) -> AgentToolResult:
        task_id = _task_id_arg(args)
        reason = args.get("reason", "Cancelled by user.")
        if not isinstance(reason, str):
            raise ValueError("reason must be a string")
        existing = self.subagents.get_result(task_id)
        if existing is not None:
            details = {
                "taskId": existing.task_id,
                "role": existing.role,
                "backend": existing.backend,
                "status": "blocked",
                "reason": "subagent_already_terminal",
                "terminalStatus": existing.status,
                "summary": existing.summary[:_SUBAGENT_RESULT_SUMMARY_LIMIT],
            }
            return self._subagent_tool_result(
                "Cancel skipped: subagent "
                f"{existing.task_id} is already {existing.status}. No cancellation is needed. "
                "Use the existing subagent result and do not retry cancel_subagent for this task.",
                details,
            )
        result = self.subagents.cancel(task_id, reason or "Cancelled by user.")
        return self._subagent_tool_result(self._format_subagent_result(result), _public_subagent_result_details(result))

    def _subagent_tool_result(self, content: str, details: dict[str, object]) -> AgentToolResult:
        return AgentToolResult(content=[TextContent(text=content)], details=details)

    def _run_internal_subagent(self, task: SubagentTask) -> SubagentResult:
        started = int(time.time() * 1000)
        tool_trace: list[dict[str, object]] = []
        trace_by_call_id: dict[str, dict[str, object]] = {}
        child = self._session_factory(
            cwd=task.cwd,
            model=self.model,
            active_tool_names=list(task.allowed_tools),
            allowed_tool_names=list(task.allowed_tools),
            thinking_level=self.thinking_level,
            stream_fn=self._stream_fn,
            max_iterations=12,
        )
        child.agent.subscribe(self._subagent_tool_trace_listener(task, child, tool_trace, trace_by_call_id))
        child.agent._after_tool_call = self._subagent_after_tool_call_tracer(  # noqa: SLF001 - parent observes delegated child tools.
            task,
            child,
            tool_trace,
            trace_by_call_id,
            child.agent._after_tool_call,  # noqa: SLF001
        )
        try:
            messages = child.prompt(task.prompt())
            self._reconcile_subagent_tool_results_from_messages(task, child, messages, tool_trace, trace_by_call_id)
            child_messages = list(child.agent.state.messages)
            self._reconcile_subagent_tool_results_from_messages(
                task,
                child,
                child_messages,
                tool_trace,
                trace_by_call_id,
            )
            summary = self._messages_to_summary(messages) or self._messages_to_summary(child_messages)
            guardrail = child._tool_guardrail_halt_decision.to_metadata() if child._tool_guardrail_halt_decision else None
            errors = []
            status = "completed"
            if guardrail:
                status = "failed"
                code = str(guardrail.get("code") or "tool_guardrail")
                tool = str(guardrail.get("tool_name") or guardrail.get("toolName") or "tool")
                errors.append(f"Subagent stopped by tool guardrail: {code} ({tool})")
                self._mark_subagent_trace_guardrail(task, tool_trace, guardrail)
            ended = int(time.time() * 1000)
            result = SubagentResult(
                task_id=task.id,
                backend=task.backend,
                role=task.role,
                status=status,
                summary=summary or "Internal subagent completed without a final message.",
                final_response=summary,
                errors=errors,
                tool_trace=tool_trace,
                guardrail=guardrail,
                child_session_id=child.session_id,
                started_at_ms=started,
                ended_at_ms=ended,
            )
            raw_log_path, log_errors = self._safe_write_internal_subagent_result_pack(task, result)
            if raw_log_path or log_errors:
                result = replace(result, raw_log_path=raw_log_path, errors=[*result.errors, *log_errors])
            return result
        finally:
            child.shutdown()

    def _safe_write_internal_subagent_result_pack(
        self,
        task: SubagentTask,
        result: SubagentResult,
    ) -> tuple[str | None, list[str]]:
        try:
            self._subagent_log_dir.mkdir(parents=True, exist_ok=True)
            path = self._subagent_log_dir / f"{task.id}.json"
            payload = result.as_dict()
            payload.update(
                {
                    "goal": task.goal,
                    "cwd": task.cwd,
                    "sandbox": task.sandbox,
                    "allowedTools": list(task.allowed_tools),
                    "returnContract": task.return_contract,
                    "rawLogPath": str(path),
                }
            )
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return str(path), []
        except (OSError, TypeError, ValueError) as error:
            return None, [f"Failed to write internal subagent result pack: {error}"]

__all__ = (
    'SessionSubagentController',
)
