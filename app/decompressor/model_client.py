"""OpenRouter SDK-backed JSON model client for decompressor prompt-chain mode."""

from __future__ import annotations

from typing import Any

from openrouter import OpenRouter


class OpenAICompatibleJSONClient:
    """Client wrapper for OpenRouter chat completions with JSON responses."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30.0,
        temperature: float = 0.0,
        response_format: str = "json_schema",
        provider_sort: str | None = None,
        max_tokens: int | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._temperature = temperature
        self._response_format = response_format
        self._provider_sort = provider_sort
        self._max_tokens = max_tokens
        self._client = OpenRouter(
            api_key=self._api_key,
            server_url=self._base_url,
            timeout_ms=int(self._timeout_seconds * 1000),
        )

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "temperature": self._temperature,
            "messages": [
                {
                    "role": "system",
                    "content": "Return only JSON matching the schema. Be specific and concise.",
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": self._response_format_payload(stage, schema),
            "stream": False,
            "timeout_ms": int(self._timeout_seconds * 1000),
        }
        if self._max_tokens is not None:
            kwargs["max_completion_tokens"] = self._max_tokens
        if self._provider_sort:
            kwargs["provider"] = {
                "sort": self._provider_sort,
                "allow_fallbacks": True,
            }
        if self._response_format in {"json_schema", "json_object"}:
            kwargs["plugins"] = [{"id": "response-healing"}]

        try:
            response = self._client.chat.send(**kwargs)
        except Exception as exc:  # pragma: no cover - SDK/network variability
            detail = _sdk_error_detail(exc)
            message = f"Model request for stage {stage} failed before receiving a response."
            if detail:
                message = f"{message} {detail}"
            raise RuntimeError(message) from exc

        return self._extract_content(stage, response)

    def _response_format_payload(self, stage: str, schema: dict[str, Any]) -> dict[str, Any]:
        if self._response_format == "json_object":
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {
                "name": f"{stage}_output",
                "schema": schema,
                "strict": False,
            },
        }

    def _extract_content(self, stage: str, response: Any) -> str:
        try:
            content = response.choices[0].message.content
        except Exception as exc:
            raise RuntimeError(f"Model response for stage {stage} did not contain JSON content.") from exc

        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                    continue
                text = getattr(part, "text", None)
                if isinstance(text, str):
                    parts.append(text)
            combined = "".join(parts).strip()
            if combined:
                return combined
        raise RuntimeError(f"Model response for stage {stage} returned non-text content.")


def _sdk_error_detail(exc: Exception) -> str:
    body = getattr(exc, "body", None)
    if isinstance(body, str) and body.strip():
        return f"OpenRouter error body: {body.strip()[:500]}"
    raw_response = getattr(exc, "raw_response", None)
    text = getattr(raw_response, "text", None)
    if isinstance(text, str) and text.strip():
        return f"OpenRouter error body: {text.strip()[:500]}"
    return ""
