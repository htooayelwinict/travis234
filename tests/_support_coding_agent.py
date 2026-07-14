from __future__ import annotations

import base64

import dataclasses

import json

import os

import sys

import threading

import time

from pathlib import Path

from types import SimpleNamespace

import pytest

from travis.agent.types import AbortSignal

from travis.agent.types import AgentTool

from travis.agent.types import AgentToolResult

from travis.ai.validation import ToolValidationError, validate_tool_arguments

from travis.ai.model_resolver import ScopedModel

from tests._provider_runtime import get_model, get_models, register_model, reset_models

from travis.ai.event_stream import create_assistant_message_event_stream

from travis.ai.types import (
    AssistantMessage,
    DoneEvent,
    ErrorEvent,
    ImageContent,
    Model,
    StartEvent,
    TextContent,
    ToolCall,
    ToolcallEndEvent,
    ToolcallStartEvent,
    ToolResultMessage,
    Usage,
    UserMessage,
    empty_usage,
    now_ms,
)

from travis.coding_agent import (
    AgentSession,
    ExtensionRunner,
    SettingsManager,
    build_system_prompt,
    create_all_tool_definitions,
    create_all_tools,
    create_coding_tools,
    create_read_only_tools,
    create_tool,
    create_tool_definition,
)

from travis.coding_agent.agent_session import BashResult, default_convert_to_llm

from travis.coding_agent.system_prompt import BuildSystemPromptOptions

from travis.coding_agent.tools.bash import (
    BASH_SCHEMA,
    BashOperations,
    BashSpawnContext,
    create_bash_tool,
    create_local_bash_operations,
)

from travis.coding_agent.tools.path_utils import resolve_to_cwd

from travis.coding_agent.tools.write import WRITE_SCHEMA, WriteOperations, create_write_tool

from travis.coding_agent.tools.truncate import truncate_head

from travis.coding_agent.tools.types import ToolContext, ToolDefinition, wrap_tool_definition

from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events, tool_call_response_events

from tests._provider_runtime import register_api_provider, reset_api_providers

from travis.coding_agent.resource_loader import Skill

from travis.coding_agent.session_store import BashExecutionMessage, SessionStore

from travis.coding_agent.source_info import create_synthetic_source_info

from travis.coding_agent.subagents import CallableSubagentBackend, SubagentResult

def setup_function() -> None:
    reset_api_providers()
    reset_models()

def _content_text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(block.text for block in content if isinstance(block, TextContent))

def _user_text(message: UserMessage) -> str:
    return _content_text(message.content)

def _serialized_text_content(text: str) -> list[dict[str, str | None]]:
    return [{"type": "text", "text": text, "textSignature": None}]

__all__ = [name for name in globals() if not (name.startswith('__') and name.endswith('__'))]
