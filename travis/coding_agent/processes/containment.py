"""Private descendant identity tracking for local managed processes."""

from __future__ import annotations

import signal
import threading
from dataclasses import dataclass

import psutil

from travis.coding_agent.processes.transport import SignalName


@dataclass(frozen=True)
class _TrackedProcess:
    pid: int
    create_time: float
    depth: int


class ProcessTreeController:
    def __init__(self, root_pid: int) -> None:
        self._root_pid = root_pid
        self._known: dict[int, _TrackedProcess] = {}
        self._lock = threading.Lock()
        try:
            root = psutil.Process(root_pid)
            self._known[root_pid] = _TrackedProcess(root_pid, root.create_time(), 0)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass

    def refresh(self) -> None:
        try:
            root = psutil.Process(self._root_pid)
            descendants = root.children(recursive=True)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            descendants = []
        observed: list[_TrackedProcess] = []
        for process in descendants:
            try:
                parents = process.parents()
                root_index = next(
                    (index for index, parent in enumerate(parents) if parent.pid == self._root_pid),
                    len(parents) - 1,
                )
                observed.append(
                    _TrackedProcess(process.pid, process.create_time(), root_index + 1)
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        with self._lock:
            self._known.update((item.pid, item) for item in observed)

    def signal(self, signal_name: SignalName) -> None:
        self.refresh()
        selected = {
            "interrupt": signal.SIGINT,
            "terminate": signal.SIGTERM,
            "kill": signal.SIGKILL,
        }[signal_name]
        with self._lock:
            identities = tuple(
                sorted(self._known.values(), key=lambda item: item.depth, reverse=True)
            )
        for identity in identities:
            try:
                process = psutil.Process(identity.pid)
                if process.create_time() != identity.create_time:
                    continue
                process.send_signal(selected)
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue


__all__ = ["ProcessTreeController"]
