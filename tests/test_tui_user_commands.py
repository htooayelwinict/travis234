from __future__ import annotations

import os
import shlex
import sys
import threading
import time
from pathlib import Path

import pytest

from travis.coding_agent.agent_session import BashResult
from travis.coding_agent.execution_backend import TrustedLocalBackend
from travis.coding_agent.processes.local import create_local_process_transport
from travis.coding_agent.processes.service import ProcessSessionService
from travis.coding_agent.processes.types import ProcessLaunchRequest, ProcessOwner
from travis.tui.user_commands import (
    ResolvedUserCommand,
    UserCommandBinding,
    UserCommandController,
    UserCommandLimitError,
)


def wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


def request(command: str, cwd: Path) -> ProcessLaunchRequest:
    return ProcessLaunchRequest(
        command=command,
        cwd=str(cwd),
        env=dict(os.environ),
        shell_path="/bin/bash",
        launch_session_id="session-a",
    )


def binding() -> UserCommandBinding:
    return UserCommandBinding(
        session=object(),
        session_id="session-a",
        session_path="/sessions/a.jsonl",
        exclude_from_context=False,
    )


def test_start_returns_before_blocked_resolver_finishes(tmp_path: Path) -> None:
    release = threading.Event()
    service = ProcessSessionService(directory=tmp_path / "processes")
    controller = UserCommandController(
        service=service,
        owner_factory=lambda: ProcessOwner("app", str(tmp_path), "user"),
        resolver=lambda command, launch, signal: (
            release.wait(timeout=2),
            ResolvedUserCommand.immediate(BashResult("done", 0, False, False)),
        )[1],
        transport_factory=lambda launch: create_local_process_transport(launch, TrustedLocalBackend()),
    )
    try:
        started = time.monotonic()
        handle = controller.start("blocked", binding())

        assert time.monotonic() - started < 0.25
        assert handle.command_id.startswith("user_")
        assert controller.inspect(handle.command_id).done is False
    finally:
        release.set()
        controller.close()
        service.close()


def test_managed_command_streams_and_completes_once(tmp_path: Path) -> None:
    service = ProcessSessionService(directory=tmp_path / "processes")
    output = []
    completed = []
    errors = []
    source = "import time; print('progress', flush=True); time.sleep(.05); print('done')"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"
    controller = UserCommandController(
        service=service,
        owner_factory=lambda: ProcessOwner("app", str(tmp_path), "user"),
        resolver=lambda value, launch, signal: ResolvedUserCommand.managed(request(value, tmp_path)),
        transport_factory=lambda launch: create_local_process_transport(launch, TrustedLocalBackend()),
        on_output=lambda command_id, text: output.append((command_id, text)),
        on_complete=lambda handle, result: completed.append((handle, result)),
        on_error=lambda handle, message: errors.append((handle, message)),
    )
    try:
        handle = controller.start(command, binding())

        assert wait_until(lambda: controller.inspect(handle.command_id).process_id is not None)
        assert wait_until(lambda: len(completed) == 1)
        assert errors == []
        assert completed[0][1].exit_code == 0
        assert "progress" in "".join(text for _, text in output)
        assert "done" in completed[0][1].output
        assert controller.inspect(handle.command_id).done is True
    finally:
        controller.close()
        service.close()


def test_repeated_focused_interrupt_escalates_stuck_managed_command(tmp_path: Path) -> None:
    service = ProcessSessionService(
        directory=tmp_path / "processes",
        termination_grace_seconds=1.0,
    )
    completed = []
    output: list[str] = []
    source = (
        "import signal,time; "
        "signal.signal(signal.SIGINT, signal.SIG_IGN); "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(30)"
    )
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"
    controller = UserCommandController(
        service=service,
        owner_factory=lambda: ProcessOwner("app", str(tmp_path), "user"),
        resolver=lambda value, launch, signal: ResolvedUserCommand.managed(request(value, tmp_path)),
        transport_factory=lambda launch: create_local_process_transport(launch, TrustedLocalBackend()),
        on_output=lambda _command_id, text: output.append(text),
        on_complete=lambda handle, result: completed.append(result),
    )
    try:
        handle = controller.start(command, binding())
        assert wait_until(lambda: controller.inspect(handle.command_id).process_id is not None)
        assert wait_until(lambda: "ready" in "".join(output))

        assert controller.interrupt_focused() is True
        time.sleep(0.1)
        assert completed == []
        escalated_at = time.monotonic()
        assert controller.interrupt_focused() is True
        assert time.monotonic() - escalated_at < 0.25
        assert wait_until(lambda: len(completed) == 1)
        assert completed[0].cancelled is True
    finally:
        controller.close()
        service.close()


def test_all_execution_variants_share_four_command_limit(tmp_path: Path) -> None:
    release = threading.Event()
    service = ProcessSessionService(directory=tmp_path / "processes")

    def custom_runner(signal, on_output):
        release.wait(timeout=2)
        return BashResult("custom", 0, signal.aborted, False)

    controller = UserCommandController(
        service=service,
        owner_factory=lambda: ProcessOwner("app", str(tmp_path), "user"),
        resolver=lambda command, launch, signal: ResolvedUserCommand.custom(custom_runner),
        transport_factory=lambda launch: create_local_process_transport(launch, TrustedLocalBackend()),
        max_active=4,
    )
    try:
        for index in range(4):
            controller.start(f"custom-{index}", binding())

        with pytest.raises(UserCommandLimitError, match="limit of 4"):
            controller.start("fifth", binding())
    finally:
        release.set()
        controller.close()
        service.close()


def test_resolved_user_command_requires_exactly_one_variant() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        ResolvedUserCommand()
    with pytest.raises(ValueError, match="exactly one"):
        ResolvedUserCommand(
            result=BashResult("", 0, False, False),
            custom_runner=lambda signal, output: BashResult("", 0, False, False),
        )


def test_interrupt_before_managed_launch_completes_cancelled(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    completed = []
    service = ProcessSessionService(directory=tmp_path / "processes")

    def resolver(command, launch, signal):
        entered.set()
        release.wait(timeout=2)
        return ResolvedUserCommand.managed(request("sleep 30", tmp_path))

    controller = UserCommandController(
        service=service,
        owner_factory=lambda: ProcessOwner("app", str(tmp_path), "user"),
        resolver=resolver,
        transport_factory=lambda launch: create_local_process_transport(launch, TrustedLocalBackend()),
        on_complete=lambda handle, result: completed.append(result),
    )
    try:
        controller.start("delayed", binding())
        assert entered.wait(timeout=1)
        assert controller.interrupt_focused() is True
        release.set()

        assert wait_until(lambda: len(completed) == 1)
        assert completed[0].cancelled is True
        assert service.list(ProcessOwner("app", str(tmp_path), "user")) == ()
    finally:
        release.set()
        controller.close()
        service.close()


def test_resolver_and_callback_failures_are_isolated(tmp_path: Path) -> None:
    service = ProcessSessionService(directory=tmp_path / "processes")
    errors = []
    failing = UserCommandController(
        service=service,
        owner_factory=lambda: ProcessOwner("app", str(tmp_path), "user"),
        resolver=lambda command, launch, signal: (_ for _ in ()).throw(RuntimeError("resolver failed")),
        transport_factory=lambda launch: create_local_process_transport(launch, TrustedLocalBackend()),
        on_error=lambda handle, message: errors.append(message),
    )
    callback_failure = UserCommandController(
        service=service,
        owner_factory=lambda: ProcessOwner("app", str(tmp_path), "user"),
        resolver=lambda command, launch, signal: ResolvedUserCommand.immediate(
            BashResult("done", 0, False, False)
        ),
        transport_factory=lambda launch: create_local_process_transport(launch, TrustedLocalBackend()),
        on_complete=lambda handle, result: (_ for _ in ()).throw(RuntimeError("callback failed")),
    )
    try:
        failed = failing.start("bad", binding())
        completed = callback_failure.start("done", binding())

        assert wait_until(lambda: failing.inspect(failed.command_id).done)
        assert wait_until(lambda: callback_failure.inspect(completed.command_id).done)
        assert "resolver failed" in errors[0]
    finally:
        failing.close()
        callback_failure.close()
        service.close()
