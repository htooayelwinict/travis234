"""Synthetic browser/computer extension used by the optional conformance gate.

It performs no browser, accessibility, or desktop action.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import tempfile

from travis.agent.types import AgentToolResult
from travis.ai.types import TextContent
from travis.coding_agent.artifacts import ArtifactRegistry, artifact_read_instruction
from travis.coding_agent.tools.types import ToolDefinition


_SOURCE_BYTES = 60_000


def extension(travis) -> None:
    artifacts = ArtifactRegistry()

    async def execute(_tool_call_id, _args, signal=None, _on_update=None, _ctx=None):
        if signal is not None and signal.aborted:
            raise asyncio.CancelledError
        descriptor, raw_path = tempfile.mkstemp(prefix="travis234-optional-fixture-", suffix=".txt")
        path = Path(raw_path)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(b"x" * _SOURCE_BYTES)
            artifact = artifacts.register(path, kind="optional-integration-output")
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            path.unlink(missing_ok=True)
            raise
        return AgentToolResult(
            content=[
                TextContent(
                    text=(
                        "Synthetic optional integration completed. "
                        f"{artifact_read_instruction(artifact.id)}"
                    )
                )
            ],
            details={"artifactId": artifact.id, "sourceBytes": _SOURCE_BYTES},
        )

    travis.register_tool(
        ToolDefinition(
            name="browser_fixture",
            label="Browser fixture",
            description="Synthetic browser conformance probe",
            parameters={"type": "object", "additionalProperties": True},
            execute=execute,
            effects=frozenset({"read", "write", "network"}),
            policy_context=lambda _args: {
                "action": "conformance",
                "target": "browser-fixture",
            },
        )
    )
    travis.register_tool(
        ToolDefinition(
            name="computer_fixture",
            label="Computer fixture",
            description="Synthetic computer-use conformance probe",
            parameters={"type": "object", "additionalProperties": True},
            execute=execute,
            effects=frozenset({"read", "write", "execute"}),
            policy_context=lambda _args: {
                "action": "conformance",
                "target": "computer-fixture",
            },
        )
    )

    async def shutdown(_event, _ctx) -> None:
        artifacts.close(remove_files=True)

    travis.on("session_shutdown", shutdown)
