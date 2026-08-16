"""Credential-free smoke for the derived optional MCP adapter image."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import Callable, Sequence


_CREDENTIAL_NAMES = (
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "TRAVIS_COMPRESSION_API_KEY",
    "TRAVIS234_COMPRESSION_LLM_API_KEY",
    "TRAVIS234_WORKER_LLM_API_KEY",
)
_RESOURCE_REFERENCE = re.compile(r"mcp-resource-[0-9a-f]{32}")


@dataclass(frozen=True)
class McpContainerQualification:
    proxy_fixture: bool
    resource_read: bool
    prompt_get: bool
    side_effect_invocation_once: bool
    reconnect: bool
    child_reaped: bool
    spill_cleaned: bool
    credential_env_absent: bool

    @property
    def passed(self) -> bool:
        return all(asdict(self).values())


def build_docker_command(image: str) -> tuple[str, ...]:
    if not isinstance(image, str) or not image.strip():
        raise ValueError("MCP smoke image must be non-empty")
    return (
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        image,
        "--inside-container",
    )


def run_mcp_container_smoke(
    image: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> McpContainerQualification:
    completed = runner(
        build_docker_command(image),
        text=True,
        capture_output=True,
        check=False,
        timeout=180,
    )
    returncode = int(getattr(completed, "returncode", 1))
    stdout = str(getattr(completed, "stdout", "") or "")
    stderr = str(getattr(completed, "stderr", "") or "")
    if returncode != 0:
        raise RuntimeError(
            f"MCP container smoke failed ({returncode}): {stderr[-2_000:]}"
        )
    try:
        payload = json.loads(stdout)
        result = McpContainerQualification(
            **{
                name: payload.get(name) is True
                for name in McpContainerQualification.__dataclass_fields__
            }
        )
    except (AttributeError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("MCP container smoke returned invalid JSON") from error
    if payload.get("passed") is not True or not result.passed:
        raise RuntimeError(f"MCP container qualification failed: {asdict(result)}")
    return result


def _text(result: object) -> str:
    content = getattr(result, "content", ())
    return "\n".join(
        str(getattr(block, "text"))
        for block in content
        if isinstance(getattr(block, "text", None), str)
    )


def _marker(result: object) -> dict[str, object]:
    details = getattr(result, "details", {})
    marker = details.get("travis234Mcp") if isinstance(details, dict) else None
    return marker if isinstance(marker, dict) else {}


async def _inside_container() -> McpContainerQualification:
    from travis234_mcp_adapter.config import (
        LoadedConfig,
        ReconnectConfig,
        ServerConfig,
    )
    from travis234_mcp_adapter.output_guard import SpillRegistry
    from travis234_mcp_adapter.proxy_tool import (
        create_proxy_definition,
        dispatch_proxy,
    )
    from travis234_mcp_adapter.runtime import McpRuntime

    with tempfile.TemporaryDirectory(prefix="travis234-mcp-smoke-") as raw_root:
        root = Path(raw_root)
        pid_file = root / "fixture-pids.txt"
        invocation_file = root / "side-effect-count.txt"
        spill_dir = root / "spills"
        server = ServerConfig(
            name="fixture",
            source_path=root / "mcp.json",
            command=sys.executable,
            args=(str(Path(__file__).resolve()), "--fixture-server"),
            env={
                "MCP_SMOKE_PID_FILE": str(pid_file),
                "MCP_SMOKE_INVOCATION_FILE": str(invocation_file),
            },
            request_timeout_ms=2_000,
            reconnect=ReconnectConfig(
                automatic=True,
                max_attempts=2,
                base_delay_ms=100,
            ),
        )
        runtime = McpRuntime({"fixture": server}, lambda: os.environ)
        spills = SpillRegistry(spill_dir)
        state = SimpleNamespace(
            config=LoadedConfig(
                servers={"fixture": server},
                sources=(server.source_path,),
                ignored_project_sources=(),
            ),
            config_error=None,
            runtime=runtime,
            catalogs={},
            resource_catalogs={},
            prompt_catalogs={},
            spills=spills,
            generation=1,
            shadowed_configured_names=(),
        )
        proxy_fixture = create_proxy_definition(state).name == "mcp"
        resource_read = False
        prompt_get = False
        side_effect_invocation_once = False
        reconnect = False
        spill_created = False
        try:
            listed_resources = await dispatch_proxy(
                state,
                {"server": "fixture", "operation": "resources.list"},
                None,
            )
            match = _RESOURCE_REFERENCE.search(_text(listed_resources))
            if match is not None:
                read = await dispatch_proxy(
                    state,
                    {
                        "server": "fixture",
                        "operation": "resources.read",
                        "resource": match.group(0),
                    },
                    None,
                )
                resource_read = (
                    _marker(read).get("isError") is False
                    and "fixture resource text" in _text(read)
                    and "Untrusted MCP resource data" in _text(read)
                )

            listed_prompts = await dispatch_proxy(
                state,
                {"server": "fixture", "operation": "prompts.list"},
                None,
            )
            if "fixture-review" in _text(listed_prompts):
                prompt = await dispatch_proxy(
                    state,
                    {
                        "server": "fixture",
                        "operation": "prompts.get",
                        "prompt": "fixture-review",
                        "arguments": {"topic": "contracts", "tone": "brief"},
                    },
                    None,
                )
                prompt_get = (
                    _marker(prompt).get("isError") is False
                    and "Review contracts." in _text(prompt)
                    and "Untrusted MCP prompt data" in _text(prompt)
                )

            failed = await dispatch_proxy(
                state,
                {
                    "server": "fixture",
                    "operation": "tools.call",
                    "name": "fail_side_effect",
                    "arguments": {},
                },
                None,
            )
            side_effect_invocation_once = (
                _marker(failed).get("isError") is True
                and invocation_file.read_text(encoding="ascii").strip() == "1"
            )
            recovered = runtime.status("fixture").state == "connected"
            explicit = await dispatch_proxy(
                state,
                {"server": "fixture", "operation": "reconnect"},
                None,
            )
            reconnect = recovered and _marker(explicit).get("isError") is False

            oversized = await dispatch_proxy(
                state,
                {
                    "server": "fixture",
                    "operation": "tools.call",
                    "name": "large_output",
                    "arguments": {"size": 60_000},
                },
                None,
            )
            spill_created = _marker(oversized).get("spilled") is True and any(
                spill_dir.glob("travis234-mcp-*.txt")
            )
        finally:
            await runtime.close()
            spills.cleanup()

        pids = {
            int(line)
            for line in pid_file.read_text(encoding="ascii").splitlines()
            if line.strip().isdigit()
        } if pid_file.is_file() else set()
        deadline = time.monotonic() + 2
        while any(_pid_exists(pid) for pid in pids) and time.monotonic() < deadline:
            await asyncio.sleep(0.02)
        return McpContainerQualification(
            proxy_fixture=proxy_fixture,
            resource_read=resource_read,
            prompt_get=prompt_get,
            side_effect_invocation_once=side_effect_invocation_once,
            reconnect=reconnect,
            child_reaped=bool(pids) and not any(_pid_exists(pid) for pid in pids),
            spill_cleaned=spill_created and not any(spill_dir.glob("*")),
            credential_env_absent=not any(name in os.environ for name in _CREDENTIAL_NAMES),
        )


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _fixture_server() -> int:
    from mcp.server import MCPServer

    pid_file = Path(os.environ["MCP_SMOKE_PID_FILE"])
    with pid_file.open("a", encoding="ascii") as stream:
        stream.write(f"{os.getpid()}\n")

    server = MCPServer("travis234-derived-smoke")

    @server.tool()
    def large_output(size: int) -> str:
        """Return controlled oversized output."""

        return "x" * size

    @server.tool()
    def fail_side_effect() -> str:
        """Record one side effect, then simulate a lost transport."""

        path = Path(os.environ["MCP_SMOKE_INVOCATION_FILE"])
        count = int(path.read_text(encoding="ascii")) if path.is_file() else 0
        path.write_text(str(count + 1), encoding="ascii")
        os._exit(17)

    @server.resource(
        "fixture://manual",
        name="fixture-manual",
        description="Fixture text resource",
        mime_type="text/plain",
    )
    def fixture_manual() -> str:
        return "fixture resource text"

    @server.prompt(name="fixture-review", description="Review a fixture topic")
    def fixture_review(topic: str, tone: str = "brief") -> list[dict[str, str]]:
        return [
            {"role": "user", "content": f"Review {topic}."},
            {"role": "assistant", "content": f"Use a {tone} response."},
        ]

    asyncio.run(server.run_stdio_async())
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image")
    parser.add_argument("--inside-container", action="store_true")
    parser.add_argument("--fixture-server", action="store_true")
    args = parser.parse_args(argv)
    if args.fixture_server:
        return _fixture_server()
    if args.inside_container:
        result = asyncio.run(_inside_container())
        print(json.dumps({**asdict(result), "passed": result.passed}, sort_keys=True))
        return 0 if result.passed else 1
    if not args.image:
        parser.error("--image is required outside the derived container")
    run_mcp_container_smoke(args.image)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
