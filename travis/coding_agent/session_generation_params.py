"""Focused durable generation-parameter ownership for coding sessions."""

from __future__ import annotations

from travis.ai.providers.params import (
    GenerationParams,
    remove_generation_param,
    replace_generation_param,
)


class SessionGenerationParams:
    """Own the active session's immutable override-only parameter snapshot."""

    @property
    def generation_param_overrides(self) -> GenerationParams:
        return self._generation_param_overrides

    def set_generation_param_override(
        self,
        name: str,
        raw_value: object,
    ) -> GenerationParams:
        candidate = replace_generation_param(
            self._generation_param_overrides,
            name,
            raw_value,
        )
        return self._publish_generation_param_overrides(candidate)

    def reset_generation_param_override(self, name: str) -> GenerationParams:
        candidate = remove_generation_param(
            self._generation_param_overrides,
            name,
        )
        return self._publish_generation_param_overrides(candidate)

    def reset_generation_param_overrides(self) -> GenerationParams:
        return self._publish_generation_param_overrides(GenerationParams())

    def _publish_generation_param_overrides(
        self,
        candidate: GenerationParams,
    ) -> GenerationParams:
        if candidate == self._generation_param_overrides:
            return candidate
        if self._session_store is not None:
            self._session_store.append_generation_params_change(candidate)
        self._generation_param_overrides = candidate
        return candidate

    def _restore_generation_param_overrides(self, params: GenerationParams) -> None:
        self._generation_param_overrides = params


__all__ = ["SessionGenerationParams"]
