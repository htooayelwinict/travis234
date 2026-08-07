# Travis234 MCP Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a separately installable `travis234-mcp-adapter` Python extension that lazily connects to stdio and Streamable HTTP MCP servers through one bounded `mcp` proxy tool without changing Travis234's agent loop or provider layer.

**Architecture:** A package under `packages/travis234-mcp-adapter/` exposes one conventional Travis extension entry. Focused configuration, runtime, proxy, result-conversion, and output-guard modules use the official MCP Python SDK v2 while the existing Travis extension host retains scheduling, allowlists, lifecycle ordering, cancellation, and persistence.

**Tech Stack:** Python 3.13, `mcp>=2,<3`, `httpx2` through the MCP SDK, Travis `ToolDefinition` and extension APIs, pytest, pytest-asyncio, setuptools, uv, twine.

## Global Constraints

- Product and CLI names remain `Travis234` and `travis234`; the root import package remains `travis`.
- The adapter distribution is `travis234-mcp-adapter`; its import package is `travis234_mcp_adapter`.
- Keep Travis-owned state under `~/.travis234`; never read `~/.pi`.
- Do not modify agent-loop ordering, model calls, provider adapters/catalogs, iteration budgets, compaction, JSONL ownership, or bounded parallel execution.
- Do not add MCP to the root `travis234` dependencies.
- Support only stdio and Streamable HTTP in the MVP.
- Exclude OAuth, SSE, persistent metadata cache, direct tools, prompts, resource discovery, sampling, elicitation, scripting, compatibility imports, and MCP Apps/UI.
- Project MCP files remain unavailable until Travis project trust resolves positively.
- Secrets resolve only from process environment; never auto-load `.env`, run secret commands, or persist resolved values.
- Every bug fix starts with a failing regression test.
- No push, publication, GHCR change, npm publication, or GitHub account switch is authorized by this plan.
- Preserve these user-owned untracked files exactly:
  - `docs/superpowers/plans/2026-07-27-red-zone-free-pi-reliability-parity.md`
  - `docs/travis234-future-agent-framework-brainstorm.md`

---

### Task 1: Prove Independent Wheel Discovery and Importability

**Files:**
- Create: `packages/travis234-mcp-adapter/pyproject.toml`
- Create: `packages/travis234-mcp-adapter/README.md`
- Create: `packages/travis234-mcp-adapter/extensions/mcp_adapter.py`
- Create: `packages/travis234-mcp-adapter/travis234_mcp_adapter/__init__.py`
- Create: `packages/travis234-mcp-adapter/travis234_mcp_adapter/extension.py`
- Create: `packages/travis234-mcp-adapter/tests/test_distribution.py`

**Interfaces:**
- Consumes: `DefaultPackageManager.install()`, conventional `extensions/` discovery, `DefaultResourceLoader.reload()`.
- Produces: `extension(travis) -> None` and a wheel whose installed payload contains `extensions/mcp_adapter.py`.

- [ ] **Step 1: Write the failing real-wheel test**

Build the wheel, install it through `DefaultPackageManager` as a direct file requirement, resolve the conventional extension, and load a probe command:

```python
def test_built_wheel_installs_and_loads_through_travis(tmp_path: Path) -> None:
    wheel = build_adapter_wheel(tmp_path / "dist")
    settings = SettingsManager.in_memory()
    manager = DefaultPackageManager(
        cwd=str(tmp_path / "repo"),
        agent_dir=str(tmp_path / "agent"),
        settings_manager=settings,
        project_trusted=True,
    )
    installed = manager.install(
        f"travis234-mcp-adapter @ {wheel.as_uri()}",
        scope="global",
    )
    resolved = manager.resolve()
    assert Path(installed.install_path).is_dir()
    assert [Path(item.path).name for item in resolved.extensions] == ["mcp_adapter.py"]

    loader = DefaultResourceLoader(
        cwd=str(tmp_path / "repo"),
        agent_dir=str(tmp_path / "agent"),
        settings_manager=settings,
        project_trusted=True,
    )
    loader.reload()
    runtime = loader.get_extensions()["runtime"]
    assert runtime.get_registered_command("mcp-package-probe") is not None
```

The helper runs `uv build --wheel --clear -o OUT_DIR PACKAGE_ROOT` with `check=True` and selects the only wheel.

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest packages/travis234-mcp-adapter/tests/test_distribution.py::test_built_wheel_installs_and_loads_through_travis -v
```

Expected: FAIL because the adapter package does not exist.

- [ ] **Step 3: Add minimal package metadata**

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "travis234-mcp-adapter"
version = "0.1.0"
description = "Optional MCP client adapter extension for Travis234."
readme = "README.md"
license = "MIT"
requires-python = ">=3.13,<3.14"
dependencies = ["mcp>=2,<3"]

[project.optional-dependencies]
test = ["pytest>=8", "pytest-asyncio>=1"]

[tool.setuptools.packages.find]
where = ["."]
include = ["travis234_mcp_adapter*"]

[tool.setuptools.data-files]
"extensions" = ["extensions/mcp_adapter.py"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
```

- [ ] **Step 4: Add the installed entry bootstrap and probe**

```python
from pathlib import Path
import sys

_PAYLOAD_ROOT = str(Path(__file__).resolve().parents[1])
if _PAYLOAD_ROOT not in sys.path:
    sys.path.insert(0, _PAYLOAD_ROOT)

from travis234_mcp_adapter.extension import extension

__all__ = ["extension"]
```

The temporary factory registers `mcp-package-probe` only, proving importability before behavior exists.

- [ ] **Step 5: Run wheel and existing package tests**

```bash
uv run pytest packages/travis234-mcp-adapter/tests/test_distribution.py tests/test_package_manager.py tests/test_coding_resources_and_services.py -q
```

Expected: PASS. If factory import fails, retain the regression and apply only the approved generic loader fallback; do not touch the agent loop or lifecycle.

- [ ] **Step 6: Commit the distribution gate**

```bash
git add packages/travis234-mcp-adapter
git commit -m "feat: scaffold optional MCP adapter package"
```

---

### Task 2: Add Trust-Aware Configuration and Secret Resolution

**Files:**
- Create: `packages/travis234-mcp-adapter/travis234_mcp_adapter/config.py`
- Create: `packages/travis234-mcp-adapter/tests/conftest.py`
- Create: `packages/travis234-mcp-adapter/tests/test_config.py`

**Interfaces:**
- Consumes: `cwd: Path`, `home: Path`, `project_trusted: bool`, `Mapping[str, str]`.
- Produces: `load_config(cwd, home, project_trusted) -> LoadedConfig`, `resolve_server(server, environ) -> ResolvedServer`, and `ConfigError`.

- [ ] **Step 1: Write failing precedence and trust tests**

```python
def test_project_source_replaces_global_only_when_trusted(config_tree: ConfigTree) -> None:
    config_tree.write_global_shared("shared", {"command": "global", "args": ["one"]})
    config_tree.write_global_travis("shared", {"command": "override", "args": ["two"]})
    config_tree.write_project_shared("shared", {"url": "https://project.test/mcp"})

    untrusted = load_config(config_tree.cwd, config_tree.home, False)
    trusted = load_config(config_tree.cwd, config_tree.home, True)
    assert untrusted.servers["shared"].command == "override"
    assert trusted.servers["shared"].url == "https://project.test/mcp"
    assert trusted.servers["shared"].command is None
```

Also assert `.travis234/mcp.json` wins over `.mcp.json` and populated `~/.pi` paths are never read.

- [ ] **Step 2: Run config tests to verify failure**

```bash
uv run pytest packages/travis234-mcp-adapter/tests/test_config.py -v
```

Expected: FAIL because `config.py` does not exist.

- [ ] **Step 3: Implement immutable types and source order**

```python
@dataclass(frozen=True)
class ServerConfig:
    name: str
    source_path: Path
    command: str | None = None
    args: tuple[str, ...] = ()
    cwd: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    url: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    request_timeout_ms: int | None = None

@dataclass(frozen=True)
class LoadedConfig:
    servers: Mapping[str, ServerConfig]
    sources: tuple[Path, ...]
    ignored_project_sources: tuple[Path, ...]
```

Read the four approved paths in order and replace duplicate server definitions atomically.

- [ ] **Step 4: Add failing strict-validation tests**

Test invalid JSON, non-object `mcpServers`, unknown fields, command/url conflicts, non-string args/env/headers, and boolean timeouts. Assert one invalid authorized file raises `ConfigError` instead of returning lower-precedence servers.

- [ ] **Step 5: Implement strict validation**

```python
_SERVER_FIELDS = {"command", "args", "cwd", "env", "url", "headers", "requestTimeoutMs"}
```

Require exactly one of command or URL. Keep path and JSON field path in errors; never include file contents.

- [ ] **Step 6: Add failing environment and redaction tests**

```python
@pytest.mark.parametrize("template", ["${SERVICE_TOKEN}", "$env:SERVICE_TOKEN"])
def test_secret_resolves_only_at_connection_time(template: str) -> None:
    resolved = resolve_server(
        stdio_server(env={"SERVICE_TOKEN": template}),
        {"SERVICE_TOKEN": "secret-value"},
    )
    assert resolved.env == {"SERVICE_TOKEN": "secret-value"}
    assert "secret-value" not in repr(resolved)
```

Missing or empty variables name only the key. Expansion never occurs in command, args, cwd, or URL. `Authorization`, `Cookie`, and `Proxy-Authorization` reject literal values without a reference.

- [ ] **Step 7: Implement non-recursive connection-time resolution**

Support `${NAME}` inside env/header strings and exact `$env:NAME`. Return `ResolvedServer` with redacted `repr`; retain no raw process-environment mapping.

- [ ] **Step 8: Run and commit config tests**

```bash
uv run pytest packages/travis234-mcp-adapter/tests/test_config.py -q
git add packages/travis234-mcp-adapter/travis234_mcp_adapter/config.py packages/travis234-mcp-adapter/tests/conftest.py packages/travis234-mcp-adapter/tests/test_config.py
git commit -m "feat: add trusted MCP configuration loading"
```

---

### Task 3: Register the Static Proxy and No-I/O Status

**Files:**
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/extension.py`
- Create: `packages/travis234-mcp-adapter/travis234_mcp_adapter/proxy_tool.py`
- Create: `packages/travis234-mcp-adapter/tests/test_extension.py`
- Create: `packages/travis234-mcp-adapter/tests/test_proxy_tool.py`

**Interfaces:**
- Consumes: `LoadedConfig`, Travis `ToolDefinition`, `AgentToolResult`, `TextContent`, lifecycle contexts.
- Produces: `MCP_TOOL_NAME = "mcp"`, `create_proxy_definition(state) -> ToolDefinition`, and status dispatch.

- [ ] **Step 1: Write a failing static schema/no-I/O test**

Assert one registered tool with this schema and no factory-time file, process, or HTTP access:

```python
{
    "type": "object",
    "properties": {
        "server": {"type": "string"},
        "search": {"type": "string"},
        "describe": {"type": "string"},
        "tool": {"type": "string"},
        "args": {"type": "object", "additionalProperties": True},
    },
    "additionalProperties": False,
}
```

- [ ] **Step 2: Run the test to verify failure**

```bash
uv run pytest packages/travis234-mcp-adapter/tests/test_extension.py::test_factory_registers_one_proxy_without_io -v
```

Expected: FAIL because the probe command still exists.

- [ ] **Step 3: Replace the probe with generation-owned registration**

```python
def extension(travis) -> None:
    state = ExtensionState()
    travis.register_tool(create_proxy_definition(state))
    travis.on("session_start", state.on_session_start)
    travis.on("session_shutdown", state.on_session_shutdown)
    travis.on("tool_result", state.on_tool_result)
```

`session_start` snapshots cwd/trust, loads configuration, creates an empty runtime, and re-registers the same tool name with authorized server names. Never force activation through `set_active_tools()`.

- [ ] **Step 4: Add failing status tests**

Assert empty parameters return disconnected server status, ignored-project count, or a bounded config error without invoking any connector.

- [ ] **Step 5: Implement status-only dispatch**

Return `AgentToolResult` details containing only adapter marker, server names, status strings, and ignored-project count. Other operation shapes return `not_implemented` until later tasks.

- [ ] **Step 6: Run and commit focused tests**

```bash
uv run pytest packages/travis234-mcp-adapter/tests/test_extension.py packages/travis234-mcp-adapter/tests/test_proxy_tool.py tests/test_extension_host_runtime.py tests/test_extension_event_parity.py -q
git add packages/travis234-mcp-adapter/travis234_mcp_adapter/extension.py packages/travis234-mcp-adapter/travis234_mcp_adapter/proxy_tool.py packages/travis234-mcp-adapter/tests/test_extension.py packages/travis234-mcp-adapter/tests/test_proxy_tool.py
git commit -m "feat: register lazy MCP proxy status"
```

---

### Task 4: Add Official-SDK Runtime and Real Stdio Transport

**Files:**
- Create: `packages/travis234-mcp-adapter/travis234_mcp_adapter/runtime.py`
- Create: `packages/travis234-mcp-adapter/tests/fixtures/server.py`
- Create: `packages/travis234-mcp-adapter/tests/test_runtime_stdio.py`
- Modify: `packages/travis234-mcp-adapter/tests/conftest.py`

**Interfaces:**
- Consumes: `ResolvedServer`, `mcp.Client`, `StdioServerParameters`, `stdio_client`, Travis `AbortSignal`.
- Produces: `McpRuntime.connect(name, signal) -> ConnectedServer`, `ConnectedServer.list_tools(signal)`, `ConnectedServer.call_tool(name, args, signal)`, `McpRuntime.close()`.

- [ ] **Step 1: Create a deterministic stdio fixture**

Use official `MCPServer` with these tools:

```python
@server.tool()
def echo(text: str) -> str:
    return text

@server.tool()
def configured_secret_name() -> str:
    return "present" if os.environ.get("FIXTURE_TOKEN") else "missing"

@server.tool()
async def slow(delay_ms: int) -> str:
    await asyncio.sleep(delay_ms / 1000)
    return "finished"
```

Run stdio only for `--transport stdio`; never print environment values.

- [ ] **Step 2: Write failing real-stdio tests**

Test lazy construction, concurrent first connect returning one client, tool listing, echo, explicit env delivery, and process exit after close.

```python
async def test_stdio_connects_once(stdio_runtime: McpRuntime) -> None:
    first, second = await asyncio.gather(
        stdio_runtime.connect("fixture", None),
        stdio_runtime.connect("fixture", None),
    )
    assert first is second
    assert [tool.name for tool in await first.list_tools(None)] == [
        "echo", "configured_secret_name", "slow"
    ]
```

- [ ] **Step 3: Run the tests to verify failure**

```bash
uv run pytest packages/travis234-mcp-adapter/tests/test_runtime_stdio.py -v
```

Expected: FAIL because `runtime.py` does not exist.

- [ ] **Step 4: Implement per-server state and SDK stdio entry**

```python
params = StdioServerParameters(
    command=resolved.command,
    args=list(resolved.args),
    env=dict(resolved.env),
    cwd=resolved.cwd,
)
client = Client(stdio_client(params))
connected_client = await stack.enter_async_context(client)
```

Use one `asyncio.Lock` and `AsyncExitStack` per server. Keep the SDK's documented minimal inherited environment unchanged.

- [ ] **Step 5: Add failing timeout and abort tests**

Call `slow` with `requestTimeoutMs=50` and assert timeout. Start a five-second call, invoke `signal.abort()`, assert cancellation, then verify a fresh connect/call succeeds.

- [ ] **Step 6: Implement one cancellable request wrapper**

```python
async def await_request(awaitable, signal: AbortSignal | None, timeout_ms: int | None):
    task = asyncio.create_task(awaitable)
    loop = asyncio.get_running_loop()
    unsubscribe = signal.add_callback(
        lambda: loop.call_soon_threadsafe(task.cancel)
    ) if signal else None
    try:
        if timeout_ms is not None and timeout_ms > 0:
            async with asyncio.timeout(timeout_ms / 1000):
                return await task
        return await task
    finally:
        if unsubscribe is not None:
            unsubscribe()
```

Translate timeout into server/operation/duration error. Re-raise `CancelledError` unchanged.

- [ ] **Step 7: Implement idempotent shutdown**

Mark runtime closed, cancel tracked requests, await each stack close with a five-second bound, and clear state. A second close returns without touching old handles.

- [ ] **Step 8: Run and commit stdio runtime**

```bash
uv run pytest packages/travis234-mcp-adapter/tests/test_runtime_stdio.py -q
git add packages/travis234-mcp-adapter/travis234_mcp_adapter/runtime.py packages/travis234-mcp-adapter/tests/fixtures/server.py packages/travis234-mcp-adapter/tests/test_runtime_stdio.py packages/travis234-mcp-adapter/tests/conftest.py
git commit -m "feat: add lazy stdio MCP runtime"
```

---

### Task 5: Add Streamable HTTP with Secret-Safe Headers

**Files:**
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/runtime.py`
- Modify: `packages/travis234-mcp-adapter/tests/fixtures/server.py`
- Create: `packages/travis234-mcp-adapter/tests/test_runtime_http.py`
- Modify: `packages/travis234-mcp-adapter/tests/conftest.py`

**Interfaces:**
- Consumes: `streamable_http_client`, `httpx2.AsyncClient`, resolved URL/headers, Task 4 request wrapper.
- Produces: the same `ConnectedServer` interface for HTTP and stdio.

- [ ] **Step 1: Add a real localhost HTTP fixture**

Start official Streamable HTTP on `127.0.0.1` with an OS-assigned port. Record only whether the expected authorization header arrived, never its value. The async fixture always shuts down.

- [ ] **Step 2: Write failing HTTP tests**

Assert construction sends no request, first list connects, echo works, the referenced header arrives, timeout is bounded, and close exits both contexts.

- [ ] **Step 3: Run tests to verify failure**

```bash
uv run pytest packages/travis234-mcp-adapter/tests/test_runtime_http.py -v
```

Expected: FAIL because HTTP definitions are not connected.

- [ ] **Step 4: Implement documented v2 HTTP transport**

```python
http_client = await stack.enter_async_context(
    httpx2.AsyncClient(
        headers=dict(resolved.headers),
        timeout=httpx2.Timeout(timeout_seconds),
        follow_redirects=True,
    )
)
transport = streamable_http_client(resolved.url, http_client=http_client)
client = await stack.enter_async_context(Client(transport))
```

Without `requestTimeoutMs`, use the SDK-documented 30-second connect/write/pool and 300-second read values. A positive configured value also wraps initialize/list/call through Task 4.

- [ ] **Step 5: Verify HTTP and leakage tests**

```bash
uv run pytest packages/travis234-mcp-adapter/tests/test_runtime_http.py packages/travis234-mcp-adapter/tests/test_config.py -q
```

Expected: PASS; captured output contains neither token nor Authorization value.

- [ ] **Step 6: Commit HTTP transport**

```bash
git add packages/travis234-mcp-adapter/travis234_mcp_adapter/runtime.py packages/travis234-mcp-adapter/tests/fixtures/server.py packages/travis234-mcp-adapter/tests/test_runtime_http.py packages/travis234-mcp-adapter/tests/conftest.py
git commit -m "feat: add Streamable HTTP MCP transport"
```

---

### Task 6: Implement Bounded Discovery, Search, Describe, and Calls

**Files:**
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/proxy_tool.py`
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/runtime.py`
- Modify: `packages/travis234-mcp-adapter/tests/test_proxy_tool.py`

**Interfaces:**
- Consumes: `McpRuntime`, SDK `Tool`, `next_cursor`, tool arguments.
- Produces: `dispatch_proxy(runtime, params, signal) -> ProxyResult`, `load_tool_catalog(connected, signal) -> tuple[Tool, ...]`.

- [ ] **Step 1: Write failing dispatch-validation tests**

Reject without connecting:

```python
{"search": "issue"}
{"describe": "search_issues"}
{"tool": "search_issues"}
{"server": "github", "search": "issue", "tool": "search_issues"}
{"server": "github", "args": {"query": "x"}}
```

Each error includes one corrected example and no config contents.

- [ ] **Step 2: Implement exact dispatch precedence**

```python
operations = [name for name in ("search", "describe", "tool") if params.get(name) is not None]
```

Empty means status; server alone means list; every other operation requires one non-empty server.

- [ ] **Step 3: Write failing pagination tests**

Use fake pages to assert normal aggregation, repeated-cursor failure, a 100-page ceiling, and a 10,000-entry ceiling with no partial catalog returned.

- [ ] **Step 4: Implement paginated in-memory catalog loading**

Track seen cursors, call `list_tools(cursor=cursor)` until `next_cursor is None`, and cache only for the active session.

- [ ] **Step 5: Write failing list/search/describe tests**

List emits names and bounded descriptions without schemas. Search ranks exact name, prefix, then substring over name/title/description; tie-break by original name; return at most 20. Describe returns one full input schema.

- [ ] **Step 6: Implement deterministic discovery output**

Return a refinement message when matches exceed 20. Apply the output guard at the final result boundary, not inside ranking.

- [ ] **Step 7: Write failing call tests**

Assert args default to `{}`, unknown tools recommend list/search, and a call invokes exactly once with the original MCP name.

- [ ] **Step 8: Implement call dispatch without retry**

```python
result = await connected.call_tool(tool_name, dict(arguments), signal)
```

Do not retry timeout, transport, JSON-RPC, or `is_error` outcomes.

- [ ] **Step 9: Run and commit proxy tests**

```bash
uv run pytest packages/travis234-mcp-adapter/tests/test_proxy_tool.py packages/travis234-mcp-adapter/tests/test_runtime_stdio.py packages/travis234-mcp-adapter/tests/test_runtime_http.py -q
git add packages/travis234-mcp-adapter/travis234_mcp_adapter/proxy_tool.py packages/travis234-mcp-adapter/travis234_mcp_adapter/runtime.py packages/travis234-mcp-adapter/tests/test_proxy_tool.py
git commit -m "feat: add bounded MCP proxy operations"
```

---

### Task 7: Convert Results and Guard Oversized Output

**Files:**
- Create: `packages/travis234-mcp-adapter/travis234_mcp_adapter/results.py`
- Create: `packages/travis234-mcp-adapter/travis234_mcp_adapter/output_guard.py`
- Create: `packages/travis234-mcp-adapter/tests/test_results.py`
- Create: `packages/travis234-mcp-adapter/tests/test_output_guard.py`
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/proxy_tool.py`
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/extension.py`

**Interfaces:**
- Consumes: SDK `CallToolResult` content union and `structured_content`/`is_error`.
- Produces: `convert_call_result(result, spills) -> AgentToolResult`, `OutputGuard.guard(text) -> GuardedText`, adapter details key `travis234Mcp`.

- [ ] **Step 1: Write failing conversion tests**

Cover text, image, resource link, embedded text, embedded blob, audio, structured, mixed, and `is_error`. Assert images become Travis `ImageContent`, structured JSON is synthesized only when ordinary content lacks equivalent text, and binary/audio placeholders contain type and size but no payload.

```python
assert converted.details["travis234Mcp"]["isError"] is False
assert converted_image.mime_type == "image/png"
```

- [ ] **Step 2: Run result tests to verify failure**

```bash
uv run pytest packages/travis234-mcp-adapter/tests/test_results.py -v
```

Expected: FAIL because `results.py` does not exist.

- [ ] **Step 3: Implement explicit SDK type narrowing**

Use `isinstance` against SDK content classes. Serialize structured content with stable compact JSON and `ensure_ascii=False`. Never dispatch on unchecked dictionary keys.

- [ ] **Step 4: Write failing byte/line guard tests**

Assert exact limits at 50 KiB and 2,000 lines, one-byte/line overflow, UTF-8 accounting, mode `0600`, random names, and deletion after cleanup.

- [ ] **Step 5: Implement session-owned guard**

```python
MAX_INLINE_BYTES = 50 * 1024
MAX_INLINE_LINES = 2_000

@dataclass(frozen=True)
class GuardedText:
    text: str
    spill_path: Path | None
    truncated_by: str | None
```

Use `tempfile.mkstemp(prefix="travis234-mcp-", text=True)`, `os.fchmod(fd, 0o600)`, and an owned `SpillRegistry`. Cleanup unlinks only registered paths.

- [ ] **Step 6: Guard aggregate model-visible text**

Combine text for limit accounting so many blocks cannot bypass bounds. Preserve image blocks. Details retain no raw oversized result.

- [ ] **Step 7: Write failing `is_error` bridge tests**

Assert a `tool_result` for `mcp` with `details={"travis234Mcp": {"isError": True}}` returns `{"isError": True}`. Bash, read, and other extension tools remain unchanged.

- [ ] **Step 8: Implement the scoped bridge**

Inspect copied details, require the adapter marker, and return only the `isError` replacement. Never mutate the event.

- [ ] **Step 9: Run and commit result tests**

```bash
uv run pytest packages/travis234-mcp-adapter/tests/test_results.py packages/travis234-mcp-adapter/tests/test_output_guard.py packages/travis234-mcp-adapter/tests/test_extension.py -q
git add packages/travis234-mcp-adapter/travis234_mcp_adapter/results.py packages/travis234-mcp-adapter/travis234_mcp_adapter/output_guard.py packages/travis234-mcp-adapter/travis234_mcp_adapter/proxy_tool.py packages/travis234-mcp-adapter/travis234_mcp_adapter/extension.py packages/travis234-mcp-adapter/tests/test_results.py packages/travis234-mcp-adapter/tests/test_output_guard.py
git commit -m "feat: guard and convert MCP tool results"
```

---

### Task 8: Prove Reload, Replacement, Isolation, and Cleanup

**Files:**
- Modify: `packages/travis234-mcp-adapter/tests/test_extension.py`
- Modify: `packages/travis234-mcp-adapter/tests/test_runtime_stdio.py`
- Modify: `packages/travis234-mcp-adapter/tests/test_runtime_http.py`
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/extension.py`
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/runtime.py`

**Interfaces:**
- Consumes: existing `session_start`/`session_shutdown` ordering and Task 4–7 ownership.
- Produces: one runtime per extension generation and idempotent cleanup for every transition.

- [ ] **Step 1: Add failing lifecycle integration tests**

With real `DefaultResourceLoader` and `AgentSession`, prove startup creates no transport, first call creates one, reload closes old and starts disconnected, replacement closes previous before new calls, stale context raises, and double shutdown leaves no child/HTTP task/spill.

- [ ] **Step 2: Add failing server-isolation tests**

Configure one nonexistent stdio command and one healthy fixture. Broken list returns an error; healthy list/call succeeds; a later broken request performs one fresh attempt.

- [ ] **Step 3: Add failing concurrent-shutdown test**

Start slow calls against two servers, emit shutdown, and assert both cancel within the bound while existing core event order remains unchanged.

- [ ] **Step 4: Implement generation transitions**

Increment generation on start and shutdown. Abort/close old runtime before assignment. Tool execution snapshots runtime and verifies it is current before returning.

- [ ] **Step 5: Bound cleanup diagnostics**

Collect close exceptions by server, finish every cleanup, and emit one bounded source-attributed diagnostic with no transport objects, headers, env, or raw stderr.

- [ ] **Step 6: Run and commit lifecycle tests**

```bash
uv run pytest packages/travis234-mcp-adapter/tests tests/test_extension_host_runtime.py tests/test_extension_event_parity.py tests/test_tui_commands_and_extensions.py -q
git add packages/travis234-mcp-adapter/travis234_mcp_adapter/extension.py packages/travis234-mcp-adapter/travis234_mcp_adapter/runtime.py packages/travis234-mcp-adapter/tests
git commit -m "test: prove MCP adapter lifecycle cleanup"
```

---

### Task 9: Document, Build, and Run Installed-Wheel Acceptance

**Files:**
- Modify: `packages/travis234-mcp-adapter/README.md`
- Modify: `README.md`
- Modify: `packages/travis234-mcp-adapter/tests/test_distribution.py`
- Create: `docs/verification/main-mcp-adapter-five-prompt-tui.md`

**Interfaces:**
- Consumes: completed package and existing release/TUI verification commands.
- Produces: user docs, checked artifacts, and reproducible acceptance evidence.

- [ ] **Step 1: Write installation and security docs**

Include `travis234 install travis234-mcp-adapter`, all four approved configuration paths, `${SERVICE_TOKEN}` and `$env:SERVICE_TOKEN` examples, the approved stdio/HTTP examples, and all five proxy forms. State that project files require trust, configured servers are the user's consent boundary, `.env` is never auto-loaded by the adapter, `requestTimeoutMs` applies only to MCP operations, OAuth/SSE are unsupported, and installing does not bypass a Travis allowlist that excludes `mcp`. Document that updating the separately installed adapter takes effect after `/reload` or a new session.

- [ ] **Step 2: Extend the distribution behavior test**

Build and inspect the real wheel metadata and payload. Assert the distribution name/version/dependency contract, the conventional `extensions/mcp_adapter.py` entry, and importability from the installed target. Do not grep human prose: README correctness is verified by direct review against Step 1.

- [ ] **Step 3: Run the distribution behavior test**

```bash
uv run pytest packages/travis234-mcp-adapter/tests/test_distribution.py -v
```

Expected: PASS with the final wheel metadata and payload.

- [ ] **Step 4: Run package and repository suites**

```bash
uv run pytest packages/travis234-mcp-adapter/tests -q
uv run pytest -q
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Expected: adapter, full Python, npm launcher, and npm dry-run all pass.

- [ ] **Step 5: Build and check both Python distributions**

```bash
uv build --clear
uv build --clear -o dist/mcp-adapter packages/travis234-mcp-adapter
uv run twine check dist/travis234-*
uv run twine check dist/mcp-adapter/*
```

Expected: root and adapter wheels/sdists build and all pass twine.

- [ ] **Step 6: Run clean installed-wheel package-manager smoke**

In a temporary virtual environment, install the root wheel. Use that CLI to install the adapter wheel through a direct file requirement, then assert `travis234 list --json` reports the adapter and extension resource. Do not read repository `.env` in this packaging smoke.

- [ ] **Step 7: Run five-prompt Minimax TUI scenario**

Use main-branch `.env` only through the established provider boundary and select `minimax-m3`. In one continuous installed-wheel TUI session against local fixtures:

1. report status without connecting;
2. list stdio tools;
3. search and describe `echo`;
4. call `echo` with a unique sentinel;
5. call controlled oversized/error output, summarize it, and exit.

Capture event/conversation JSONL outside the repo. Independently verify status created no child, one stdio child served later prompts, the sentinel came from MCP, spill mode was `0600`, and exit removed child/spill. Never record `.env` contents.

- [ ] **Step 8: Run relevant unprivileged container smoke**

Mount only a temporary workspace and isolated `~/.travis234`, install the adapter from local wheel, run status and a local fixture call, and verify container exit leaves no child. Use no public MCP service.

- [ ] **Step 9: Inspect final scope**

```bash
git diff --check
git status --short
git diff --stat HEAD~1
```

Expected: only approved package/docs/evidence and any proven conditional loader file changed; protected documents remain untracked and unchanged.

- [ ] **Step 10: Commit docs and evidence**

```bash
git add README.md packages/travis234-mcp-adapter/README.md packages/travis234-mcp-adapter/tests/test_distribution.py docs/verification/main-mcp-adapter-five-prompt-tui.md
git commit -m "docs: verify optional MCP adapter"
```

---

## Final Completion Gate

Record fresh outputs and counts for:

```bash
uv run pytest packages/travis234-mcp-adapter/tests -q
uv run pytest -q
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
uv build --clear
uv build --clear -o dist/mcp-adapter packages/travis234-mcp-adapter
uv run twine check dist/travis234-*
uv run twine check dist/mcp-adapter/*
```

Also record the clean package-manager smoke, five-prompt `minimax-m3` TUI evidence, and unprivileged container smoke. Do not publish or push until separately authorized.

## File Structure

New package files:

- `packages/travis234-mcp-adapter/pyproject.toml`: independent distribution metadata and extension data-file mapping.
- `packages/travis234-mcp-adapter/README.md`: install, configuration, trust, secrets, usage, and exclusions.
- `packages/travis234-mcp-adapter/extensions/mcp_adapter.py`: installed-payload bootstrap and exported factory.
- `packages/travis234-mcp-adapter/travis234_mcp_adapter/__init__.py`: version and public factory.
- `packages/travis234-mcp-adapter/travis234_mcp_adapter/extension.py`: registration and generation lifecycle.
- `packages/travis234-mcp-adapter/travis234_mcp_adapter/config.py`: precedence, trust, validation, and environment resolution.
- `packages/travis234-mcp-adapter/travis234_mcp_adapter/runtime.py`: SDK clients, locks, cancellation, timeout, and shutdown.
- `packages/travis234-mcp-adapter/travis234_mcp_adapter/proxy_tool.py`: schema and status/list/search/describe/call dispatch.
- `packages/travis234-mcp-adapter/travis234_mcp_adapter/results.py`: MCP-to-Travis result conversion.
- `packages/travis234-mcp-adapter/travis234_mcp_adapter/output_guard.py`: inline bounds and secure spill cleanup.
- `packages/travis234-mcp-adapter/tests/`: focused tests plus deterministic local MCP fixtures.

Existing integration files:

- `README.md`: optional adapter installation section.
- `tests/test_package_manager.py`: modified only when a real-wheel regression proves a generic loader defect.
- `travis/coding_agent/package_manager.py` or `travis/coding_agent/resource_loader.py`: modified only for that focused defect, never both without separate regressions.

---
