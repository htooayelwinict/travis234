"""Direct, durable session generation-parameter controls for the TUI."""

from __future__ import annotations

from dataclasses import replace

from travis.ai.providers.capabilities import ProviderParamWarning, build_generation_payload
from travis.ai.providers.catalog import determine_api_mode
from travis.ai.providers.params import (
    GENERATION_PARAM_FIELDS,
    GenerationParams,
    compact_generation_params_display,
    merge_generation_params,
)
from travis.ai.providers.transports import get_transport
from travis.ai.types import SimpleStreamOptions


_PARAM_NAMES = ("thinking", *GENERATION_PARAM_FIELDS)


def _compact_generation_param_warnings(warnings: list[ProviderParamWarning]) -> str:
    return ", ".join(f"{warning.param} {warning.action}" for warning in warnings)


def _params_argument_completions(argument_text: str) -> list[dict[str, str]]:
    normalized = argument_text.strip().lower()
    if normalized == "reset" or normalized.startswith("reset "):
        field_prefix = normalized.removeprefix("reset").strip()
        return [
            {
                "value": f"reset {name}",
                "label": f"reset {name}",
                "description": f"Clear the session {name} override",
            }
            for name in GENERATION_PARAM_FIELDS
            if name.startswith(field_prefix)
        ]
    return [
        {
            "value": name,
            "label": name,
            "description": (
                "Change the durable session thinking level"
                if name == "thinking"
                else f"Show or change session {name}"
            ),
        }
        for name in (*_PARAM_NAMES, "reset")
        if name.startswith(normalized)
    ]


class InteractiveParams:
    """Own `/params` grammar, display, mutation, and capability state."""

    def _session_generation_param_overrides(self) -> GenerationParams:
        params = getattr(self.app.session, "generation_param_overrides", None)
        return params if isinstance(params, GenerationParams) else GenerationParams()

    def _effective_generation_params(self) -> GenerationParams:
        return merge_generation_params(
            self.startup_generation_params,
            self._session_generation_param_overrides(),
        )

    def _refresh_generation_param_state(self) -> None:
        self.generation_params = self._effective_generation_params()
        model = self.app.session.model
        get_active_tool_names = getattr(self.app.session, "get_active_tool_names", None)
        tools_enabled = (
            bool(get_active_tool_names())
            if callable(get_active_tool_names)
            else False
        )
        api_mode = getattr(get_transport(model.api), "api_mode", model.api)
        try:
            payload = build_generation_payload(
                provider=model.provider,
                api_mode=api_mode,
                params=self.generation_params,
                tools_enabled=tools_enabled,
            )
        except ValueError:
            payload = build_generation_payload(
                provider=model.provider,
                api_mode=determine_api_mode(model.provider, model.base_url),
                params=self.generation_params,
                tools_enabled=tools_enabled,
            )
        self.generation_param_warnings = list(payload.warnings)

    def _stream_with_session_generation_params(self, model, context, options=None):
        current = options or SimpleStreamOptions()
        effective = self._effective_generation_params()
        requested_max = effective.max_tokens
        runtime_max = current.max_tokens
        if requested_max is None:
            bounded_max = runtime_max
        elif runtime_max is None:
            bounded_max = requested_max
        else:
            bounded_max = min(requested_max, runtime_max)
        adapted = replace(
            current,
            generation_params=effective,
            max_tokens=bounded_max,
        )
        return self.app.session.model_registry.stream_simple(model, context, adapted)

    def _run_params_command(self, query: str | None = None) -> None:
        normalized_query = (query or "").strip()
        if not normalized_query:
            self._show_params()
            return

        if normalized_query == "reset":
            self._reset_all_generation_params()
            return

        if normalized_query.startswith("reset "):
            name = normalized_query[len("reset ") :].strip().lower()
            self._reset_generation_param(name)
            return

        if " " not in normalized_query:
            self._show_params(normalized_query)
            return

        name, raw_value = normalized_query.split(maxsplit=1)
        self._set_session_param(name.lower(), raw_value)

    def _show_params(self, query: str | None = None) -> None:
        self._refresh_generation_param_state()
        provider = getattr(self.app.session.model, "provider", "")
        model_id = getattr(self.app.session.model, "id", "")
        thinking_display = f"thinking={self.app.session.thinking_level}"
        params_display = compact_generation_params_display(self.generation_params)
        warning_display = _compact_generation_param_warnings(self.generation_param_warnings)

        if query:
            normalized = query.strip().lower()
            pieces = [thinking_display] if normalized in thinking_display.lower() else []
            pieces.extend(
                part
                for part in params_display.split(", ")
                if normalized in part.lower()
            )
            warning_pieces = [
                part
                for part in warning_display.split(", ")
                if part and normalized in part.lower()
            ]
            if pieces:
                display = ", ".join(pieces)
            elif warning_pieces:
                display = f"warnings: {', '.join(warning_pieces)}"
            else:
                display = f"no generation parameter matching {query}"
            self._show_status(f"{provider}/{model_id}: {display}", kind="model")
            return

        display = f"{thinking_display}, {params_display}"
        if warning_display:
            display = f"{display}; warnings: {warning_display}"
        self._show_status(f"{provider}/{model_id}: {display}", kind="model")

    def _set_session_param(self, name: str, raw_value: str) -> None:
        if name not in _PARAM_NAMES:
            self._show_unknown_param(name)
            return
        if self._reject_active_param_write():
            return
        if name == "thinking" and raw_value.strip().lower() in {"", "none", "null"}:
            self._show_status(
                "Thinking requires a level; use /params thinking <level>.",
                kind="error",
            )
            return
        try:
            if name == "thinking":
                self.app.session.set_thinking_level(raw_value.strip().lower())
            else:
                self.app.session.set_generation_param_override(name, raw_value)
        except ValueError as error:
            self._show_status(str(error), kind="error")
            return
        except Exception as error:  # noqa: BLE001 - command failures render without crashing the TUI.
            self._show_status(f"Failed to update {name}: {error}", kind="error")
            return

        self._refresh_generation_param_state()
        if name == "thinking":
            value_display = self.app.session.thinking_level
        else:
            value_display = self._effective_param_display(name)
        self._show_status(
            f"Session {name}={value_display}; applies to the next turn.",
            kind="model",
        )
        self._refresh_footer()
        self.tui.request_render()

    def _reset_generation_param(self, name: str) -> None:
        if not name or " " in name:
            self._show_status("Usage: /params reset <name>", kind="error")
            return
        if name == "thinking":
            self._show_status(
                "Thinking must be set explicitly with /params thinking <level>.",
                kind="error",
            )
            return
        if name not in GENERATION_PARAM_FIELDS:
            self._show_unknown_param(name)
            return
        if self._reject_active_param_write():
            return
        try:
            self.app.session.reset_generation_param_override(name)
        except Exception as error:  # noqa: BLE001 - persistence errors must preserve the running TUI.
            self._show_status(f"Failed to reset {name}: {error}", kind="error")
            return
        self._refresh_generation_param_state()
        value_display = self._effective_param_display(name)
        self._show_status(
            f"Reset {name} to {value_display}; applies to the next turn.",
            kind="model",
        )
        self._refresh_footer()
        self.tui.request_render()

    def _reset_all_generation_params(self) -> None:
        if self._reject_active_param_write():
            return
        try:
            self.app.session.reset_generation_param_overrides()
        except Exception as error:  # noqa: BLE001 - persistence errors must preserve the running TUI.
            self._show_status(f"Failed to reset session parameters: {error}", kind="error")
            return
        self._refresh_generation_param_state()
        self._show_status(
            "Reset all session generation parameters; applies to the next turn. Thinking is unchanged.",
            kind="model",
        )
        self._refresh_footer()
        self.tui.request_render()

    def _effective_param_display(self, name: str) -> str:
        for part in compact_generation_params_display(self.generation_params).split(", "):
            if part.startswith(f"{name}="):
                return part.split("=", 1)[1]
        return "provider/model default"

    def _reject_active_param_write(self) -> bool:
        if not self._is_turn_active():
            return False
        self._show_status(
            "Cannot change session parameters while an Agent turn is active.",
            kind="error",
        )
        return True

    def _show_unknown_param(self, name: str) -> None:
        supported = ", ".join(_PARAM_NAMES)
        self._show_status(
            f"Unknown parameter '{name}'. Supported: {supported}.",
            kind="error",
        )


__all__ = (
    "InteractiveParams",
    "_compact_generation_param_warnings",
    "_params_argument_completions",
)
