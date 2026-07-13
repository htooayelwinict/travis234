"""settings manager for travis coding-agent runtime."""

from __future__ import annotations

import copy
import json
import math
import os
import uuid
from pathlib import Path
from typing import Callable, Literal, TypedDict

CONFIG_DIR_NAME = ".travis234"
DEFAULT_HTTP_IDLE_TIMEOUT_MS = 300_000
SettingsScope = Literal["global", "project"]


class SettingsError(TypedDict):
    scope: SettingsScope
    error: Exception


class SettingsStorage:
    def with_lock(self, scope: SettingsScope, fn: Callable[[str | None], str | None]) -> None:
        raise NotImplementedError



class InMemorySettingsStorage(SettingsStorage):
    def __init__(self) -> None:
        self.global_content: str | None = None
        self.project_content: str | None = None

    def with_lock(self, scope: SettingsScope, fn: Callable[[str | None], str | None]) -> None:
        current = self.global_content if scope == "global" else self.project_content
        next_content = fn(current)
        if next_content is None:
            return
        if scope == "global":
            self.global_content = next_content
        else:
            self.project_content = next_content



class FileSettingsStorage(SettingsStorage):
    def __init__(self, cwd: str, agent_dir: str) -> None:
        resolved_cwd = Path(cwd).expanduser().resolve()
        resolved_agent_dir = Path(agent_dir).expanduser().resolve()
        self.global_settings_path = resolved_agent_dir / "settings.json"
        self.project_settings_path = resolved_cwd / CONFIG_DIR_NAME / "settings.json"

    def with_lock(self, scope: SettingsScope, fn: Callable[[str | None], str | None]) -> None:
        path = self.global_settings_path if scope == "global" else self.project_settings_path
        current = path.read_text(encoding="utf-8") if path.exists() else None
        next_content = fn(current)
        if next_content is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(next_content, encoding="utf-8")



def _deep_merge_settings(base: dict, overrides: dict) -> dict:
    result = copy.deepcopy(base)
    for key, override_value in overrides.items():
        if override_value is None:
            continue
        base_value = base.get(key)
        if isinstance(base_value, dict) and isinstance(override_value, dict):
            result[key] = {**copy.deepcopy(base_value), **copy.deepcopy(override_value)}
        else:
            result[key] = copy.deepcopy(override_value)
    return result


def _migrate_settings(settings: dict) -> dict:
    migrated = copy.deepcopy(settings)
    if "queueMode" in migrated and "steeringMode" not in migrated:
        migrated["steeringMode"] = migrated.pop("queueMode")
    if "transport" not in migrated and isinstance(migrated.get("websockets"), bool):
        migrated["transport"] = "websocket" if migrated.pop("websockets") else "sse"
    skills = migrated.get("skills")
    if isinstance(skills, dict):
        if "enableSkillCommands" not in migrated and "enableSkillCommands" in skills:
            migrated["enableSkillCommands"] = skills["enableSkillCommands"]
        custom_directories = skills.get("customDirectories")
        if isinstance(custom_directories, list) and custom_directories:
            migrated["skills"] = copy.deepcopy(custom_directories)
        else:
            migrated.pop("skills", None)
    retry = migrated.get("retry")
    if isinstance(retry, dict):
        provider = retry.get("provider") if isinstance(retry.get("provider"), dict) else {}
        if isinstance(retry.get("maxDelayMs"), (int, float)) and provider.get("maxRetryDelayMs") is None:
            retry["provider"] = {**provider, "maxRetryDelayMs": retry["maxDelayMs"]}
        retry.pop("maxDelayMs", None)
    return migrated


def _parse_timeout_setting(value, setting_name: str) -> int | None:
    timeout = _parse_http_idle_timeout_ms(value)
    if timeout is not None:
        return timeout
    if value is not None:
        raise RuntimeError(f"Invalid {setting_name} setting: {value}")
    return None


def _parse_http_idle_timeout_ms(value) -> int | None:
    if isinstance(value, str):
        trimmed = value.strip()
        if trimmed.lower() == "disabled":
            return 0
        if not trimmed:
            return None
        try:
            return _parse_http_idle_timeout_ms(float(trimmed))
        except ValueError:
            return None
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return math.floor(value)


def _normalized_path(value: str | None) -> str | None:
    if not value:
        return value
    return os.path.normpath(os.path.expanduser(value))


class SettingsManager:
    def __init__(
        self,
        storage: SettingsStorage,
        initial_global: dict | None = None,
        initial_project: dict | None = None,
        *,
        project_trusted: bool = True,
        initial_errors: list[SettingsError] | None = None,
    ) -> None:
        self.storage = storage
        self.global_settings = copy.deepcopy(initial_global or {})
        self.project_settings = copy.deepcopy(initial_project or {})
        self.project_trusted = project_trusted
        self.errors: list[SettingsError] = list(initial_errors or [])
        self.settings = _deep_merge_settings(self.global_settings, self.project_settings)

    @classmethod
    def create(
        cls,
        cwd: str,
        agent_dir: str | None = None,
        options: dict | None = None,
    ) -> "SettingsManager":
        agent_dir = agent_dir or str(Path.home() / ".travis234" / "agent")
        return cls.from_storage(FileSettingsStorage(cwd, agent_dir), options)

    @classmethod
    def from_storage(cls, storage: SettingsStorage, options: dict | None = None) -> "SettingsManager":
        project_trusted = (options or {}).get("projectTrusted", True)
        global_settings, global_error = cls._try_load_from_storage(storage, "global")
        project_settings, project_error = cls._try_load_from_storage(storage, "project", project_trusted)
        errors: list[SettingsError] = []
        if global_error:
            errors.append({"scope": "global", "error": global_error})
        if project_error:
            errors.append({"scope": "project", "error": project_error})
        return cls(
            storage,
            global_settings,
            project_settings,
            project_trusted=project_trusted,
            initial_errors=errors,
        )


    @classmethod
    def in_memory(cls, settings: dict | None = None) -> "SettingsManager":
        storage = InMemorySettingsStorage()
        initial_settings = _migrate_settings(copy.deepcopy(settings or {}))
        storage.with_lock("global", lambda _current: json.dumps(initial_settings, indent=2))
        return cls.from_storage(storage)


    @staticmethod
    def _load_from_storage(storage: SettingsStorage, scope: SettingsScope, project_trusted: bool = True) -> dict:
        if scope == "project" and not project_trusted:
            return {}
        content: str | None = None

        def capture(current: str | None) -> None:
            nonlocal content
            content = current
            return None

        storage.with_lock(scope, capture)
        if not content:
            return {}
        loaded = json.loads(content)
        return _migrate_settings(loaded if isinstance(loaded, dict) else {})

    @classmethod
    def _try_load_from_storage(
        cls,
        storage: SettingsStorage,
        scope: SettingsScope,
        project_trusted: bool = True,
    ) -> tuple[dict, Exception | None]:
        try:
            return cls._load_from_storage(storage, scope, project_trusted), None
        except Exception as error:  # noqa: BLE001 - SettingsManager records non-fatal parse errors.
            return {}, error

    def reload(self) -> None:
        global_settings, global_error = self._try_load_from_storage(self.storage, "global")
        if global_error is None:
            self.global_settings = global_settings
        else:
            self._record_error("global", global_error)
        project_settings, project_error = self._try_load_from_storage(self.storage, "project", self.project_trusted)
        if project_error is None:
            self.project_settings = project_settings
        else:
            self._record_error("project", project_error)
        self._refresh_merged()

    def flush(self) -> None:
        return None

    def drain_errors(self) -> list[SettingsError]:
        drained = list(self.errors)
        self.errors = []
        return drained


    def apply_overrides(self, overrides: dict) -> None:
        self.settings = _deep_merge_settings(self.settings, overrides)


    def get_global_settings(self) -> dict:
        return copy.deepcopy(self.global_settings)


    def get_project_settings(self) -> dict:
        return copy.deepcopy(self.project_settings)


    def is_project_trusted(self) -> bool:
        return self.project_trusted


    def set_project_trusted(self, trusted: bool) -> None:
        if self.project_trusted == trusted:
            return
        self.project_trusted = trusted
        if not trusted:
            self.project_settings = {}
        else:
            project_settings, project_error = self._try_load_from_storage(self.storage, "project", True)
            self.project_settings = project_settings
            if project_error:
                self._record_error("project", project_error)
        self._refresh_merged()


    def get_last_changelog_version(self) -> str | None:
        return self.settings.get("lastChangelogVersion")

    def set_last_changelog_version(self, version: str) -> None:
        self._set_global("lastChangelogVersion", version)

    def get_session_dir(self) -> str | None:
        return _normalized_path(self.settings.get("sessionDir"))

    def get_default_provider(self) -> str | None:
        return self.settings.get("defaultProvider")

    def set_default_provider(self, provider: str) -> None:
        self._set_global("defaultProvider", provider)

    def get_default_model(self) -> str | None:
        return self.settings.get("defaultModel")

    def set_default_model(self, model_id: str) -> None:
        self._set_global("defaultModel", model_id)

    def set_default_model_and_provider(self, provider: str, model_id: str) -> None:
        self.global_settings["defaultProvider"] = provider
        self.global_settings["defaultModel"] = model_id
        self._save_global()

    def get_steering_mode(self) -> str:
        return self.settings.get("steeringMode") or "one-at-a-time"

    def set_steering_mode(self, mode: str) -> None:
        self._set_global("steeringMode", mode)

    def get_follow_up_mode(self) -> str:
        return self.settings.get("followUpMode") or "one-at-a-time"

    def set_follow_up_mode(self, mode: str) -> None:
        self._set_global("followUpMode", mode)

    def get_theme(self) -> str | None:
        return self.settings.get("theme")

    def set_theme(self, theme: str) -> None:
        self._set_global("theme", theme)

    def get_default_thinking_level(self) -> str | None:
        return self.settings.get("defaultThinkingLevel")

    def set_default_thinking_level(self, level: str) -> None:
        self._set_global("defaultThinkingLevel", level)

    def get_transport(self) -> str:
        return self.settings.get("transport") or "auto"

    def set_transport(self, transport: str) -> None:
        self._set_global("transport", transport)

    def get_compaction_enabled(self) -> bool:
        return self.settings.get("compaction", {}).get("enabled", True)

    def set_compaction_enabled(self, enabled: bool) -> None:
        self._set_global_nested("compaction", "enabled", enabled)

    def get_compaction_reserve_tokens(self) -> int:
        return self.settings.get("compaction", {}).get("reserveTokens", 16_384)

    def get_compaction_keep_recent_tokens(self) -> int:
        return self.settings.get("compaction", {}).get("keepRecentTokens", 20_000)

    def get_compaction_settings(self) -> dict:
        return {
            "enabled": self.get_compaction_enabled(),
            "reserveTokens": self.get_compaction_reserve_tokens(),
            "keepRecentTokens": self.get_compaction_keep_recent_tokens(),
        }

    def get_branch_summary_settings(self) -> dict:
        branch_summary = self.settings.get("branchSummary", {})
        return {
            "reserveTokens": branch_summary.get("reserveTokens", 16_384),
            "skipPrompt": branch_summary.get("skipPrompt", False),
        }

    def get_branch_summary_skip_prompt(self) -> bool:
        return self.settings.get("branchSummary", {}).get("skipPrompt", False)

    def get_retry_enabled(self) -> bool:
        return self.settings.get("retry", {}).get("enabled", True)

    def set_retry_enabled(self, enabled: bool) -> None:
        self._set_global_nested("retry", "enabled", enabled)

    def get_retry_settings(self) -> dict:
        retry = self.settings.get("retry", {})
        return {
            "enabled": self.get_retry_enabled(),
            "maxRetries": retry.get("maxRetries", 3),
            "baseDelayMs": retry.get("baseDelayMs", 2000),
        }

    def get_http_idle_timeout_ms(self) -> int:
        parsed = _parse_timeout_setting(self.settings.get("httpIdleTimeoutMs"), "httpIdleTimeoutMs")
        return DEFAULT_HTTP_IDLE_TIMEOUT_MS if parsed is None else parsed

    def set_http_idle_timeout_ms(self, timeout_ms: int) -> None:
        timeout = _parse_http_idle_timeout_ms(timeout_ms)
        if timeout is None:
            raise RuntimeError(f"Invalid httpIdleTimeoutMs setting: {timeout_ms}")
        self._set_global("httpIdleTimeoutMs", timeout)

    def get_provider_retry_settings(self) -> dict:
        provider = self.settings.get("retry", {}).get("provider", {})
        return {
            "timeoutMs": provider.get("timeoutMs"),
            "maxRetries": provider.get("maxRetries"),
            "maxRetryDelayMs": provider.get("maxRetryDelayMs", 60_000),
        }

    def get_websocket_connect_timeout_ms(self) -> int | None:
        return _parse_timeout_setting(self.settings.get("websocketConnectTimeoutMs"), "websocketConnectTimeoutMs")

    def get_hide_thinking_block(self) -> bool:
        return self.settings.get("hideThinkingBlock", False)

    def set_hide_thinking_block(self, hide: bool) -> None:
        self._set_global("hideThinkingBlock", hide)

    def get_shell_path(self) -> str | None:
        return self.settings.get("shellPath")


    def set_shell_path(self, path: str | None) -> None:
        self._set_global("shellPath", path)

    def get_quiet_startup(self) -> bool:
        return self.settings.get("quietStartup", False)

    def set_quiet_startup(self, quiet: bool) -> None:
        self._set_global("quietStartup", quiet)

    def get_default_project_trust(self) -> str:
        value = self.global_settings.get("defaultProjectTrust")
        return value if value in {"always", "never"} else "ask"

    def set_default_project_trust(self, default_project_trust: str) -> None:
        self._set_global("defaultProjectTrust", default_project_trust)

    def get_shell_command_prefix(self) -> str | None:
        return self.settings.get("shellCommandPrefix")


    def set_shell_command_prefix(self, prefix: str | None) -> None:
        self._set_global("shellCommandPrefix", prefix)

    def get_npm_command(self) -> list[str] | None:
        command = self.settings.get("npmCommand")
        return list(command) if isinstance(command, list) else None

    def set_npm_command(self, command: list[str] | None) -> None:
        self._set_global("npmCommand", list(command) if command is not None else None)

    def get_collapse_changelog(self) -> bool:
        return self.settings.get("collapseChangelog", False)

    def set_collapse_changelog(self, collapse: bool) -> None:
        self._set_global("collapseChangelog", collapse)

    def get_enable_install_telemetry(self) -> bool:
        return self.settings.get("enableInstallTelemetry", True)

    def set_enable_install_telemetry(self, enabled: bool) -> None:
        self._set_global("enableInstallTelemetry", enabled)

    def get_enable_analytics(self) -> bool:
        return self.settings.get("enableAnalytics", False)

    def get_tracking_id(self) -> str | None:
        return self.settings.get("trackingId")

    def set_enable_analytics(self, enabled: bool) -> None:
        self.global_settings["enableAnalytics"] = enabled
        if enabled and not self.global_settings.get("trackingId"):
            self.global_settings["trackingId"] = str(uuid.uuid4())
        self._save_global()

    def get_packages(self) -> list:
        return list(self.settings.get("packages", []))

    def set_packages(self, packages: list) -> None:
        self._set_global("packages", list(packages))

    def set_project_packages(self, packages: list) -> None:
        self._set_project("packages", list(packages))

    def get_extension_paths(self) -> list[str]:
        return list(self.settings.get("extensions", []))

    def set_extension_paths(self, paths: list[str]) -> None:
        self._set_global("extensions", list(paths))

    def set_project_extension_paths(self, paths: list[str]) -> None:
        self._set_project("extensions", list(paths))

    def get_skill_paths(self) -> list[str]:
        return list(self.settings.get("skills", []))

    def set_skill_paths(self, paths: list[str]) -> None:
        self._set_global("skills", list(paths))

    def set_project_skill_paths(self, paths: list[str]) -> None:
        self._set_project("skills", list(paths))

    def get_prompt_template_paths(self) -> list[str]:
        return list(self.settings.get("prompts", []))

    def set_prompt_template_paths(self, paths: list[str]) -> None:
        self._set_global("prompts", list(paths))

    def set_project_prompt_template_paths(self, paths: list[str]) -> None:
        self._set_project("prompts", list(paths))

    def get_theme_paths(self) -> list[str]:
        return list(self.settings.get("themes", []))

    def set_theme_paths(self, paths: list[str]) -> None:
        self._set_global("themes", list(paths))

    def set_project_theme_paths(self, paths: list[str]) -> None:
        self._set_project("themes", list(paths))

    def get_enable_skill_commands(self) -> bool:
        return self.settings.get("enableSkillCommands", True)

    def set_enable_skill_commands(self, enabled: bool) -> None:
        self._set_global("enableSkillCommands", enabled)

    def get_thinking_budgets(self) -> dict | None:
        budgets = self.settings.get("thinkingBudgets")
        return copy.deepcopy(budgets) if isinstance(budgets, dict) else None

    def get_show_images(self) -> bool:
        return self.settings.get("terminal", {}).get("showImages", True)

    def set_show_images(self, show: bool) -> None:
        self._set_global_nested("terminal", "showImages", show)

    def get_image_width_cells(self) -> int:
        width = self.settings.get("terminal", {}).get("imageWidthCells")
        if not isinstance(width, (int, float)) or not math.isfinite(width):
            return 60
        return max(1, math.floor(width))

    def set_image_width_cells(self, width: int) -> None:
        self._set_global_nested("terminal", "imageWidthCells", max(1, math.floor(width)))

    def get_clear_on_shrink(self) -> bool:
        terminal = self.settings.get("terminal", {})
        if "clearOnShrink" in terminal:
            return bool(terminal["clearOnShrink"])
        return os.environ.get("TRAVIS234_CLEAR_ON_SHRINK") == "1"

    def set_clear_on_shrink(self, enabled: bool) -> None:
        self._set_global_nested("terminal", "clearOnShrink", enabled)

    def get_show_terminal_progress(self) -> bool:
        return self.settings.get("terminal", {}).get("showTerminalProgress", False)

    def set_show_terminal_progress(self, enabled: bool) -> None:
        self._set_global_nested("terminal", "showTerminalProgress", enabled)

    def get_image_auto_resize(self) -> bool:
        return self.settings.get("images", {}).get("autoResize", True)


    def set_image_auto_resize(self, enabled: bool) -> None:
        self._set_global_nested("images", "autoResize", enabled)

    def get_block_images(self) -> bool:
        return self.settings.get("images", {}).get("blockImages", False)

    def set_block_images(self, blocked: bool) -> None:
        self._set_global_nested("images", "blockImages", blocked)

    def get_enabled_models(self) -> list[str] | None:
        patterns = self.settings.get("enabledModels")
        return list(patterns) if isinstance(patterns, list) else None

    def set_enabled_models(self, patterns: list[str] | None) -> None:
        self._set_global("enabledModels", list(patterns) if patterns is not None else None)

    def get_double_escape_action(self) -> str:
        return self.settings.get("doubleEscapeAction") or "tree"

    def set_double_escape_action(self, action: str) -> None:
        self._set_global("doubleEscapeAction", action)

    def get_tree_filter_mode(self) -> str:
        mode = self.settings.get("treeFilterMode")
        return mode if mode in {"default", "no-tools", "user-only", "labeled-only", "all"} else "default"

    def set_tree_filter_mode(self, mode: str) -> None:
        self._set_global("treeFilterMode", mode)

    def get_show_hardware_cursor(self) -> bool:
        return self.settings.get("showHardwareCursor", os.environ.get("TRAVIS234_HARDWARE_CURSOR") == "1")

    def set_show_hardware_cursor(self, enabled: bool) -> None:
        self._set_global("showHardwareCursor", enabled)

    def get_editor_padding_x(self) -> int:
        return self.settings.get("editorPaddingX", 0)

    def set_editor_padding_x(self, padding: int) -> None:
        self._set_global("editorPaddingX", max(0, min(3, math.floor(padding))))

    def get_autocomplete_max_visible(self) -> int:
        return self.settings.get("autocompleteMaxVisible", 5)

    def set_autocomplete_max_visible(self, max_visible: int) -> None:
        self._set_global("autocompleteMaxVisible", max(3, min(20, math.floor(max_visible))))

    def get_code_block_indent(self) -> str:
        return self.settings.get("markdown", {}).get("codeBlockIndent", "  ")

    def get_warnings(self) -> dict:
        return copy.deepcopy(self.settings.get("warnings", {}))

    def set_warnings(self, warnings: dict) -> None:
        self._set_global("warnings", copy.deepcopy(warnings))

    def _set_global(self, key: str, value) -> None:
        self.global_settings[key] = value
        self._save_global()

    def _set_global_nested(self, key: str, nested_key: str, value) -> None:
        current = self.global_settings.get(key)
        if not isinstance(current, dict):
            current = {}
            self.global_settings[key] = current
        current[nested_key] = value
        self._save_global()

    def _set_project(self, key: str, value) -> None:
        self._assert_project_trusted_for_write()
        self.project_settings[key] = value
        self._save_project()

    def _save_global(self) -> None:
        self._refresh_merged()
        self._persist("global", self.global_settings)

    def _save_project(self) -> None:
        self._assert_project_trusted_for_write()
        self._refresh_merged()
        self._persist("project", self.project_settings)

    def _persist(self, scope: SettingsScope, settings: dict) -> None:
        try:
            self.storage.with_lock(scope, lambda _current: json.dumps(_migrate_settings(settings), indent=2))
        except Exception as error:  # noqa: BLE001 - SettingsManager records write errors.
            self._record_error(scope, error)

    def _refresh_merged(self) -> None:
        self.settings = _deep_merge_settings(self.global_settings, self.project_settings)

    def _assert_project_trusted_for_write(self) -> None:
        if not self.project_trusted:
            raise RuntimeError("Project is not trusted; refusing to write project settings")

    def _record_error(self, scope: SettingsScope, error: Exception) -> None:
        self.errors.append({"scope": scope, "error": error})
