from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Literal

from travis234_ghost_mcp.host import ghost_binary, package_root

CommandMode = Literal["setup", "doctor"]
MAX_OUTPUT_BYTES = 16_384
_SAFE_ENVIRONMENT = (
    "HOME",
    "PATH",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "TERM_PROGRAM",
)


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    text: str
    exit_code: int


def parse_setup_argument(argument: str) -> bool:
    value = argument.strip()
    if not value:
        return False
    if value == "vision":
        return True
    raise ValueError("/ghost-setup accepts only empty input or 'vision'")


def run_ghost_command(
    mode: CommandMode,
    include_vision: bool = False,
) -> CommandResult:
    if mode not in ("setup", "doctor"):
        raise ValueError("Unsupported Ghost command mode")
    if mode == "doctor" and include_vision:
        raise ValueError("Vision is valid only for Ghost setup")

    binary = ghost_binary()
    if mode == "doctor":
        argv = [str(binary), "doctor", "--json"]
        timeout = 30
    else:
        argv = [str(binary), "setup"]
        if include_vision:
            argv.append("--vision")
        timeout = 1_800 if include_vision else 120

    environment = {
        name: os.environ[name] for name in _SAFE_ENVIRONMENT if name in os.environ
    }
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            cwd=package_root(),
            env=environment,
            stdin=subprocess.DEVNULL,
            shell=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(
            ok=False,
            text=f"Ghost {mode} stopped after its {timeout}-second deadline.",
            exit_code=124,
        )
    except OSError as error:
        message = _bounded_text(error.strerror or "could not start embedded Ghost")
        return CommandResult(ok=False, text=f"Ghost {mode} failed: {message}", exit_code=3)

    stdout = _decode(completed.stdout).strip()
    stderr = _decode(completed.stderr).strip()
    text = stdout
    if stderr:
        text = f"{text}\n{stderr}" if text else stderr
    if not text:
        text = "Ghost command completed." if completed.returncode == 0 else "Ghost command failed."
    return CommandResult(
        ok=completed.returncode == 0,
        text=_bounded_text(text),
        exit_code=completed.returncode,
    )


def setup_handler(args: str, ctx) -> object:
    try:
        include_vision = parse_setup_argument(args)
    except ValueError as error:
        return _send_result(
            ctx,
            "setup",
            CommandResult(ok=False, text=str(error), exit_code=1),
        )
    return _send_result(
        ctx,
        "setup",
        run_ghost_command("setup", include_vision=include_vision),
    )


def doctor_handler(args: str, ctx) -> object:
    if args.strip():
        result = CommandResult(
            ok=False,
            text="/ghost-doctor does not accept arguments",
            exit_code=1,
        )
    else:
        result = run_ghost_command("doctor")
    return _send_result(ctx, "doctor", result)


def _send_result(ctx, operation: str, result: CommandResult) -> object:
    return ctx.send_message(
        {
            "customType": "ghost-mcp-status",
            "content": result.text,
            "display": True,
            "details": {
                "operation": operation,
                "ok": result.ok,
                "exitCode": result.exit_code,
            },
        }
    )


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _bounded_text(value: str) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= MAX_OUTPUT_BYTES:
        return value
    suffix = "\n… output truncated"
    budget = MAX_OUTPUT_BYTES - len(suffix.encode("utf-8"))
    return encoded[:budget].decode("utf-8", errors="ignore") + suffix


__all__ = [
    "CommandResult",
    "doctor_handler",
    "parse_setup_argument",
    "run_ghost_command",
    "setup_handler",
]
