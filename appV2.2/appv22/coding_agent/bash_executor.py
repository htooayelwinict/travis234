"""Shared Pi-style bash execution helper."""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from typing import Callable, Mapping

from appv22.coding_agent.agent_session import BashResult
from appv22.coding_agent.tools.bash import BashExecOptions, BashOperations
from appv22.coding_agent.tools.output_accumulator import OutputAccumulator

_ANSI_RE = re.compile(
    r"(?:\x1b\][\s\S]*?(?:\x07|\x1b\\|\x9c))|"
    r"(?:[\x1b\x9b][\[\]()#;?]*(?:\d{1,4}(?:[;:]\d{0,4})*)?[\dA-PR-TZcf-nq-uy=><~])"
)


@dataclass(frozen=True)
class BashExecutorOptions:
    on_chunk: Callable[[str], None] | None = None
    signal: object | None = None

    @property
    def onChunk(self) -> Callable[[str], None] | None:
        return self.on_chunk


def execute_bash_with_operations(
    command: str,
    cwd: str,
    operations: BashOperations,
    options: BashExecutorOptions | Mapping[str, object] | None = None,
) -> BashResult:
    """Execute a bash command through custom operations, matching Pi's public helper."""
    executor_options = _coerce_options(options)
    decoder = codecs.getincrementaldecoder("utf-8")()
    output = OutputAccumulator(temp_file_prefix="pi-bash")

    def on_data(data: bytes) -> None:
        text = decoder.decode(data, final=False)
        sanitized = _sanitize_binary_output(_strip_ansi(text)).replace("\r", "")
        if not sanitized:
            return
        output.append(sanitized.encode("utf-8"))
        if executor_options.on_chunk:
            executor_options.on_chunk(sanitized)

    exit_code: int | None = None
    cancelled = False
    try:
        result = operations.exec(
            command,
            cwd,
            BashExecOptions(on_data=on_data, signal=executor_options.signal),
        )
        exit_code = result.get("exit_code")
    except Exception:
        if _is_aborted(executor_options.signal):
            cancelled = True
        else:
            raise
    finally:
        tail = decoder.decode(b"", final=True)
        if tail:
            sanitized_tail = _sanitize_binary_output(_strip_ansi(tail)).replace("\r", "")
            if sanitized_tail:
                output.append(sanitized_tail.encode("utf-8"))
                if executor_options.on_chunk:
                    executor_options.on_chunk(sanitized_tail)
        output.finish()

    snapshot = output.snapshot(persist_if_truncated=True)
    output.close_temp_file()
    return BashResult(
        output=snapshot.content,
        exit_code=None if cancelled else exit_code,
        cancelled=cancelled,
        truncated=bool(snapshot.truncation.truncated),
        full_output_path=snapshot.full_output_path,
    )


def _coerce_options(options: BashExecutorOptions | Mapping[str, object] | None) -> BashExecutorOptions:
    if isinstance(options, BashExecutorOptions):
        return options
    if not isinstance(options, Mapping):
        return BashExecutorOptions()
    on_chunk = options.get("on_chunk")
    if on_chunk is None:
        on_chunk = options.get("onChunk")
    return BashExecutorOptions(
        on_chunk=on_chunk if callable(on_chunk) else None,
        signal=options.get("signal"),
    )


def _is_aborted(signal: object | None) -> bool:
    return signal is not None and bool(getattr(signal, "aborted", False))


def _strip_ansi(text: str) -> str:
    if "\x1b" not in text and "\x9b" not in text:
        return text
    return _ANSI_RE.sub("", text)


def _sanitize_binary_output(text: str) -> str:
    kept: list[str] = []
    for char in text:
        code = ord(char)
        if code in (0x09, 0x0A, 0x0D):
            kept.append(char)
        elif code <= 0x1F:
            continue
        elif 0xFFF9 <= code <= 0xFFFB:
            continue
        else:
            kept.append(char)
    return "".join(kept)


executeBashWithOperations = execute_bash_with_operations
