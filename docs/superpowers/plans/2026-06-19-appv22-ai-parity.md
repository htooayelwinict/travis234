# appv22 ai-parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port pi's `ai` package surface into a fresh `appV2.2/appv22/ai/` Python package (message/model types, streaming `AssistantMessageEvent` protocol, api-registry, a real-SSE appv2-env provider), and remove the appv21 dependency.

**Architecture:** New `appv22/ai/` package mirrors `pi/packages/ai/src` (types → event_stream → stream/api-registry → providers). The agent loop is NOT rewired here (sub-project 2). The legacy `appv22/providers/appv2_env.py` `decide()` shim stays but is repointed off appv21 onto the new transport.

**Tech Stack:** Python 3.13, dataclasses + `typing.Literal`, `httpx` (SSE streaming), `queue`/`threading` for the stream, `pytest`.

## Global Constraints

- Structural + behavioral parity with `pi/packages/ai/src`; keep event `type` string literals identical to pi (`"text_delta"`, `"toolcall_end"`, `"done"`, `"error"`, `"toolCall"`, …). Python idiom: snake_case field/function names.
- No runtime import of `pi`, `hermes`, `hermes-agent`, `appv21`, or `appV2.1` anywhere under `appV2.2/`.
- Failures after a stream is returned are encoded as a `done`/`error` event, never raised out of the stream.
- New package path: `appV2.2/appv22/ai/`; importable as `appv22.ai`. Tests live in `appV2.2/tests/` and run from `appV2.2/`.
- Run tests from `appV2.2/`: `/Users/htooayelwin/lewis/allthebest/.venv/bin/python -m pytest tests/<file> -q`. (Below abbreviated as `python -m pytest`.)
- DRY, YAGNI, TDD, frequent commits. Only `git add` the exact files you changed.

---

## Task 1: Package scaffold + core types (`ai/types.py`)

**Files:**
- Create: `appV2.2/appv22/ai/__init__.py` (empty placeholder; real barrel in Task 9)
- Create: `appV2.2/appv22/ai/types.py`
- Test: `appV2.2/tests/test_ai_types.py`

**Interfaces:**
- Produces: dataclasses `TextContent`, `ThinkingContent`, `ImageContent`, `ToolCall`, `Cost`, `Usage`, `UserMessage`, `AssistantMessage`, `ToolResultMessage`, `Tool`, `Context`, `Model`; type aliases `Message`, `ContentBlock`, `StopReason`, `ThinkingLevel`, `Api`, `Provider`; option dataclasses `StreamOptions`, `SimpleStreamOptions`, `ProviderResponse`; event dataclasses `StartEvent`, `TextStartEvent`, `TextDeltaEvent`, `TextEndEvent`, `ThinkingStartEvent`, `ThinkingDeltaEvent`, `ThinkingEndEvent`, `ToolcallStartEvent`, `ToolcallDeltaEvent`, `ToolcallEndEvent`, `DoneEvent`, `ErrorEvent`; union alias `AssistantMessageEvent`; helper `empty_usage()`, `now_ms()`.

- [ ] **Step 1: Write the failing test** — `appV2.2/tests/test_ai_types.py`

```python
from __future__ import annotations

from appv22.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    TextContent,
    TextDeltaEvent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    Usage,
    empty_usage,
    now_ms,
)


def test_content_blocks_carry_pi_type_literals() -> None:
    assert TextContent(text="hi").type == "text"
    assert ToolCall(id="t1", name="read", arguments={"path": "a"}).type == "toolCall"


def test_empty_usage_shape_matches_pi() -> None:
    usage = empty_usage()
    assert usage.input == 0 and usage.total_tokens == 0
    assert usage.cost.total == 0.0


def test_user_and_assistant_messages() -> None:
    user = UserMessage(content="hello", timestamp=now_ms())
    assert user.role == "user"
    assistant = AssistantMessage(
        content=[TextContent(text="ok")],
        api="openai-completions",
        provider="openrouter",
        model="m",
        usage=empty_usage(),
        stop_reason="stop",
        timestamp=now_ms(),
    )
    assert assistant.role == "assistant"
    assert assistant.content[0].text == "ok"


def test_tool_result_message_defaults() -> None:
    result = ToolResultMessage(
        tool_call_id="t1",
        tool_name="read",
        content=[TextContent(text="data")],
        is_error=False,
        timestamp=now_ms(),
    )
    assert result.role == "toolResult"
    assert result.details is None


def test_event_type_literals() -> None:
    msg = AssistantMessage(
        content=[],
        api="x",
        provider="p",
        model="m",
        usage=empty_usage(),
        stop_reason="stop",
        timestamp=now_ms(),
    )
    assert TextDeltaEvent(content_index=0, delta="a", partial=msg).type == "text_delta"
    assert DoneEvent(reason="stop", message=msg).type == "done"


def test_context_holds_messages_and_tools() -> None:
    ctx = Context(
        system_prompt="sys",
        messages=[UserMessage(content="q", timestamp=now_ms())],
        tools=[Tool(name="read", description="read a file", parameters={"type": "object"})],
    )
    assert ctx.tools[0].name == "read"
    assert ctx.messages[0].role == "user"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ai_types.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'appv22.ai'`.

- [ ] **Step 3: Write minimal implementation** — `appV2.2/appv22/ai/__init__.py`

```python
"""appv22 port of pi's `ai` package (provider/model abstraction + streaming)."""
```

- [ ] **Step 4: Write minimal implementation** — `appV2.2/appv22/ai/types.py`

```python
"""Core data types for the appv22 ai layer. Port of pi/packages/ai/src/types.ts."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Union

Api = str
Provider = str
ThinkingLevel = Literal["minimal", "low", "medium", "high", "xhigh"]
StopReason = Literal["stop", "length", "toolUse", "error", "aborted"]
Transport = Literal["sse", "websocket", "websocket-cached", "auto"]


def now_ms() -> int:
    """Unix timestamp in milliseconds (pi messages use ms timestamps)."""
    return int(time.time() * 1000)


@dataclass
class TextContent:
    text: str
    text_signature: str | None = None
    type: Literal["text"] = "text"


@dataclass
class ThinkingContent:
    thinking: str
    thinking_signature: str | None = None
    redacted: bool = False
    type: Literal["thinking"] = "thinking"


@dataclass
class ImageContent:
    data: str  # base64
    mime_type: str
    type: Literal["image"] = "image"


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    thought_signature: str | None = None
    type: Literal["toolCall"] = "toolCall"


ContentBlock = Union[TextContent, ThinkingContent, ImageContent, ToolCall]


@dataclass
class Cost:
    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0
    total: float = 0.0


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total_tokens: int = 0
    cost: Cost = field(default_factory=Cost)


def empty_usage() -> Usage:
    return Usage(cost=Cost())


@dataclass
class UserMessage:
    content: "str | list[TextContent | ImageContent]"
    timestamp: int = field(default_factory=now_ms)
    role: Literal["user"] = "user"


@dataclass
class AssistantMessage:
    content: list[ContentBlock]
    api: Api
    provider: Provider
    model: str
    usage: Usage
    stop_reason: StopReason
    response_model: str | None = None
    response_id: str | None = None
    diagnostics: list[dict[str, Any]] | None = None
    error_message: str | None = None
    timestamp: int = field(default_factory=now_ms)
    role: Literal["assistant"] = "assistant"


@dataclass
class ToolResultMessage:
    tool_call_id: str
    tool_name: str
    content: list[TextContent | ImageContent]
    is_error: bool
    details: Any | None = None
    timestamp: int = field(default_factory=now_ms)
    role: Literal["toolResult"] = "toolResult"


Message = Union[UserMessage, AssistantMessage, ToolResultMessage]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON schema (pi uses TypeBox; we use plain JSON schema)


@dataclass
class Context:
    messages: list[Message]
    system_prompt: str | None = None
    tools: list[Tool] | None = None


@dataclass
class Model:
    id: str
    name: str
    api: Api
    provider: Provider
    base_url: str
    reasoning: bool = False
    thinking_level_map: dict[str, str | None] | None = None
    input: list[Literal["text", "image"]] = field(default_factory=lambda: ["text"])
    cost: Cost = field(default_factory=Cost)
    context_window: int = 0
    max_tokens: int = 0
    headers: dict[str, str] | None = None


@dataclass
class ProviderResponse:
    status: int
    headers: dict[str, str]


@dataclass
class StreamOptions:
    temperature: float | None = None
    max_tokens: int | None = None
    api_key: str | None = None
    transport: Transport | None = None
    session_id: str | None = None
    headers: dict[str, str] | None = None
    timeout_ms: int | None = None
    max_retries: int | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class SimpleStreamOptions(StreamOptions):
    reasoning: ThinkingLevel | None = None
    thinking_budgets: dict[str, int] | None = None


# --- Streaming event protocol (pi AssistantMessageEvent union) ---


@dataclass
class StartEvent:
    partial: AssistantMessage
    type: Literal["start"] = "start"


@dataclass
class TextStartEvent:
    content_index: int
    partial: AssistantMessage
    type: Literal["text_start"] = "text_start"


@dataclass
class TextDeltaEvent:
    content_index: int
    delta: str
    partial: AssistantMessage
    type: Literal["text_delta"] = "text_delta"


@dataclass
class TextEndEvent:
    content_index: int
    content: str
    partial: AssistantMessage
    type: Literal["text_end"] = "text_end"


@dataclass
class ThinkingStartEvent:
    content_index: int
    partial: AssistantMessage
    type: Literal["thinking_start"] = "thinking_start"


@dataclass
class ThinkingDeltaEvent:
    content_index: int
    delta: str
    partial: AssistantMessage
    type: Literal["thinking_delta"] = "thinking_delta"


@dataclass
class ThinkingEndEvent:
    content_index: int
    content: str
    partial: AssistantMessage
    type: Literal["thinking_end"] = "thinking_end"


@dataclass
class ToolcallStartEvent:
    content_index: int
    partial: AssistantMessage
    type: Literal["toolcall_start"] = "toolcall_start"


@dataclass
class ToolcallDeltaEvent:
    content_index: int
    delta: str
    partial: AssistantMessage
    type: Literal["toolcall_delta"] = "toolcall_delta"


@dataclass
class ToolcallEndEvent:
    content_index: int
    tool_call: ToolCall
    partial: AssistantMessage
    type: Literal["toolcall_end"] = "toolcall_end"


@dataclass
class DoneEvent:
    reason: Literal["stop", "length", "toolUse"]
    message: AssistantMessage
    type: Literal["done"] = "done"


@dataclass
class ErrorEvent:
    reason: Literal["aborted", "error"]
    error: AssistantMessage
    type: Literal["error"] = "error"


AssistantMessageEvent = Union[
    StartEvent,
    TextStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    ThinkingStartEvent,
    ThinkingDeltaEvent,
    ThinkingEndEvent,
    ToolcallStartEvent,
    ToolcallDeltaEvent,
    ToolcallEndEvent,
    DoneEvent,
    ErrorEvent,
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_ai_types.py -q`
Expected: PASS (6 passed).

- [ ] **Step 6: Commit**

```bash
git add appV2.2/appv22/ai/__init__.py appV2.2/appv22/ai/types.py appV2.2/tests/test_ai_types.py
git commit -m "feat(ai): port pi ai core types into appv22/ai/types.py"
```

---

## Task 2: Stream primitives (`ai/event_stream.py`)

**Files:**
- Create: `appV2.2/appv22/ai/event_stream.py`
- Test: `appV2.2/tests/test_ai_event_stream.py`

**Interfaces:**
- Consumes: `AssistantMessage`, `AssistantMessageEvent`, `DoneEvent`, `ErrorEvent` from `appv22.ai.types`.
- Produces: `EventStream` (`push(event)`, `end(result=None)`, `fail(exc)`, `__iter__`, `__aiter__`, `result_sync()`, `await result()`); `AssistantMessageEventStream(EventStream)` that auto-completes on `done`/`error` with the final `AssistantMessage`; `create_assistant_message_event_stream()`.

Notes for the implementer:
- Back the stream with `queue.Queue` + `threading.Event` so it works whether the producer is a worker thread (the real provider) or the same thread (faux/tests), and whether the consumer is sync (`result_sync`/`for`) or async (`async for`/`await result()`). No running event loop is required at construction.
- `AssistantMessageEventStream.result()` returns the final message for BOTH `done` and `error` (it does NOT raise on an `error` event — the error is encoded in the returned `AssistantMessage`). `fail()`/raising is reserved for unexpected internal stream errors only.

- [ ] **Step 1: Write the failing test** — `appV2.2/tests/test_ai_event_stream.py`

```python
from __future__ import annotations

import asyncio

from appv22.ai.event_stream import (
    AssistantMessageEventStream,
    create_assistant_message_event_stream,
)
from appv22.ai.types import (
    AssistantMessage,
    DoneEvent,
    ErrorEvent,
    StartEvent,
    TextDeltaEvent,
    empty_usage,
    now_ms,
)


def _msg(stop_reason: str = "stop", error_message: str | None = None) -> AssistantMessage:
    return AssistantMessage(
        content=[],
        api="faux",
        provider="faux",
        model="m",
        usage=empty_usage(),
        stop_reason=stop_reason,
        error_message=error_message,
        timestamp=now_ms(),
    )


def test_iterates_events_until_done_and_result_sync() -> None:
    stream = create_assistant_message_event_stream()
    final = _msg()
    stream.push(StartEvent(partial=final))
    stream.push(TextDeltaEvent(content_index=0, delta="hi", partial=final))
    stream.push(DoneEvent(reason="stop", message=final))

    events = list(stream)
    assert [e.type for e in events] == ["start", "text_delta", "done"]
    assert stream.result_sync() is final


def test_error_event_resolves_result_without_raising() -> None:
    stream = create_assistant_message_event_stream()
    err = _msg(stop_reason="error", error_message="boom")
    stream.push(ErrorEvent(reason="error", error=err))

    events = list(stream)
    assert [e.type for e in events] == ["error"]
    result = stream.result_sync()
    assert result.error_message == "boom"


def test_async_iteration_and_await_result() -> None:
    stream = create_assistant_message_event_stream()
    final = _msg()
    stream.push(StartEvent(partial=final))
    stream.push(DoneEvent(reason="stop", message=final))

    async def drive() -> list[str]:
        types = [e.type async for e in stream]
        assert (await stream.result()) is final
        return types

    assert asyncio.run(drive()) == ["start", "done"]


def test_is_assistant_message_event_stream() -> None:
    assert isinstance(create_assistant_message_event_stream(), AssistantMessageEventStream)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ai_event_stream.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'appv22.ai.event_stream'`.

- [ ] **Step 3: Write minimal implementation** — `appV2.2/appv22/ai/event_stream.py`

```python
"""Push/async-iterable event stream. Port of pi/packages/ai/src/utils/event-stream.ts."""

from __future__ import annotations

import asyncio
import queue
import threading
from typing import AsyncIterator, Generic, Iterator, TypeVar

from appv22.ai.types import AssistantMessage, AssistantMessageEvent

T = TypeVar("T")
R = TypeVar("R")

_SENTINEL = object()


class EventStream(Generic[T, R]):
    """A queue-backed stream of events that resolves to a single result."""

    def __init__(self) -> None:
        self._queue: "queue.Queue[object]" = queue.Queue()
        self._done = threading.Event()
        self._result: R | None = None
        self._error: BaseException | None = None

    def push(self, event: T) -> None:
        self._queue.put(event)

    def end(self, result: R | None = None) -> None:
        if self._done.is_set():
            return
        self._result = result
        self._done.set()
        self._queue.put(_SENTINEL)

    def fail(self, error: BaseException) -> None:
        if self._done.is_set():
            return
        self._error = error
        self._done.set()
        self._queue.put(_SENTINEL)

    def __iter__(self) -> Iterator[T]:
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                return
            yield item  # type: ignore[misc]

    async def __aiter__(self) -> AsyncIterator[T]:
        while True:
            item = await asyncio.to_thread(self._queue.get)
            if item is _SENTINEL:
                return
            yield item  # type: ignore[misc]

    def result_sync(self) -> R:
        self._done.wait()
        if self._error is not None:
            raise self._error
        return self._result  # type: ignore[return-value]

    async def result(self) -> R:
        await asyncio.to_thread(self._done.wait)
        if self._error is not None:
            raise self._error
        return self._result  # type: ignore[return-value]


class AssistantMessageEventStream(EventStream[AssistantMessageEvent, AssistantMessage]):
    """Completes on a `done` or `error` event with the final AssistantMessage."""

    def push(self, event: AssistantMessageEvent) -> None:
        super().push(event)
        if event.type == "done":
            self.end(event.message)
        elif event.type == "error":
            self.end(event.error)


def create_assistant_message_event_stream() -> AssistantMessageEventStream:
    return AssistantMessageEventStream()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ai_event_stream.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add appV2.2/appv22/ai/event_stream.py appV2.2/tests/test_ai_event_stream.py
git commit -m "feat(ai): port AssistantMessageEventStream into appv22/ai"
```

---

## Task 3: api-registry + stream entrypoints (`ai/stream.py`)

**Files:**
- Create: `appV2.2/appv22/ai/stream.py`
- Test: `appV2.2/tests/test_ai_stream.py`

**Interfaces:**
- Consumes: `Model`, `Context`, `StreamOptions`, `SimpleStreamOptions`, `AssistantMessage` from `appv22.ai.types`; `AssistantMessageEventStream` from `appv22.ai.event_stream`.
- Produces: `ApiProvider` dataclass (`api: str`, `stream: Callable`, `stream_simple: Callable`); `register_api_provider(provider)`, `get_api_provider(api)`, `reset_api_providers()`; `stream(model, context, options=None)`, `stream_simple(model, context, options=None)` returning `AssistantMessageEventStream`; `async complete(...)`, `async complete_simple(...)`; sync helpers `complete_sync(...)`, `complete_simple_sync(...)`.

- [ ] **Step 1: Write the failing test** — `appV2.2/tests/test_ai_stream.py`

```python
from __future__ import annotations

import pytest

from appv22.ai.event_stream import create_assistant_message_event_stream
from appv22.ai.stream import (
    ApiProvider,
    complete_simple_sync,
    get_api_provider,
    register_api_provider,
    reset_api_providers,
    stream,
    stream_simple,
)
from appv22.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    Model,
    StartEvent,
    TextContent,
    UserMessage,
    empty_usage,
    now_ms,
)


def _model(api: str = "faux") -> Model:
    return Model(id="m", name="m", api=api, provider="faux", base_url="")


def _provider(api: str = "faux") -> ApiProvider:
    def _stream(model, context, options=None):
        s = create_assistant_message_event_stream()
        msg = AssistantMessage(
            content=[TextContent(text="ok")],
            api=model.api,
            provider=model.provider,
            model=model.id,
            usage=empty_usage(),
            stop_reason="stop",
            timestamp=now_ms(),
        )
        s.push(StartEvent(partial=msg))
        s.push(DoneEvent(reason="stop", message=msg))
        return s

    return ApiProvider(api=api, stream=_stream, stream_simple=_stream)


def setup_function() -> None:
    reset_api_providers()


def test_register_and_get_provider() -> None:
    p = _provider()
    register_api_provider(p)
    assert get_api_provider("faux") is p


def test_get_unknown_provider_raises() -> None:
    with pytest.raises(KeyError):
        get_api_provider("nope")


def test_stream_routes_to_provider_by_model_api() -> None:
    register_api_provider(_provider())
    result = stream(_model(), Context(messages=[UserMessage(content="q", timestamp=now_ms())])).result_sync()
    assert result.content[0].text == "ok"


def test_complete_simple_sync() -> None:
    register_api_provider(_provider())
    msg = complete_simple_sync(_model(), Context(messages=[UserMessage(content="q", timestamp=now_ms())]))
    assert msg.stop_reason == "stop"
    _ = stream_simple  # referenced for import coverage
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ai_stream.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'appv22.ai.stream'`.

- [ ] **Step 3: Write minimal implementation** — `appV2.2/appv22/ai/stream.py`

```python
"""Stream entrypoints + api-registry. Port of stream.ts + api-registry.ts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable

from appv22.ai.event_stream import AssistantMessageEventStream
from appv22.ai.types import (
    AssistantMessage,
    Context,
    Model,
    SimpleStreamOptions,
    StreamOptions,
)

StreamFn = Callable[[Model, Context, "StreamOptions | None"], AssistantMessageEventStream]
SimpleStreamFn = Callable[[Model, Context, "SimpleStreamOptions | None"], AssistantMessageEventStream]


@dataclass
class ApiProvider:
    api: str
    stream: StreamFn
    stream_simple: SimpleStreamFn


_API_PROVIDERS: dict[str, ApiProvider] = {}


def register_api_provider(provider: ApiProvider) -> None:
    _API_PROVIDERS[provider.api] = provider


def get_api_provider(api: str) -> ApiProvider:
    provider = _API_PROVIDERS.get(api)
    if provider is None:
        raise KeyError(f"No api provider registered for api '{api}'")
    return provider


def reset_api_providers() -> None:
    _API_PROVIDERS.clear()


def stream(model: Model, context: Context, options: StreamOptions | None = None) -> AssistantMessageEventStream:
    return get_api_provider(model.api).stream(model, context, options)


def stream_simple(
    model: Model, context: Context, options: SimpleStreamOptions | None = None
) -> AssistantMessageEventStream:
    return get_api_provider(model.api).stream_simple(model, context, options)


async def complete(model: Model, context: Context, options: StreamOptions | None = None) -> AssistantMessage:
    return await stream(model, context, options).result()


async def complete_simple(
    model: Model, context: Context, options: SimpleStreamOptions | None = None
) -> AssistantMessage:
    return await stream_simple(model, context, options).result()


def complete_sync(model: Model, context: Context, options: StreamOptions | None = None) -> AssistantMessage:
    return stream(model, context, options).result_sync()


def complete_simple_sync(
    model: Model, context: Context, options: SimpleStreamOptions | None = None
) -> AssistantMessage:
    return stream_simple(model, context, options).result_sync()


_ = asyncio  # keep asyncio import meaningful if later helpers need it
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ai_stream.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add appV2.2/appv22/ai/stream.py appV2.2/tests/test_ai_stream.py
git commit -m "feat(ai): add api-registry + stream/complete entrypoints"
```

---

## Task 4: Faux provider (`ai/providers/faux.py`)

**Files:**
- Create: `appV2.2/appv22/ai/providers/__init__.py` (empty)
- Create: `appV2.2/appv22/ai/providers/faux.py`
- Test: `appV2.2/tests/test_ai_faux.py`

**Interfaces:**
- Consumes: types from `appv22.ai.types`; `AssistantMessageEventStream` from `appv22.ai.event_stream`; `ApiProvider` from `appv22.ai.stream`.
- Produces: `faux_model(api="faux")`; `text_response_events(model, text)`; `tool_call_response_events(model, tool_name, arguments, call_id="call_1")`; `create_faux_provider(script)` where `script: Callable[[Model, Context], list[AssistantMessageEvent]]`; convenience `register_faux_text(text)`/`register_faux_tool_call(...)` returning the registered `Model`.

- [ ] **Step 1: Write the failing test** — `appV2.2/tests/test_ai_faux.py`

```python
from __future__ import annotations

from appv22.ai.providers.faux import (
    create_faux_provider,
    faux_model,
    text_response_events,
    tool_call_response_events,
)
from appv22.ai.stream import register_api_provider, reset_api_providers, stream
from appv22.ai.types import Context, UserMessage, now_ms


def setup_function() -> None:
    reset_api_providers()


def _ctx() -> Context:
    return Context(messages=[UserMessage(content="q", timestamp=now_ms())])


def test_faux_text_response_event_sequence() -> None:
    model = faux_model()
    register_api_provider(create_faux_provider(lambda m, c: text_response_events(m, "hello world")))
    s = stream(model, _ctx())
    types = [e.type for e in s]
    assert types[0] == "start"
    assert types[-1] == "done"
    assert "text_delta" in types
    assert s.result_sync().content[0].text == "hello world"


def test_faux_tool_call_response() -> None:
    model = faux_model()
    register_api_provider(
        create_faux_provider(lambda m, c: tool_call_response_events(m, "read", {"path": "a.txt"}))
    )
    msg = stream(model, _ctx()).result_sync()
    assert msg.stop_reason == "toolUse"
    tool_call = msg.content[0]
    assert tool_call.type == "toolCall"
    assert tool_call.name == "read"
    assert tool_call.arguments == {"path": "a.txt"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ai_faux.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'appv22.ai.providers'`.

- [ ] **Step 3: Write implementation** — `appV2.2/appv22/ai/providers/__init__.py` (empty file) and `appV2.2/appv22/ai/providers/faux.py`

```python
"""Scripted faux provider for tests. Port of pi/packages/ai/src/providers/faux.ts."""

from __future__ import annotations

import json
from typing import Callable

from appv22.ai.event_stream import AssistantMessageEventStream, create_assistant_message_event_stream
from appv22.ai.stream import ApiProvider
from appv22.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    DoneEvent,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolCall,
    ToolcallDeltaEvent,
    ToolcallEndEvent,
    ToolcallStartEvent,
    Usage,
    empty_usage,
    now_ms,
)

FauxScript = Callable[[Model, Context], "list[AssistantMessageEvent]"]


def faux_model(api: str = "faux") -> Model:
    return Model(id="faux-model", name="Faux", api=api, provider="faux", base_url="")


def _blank_message(model: Model) -> AssistantMessage:
    return AssistantMessage(
        content=[],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="stop",
        timestamp=now_ms(),
    )


def text_response_events(model: Model, text: str) -> list[AssistantMessageEvent]:
    msg = _blank_message(model)
    msg.content = [TextContent(text="")]
    events: list[AssistantMessageEvent] = [
        StartEvent(partial=msg),
        TextStartEvent(content_index=0, partial=msg),
    ]
    chunks = [text[i : i + 4] for i in range(0, len(text), 4)] or [""]
    for chunk in chunks:
        msg.content[0].text += chunk
        events.append(TextDeltaEvent(content_index=0, delta=chunk, partial=msg))
    events.append(TextEndEvent(content_index=0, content=text, partial=msg))
    final = _blank_message(model)
    final.content = [TextContent(text=text)]
    events.append(DoneEvent(reason="stop", message=final))
    return events


def tool_call_response_events(
    model: Model, tool_name: str, arguments: dict, call_id: str = "call_1"
) -> list[AssistantMessageEvent]:
    msg = _blank_message(model)
    partial_call = ToolCall(id=call_id, name=tool_name, arguments={})
    msg.content = [partial_call]
    payload = json.dumps(arguments)
    events: list[AssistantMessageEvent] = [
        StartEvent(partial=msg),
        ToolcallStartEvent(content_index=0, partial=msg),
        ToolcallDeltaEvent(content_index=0, delta=payload, partial=msg),
        ToolcallEndEvent(
            content_index=0,
            tool_call=ToolCall(id=call_id, name=tool_name, arguments=arguments),
            partial=msg,
        ),
    ]
    final = _blank_message(model)
    final.stop_reason = "toolUse"
    final.content = [ToolCall(id=call_id, name=tool_name, arguments=arguments)]
    events.append(DoneEvent(reason="toolUse", message=final))
    return events


def create_faux_provider(script: FauxScript, api: str = "faux") -> ApiProvider:
    def _stream(model: Model, context: Context, options=None) -> AssistantMessageEventStream:
        s = create_assistant_message_event_stream()
        for event in script(model, context):
            s.push(event)
        return s

    return ApiProvider(api=api, stream=_stream, stream_simple=_stream)


_ = Usage  # exported type kept available for test authors
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ai_faux.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add appV2.2/appv22/ai/providers/__init__.py appV2.2/appv22/ai/providers/faux.py appV2.2/tests/test_ai_faux.py
git commit -m "feat(ai): add scripted faux provider for tests"
```

---

## Task 5: Model registry + cost (`ai/models.py`)

**Files:**
- Create: `appV2.2/appv22/ai/models.py`
- Test: `appV2.2/tests/test_ai_models.py`

**Interfaces:**
- Consumes: `Model`, `Usage`, `Cost` from `appv22.ai.types`.
- Produces: `register_model(model)`, `get_model(provider, model_id)`, `get_models(provider)`, `get_providers()`, `reset_models()`, `calculate_cost(model, usage_tokens)` returning a `Cost` (per-million-token pricing: input/output/cache_read/cache_write, total = sum).

- [ ] **Step 1: Write the failing test** — `appV2.2/tests/test_ai_models.py`

```python
from __future__ import annotations

from appv22.ai.models import (
    calculate_cost,
    get_model,
    get_models,
    get_providers,
    register_model,
    reset_models,
)
from appv22.ai.types import Cost, Model


def setup_function() -> None:
    reset_models()


def _model() -> Model:
    return Model(
        id="m1",
        name="M1",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        cost=Cost(input=1.0, output=2.0, cache_read=0.5, cache_write=0.0),
        context_window=128000,
        max_tokens=8192,
    )


def test_register_and_lookup() -> None:
    m = _model()
    register_model(m)
    assert get_model("openrouter", "m1") is m
    assert get_models("openrouter") == [m]
    assert get_providers() == ["openrouter"]


def test_get_unknown_model_returns_none() -> None:
    assert get_model("openrouter", "missing") is None


def test_calculate_cost_per_million_tokens() -> None:
    m = _model()
    cost = calculate_cost(m, {"input": 1_000_000, "output": 500_000, "cache_read": 2_000_000, "cache_write": 0})
    assert cost.input == 1.0
    assert cost.output == 1.0
    assert cost.cache_read == 1.0
    assert cost.total == 3.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ai_models.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'appv22.ai.models'`.

- [ ] **Step 3: Write implementation** — `appV2.2/appv22/ai/models.py`

```python
"""Model registry + cost. Port of pi/packages/ai/src/models.ts (minimal subset)."""

from __future__ import annotations

from appv22.ai.types import Cost, Model

_MODELS: dict[str, dict[str, Model]] = {}


def register_model(model: Model) -> None:
    _MODELS.setdefault(model.provider, {})[model.id] = model


def get_model(provider: str, model_id: str) -> Model | None:
    return _MODELS.get(provider, {}).get(model_id)


def get_models(provider: str) -> list[Model]:
    return list(_MODELS.get(provider, {}).values())


def get_providers() -> list[str]:
    return list(_MODELS.keys())


def reset_models() -> None:
    _MODELS.clear()


def calculate_cost(model: Model, usage_tokens: dict[str, int]) -> Cost:
    """Cost from per-million-token pricing on the model."""
    per_million = lambda tokens, rate: (tokens / 1_000_000.0) * rate
    cost = Cost(
        input=per_million(usage_tokens.get("input", 0), model.cost.input),
        output=per_million(usage_tokens.get("output", 0), model.cost.output),
        cache_read=per_million(usage_tokens.get("cache_read", 0), model.cost.cache_read),
        cache_write=per_million(usage_tokens.get("cache_write", 0), model.cost.cache_write),
    )
    cost.total = cost.input + cost.output + cost.cache_read + cost.cache_write
    return cost
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ai_models.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add appV2.2/appv22/ai/models.py appV2.2/tests/test_ai_models.py
git commit -m "feat(ai): add model registry + calculate_cost"
```

---

## Task 6: Context-overflow detection (`ai/overflow.py`)

**Files:**
- Create: `appV2.2/appv22/ai/overflow.py`
- Modify: `appV2.2/appv22/runtime/provider_errors.py` (re-export from the new module; keep `is_context_overflow_error` name working)
- Test: `appV2.2/tests/test_ai_overflow.py`

**Interfaces:**
- Produces: `is_context_overflow(error)` (pi name) — ports the regex patterns from `runtime/provider_errors.py` verbatim. `runtime/provider_errors.py` keeps exporting `is_context_overflow_error` as an alias so existing runtime call sites are unaffected.

- [ ] **Step 1: Write the failing test** — `appV2.2/tests/test_ai_overflow.py`

```python
from __future__ import annotations

from appv22.ai.overflow import is_context_overflow
from appv22.runtime.provider_errors import is_context_overflow_error


def test_detects_overflow_messages() -> None:
    assert is_context_overflow("This prompt is too long for the model")
    assert is_context_overflow("context_length_exceeded")
    assert is_context_overflow("input token count of 200000 exceeds the maximum")


def test_ignores_rate_limit_and_throttling() -> None:
    assert not is_context_overflow("Throttling error: slow down")
    assert not is_context_overflow("rate limit reached, too many requests")
    assert not is_context_overflow("")


def test_runtime_alias_still_works() -> None:
    assert is_context_overflow_error("exceeds the context window") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ai_overflow.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'appv22.ai.overflow'`.

- [ ] **Step 3: Write implementation** — `appV2.2/appv22/ai/overflow.py`

Copy the `_OVERFLOW_PATTERNS`, `_NON_OVERFLOW_PATTERNS`, and `_error_text` bodies verbatim from `appV2.2/appv22/runtime/provider_errors.py` (do not change the regexes), exposing the pi name:

```python
"""Context-overflow detection. Port of pi overflow.ts + appv22 provider_errors.py."""

from __future__ import annotations

import re
from typing import Any

_OVERFLOW_PATTERNS = (
    re.compile(r"prompt is too long", re.I),
    re.compile(r"request_too_large", re.I),
    re.compile(r"input is too long for requested model", re.I),
    re.compile(r"exceeds the context window", re.I),
    re.compile(r"exceeds (?:the )?(?:model'?s )?maximum context length(?: of [\d,]+ tokens?|\s*\([\d,]+\))?", re.I),
    re.compile(r"input token count.*exceeds the maximum", re.I),
    re.compile(r"maximum prompt length is \d+", re.I),
    re.compile(r"reduce the length of the messages", re.I),
    re.compile(r"maximum context length is \d+ tokens", re.I),
    re.compile(r"exceeds (?:the )?maximum allowed input length of [\d,]+ tokens?", re.I),
    re.compile(r"input \(\d+ tokens\) is longer than the model'?s context length \(\d+ tokens\)", re.I),
    re.compile(r"exceeds the limit of \d+", re.I),
    re.compile(r"exceeds the available context size", re.I),
    re.compile(r"greater than the context length", re.I),
    re.compile(r"context window exceeds limit", re.I),
    re.compile(r"exceeded model token limit", re.I),
    re.compile(r"too large for model with \d+ maximum context length", re.I),
    re.compile(r"model_context_window_exceeded", re.I),
    re.compile(r"prompt too long; exceeded (?:max )?context length", re.I),
    re.compile(r"context[_ ]length[_ ]exceeded", re.I),
    re.compile(r"too many tokens", re.I),
    re.compile(r"token limit exceeded", re.I),
    re.compile(r"^4(?:00|13)\s*(?:status code)?\s*\(no body\)", re.I),
)

_NON_OVERFLOW_PATTERNS = (
    re.compile(r"^(Throttling error|Service unavailable):", re.I),
    re.compile(r"rate limit", re.I),
    re.compile(r"too many requests", re.I),
)


def is_context_overflow(error: "BaseException | Any") -> bool:
    text = _error_text(error)
    if not text:
        return False
    if any(pattern.search(text) for pattern in _NON_OVERFLOW_PATTERNS):
        return False
    return any(pattern.search(text) for pattern in _OVERFLOW_PATTERNS)


def _error_text(error: "BaseException | Any") -> str:
    parts = [str(error)]
    for attr in ("message", "error", "body", "response"):
        value = getattr(error, attr, None)
        if value:
            parts.append(str(value))
    status_code = getattr(error, "status_code", None) or getattr(error, "status", None)
    if status_code in {400, 413} and len(" ".join(parts).strip()) <= 16:
        parts.append(f"{status_code} status code (no body)")
    return " ".join(part for part in parts if part).strip()
```

- [ ] **Step 4: Replace `appV2.2/appv22/runtime/provider_errors.py` body with a re-export**

```python
"""Backward-compatible alias. Real implementation lives in appv22.ai.overflow."""

from __future__ import annotations

from appv22.ai.overflow import is_context_overflow

is_context_overflow_error = is_context_overflow

__all__ = ["is_context_overflow", "is_context_overflow_error"]
```

- [ ] **Step 5: Run tests to verify pass (incl. no runtime regression)**

Run: `python -m pytest tests/test_ai_overflow.py tests/test_runtime_protection.py -q`
Expected: PASS (overflow tests pass; runtime suite unaffected).

- [ ] **Step 6: Commit**

```bash
git add appV2.2/appv22/ai/overflow.py appV2.2/appv22/runtime/provider_errors.py appV2.2/tests/test_ai_overflow.py
git commit -m "feat(ai): port is_context_overflow; alias runtime provider_errors"
```

---

## Task 7: Fresh env config (`ai/env_config.py`)

**Files:**
- Create: `appV2.2/appv22/ai/env_config.py`
- Test: `appV2.2/tests/test_ai_env_config.py`

**Interfaces:**
- Produces: `ModelConfig` dataclass (`enabled, api_key, model, base_url, timeout_seconds, temperature, top_p, frequency_penalty, presence_penalty, seed, stop, provider_sort, max_tokens`); `load_dotenv_values(path=".env")`; `load_model_config(prefix, dotenv_path=".env")`. This is a fresh port of appv21's `env_config.py` resolution rules — NO `from appv21` import.

Notes: resolution mirrors appv21 — prefix-specific keys override `OPENROUTER_*`/`OPENAI_*` fallbacks; `os.environ` overrides `.env`; default base_url `https://openrouter.ai/api/v1`; default `APPV2_WORKER_LLM` model `xiaomi/mimo-v2.5-pro`.

- [ ] **Step 1: Write the failing test** — `appV2.2/tests/test_ai_env_config.py`

```python
from __future__ import annotations

from pathlib import Path

from appv22.ai.env_config import load_dotenv_values, load_model_config


def test_load_dotenv_values_strips_quotes_and_comments(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        'APPV2_WORKER_LLM_API_KEY="secret"  # inline comment\n'
        "APPV2_WORKER_LLM_MODEL=acme/model-x\n"
        "# full comment line\n",
        encoding="utf-8",
    )
    values = load_dotenv_values(env)
    assert values["APPV2_WORKER_LLM_API_KEY"] == "secret"
    assert values["APPV2_WORKER_LLM_MODEL"] == "acme/model-x"


def test_load_model_config_resolves_prefix_then_fallbacks(tmp_path: Path, monkeypatch) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "APPV2_WORKER_LLM_ENABLED=true\n"
        "OPENROUTER_API_KEY=fallback-key\n"
        "APPV2_WORKER_LLM_MODEL=acme/model-x\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("APPV2_WORKER_LLM_API_KEY", raising=False)
    config = load_model_config("APPV2_WORKER_LLM", env)
    assert config.enabled is True
    assert config.api_key == "fallback-key"
    assert config.model == "acme/model-x"
    assert config.base_url == "https://openrouter.ai/api/v1"


def test_disabled_when_flag_absent(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("OPENROUTER_API_KEY=k\n", encoding="utf-8")
    config = load_model_config("APPV2_WORKER_LLM", env)
    assert config.enabled is False
    assert config.model == "xiaomi/mimo-v2.5-pro"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ai_env_config.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'appv22.ai.env_config'`.

- [ ] **Step 3: Write implementation** — `appV2.2/appv22/ai/env_config.py`

```python
"""Fresh env config for the appv22 ai provider (no appv21)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

TRUE_VALUES = {"1", "true", "yes", "on"}
COMMON_KEYS = (
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_PROVIDER_SORT",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
)
SUFFIXES = (
    "ENABLED", "API_KEY", "MODEL", "BASE_URL", "TIMEOUT_SECONDS", "TEMPERATURE",
    "TOP_P", "FREQUENCY_PENALTY", "PRESENCE_PENALTY", "SEED", "STOP",
    "PROVIDER_SORT", "MAX_TOKENS",
)


@dataclass(frozen=True)
class ModelConfig:
    enabled: bool
    api_key: str | None
    model: str | None
    base_url: str
    timeout_seconds: float
    temperature: float
    top_p: float | None
    frequency_penalty: float | None
    presence_penalty: float | None
    seed: int | None
    stop: list[str] = field(default_factory=list)
    provider_sort: str | None = "latency"
    max_tokens: int | None = None


def load_dotenv_values(path: "str | Path" = ".env") -> dict[str, str]:
    dotenv_path = Path(path)
    if not dotenv_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = _strip_inline_comment(value.strip())
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_model_config(prefix: str, dotenv_path: "str | Path" = ".env") -> ModelConfig:
    config = load_dotenv_values(dotenv_path)
    for key in (*COMMON_KEYS, *(f"{prefix}_{suffix}" for suffix in SUFFIXES)):
        if key in os.environ:
            config[key] = os.environ[key]
    enabled = config.get(f"{prefix}_ENABLED", "").lower() in TRUE_VALUES
    api_key = config.get(f"{prefix}_API_KEY") or config.get("OPENROUTER_API_KEY") or config.get("OPENAI_API_KEY")
    model = (
        config.get(f"{prefix}_MODEL")
        or config.get("OPENROUTER_MODEL")
        or config.get("OPENAI_MODEL")
        or _default_model(prefix)
    )
    return ModelConfig(
        enabled=enabled,
        api_key=api_key,
        model=model,
        base_url=config.get(f"{prefix}_BASE_URL") or config.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1",
        timeout_seconds=float(config.get(f"{prefix}_TIMEOUT_SECONDS", "60")),
        temperature=float(config.get(f"{prefix}_TEMPERATURE", "0")),
        top_p=_optional_float(config.get(f"{prefix}_TOP_P")),
        frequency_penalty=_optional_float(config.get(f"{prefix}_FREQUENCY_PENALTY")),
        presence_penalty=_optional_float(config.get(f"{prefix}_PRESENCE_PENALTY")),
        seed=_optional_int(config.get(f"{prefix}_SEED")),
        stop=_optional_list(config.get(f"{prefix}_STOP")),
        provider_sort=config.get(f"{prefix}_PROVIDER_SORT") or config.get("OPENROUTER_PROVIDER_SORT") or "latency",
        max_tokens=_optional_int(config.get(f"{prefix}_MAX_TOKENS")),
    )


def _default_model(prefix: str) -> str | None:
    return "xiaomi/mimo-v2.5-pro" if prefix == "APPV2_WORKER_LLM" else None


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    for index, char in enumerate(value):
        if char in {"'", '"'}:
            quote = None if quote == char else char
        if char == "#" and quote is None and index > 0 and value[index - 1].isspace():
            return value[:index].strip()
    return value


def _optional_int(value: str | None) -> int | None:
    if value is None or value == "" or value.lower() in {"none", "null"}:
        return None
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("max token settings must be positive or blank.")
    return parsed


def _optional_float(value: str | None) -> float | None:
    if value is None or value == "" or value.lower() in {"none", "null"}:
        return None
    return float(value)


def _optional_list(value: str | None) -> list[str]:
    if value is None or value == "" or value.lower() in {"none", "null"}:
        return []
    stripped = value.strip()
    if stripped.startswith("["):
        parsed = json.loads(stripped)
        if not isinstance(parsed, list):
            raise ValueError("Stop setting must be a JSON array or comma-separated list.")
        return [str(item) for item in parsed]
    return [item.strip() for item in stripped.split(",") if item.strip()]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ai_env_config.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add appV2.2/appv22/ai/env_config.py appV2.2/tests/test_ai_env_config.py
git commit -m "feat(ai): fresh env_config port (no appv21)"
```

---

## Task 8: appv2-env provider with real SSE (`ai/providers/appv2_env.py`)

**Files:**
- Create: `appV2.2/appv22/ai/providers/appv2_env.py`
- Test: `appV2.2/tests/test_ai_appv2_env_provider.py`

**Interfaces:**
- Consumes: types from `appv22.ai.types`; `AssistantMessageEventStream` from `appv22.ai.event_stream`; `ApiProvider` from `appv22.ai.stream`; `ModelConfig` from `appv22.ai.env_config`.
- Produces:
  - `convert_messages(context)` → `(messages: list[dict], tools: list[dict] | None)` OpenAI/OpenRouter chat payload.
  - `parse_sse_chunks(lines, model)` → generator of `AssistantMessageEvent` (pure function over decoded SSE `data:` lines; no network; emits `start` lazily on first content). This is the unit-tested core.
  - `AppV2EnvProvider(config)` with `.api = "openai-completions"`, `.stream()` / `.stream_simple()` returning `AssistantMessageEventStream` (runs the httpx request in a worker thread, feeding `parse_sse_chunks`).
  - `NullProvider(api="openai-completions")` whose `.stream()` emits a single `error` event.

The SSE parser is the testable heart; the httpx call is a thin driver around it.

- [ ] **Step 1: Write the failing test** — `appV2.2/tests/test_ai_appv2_env_provider.py`

```python
from __future__ import annotations

import json

from appv22.ai.providers.appv2_env import (
    NullProvider,
    convert_messages,
    parse_sse_chunks,
)
from appv22.ai.types import (
    AssistantMessage,
    Context,
    Model,
    TextContent,
    Tool,
    ToolResultMessage,
    UserMessage,
    empty_usage,
    now_ms,
)


def _model() -> Model:
    return Model(id="acme/x", name="X", api="openai-completions", provider="openrouter", base_url="")


def test_convert_messages_maps_roles_and_tools() -> None:
    ctx = Context(
        system_prompt="sys",
        messages=[
            UserMessage(content="hello", timestamp=now_ms()),
            ToolResultMessage(
                tool_call_id="c1", tool_name="read",
                content=[TextContent(text="file body")], is_error=False, timestamp=now_ms(),
            ),
        ],
        tools=[Tool(name="read", description="read", parameters={"type": "object"})],
    )
    messages, tools = convert_messages(ctx)
    assert messages[0] == {"role": "system", "content": "sys"}
    assert messages[1] == {"role": "user", "content": "hello"}
    assert messages[2]["role"] == "tool"
    assert messages[2]["tool_call_id"] == "c1"
    assert tools[0]["type"] == "function"
    assert tools[0]["function"]["name"] == "read"


def _sse(obj: dict) -> str:
    return "data: " + json.dumps(obj)


def test_parse_sse_text_stream() -> None:
    lines = [
        _sse({"choices": [{"delta": {"content": "Hel"}}]}),
        _sse({"choices": [{"delta": {"content": "lo"}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        "data: [DONE]",
    ]
    events = list(parse_sse_chunks(lines, _model()))
    types = [e.type for e in events]
    assert types[0] == "start"
    assert "text_delta" in types
    assert types[-1] == "done"
    final = events[-1].message
    assert final.content[0].text == "Hello"
    assert final.stop_reason == "stop"


def test_parse_sse_tool_call_stream() -> None:
    lines = [
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "read", "arguments": ""}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": "{\"path\":"}}]}}]}),
        _sse({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": " \"a.txt\"}"}}]}}]}),
        _sse({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}),
        "data: [DONE]",
    ]
    events = list(parse_sse_chunks(lines, _model()))
    assert events[-1].type == "done"
    assert events[-1].reason == "toolUse"
    tool_call = events[-1].message.content[0]
    assert tool_call.type == "toolCall"
    assert tool_call.name == "read"
    assert tool_call.arguments == {"path": "a.txt"}


def test_null_provider_emits_error_event() -> None:
    s = NullProvider().stream(_model(), Context(messages=[]))
    events = list(s)
    assert events[-1].type == "error"
    msg = s.result_sync()
    assert isinstance(msg, AssistantMessage)
    assert msg.stop_reason == "error"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ai_appv2_env_provider.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'appv22.ai.providers.appv2_env'`.

- [ ] **Step 3: Write implementation** — `appV2.2/appv22/ai/providers/appv2_env.py`

```python
"""appv2-env provider: OpenAI/OpenRouter-compatible streaming over httpx SSE."""

from __future__ import annotations

import json
import threading
from typing import Iterable, Iterator

import httpx

from appv22.ai.env_config import ModelConfig, load_model_config
from appv22.ai.event_stream import AssistantMessageEventStream, create_assistant_message_event_stream
from appv22.ai.stream import ApiProvider
from appv22.ai.types import (
    AssistantMessage,
    Context,
    DoneEvent,
    ErrorEvent,
    Message,
    Model,
    StartEvent,
    TextContent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ThinkingContent,
    ThinkingDeltaEvent,
    ThinkingStartEvent,
    ToolCall,
    ToolcallDeltaEvent,
    ToolcallEndEvent,
    ToolcallStartEvent,
    Usage,
    empty_usage,
    now_ms,
)

PROVIDER_API = "openai-completions"

_FINISH_REASON_MAP = {"stop": "stop", "length": "length", "tool_calls": "toolUse"}


def convert_messages(context: Context) -> "tuple[list[dict], list[dict] | None]":
    messages: list[dict] = []
    if context.system_prompt:
        messages.append({"role": "system", "content": context.system_prompt})
    for message in context.messages:
        messages.append(_convert_message(message))
    tools = None
    if context.tools:
        tools = [
            {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}
            for t in context.tools
        ]
    return messages, tools


def _convert_message(message: Message) -> dict:
    if message.role == "user":
        content = message.content if isinstance(message.content, str) else _text_of(message.content)
        return {"role": "user", "content": content}
    if message.role == "toolResult":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "name": message.tool_name,
            "content": _text_of(message.content),
        }
    # assistant
    text_parts = [b.text for b in message.content if isinstance(b, TextContent)]
    tool_calls = [
        {"id": b.id, "type": "function", "function": {"name": b.name, "arguments": json.dumps(b.arguments)}}
        for b in message.content
        if isinstance(b, ToolCall)
    ]
    out: dict = {"role": "assistant", "content": "".join(text_parts)}
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.text for b in content if isinstance(b, TextContent))


def _blank(model: Model) -> AssistantMessage:
    return AssistantMessage(
        content=[], api=model.api, provider=model.provider, model=model.id,
        usage=empty_usage(), stop_reason="stop", timestamp=now_ms(),
    )


def _iter_sse_data(lines: Iterable[str]) -> Iterator[str]:
    for raw in lines:
        line = raw.strip()
        if not line or not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if payload == "[DONE]":
            return
        yield payload


def parse_sse_chunks(lines: Iterable[str], model: Model) -> Iterator:
    """Pure transform: decoded SSE lines -> AssistantMessageEvent stream."""
    message = _blank(model)
    started = False
    text_index: int | None = None
    text_buf = ""
    thinking_index: int | None = None
    tool_index: int | None = None
    tool_arg_buf = ""
    tool_call: ToolCall | None = None
    finish_reason = "stop"
    usage = empty_usage()

    def ensure_start():
        nonlocal started
        if not started:
            started = True
            return StartEvent(partial=message)
        return None

    for payload in _iter_sse_data(lines):
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        usage = _merge_usage(usage, chunk.get("usage"))
        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta") or {}

        reasoning = delta.get("reasoning")
        if reasoning:
            start = ensure_start()
            if start:
                yield start
            if thinking_index is None:
                thinking_index = len(message.content)
                message.content.append(ThinkingContent(thinking=""))
                yield ThinkingStartEvent(content_index=thinking_index, partial=message)
            message.content[thinking_index].thinking += reasoning
            yield ThinkingDeltaEvent(content_index=thinking_index, delta=reasoning, partial=message)

        content_piece = delta.get("content")
        if content_piece:
            start = ensure_start()
            if start:
                yield start
            if text_index is None:
                text_index = len(message.content)
                message.content.append(TextContent(text=""))
                yield TextStartEvent(content_index=text_index, partial=message)
            text_buf += content_piece
            message.content[text_index].text = text_buf
            yield TextDeltaEvent(content_index=text_index, delta=content_piece, partial=message)

        for tc in delta.get("tool_calls") or []:
            start = ensure_start()
            if start:
                yield start
            if tool_index is None:
                tool_index = len(message.content)
                tool_call = ToolCall(id=tc.get("id") or "call_1", name=(tc.get("function") or {}).get("name") or "", arguments={})
                message.content.append(tool_call)
                yield ToolcallStartEvent(content_index=tool_index, partial=message)
            fn = tc.get("function") or {}
            if fn.get("name") and tool_call is not None and not tool_call.name:
                tool_call.name = fn["name"]
            arg_fragment = fn.get("arguments") or ""
            if arg_fragment:
                tool_arg_buf += arg_fragment
                yield ToolcallDeltaEvent(content_index=tool_index, delta=arg_fragment, partial=message)

        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]

    if text_index is not None:
        yield TextEndEvent(content_index=text_index, content=text_buf, partial=message)
    if tool_index is not None and tool_call is not None:
        try:
            tool_call.arguments = json.loads(tool_arg_buf) if tool_arg_buf else {}
        except json.JSONDecodeError:
            tool_call.arguments = {}
        yield ToolcallEndEvent(content_index=tool_index, tool_call=tool_call, partial=message)

    if not started:
        yield StartEvent(partial=message)
    message.usage = usage
    reason = _FINISH_REASON_MAP.get(finish_reason, "stop")
    message.stop_reason = reason
    yield DoneEvent(reason=reason, message=message)


def _merge_usage(usage: Usage, raw: "dict | None") -> Usage:
    if not raw:
        return usage
    prompt = int(raw.get("prompt_tokens") or 0)
    completion = int(raw.get("completion_tokens") or 0)
    usage.input = prompt or usage.input
    usage.output = completion or usage.output
    usage.total_tokens = int(raw.get("total_tokens") or 0) or usage.total_tokens
    return usage


class AppV2EnvProvider:
    api = PROVIDER_API

    def __init__(self, config: ModelConfig) -> None:
        self.config = config

    def stream(self, model: Model, context: Context, options=None) -> AssistantMessageEventStream:
        s = create_assistant_message_event_stream()
        threading.Thread(target=self._run, args=(s, model, context, options), daemon=True).start()
        return s

    stream_simple = stream

    def _run(self, s: AssistantMessageEventStream, model: Model, context: Context, options) -> None:
        try:
            messages, tools = convert_messages(context)
            body: dict = {
                "model": self.config.model or model.id,
                "messages": messages,
                "stream": True,
                "temperature": self.config.temperature,
            }
            if tools:
                body["tools"] = tools
            if self.config.max_tokens is not None:
                body["max_tokens"] = self.config.max_tokens
            if self.config.provider_sort:
                body["provider"] = {"sort": self.config.provider_sort, "allow_fallbacks": True}
            headers = {"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"}
            url = self.config.base_url.rstrip("/") + "/chat/completions"
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                with client.stream("POST", url, json=body, headers=headers) as response:
                    response.raise_for_status()
                    for event in parse_sse_chunks(response.iter_lines(), model):
                        s.push(event)
        except Exception as exc:  # encode failure as an error event, never raise
            err = _blank(model)
            err.stop_reason = "error"
            err.error_message = str(exc)
            s.push(ErrorEvent(reason="error", error=err))


class NullProvider:
    api = PROVIDER_API

    def stream(self, model: Model, context: Context, options=None) -> AssistantMessageEventStream:
        s = create_assistant_message_event_stream()
        err = _blank(model)
        err.stop_reason = "error"
        err.error_message = "model transport not configured"
        s.push(ErrorEvent(reason="error", error=err))
        return s

    stream_simple = stream


def create_appv2_env_provider(prefix: str = "APPV2_WORKER_LLM", dotenv_path: "str" = ".env") -> ApiProvider:
    config = load_model_config(prefix, dotenv_path)
    impl = AppV2EnvProvider(config) if (config.enabled and config.api_key) else NullProvider()
    return ApiProvider(api=PROVIDER_API, stream=impl.stream, stream_simple=impl.stream_simple)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_ai_appv2_env_provider.py -q`
Expected: PASS (4 passed). (No network — only `convert_messages`, `parse_sse_chunks`, and `NullProvider` are exercised.)

- [ ] **Step 5: Commit**

```bash
git add appV2.2/appv22/ai/providers/appv2_env.py appV2.2/tests/test_ai_appv2_env_provider.py
git commit -m "feat(ai): appv2-env provider with real httpx SSE streaming"
```

---

## Task 9: Builtins registration + public barrel (`ai/register_builtins.py`, `ai/__init__.py`)

**Files:**
- Create: `appV2.2/appv22/ai/register_builtins.py`
- Modify: `appV2.2/appv22/ai/__init__.py` (replace placeholder with the real barrel)
- Test: `appV2.2/tests/test_ai_register_builtins.py`

**Interfaces:**
- Consumes: `create_appv2_env_provider`, `PROVIDER_API` from `appv22.ai.providers.appv2_env`; `register_api_provider`, `get_api_provider` from `appv22.ai.stream`.
- Produces: `register_builtin_providers(prefix="APPV2_WORKER_LLM", dotenv_path=".env")` (registers the appv2-env provider for `openai-completions`); `appv22.ai` barrel re-exporting the public surface (types, event stream, stream entrypoints, models, overflow, faux).

- [ ] **Step 1: Write the failing test** — `appV2.2/tests/test_ai_register_builtins.py`

```python
from __future__ import annotations

import appv22.ai as ai
from appv22.ai.register_builtins import register_builtin_providers
from appv22.ai.stream import get_api_provider, reset_api_providers


def setup_function() -> None:
    reset_api_providers()


def test_register_builtins_registers_openai_completions(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("OPENROUTER_API_KEY=k\n", encoding="utf-8")
    register_builtin_providers(dotenv_path=str(env))
    provider = get_api_provider("openai-completions")
    assert provider.api == "openai-completions"


def test_barrel_reexports_public_surface() -> None:
    assert hasattr(ai, "AssistantMessage")
    assert hasattr(ai, "stream")
    assert hasattr(ai, "stream_simple")
    assert hasattr(ai, "create_assistant_message_event_stream")
    assert hasattr(ai, "is_context_overflow")
    assert hasattr(ai, "calculate_cost")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_ai_register_builtins.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'appv22.ai.register_builtins'`.

- [ ] **Step 3: Write implementation** — `appV2.2/appv22/ai/register_builtins.py`

```python
"""Register built-in api providers. Port of providers/register-builtins.ts."""

from __future__ import annotations

from appv22.ai.providers.appv2_env import create_appv2_env_provider
from appv22.ai.stream import register_api_provider


def register_builtin_providers(prefix: str = "APPV2_WORKER_LLM", dotenv_path: str = ".env") -> None:
    register_api_provider(create_appv2_env_provider(prefix, dotenv_path))
```

- [ ] **Step 4: Write implementation** — replace `appV2.2/appv22/ai/__init__.py` with the barrel

```python
"""appv22 port of pi's `ai` package (provider/model abstraction + streaming)."""

from appv22.ai.event_stream import (
    AssistantMessageEventStream,
    EventStream,
    create_assistant_message_event_stream,
)
from appv22.ai.models import (
    calculate_cost,
    get_model,
    get_models,
    get_providers,
    register_model,
    reset_models,
)
from appv22.ai.overflow import is_context_overflow
from appv22.ai.register_builtins import register_builtin_providers
from appv22.ai.stream import (
    ApiProvider,
    complete,
    complete_simple,
    complete_simple_sync,
    complete_sync,
    get_api_provider,
    register_api_provider,
    reset_api_providers,
    stream,
    stream_simple,
)
from appv22.ai.types import (
    AssistantMessage,
    AssistantMessageEvent,
    Context,
    Cost,
    DoneEvent,
    ErrorEvent,
    ImageContent,
    Message,
    Model,
    SimpleStreamOptions,
    StreamOptions,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    Usage,
    UserMessage,
    empty_usage,
    now_ms,
)

__all__ = [
    "ApiProvider",
    "AssistantMessage",
    "AssistantMessageEvent",
    "AssistantMessageEventStream",
    "Context",
    "Cost",
    "DoneEvent",
    "ErrorEvent",
    "EventStream",
    "ImageContent",
    "Message",
    "Model",
    "SimpleStreamOptions",
    "StreamOptions",
    "TextContent",
    "ThinkingContent",
    "Tool",
    "ToolCall",
    "ToolResultMessage",
    "Usage",
    "UserMessage",
    "calculate_cost",
    "complete",
    "complete_simple",
    "complete_simple_sync",
    "complete_sync",
    "create_assistant_message_event_stream",
    "empty_usage",
    "get_api_provider",
    "get_model",
    "get_models",
    "get_providers",
    "is_context_overflow",
    "now_ms",
    "register_api_provider",
    "register_builtin_providers",
    "register_model",
    "reset_api_providers",
    "reset_models",
    "stream",
    "stream_simple",
]
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_ai_register_builtins.py -q`
Expected: PASS (2 passed).

- [ ] **Step 6: Commit**

```bash
git add appV2.2/appv22/ai/register_builtins.py appV2.2/appv22/ai/__init__.py appV2.2/tests/test_ai_register_builtins.py
git commit -m "feat(ai): register builtins + public ai barrel"
```

---

## Task 10: Remove appv21 — fresh JSON client + repoint legacy shim + grep gate

This deletes all `appv21`/`appV2.1` coupling. The legacy `decide()` shim still
exists (deleted in sub-project 2) but now runs on a fresh appv22 JSON client.

**Files:**
- Create: `appV2.2/appv22/providers/json_client.py` (fresh non-streaming JSON client; httpx; no appv21)
- Modify: `appV2.2/appv22/providers/appv2_env.py` (drop appv21 import/discovery; build from `json_client` + `ai.env_config`)
- Modify: `pyproject.toml` (add `httpx` to `dependencies`)
- Test: `appV2.2/tests/test_no_appv21_coupling.py`

**Interfaces:**
- Produces: `JsonModelClient(config: ModelConfig)` with `complete_json(stage, prompt, schema) -> str` (non-streaming httpx `POST /chat/completions`, `stream: False`, `response_format` json_schema) and `usage_snapshot(reset=False) -> dict`. `create_appv22_provider_from_appv2_env(dotenv_path)` keeps its signature and returns an object exposing `.decide(prompt)` + `.usage_snapshot()`; returns a `NullDecisionProvider` when config is disabled/missing key.

- [ ] **Step 1: Write the failing test** — `appV2.2/tests/test_no_appv21_coupling.py`

```python
from __future__ import annotations

from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]  # appV2.2/


def test_no_appv21_references_in_source() -> None:
    offenders: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        # Allow this guard test itself to mention the token.
        if path.name == "test_no_appv21_coupling.py":
            continue
        if "appv21" in text or "appV2.1" in text:
            offenders.append(str(path.relative_to(APP_ROOT)))
    assert offenders == [], f"appv21 references remain: {offenders}"


def test_legacy_provider_returns_null_when_disabled(tmp_path: Path) -> None:
    from appv22.providers import create_appv22_provider_from_appv2_env

    env = tmp_path / ".env"
    env.write_text("OPENROUTER_API_KEY=k\n", encoding="utf-8")  # not enabled
    provider = create_appv22_provider_from_appv2_env(str(env))
    decision = provider.decide({"selection": {}, "state": {}})
    assert decision.kind == "pause"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_no_appv21_coupling.py -q`
Expected: FAIL — `test_no_appv21_references_in_source` lists `appv22/providers/appv2_env.py`.

- [ ] **Step 3: Write implementation** — `appV2.2/appv22/providers/json_client.py`

```python
"""Fresh non-streaming JSON client for the legacy decide() shim (no appv21)."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

import httpx

from appv22.ai.env_config import ModelConfig


class JsonModelClient:
    """Minimal OpenAI/OpenRouter-compatible JSON-schema completion client."""

    def __init__(self, config: ModelConfig) -> None:
        self._config = config
        self._usage: dict[str, Any] = {"model_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    def usage_snapshot(self, *, reset: bool = False) -> dict[str, Any]:
        snapshot = deepcopy(self._usage)
        if reset:
            self._usage = {"model_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return snapshot

    def complete_json(self, *, stage: str, prompt: str, schema: dict[str, Any]) -> str:
        body: dict[str, Any] = {
            "model": self._config.model,
            "temperature": self._config.temperature,
            "messages": [
                {"role": "system", "content": "Return only JSON matching the supplied schema. No markdown."},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_schema", "json_schema": {"name": f"{stage}_output", "schema": schema, "strict": False}},
            "stream": False,
        }
        if self._config.provider_sort:
            body["provider"] = {"sort": self._config.provider_sort, "allow_fallbacks": True}
        if self._config.max_tokens is not None:
            body["max_tokens"] = self._config.max_tokens
        headers = {"Authorization": f"Bearer {self._config.api_key}", "Content-Type": "application/json"}
        url = self._config.base_url.rstrip("/") + "/chat/completions"
        with httpx.Client(timeout=self._config.timeout_seconds) as client:
            response = client.post(url, json=body, headers=headers)
            response.raise_for_status()
            data = response.json()
        usage = data.get("usage") or {}
        self._usage["model_calls"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            self._usage[key] += int(usage.get(key) or 0)
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return content if isinstance(content, str) else json.dumps(content)
```

- [ ] **Step 4: Rewrite `appV2.2/appv22/providers/appv2_env.py`** — remove appv21; keep `decide()`/schema/normalizer

Replace the top imports and the helper/factory functions that referenced appv21
(`_ensure_local_appv21_import_path`, `_discover_local_appv21_root`,
`import_module("appv21...")`, `null_model`) with the following. Keep
`APPV22_DECISION_SCHEMA`, `AppV22NativeProvider`, the `_appv22_decision_prompt`
family, and `normalize_appv22_decision_payload` exactly as they are now.

Change the imports block at the top to:

```python
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Mapping

from appv22.ai.env_config import load_model_config
from appv22.providers.json_client import JsonModelClient
from appv22.runtime.decisions import KNOWN_DECISION_KINDS, RuntimeDecision
```

Replace `create_appv22_provider_from_appv2_env` and delete the two appv21
discovery helpers entirely, with:

```python
class NullDecisionProvider:
    provider_id = "null-model"

    def decide(self, prompt_payload: dict) -> RuntimeDecision:
        return RuntimeDecision(
            kind="pause",
            reason="No provider is configured for autonomous decisions.",
            payload={"pause_type": "missing_context"},
        )

    def usage_snapshot(self, *, reset: bool = False) -> dict[str, Any]:
        return {}


def create_appv22_provider_from_appv2_env(dotenv_path: "str | Path") -> Any:
    """Create an AppV2.2-native provider from env model settings (no appv21)."""

    config = load_model_config("APPV2_WORKER_LLM", dotenv_path)
    if not (config.enabled and config.api_key):
        return NullDecisionProvider()
    return AppV22NativeProvider(client=JsonModelClient(config))
```

(Confirm `RuntimeDecision(kind="pause", ...)` accepts these fields — it does;
see `appv22/runtime/decisions.py`. The legacy `importlib`/`sys`/`importlib.util`
imports are removed.)

- [ ] **Step 5: Add `httpx` dependency** — `pyproject.toml`

In `[project].dependencies`, add `"httpx>=0.27"` (alongside the existing entries).

```toml
dependencies = [
  "httpx>=0.27",
  "langgraph>=0.6.0",
  "openrouter>=0.9.1",
  "playwright>=1.59",
  "pydantic>=2.0",
]
```

Then sync: `uv sync` (from repo root). Expected: httpx already present (resolves quickly).

- [ ] **Step 6: Run tests to verify pass + no regression**

Run (from `appV2.2/`): `python -m pytest tests/test_no_appv21_coupling.py tests/test_runtime_protection.py tests/test_tui_app.py -q`
Expected: PASS — zero appv21 references; runtime + tui suites still green (legacy shim works on the new transport).

- [ ] **Step 7: Commit**

```bash
git add appV2.2/appv22/providers/json_client.py appV2.2/appv22/providers/appv2_env.py pyproject.toml appV2.2/tests/test_no_appv21_coupling.py
git commit -m "refactor(ai): remove appv21 coupling; repoint legacy decide() shim onto httpx"
```

---

## Final verification (run after all tasks)

- [ ] Full appv22 suite: from `appV2.2/`, `python -m pytest -q` → all green.
- [ ] Grep gate: `rg -n "appv21|appV2\\.1|import pi|import hermes" appV2.2/appv22 appV2.2/appv22_ui` → no matches (rg not available? use `python -m pytest tests/test_no_appv21_coupling.py`).
- [ ] Parity mapping (record in PR / sub-project notes):

| pi `ai` symbol | appv22 symbol |
|---|---|
| `Message`/`AssistantMessage`/`ToolResultMessage`/`UserMessage` | `appv22.ai.types` same names |
| `AssistantMessageEvent` union | `appv22.ai.types.AssistantMessageEvent` |
| `AssistantMessageEventStream` | `appv22.ai.event_stream.AssistantMessageEventStream` |
| `stream`/`complete`/`streamSimple`/`completeSimple` | `stream`/`complete`/`stream_simple`/`complete_simple` |
| `registerApiProvider`/`getApiProvider` | `register_api_provider`/`get_api_provider` |
| `getModel`/`calculateCost` | `get_model`/`calculate_cost` |
| `isContextOverflow` | `is_context_overflow` |
| `providers/faux.ts` | `appv22.ai.providers.faux` |
| `providers/register-builtins.ts` | `appv22.ai.register_builtins` |

---

## Self-Review

**Spec coverage:** every ai-parity spec file/section maps to a task — types (T1),
event_stream (T2), stream+api-registry (T3), faux (T4), models (T5), overflow (T6),
env_config (T7), appv2-env SSE provider + convert_messages + SSE parser + NullProvider
(T8), register_builtins + barrel (T9), appv21 removal + grep gate + httpx dep (T10).

**Placeholder scan:** no TBD/TODO; every code step shows complete code.

**Type consistency:** event `type` literals identical to pi; `AssistantMessageEventStream`
used consistently; `ApiProvider(api, stream, stream_simple)` consistent across T3/T4/T8/T9;
`ModelConfig` fields consistent between T7 and T8/T10; `is_context_overflow` name consistent
T6/T9.

**Deferred to sub-project 2 (documented, not gaps):** rewiring the agent loop onto
`stream_simple`; deleting the `decide()` shim; the async loop driver.
