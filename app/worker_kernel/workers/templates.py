"""Agentic worker instance templates."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkerInstanceTemplate:
    name: str
    role: str
    system_prompt: str = ""
    allowed_tools: tuple[str, ...] = ()
