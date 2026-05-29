"""LLM-only decompression into `Envelope` objects."""

from __future__ import annotations

import itertools
from typing import Any

from app.decompressor.contracts import PromptChainModelClient
from app.decompressor.env_config import build_decompressor_model_client
from app.decompressor.prompt_chain import LLMPromptChainDecompressor
from app.schemas import Envelope


_REQUEST_COUNTER = itertools.count(1)


class DecompressorRuntime:
    """Runs an injected LLM prompt chain and returns a validated Envelope.

    This runtime intentionally has no deterministic or heuristic Envelope builder.
    Prompt-chain stages are responsible for understanding the request; this class
    owns request IDs and the stable runtime boundary only.
    """

    def __init__(
        self,
        model_client: PromptChainModelClient | None = None,
        prompt_chain: Any | None = None,
    ) -> None:
        if prompt_chain is not None:
            self._prompt_chain = prompt_chain
        elif model_client is not None:
            self._prompt_chain = LLMPromptChainDecompressor(model_client=model_client)
        else:
            raise ValueError("DecompressorRuntime requires an LLM model_client or prompt_chain.")

    @classmethod
    def from_env(cls, dotenv_path: str = ".env", **client_options: Any) -> "DecompressorRuntime":
        """Create an LLM-only runtime from configured environment values."""

        model_client = build_decompressor_model_client(dotenv_path, **client_options)
        if model_client is None:
            raise ValueError("LLM decompressor is not configured. Set DECOMPRESSOR_LLM_ENABLED=true.")
        return cls(model_client=model_client)

    def run(self, user_input: str) -> Envelope:
        request_id = f"req_{next(_REQUEST_COUNTER):03d}"
        return self._prompt_chain.run(user_input or "", request_id)
