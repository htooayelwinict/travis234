"""Static contracts for the stable coding-session facade."""

from __future__ import annotations

from typing import Protocol

from travis.coding_agent.extensions import ExtensionRunner

AGENT_SESSION_PUBLIC_MEMBERS: frozenset[str] = frozenset(
    {
        "abort_bash",
        "abort_retry",
        "agent",
        "append_custom_entry",
        "auth_storage",
        "auto_retry_enabled",
        "bind_extensions",
        "branch",
        "clear_queue",
        "compact",
        "compaction_adapter",
        "compaction_transactions",
        "continue_",
        "create_branched_session",
        "create_replaced_session_context",
        "cwd",
        "cycle_model",
        "cycle_thinking_level",
        "dispose",
        "emit_agent_settled",
        "emit_deferred_session_start",
        "execute_bash",
        "export_to_html",
        "export_to_jsonl",
        "extension_runner",
        "follow_up",
        "follow_up_mode",
        "generation_param_overrides",
        "get_active_tool_names",
        "get_all_tools",
        "get_available_thinking_levels",
        "get_context_usage",
        "get_follow_up_messages",
        "get_known_tool_names",
        "get_last_assistant_text",
        "get_session_entry",
        "get_session_leaf_id",
        "get_session_stats",
        "get_steering_messages",
        "get_tool_definition",
        "get_user_messages_for_forking",
        "has_extension_handlers",
        "has_pending_bash_messages",
        "is_bash_running",
        "is_compacting",
        "is_retrying",
        "is_streaming",
        "messages",
        "model",
        "model_registry",
        "model_role_router",
        "navigate_tree",
        "operation_coordinator",
        "operation_runtime",
        "pending_message_count",
        "process_service",
        "prompt",
        "prompt_templates",
        "record_bash_result",
        "refresh_tools",
        "reload",
        "reload_resources",
        "rename_session",
        "reset_generation_param_override",
        "reset_generation_param_overrides",
        "resolve_model_role",
        "resource_loader",
        "retry_attempt",
        "scoped_models",
        "send_custom_message",
        "session_entries",
        "session_file",
        "session_id",
        "session_name",
        "session_path",
        "session_tree",
        "set_active_tools_by_name",
        "set_auto_retry_enabled",
        "set_compaction_manager",
        "set_follow_up_mode",
        "set_generation_param_override",
        "set_label",
        "set_model",
        "set_scoped_models",
        "set_session_name",
        "set_steering_mode",
        "set_thinking_level",
        "settings_manager",
        "shutdown",
        "state",
        "steer",
        "steering_mode",
        "subagent_observer_errors",
        "subagents",
        "subscribe",
        "supports_thinking",
        "system_prompt",
        "thinking_level",
        "with_model_overrides",
    }
)


class SessionLifecyclePort(Protocol):
    """Lifecycle seam shared by real and replacement session runtimes."""

    def dispose(self) -> None: ...

    def shutdown(self, *args: object, **kwargs: object) -> None: ...


class SessionFactory(Protocol):
    """Callable construction seam used to break concrete session imports."""

    def __call__(self, **kwargs: object) -> SessionLifecyclePort: ...


class SessionRuntimePort(SessionLifecyclePort, Protocol):
    """Session operations required by the replacement runtime host."""

    cwd: str

    @property
    def extension_runner(self) -> ExtensionRunner: ...

    @property
    def session_path(self) -> str | None: ...

    def create_branched_session(self, leaf_id: str, path: str | None = None) -> str: ...

    def emit_deferred_session_start(self) -> None: ...

    def get_session_entry(self, entry_id: str) -> dict[str, object] | None: ...

    def get_session_leaf_id(self) -> str | None: ...


__all__ = [
    "AGENT_SESSION_PUBLIC_MEMBERS",
    "SessionFactory",
    "SessionLifecyclePort",
    "SessionRuntimePort",
]
