"""Narrow structural ports used by coding-session collaborators."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Protocol

from travis.coding_agent.session_state import SessionPresentationState, SessionTurnState
from travis.controller_ports import (
    ControllerDependencies,
    ExplicitController,
)
from travis.controller_ports import (
    install_controller_delegates as install_session_controller_delegates,
)


class SessionControllerPort(Protocol):
    def read(self, name: str) -> object: ...

    def write(self, name: str, value: object) -> None: ...

class SessionPortBoundController[SessionControllerPortT](
    ExplicitController[ControllerDependencies[SessionControllerPortT]]
):
    """Own a responsibility-specific dependency record."""

    __slots__ = (
        "_allowed_tool_names", "_append_system_prompt", "_artifacts", "_base_definition_by_name",
        "_base_source_info_by_name", "_base_tool_by_name", "_bash_signals", "_bash_signals_lock",
        "_build_system_prompt", "_caller_transform_context", "_command_signal",
        "_compaction_adapter", "_compaction_coordinator", "_compaction_manager",
        "_compaction_transactions", "_context_files", "_convert_to_llm",
        "_coordination_runtime_guard_active", "_custom_prompt", "_defer_agent_settled",
        "_defer_session_start", "_disable_operation_journal", "_emit", "_emit_queue_update",
        "_emit_tool_policy_decision", "_event_listeners", "_excluded_tool_names",
        "_extend_resources_from_extensions", "_extension_abort_handler",
        "_extension_command_context_actions", "_extension_error_listener",
        "_extension_error_unsubscribe", "_extension_has_ui", "_extension_mode",
        "_extension_provider_original_models", "_extension_provider_registrations",
        "_extension_runner", "_extension_shutdown_handler", "_extension_ui_context",
        "_extensions_bound", "_flush_pending_bash_messages", "_format_subagent_result",
        "_generation_param_overrides", "_is_retryable_error", "_max_retries",
        "_language_services",
        "_memory_settings", "_memory_tool_runtime", "_model_change_listener",
        "_messages_to_summary", "_model_subagent_spawn_signatures_this_turn",
        "_model_subagents_spawned_this_turn", "_operation_assistant_sequence",
        "_operation_continue", "_operation_finish_turn", "_operation_invoke_provider",
        "_operation_record_persisted_message", "_operation_record_tools_settled",
        "_operation_role", "_operation_start_message_count", "_operation_start_turn",
        "_operation_task_id", "_operation_tool_effects", "_operation_tool_effects_lock",
        "_operation_turn_active", "_operation_turn_sequence", "_operation_usage_keys",
        "_parse_extension_command", "_partial_stream_continue_retries",
        "_pending_bash_messages", "_pending_next_turn_messages",
        "_prepare_public_subagent_result", "_promote_declared_subagent_artifacts",
        "_public_subagent_results", "_raise_if_extension_command",
        "_reconcile_subagent_tool_results_from_messages", "_refresh_resource_prompt_inputs",
        "_resource_loader", "_restore_generation_param_overrides",
        "_restore_unacknowledged_turn_messages", "_retry_attempt", "_retry_delay_ms",
        "_retry_enabled", "_retry_signal", "_retryable_error_predicate", "_scoped_models",
        "_session_factory", "_session_name", "_session_start_event", "_session_store",
        "_settings_shell_command_prefix", "_settings_shell_path", "_spawn_and_wait_for_subagent",
        "_stream_fn", "_subagent_after_tool_call_tracer", "_subagent_artifact_promotions",
        "_subagent_log_dir", "_subagent_observer_errors", "_subagent_tool_trace_listener",
        "_tool_approval_broker", "_tool_by_name", "_tool_definition_by_name",
        "_tool_policy_engine", "_tool_policy_event_sink", "_tool_source_info_by_name",
        "_try_execute_extension_command", "_turn_index", "_turn_mailbox", "_unsubscribe_agent",
        "_workspace", "agent", "append_custom_entry", "compact", "cwd", "execution_backend",
        "get_active_tool_names", "get_all_tools", "get_context_usage", "get_follow_up_messages",
        "get_session_leaf_id", "get_steering_messages", "get_tool_definition", "is_streaming",
        "messages", "model", "model_registry", "model_role_router", "operation_coordinator",
        "operation_runtime", "pending_message_count", "process_owner", "process_service", "prompt",
        "prompt_templates", "refresh_tools", "resolve_model_role", "send_custom_message",
        "session_id", "session_name", "set_active_tools_by_name", "set_model", "set_session_name",
        "set_thinking_level", "settings_manager", "subagents", "system_prompt", "thinking_level",
    )
    _bash_signals_lock: AbstractContextManager[object]
    _operation_tool_effects_lock: AbstractContextManager[object]


@dataclass(frozen=True, slots=True)
class SessionModelDependencies(ControllerDependencies[SessionControllerPort]):
    presentation_state: SessionPresentationState


@dataclass(frozen=True, slots=True)
class SessionTurnDependencies(ControllerDependencies[SessionControllerPort]):
    turn_state: SessionTurnState


class SessionEventPort(Protocol):
    def subscribe(self, listener: Callable[[object], None]) -> Callable[[], None]: ...

    def emit(self, event: object) -> None: ...


class SessionModelSettingsPort(Protocol):
    @property
    def model(self) -> object: ...

    @property
    def thinking_level(self) -> str: ...

    def set_model(self, model: object) -> None: ...


class SessionPersistencePort(Protocol):
    @property
    def session_path(self) -> str | None: ...

    def append_custom_entry(self, custom_type: str, data: object = None) -> str: ...

    def get_session_entry(self, entry_id: str) -> Mapping[str, object] | None: ...

    def create_branched_session(self, leaf_id: str, path: str | None = None) -> str: ...


class SessionMessageStatePort(Protocol):
    @property
    def messages(self) -> Sequence[object]: ...

    def replace_messages(self, messages: Sequence[object]) -> None: ...


class SessionToolRegistryPort(Protocol):
    def get_active_tool_names(self) -> list[str]: ...

    def get_tool_definition(self, name: str) -> object | None: ...

    def refresh_tools(self) -> None: ...


class SessionPolicyPort(Protocol):
    def evaluate_tool_call(self, name: str, arguments: Mapping[str, object]) -> object: ...


class SessionExtensionPort(Protocol):
    def call(self, event_name: str, payload: object) -> object: ...


class SessionProcessContextPort(Protocol):
    def resolve(self, process_id: str) -> object | None: ...


class SessionSubagentPort(Protocol):
    def snapshot(self) -> object: ...

    def cancel(self, task_id: str, reason: str | None = None) -> object: ...


class SessionTurnMailboxPort(Protocol):
    def enqueue(self, kind: str, text: str, images: Iterable[object] | None = None) -> object: ...

    def drain(self, kind: str, *, mode: str) -> list[object]: ...

    def clear(self, kind: str) -> list[object]: ...


class SessionCancellationPort(Protocol):
    @property
    def aborted(self) -> bool: ...

    def abort(self, reason: str | None = None) -> None: ...


__all__ = [
    "SessionCancellationPort",
    "SessionControllerPort",
    "SessionEventPort",
    "SessionExtensionPort",
    "SessionMessageStatePort",
    "SessionModelDependencies",
    "SessionModelSettingsPort",
    "SessionPersistencePort",
    "SessionPolicyPort",
    "SessionProcessContextPort",
    "SessionSubagentPort",
    "SessionToolRegistryPort",
    "SessionTurnMailboxPort",
    "SessionTurnDependencies",
    "SessionPortBoundController",
    "install_session_controller_delegates",
]
