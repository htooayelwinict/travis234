"""pure tool-call loop guardrail primitives."""

from __future__ import annotations

import hashlib
import json
import posixpath
import shlex
from dataclasses import dataclass, field
from typing import Any, Mapping

from travis.coding_agent.policies.bash_classification import BashMutationClass, classify_bash_mutation
from travis.coding_agent.policies.types import Allow, Block, CodingTurnContext, PolicyDecision, ToolCallView

# Keep this aligned with ProcessSnapshot without importing the process package into policy initialization.
COOPERATIVE_PROCESS_POLL_WAIT_MS = 1000

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
WORKSPACE_SCOPE_VIOLATION_CODE = "workspace_scope_violation"
WORKSPACE_SCOPE_REPEATED_WARNING_CODE = "workspace_scope_repeated_warning"
WORKSPACE_SCOPE_REPEATED_BLOCK_CODE = "workspace_scope_repeated_block"
IDEMPOTENT_NO_PROGRESS_RECOVERY_BLOCK_CODE = "idempotent_no_progress_recovery_block"
REPEATED_EXACT_FAILURE_RECOVERY_BLOCK_CODE = "repeated_exact_failure_recovery_block"
REPEATED_EXACT_SUCCESS_RECOVERY_BLOCK_CODE = "repeated_exact_success_recovery_block"
RECOVERABLE_BLOCK_CODES = frozenset(
    {
        WORKSPACE_SCOPE_VIOLATION_CODE,
        IDEMPOTENT_NO_PROGRESS_RECOVERY_BLOCK_CODE,
        REPEATED_EXACT_FAILURE_RECOVERY_BLOCK_CODE,
        REPEATED_EXACT_SUCCESS_RECOVERY_BLOCK_CODE,
    }
)

DEFAULT_NO_PROGRESS_BLOCK_TOOL_NAMES = frozenset(
    {
        "read",
        "grep",
        "find",
        "ls",
        "bash",
        "read_file",
        "search_files",
        "process",
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
DEFAULT_ADAPTIVE_NO_PROGRESS_BLOCK_AFTER = 2
DEFAULT_ADAPTIVE_EXACT_REPEAT_BLOCK_AFTER = 2
DEFAULT_MUTATING_NO_PROGRESS_WARN_AFTER = 3
DEFAULT_MUTATING_NO_PROGRESS_HALT_AFTER = 6
_BASH_FILE_PREVIEW_COMMANDS = frozenset({"awk", "cat", "head", "sed", "tail"})
_BASH_INVENTORY_COMMANDS = frozenset({"find", "ls", "rg"})
_BASH_READ_ONLY_COMMANDS = _BASH_FILE_PREVIEW_COMMANDS | _BASH_INVENTORY_COMMANDS | frozenset({"grep"})
@dataclass(frozen=True)
class ToolCallGuardrailConfig:
    """Thresholds for per-turn tool-call loop detection."""

    guidance_enabled: bool = True
    # Warnings help the model recover inside the current turn. Hard stops are
    # an administrative circuit breaker and must be explicitly enabled.
    blocking_enabled: bool = False
    exact_failure_warn_after: int = 2
    exact_failure_block_after: int = 5
    same_tool_failure_warn_after: int = 3
    same_tool_failure_halt_after: int = 8
    no_progress_warn_after: int = 2
    no_progress_block_after: int = 5
    consecutive_no_progress_warn_after: int = 3
    consecutive_no_progress_block_after: int = 4
    mutating_no_progress_warn_after: int = DEFAULT_MUTATING_NO_PROGRESS_WARN_AFTER
    mutating_no_progress_halt_after: int = DEFAULT_MUTATING_NO_PROGRESS_HALT_AFTER
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
            guidance_enabled=_as_bool(
                data.get("guidance_enabled", data.get("warnings_enabled")),
                defaults.guidance_enabled,
            ),
            blocking_enabled=_as_bool(
                data.get("blocking_enabled", data.get("hard_stop_enabled")),
                defaults.blocking_enabled,
            ),
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
            mutating_no_progress_warn_after=_positive_int(
                warn_after.get("mutating_no_progress", data.get("mutating_no_progress_warn_after")),
                defaults.mutating_no_progress_warn_after,
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
            mutating_no_progress_halt_after=_positive_int(
                hard_stop_after.get("mutating_no_progress", data.get("mutating_no_progress_halt_after")),
                defaults.mutating_no_progress_halt_after,
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
        self._workspace_scope_violation_counts: dict[ToolCallSignature, int] = {}
        self._mutating_no_progress: dict[str, tuple[str, int]] = {}
        self._adaptive_block_counts: dict[ToolCallSignature, int] = {}
        self._adaptive_failure_block_counts: dict[ToolCallSignature, int] = {}
        self._exact_successes: dict[ToolCallSignature, tuple[str, int]] = {}
        self._adaptive_success_block_counts: dict[ToolCallSignature, int] = {}
        self._halt_decision: ToolGuardrailDecision | None = None

    @property
    def halt_decision(self) -> ToolGuardrailDecision | None:
        return self._halt_decision

    def before_call(self, tool_name: str, args: Mapping[str, Any] | None) -> ToolGuardrailDecision:
        args = _coerce_args(args)
        signature = ToolCallSignature.from_call(tool_name, args)
        if (
            self.config.blocking_enabled
            and
            self._is_idempotent(tool_name, args)
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

        if not self.config.blocking_enabled and tool_name in DEFAULT_NO_PROGRESS_BLOCK_TOOL_NAMES:
            progress_signature = _no_progress_signature(tool_name, args, self.cwd) or signature
            record = self._no_progress.get(progress_signature)
            if record is not None and record[1] >= DEFAULT_ADAPTIVE_NO_PROGRESS_BLOCK_AFTER:
                blocked_count = self._adaptive_block_counts.get(progress_signature, 0) + 1
                self._adaptive_block_counts[progress_signature] = blocked_count
                count = record[1] + blocked_count
                return ToolGuardrailDecision(
                    action="block",
                    code=IDEMPOTENT_NO_PROGRESS_RECOVERY_BLOCK_CODE,
                    message=(
                        f"BLOCKED: {tool_name} already returned this unchanged result {record[1]} times, "
                        "so the duplicate call was not executed and its content was not added again. "
                        "Use the earlier result. Make a materially different or state-changing tool call "
                        "if work remains; otherwise finish the task now without asking the user to repeat it."
                        + (
                            " The process is stuck: terminate or kill it before diagnosing and fixing the cause."
                            if tool_name == "process"
                            else ""
                        )
                    ),
                    tool_name=tool_name,
                    count=count,
                    signature=progress_signature,
                )

        if not self.config.blocking_enabled:
            exact_failure_count = self._exact_failure_counts.get(signature, 0)
            if exact_failure_count >= DEFAULT_ADAPTIVE_EXACT_REPEAT_BLOCK_AFTER:
                blocked_count = self._adaptive_failure_block_counts.get(signature, 0) + 1
                self._adaptive_failure_block_counts[signature] = blocked_count
                count = exact_failure_count + blocked_count
                return ToolGuardrailDecision(
                    action="block",
                    code=REPEATED_EXACT_FAILURE_RECOVERY_BLOCK_CODE,
                    message=(
                        f"BLOCKED: {tool_name} already failed with these exact arguments "
                        f"{exact_failure_count} times, so the duplicate call was not executed. "
                        "Use the earlier failure already in context, inspect its cause, and make a "
                        "materially different tool call while continuing this same task."
                    ),
                    tool_name=tool_name,
                    count=count,
                    signature=signature,
                )

            if tool_name == "bash" and _tool_call_may_change_state(tool_name, args):
                success_record = self._exact_successes.get(signature)
                if (
                    success_record is not None
                    and success_record[1] >= DEFAULT_ADAPTIVE_EXACT_REPEAT_BLOCK_AFTER
                ):
                    blocked_count = self._adaptive_success_block_counts.get(signature, 0) + 1
                    self._adaptive_success_block_counts[signature] = blocked_count
                    count = success_record[1] + blocked_count
                    return ToolGuardrailDecision(
                        action="block",
                        code=REPEATED_EXACT_SUCCESS_RECOVERY_BLOCK_CODE,
                        message=(
                            f"BLOCKED: bash already completed with these exact arguments and the same "
                            f"result {success_record[1]} times, so the duplicate call was not executed. "
                            "Reuse the successful result. If work remains, edit or inspect something "
                            "materially different; otherwise provide the final response now."
                        ),
                        tool_name=tool_name,
                        count=count,
                        signature=signature,
                    )

        if not self.config.blocking_enabled:
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

        if self._is_idempotent(tool_name, args):
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
            self._exact_successes.pop(signature, None)
            self._adaptive_success_block_counts.pop(signature, None)
            exact_count = self._exact_failure_counts.get(signature, 0) + 1
            self._exact_failure_counts[signature] = exact_count
            self._forget_no_progress(tool_name, args, signature)

            same_count = self._same_tool_failure_counts.get(tool_name, 0) + 1
            self._same_tool_failure_counts[tool_name] = same_count

            exact_failure_block_after = self.config.exact_failure_block_after
            if tool_name in DEFAULT_NO_PROGRESS_BLOCK_TOOL_NAMES:
                exact_failure_block_after = min(
                    exact_failure_block_after,
                    DEFAULT_READ_STYLE_EXACT_FAILURE_BLOCK_AFTER,
                )
            if self.config.blocking_enabled and exact_count >= exact_failure_block_after:
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

            if self.config.blocking_enabled and same_count >= self.config.same_tool_failure_halt_after:
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

            if self.config.guidance_enabled and exact_count >= self.config.exact_failure_warn_after:
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

            if self.config.guidance_enabled and same_count >= self.config.same_tool_failure_warn_after:
                return ToolGuardrailDecision(
                    action="warn",
                    code="same_tool_failure_warning",
                    message=_tool_failure_recovery_hint(tool_name, same_count),
                    tool_name=tool_name,
                    count=same_count,
                    signature=signature,
                )

            return ToolGuardrailDecision(tool_name=tool_name, count=exact_count, signature=signature)

        exact_success_decision: ToolGuardrailDecision | None = None
        track_exact_bash_success = tool_name == "bash" and _tool_call_may_change_state(tool_name, args)
        success_result_hash = _result_hash(result)
        previous_success = self._exact_successes.get(signature) if track_exact_bash_success else None
        success_repeat_count = (
            previous_success[1] + 1
            if previous_success is not None and previous_success[0] == success_result_hash
            else 1
        )

        if _tool_call_may_change_state(tool_name, args):
            self._exact_failure_counts.clear()
            self._adaptive_failure_block_counts.clear()
            self._same_tool_failure_counts.clear()
            self._reset_consecutive()
            self._exact_successes.clear()
            self._adaptive_success_block_counts.clear()
            if not self.config.blocking_enabled:
                # Adaptive dedup is freshness-sensitive. Any successful mutation
                # may invalidate an earlier observation, so let the model read
                # again. Strict administrative hard-stop mode intentionally keeps
                # its turn-wide evidence instead.
                self._no_progress.clear()
                self._adaptive_block_counts.clear()
        else:
            self._exact_failure_counts.pop(signature, None)
            self._adaptive_failure_block_counts.pop(signature, None)
            self._same_tool_failure_counts.pop(tool_name, None)

        if track_exact_bash_success:
            self._exact_successes[signature] = (success_result_hash, success_repeat_count)
            if (
                self.config.guidance_enabled
                and success_repeat_count >= DEFAULT_ADAPTIVE_EXACT_REPEAT_BLOCK_AFTER
            ):
                exact_success_decision = ToolGuardrailDecision(
                    action="warn",
                    code="repeated_exact_success_warning",
                    message=(
                        f"bash completed with the same exact arguments and result "
                        f"{success_repeat_count} times. Reuse that successful result and move to the "
                        "next unfinished task instead of validating it again."
                    ),
                    tool_name=tool_name,
                    count=success_repeat_count,
                    signature=signature,
                )

        observed_path = _file_observation_path_key(tool_name, args, self.cwd)
        if observed_path is not None:
            self._landed_file_mutations.pop(observed_path, None)
            self._landed_file_mutation_counts.pop(observed_path, None)
            self._mutating_no_progress.pop(observed_path, None)

        mutation_path = _file_mutation_path_key(tool_name, args, self.cwd)
        mutating_no_progress_decision: ToolGuardrailDecision | None = None
        if mutation_path is not None:
            display_path = _display_file_mutation_path(tool_name, args)
            mutation_count = self._landed_file_mutation_counts.get(mutation_path, 0) + 1
            self._landed_file_mutation_counts[mutation_path] = mutation_count
            self._landed_file_mutations[mutation_path] = display_path
            mutation_fingerprint = _sha256(f"{canonical_tool_args(args)}\n{_result_hash(result)}")
            previous_mutation = self._mutating_no_progress.get(mutation_path)
            repeat_count = (
                previous_mutation[1] + 1
                if previous_mutation is not None and previous_mutation[0] == mutation_fingerprint
                else 1
            )
            self._mutating_no_progress[mutation_path] = (mutation_fingerprint, repeat_count)
            if self.config.blocking_enabled and repeat_count >= self.config.mutating_no_progress_halt_after:
                mutating_no_progress_decision = ToolGuardrailDecision(
                    action="halt",
                    code="mutating_no_progress_halt",
                    message=(
                        f"STOP: {tool_name} successfully mutated {display_path} with identical arguments "
                        f"and result {repeat_count} times. The state is not progressing. Read the file, "
                        "change strategy, or explain the blocker instead of repeating the same mutation."
                    ),
                    tool_name=tool_name,
                    count=repeat_count,
                    signature=signature,
                )
                self._halt_decision = mutating_no_progress_decision
            elif self.config.guidance_enabled and repeat_count >= self.config.mutating_no_progress_warn_after:
                mutating_no_progress_decision = ToolGuardrailDecision(
                    action="warn",
                    code="mutating_no_progress_warning",
                    message=(
                        f"{tool_name} has successfully mutated {display_path} with identical arguments "
                        f"and result {repeat_count} times. This looks like a loop; read the file or "
                        "change strategy instead of repeating the same mutation."
                    ),
                    tool_name=tool_name,
                    count=repeat_count,
                    signature=signature,
                )

        if exact_success_decision is not None:
            return exact_success_decision

        if not self._is_idempotent(tool_name, args):
            self._forget_no_progress(tool_name, args, signature)
            self._reset_consecutive()
            return (
                mutating_no_progress_decision
                or ToolGuardrailDecision(tool_name=tool_name, signature=signature)
            )

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
        if tool_name in DEFAULT_NO_PROGRESS_BLOCK_TOOL_NAMES:
            no_progress_block_after = min(
                no_progress_block_after,
                DEFAULT_READ_STYLE_NO_PROGRESS_BLOCK_AFTER,
            )
        if self.config.blocking_enabled and repeat_count >= no_progress_block_after:
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

        if self.config.guidance_enabled and self._consecutive_count >= self.config.consecutive_no_progress_warn_after:
            return ToolGuardrailDecision(
                action="warn",
                code="idempotent_consecutive_warning",
                message=_no_progress_recovery_message(tool_name, self._consecutive_count, blocked=False),
                tool_name=tool_name,
                count=self._consecutive_count,
                signature=signature,
            )

        if self.config.guidance_enabled and repeat_count >= self.config.no_progress_warn_after:
            return ToolGuardrailDecision(
                action="warn",
                code="idempotent_no_progress_warning",
                message=_no_progress_recovery_message(tool_name, repeat_count, blocked=False),
                tool_name=tool_name,
                count=repeat_count,
                signature=progress_signature,
            )

        return ToolGuardrailDecision(tool_name=tool_name, count=repeat_count, signature=signature)

    def workspace_scope_violation_decision(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None,
        resolved_path: str,
        message: str,
    ) -> ToolGuardrailDecision:
        args = _coerce_args(args)
        signature = _workspace_scope_violation_signature(tool_name, resolved_path)
        count = self._workspace_scope_violation_counts.get(signature, 0) + 1
        self._workspace_scope_violation_counts[signature] = count

        block_after = min(self.config.exact_failure_block_after, DEFAULT_READ_STYLE_NO_PROGRESS_BLOCK_AFTER)
        if self.config.blocking_enabled and count >= block_after:
            decision = ToolGuardrailDecision(
                action="halt",
                code=WORKSPACE_SCOPE_REPEATED_BLOCK_CODE,
                message=_workspace_scope_violation_recovery_message(message, count, blocked=True),
                tool_name=tool_name,
                count=count,
                signature=signature,
            )
            self._halt_decision = decision
            return decision

        if self.config.guidance_enabled and count >= self.config.exact_failure_warn_after:
            return ToolGuardrailDecision(
                action="warn",
                code=WORKSPACE_SCOPE_REPEATED_WARNING_CODE,
                message=_workspace_scope_violation_recovery_message(message, count, blocked=False),
                tool_name=tool_name,
                count=count,
                signature=signature,
            )

        return ToolGuardrailDecision(
            action="block",
            code=WORKSPACE_SCOPE_VIOLATION_CODE,
            message=message,
            tool_name=tool_name,
            count=count,
            signature=signature,
        )

    def _is_idempotent(self, tool_name: str, args: Mapping[str, Any]) -> bool:
        if tool_name == "process":
            action = args.get("action")
            if action == "wait":
                return True
            if action == "poll":
                wait_ms = args.get("yield_time_ms", COOPERATIVE_PROCESS_POLL_WAIT_MS)
                return isinstance(wait_ms, int) and wait_ms < COOPERATIVE_PROCESS_POLL_WAIT_MS
            return action == "list"
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


class ToolLoopPolicy:
    """Typed before-call adapter for the stateful loop controller."""

    def __init__(self, controller: ToolCallGuardrailController) -> None:
        self.controller = controller

    def evaluate(self, call: ToolCallView, context: CodingTurnContext) -> PolicyDecision:
        decision = self.controller.before_call(call.name, call.args)
        if decision.allows_execution:
            return Allow()
        return Block(decision.code, decision.message, decision.to_metadata())


def _file_mutation_path_key(tool_name: str, args: Mapping[str, Any], cwd: str | None = None) -> str | None:
    if tool_name not in FILE_MUTATING_TOOL_NAMES:
        return None
    path = _file_mutation_arg_path(args)
    if path is None:
        return None
    return _canonical_shell_path(path, cwd)


def _tool_call_may_change_state(tool_name: str, args: Mapping[str, Any]) -> bool:
    if tool_name == "process":
        return args.get("action") not in {"poll", "wait", "list"}
    if tool_name in MUTATING_TOOL_NAMES:
        return True
    if tool_name != "bash":
        return False
    command = args.get("command")
    if not isinstance(command, str):
        return True
    return classify_bash_mutation(command).classification is not BashMutationClass.READ_ONLY


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
        "This recovery guidance applies unless the user explicitly limited attempts, retries, or commands. "
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
    if tool_name == "process":
        return common + (
            "The managed process is not making observable progress. Do not wait again. "
            "Terminate or kill it, inspect the last output, then fix the cause or choose a different strategy."
        )
    return common + "Use a different query/path only if the existing result is insufficient."


def _coerce_args(args: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return args if isinstance(args, Mapping) else {}


def _no_progress_signature(tool_name: str, args: Mapping[str, Any], cwd: str | None = None) -> ToolCallSignature | None:
    if tool_name == "process" and args.get("action") in {"poll", "wait", "list"}:
        semantic = {"action": args.get("action")}
        if args.get("action") in {"poll", "wait"}:
            semantic.update(
                {
                    "session_id": args.get("session_id"),
                    "cursor": args.get("cursor"),
                }
            )
        semantic_key = json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return ToolCallSignature(tool_name=tool_name, args_hash=_sha256(semantic_key))
    if tool_name != "bash":
        return None
    command = args.get("command")
    if not isinstance(command, str):
        return None
    semantic_key = _semantic_bash_read_key(command, cwd) or _bash_effective_args_key(args)
    if semantic_key is None:
        return None
    return ToolCallSignature(tool_name=tool_name, args_hash=_sha256(semantic_key))


def _workspace_scope_violation_signature(tool_name: str, resolved_path: str) -> ToolCallSignature:
    semantic_key = json.dumps(
        {
            "kind": "workspace_scope_violation",
            "path": _normalize_shell_path(resolved_path.replace("\\", "/")),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ToolCallSignature(tool_name=tool_name, args_hash=_sha256(semantic_key))


def _workspace_scope_violation_recovery_message(message: str, count: int, *, blocked: bool) -> str:
    prefix = "BLOCKED: " if blocked else ""
    return (
        f"{prefix}{message} The same out-of-workspace path {count} times this turn was blocked. "
        "The policy result has NOT changed and writing files inside the current working directory will not make "
        "that external path valid. STOP repeating this tool call. Use a path inside the current working directory, "
        "change the implementation to avoid the external path, or report the blocker and ask the user to authorize "
        "the exact absolute path."
    )


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
    if classify_bash_mutation(stripped).classification is not BashMutationClass.READ_ONLY:
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
