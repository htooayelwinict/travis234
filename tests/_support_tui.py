from __future__ import annotations

import base64

import builtins

import json

import os

import select

import threading

import time

import urllib.error

from types import SimpleNamespace

import pytest

import travis.tui.interactive_mode as interactive_mode
import travis.tui.interactive_model_auth as interactive_model_auth

from travis.tui import (
    Image,
    Component,
    Container,
    FooterComponent,
    FakeTerminal,
    format_cwd_for_footer,
    allocate_image_id,
    calculate_image_rows,
    fuzzy_filter,
    fuzzy_match,
    CancellableLoader,
    CombinedAutocompleteProvider,
    delete_all_kitty_images,
    delete_kitty_image,
    detect_capabilities,
    decode_kitty_printable,
    encode_iterm2,
    encode_kitty,
    get_capabilities,
    get_cell_dimensions,
    get_gif_dimensions,
    get_image_dimensions,
    Input,
    InteractiveMode,
    InteractiveRenderer,
    KeybindingsManager,
    Loader,
    Markdown,
    parse_osc11_background_color,
    ProcessTerminal,
    SelectItem,
    SelectList,
    SettingsList,
    SimpleAutocompleteProvider,
    StatusLine,
    StdinBuffer,
    get_png_dimensions,
    hyperlink,
    image_fallback,
    is_image_line,
    is_focusable,
    render_image,
    reset_capabilities_cache,
    set_capabilities,
    set_cell_dimensions,
    TUI,
    Text,
    ToolExecutionComponent,
    TruncatedText,
    TUI_KEYBINDINGS,
    get_keybindings,
    set_keybindings,
    extract_segments,
    strip_ansi,
    slice_by_column,
    slice_with_width,
    truncate_to_width,
    visible_width,
    wrap_text,
)

from travis.agent.types import (
    AgentEndEvent,
    AgentContext,
    AgentLoopConfig,
    MessageEndEvent,
    MessageStartEvent,
    MessageUpdateEvent,
    ToolExecutionEndEvent,
    ToolExecutionStartEvent,
    TurnEndEvent,
)

from travis.agent.types import AgentTool, AgentToolResult

from tests._provider_runtime import run_agent_loop

from travis.ai.providers.capabilities import ProviderParamWarning

from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events, tool_call_response_events

from travis.ai.providers.params import GenerationParams

from tests._provider_runtime import (
    get_api_key_for_provider,
    get_provider_auth_status,
    register_model,
    reset_models,
)

from tests._provider_runtime import register_api_provider, reset_api_providers

from travis.ai.types import (
    AssistantMessage,
    Cost,
    DoneEvent,
    ErrorEvent,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingContent,
    Usage,
    UserMessage,
    empty_usage,
    now_ms,
)

from travis.ai.model_resolver import ScopedModel

from travis.app import CodingApp

from travis.compaction.timing import ManualCompressionStatus

from travis.coding_agent import BashResult

from travis.coding_agent.processes.types import ProcessEvent, ProcessOwner, ProcessSnapshot, ProcessState

from travis.coding_agent.session_catalog import SessionCatalog

from travis.coding_agent.session_store import (
    BashExecutionMessage,
    BranchSummaryMessage,
    CustomMessage,
    SessionStore,
)

from travis.coding_agent.subagents import CallableSubagentBackend

from travis.coding_agent.tools.bash import BashOperations

from travis.coding_agent.tools.read import create_read_tool_definition

from travis.coding_agent.tools.types import ToolDefinition

from travis.ai.event_stream import create_assistant_message_event_stream

from tests._provider_runtime import ApiProvider

def setup_function() -> None:
    reset_api_providers()
    reset_models()

def _visible_index_of(line: str, text: str) -> int:
    index = line.index(text)
    return visible_width(line[:index])

def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()

def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)], api="faux", provider="faux", model="m",
        usage=empty_usage(), stop_reason="stop", timestamp=now_ms(),
    )

def _seed_tui_resume_session(agent_dir, cwd, *, session_id="resume-target", marker="persisted marker"):
    catalog = SessionCatalog(str(agent_dir))
    path, resolved_id = catalog.new_session_path(str(cwd), session_id=session_id)
    store = SessionStore(path, cwd=str(cwd.resolve()), session_id=resolved_id)
    model = faux_model()
    store.append_model_change(model.provider, model.id)
    store.append_thinking_level_change("medium")
    store.append_message(UserMessage(content=marker, timestamp=now_ms()))
    return path

__all__ = [name for name in globals() if not (name.startswith('__') and name.endswith('__'))]
