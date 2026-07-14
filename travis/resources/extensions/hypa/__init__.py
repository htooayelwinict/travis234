"""Optional Hypa integration implemented entirely through Travis234 extension hooks."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
from typing import Any, Mapping

from travis.coding_agent.extensions import ExtensionRunner, is_tool_call_event_type


_VALID_OUTCOMES = frozenset({"Rewritten", "GenericWrapper", "Passthrough", "Deny", "Ask"})
_REPLACED_BUILTINS = frozenset({"bash", "read", "grep", "find", "ls"})


@dataclass(frozen=True)
class HypaConfig:
    enabled: bool
    mode: str
    binary: str
    rewrite_timeout_ms: int
    ask_non_interactive: str
    mcp_proxy_enabled: bool
    mcp_proxy_timeout_ms: int
    mcp_config_path: Path | None
    config_path: Path | None


def _boolean(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _positive_integer(value: object, fallback: int) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _mode(value: object) -> str:
    return "replace" if str(value or "").strip().lower() == "replace" else "additive"


def _ask_policy(value: object) -> str:
    return "allow" if str(value or "").strip().lower() == "allow" else "deny"


def _config_path(env: Mapping[str, str]) -> Path | None:
    configured = env.get("TRAVIS234_HYPA_CONFIG")
    if configured is not None:
        stripped = configured.strip()
        if not stripped or stripped.lower() == "none":
            return None
        return Path(stripped).expanduser()
    return Path.home() / ".travis234" / "hypa.json"


def _load_config_file(path: Path | None) -> dict[str, object]:
    if path is None or not path.exists():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Failed to parse config file {path}: {error}") from error
    return dict(parsed) if isinstance(parsed, dict) else {}


def load_config(env: Mapping[str, str] | None = None) -> HypaConfig:
    values = dict(os.environ if env is None else env)
    path = _config_path(values)
    file_config = _load_config_file(path)
    binary = values.get("HYPA_BIN", "").strip() or str(file_config.get("binary") or "hypa")
    enabled_value = values.get("TRAVIS234_HYPA_ENABLED")
    enabled = (
        _boolean(enabled_value)
        if enabled_value is not None
        else _boolean(file_config.get("enabled"), default=shutil.which(binary) is not None)
    )
    raw_mcp_path = values.get("TRAVIS234_HYPA_MCP_CONFIG") or file_config.get("mcpConfigPath")
    return HypaConfig(
        enabled=enabled,
        mode=_mode(values.get("TRAVIS234_HYPA_MODE", file_config.get("mode"))),
        binary=binary,
        rewrite_timeout_ms=_positive_integer(
            values.get("TRAVIS234_HYPA_REWRITE_TIMEOUT_MS", file_config.get("rewriteTimeoutMs")),
            5000,
        ),
        ask_non_interactive=_ask_policy(
            values.get("TRAVIS234_HYPA_ASK_NON_INTERACTIVE", file_config.get("askNonInteractive"))
        ),
        mcp_proxy_enabled=_boolean(
            values.get("TRAVIS234_HYPA_ENABLE_MCP_PROXY", file_config.get("mcpProxyEnabled"))
        ),
        mcp_proxy_timeout_ms=_positive_integer(
            values.get("TRAVIS234_HYPA_MCP_PROXY_TIMEOUT_MS", file_config.get("mcpProxyTimeoutMs")),
            10000,
        ),
        mcp_config_path=Path(str(raw_mcp_path)).expanduser() if raw_mcp_path else None,
        config_path=path,
    )


def is_hypa_command(command: str) -> bool:
    stripped = command.lstrip()
    return stripped == "hypa" or stripped.startswith("hypa ")


def parse_rewrite_json(stdout: str) -> dict[str, object]:
    try:
        payload = json.loads(stdout.strip())
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid rewrite JSON: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError("rewrite result must be an object")
    if not isinstance(payload.get("input"), str):
        raise ValueError("rewrite result missing string field: input")
    outcome = payload.get("outcome")
    if not isinstance(outcome, str) or outcome not in _VALID_OUTCOMES:
        raise ValueError(f"rewrite result has unknown outcome: {outcome}")
    if not isinstance(payload.get("command"), str):
        raise ValueError("rewrite result missing string field: command")
    return payload


def map_rewrite_result(result: Mapping[str, object]) -> dict[str, object]:
    outcome = str(result["outcome"])
    input_text = str(result["input"])
    command = str(result["command"])
    if outcome in {"Rewritten", "GenericWrapper"}:
        return {
            "kind": "rewritten",
            "outcome": outcome,
            "input": input_text,
            "command": command,
        }
    if outcome == "Passthrough":
        return {"kind": "passthrough", "outcome": outcome, "input": input_text, "command": command}
    if outcome == "Deny":
        return {
            "kind": "deny",
            "input": input_text,
            "command": command,
            "reason": f"Command blocked by Hypa policy: {input_text}",
        }
    return {
        "kind": "ask",
        "input": input_text,
        "command": command,
        "reason": f"Hypa requests confirmation before running: {command or input_text}",
    }


def apply_replace_mode_filter(tools: list[str], mode: str) -> list[str]:
    if mode != "replace":
        return list(tools)
    return [name for name in tools if name not in _REPLACED_BUILTINS]


def rewrite_command(runner: ExtensionRunner, config: HypaConfig, command: str) -> dict[str, object]:
    if is_hypa_command(command):
        return {"kind": "skipped", "input": command, "reason": "command already starts with hypa"}
    try:
        result = runner.exec(
            config.binary,
            ["rewrite", "--json", command],
            {"timeout": config.rewrite_timeout_ms},
        )
        if result.get("killed") is True:
            return {
                "kind": "error",
                "input": command,
                "error": f"hypa rewrite timed out after {config.rewrite_timeout_ms}ms",
            }
        stdout = str(result.get("stdout") or "")
        if not stdout.strip():
            detail = str(result.get("stderr") or "").strip() or f"exit code {result.get('code')}"
            return {
                "kind": "error",
                "input": command,
                "error": f"hypa rewrite produced no JSON ({detail})",
            }
        return map_rewrite_result(parse_rewrite_json(stdout))
    except Exception as error:  # noqa: BLE001 - rewrite failures intentionally preserve the original command.
        return {"kind": "error", "input": command, "error": str(error)}


def _diagnostic_text(config: HypaConfig, last_rewrite: Mapping[str, object] | None) -> str:
    if last_rewrite is None:
        status = "none"
    elif last_rewrite.get("kind") == "error":
        status = f"error: {last_rewrite.get('error')}"
    else:
        status = str(last_rewrite.get("kind"))
    return "\n".join(
        (
            "Hypa extension",
            f"Mode: {config.mode}",
            f"Binary: {config.binary}",
            f"Rewrite timeout: {config.rewrite_timeout_ms}ms",
            f"Ask fallback (non-UI): {config.ask_non_interactive}",
            f"MCP proxy discovery: {'enabled' if config.mcp_proxy_enabled else 'disabled'}",
            f"Last rewrite: {status}",
        )
    )


def install_hypa_extension(
    runner: ExtensionRunner,
    config: HypaConfig | None = None,
) -> dict[str, object]:
    resolved = config or load_config()
    state: dict[str, object] = {"config": resolved, "last_rewrite": None}
    if not resolved.enabled:
        return state

    if resolved.mode == "replace":
        runner.on(
            "before_agent_start",
            lambda _event: runner.set_active_tools(
                apply_replace_mode_filter(runner.get_active_tools(), resolved.mode)
            ),
        )

    def intercept(event: dict[str, Any], context) -> dict[str, object] | None:
        if not is_tool_call_event_type("bash", event):
            return None
        payload = event.get("input")
        if not isinstance(payload, dict) or not isinstance(payload.get("command"), str):
            return None
        status = rewrite_command(runner, resolved, payload["command"])
        state["last_rewrite"] = status
        kind = status.get("kind")
        if kind == "rewritten":
            payload["command"] = str(status["command"])
            return None
        if kind == "deny":
            return {"block": True, "reason": str(status["reason"])}
        if kind != "ask":
            return None
        ui = context.ui if context.has_ui else None
        confirm = getattr(ui, "confirm", None)
        if callable(confirm) and bool(confirm("Hypa confirmation", str(status["reason"]))):
            payload["command"] = str(status["command"])
            return None
        if not context.has_ui and resolved.ask_non_interactive == "allow":
            payload["command"] = str(status["command"])
            return None
        reason = str(status["reason"])
        if not context.has_ui:
            reason += (
                " Non-interactive fallback is deny "
                "(set TRAVIS234_HYPA_ASK_NON_INTERACTIVE=allow to allow)."
            )
        return {"block": True, "reason": reason}

    runner.on("tool_call", intercept)

    def diagnostics(_args: str, context) -> object:
        text = _diagnostic_text(resolved, state.get("last_rewrite"))
        send = getattr(context, "send_message", None)
        if callable(send):
            return send(
                {"customType": "hypa", "content": text, "display": True},
                {"transient": True},
            )
        return []

    runner.register_command(
        "hypa",
        {"description": "Show local context-runtime diagnostics", "handler": diagnostics},
    )
    return state


def extension(runner: ExtensionRunner) -> dict[str, object]:
    """Travis234 extension entry point loaded from a user or project extension directory."""
    config = load_config()
    if config.enabled:
        from .hypa_tools import register_hypa_tools

        register_hypa_tools(runner, config)
    return install_hypa_extension(runner, config)


__all__ = [
    "HypaConfig",
    "apply_replace_mode_filter",
    "install_hypa_extension",
    "is_hypa_command",
    "load_config",
    "map_rewrite_result",
    "parse_rewrite_json",
    "rewrite_command",
    "extension",
]
