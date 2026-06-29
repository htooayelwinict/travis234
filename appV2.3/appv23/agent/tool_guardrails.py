"""Hermes-style pure tool-call loop guardrail primitives."""

from __future__ import annotations

import hashlib
import json
import posixpath
import shlex
from dataclasses import dataclass, field
from typing import Any, Mapping

IDEMPOTENT_TOOL_NAMES = frozenset(
    {
        "read",
        "grep",
        "find",
        "ls",
        "bash",
        "read_file",
        "search_files",
        "web_search",
        "web_extract",
        "session_search",
        "browser_snapshot",
        "browser_console",
        "browser_get_images",
        "mcp_filesystem_read_file",
        "mcp_filesystem_read_text_file",
        "mcp_filesystem_read_multiple_files",
        "mcp_filesystem_list_directory",
        "mcp_filesystem_list_directory_with_sizes",
        "mcp_filesystem_directory_tree",
        "mcp_filesystem_get_file_info",
        "mcp_filesystem_search_files",
    }
)

MUTATING_TOOL_NAMES = frozenset(
    {
        "edit",
        "write",
        "terminal",
        "execute_code",
        "write_file",
        "patch",
        "todo",
        "memory",
        "skill_manage",
        "browser_click",
        "browser_type",
        "browser_press",
        "browser_scroll",
        "browser_navigate",
        "send_message",
        "cronjob",
        "delegate_task",
        "process",
    }
)

FILE_MUTATING_TOOL_NAMES = frozenset({"edit", "write", "write_file", "patch"})
FILE_MUTATION_PATH_ARG_NAMES = ("path", "file_path", "filename")
FILE_OBSERVING_TOOL_NAMES = frozenset(
    {
        "read",
        "read_file",
        "mcp_filesystem_read_file",
        "mcp_filesystem_read_text_file",
    }
)
RECOVERABLE_BLOCK_CODES = frozenset()

DEFAULT_NO_PROGRESS_BLOCK_TOOL_NAMES = frozenset(
    {
        "read",
        "grep",
        "find",
        "ls",
        "bash",
        "read_file",
        "search_files",
        "mcp_filesystem_read_file",
        "mcp_filesystem_read_text_file",
        "mcp_filesystem_read_multiple_files",
        "mcp_filesystem_list_directory",
        "mcp_filesystem_list_directory_with_sizes",
        "mcp_filesystem_directory_tree",
        "mcp_filesystem_get_file_info",
        "mcp_filesystem_search_files",
    }
)
DEFAULT_READ_STYLE_NO_PROGRESS_BLOCK_AFTER = 3
DEFAULT_READ_STYLE_EXACT_FAILURE_BLOCK_AFTER = 4
_BASH_FILE_PREVIEW_COMMANDS = frozenset({"awk", "cat", "head", "sed", "tail"})
_BASH_INVENTORY_COMMANDS = frozenset({"find", "ls", "rg"})
_BASH_READ_ONLY_COMMANDS = _BASH_FILE_PREVIEW_COMMANDS | _BASH_INVENTORY_COMMANDS | frozenset({"grep"})
_BASH_MUTATION_MARKERS = (
    " rm ",
    " mv ",
    " cp ",
    " mkdir ",
    " rmdir ",
    " touch ",
    " chmod ",
    " chown ",
    " tee ",
)


@dataclass(frozen=True)
class ToolCallGuardrailConfig:
    """Thresholds for per-turn tool-call loop detection."""

    warnings_enabled: bool = True
    hard_stop_enabled: bool = False
    exact_failure_warn_after: int = 2
    exact_failure_block_after: int = 5
    same_tool_failure_warn_after: int = 3
    same_tool_failure_halt_after: int = 8
    no_progress_warn_after: int = 2
    no_progress_block_after: int = 5
    consecutive_no_progress_warn_after: int = 3
    consecutive_no_progress_block_after: int = 4
    idempotent_tools: frozenset[str] = field(default_factory=lambda: IDEMPOTENT_TOOL_NAMES)
    mutating_tools: frozenset[str] = field(default_factory=lambda: MUTATING_TOOL_NAMES)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "ToolCallGuardrailConfig":
        if not isinstance(data, Mapping):
            return cls()

        warn_after = data.get("warn_after")
        if not isinstance(warn_after, Mapping):
            warn_after = {}
        hard_stop_after = data.get("hard_stop_after")
        if not isinstance(hard_stop_after, Mapping):
            hard_stop_after = {}

        defaults = cls()
        return cls(
            warnings_enabled=_as_bool(data.get("warnings_enabled"), defaults.warnings_enabled),
            hard_stop_enabled=_as_bool(data.get("hard_stop_enabled"), defaults.hard_stop_enabled),
            exact_failure_warn_after=_positive_int(
                warn_after.get("exact_failure", data.get("exact_failure_warn_after")),
                defaults.exact_failure_warn_after,
            ),
            same_tool_failure_warn_after=_positive_int(
                warn_after.get("same_tool_failure", data.get("same_tool_failure_warn_after")),
                defaults.same_tool_failure_warn_after,
            ),
            no_progress_warn_after=_positive_int(
                warn_after.get("idempotent_no_progress", data.get("no_progress_warn_after")),
                defaults.no_progress_warn_after,
            ),
            consecutive_no_progress_warn_after=_positive_int(
                warn_after.get("idempotent_consecutive", data.get("consecutive_no_progress_warn_after")),
                defaults.consecutive_no_progress_warn_after,
            ),
            exact_failure_block_after=_positive_int(
                hard_stop_after.get("exact_failure", data.get("exact_failure_block_after")),
                defaults.exact_failure_block_after,
            ),
            same_tool_failure_halt_after=_positive_int(
                hard_stop_after.get("same_tool_failure", data.get("same_tool_failure_halt_after")),
                defaults.same_tool_failure_halt_after,
            ),
            no_progress_block_after=_positive_int(
                hard_stop_after.get("idempotent_no_progress", data.get("no_progress_block_after")),
                defaults.no_progress_block_after,
            ),
            consecutive_no_progress_block_after=_positive_int(
                hard_stop_after.get("idempotent_consecutive", data.get("consecutive_no_progress_block_after")),
                defaults.consecutive_no_progress_block_after,
            ),
        )


@dataclass(frozen=True)
class ToolCallSignature:
    """Stable, non-reversible identity for a tool name plus canonical args."""

    tool_name: str
    args_hash: str

    @classmethod
    def from_call(cls, tool_name: str, args: Mapping[str, Any] | None) -> "ToolCallSignature":
        canonical = canonical_tool_args(args or {})
        return cls(tool_name=tool_name, args_hash=_sha256(canonical))

    def to_metadata(self) -> dict[str, str]:
        return {"tool_name": self.tool_name, "args_hash": self.args_hash}


@dataclass(frozen=True)
class ToolGuardrailDecision:
    """Decision returned by the tool-call guardrail controller."""

    action: str = "allow"
    code: str = "allow"
    message: str = ""
    tool_name: str = ""
    count: int = 0
    signature: ToolCallSignature | None = None

    @property
    def allows_execution(self) -> bool:
        return self.action in {"allow", "warn"}

    @property
    def should_halt(self) -> bool:
        if self.action == "halt":
            return True
        if self.action == "block":
            return self.code not in RECOVERABLE_BLOCK_CODES
        return False

    def to_metadata(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "action": self.action,
            "code": self.code,
            "message": self.message,
            "tool_name": self.tool_name,
            "count": self.count,
        }
        if self.signature is not None:
            data["signature"] = self.signature.to_metadata()
        return data


def canonical_tool_args(args: Mapping[str, Any]) -> str:
    if not isinstance(args, Mapping):
        raise TypeError(f"tool args must be a mapping, got {type(args).__name__}")
    return json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def classify_tool_failure(tool_name: str, result: str | None) -> tuple[bool, str]:
    if result is None:
        return False, ""
    if tool_name == "bash":
        if "Command exited with code " in result or "Command timed out after " in result:
            return True, " [error]"
        return False, ""
    lower = result.lstrip()[:500].lower()
    if lower.startswith(
        (
            "error:",
            "error\n",
            "file not found:",
            "permission denied:",
            "permissionerror:",
            "filenotfounderror:",
            "operation aborted",
        )
    ):
        return True, " [error]"
    return False, ""


class ToolCallGuardrailController:
    """Per-turn controller for repeated failed/non-progressing tool calls."""

    def __init__(self, config: ToolCallGuardrailConfig | None = None, cwd: str | None = None):
        self.config = config or ToolCallGuardrailConfig()
        self.cwd = _normalize_shell_path(cwd.replace("\\", "/")) if cwd else None
        self.reset_for_turn()

    def reset_for_turn(self) -> None:
        self._exact_failure_counts: dict[ToolCallSignature, int] = {}
        self._same_tool_failure_counts: dict[str, int] = {}
        self._no_progress: dict[ToolCallSignature, tuple[str, int]] = {}
        self._consecutive_signature: ToolCallSignature | None = None
        self._consecutive_result_hash: str | None = None
        self._consecutive_count = 0
        self._landed_file_mutations: dict[str, str] = {}
        self._landed_file_mutation_counts: dict[str, int] = {}
        self._halt_decision: ToolGuardrailDecision | None = None

    @property
    def halt_decision(self) -> ToolGuardrailDecision | None:
        return self._halt_decision

    def before_call(self, tool_name: str, args: Mapping[str, Any] | None) -> ToolGuardrailDecision:
        args = _coerce_args(args)
        signature = ToolCallSignature.from_call(tool_name, args)
        if (
            self._is_idempotent(tool_name)
            and self._consecutive_signature == signature
            and self._consecutive_count >= self.config.consecutive_no_progress_block_after - 1
        ):
            count = self._consecutive_count + 1
            decision = ToolGuardrailDecision(
                action="block",
                code="idempotent_consecutive_block",
                message=(
                    f"BLOCKED: {tool_name} has been called with the same arguments "
                    f"{count} times in a row and the prior results did not change. "
                    "You already have this information. STOP repeating this tool call "
                    "and proceed with the task using the result already provided."
                ),
                tool_name=tool_name,
                count=count,
                signature=signature,
            )
            self._halt_decision = decision
            return decision

        if not self.config.hard_stop_enabled:
            return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

        exact_count = self._exact_failure_counts.get(signature, 0)
        if exact_count >= self.config.exact_failure_block_after:
            decision = ToolGuardrailDecision(
                action="block",
                code="repeated_exact_failure_block",
                message=(
                    f"Blocked {tool_name}: the same tool call failed {exact_count} "
                    "times with identical arguments. Stop retrying it unchanged; "
                    "change strategy or explain the blocker."
                ),
                tool_name=tool_name,
                count=exact_count,
                signature=signature,
            )
            self._halt_decision = decision
            return decision

        if self._is_idempotent(tool_name):
            progress_signature = _no_progress_signature(tool_name, _coerce_args(args), self.cwd) or signature
            record = self._no_progress.get(progress_signature)
            if record is not None:
                _result_hash, repeat_count = record
                if repeat_count >= self.config.no_progress_block_after:
                    decision = ToolGuardrailDecision(
                        action="block",
                        code="idempotent_no_progress_block",
                        message=_no_progress_recovery_message(tool_name, repeat_count, blocked=True),
                        tool_name=tool_name,
                        count=repeat_count,
                        signature=progress_signature,
                    )
                    self._halt_decision = decision
                    return decision

        return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

    def after_call(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None,
        result: str | None,
        *,
        failed: bool | None = None,
    ) -> ToolGuardrailDecision:
        args = _coerce_args(args)
        signature = ToolCallSignature.from_call(tool_name, args)
        if failed is None:
            failed, _ = classify_tool_failure(tool_name, result)

        if failed:
            self._reset_consecutive()
            exact_count = self._exact_failure_counts.get(signature, 0) + 1
            self._exact_failure_counts[signature] = exact_count
            self._forget_no_progress(tool_name, args, signature)

            same_count = self._same_tool_failure_counts.get(tool_name, 0) + 1
            self._same_tool_failure_counts[tool_name] = same_count

            exact_failure_block_after = self.config.exact_failure_block_after
            if tool_name in DEFAULT_NO_PROGRESS_BLOCK_TOOL_NAMES and not self.config.hard_stop_enabled:
                exact_failure_block_after = min(
                    exact_failure_block_after,
                    DEFAULT_READ_STYLE_EXACT_FAILURE_BLOCK_AFTER,
                )

            if exact_count >= exact_failure_block_after and (
                self.config.hard_stop_enabled or tool_name in DEFAULT_NO_PROGRESS_BLOCK_TOOL_NAMES
            ):
                decision = ToolGuardrailDecision(
                    action="halt",
                    code="repeated_exact_failure_block",
                    message=(
                        f"BLOCKED: {tool_name} failed with identical arguments {exact_count} times. "
                        "The failure has NOT changed. STOP repeating this tool call and change "
                        "strategy or explain the blocker."
                    ),
                    tool_name=tool_name,
                    count=exact_count,
                    signature=signature,
                )
                self._halt_decision = decision
                return decision

            if self.config.hard_stop_enabled and same_count >= self.config.same_tool_failure_halt_after:
                decision = ToolGuardrailDecision(
                    action="halt",
                    code="same_tool_failure_halt",
                    message=(
                        f"Stopped {tool_name}: it failed {same_count} times this turn. "
                        "Stop retrying the same failing tool path and choose a different approach."
                    ),
                    tool_name=tool_name,
                    count=same_count,
                    signature=signature,
                )
                self._halt_decision = decision
                return decision

            if self.config.warnings_enabled and exact_count >= self.config.exact_failure_warn_after:
                return ToolGuardrailDecision(
                    action="warn",
                    code="repeated_exact_failure_warning",
                    message=(
                        f"{tool_name} has failed {exact_count} times with identical arguments. "
                        "This looks like a loop; inspect the error and change strategy "
                        "instead of retrying it unchanged."
                    ),
                    tool_name=tool_name,
                    count=exact_count,
                    signature=signature,
                )

            if self.config.warnings_enabled and same_count >= self.config.same_tool_failure_warn_after:
                return ToolGuardrailDecision(
                    action="warn",
                    code="same_tool_failure_warning",
                    message=_tool_failure_recovery_hint(tool_name, same_count),
                    tool_name=tool_name,
                    count=same_count,
                    signature=signature,
                )

            return ToolGuardrailDecision(tool_name=tool_name, count=exact_count, signature=signature)

        self._exact_failure_counts.pop(signature, None)
        self._same_tool_failure_counts.pop(tool_name, None)

        observed_path = _file_observation_path_key(tool_name, args, self.cwd)
        if observed_path is not None:
            self._landed_file_mutations.pop(observed_path, None)
            self._landed_file_mutation_counts.pop(observed_path, None)

        mutation_path = _file_mutation_path_key(tool_name, args, self.cwd)
        if mutation_path is not None:
            display_path = _display_file_mutation_path(tool_name, args)
            mutation_count = self._landed_file_mutation_counts.get(mutation_path, 0) + 1
            self._landed_file_mutation_counts[mutation_path] = mutation_count
            self._landed_file_mutations[mutation_path] = display_path
            if mutation_count > 1:
                return ToolGuardrailDecision(
                    action="warn",
                    code="repeated_file_mutation_warning",
                    message=(
                        f"{tool_name} changed {display_path} {mutation_count} times in this turn. "
                        "If this was intentional, continue with the current file state. "
                        "If not, inspect the latest file content before making another mutation."
                    ),
                    tool_name=tool_name,
                    count=mutation_count,
                    signature=signature,
                )

        if not self._is_idempotent(tool_name):
            self._forget_no_progress(tool_name, args, signature)
            self._reset_consecutive()
            return ToolGuardrailDecision(tool_name=tool_name, signature=signature)

        result_hash = _result_hash(result)
        if self._consecutive_signature == signature and self._consecutive_result_hash == result_hash:
            self._consecutive_count += 1
        else:
            self._consecutive_signature = signature
            self._consecutive_result_hash = result_hash
            self._consecutive_count = 1

        progress_signature = _no_progress_signature(tool_name, args, self.cwd) or signature
        previous = self._no_progress.get(progress_signature)
        repeat_count = 1
        if previous is not None and previous[0] == result_hash:
            repeat_count = previous[1] + 1
        self._no_progress[progress_signature] = (result_hash, repeat_count)

        no_progress_block_after = self.config.no_progress_block_after
        if tool_name in DEFAULT_NO_PROGRESS_BLOCK_TOOL_NAMES and not self.config.hard_stop_enabled:
            no_progress_block_after = min(no_progress_block_after, DEFAULT_READ_STYLE_NO_PROGRESS_BLOCK_AFTER)

        if repeat_count >= no_progress_block_after and (
            self.config.hard_stop_enabled or tool_name in DEFAULT_NO_PROGRESS_BLOCK_TOOL_NAMES
        ):
            decision = ToolGuardrailDecision(
                action="halt",
                code="idempotent_no_progress_block",
                message=_no_progress_recovery_message(tool_name, repeat_count, blocked=True),
                tool_name=tool_name,
                count=repeat_count,
                signature=progress_signature,
            )
            self._halt_decision = decision
            return decision

        if self.config.warnings_enabled and self._consecutive_count >= self.config.consecutive_no_progress_warn_after:
            return ToolGuardrailDecision(
                action="warn",
                code="idempotent_consecutive_warning",
                message=_no_progress_recovery_message(tool_name, self._consecutive_count, blocked=False),
                tool_name=tool_name,
                count=self._consecutive_count,
                signature=signature,
            )

        if self.config.warnings_enabled and repeat_count >= self.config.no_progress_warn_after:
            return ToolGuardrailDecision(
                action="warn",
                code="idempotent_no_progress_warning",
                message=_no_progress_recovery_message(tool_name, repeat_count, blocked=False),
                tool_name=tool_name,
                count=repeat_count,
                signature=progress_signature,
            )

        return ToolGuardrailDecision(tool_name=tool_name, count=repeat_count, signature=signature)

    def _is_idempotent(self, tool_name: str) -> bool:
        if tool_name in self.config.mutating_tools:
            return False
        return tool_name in self.config.idempotent_tools

    def _reset_consecutive(self) -> None:
        self._consecutive_signature = None
        self._consecutive_result_hash = None
        self._consecutive_count = 0

    def _forget_no_progress(self, tool_name: str, args: Mapping[str, Any], signature: ToolCallSignature) -> None:
        self._no_progress.pop(signature, None)
        progress_signature = _no_progress_signature(tool_name, args, self.cwd)
        if progress_signature is not None:
            self._no_progress.pop(progress_signature, None)


def _file_mutation_path_key(tool_name: str, args: Mapping[str, Any], cwd: str | None = None) -> str | None:
    if tool_name not in FILE_MUTATING_TOOL_NAMES:
        return None
    path = _file_mutation_arg_path(args)
    if path is None:
        return None
    return _canonical_shell_path(path, cwd)


def _file_observation_path_key(tool_name: str, args: Mapping[str, Any], cwd: str | None = None) -> str | None:
    if tool_name not in FILE_OBSERVING_TOOL_NAMES:
        return None
    path = _file_mutation_arg_path(args)
    if path is None:
        return None
    return _canonical_shell_path(path, cwd)


def _file_mutation_arg_path(args: Mapping[str, Any]) -> str | None:
    for name in FILE_MUTATION_PATH_ARG_NAMES:
        value = args.get(name)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _display_file_mutation_path(tool_name: str, args: Mapping[str, Any]) -> str:
    return _file_mutation_arg_path(args) or tool_name


def toolguard_synthetic_result(decision: ToolGuardrailDecision) -> str:
    return json.dumps({"error": decision.message, "guardrail": decision.to_metadata()}, ensure_ascii=False)


def append_toolguard_guidance(result: str, decision: ToolGuardrailDecision) -> str:
    if decision.action not in {"warn", "halt"} or not decision.message:
        return result
    label = "Tool loop hard stop" if decision.action == "halt" else "Tool loop warning"
    return (result or "") + f"\n\n[{label}: {decision.code}; count={decision.count}; {decision.message}]"


def _tool_failure_recovery_hint(tool_name: str, count: int) -> str:
    common = (
        f"{tool_name} has failed {count} times this turn. This looks like a loop. "
        "Do not switch to text-only replies; keep using tools, but diagnose before retrying. "
        "First inspect the latest error/output and verify your assumptions. "
    )
    if tool_name in {"terminal", "bash"}:
        return common + (
            "For terminal failures, run a small diagnostic such as `pwd && ls -la` "
            "in the same tool, then try an absolute path, a simpler command, a different "
            "working directory, or a different tool such as read/write/edit."
        )
    return common + (
        "Try different arguments, a narrower query/path, an absolute path when relevant, "
        "or a different tool that can make progress. If the blocker is external, report "
        "the blocker after one diagnostic attempt instead of repeating the same failing path."
    )


def _no_progress_recovery_message(tool_name: str, count: int, *, blocked: bool) -> str:
    prefix = f"BLOCKED: {tool_name} returned the same result {count} times. " if blocked else (
        f"{tool_name} returned the same result {count} times. "
    )
    common = (
        prefix
        + "The result has NOT changed. You already have this information. "
        + "STOP repeating this tool call. Use the result already provided and proceed with the task. "
    )
    if tool_name == "bash":
        return common + (
            "Do not call the same bash command or equivalent directory/search/file-preview command again. "
            "For codebase scans, treat listings/search output as inventory, choose relevant paths from it, "
            "then use read with path/offset/limit for file contents. If the inventory is insufficient, "
            "change the path/glob once or explain the blocker without another same bash command."
        )
    return common + "Use a different query/path only if the existing result is insufficient."


def _coerce_args(args: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return args if isinstance(args, Mapping) else {}


def _no_progress_signature(tool_name: str, args: Mapping[str, Any], cwd: str | None = None) -> ToolCallSignature | None:
    if tool_name != "bash":
        return None
    command = args.get("command")
    if not isinstance(command, str):
        return None
    semantic_key = _semantic_bash_read_key(command, cwd) or _bash_effective_args_key(args)
    if semantic_key is None:
        return None
    return ToolCallSignature(tool_name=tool_name, args_hash=_sha256(semantic_key))


def _bash_effective_args_key(args: Mapping[str, Any]) -> str | None:
    command = args.get("command")
    if not isinstance(command, str):
        return None
    effective: dict[str, Any] = {"kind": "bash_effective", "command": command}
    if args.get("timeout") is not None:
        effective["timeout"] = args.get("timeout")
    return json.dumps(effective, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _semantic_bash_read_key(command: str, cwd: str | None = None) -> str | None:
    stripped = command.strip()
    if not stripped:
        return None
    lowered = f" {stripped.lower()} "
    if any(marker in lowered for marker in _BASH_MUTATION_MARKERS):
        return None
    try:
        tokens = shlex.split(stripped)
    except ValueError:
        return None
    if not tokens:
        return None
    redirections = [index for index, token in enumerate(tokens) if _is_redirection_token(token)]
    if any(index < len(tokens) - 1 and tokens[index + 1] not in {"|", "||", "&&", ";"} for index in redirections):
        return None
    command_index, command_name = _first_bash_read_only_command(tokens)
    if command_name is None:
        return None
    base_cwd = _effective_shell_cwd(tokens, command_index, cwd)
    if command_name in _BASH_FILE_PREVIEW_COMMANDS:
        path = _first_path_like_token(tokens)
        if not path:
            return None
        return json.dumps(
            {"kind": "bash_file_preview", "path": _canonical_shell_path(path, base_cwd)},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    if command_name in _BASH_INVENTORY_COMMANDS:
        root = _bash_inventory_root(command_name, tokens, command_index, base_cwd)
        if root is None:
            return None
        return json.dumps(
            {"kind": "bash_inventory", "path": root},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    return None


def _basename(value: str) -> str:
    return value.rsplit("/", 1)[-1]


def _first_bash_read_only_command(tokens: list[str]) -> tuple[int, str | None]:
    for index, token in enumerate(tokens):
        name = _basename(token)
        if name in _BASH_READ_ONLY_COMMANDS:
            return index, name
    return -1, None


def _bash_inventory_root(
    command_name: str,
    tokens: list[str],
    command_index: int,
    cwd: str | None = None,
) -> str | None:
    segment = _command_segment(tokens, command_index + 1)
    if command_name == "find":
        root = _first_find_root(segment)
    elif command_name == "ls":
        root = _first_ls_root(segment)
    elif command_name == "rg":
        root = _first_rg_files_root(segment)
    else:
        return None
    return _canonical_shell_path(root or ".", cwd)


def _command_segment(tokens: list[str], start_index: int) -> list[str]:
    segment: list[str] = []
    for token in tokens[start_index:]:
        if token in {"|", "||", "&&", ";"}:
            break
        segment.append(token)
    return segment


def _first_find_root(segment: list[str]) -> str:
    for token in segment:
        if _is_redirection_token(token):
            continue
        if token.startswith("-"):
            continue
        return token
    return "."


def _first_ls_root(segment: list[str]) -> str:
    skip_next = False
    for token in segment:
        if skip_next:
            skip_next = False
            continue
        if token in {"--color", "--sort", "--time-style", "--format", "--indicator-style", "--quoting-style"}:
            skip_next = True
            continue
        if _is_redirection_token(token):
            continue
        if token.startswith("-"):
            continue
        return token
    return "."


def _first_rg_files_root(segment: list[str]) -> str | None:
    if "--files" not in segment:
        return None
    skip_next = False
    for token in segment:
        if skip_next:
            skip_next = False
            continue
        if token == "--files":
            continue
        if token in {"-g", "--glob", "-t", "--type", "-T", "--type-not", "--ignore-file", "--max-depth"}:
            skip_next = True
            continue
        if _is_redirection_token(token):
            continue
        if token.startswith("-"):
            continue
        return token
    return "."


def _normalize_shell_path(path: str) -> str:
    normalized = posixpath.normpath(path.strip() or ".")
    return "." if normalized == "" else normalized


def _canonical_shell_path(path: str, cwd: str | None = None) -> str:
    raw = path.strip() or "."
    if raw.startswith(("~", "$")):
        return _normalize_shell_path(raw)
    base = _normalize_shell_path(cwd) if cwd else None
    if base and not posixpath.isabs(raw):
        raw = posixpath.join(base, raw)
    normalized = _normalize_shell_path(raw)
    if base and posixpath.isabs(normalized):
        relative = posixpath.relpath(normalized, base)
        if relative == ".":
            return "."
        if not relative.startswith("../") and relative != "..":
            return relative
    return normalized


def _effective_shell_cwd(tokens: list[str], command_index: int, cwd: str | None = None) -> str | None:
    current = _normalize_shell_path(cwd) if cwd else None
    index = 0
    while index < command_index:
        token = tokens[index]
        if _basename(token) == "cd" and index + 1 < command_index:
            current = _canonical_shell_path(tokens[index + 1], current)
            index += 2
            continue
        index += 1
    return current


def _first_path_like_token(tokens: list[str]) -> str | None:
    skip_next = False
    for index, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if token in {"|", "||", "&&", ";"}:
            continue
        if _is_redirection_token(token):
            continue
        name = _basename(token)
        if name in _BASH_READ_ONLY_COMMANDS:
            continue
        if token in {"-n", "--lines", "-c", "--bytes", "-m", "--max-count", "-e", "--regexp"}:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        if index > 0 and _basename(tokens[index - 1]) in {"awk", "sed"} and not _looks_like_path(token):
            continue
        if _looks_like_path(token):
            return token
    return None


def _looks_like_path(token: str) -> bool:
    return "/" in token or token.startswith(".") or "." in _basename(token)


def _is_redirection_token(token: str) -> bool:
    return token in {">", ">>", "<", "2>", "2>>", "&>", ">&", "2>&1", "1>&2"} or token.startswith(
        (">", ">>", "<", "2>", "2>>", "1>", "1>>", "&>")
    )


def _result_hash(result: str | None) -> str:
    parsed = _safe_json_loads(result or "")
    if parsed is not None:
        try:
            canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        except TypeError:
            canonical = str(parsed)
    else:
        canonical = result or ""
    return _sha256(canonical)


def _safe_json_loads(value: str) -> Any | None:
    try:
        return json.loads(value)
    except Exception:
        return None


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "enabled"}:
            return True
        if lowered in {"0", "false", "no", "off", "disabled"}:
            return False
    return default


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
