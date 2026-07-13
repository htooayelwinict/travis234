"""Integrated Travis234 coding app: AI, agent, tools, compaction, and TUI.

Capstone composition that wires the runtime packages into one end-to-end
application, with no imports of external source packages.
"""

from __future__ import annotations

import copy
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Literal, Mapping, Optional

from travis.ai.stream import complete_simple_sync
from travis.ai.model_resolver import ScopedModel
from travis.ai.overflow import is_context_overflow, parse_available_output_tokens_from_error
from travis.ai.types import Context, Model, SimpleStreamOptions, TextContent, ToolResultMessage, UserMessage, now_ms
from travis.ai.types import AssistantMessage
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.message_utils import last_assistant_message as _last_assistant_message
from travis.coding_agent.object_utils import first_defined as _first_setting
from travis.coding_agent.agent_session_runtime import AgentSessionRuntime, CreateAgentSessionRuntimeResult
from travis.coding_agent.compaction_adapter import to_compressor_messages
from travis.coding_agent.branch_summarization import SUMMARIZATION_SYSTEM_PROMPT
from travis.coding_agent.config import get_agent_dir
from travis.coding_agent.settings_manager import SettingsManager
from travis.coding_agent.provider_control_plane import ProviderControlPlane
from travis.coding_agent.processes.completions import ProcessCompletionStore
from travis.coding_agent.processes.local import create_local_process_transport
from travis.coding_agent.processes.service import ProcessSessionService
from travis.coding_agent.processes.types import ProcessLaunchRequest, ProcessOwner
from travis.coding_agent.tools.bash import get_shell_env
from travis.coding_agent.session_catalog import SessionCatalog
from travis.coding_agent.session_store import SessionStore
from travis.compaction.compressor import ContextCompressor, estimate_tokens
from travis.compaction.timing import CompactionManager
from travis.tui.interactive import InteractiveRenderer
from travis.tui.terminal import ProcessTerminal, Terminal
from travis.tui.tui import TUI

DEFAULT_CONTEXT_LENGTH = 32000
DEFAULT_COMPACTION_RESERVE_TOKENS = 16_384
TRAVIS_STATIC_PROMPT_BREATHING_ROOM = 4_096


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
        thinking_level: str = "off",
        scoped_models: list[ScopedModel] | None = None,
        enable_tui: bool = True,
        settings_manager: object | None = None,
        max_iterations: int = 90,
        tool_loop_guardrails: Mapping[str, object] | None = None,
        session_path: str | None = None,
        session_id: str | None = None,
        agent_dir: str | None = None,
        provider_control_plane: ProviderControlPlane | None = None,
        event_trace=None,
        conversation_log=None,
    ) -> None:
        self.cwd = str(Path(cwd).expanduser().resolve())
        self.event_trace = event_trace
        self.conversation_log = conversation_log
        self.provider_control_plane = provider_control_plane or ProviderControlPlane.create_default()
        self.provider_control_plane.ensure_model(model)
        self._settings_manager = settings_manager or SettingsManager.in_memory()
        self._retry_settings = _resolve_session_retry_settings(self._settings_manager)
        self._scoped_models = list(scoped_models or [])
        self._max_iterations = max_iterations
        self._tool_loop_guardrails = tool_loop_guardrails
        self._agent_dir = str(Path(agent_dir or get_agent_dir()).expanduser().resolve())
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
                complete_fn=lambda active_model, context, options: self.provider_control_plane.stream_simple(
                    active_model,
                    context,
                    options,
                ).result_sync(),
            )
        self._summarizer = summarizer
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
        self.provider_control_plane.ensure_model(model)
        retry_enabled, max_retries, retry_delay_ms = self._retry_settings
        fresh_session = bool(
            session_path
            and (not Path(session_path).exists() or Path(session_path).stat().st_size == 0)
        )
        session = AgentSession(
            cwd=resolved_cwd,
            model=model,
            transform_context=self._transform_context,
            thinking_level=thinking_level,
            scoped_models=self._scoped_models,
            settings_manager=self._settings_manager,
            retry_enabled=retry_enabled,
            max_retries=max_retries,
            retry_delay_ms=retry_delay_ms,
            max_iterations=self._max_iterations,
            tool_loop_guardrails=self._tool_loop_guardrails,
            session_path=session_path,
            parent_session_path=parent_session_path,
            session_id=session_id,
            session_start_event=session_start_event,
            defer_session_start=defer_session_start,
            agent_dir=self._agent_dir,
            session_index=self.session_catalog.index,
            provider_control_plane=self.provider_control_plane,
            process_service=self.process_service,
            process_owner=self._process_owner_for(resolved_cwd),
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
        restored = self.provider_control_plane.models.find(
            snapshot.model.get("provider", ""),
            snapshot.model.get("modelId", ""),
        )
        return restored or fallback

    def _configure_session_components(self) -> None:
        resolved_context_length, threshold_percent = _resolve_compaction_window(
            self.session.model,
            self.session,
            explicit_context_length=self._context_length,
        )
        self.compressor = ContextCompressor(
            context_length=resolved_context_length,
            threshold_percent=threshold_percent,
            summarizer=self._summarizer,
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
            env=get_shell_env(),
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
    ):
        turn_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        before_message_count = len(self.session.messages)
        self._trace(
            "turn_start",
            {"turn_id": turn_id, "provider": self.session.model.provider, "model": self.session.model.id},
        )
        status = "ok"
        try:
            if self._recover_output_cap(stream_fn=stream_fn):
                return []
            if self._recover_context_overflow(stream_fn=stream_fn):
                return []
            self._compact_failed_turn_context()
            before_prompt_compressions = self.compaction.compressor.compression_count
            new_messages = self.session.prompt(prompt, stream_fn=stream_fn)
            if self._recover_output_cap(stream_fn=stream_fn):
                return new_messages
            if self._recover_context_overflow(stream_fn=stream_fn):
                return new_messages
            just_compacted = self.compaction.compressor.compression_count > before_prompt_compressions
            if self._compact_failed_turn_context(skip_if_just_compacted=just_compacted):
                return new_messages
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
            if self.conversation_log is not None:
                self.conversation_log.write(
                    turn_id=turn_id,
                    prompt=prompt,
                    response=_assistant_text_after(self.session.messages, before_message_count),
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
            self._trace(
                "compaction_end",
                {
                    "status": "error" if getattr(event, "error_message", None) else "ok",
                    "compression_count": self.compaction.compressor.compression_count,
                    "trigger": str(getattr(event, "reason", "")),
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
        is_prompt_guardrail = message.stop_reason == "error" and _is_prompt_injection_guardrail_error(
            message.error_message or ""
        )

        # A provider error is not proof that the post-compaction request fit.
        # Clear Travis' "await real usage" gate so the next prompt can compact
        # instead of resending a failed large turn unchanged.
        if message.stop_reason == "error":
            self.compaction.awaiting_real_usage_after_compression = False
        if is_prompt_guardrail:
            retained = [
                item
                for item in self.session.messages
                if item is not message
            ]
            retained = _elide_failed_turn_tool_results(retained)
            self.session.compaction_transactions.compact_error_context(
                retained,
                force=True,
                retain_source_suffix=False,
            )
            return True
        if skip_if_just_compacted:
            return False

        if not self.compaction.compressor.should_compress(
            estimate_tokens(to_compressor_messages(self.session.messages))
        ):
            return False

        source_messages = list(self.session.messages)
        outcome = self.session.compaction_transactions.compact_error_context(
            source_messages,
            force=False,
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
    complete_fn=complete_simple_sync,
):
    def summarize(prompt: str) -> str:
        active_model = model() if callable(model) else model
        active_thinking_level = thinking_level() if callable(thinking_level) else thinking_level
        options = SimpleStreamOptions(
            max_tokens=_summarizer_max_tokens(active_model),
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


def _summarizer_max_tokens(model: Model) -> int:
    if model.max_tokens and model.max_tokens > 0:
        return min(model.max_tokens, 12_000)
    return 2048


def _assistant_text_after(messages, start_index: int) -> str | None:
    message = _last_assistant_message(list(messages)[start_index:])
    if message is None:
        return None
    text = "".join(block.text for block in message.content if isinstance(block, TextContent)).strip()
    return text or None


def _assistant_prompt_tokens(message: AssistantMessage) -> int:
    return message.usage.total_tokens or (
        message.usage.input + message.usage.output + message.usage.cache_read + message.usage.cache_write
    )


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


_PROMPT_GUARDRAIL_ERROR_PATTERNS = (
    "prompt-injection guardrail",
    "prompt injection patterns detected",
    "system_prefix_spoofing",
)


def _is_prompt_injection_guardrail_error(error_message: str) -> bool:
    lowered = (error_message or "").lower()
    return any(pattern in lowered for pattern in _PROMPT_GUARDRAIL_ERROR_PATTERNS)


def _elide_failed_turn_tool_results(messages) -> list:
    last_user_index = -1
    for index, message in enumerate(messages):
        if getattr(message, "role", None) == "user":
            last_user_index = index
    if last_user_index < 0:
        return list(messages)

    elided = list(messages)
    for index in range(last_user_index + 1, len(elided)):
        message = elided[index]
        if not isinstance(message, ToolResultMessage):
            continue
        text = "".join(block.text for block in message.content if isinstance(block, TextContent))
        clone = copy.copy(message)
        clone.content = [TextContent(text=_tool_result_guardrail_placeholder(message, text))]
        elided[index] = clone
    return elided


def _tool_result_guardrail_placeholder(message: ToolResultMessage, text: str) -> str:
    line_count = text.count("\n") + 1 if text else 0
    return (
        f"[{message.tool_name}] result elided after provider prompt-injection guardrail "
        f"({len(text)} chars, {line_count} lines). Use narrower reads or summarize code scans before retrying."
    )


def _resolve_compaction_window(
    model: Model,
    session: AgentSession,
    *,
    explicit_context_length: int | None,
) -> tuple[int, float]:
    if explicit_context_length is not None:
        return explicit_context_length, 0.5

    context_length = int(model.context_window or 0) or DEFAULT_CONTEXT_LENGTH
    if context_length <= DEFAULT_CONTEXT_LENGTH:
        return context_length, 0.5

    static_tokens = _estimate_static_prompt_tool_tokens(session)
    reserve_tokens = DEFAULT_COMPACTION_RESERVE_TOKENS + static_tokens + TRAVIS_STATIC_PROMPT_BREATHING_ROOM
    threshold_tokens = context_length - reserve_tokens
    if threshold_tokens <= 0:
        threshold_tokens = max(1, context_length // 2)
    return context_length, threshold_tokens / context_length


def _estimate_static_prompt_tool_tokens(session: AgentSession) -> int:
    system_prompt = session.system_prompt or ""
    tools = []
    for tool in session.agent.state.tools:
        tools.append({
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        })
    payload = system_prompt + json.dumps(tools, sort_keys=True)
    return len(payload) // 4
