"""Model adapter boundary for worker-agent decisions."""

from __future__ import annotations

import json
from typing import Any, Callable, Protocol


class WorkerModelAdapter(Protocol):
    """Turns one worker prompt into one validated worker decision."""

    def decide(self, *, stage: str, prompt: str) -> Any:
        """Return the worker decision object expected by the caller."""


class WorkerModelDecisionError(Exception):
    """Raised when a provider response cannot be normalized into a worker decision."""

    def __init__(self, message: str, *, raw_response: Any | None = None) -> None:
        self.raw_response = raw_response
        super().__init__(message)


class JSONDecisionAdapter:
    """Default JSON-text adapter used by the current OpenAI-compatible client.

    This keeps the existing JSON response protocol stable while isolating the
    provider/normalization boundary from the agent loop.
    """

    def __init__(
        self,
        *,
        model_client: Any,
        schema: dict[str, Any],
        normalizer: Callable[[Any], Any],
        validator: Callable[[Any], Any],
    ) -> None:
        self._model_client = model_client
        self._schema = schema
        self._normalizer = normalizer
        self._validator = validator

    def decide(self, *, stage: str, prompt: str) -> Any:
        response = self._model_client.complete_json(
            stage=stage,
            prompt=prompt,
            schema=self._schema,
        )
        try:
            parsed = json.loads(response)
        except json.JSONDecodeError as exc:
            raise WorkerModelDecisionError(
                f"worker model returned invalid JSON: {exc}",
                raw_response=response,
            ) from exc
        try:
            return self._validator(self._normalizer(parsed))
        except Exception as exc:  # pydantic exposes several concrete errors.
            raise WorkerModelDecisionError(
                f"worker model decision failed schema validation: {exc}",
                raw_response=parsed,
            ) from exc


class NativeToolCallAdapter:
    """Placeholder boundary for provider-native tool calls.

    The runtime keeps JSON mode as default. This adapter exists so native tool
    calling can be wired without changing the agent loop or worker groups.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("native worker tool-call adapter is not enabled yet")
