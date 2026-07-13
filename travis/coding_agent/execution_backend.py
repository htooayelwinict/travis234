"""Declared process execution trust boundaries for the coding profile."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Literal, Mapping


class ExecutionBackend:
    mode: Literal["trusted", "sandboxed"]
    filesystem_contained: bool

    def spawn(
        self,
        command: str,
        cwd: str,
        env: Mapping[str, str],
        options: Mapping[str, object] | None = None,
    ) -> subprocess.Popen:
        options = options or {}
        shell = str(options.get("shell_path") or os.environ.get("SHELL") or "/bin/bash")
        return subprocess.Popen(
            [shell, "-c", command],
            cwd=cwd,
            env=dict(env),
            stdin=options.get("stdin", subprocess.DEVNULL),
            stdout=options.get("stdout", subprocess.PIPE),
            stderr=options.get("stderr", subprocess.PIPE),
            start_new_session=bool(options.get("start_new_session", os.name == "posix")),
        )


class TrustedLocalBackend(ExecutionBackend):
    mode: Literal["trusted"] = "trusted"
    filesystem_contained = False


class ContainerSandboxBackend(ExecutionBackend):
    mode: Literal["sandboxed"] = "sandboxed"
    filesystem_contained = True

    def __init__(self, workspace_root: Path, agent_home: Path) -> None:
        if os.environ.get("TRAVIS234_SANDBOX") != "1":
            raise RuntimeError("Container sandbox backend requires the TRAVIS234_SANDBOX=1 sandbox marker")
        expected_workspace = Path(os.environ.get("TRAVIS234_WORKSPACE_ROOT", "/workspace")).resolve()
        expected_agent_home = Path(os.environ.get("TRAVIS234_AGENT_HOME", "/travis-home")).resolve()
        self.workspace_root = workspace_root.resolve()
        self.agent_home = agent_home.resolve()
        if self.workspace_root != expected_workspace or self.agent_home != expected_agent_home:
            raise RuntimeError("Sandbox marker roots do not match the canonical workspace and agent home")


def select_execution_backend(cwd: str) -> ExecutionBackend:
    if os.environ.get("TRAVIS234_SANDBOX") != "1":
        return TrustedLocalBackend()
    return ContainerSandboxBackend(
        Path(cwd),
        Path(os.environ.get("TRAVIS234_AGENT_HOME", os.environ.get("HOME", "/travis-home"))),
    )


__all__ = [
    "ContainerSandboxBackend",
    "ExecutionBackend",
    "TrustedLocalBackend",
    "select_execution_backend",
]
