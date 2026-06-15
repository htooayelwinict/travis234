"""In-memory evidence store for raw AppV2.1 tool payloads."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class EvidenceStore:
    def __init__(self) -> None:
        self._payloads: dict[str, Any] = {}

    def put_tool_payload(self, *, tool_result_id: str, payload_ref: str, payload: Any) -> str:
        stored = deepcopy(payload)
        self._payloads[payload_ref] = stored
        self._payloads[tool_result_id] = stored
        return payload_ref

    def get(self, ref_or_tool_result_id: str) -> Any:
        payload = self._payloads.get(ref_or_tool_result_id)
        return deepcopy(payload)
