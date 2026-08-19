"""Focused subagents ownership for coding sessions."""

from __future__ import annotations

import json
import re
import stat
import time
from dataclasses import replace
from pathlib import Path
from typing import cast

from travis.agent.types import (
    AbortSignal,
    AgentToolResult,
)
from travis.ai.types import (
    TextContent,
)
from travis.coding_agent.artifact_store import ArtifactPromotionError
from travis.coding_agent.policy.context import fixed_action_context, subagent_policy_context
from travis.coding_agent.policy.types import ALL_TOOL_EFFECTS
from travis.coding_agent.processes.types import ProcessOwner
from travis.coding_agent.session_surfaces import SessionSubagentControllerSurface
from travis.coding_agent.session_types import (
    _CANCEL_SUBAGENT_SCHEMA,
    _DEFAULT_SUBAGENT_ALLOWED_TOOLS,
    _EXPAND_SUBAGENT_RESULT_SCHEMA,
    _LIST_SUBAGENTS_SCHEMA,
    _MODEL_SUBAGENT_SPAWN_LIMIT_PER_TURN,
    _SKILL_SUBAGENT_ALLOWED_TOOL_NAMES,
    _SPAWN_SUBAGENT_SCHEMA,
    _SUBAGENT_RESULT_SUMMARY_LIMIT,
    _TASK_ID_SCHEMA,
)
from travis.coding_agent.subagent_roles import resolve_agent_role, typed_role_prompt_guidelines
from travis.coding_agent.subagent_supervision import ControlResult
from travis.coding_agent.subagent_trace import (
    _coerce_subagent_timeout_seconds,
    _expanded_subagent_result_details,
    _format_subagent_expansion,
    _model_subagent_timeout_seconds_arg,
    _optional_timeout_arg,
    _public_subagent_result_details,
    _public_subagent_tool_trace,
    _reject_unexpected_args,
    _replace_artifact_paths,
    _required_text_arg,
    _subagent_changed_files,
    _subagent_expansion_budget_arg,
    _subagent_expansion_offset_arg,
    _subagent_expansion_section_arg,
    _task_id_arg,
)
from travis.coding_agent.subagents import (
    CODING_SUBAGENT_TOOLS,
    SubagentResult,
    SubagentTask,
)
from travis.coding_agent.tools.types import (
    ToolDefinition,
)


def _merge_role_context(role_context: str, task_context: str) -> str:
    parts = [part.strip() for part in (role_context, task_context) if part.strip()]
    return "\n\n".join(parts)[:65_536]


class _InternalSubagentControlHandle:
    def __init__(self, child: object) -> None:
        self._child = child

    def steer(self, message: str) -> ControlResult:
        steer = getattr(self._child, "steer", None)
        if not callable(steer):
            return ControlResult(False, "steering_unsupported")
        steer(message)
        return ControlResult(True, "steering_queued")

    def cancel(self, reason: str) -> ControlResult:
        del reason
        agent = getattr(self._child, "agent", None)
        abort = getattr(agent, "abort", None)
        if callable(abort):
            abort()
        for name in ("abort_bash", "abort_retry"):
            callback = getattr(self._child, name, None)
            if callable(callback):
                callback()
        return ControlResult(True, "cancellation_requested")

class SessionSubagentController(SessionSubagentControllerSurface):
    """Owns a focused AgentSession runtime concern."""

    __slots__ = ()

    def _subagent_allowed_tools_for_role(self, role: str) -> tuple[str, ...]:
        if self._resource_loader is None:
            return _DEFAULT_SUBAGENT_ALLOWED_TOOLS
        for skill in self._resource_loader.get_skills()["skills"]:
            if getattr(skill, "name", None) != role:
                continue
            raw_allowed_tools = cast(
                tuple[str, ...] | list[str],
                getattr(skill, "allowed_tools", None) or getattr(skill, "allowedTools", None) or (),
            )
            tools = tuple(dict.fromkeys(raw_allowed_tools))
            if tools and all(tool in _SKILL_SUBAGENT_ALLOWED_TOOL_NAMES for tool in tools):
                return tools
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
        if sandbox is not None and sandbox != "workspace_write":
            raise ValueError("Subagent safety overrides are not supported: sandbox")
        allowed_tools = options.get("allowedTools", options.get("allowed_tools"))
        if allowed_tools is not None:
            if isinstance(allowed_tools, str):
                raise ValueError("Subagent safety overrides are not supported: allowedTools")
            allowed_tools = tuple(allowed_tools)
            if not allowed_tools or any(tool not in CODING_SUBAGENT_TOOLS for tool in allowed_tools):
                raise ValueError("Subagent safety overrides are not supported: allowedTools")
        timeout_value = options.get("timeoutSeconds", options.get("timeout_seconds"))
        requested_timeout = (
            _coerce_subagent_timeout_seconds(timeout_value, default=1800)
            if timeout_value is not None
            else None
        )
        definition_name = options.get(
            "roleDefinitionName", options.get("role_definition_name")
        )
        definition = None
        if self._resource_loader is not None:
            registry = self._resource_loader.get_agent_roles()
            if definition_name is not None:
                if not isinstance(definition_name, str) or not definition_name:
                    raise ValueError("roleDefinitionName must be a non-empty string")
                definition = registry.get(definition_name)
                if definition is None:
                    raise ValueError(f'Unknown agent role definition: "{definition_name}"')
            else:
                definition = registry.get(role)
        resolved_role = None
        if definition is not None:
            parent_tools = tuple(self.get_active_tool_names())
            if allowed_tools is not None:
                requested_tool_set = set(allowed_tools)
                parent_tools = tuple(
                    name for name in parent_tools if name in requested_tool_set
                )
            resolved_role = resolve_agent_role(
                definition,
                parent_tools=parent_tools,
                definitions_by_name=self._tool_definition_by_name,
                requested_timeout=requested_timeout,
            )
        task_options = {
            "role": role,
            "goal": goal,
            "cwd": str(options.get("cwd") or self.cwd),
            "backend": str(options.get("backend") or "internal"),
            "sandbox": str(options.get("sandbox") or "workspace_write"),
            "model": options.get("model"),
            "reasoning": options.get("reasoning"),
            "context_pack": _merge_role_context(
                resolved_role.context_pack if resolved_role is not None else "",
                str(options.get("contextPack", options.get("context_pack", "")) or ""),
            ),
            "timeout_seconds": (
                resolved_role.timeout_seconds
                if resolved_role is not None
                else requested_timeout or 1800
            ),
            "allowed_tools": (
                resolved_role.allowed_tools
                if resolved_role is not None
                else tuple(allowed_tools) if allowed_tools is not None else self._subagent_allowed_tools_for_role(role)
            ),
            "parent_session_id": self.session_id,
            "parent_turn_id": options.get("parentTurnId", options.get("parent_turn_id")),
            "role_definition_name": (
                resolved_role.definition_name if resolved_role is not None else None
            ),
            "allowed_effects": (
                resolved_role.allowed_effects if resolved_role is not None else None
            ),
            "model_role": resolved_role.model_role if resolved_role is not None else None,
            "result_schema": resolved_role.result_schema if resolved_role is not None else None,
            "artifact_policy": (
                resolved_role.artifact_policy if resolved_role is not None else "none"
            ),
        }
        return SubagentTask(**task_options)

    def _spawn_subagent_task(self, role: str, goal: str, options: dict | None = None) -> tuple[str, SubagentTask]:
        task = self._build_subagent_task(role, goal, options)
        task_id = self.subagents.spawn(task)
        return task_id, task

    def _invoke_spawn_subagent_task(
        self,
        role: str,
        goal: str,
        options: dict | None = None,
    ) -> tuple[str, SubagentTask]:
        spawn = self.dependencies._spawn_subagent_task.get()
        if not callable(spawn):
            raise TypeError("subagent spawn dependency is not callable")
        result = spawn(role, goal, options)
        if (
            not isinstance(result, tuple)
            or len(result) != 2
            or not isinstance(result[0], str)
            or not isinstance(result[1], SubagentTask)
        ):
            raise TypeError("subagent spawn dependency returned an invalid task")
        return result

    def _spawn_and_wait_for_subagent(
        self,
        role: str,
        goal: str,
        options: dict | None = None,
        *,
        signal: AbortSignal | None = None,
    ) -> SubagentResult:
        task_id, task = self._invoke_spawn_subagent_task(role, goal, options)
        return self._prepare_public_subagent_result(
            self.subagents.wait(
                task_id,
                timeout=task.timeout_seconds + 1,
                signal=signal,
                cancel_reason="Cancelled by parent abort.",
            )
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
                    "Pass the user's exact delegated path or name directly to the child; do not inspect or resolve that target first with parent tools.",
                    "Spawn independent children together with wait=false; collect every result and verify child evidence before finalizing.",
                    *typed_role_prompt_guidelines(self._resource_loader),
                ],
                execute=self._execute_spawn_subagent_tool,
                effects=ALL_TOOL_EFFECTS,
                policy_context=subagent_policy_context,
            ),
            ToolDefinition(
                name="wait_subagent",
                label="wait_subagent",
                description="Wait for an existing subagent task to reach a terminal result.",
                parameters=_TASK_ID_SCHEMA,
                prompt_snippet="Wait for a delegated child task by task id.",
                execute=self._execute_wait_subagent_tool,
                effects=frozenset({"read"}),
                policy_context=fixed_action_context("wait"),
            ),
            ToolDefinition(
                name="list_subagents",
                label="list_subagents",
                description="List delegated subagents and their current statuses.",
                parameters=_LIST_SUBAGENTS_SCHEMA,
                prompt_snippet="Inspect active and completed child subagents.",
                execute=self._execute_list_subagents_tool,
                effects=frozenset({"read"}),
                policy_context=fixed_action_context("list"),
            ),
            ToolDefinition(
                name="get_subagent_result",
                label="get_subagent_result",
                description="Return a completed subagent result if one is available.",
                parameters=_TASK_ID_SCHEMA,
                prompt_snippet="Fetch a child subagent result by task id without blocking indefinitely.",
                execute=self._execute_get_subagent_result_tool,
                effects=frozenset({"read"}),
                policy_context=fixed_action_context("result"),
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
                effects=frozenset({"read"}),
                policy_context=fixed_action_context("expand"),
            ),
            ToolDefinition(
                name="cancel_subagent",
                label="cancel_subagent",
                description="Cancel a delegated subagent task by task id.",
                parameters=_CANCEL_SUBAGENT_SCHEMA,
                prompt_snippet="Cancel a child subagent that is no longer needed.",
                execute=self._execute_cancel_subagent_tool,
                effects=frozenset({"execute"}),
                policy_context=fixed_action_context("cancel"),
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
        task_id, task = self._invoke_spawn_subagent_task(normalized_role, goal, options)
        self._model_subagents_spawned_this_turn += 1
        self._model_subagent_spawn_signatures_this_turn.add(spawn_signature)
        if wait_for_result:
            result = self.subagents.wait(
                task_id,
                timeout=task.timeout_seconds + 1,
                signal=signal,
                cancel_reason="Cancelled by parent abort.",
            )
            result = self._prepare_public_subagent_result(result)
            return self._subagent_tool_result(self._format_subagent_result(result), _public_subagent_result_details(result))
        details: dict[str, object] = {
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

    def _execute_wait_subagent_tool(self, _tool_call_id, args, signal=None, on_update=None, ctx=None) -> AgentToolResult:
        task_id = _task_id_arg(args)
        timeout = _optional_timeout_arg(args)
        result = self.subagents.wait(
            task_id,
            timeout=timeout,
            signal=signal,
            cancel_reason="Cancelled by parent abort.",
        )
        result = self._prepare_public_subagent_result(result)
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
        result = self._prepare_public_subagent_result(result)
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
        result = self._prepare_public_subagent_result(result)
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
            existing = self._prepare_public_subagent_result(existing)
            details: dict[str, object] = {
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
        result = self._prepare_public_subagent_result(result)
        return self._subagent_tool_result(self._format_subagent_result(result), _public_subagent_result_details(result))

    def _prepare_public_subagent_result(self, result: SubagentResult) -> SubagentResult:
        cached = self._public_subagent_results.get(result.task_id)
        if cached is not None:
            return cached
        task = self.subagents.get_task(result.task_id)
        typed = task is not None and task.role_definition_name is not None
        policy = task.artifact_policy if typed and task is not None else "declared"
        declared = result.artifacts if policy != "none" else []
        artifact_ids, errors, replacements = self._promote_declared_subagent_artifacts(
            result.task_id,
            declared,
            require_utf8=typed,
        )
        if policy == "declared_and_trace" and result.tool_trace:
            trace_id, trace_error = self._promote_subagent_trace(result)
            if trace_id is not None:
                artifact_ids.append(trace_id)
            if trace_error is not None:
                errors.append(trace_error)
        summary = _replace_artifact_paths(result.summary, replacements)
        final_response = _replace_artifact_paths(result.final_response, replacements)
        prepared = replace(
            result,
            summary=summary,
            final_response=final_response,
            artifacts=artifact_ids,
            errors=[*result.errors, *errors],
        )
        self._public_subagent_results[result.task_id] = prepared
        return prepared

    def _promote_declared_subagent_artifacts(
        self,
        task_id: str,
        declared: list[str],
        *,
        require_utf8: bool = False,
    ) -> tuple[list[str], list[str], dict[str, str]]:
        raw_paths = tuple(declared)
        cached = self._subagent_artifact_promotions.get(task_id)
        if cached is not None and cached[0] == raw_paths:
            return list(cached[1]), list(cached[2]), dict(cached[3])

        artifact_ids: list[str] = []
        errors: list[str] = []
        replacements: dict[str, str] = {}
        for raw_path in raw_paths:
            if self._artifacts.is_readable_reference(raw_path):
                artifact_ids.append(raw_path)
                continue
            lexical: Path | None = None
            try:
                requested = Path(raw_path).expanduser()
                lexical = requested if requested.is_absolute() else self._workspace.root / requested
                metadata = lexical.lstat()
                resolved = lexical.resolve(strict=True)
                if not resolved.is_relative_to(self._workspace.root):
                    raise ArtifactPromotionError("outside_workspace", "Declared artifact is outside workspace")
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                    raise ArtifactPromotionError("invalid_source", "Declared artifact must be a regular file")
                if require_utf8:
                    try:
                        resolved.read_text(encoding="utf-8")
                    except UnicodeDecodeError as error:
                        raise ArtifactPromotionError(
                            "invalid_utf8", "Declared artifact must be valid UTF-8"
                        ) from error
                ref = self._artifacts.promote(
                    resolved,
                    "subagent-output",
                    retained=True,
                )
                artifact_ids.append(ref.id)
                replacements[raw_path] = ref.id
                replacements[str(resolved)] = ref.id
            except (ArtifactPromotionError, OSError, RuntimeError, ValueError) as error:
                code = error.code if isinstance(error, ArtifactPromotionError) else "invalid_source"
                errors.append(f"artifact_unavailable:{code}")
                replacements[raw_path] = "[artifact unavailable]"
                if lexical is not None:
                    replacements[str(lexical)] = "[artifact unavailable]"
        self._subagent_artifact_promotions[task_id] = (
            raw_paths,
            list(artifact_ids),
            list(errors),
            dict(replacements),
        )
        return artifact_ids, errors, replacements

    def _promote_subagent_trace(
        self, result: SubagentResult
    ) -> tuple[str | None, str | None]:
        try:
            self._subagent_log_dir.mkdir(parents=True, exist_ok=True)
            path = self._subagent_log_dir / f"{result.task_id}.sanitized-trace.json"
            path.write_text(
                json.dumps(
                    _public_subagent_tool_trace(result.tool_trace),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            ref = self._artifacts.promote(path, "subagent-trace", retained=True)
            return ref.id, None
        except (ArtifactPromotionError, OSError, RuntimeError, ValueError) as error:
            code = error.code if isinstance(error, ArtifactPromotionError) else "invalid_trace"
            return None, f"artifact_unavailable:{code}"

    def _subagent_tool_result(self, content: str, details: dict[str, object]) -> AgentToolResult:
        return AgentToolResult(content=[TextContent(text=content)], details=details)

    def _subagent_process_owner(self, task: SubagentTask) -> ProcessOwner | None:
        if self.process_owner is None:
            return None
        return replace(
            self.process_owner,
            app_instance_id=f"{self.process_owner.app_instance_id}:subagent:{task.id}",
            origin="agent",
        )

    def _kill_active_subagent_processes(self, owner: ProcessOwner | None) -> None:
        if owner is None or self.process_service is None:
            return
        try:
            snapshots = self.process_service.list(owner)
        except Exception:
            return
        for snapshot in snapshots:
            if snapshot.state.terminal:
                continue
            try:
                self.process_service.kill(owner, snapshot.session_id)
            except Exception:
                continue

    def _run_internal_subagent(self, task: SubagentTask) -> SubagentResult:
        started = int(time.time() * 1000)
        routed_role = task.model_role or ("reviewer" if task.role == "reviewer" else "worker")
        resolution = self.resolve_model_role(
            routed_role,
            selector_override=task.model,
        )
        if not resolution.available or resolution.scoped_model is None:
            ended = int(time.time() * 1000)
            return SubagentResult(
                task_id=task.id,
                backend=task.backend,
                role=task.role,
                status="failed",
                summary=f"No text-capable model is available for the {routed_role} role.",
                errors=[f"model role unavailable: {routed_role}"],
                started_at_ms=started,
                ended_at_ms=ended,
            )
        binding = resolution.scoped_model
        child_thinking = task.reasoning or binding.thinking_level or self.thinking_level
        tool_trace: list[dict[str, object]] = []
        trace_by_call_id: dict[str, dict[str, object]] = {}
        child_owner = self._subagent_process_owner(task)
        child_broker = self._tool_approval_broker
        contextualize = getattr(child_broker, "for_child", None)
        if callable(contextualize):
            child_broker = contextualize(task.role, task.id)
        child = self._session_factory(
            cwd=task.cwd,
            model=binding.model,
            active_tool_names=list(task.allowed_tools),
            allowed_tool_names=list(task.allowed_tools),
            thinking_level=child_thinking,
            stream_fn=self._stream_fn,
            model_registry=self.model_registry,
            settings_manager=self.settings_manager,
            process_service=self.process_service if child_owner is not None else None,
            process_owner=child_owner,
            tool_approval_broker=child_broker,
            tool_policy_event_sink=self._tool_policy_event_sink,
            tool_policy_redactor=self._tool_policy_engine.redactor,
            operation_runtime=self.operation_runtime,
            operation_role=task.role,
            operation_task_id=task.id,
        )
        self.subagents.attach_control_handle(
            task.id, _InternalSubagentControlHandle(child)
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
            errors = []
            status = "completed"
            ended = int(time.time() * 1000)
            result = SubagentResult(
                task_id=task.id,
                backend=task.backend,
                role=task.role,
                status=status,
                summary=summary or "Internal subagent completed without a final message.",
                final_response=summary,
                files_changed=_subagent_changed_files(task, tool_trace),
                errors=errors,
                tool_trace=tool_trace,
                child_session_id=child.session_id,
                started_at_ms=started,
                ended_at_ms=ended,
            )
            raw_log_path, log_errors = self._safe_write_internal_subagent_result_pack(task, result)
            if raw_log_path or log_errors:
                result = replace(result, raw_log_path=raw_log_path, errors=[*result.errors, *log_errors])
            return result
        finally:
            self.subagents.detach_control_handle(task.id)
            self._kill_active_subagent_processes(child_owner)
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
