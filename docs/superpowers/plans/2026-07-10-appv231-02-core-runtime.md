# appv231 Pure Core Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `appv231.agent` a domain-neutral, awaitable Pi-style runtime with serialized lifecycle handling, bounded parallel tools, and explicit failures.

**Architecture:** Migrate coding policy out of core, expose generic iteration-limit injection, make async functions the canonical implementation, retain sync facades for current callers, and use the asyncio loop as the single state/event coordinator. Synchronous tool bodies alone run in a bounded executor.

**Tech Stack:** Python 3.13, asyncio, concurrent futures, inspect/AST, pytest, existing queue-backed `EventStream`.

## Global Constraints

- Complete Plan 1 first; this plan consumes its `RunLease`.
- Do not edit compaction files or perform mutating git operations; read-only status and diff checks are permitted.
- Core may not import coding-agent, TUI, session, compaction policy, provider catalog, or named coding tools.
- Preserve Pi source-order tool-result semantics.
- Existing sync callers remain supported through facades; async callers use explicit async APIs.

---

### Task 1: Enforce the Core Dependency Boundary

**Files:**
- Create: `appV2.3.1/tests/test_agent_core_boundary.py`
- Create: `appV2.3.1/appv231/coding_agent/policies/__init__.py`
- Move: `appV2.3.1/appv231/agent/tool_guardrails.py` to `appV2.3.1/appv231/coding_agent/policies/tool_guardrails.py`
- Delete: `appV2.3.1/appv231/agent/tool_dispatch.py`
- Modify: `appV2.3.1/appv231/agent/__init__.py`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py`
- Modify imports in: `appV2.3.1/tests/test_coding_agent.py`, `appV2.3.1/tests/test_tool_execution_modes.py`

**Interfaces:**
- Produces: `appv231.coding_agent.policies.tool_guardrails`
- Removes: all guardrail exports from `appv231.agent`
- Produces: AST-based architecture test used by every later gate

- [ ] **Step 1: Write the boundary test**

```python
import ast
from pathlib import Path

CORE = Path(__file__).parents[1] / "appv231" / "agent"
FORBIDDEN_PREFIXES = (
    "appv231.coding_agent",
    "appv231.compaction",
    "appv231.tui",
)

def test_agent_core_has_no_domain_imports_or_named_tool_policy():
    violations: list[str] = []
    for path in CORE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(FORBIDDEN_PREFIXES):
                violations.append(f"{path.name}:{node.lineno}:{node.module}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(FORBIDDEN_PREFIXES):
                        violations.append(f"{path.name}:{node.lineno}:{alias.name}")
    assert violations == []

def test_dead_hermes_dispatch_module_is_absent():
    assert not (CORE / "tool_dispatch.py").exists()
```

- [ ] **Step 2: Verify the test fails on current ownership**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_agent_core_boundary.py
```

Expected before migration: `tool_dispatch.py` exists and coding guardrails are exported by core.

- [ ] **Step 3: Move policy without changing behavior**

Move the file, update `AgentSession` and tests atomically, remove guardrail imports/exports from `agent/__init__.py`, and delete the zero-reference dispatcher. Do not refactor the policy in this task.

- [ ] **Step 4: Prove no stale imports remain**

```bash
rg -n "appv231\.agent\.tool_guardrails|agent/tool_guardrails|tool_dispatch" appV2.3.1
```

Expected: no output except migration documentation that intentionally names old paths.

- [ ] **Step 5: Run the boundary and coding-policy tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_agent_core_boundary.py appV2.3.1/tests/test_coding_agent.py -k "guardrail or package_manager or core"
```

Expected: pass.

### Task 2: Extract Iteration-Limit Presentation Policy

**Files:**
- Modify: `appV2.3.1/appv231/agent/types.py:103-149`
- Modify: `appV2.3.1/appv231/agent/agent.py:70-242`
- Modify: `appV2.3.1/appv231/agent/agent_loop.py:153-300`
- Create: `appV2.3.1/appv231/coding_agent/policies/iteration_limit.py`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py`
- Extend: `appV2.3.1/tests/test_agent_loop.py`
- Extend: `appV2.3.1/tests/test_coding_agent.py`

**Interfaces:**
- Produces: `IterationLimitContext(context, api_call_count, max_iterations, signal)`
- Produces: `AgentLoopConfig.on_iteration_limit: Callable[[IterationLimitContext], AgentMessage | None] | None`
- Produces: `coding_iteration_limit_message(context) -> UserMessage`
- Removes: hard-coded summary prose from `agent_loop.py`

- [ ] **Step 1: Rewrite the core test as a policy-injection test**

```python
def test_iteration_limit_uses_injected_message_without_core_summary_text(model):
    seen: list[IterationLimitContext] = []

    def on_limit(context):
        seen.append(context)
        return UserMessage(content="profile final-response request", timestamp=now_ms())

    config = AgentLoopConfig(
        model=model,
        convert_to_llm=_convert,
        max_iterations=1,
        on_iteration_limit=on_limit,
    )
    messages = run_tool_loop_until_limit(config)
    assert seen[0].max_iterations == 1
    assert message_text(messages[-2]) == "profile final-response request"
    assert assistant_tool_calls(messages[-1]) == []
```

Add a second test with `on_iteration_limit=None` asserting clean termination without another provider call.

- [ ] **Step 2: Run and verify the missing interface**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_agent_loop.py -k iteration_limit
```

Expected before repair: `AgentLoopConfig` rejects `on_iteration_limit`.

- [ ] **Step 3: Add the generic callback**

Replace `_request_iteration_summary()` with `_handle_iteration_limit()`. The core emits an injected message and performs at most one final provider request with `tools=[]`; it contains no summary wording.

- [ ] **Step 4: Supply coding-agent wording externally**

```python
def coding_iteration_limit_message(context: IterationLimitContext) -> UserMessage:
    return UserMessage(
        content=(
            f"The coding run reached its {context.max_iterations}-iteration limit. "
            "Stop calling tools and give the user a concise status, completed work, "
            "verification, and remaining blockers."
        ),
        timestamp=now_ms(),
    )
```

Wire this callback only in `AgentSession`.

- [ ] **Step 5: Verify core source neutrality**

```bash
rg -n "maximum number of tool-calling|concise summary|completed work|remaining blockers" appV2.3.1/appv231/agent
```

Expected: no output.

### Task 3: Canonical Awaitable APIs

**Files:**
- Create: `appV2.3.1/appv231/agent/async_utils.py`
- Modify: `appV2.3.1/appv231/agent/types.py`
- Modify: `appV2.3.1/appv231/agent/agent_loop.py`
- Modify: `appV2.3.1/appv231/agent/agent.py`
- Extend: `appV2.3.1/tests/test_agent_runtime_hardening.py`

**Interfaces:**
- Produces: `MaybeAwaitable[T] = T | Awaitable[T]`
- Produces: `async resolve(value: MaybeAwaitable[T]) -> T`
- Produces: `async run_agent_loop_async(...) -> list[AgentMessage]`
- Produces: `async run_agent_loop_continue_async(...) -> list[AgentMessage]`
- Produces: `Agent.async_prompt(...)` and `Agent.async_continue(...)`
- Preserves: `run_agent_loop()`, `run_agent_loop_continue()`, `Agent.prompt()`, `Agent.continue_()` as sync facades

- [ ] **Step 1: Write async listener, hook, and tool regressions**

```python
def test_async_extension_points_are_awaited_once(model):
    async def exercise() -> Counter:
        calls = Counter()

        async def listener(event, _signal):
            await asyncio.sleep(0)
            calls[("listener", event.type)] += 1

        async def before(_context, _signal):
            calls["before"] += 1
            await asyncio.sleep(0)
            return None

        async def execute(_call_id, _args, _signal, _update):
            calls["tool"] += 1
            await asyncio.sleep(0)
            return AgentToolResult(content=[TextContent(text="ok")])

        agent = make_async_agent(model=model, before=before, tool_execute=execute)
        agent.subscribe(listener)
        await agent.async_prompt("run tool")
        return calls

    calls = asyncio.run(exercise())
    assert calls["before"] == 1
    assert calls["tool"] == 1
    assert calls[("listener", "agent_end")] == 1
```

Drive async tests with `asyncio.run()` from normal pytest tests; do not add an async-test plugin solely for this migration.

- [ ] **Step 2: Verify current unawaited behavior**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_agent_runtime_hardening.py -k async_extension
```

Expected before repair: coroutine warnings or a callback-shape error.

- [ ] **Step 3: Implement the canonical async path**

```python
async def resolve(value: MaybeAwaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value

def run_sync(coroutine: Coroutine[Any, Any, T]) -> T:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    raise RuntimeError("Use the async appv231 API from an active event loop")
```

Move loop logic into async functions and let sync functions call `run_sync()`. Adapt synchronous provider/event iterators with `asyncio.to_thread(next, iterator)` or the stream's async iterator so the coordinator is not blocked.

- [ ] **Step 4: Await every extension point**

Apply `resolve()` to context transforms, prepare/stop hooks, iteration-limit callback, before/after hooks, listener sinks, provider callbacks, and tool execution. Delete `_settle_emit_result()` and `_settle_emit_results()` after no callers remain.

- [ ] **Step 5: Add sync-facade compatibility tests**

Assert existing `Agent.prompt()` results and event order remain unchanged, and assert calling the sync facade from an active event loop raises the explicit guidance error rather than nesting an event loop.

- [ ] **Step 6: Run core lifecycle tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_agent_runtime_hardening.py appV2.3.1/tests/test_agent_loop.py
```

Expected: pass without coroutine warnings.

### Task 4: Serialized Bounded Tool Coordinator

**Files:**
- Create: `appV2.3.1/appv231/agent/tool_coordinator.py`
- Modify: `appV2.3.1/appv231/agent/types.py`
- Modify: `appV2.3.1/appv231/agent/agent_loop.py:458-779`
- Extend: `appV2.3.1/tests/test_agent_runtime_hardening.py`
- Extend: `appV2.3.1/tests/test_agent_loop.py`

**Interfaces:**
- Produces: `ToolCoordinator(max_parallel_tools: int = 8)`
- Produces: `AgentLoopConfig.max_parallel_tools: int = 8`
- Produces: `async ToolCoordinator.execute_batch(...) -> ExecutedBatch`
- Guarantees: only raw sync tool bodies run off coordinator thread

- [ ] **Step 1: Write owner-thread and concurrency-limit regressions**

```python
def test_parallel_callbacks_are_serialized_on_coordinator_thread():
    observed: dict[str, list[str]] = defaultdict(list)
    coordinator_thread = threading.current_thread().name
    result = run_parallel_batch(
        count=20,
        max_parallel_tools=4,
        on_execute=lambda: observed["execute"].append(threading.current_thread().name),
        on_update=lambda: observed["update"].append(threading.current_thread().name),
        after_hook=lambda: observed["after"].append(threading.current_thread().name),
        on_event=lambda: observed["event"].append(threading.current_thread().name),
    )
    assert result.max_simultaneous_tools <= 4
    assert set(observed["update"]) == {coordinator_thread}
    assert set(observed["after"]) == {coordinator_thread}
    assert set(observed["event"]) == {coordinator_thread}
```

Also assert result messages follow source call IDs even when tools complete in reverse order.

- [ ] **Step 2: Verify current worker-thread leakage**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_agent_runtime_hardening.py -k parallel_callbacks
```

Expected before repair: updates or after-hooks report executor thread names, or concurrency exceeds four.

- [ ] **Step 3: Implement coordinator execution**

Use `asyncio.Semaphore(max_parallel_tools)` and `asyncio.gather()` around per-call tasks. Run a synchronous `tool.execute` through the bounded executor; await async tools directly. Return raw `(index, execution)` outcomes and perform finalization, after-hooks, state reduction, and all event emission on the event-loop thread.

For sync tools emitting updates from a worker, bridge to the owner loop with `asyncio.run_coroutine_threadsafe()` and wait for settlement before continuing that worker.

- [ ] **Step 4: Preserve source ordering**

Store final outcomes by source index. Emit completion events as calls settle if desired, but append `ToolResultMessage` objects to context in ascending source index.

- [ ] **Step 5: Run ordering and execution-mode tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_agent_runtime_hardening.py \
  appV2.3.1/tests/test_agent_loop.py \
  appV2.3.1/tests/test_tool_execution_modes.py
```

Expected: pass.

### Task 5: Typed Immediate Tool Outcomes

**Files:**
- Modify: `appV2.3.1/appv231/agent/types.py`
- Modify: `appV2.3.1/appv231/agent/agent_loop.py:448-734`
- Extend: `appV2.3.1/tests/test_agent_loop.py`

**Interfaces:**
- Produces: `ImmediateToolOutcome(result: AgentToolResult, is_error: bool, reason_code: str)`
- Removes: `_is_guardrail_block_result()` and `apply_after_tool_call` dictionaries
- Guarantees: after-hook only follows actual tool invocation

- [ ] **Step 1: Parameterize immediate-outcome tests**

```python
@pytest.mark.parametrize("case", ["before_block", "unknown_tool", "invalid_arguments"])
def test_immediate_tool_outcomes_bypass_after_hook(case):
    after_calls: list[str] = []
    events = run_case(case, after_hook=lambda ctx, signal: after_calls.append(ctx.tool_call.id))
    assert after_calls == []
    result = next(event.message for event in events if event.type == "message_end" and event.message.role == "toolResult")
    assert result.is_error is True
```

Add one invoked-tool failure test asserting the after-hook runs exactly once.

- [ ] **Step 2: Verify the generic block currently invokes after-hook**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_agent_loop.py -k immediate_tool_outcomes
```

Expected before repair: at least the ordinary before-block case records an after-hook call.

- [ ] **Step 3: Replace dictionary flags with typed branches**

`_prepare_tool_call()` returns either `PreparedToolCall` or `ImmediateToolOutcome`. `_finalize_immediate()` emits the provided result directly and never enters the after-hook path. `_finalize_executed()` always applies the after-hook exactly once.

- [ ] **Step 4: Delete policy parsing**

Remove `_is_guardrail_block_result`, its JSON dependency if unused, and every special case for a `guardrail` key.

- [ ] **Step 5: Run tool lifecycle tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_agent_loop.py -k "tool or before or after or unknown or validation"
```

Expected: pass.

### Task 6: Explicit Low-Level Stream Failure

**Files:**
- Modify: `appV2.3.1/appv231/agent/agent_loop.py:53-102`
- Extend: `appV2.3.1/tests/test_agent_runtime_hardening.py`
- Extend: `appV2.3.1/tests/test_agent_loop.py`

**Interfaces:**
- Consumes: `EventStream.fail(error)`
- Guarantees: worker exceptions are raised by `result_sync()`/`result()`
- Preserves: high-level Agent conversion of unexpected failure into assistant error lifecycle

- [ ] **Step 1: Write low-level failure tests**

```python
def test_agent_loop_stream_fails_on_provider_exception(model):
    expected = RuntimeError("provider exploded")
    stream = agent_loop([user("go")], context(), config(model), stream_fn=lambda *_: raise_(expected))
    with pytest.raises(RuntimeError, match="provider exploded"):
        stream.result_sync()
    assert not any(event.type == "agent_end" for event in list(stream))
```

Write the equivalent continuation test.

- [ ] **Step 2: Verify current success-shaped termination**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_agent_runtime_hardening.py -k stream_fails
```

Expected before repair: no exception is raised and a partial `agent_end` appears.

- [ ] **Step 3: Fail streams from wrappers**

```python
def _run() -> None:
    try:
        run_agent_loop(...)
    except BaseException as error:
        stream.fail(error)
```

Do not synthesize `AgentEndEvent` in the low-level wrapper. Keep high-level `_handle_run_failure()` responsible for user-facing assistant errors.

- [ ] **Step 4: Run stream and high-level failure tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_agent_runtime_hardening.py appV2.3.1/tests/test_agent_loop.py -k "failure or exception or stream"
```

Expected: pass.

### Task 7: Core Runtime Gate

**Files:**
- Modify: none

**Interfaces:**
- Produces the stable generic runtime contract used by Plans 3-6

- [ ] **Step 1: Run all core-focused tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_agent_core_boundary.py \
  appV2.3.1/tests/test_agent_runtime_hardening.py \
  appV2.3.1/tests/test_agent_loop.py \
  appV2.3.1/tests/test_abort_context.py \
  appV2.3.1/tests/test_tool_execution_modes.py
```

Expected: pass without warnings.

- [ ] **Step 2: Run the full suite**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests
```

Expected: zero failures.

- [ ] **Step 3: Prove dependency and redzone boundaries**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_agent_core_boundary.py
git diff --exit-code -- appV2.3.1/appv231/compaction
```

Expected: test passes; git diff command has no output.
