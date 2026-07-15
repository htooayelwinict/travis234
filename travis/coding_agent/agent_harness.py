"""Async Python SDK facade composed from the production CodingApp owners."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from travis.ai.types import AssistantMessage, Model
from travis.coding_agent.automation import serialize_machine_value
from travis.coding_agent.config import get_agent_dir
from travis.coding_agent.session_catalog import SessionCatalog

if TYPE_CHECKING:
    from travis.app import CodingApp
    from travis.coding_agent.agent_session import AgentSession


HarnessListener = Callable[[dict[str, object]], object]


@dataclass(frozen=True)
class AgentHarnessConfig:
    cwd: str
    model: Model
    agent_dir: str | None = None
    persist_session: bool = True
    session_path: str | None = None
    thinking_level: str = "off"
    trust_override: bool | None = None
    offline: bool = False
    allowed_tools: tuple[str, ...] | None = None
    excluded_tools: tuple[str, ...] = ()
    extension_paths: tuple[str, ...] = ()
    skill_paths: tuple[str, ...] = ()
    prompt_template_paths: tuple[str, ...] = ()
    theme_paths: tuple[str, ...] = ()


class AgentHarness:
    """Async facade that delegates to one CodingApp and its existing owners."""

    def __init__(self, app: CodingApp) -> None:
        self._app = app
        self._operation_lock = asyncio.Lock()
        self._active_task: asyncio.Task[Any] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._listeners: list[HarnessListener] = []
        self._session_unsubscribe: Callable[[], None] | None = None
        self._rebound_unsubscribe = app.subscribe_session_rebound(self._bind_session_events)
        self._closed = False
        self._bind_session_events(app.session)

    @classmethod
    def create(cls, config: AgentHarnessConfig) -> "AgentHarness":
        from travis.app import CodingApp

        cwd = str(Path(config.cwd).expanduser().resolve())
        agent_dir = str(Path(config.agent_dir or get_agent_dir()).expanduser().resolve())
        if not config.persist_session and config.session_path is not None:
            raise ValueError("session_path requires persist_session=True")
        session_path = config.session_path
        if config.persist_session and session_path is None:
            catalog = SessionCatalog(agent_dir)
            try:
                session_path, _ = catalog.new_session_path(cwd)
            finally:
                catalog.close()
        app = CodingApp(
            cwd=cwd,
            model=config.model,
            thinking_level=config.thinking_level,
            enable_tui=False,
            project_trust_override=config.trust_override,
            session_path=session_path,
            agent_dir=agent_dir,
            allowed_tool_names=(list(config.allowed_tools) if config.allowed_tools is not None else None),
            excluded_tool_names=list(config.excluded_tools),
            additional_extension_paths=list(config.extension_paths),
            additional_skill_paths=list(config.skill_paths),
            additional_prompt_template_paths=list(config.prompt_template_paths),
            additional_theme_paths=list(config.theme_paths),
            offline=config.offline,
        )
        return cls(app)

    @property
    def app(self) -> CodingApp:
        return self._app

    @property
    def session(self) -> AgentSession:
        return self._app.session

    @property
    def resource_loader(self):
        return self.session.resource_loader

    @property
    def closed(self) -> bool:
        return self._closed

    async def __aenter__(self) -> "AgentHarness":
        self._ensure_open()
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        await self.close()

    def subscribe(self, listener: HarnessListener) -> Callable[[], None]:
        self._ensure_open()
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    async def prompt(
        self,
        text: str,
        *,
        image_paths: Sequence[str] = (),
    ) -> AssistantMessage:
        messages = await self._run_owner(
            self._app.run_turn,
            text,
            image_paths=list(image_paths) if image_paths else None,
        )
        assistant = _last_assistant(messages) or _last_assistant(self.session.messages)
        if assistant is None:
            raise RuntimeError("Prompt completed without an assistant message")
        return assistant

    async def continue_agent(self) -> AssistantMessage:
        messages = await self._run_owner(self.session.continue_)
        assistant = _last_assistant(messages) or _last_assistant(self.session.messages)
        if assistant is None:
            raise RuntimeError("Continue completed without an assistant message")
        return assistant

    async def compact(self, *, focus: str | None = None, deep: bool = False):
        return await self._run_owner(self.session.compact, focus=focus, deep=deep)

    async def abort(self) -> None:
        if self._closed:
            return
        self.session.agent.abort()
        self.session.abort_retry()
        self.session.abort_bash()

    async def rename_session(self, name: str | None) -> None:
        await self._run_owner(self._app.rename_session, name)

    async def switch_session(self, path: str, *, cwd_override: str | None = None) -> dict[str, bool]:
        return await self._run_owner(self._app.switch_session, path, cwd_override=cwd_override)

    async def fork_session(self, entry_id: str, *, position: str = "before") -> dict[str, object]:
        return await self._run_owner(self._app.fork_session, entry_id, position=position)

    async def clone_session(self) -> dict[str, object]:
        return await self._run_owner(self._app.clone_session)

    def session_tree(self) -> list[dict]:
        self._ensure_open()
        return self._app.session_tree()

    async def navigate_session_tree(self, target_id: str, options: dict | None = None) -> dict:
        return await self._run_owner(self._app.navigate_session_tree, target_id, options)

    async def reload_resources(self) -> None:
        await self._run_owner(self.session.reload)

    def list_skills(self) -> tuple[object, ...]:
        self._ensure_open()
        return tuple(self.resource_loader.get_skills().get("skills", []))

    def list_prompt_templates(self) -> tuple[object, ...]:
        self._ensure_open()
        return tuple(self.resource_loader.get_prompts().get("prompts", []))

    def list_themes(self) -> tuple[object, ...]:
        self._ensure_open()
        return tuple(self.resource_loader.get_themes().get("themes", []))

    async def close(self) -> None:
        if self._closed:
            return
        await self.abort()
        active = self._active_task
        current = asyncio.current_task()
        if active is not None and active is not current and not active.done():
            try:
                await asyncio.wait_for(asyncio.shield(active), timeout=10)
            except (TimeoutError, asyncio.CancelledError):
                pass
        async with self._operation_lock:
            if self._closed:
                return
            self._closed = True
            if self._session_unsubscribe is not None:
                self._session_unsubscribe()
                self._session_unsubscribe = None
            self._rebound_unsubscribe()
            await asyncio.to_thread(self._app.close)
            self._listeners.clear()

    async def _run_owner(self, callback: Callable[..., Any], *args: object, **kwargs: object):
        self._ensure_open()
        async with self._operation_lock:
            self._ensure_open()
            self._loop = asyncio.get_running_loop()
            self._active_task = asyncio.current_task()
            try:
                return await asyncio.to_thread(callback, *args, **kwargs)
            finally:
                self._active_task = None

    def _bind_session_events(self, session: AgentSession) -> None:
        if self._session_unsubscribe is not None:
            self._session_unsubscribe()
        self._session_unsubscribe = session.subscribe(self._forward_event)

    def _forward_event(self, event: object) -> None:
        value = serialize_machine_value(event)
        if isinstance(value, dict):
            normalized = value
        else:
            normalized = {
                "type": str(getattr(event, "type", type(event).__name__)),
                "value": value,
            }
        for listener in list(self._listeners):
            result = listener(dict(normalized))
            if not inspect.isawaitable(result):
                continue
            if self._loop is not None and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(result, self._loop).result()
            else:
                asyncio.run(result)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("AgentHarness is closed")


def _last_assistant(messages: object) -> AssistantMessage | None:
    if not isinstance(messages, Sequence):
        return None
    return next((message for message in reversed(messages) if isinstance(message, AssistantMessage)), None)


__all__ = ["AgentHarness", "AgentHarnessConfig"]
