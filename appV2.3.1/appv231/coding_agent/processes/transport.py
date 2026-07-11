"""Backend-facing transport protocol for managed process sessions."""

from __future__ import annotations

from typing import BinaryIO, Literal, Protocol


SignalName = Literal["interrupt", "terminate", "kill"]


class ProcessTransport(Protocol):
    tty: bool

    def read_sources(self) -> tuple[BinaryIO, ...]: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def write(self, data: bytes) -> int: ...

    def close_stdin(self) -> None: ...

    def resize(self, rows: int, cols: int) -> None: ...

    def signal_group(self, signal_name: SignalName) -> None: ...

    def close(self) -> None: ...


__all__ = ["ProcessTransport", "SignalName"]
