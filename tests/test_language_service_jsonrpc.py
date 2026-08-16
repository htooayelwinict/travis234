from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import psutil
import pytest

from travis.agent.types import AbortSignal
from travis.coding_agent.language_services.jsonrpc import (
    JsonRpcProtocolError,
    JsonRpcRequestError,
    JsonRpcStdioClient,
)
from travis.coding_agent.language_services.types import LanguageServiceLimits

FIXTURE = Path(__file__).parent / "fixtures" / "lsp_fixture_server.py"


def _client(tmp_path: Path, mode: str, *, record: Path | None = None, timeout: float = 0.5) -> JsonRpcStdioClient:
    args = [str(FIXTURE), "--mode", mode]
    if record is not None:
        args += ["--record", str(record)]
    return JsonRpcStdioClient(
        sys.executable,
        args,
        cwd=tmp_path,
        limits=LanguageServiceLimits(request_timeout_seconds=timeout),
    )


def _run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("mode", ["echo", "fragment", "combined", "notify", "lowercase-header"])
def test_client_handles_fragmented_combined_and_interleaved_frames(tmp_path: Path, mode: str) -> None:
    async def scenario() -> None:
        client = _client(tmp_path, mode)
        await client.start()
        try:
            assert await client.request("fixture/echo", {"mode": mode}) == {"mode": mode}
        finally:
            await client.close()
        assert client.process_id is None

    _run(scenario())


def test_request_cancellation_sends_cancel_notification_and_cleans_pending(tmp_path: Path) -> None:
    record = tmp_path / "cancel.json"

    async def scenario() -> None:
        client = _client(tmp_path, "delay", record=record, timeout=5)
        await client.start()
        signal = AbortSignal()
        task = asyncio.create_task(client.request("fixture/slow", {"ok": True}, signal=signal))
        await asyncio.sleep(0.05)
        signal.abort()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.05)
        assert client.pending_request_count == 0
        await client.close()

    _run(scenario())
    assert record.exists()
    assert '"id"' in record.read_text(encoding="utf-8")


def test_request_timeout_is_bounded_and_sends_cancel(tmp_path: Path) -> None:
    record = tmp_path / "timeout.json"

    async def scenario() -> None:
        client = _client(tmp_path, "delay", record=record, timeout=0.05)
        await client.start()
        with pytest.raises(TimeoutError, match="timed out"):
            await client.request("fixture/slow", {})
        await asyncio.sleep(0.05)
        assert client.pending_request_count == 0
        await client.close()

    _run(scenario())
    assert record.exists()


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("malformed", "protocol"),
        ("oversized", "frame"),
        ("exit", "closed"),
    ],
)
def test_protocol_eof_and_frame_failures_are_shaped(tmp_path: Path, mode: str, message: str) -> None:
    async def scenario() -> None:
        client = _client(tmp_path, mode)
        await client.start()
        with pytest.raises(JsonRpcProtocolError, match=message):
            await client.request("fixture/fail", {})
        await client.close()

    _run(scenario())


def test_server_error_is_shaped_without_protocol_failure(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = _client(tmp_path, "error")
        await client.start()
        with pytest.raises(JsonRpcRequestError) as caught:
            await client.request("fixture/fail", {})
        assert caught.value.code == -32001
        assert "fixture failure" in str(caught.value)
        await client.close()

    _run(scenario())


def test_subprocess_environment_excludes_credentials(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-leak")
    monkeypatch.setenv("TRAVIS234_WORKER_LLM_API_KEY", "must-not-leak")
    monkeypatch.setenv("SAFE_RUNTIME_MARKER", "allowed")

    async def scenario() -> None:
        client = _client(tmp_path, "echo")
        await client.start()
        keys = await client.request("fixture/env", {})
        assert "PATH" in keys
        assert "OPENROUTER_API_KEY" not in keys
        assert "TRAVIS234_WORKER_LLM_API_KEY" not in keys
        assert "SAFE_RUNTIME_MARKER" not in keys
        assert "OPENROUTER_API_KEY" not in client.environment
        assert "TRAVIS234_WORKER_LLM_API_KEY" not in client.environment
        assert "SAFE_RUNTIME_MARKER" not in client.environment
        await client.close()

    _run(scenario())


def test_stderr_tail_redacts_sensitive_assignment(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = _client(tmp_path, "stderr")
        await client.start()
        with pytest.raises(JsonRpcProtocolError) as caught:
            await client.request("fixture/fail", {})
        assert "fixture-super-secret" not in str(caught.value)
        assert "OPENROUTER_API_KEY=[REDACTED]" in str(caught.value)
        await client.close()

    _run(scenario())


def test_close_resolves_pending_requests_and_leaves_no_process(tmp_path: Path) -> None:
    async def scenario() -> int:
        client = _client(tmp_path, "delay", timeout=5)
        await client.start()
        pid = client.process_id
        assert pid is not None
        task = asyncio.create_task(client.request("fixture/slow", {}))
        await asyncio.sleep(0.05)
        await client.close()
        with pytest.raises((JsonRpcProtocolError, asyncio.CancelledError)):
            await task
        assert client.pending_request_count == 0
        assert client.process_id is None
        return pid

    pid = _run(scenario())
    assert not psutil.pid_exists(pid)


def test_initialized_close_requests_shutdown_then_exit(tmp_path: Path) -> None:
    record = tmp_path / "close.jsonl"

    async def scenario() -> None:
        client = _client(tmp_path, "echo", record=record)
        await client.start()
        client.initialized = True
        await client.close()

    _run(scenario())
    recorded = record.read_text(encoding="utf-8")
    assert '"method":"shutdown"' in recorded
    assert recorded.index('"method":"shutdown"') < recorded.index('"method":"exit"')


def test_client_survives_separate_sync_turn_event_loops(tmp_path: Path) -> None:
    client = _client(tmp_path, "echo", timeout=0.2)

    assert _run(client.request("fixture/first-turn", {"turn": 1})) == {"turn": 1}
    assert _run(client.request("fixture/second-turn", {"turn": 2})) == {"turn": 2}
    _run(client.close())

    assert client.process_id is None


def test_concurrent_close_calls_stop_one_runtime(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = _client(tmp_path, "echo")
        await client.request("fixture/echo", {"ok": True})
        await asyncio.gather(client.close(), client.close())
        assert client.process_id is None

    _run(scenario())
