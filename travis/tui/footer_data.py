"""Focused footer data ownership for the TUI."""

from __future__ import annotations

import inspect
import json
import os
import queue
import signal as signal_module
import subprocess
import threading
import time
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

from travis.ai.providers.capabilities import ProviderParamWarning
from travis.ai.providers.model_catalog import get_last_openrouter_live_catalog_error, get_live_openrouter_models
from travis.ai.providers.params import GenerationParams, compact_generation_params_display
from travis.compaction import estimate_tokens
from travis.coding_agent.session_types import BashResult
from travis.coding_agent.session_catalog import SessionInfo
from travis.coding_agent.session_commands import SessionCommandExecutor
from travis.coding_agent.processes.types import ProcessEvent, ProcessSnapshot, ProcessState
from travis.coding_agent.tools.bash import BashExecOptions, get_shell_env
from travis.coding_agent.tools.output_spool import OutputSpool
from travis.tui.components import (
    CombinedAutocompleteProvider,
    Component,
    Container,
    FooterComponent,
    Input,
    Spacer,
    StatusLine,
    Text,
)
from travis.tui.components.autocomplete import _call_autocomplete_method, _settle_autocomplete_result
from travis.tui.interactive import (
    AssistantMessageComponent,
    BashExecutionComponent,
    message_to_component,
    user_message_to_component,
)
from travis.tui.model_loader import ModelCatalogLoader
from travis.tui.user_commands import (
    ResolvedUserCommand,
    UserCommandBinding,
    UserCommandController,
    UserCommandHandle,
)

def _footer_usage_stats(messages) -> dict[str, object]:
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_write = 0
    total_cost = 0.0
    latest_cache_hit_rate: float | None = None
    for message in messages:
        if getattr(message, "role", None) != "assistant":
            continue
        usage = getattr(message, "usage", None)
        if usage is None:
            continue
        input_tokens = int(getattr(usage, "input", 0) or 0)
        output_tokens = int(getattr(usage, "output", 0) or 0)
        cache_read = int(getattr(usage, "cache_read", getattr(usage, "cacheRead", 0)) or 0)
        cache_write = int(getattr(usage, "cache_write", getattr(usage, "cacheWrite", 0)) or 0)
        total_input += input_tokens
        total_output += output_tokens
        total_cache_read += cache_read
        total_cache_write += cache_write
        cost = getattr(usage, "cost", None)
        total_cost += float(getattr(cost, "total", 0.0) or 0.0)
        latest_prompt_tokens = input_tokens + cache_read + cache_write
        latest_cache_hit_rate = (cache_read / latest_prompt_tokens) * 100 if latest_prompt_tokens > 0 else None
    return {
        "input": total_input,
        "output": total_output,
        "cache_read": total_cache_read,
        "cache_write": total_cache_write,
        "cost": total_cost,
        "latest_cache_hit_rate": latest_cache_hit_rate,
    }


@dataclass(frozen=True)
class _GitPaths:
    repo_dir: Path
    common_git_dir: Path
    head_path: Path


_UNSET_BRANCH = object()
_GIT_WATCH_DEBOUNCE_SECONDS = 0.5
_GIT_WATCH_POLL_SECONDS = 0.1


def _find_git_paths(cwd: str) -> _GitPaths | None:
    directory = Path(cwd).resolve()
    if directory.is_file():
        directory = directory.parent
    while True:
        git_path = directory / ".git"
        if git_path.exists():
            try:
                if git_path.is_file():
                    content = git_path.read_text(encoding="utf-8").strip()
                    if content.startswith("gitdir: "):
                        git_dir = (directory / content[8:].strip()).resolve()
                        head_path = git_dir / "HEAD"
                        if not head_path.exists():
                            return None
                        common_dir_path = git_dir / "commondir"
                        common_git_dir = (
                            (git_dir / common_dir_path.read_text(encoding="utf-8").strip()).resolve()
                            if common_dir_path.exists()
                            else git_dir
                        )
                        return _GitPaths(repo_dir=directory, common_git_dir=common_git_dir, head_path=head_path)
                elif git_path.is_dir():
                    head_path = git_path / "HEAD"
                    if not head_path.exists():
                        return None
                    return _GitPaths(repo_dir=directory, common_git_dir=git_path, head_path=head_path)
            except OSError:
                return None
        parent = directory.parent
        if parent == directory:
            return None
        directory = parent


def _resolve_branch_with_git_sync(repo_dir: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "symbolic-ref", "--quiet", "--short", "HEAD"],
            cwd=str(repo_dir),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    branch = result.stdout.strip() if result.returncode == 0 else ""
    return branch or None


def _resolve_git_branch_sync(git_paths: _GitPaths | None) -> str | None:
    try:
        if git_paths is None:
            return None
        content = git_paths.head_path.read_text(encoding="utf-8").strip()
        if content.startswith("ref: refs/heads/"):
            branch = content[16:]
            if branch == ".invalid":
                return _resolve_branch_with_git_sync(git_paths.repo_dir) or "detached"
            return branch
        return "detached"
    except OSError:
        return None


def _path_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


class _ExtensionFooterDataProvider:
    def __init__(self, mode: InteractiveMode) -> None:
        self._mode = mode
        self._cwd = str(mode.app.cwd)
        self._git_paths = _find_git_paths(self._cwd)
        self._cached_branch: str | None | object = _UNSET_BRANCH
        self._branch_change_callbacks: list[Callable[[], object]] = []
        self._available_provider_count = 0
        self._disposed = False
        self._lock = threading.RLock()
        self._refresh_timer: threading.Timer | None = None
        self._watch_stop = threading.Event()
        self._watch_thread: threading.Thread | None = None
        self._watch_signatures: dict[Path, tuple[int, int] | None] = {}
        self._setup_git_watcher()

    def get_git_branch(self) -> str | None:
        with self._lock:
            if self._cached_branch is _UNSET_BRANCH:
                self._cached_branch = _resolve_git_branch_sync(self._git_paths)
            if isinstance(self._cached_branch, str) or self._cached_branch is None:
                return self._cached_branch
        return None


    def get_extension_statuses(self) -> dict[str, str]:
        return dict(self._mode.extension_statuses)


    def set_extension_status(self, key: str, text: str | None) -> None:
        if text is None:
            self._mode.extension_statuses.pop(str(key), None)
        else:
            self._mode.extension_statuses[str(key)] = str(text)


    def clear_extension_statuses(self) -> None:
        self._mode.extension_statuses.clear()


    def get_available_provider_count(self) -> int:
        return self._available_provider_count


    def set_available_provider_count(self, count: int) -> None:
        self._available_provider_count = max(0, int(count))


    def set_cwd(self, cwd: str) -> None:
        with self._lock:
            if self._cwd == cwd:
                return
            self._cwd = cwd
            self._cancel_refresh_timer()
            self._git_paths = _find_git_paths(cwd)
            self._cached_branch = _UNSET_BRANCH
            self._watch_signatures = self._current_watch_signatures()
            self._setup_git_watcher()
        self._notify_branch_change()


    def refresh_git_branch(self) -> None:
        with self._lock:
            previous_branch = self.get_git_branch()
            self._cached_branch = _UNSET_BRANCH
            next_branch = self.get_git_branch()
        if previous_branch != next_branch:
            self._notify_branch_change()


    def on_branch_change(self, handler: Callable[[], object]) -> Callable[[], None]:
        with self._lock:
            self._branch_change_callbacks.append(handler)

        def unsubscribe() -> None:
            with self._lock:
                try:
                    self._branch_change_callbacks.remove(handler)
                except ValueError:
                    return

        return unsubscribe


    def dispose(self) -> None:
        with self._lock:
            self._disposed = True
            self._cancel_refresh_timer()
            self._branch_change_callbacks.clear()
            self._watch_stop.set()
        if self._watch_thread is not None and threading.current_thread() is not self._watch_thread:
            self._watch_thread.join(timeout=0.5)

    def _notify_branch_change(self) -> None:
        with self._lock:
            callbacks = list(self._branch_change_callbacks)
        for callback in callbacks:
            callback()

    def _cancel_refresh_timer(self) -> None:
        if self._refresh_timer is not None:
            self._refresh_timer.cancel()
            self._refresh_timer = None

    def _setup_git_watcher(self) -> None:
        if self._disposed or self._git_paths is None:
            return
        if self._watch_thread is not None and self._watch_thread.is_alive():
            return
        self._watch_stop.clear()
        self._watch_signatures = self._current_watch_signatures()
        self._watch_thread = threading.Thread(target=self._watch_git_paths, name="travis-footer-git-watch", daemon=True)
        self._watch_thread.start()

    def _current_watch_paths(self) -> list[Path]:
        if self._git_paths is None:
            return []
        paths = [self._git_paths.head_path.parent, self._git_paths.head_path]
        reftable_dir = self._git_paths.common_git_dir / "reftable"
        if reftable_dir.exists():
            paths.append(reftable_dir)
            tables_list_path = reftable_dir / "tables.list"
            if tables_list_path.exists():
                paths.append(tables_list_path)
        return paths

    def _current_watch_signatures(self) -> dict[Path, tuple[int, int] | None]:
        return {path: _path_signature(path) for path in self._current_watch_paths()}

    def _watch_git_paths(self) -> None:
        while not self._watch_stop.wait(_GIT_WATCH_POLL_SECONDS):
            with self._lock:
                if self._disposed:
                    return
                next_signatures = self._current_watch_signatures()
                if not next_signatures:
                    self._watch_signatures = next_signatures
                    continue
                changed = next_signatures != self._watch_signatures
                self._watch_signatures = next_signatures
            if changed:
                self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        with self._lock:
            if self._disposed or self._refresh_timer is not None:
                return
            self._refresh_timer = threading.Timer(_GIT_WATCH_DEBOUNCE_SECONDS, self._run_scheduled_refresh)
            self._refresh_timer.daemon = True
            self._refresh_timer.start()

    def _run_scheduled_refresh(self) -> None:
        with self._lock:
            self._refresh_timer = None
            if self._disposed:
                return
        self.refresh_git_branch()

__all__ = (
    '_ExtensionFooterDataProvider',
    '_GIT_WATCH_DEBOUNCE_SECONDS',
    '_GIT_WATCH_POLL_SECONDS',
    '_GitPaths',
    '_UNSET_BRANCH',
    '_find_git_paths',
    '_footer_usage_stats',
    '_path_signature',
    '_resolve_branch_with_git_sync',
    '_resolve_git_branch_sync',
)
