# Travis234 MCP Adapter Extension Design

Date: 2026-08-08
Status: approved for implementation planning; no runtime implementation in this commit

## Decision summary

Travis234 will gain MCP client support through a separate, optional Python
extension package named `travis234-mcp-adapter`. It will not add MCP to the
Travis234 core agent loop or root runtime dependencies.

The first release is a surgical MVP:

- one token-efficient `mcp` proxy tool;
- stdio and Streamable HTTP transports;
- lazy, per-server connections;
- status, list, search, describe, and call operations;
- process-environment secret references;
- project-trust-aware configuration;
- bounded text output with session-owned spill files;
- Travis cancellation propagation; and
- deterministic session shutdown.

OAuth, legacy SSE, persistent metadata caches, direct tools, prompts, resource
discovery, sampling, elicitation, scripting, compatibility imports, and MCP
Apps/UI are explicitly deferred.

## Source authority

The design was checked against these sources:

1. Travis234 `main` at commit
   `97aff8b174fe95064b390f30da07093b33de3c0b`.
2. The local Pi parity checkout at commit
   `bde81c84405514c8b0f57c34405c152fb129c0ce`.
3. `pi-mcp-adapter` 2.21.0 at commit
   `eaf379782fddf836828811d1b71ad85d27bc70dd`.
4. The official MCP 2026-07-28 specification and the stable v2 Python SDK.

References:

- https://modelcontextprotocol.io/specification/2026-07-28
- https://github.com/modelcontextprotocol/python-sdk
- https://py.sdk.modelcontextprotocol.io/
- https://github.com/nicobailon/pi-mcp-adapter

Pi and its adapter are behavioral and ergonomics references, not runtime
dependencies. The official MCP SDK owns protocol correctness.

## Why an extension

Pi intentionally keeps MCP outside its core and supports it through extension
packages. Travis234 already exposes the required Python-native boundaries:

- typed tool registration;
- slash commands and typed flags;
- `session_start` and `session_shutdown` lifecycle events;
- generation-guarded extension contexts;
- live tool refresh;
- project trust;
- transactional resource-package installation; and
- canonical bounded tool execution.

The MCP adapter therefore does not need to own or modify:

- agent-loop ordering;
- model-call behavior;
- iteration budgeting;
- bounded parallel tool scheduling;
- context construction or compaction;
- provider adapters or model catalogs;
- session JSONL persistence; or
- built-in process, PTY, or tmux behavior.

## Options considered

### Native Python extension with the official SDK (selected)

Publish a separate Python package that registers one Travis tool and uses
`mcp>=2,<3` for protocol and transport ownership.

Advantages:

- maps directly to Travis's current extension runtime;
- follows the current official protocol implementation;
- preserves cancellation and async request handling;
- keeps MCP dependencies out of the root Travis distribution; and
- gives the smallest maintainable runtime surface.

### Isolated MCP sidecar

Run another adapter process and speak a private JSON protocol between the
extension and the sidecar.

Rejected for the MVP because it adds an IPC protocol, another process lifecycle,
duplicated cancellation behavior, and more packaging failure modes.

### Handwritten protocol client

Implement MCP JSON-RPC, negotiation, transports, and compatibility directly.

Rejected because it duplicates the official SDK and creates avoidable protocol,
security, and maintenance risk.

## Package and installation design

The source will be additive under:

```text
packages/travis234-mcp-adapter/
├── pyproject.toml
├── extensions/
│   └── mcp_adapter.py
├── travis234_mcp_adapter/
│   ├── __init__.py
│   ├── config.py
│   ├── extension.py
│   ├── output_guard.py
│   ├── proxy_tool.py
│   ├── results.py
│   └── runtime.py
└── tests/
```

The distribution name is `travis234-mcp-adapter`; its import package is
`travis234_mcp_adapter`. Its wheel exposes only `extensions/mcp_adapter.py` as a
Travis extension resource. The SDK and its transitive dependencies remain owned
by the adapter distribution.

The public installation path is:

```bash
travis234 install travis234-mcp-adapter
```

The installed extension executes with normal Travis extension permissions. It
does not install or update itself during ordinary startup.

### Real-wheel installation gate

Before implementing MCP behavior, build a minimal local adapter wheel and install
it through the real `DefaultPackageManager`. The regression must prove:

1. the wheel's extension resource is discovered;
2. the adapter import package is importable;
3. an adapter-owned dependency installed into the same target is importable;
4. `/reload` can load the extension; and
5. removal leaves no active registration after the next reload or process start.

The preferred result requires no Travis production change. A package-specific
bootstrap may expose its installed payload root without changing core behavior.

If and only if the real-wheel regression proves that advertised Python package
resources or their dependencies cannot load safely, make one generic,
source-scoped repair in `package_manager.py` or `resource_loader.py`. That repair
must start with the failing regression, must not add a global alternate package
tree, and must not change extension precedence or trust behavior.

Package update semantics must be explicit. If Python module caching prevents a
newly installed adapter version from replacing an already imported dependency in
the same process, `/update` will report that a Travis restart is required rather
than pretending `/reload` applied the new code.

## Change radius

Expected changes to existing production modules:

```text
agent loop                     none
extension runtime              none
session lifecycle              none
provider adapters/catalogs     none
root runtime dependencies      none
npm launcher/container         none
```

The adapter implementation belongs entirely to the new package. Existing
repository integration changes may include documentation, verification wiring,
and a separate release workflow.

The maximum conditional core fallback is one loader/package-manager production
module plus its focused regression test. No broader refactor is authorized by
this design.

## Configuration model

Configuration files are read in this precedence order, lowest to highest:

```text
~/.config/mcp/mcp.json
~/.travis234/agent/mcp.json
<project>/.mcp.json
<project>/.travis234/mcp.json
```

The adapter never reads `~/.pi` or another product's private state. All
Travis-owned persistent paths remain under `~/.travis234`.

Project files are ignored until Travis project trust resolves positively. In an
unknown or denied project, authorized global MCP configuration remains available
and `mcp({})` states that project configuration was ignored. After `/trust`, the
user applies the new trust decision through `/reload`.

The MVP reads configuration only. It does not create, reformat, or rewrite any
MCP file.

### Supported server entries

Stdio:

```json
{
  "mcpServers": {
    "local-server": {
      "command": "example-mcp",
      "args": ["--stdio"],
      "cwd": "/optional/path",
      "env": {
        "SERVICE_TOKEN": "${SERVICE_TOKEN}"
      },
      "requestTimeoutMs": 1800000
    }
  }
}
```

Streamable HTTP:

```json
{
  "mcpServers": {
    "remote-server": {
      "url": "https://example.com/mcp",
      "headers": {
        "Authorization": "Bearer ${REMOTE_TOKEN}"
      }
    }
  }
}
```

Each entry must specify exactly one of `command` or `url`. The MVP rejects
unknown transport selectors and unsupported authentication fields with
source-attributed validation errors.

### Merge and failure behavior

A higher-precedence definition replaces the complete lower-precedence server
definition. Definitions are not merged field by field. This prevents a changed
URL or command from inheriting credentials or arguments from a less-preferred
source.

Any present authorized file must contain valid UTF-8 JSON with an object-shaped
`mcpServers` mapping. An invalid authorized file disables the adapter for that
session and preserves a source-attributed diagnostic. It does not silently fall
back to a configuration the user may have intended to override.

## Secret boundary

Configuration may contain literal non-secret values plus environment references
in stdio `env` values and HTTP header values:

```text
${NAME}
$env:NAME
```

The real value must already exist in the Travis process environment. The adapter
does not automatically read `.env`. For native invocation, users export the
required variables before launching. A container launched through the existing
explicit `--dotenv` boundary receives those values as process environment, so
the adapter still uses the same rule.

Literal values remain valid for non-secret server settings. Secret-bearing stdio
environment variables and HTTP headers must use references; documentation and
validation call out common sensitive headers such as `Authorization`, `Cookie`,
and `Proxy-Authorization`. The adapter cannot reliably classify every custom
header, so configuration remains trusted code and users must not place secrets in
unrecognized literal fields.

Resolution occurs only when connecting to the target server. A missing or empty
referenced variable fails before a child starts or an HTTP request is sent.

The adapter never:

- writes a resolved value back to configuration;
- includes resolved headers or environment values in exceptions, diagnostics,
  status, tool details, or session JSONL;
- executes `!command` or another secret-producing subprocess;
- stores plaintext credentials; or
- implements OAuth in the MVP.

Stdio children receive a small operational environment needed for process
execution plus the entry's explicitly configured variables. They do not inherit
the entire ambient environment. The implementation delegates this baseline to
the official SDK's documented stdio allowlist and adds only explicitly configured
values; it does not reconstruct or broaden that allowlist itself.

### Consent model

Installing and activating the trusted extension, placing a server in an
authorized configuration source, and keeping the `mcp` tool active together form
the user's consent for calls to that server. The MVP adds no per-call or
per-server-session confirmation dialog. This matches Travis's existing tool
authorization model and the approved Pi-default behavior, but it is deliberately
broader than a granular approval UI. Users must disable the extension or remove a
server they do not authorize. Pattern-based or interactive approvals are a
possible later extension and are not inferred from untrusted MCP tool
annotations.

## Public `mcp` tool

The extension registers one compact tool named `mcp`. Its schema follows Pi's
proxy ergonomics without exposing Pi-specific runtime internals.

Status, with no connection:

```python
mcp({})
```

Lazy connect and list:

```python
mcp({"server": "github"})
```

Search one explicit server:

```python
mcp({"server": "github", "search": "open issues"})
```

Describe one tool:

```python
mcp({"server": "github", "describe": "search_issues"})
```

Call one tool:

```python
mcp({
    "server": "github",
    "tool": "search_issues",
    "args": {"query": "is:open label:bug"},
})
```

### Dispatch rules

- An empty object returns configuration and connection status without connecting.
- `server` alone connects lazily when necessary and lists that server's tools.
- `search` requires `server`, connects only that server, and searches its
  in-memory metadata.
- `describe` requires `server` and returns the original MCP input schema.
- `tool` requires `server`; `args` defaults to an empty object.
- Conflicting operation fields fail validation with one corrective example.
- Tool names remain the server's original MCP names.
- The adapter performs no automatic tool-call retry.
- No operation implicitly connects every configured server.

Tool discovery follows SDK pagination until `next_cursor` is absent. Cursor
cycles, excessive page counts, or excessive aggregate entries fail with a bounded
error rather than looping forever or accepting an unbounded catalog. Search
returns a small ranked result set and asks the model to refine broad queries; it
does not dump every schema into context.

The tool description includes configured server names but not tool catalogs,
credentials, instructions, or schemas. Tool metadata enters model context only
when the model requests list, search, or describe output.

MCP `isError` responses are converted to Travis tool errors through the existing
`tool_result` lifecycle event. The handler is scoped to `toolName == "mcp"` and
adapter-owned result details, so it cannot change unrelated tools.

## Runtime ownership

The extension factory registers the tool and lifecycle handlers. It does not read
configuration, spawn a process, or make a network request.

On `session_start`:

1. snapshot the generation-guarded context values needed by the runtime;
2. determine project trust;
3. read and validate authorized configuration;
4. create an empty session runtime; and
5. re-register the same proxy definition with the authorized server names in its
   compact description; and
6. expose status without opening transports.

That same-name refresh uses the existing extension registry and does not add a
second tool. Normal Travis allowlists remain authoritative: installing the
adapter does not force `mcp` into a session that excludes it.

Each configured server has a state record and a connection lock. The first
targeted operation enters the official SDK client context and retains it for the
active Travis session. Simultaneous initial requests for the same server share
one connection attempt. Different servers remain independently executable under
Travis's existing bounded tool coordinator.

There is no background eager connection, health-check loop, idle timer, private
thread pool, or adapter-owned parallel scheduler in the MVP.

### Connection failure

A failed connection is closed and discarded. The failure is returned only for
the targeted server. A later explicit operation may retry. Failure of one server
does not disable other configured servers.

The adapter does not retry MCP tool calls because it cannot safely infer
idempotency from an arbitrary server.

## Cancellation, timeout, and shutdown

Every connection, discovery request, and tool call combines:

- the Travis tool abort signal; and
- the adapter session-shutdown signal.

User cancellation or session replacement always wins over an MCP timeout.

`requestTimeoutMs` is optional per server. A positive integer applies to
initialization, discovery, and tool calls. Omitted or non-positive values use the
official SDK default. There is no adapter hard-coded ten-minute ceiling. This
setting affects MCP only; it does not change model-call or subagent timeouts.

On `session_shutdown`, `/reload`, replacement, or application exit:

1. mark the runtime closed so no new operation can begin;
2. signal all active adapter work;
3. exit HTTP and SDK client contexts;
4. give the SDK's documented stdio close/wait/kill sequence a short bounded
   window;
5. cancel the owning transport task if SDK cleanup does not settle;
6. remove session-owned spill files; and
7. clear in-memory metadata and secret-bearing resolved values.

Shutdown is idempotent. Repeated lifecycle events cannot double-close transports
or signal a PID that has been replaced.

## Result conversion and output guard

MCP result blocks convert as follows:

| MCP result | Travis result |
| --- | --- |
| text | `TextContent` |
| image | native `ImageContent` with base64 and MIME type |
| structured content | compact non-secret details, with bounded JSON text synthesized only when ordinary content has no equivalent text |
| resource link | explicit URI text block |
| embedded textual resource | bounded text block with URI and MIME type |
| unsupported binary/audio | descriptive placeholder with type and size |

Unsupported blocks are never silently dropped. Resource discovery itself remains
out of scope; this table only handles content returned by an MCP tool.

The default text guard matches the proven Pi adapter limits:

- 50 KiB maximum inline UTF-8 text;
- 2,000 maximum inline lines; and
- a compact preview when either limit is exceeded.

The full oversized text is written to a random session-owned temporary file with
mode `0600`. The result names that file so ordinary Travis `read` or `grep` can
inspect it. Spill files are removed at session shutdown. Images pass through
without text truncation.

Tool details never retain raw headers, expanded environment values, SDK client
objects, transports, or complete oversized protocol results.

## Error behavior

Errors use bounded, source-aware messages and preserve enough context to recover:

- invalid config identifies the file and JSON/validation location;
- missing environment variables identify names, never values;
- unknown servers list configured server names;
- unknown tools recommend listing or searching the named server;
- timeout errors include server, operation, and configured duration;
- transport failures identify stdio versus HTTP without echoing headers or env;
- MCP tool errors preserve bounded server-provided text; and
- cancellation remains cancellation rather than being rewritten as timeout.

Stdio stderr is bounded and control-character sanitized before appearing in a
diagnostic. Protocol payloads and secret-bearing launch configuration are never
logged.

## Tests and implementation order

Every defect discovered during implementation starts with a failing regression
before its fix.

Implementation proceeds in this order:

1. Real-wheel installation/import proof.
2. Package skeleton and static extension registration.
3. Configuration precedence, trust, validation, and secret resolution.
4. Static `mcp({})` status with a fake runtime.
5. Official SDK client wrapper and lazy stdio connection.
6. Streamable HTTP connection.
7. List, search, describe, and call dispatch.
8. MCP error conversion and result normalization.
9. Output guarding and spill cleanup.
10. Concurrent connection deduplication, cancellation, timeout, and shutdown.
11. Installed-wheel TUI verification.

### Focused tests

The adapter package will cover:

- source precedence and whole-definition replacement;
- project config ignored before trust and admitted after trust/reload;
- invalid-file fail-closed behavior;
- exact environment-reference resolution and redaction;
- no automatic dotenv access;
- no I/O during factory load or status;
- explicit-server requirements and conflicting operation validation;
- lazy connection and one connection under concurrent first use;
- real stdio initialization, list, call, timeout, abort, and child cleanup;
- real Streamable HTTP initialization, list, call, timeout, and closure;
- text, image, structured, resource-link, embedded-resource, unsupported, and
  `isError` result conversion;
- 50 KiB/2,000-line output limits, mode `0600`, and shutdown removal;
- server failure isolation;
- reload/session-replacement staleness; and
- absence of credentials from captured output, exceptions, details, and JSONL.

Tests use local deterministic MCP fixtures. They do not require public MCP
services, browser OAuth, or live credentials.

### Repository-level verification

Before reporting implementation complete:

- run adapter package tests;
- run focused Travis extension/package regressions;
- run the complete Travis Python suite;
- run the npm launcher suite;
- build the root Travis wheel and sdist;
- build the adapter wheel and sdist;
- run package metadata checks for both distributions;
- install the adapter wheel through a clean installed Travis wheel;
- run relevant container smoke checks; and
- confirm the two protected user-owned untracked documents remain untouched.

### Installed-wheel TUI scenario

Use the existing main-branch `.env` only through Travis's established credential
boundary and never print it. Run one continuous five-prompt TUI session with the
configured `minimax-m3` model and deterministic local MCP fixtures:

1. report status without connecting;
2. lazily connect and list the stdio fixture;
3. search and describe one fixture tool;
4. call a tool and verify text/structured conversion; and
5. exercise a controlled error or oversized result, then exit and independently
   verify child and spill-file cleanup.

The parent test harness independently verifies event traces, process state, and
cleanup rather than trusting the model's narrative.

## Release boundary

If no Travis core change is required, the root Travis version remains unchanged
and only `travis234-mcp-adapter` needs an independent Python-package release.
There is no npm or GHCR artifact for the adapter itself.

If the conditional loader repair is required, it becomes a separate Travis patch
release decision with the normal root Python, npm, and container verification.
Publishing either artifact, changing GitHub accounts, or pushing release tags is
outside implementation authorization until the user explicitly requests those
GitOps actions.

## Explicit non-goals

- Native MCP code in the Travis agent loop.
- Changes to model calls, subagent timeouts, or provider catalogs.
- JavaScript or TypeScript extension execution.
- Legacy SSE transport.
- OAuth, bearer-token persistence, or a credential store.
- `!command` secret resolution.
- Automatic `.env` loading.
- Persistent tool metadata cache.
- Direct MCP tools in the provider schema.
- MCP prompt registration.
- MCP resource discovery or generated resource tools.
- Sampling or elicitation callbacks.
- Compatibility imports from Pi, Codex, Claude, Cursor, VS Code, or OpenCode.
- Interactive MCP setup or status panels.
- MCP scripting, MCP Apps/UI, browser windows, or native webviews.
- Background reconnect or health checks.
- Automatic package installation or update.

## Success criteria

The design is successful when a clean Travis234 installation can install the
separate adapter package, load authorized layered configuration, lazily call
local or remote MCP tools through one bounded proxy, propagate cancellation, and
leave no owned process or spill file after shutdown—without changing the agent
loop, provider layer, root dependencies, or existing behavior when the adapter
is absent.
