"""Integrated Travis234 coding app: AI, agent, tools, compaction, and TUI.

Capstone composition that wires the runtime packages into one end-to-end
application, with no imports of external source packages.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Optional

from travis.ai.model_resolver import ScopedModel
from travis.ai.context_estimate import calculate_prompt_tokens, estimate_context_tokens
from travis.ai.overflow import is_context_overflow, parse_available_output_tokens_from_error
from travis.ai.types import Context, Model, SimpleStreamOptions, TextContent, Tool, UserMessage, now_ms
from travis.ai.types import AssistantMessage
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.message_utils import last_assistant_message as _last_assistant_message
from travis.coding_agent.object_utils import first_defined as _first_setting
from travis.coding_agent.agent_session_runtime import AgentSessionRuntime, CreateAgentSessionRuntimeResult
from travis.coding_agent.compaction_adapter import to_compressor_messages
from travis.coding_agent.branch_summarization import SUMMARIZATION_SYSTEM_PROMPT
from travis.coding_agent.config import get_agent_dir
from travis.coding_agent.settings_manager import SettingsManager
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.processes.completions import ProcessCompletionStore
from travis.coding_agent.processes.local import create_local_process_transport
from travis.coding_agent.processes.service import ProcessSessionService
from travis.coding_agent.processes.types import ProcessLaunchRequest, ProcessOwner
from travis.coding_agent.project_trust import ProjectTrustContext
from travis.coding_agent.resource_loader import DefaultResourceLoader
from travis.coding_agent.tools.bash import get_shell_env
from travis.coding_agent.session_catalog import SessionCatalog
from travis.coding_agent.session_store import SessionStore
from travis.compaction.compressor import ContextCompressor, estimate_tokens
from travis.compaction.policy import CompactionPolicyInput
from travis.compaction.timing import CompactionManager
from travis.tui.interactive import InteractiveRenderer
from travis.tui.terminal import ProcessTerminal, Terminal
from travis.tui.tui import TUI

DEFAULT_CONTEXT_LENGTH = 32000


def _resolve_session_retry_settings(settings_manager: object) -> tuple[bool, int, int]:
    retry_settings = _call_setting(settings_manager, "getRetrySettings", "get_retry_settings")
    if not isinstance(retry_settings, Mapping):
        retry_settings = {}
    enabled = _first_setting(
        _call_setting(settings_manager, "getRetryEnabled", "get_retry_enabled"),
        retry_settings.get("enabled"),
        True,
    )
    max_retries = _coerce_nonnegative_int(
        _first_setting(retry_settings.get("maxRetries"), retry_settings.get("max_retries"), 0),
        default=0,
    )
    retry_delay_ms = _coerce_nonnegative_int(
        _first_setting(retry_settings.get("baseDelayMs"), retry_settings.get("base_delay_ms"), 0),
        default=0,
    )
    return bool(enabled), max_retries, retry_delay_ms


def _call_setting(settings_manager: object, *names: str) -> Any:
    for name in names:
        candidate = getattr(settings_manager, name, None)
        if callable(candidate):
            return candidate()
    return None


def _coerce_nonnegative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, parsed)


class CodingApp:
    """End-to-end app: AgentSession + travis compaction (preflight) + tui rendering."""

    def __init__(
        self,
        *,
        cwd: str,
        model: Model,
        terminal: Optional[Terminal] = None,
        context_length: int | None = None,
        summarizer=None,
        compression_model: Model | None = None,
        compression_api_key: str | None = None,
        compression_timeout_seconds: float | None = None,
        compression_generation_params: object | None = None,
        thinking_level: str = "off",
        scoped_models: list[ScopedModel] | None = None,
        enable_tui: bool = True,
        settings_manager: object | None = None,
        project_trust_override: bool | None = None,
        project_trust_context: ProjectTrustContext | None = None,
        session_path: str | None = None,
        session_id: str | None = None,
        agent_dir: str | None = None,
        model_registry: ModelRegistry | None = None,
        allowed_tool_names: list[str] | None = None,
        excluded_tool_names: list[str] | None = None,
        additional_extension_paths: list[str] | None = None,
        additional_skill_paths: list[str] | None = None,
        additional_prompt_template_paths: list[str] | None = None,
        additional_theme_paths: list[str] | None = None,
        offline: bool = False,
        event_trace=None,
        conversation_log=None,
    ) -> None:
        self.cwd = str(Path(cwd).expanduser().resolve())
        self.event_trace = event_trace
        self.conversation_log = conversation_log
        self._agent_dir = str(Path(agent_dir or get_agent_dir()).expanduser().resolve())
        self.model_registry = model_registry or ModelRegistry.create(
            AuthStorage.create(Path(self._agent_dir) / "auth.json"),
            Path(self._agent_dir) / "models.json",
        )
        self.model_registry.set_offline(offline)
        self.model_registry.ensure_model(model)
        if compression_model is not None:
            self.model_registry.ensure_model(compression_model)
        self._settings_manager = settings_manager or SettingsManager.in_memory()
        self._project_trust_override = project_trust_override
        self._project_trust_context = project_trust_context or ProjectTrustContext(False, None)
        self._allowed_tool_names = (
            list(allowed_tool_names) if allowed_tool_names is not None else None
        )
        self._excluded_tool_names = list(excluded_tool_names or [])
        self._additional_extension_paths = list(additional_extension_paths or [])
        self._additional_skill_paths = list(additional_skill_paths or [])
        self._additional_prompt_template_paths = list(additional_prompt_template_paths or [])
        self._additional_theme_paths = list(additional_theme_paths or [])
        self._offline = bool(offline)
        if thinking_level != "off":
            set_default_thinking_level = getattr(
                self._settings_manager,
                "set_default_thinking_level",
                None,
            ) or getattr(self._settings_manager, "setDefaultThinkingLevel", None)
            if callable(set_default_thinking_level):
                set_default_thinking_level(thinking_level)
        self._retry_settings = _resolve_session_retry_settings(self._settings_manager)
        self._scoped_models = list(scoped_models or [])
        self._app_instance_id = uuid.uuid4().hex
        self.process_completion_store = ProcessCompletionStore(
            Path(self._agent_dir) / "process-results"
        )
        self.process_service = ProcessSessionService(
            completion_store=self.process_completion_store,
            max_active_per_owner=4,
            max_active_total=16,
        )
        self._closed = False
        configured_session_dir = _call_setting(self._settings_manager, "getSessionDir", "get_session_dir")
        self.session_catalog = SessionCatalog(
            self._agent_dir,
            session_dir=str(configured_session_dir) if configured_session_dir else None,
        )
        self._context_length = context_length
        self._enable_tui = enable_tui
        self._session_unsubscribers: list[Callable[[], None]] = []
        self._session_rebound_listeners: list[Callable[[AgentSession], None]] = []
        self.terminal = terminal or ProcessTerminal()
        self.tui = TUI(self.terminal, render_interval=0.016)
        if summarizer is None:
            summarizer = _model_summarizer(
                lambda: self.session.model,
                thinking_level=lambda: self.session.thinking_level,
                complete_fn=lambda active_model, context, options: self.model_registry.stream_simple(
                    active_model,
                    context,
                    options,
                ).result_sync(),
            )
        self._summarizer = summarizer
        self._compression_model = compression_model
        self._compression_summarizer = (
            _model_summarizer(
                compression_model,
                thinking_level="off",
                api_key=compression_api_key,
                timeout_seconds=compression_timeout_seconds,
                generation_params=compression_generation_params,
                complete_fn=lambda active_model, context, options: self.model_registry.stream_simple(
                    active_model,
                    context,
                    options,
                ).result_sync(),
            )
            if compression_model is not None
            else None
        )
        initial_session = self._create_session(
            cwd=self.cwd,
            fallback_model=model,
            thinking_level=thinking_level,
            session_path=session_path,
            session_id=session_id,
        )
        self._bind_session(initial_session)
        services = {
            "cwd": self.cwd,
            "agentDir": self._agent_dir,
            "sessionCatalog": self.session_catalog,
        }
        self.session_runtime = AgentSessionRuntime(
            self.session,
            services,
            self._create_runtime_session,
        )
        self.session_runtime.set_before_session_invalidate(self._unbind_session)
        self.session_runtime.set_rebind_session(self._handle_session_rebound)

    def _create_session(
        self,
        *,
        cwd: str,
        fallback_model: Model,
        thinking_level: str,
        session_path: str | None,
        session_id: str | None = None,
        parent_session_path: str | None = None,
        session_start_event: dict[str, object] | None = None,
        defer_session_start: bool = False,
    ) -> AgentSession:
        resolved_cwd = str(Path(cwd).expanduser().resolve())
        model = self._restored_session_model(session_path, resolved_cwd, fallback_model)
        self.model_registry.ensure_model(model)
        retry_enabled, max_retries, retry_delay_ms = self._retry_settings
        fresh_session = bool(
            session_path
            and (not Path(session_path).exists() or Path(session_path).stat().st_size == 0)
        )
        resource_loader = DefaultResourceLoader(
            cwd=resolved_cwd,
            agent_dir=self._agent_dir,
            settings_manager=self._settings_manager,
            project_trusted=self._project_trust_override,
            additional_extension_paths=self._additional_extension_paths,
            additional_skill_paths=self._additional_skill_paths,
            additional_prompt_template_paths=self._additional_prompt_template_paths,
            additional_theme_paths=self._additional_theme_paths,
            offline=self._offline,
        )
        resource_loader.reload({"projectTrustContext": self._project_trust_context})
        session = AgentSession(
            cwd=resolved_cwd,
            model=model,
            transform_context=self._transform_context,
            thinking_level=thinking_level,
            scoped_models=self._scoped_models,
            active_tool_names=(
                list(self._allowed_tool_names)
                if self._allowed_tool_names is not None
                else None
            ),
            allowed_tool_names=self._allowed_tool_names,
            excluded_tool_names=self._excluded_tool_names,
            settings_manager=self._settings_manager,
            resource_loader=resource_loader,
            retry_enabled=retry_enabled,
            max_retries=max_retries,
            retry_delay_ms=retry_delay_ms,
            session_path=session_path,
            parent_session_path=parent_session_path,
            session_id=session_id,
            session_start_event=session_start_event,
            defer_session_start=defer_session_start,
            defer_agent_settled=True,
            agent_dir=self._agent_dir,
            session_index=self.session_catalog.index,
            model_registry=self.model_registry,
            process_service=self.process_service,
            process_owner=self._process_owner_for(resolved_cwd),
            model_change_listener=self._handle_session_model_changed,
        )
        if fresh_session and session._session_store is not None:
            session._session_store.append_model_change(session.model.provider, session.model.id)
            session._session_store.append_thinking_level_change(session.thinking_level)
        return session

    def _restored_session_model(self, session_path: str | None, cwd: str, fallback: Model) -> Model:
        if not session_path:
            return fallback
        path = Path(session_path).expanduser()
        if not path.exists() or path.stat().st_size == 0:
            return fallback
        snapshot = SessionStore(str(path), cwd=cwd).build_context()
        if not snapshot.model:
            return fallback
        restored = self.model_registry.find(
            snapshot.model.get("provider", ""),
            snapshot.model.get("modelId", ""),
        )
        return restored or fallback

    def _configure_session_components(self) -> None:
        compaction_policy = _resolve_compaction_policy(
            self.session.model,
            explicit_context_length=self._context_length,
            summarizer_model=self._compression_model,
        )
        self.compressor = ContextCompressor(
            context_length=compaction_policy.context_window,
            threshold_percent=compaction_policy.threshold_ratio,
            max_tokens=compaction_policy.max_output_tokens,
            summarizer_context_window=compaction_policy.summarizer_context_window,
            summarizer_max_tokens=compaction_policy.summarizer_max_output_tokens,
            summarizer=self._summarizer,
            summary_summarizer=self._compression_summarizer,
            model=_model_route(self.session.model),
            summary_model_override=(
                _model_route(self._compression_model)
                if self._compression_model is not None
                else None
            ),
        )
        self.compaction = CompactionManager(
            self.compressor,
            summarizer=self._summarizer,
            deep_baseline_tokens=_estimate_static_prompt_tool_tokens(self.session),
        )
        self.session.set_compaction_manager(self.compaction)
        tool_definitions = {
            name: definition
            for name in self.session.get_active_tool_names()
            if (definition := self.session.get_tool_definition(name)) is not None
        }
        self.renderer = InteractiveRenderer(self.tui, tool_definitions=tool_definitions, cwd=self.cwd)
        if self._enable_tui:
            self._session_unsubscribers.append(self.session.subscribe(self.renderer.handle_event))
        if self.event_trace is not None:
            self._session_unsubscribers.append(self.session.subscribe(self._trace_session_event))

    def _bind_session(self, session: AgentSession) -> None:
        self.session = session
        self.cwd = str(Path(session.cwd).expanduser().resolve())
        self._configure_session_components()

    def _handle_session_model_changed(self, _previous_model: Model, model: Model) -> None:
        compaction_policy = _resolve_compaction_policy(
            model,
            explicit_context_length=self._context_length,
            summarizer_model=self._compression_model,
        )
        self.compressor.update_context_window(
            compaction_policy.context_window,
            threshold_percent=compaction_policy.threshold_ratio,
            max_tokens=compaction_policy.max_output_tokens,
            model=_model_route(model),
            summarizer_context_window=compaction_policy.summarizer_context_window,
            summarizer_max_tokens=compaction_policy.summarizer_max_output_tokens,
        )
        self.compaction.deep_baseline_tokens = _estimate_static_prompt_tool_tokens(self.session)

    def _unbind_session(self) -> None:
        for unsubscribe in self._session_unsubscribers:
            unsubscribe()
        self._session_unsubscribers.clear()

    def _create_runtime_session(self, options: dict[str, object]) -> CreateAgentSessionRuntimeResult:
        current = self.session
        next_cwd = str(options.get("cwd") or self.cwd)
        session = self._create_session(
            cwd=next_cwd,
            fallback_model=current.model,
            thinking_level=current.thinking_level,
            session_path=str(options["session_path"]) if options.get("session_path") else None,
            parent_session_path=(
                str(options["parent_session_path"]) if options.get("parent_session_path") else None
            ),
            session_start_event=(
                dict(options["session_start_event"])
                if isinstance(options.get("session_start_event"), Mapping)
                else None
            ),
            defer_session_start=bool(options.get("defer_session_start", False)),
        )
        return CreateAgentSessionRuntimeResult(
            session=session,
            services={
                "cwd": session.cwd,
                "agentDir": self._agent_dir,
                "sessionCatalog": self.session_catalog,
            },
        )

    def _handle_session_rebound(self, session: AgentSession) -> None:
        self._bind_session(session)
        for listener in list(self._session_rebound_listeners):
            listener(session)

    def subscribe_session_rebound(self, listener: Callable[[AgentSession], None]) -> Callable[[], None]:
        self._session_rebound_listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._session_rebound_listeners:
                self._session_rebound_listeners.remove(listener)

        return unsubscribe

    def switch_session(self, path: str, *, cwd_override: str | None = None) -> dict[str, bool]:
        options = {"cwdOverride": cwd_override} if cwd_override else None
        return self.session_runtime.switch_session(path, options)

    def new_session(self) -> dict[str, bool]:
        return self.session_runtime.new_session()

    def rename_session(self, name: str | None) -> None:
        self.session.rename_session(name)

    def fork_session(self, entry_id: str, *, position: str = "before") -> dict[str, object]:
        return self.session_runtime.fork(entry_id, {"position": position})

    def clone_session(self) -> dict[str, object]:
        return self.session_runtime.clone()

    def session_tree(self) -> list[dict]:
        return self.session.session_tree()

    def navigate_session_tree(self, target_id: str, options: dict | None = None) -> dict:
        return self.session.navigate_tree(target_id, options)

    def import_session(self, input_path: str, *, cwd_override: str | None = None) -> dict[str, bool]:
        return self.session_runtime.import_from_jsonl(input_path, cwd_override)

    def export_session_jsonl(self, output_path: str | None = None) -> str:
        return self.session.export_to_jsonl(output_path)

    def process_owner(self, origin: Literal["agent", "user"] = "agent") -> ProcessOwner:
        return self._process_owner_for(self.cwd, origin=origin)

    def user_command_request(
        self,
        command: str,
        *,
        session: AgentSession,
        command_prefix: str | None = None,
        shell_path: str | None = None,
    ) -> ProcessLaunchRequest:
        prefix = (
            command_prefix
            if command_prefix is not None
            else session._settings_shell_command_prefix()
        )
        resolved_command = f"{prefix}\n{command}" if prefix else command
        shell = shell_path or session._settings_shell_path() or os.environ.get("SHELL") or "/bin/bash"
        return ProcessLaunchRequest(
            command=resolved_command,
            cwd=session.cwd,
            env=get_shell_env(sanitize_credentials=False),
            shell_path=shell,
            launch_session_id=session.session_id or None,
        )

    @staticmethod
    def user_command_transport(request: ProcessLaunchRequest):
        from travis.coding_agent.execution_backend import select_execution_backend

        return create_local_process_transport(request, select_execution_backend(request.cwd))

    def _process_owner_for(
        self,
        cwd: str,
        *,
        origin: Literal["agent", "user"] = "agent",
    ) -> ProcessOwner:
        return ProcessOwner(
            app_instance_id=self._app_instance_id,
            workspace_key=str(Path(cwd).expanduser().resolve()),
            origin=origin,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: BaseException | None = None
        try:
            self._unbind_session()
        except BaseException as error:  # noqa: BLE001 - complete all lifecycle cleanup before re-raising.
            first_error = error
        try:
            self.process_service.close()
        except BaseException as error:  # noqa: BLE001
            if first_error is None:
                first_error = error
        try:
            self.session_runtime.dispose()
        except BaseException as error:  # noqa: BLE001
            if first_error is None:
                first_error = error
        try:
            self.process_completion_store.close()
        except BaseException as error:  # noqa: BLE001
            if first_error is None:
                first_error = error
        try:
            self.session_catalog.close()
        except BaseException as error:  # noqa: BLE001
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error

    def _transform_context(self, messages, signal=None):
        return self.session.compaction_transactions.preflight(messages).messages

    def run_turn(
        self,
        prompt: str,
        stream_fn=None,
        on_post_response_compaction_start: Callable[[], object] | None = None,
        image_paths: list[str] | tuple[str, ...] | None = None,
    ):
        turn_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        before_message_count = len(self.session.messages)
        self._trace(
            "turn_start",
            {"turn_id": turn_id, "provider": self.session.model.provider, "model": self.session.model.id},
        )
        status = "ok"
        completed_turn_messages = None
        try:
            if self._recover_output_cap(stream_fn=stream_fn):
                return []
            if self._recover_context_overflow(stream_fn=stream_fn):
                return []
            self._compact_failed_turn_context()
            before_prompt_compressions = self.compaction.compressor.compression_count
            new_messages = self.session.prompt(
                prompt,
                stream_fn=stream_fn,
                image_paths=image_paths,
            )
            if self._recover_output_cap(stream_fn=stream_fn):
                return new_messages
            if self._recover_context_overflow(stream_fn=stream_fn):
                return new_messages
            just_compacted = self.compaction.compressor.compression_count > before_prompt_compressions
            if self._compact_failed_turn_context(skip_if_just_compacted=just_compacted):
                return new_messages
            # Post-response compaction can replace the session message list with a
            # shorter summary. Preserve the completed turn before that boundary;
            # slicing the compacted list with the pre-turn length drops the reply.
            completed_turn_messages = list(self.session.messages[before_message_count:])
            if not completed_turn_messages:
                completed_turn_messages = list(new_messages)
            if on_post_response_compaction_start and self._will_compact_post_response():
                on_post_response_compaction_start()
            self._compact_post_response()
            return new_messages
        except Exception as error:
            status = "error"
            self._trace(
                "fatal",
                {"turn_id": turn_id, "status": "error", "error_code": type(error).__name__},
            )
            raise
        finally:
            self.session.emit_agent_settled()
            turn_messages = (
                completed_turn_messages
                if completed_turn_messages is not None
                else self.session.messages[before_message_count:]
            )
            terminal_message = _last_assistant_message(turn_messages)
            if status == "ok" and terminal_message is not None:
                if terminal_message.stop_reason == "error":
                    status = "error"
                elif terminal_message.stop_reason == "aborted":
                    status = "aborted"
            if self.conversation_log is not None:
                self.conversation_log.write(
                    turn_id=turn_id,
                    prompt=prompt,
                    response=_assistant_log_text(turn_messages),
                    status=status,
                )
            self._trace(
                "turn_end",
                {
                    "turn_id": turn_id,
                    "status": status,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )

    def _trace(self, event_type: str, fields: Mapping[str, object] | None = None) -> None:
        if self.event_trace is not None:
            self.event_trace.write(event_type, fields)

    def _trace_session_event(self, event) -> None:
        event_type = getattr(event, "type", None)
        if event_type == "tool_execution_end":
            tool_name = str(getattr(event, "tool_name", ""))
            args = getattr(event, "args", None)
            metadata = _safe_eval_tool_metadata(tool_name, args)
            reason_code = getattr(event, "reason_code", None)
            if isinstance(reason_code, str) and reason_code:
                metadata["reason_code"] = reason_code
            self._trace(
                "tool_end",
                {
                    "tool_call_id": str(getattr(event, "tool_call_id", "")),
                    "tool": tool_name,
                    "status": "error" if getattr(event, "is_error", False) else "ok",
                    **metadata,
                },
            )
        elif event_type == "compaction_end":
            result = self.compaction.last_compression_result
            if getattr(event, "error_message", None):
                status = "error"
            elif getattr(event, "aborted", False):
                status = "aborted"
            else:
                status = "ok"
            self._trace(
                "compaction_end",
                {
                    "status": status,
                    "compression_count": self.compaction.compressor.compression_count,
                    "trigger": str(getattr(event, "reason", "")),
                    "summary_model_requested": getattr(result, "summary_model_requested", None),
                    "summary_model_used": getattr(result, "summary_model_used", None),
                    "summary_model_fallback": bool(getattr(result, "summary_model_fallback", False)),
                },
            )

    @property
    def messages(self):
        return self.session.messages

    def _compact_post_response(self) -> None:
        message = _last_assistant_message(self.session.messages)
        if message is None or message.stop_reason in {"error", "aborted"}:
            return
        prompt_tokens = _assistant_prompt_tokens(message)
        self.session.compaction_transactions.post_response(self.session.messages, prompt_tokens)

    def _will_compact_post_response(self) -> bool:
        message = _last_assistant_message(self.session.messages)
        if message is None or message.stop_reason in {"error", "aborted"}:
            return False
        prompt_tokens = _assistant_prompt_tokens(message)
        real_tokens = 0 if prompt_tokens == -1 else prompt_tokens
        return self.compaction.compressor.should_compress(real_tokens)

    def _recover_context_overflow(self, *, stream_fn=None) -> bool:
        message = _last_assistant_message(self.session.messages)
        if message is None or message.stop_reason != "error":
            return False
        if not is_context_overflow(message.error_message or ""):
            return False

        # Travis removes the overflow assistant error from model context before compact-and-retry.
        retained = [
            item
            for item in self.session.messages
            if item is not message
        ]
        outcome = self.session.compaction_transactions.recover_overflow(retained, stream_fn=stream_fn)
        if outcome.recovered:
            self._compact_post_response()
        return True

    def _compact_failed_turn_context(self, *, skip_if_just_compacted: bool = False) -> bool:
        message = _last_assistant_message(self.session.messages)
        if message is None or message.stop_reason not in {"error", "aborted"}:
            return False
        # A provider error is not proof that the post-compaction request fit.
        # Clear Travis' "await real usage" gate so the next prompt can compact
        # instead of resending a failed large turn unchanged.
        if message.stop_reason == "error":
            self.compaction.awaiting_real_usage_after_compression = False
        if skip_if_just_compacted:
            return False

        if not self.compaction.compressor.should_compress(
            estimate_tokens(to_compressor_messages(self.session.messages))
        ):
            return False

        source_messages = list(self.session.messages)
        outcome = self.session.compaction_transactions.compact_error_context(
            source_messages,
            retain_source_suffix=False,
        )
        return outcome.compressed

    def _recover_output_cap(self, *, stream_fn=None) -> bool:
        message = _last_assistant_message(self.session.messages)
        if message is None or message.stop_reason != "error":
            return False
        available_tokens = parse_available_output_tokens_from_error(message.error_message or "")
        if available_tokens is None:
            return False

        model = self.session.agent.state.model
        current_max_tokens = int(model.max_tokens or 0)
        if current_max_tokens > 0 and available_tokens >= current_max_tokens:
            return False

        self.session.with_model_overrides(max_tokens=max(1, available_tokens))
        retained = [item for item in self.session.messages if item is not message]
        self.session.agent.state.messages = retained
        self.compaction.reset_overflow_attempts()
        if not retained or getattr(retained[-1], "role", None) == "assistant":
            return True

        self.session.agent.continue_(stream_fn=stream_fn)
        self._compact_post_response()
        return True


def _model_summarizer(
    model: Model | Callable[[], Model],
    *,
    thinking_level: str | Callable[[], str] = "off",
    api_key: str | None = None,
    timeout_seconds: float | None = None,
    generation_params: object | None = None,
    complete_fn=None,
):
    if complete_fn is None:
        raise ValueError("summarization requires an injected model runtime")
    def summarize(prompt: str) -> str:
        active_model = model() if callable(model) else model
        active_thinking_level = thinking_level() if callable(thinking_level) else thinking_level
        options = SimpleStreamOptions(
            omit_max_tokens=True,
            api_key=api_key,
            timeout_ms=(
                max(0, int(float(timeout_seconds) * 1000))
                if timeout_seconds is not None
                else None
            ),
            generation_params=generation_params,
            reasoning=(
                active_thinking_level
                if active_model.reasoning and active_thinking_level != "off"
                else None
            ),
        )
        response = complete_fn(
            active_model,
            Context(
                system_prompt=SUMMARIZATION_SYSTEM_PROMPT,
                messages=[UserMessage(content=[TextContent(text=prompt)], timestamp=now_ms())],
            ),
            options,
        )
        if response.stop_reason == "error":
            raise RuntimeError(response.error_message or "Summarization failed")
        return "\n".join(block.text for block in response.content if isinstance(block, TextContent))

    return summarize


def _model_route(model: Model) -> str:
    return f"{model.provider}/{model.id}"


def _assistant_text_after(messages, start_index: int) -> str | None:
    message = _last_assistant_message(list(messages)[start_index:])
    if message is None:
        return None
    text = "".join(block.text for block in message.content if isinstance(block, TextContent)).strip()
    return text or None


def _assistant_log_text(messages) -> str | None:
    message = _last_assistant_message(list(messages))
    if message is None:
        return None
    text = "".join(block.text for block in message.content if isinstance(block, TextContent)).strip()
    parts = [text] if text else []
    if message.stop_reason == "error":
        parts.append(f"Error: {message.error_message or 'Unknown error'}")
    elif message.stop_reason == "aborted":
        parts.append(message.error_message or "Operation aborted")
    return "\n\n".join(parts) or None


def _assistant_prompt_tokens(message: AssistantMessage) -> int:
    return calculate_prompt_tokens(message.usage)


def _safe_eval_tool_metadata(tool_name: str, args: object) -> dict[str, str]:
    """Return allowlisted semantic tags without recording tool arguments."""

    if not isinstance(args, Mapping):
        return {}
    if tool_name == "process":
        action = args.get("action")
        if isinstance(action, str) and action in {
            "start", "poll", "write", "interrupt", "terminate", "kill", "list", "tail",
        }:
            return {"action": action}
        return {}
    if tool_name == "bash":
        command = args.get("command")
        if not isinstance(command, str):
            return {}
        lowered = command.lower()
        if any(marker in lowered for marker in ("rg ", "grep ", "find ", "fd ")):
            return {"operation": "search"}
        if any(marker in lowered for marker in ("pytest", "node --test", "npm test")):
            return {"operation": "test"}
        if any(marker in lowered for marker in ("python -m build", "npm pack")):
            return {"operation": "package_build"}
    return {}


def _resolve_compaction_policy(
    model: Model,
    *,
    explicit_context_length: int | None,
    summarizer_model: Model | None,
) -> CompactionPolicyInput:
    context_length = (
        explicit_context_length
        if explicit_context_length is not None
        else int(model.context_window or 0) or DEFAULT_CONTEXT_LENGTH
    )
    return CompactionPolicyInput(
        context_window=context_length,
        max_output_tokens=int(model.max_tokens or 0),
        model_id=_model_route(model),
        summarizer_context_window=(
            int(summarizer_model.context_window or 0) or None
            if summarizer_model is not None
            else None
        ),
        summarizer_max_output_tokens=(
            int(summarizer_model.max_tokens or 0)
            if summarizer_model is not None
            else 0
        ),
    )


def _estimate_static_prompt_tool_tokens(session: AgentSession) -> int:
    tools = [
        Tool(name=tool.name, description=tool.description, parameters=tool.parameters)
        for tool in session.agent.state.tools
    ]
    return estimate_context_tokens(
        Context(system_prompt=session.system_prompt or "", messages=[], tools=tools)
    ).tokens
