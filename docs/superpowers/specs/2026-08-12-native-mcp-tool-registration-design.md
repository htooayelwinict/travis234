# Native MCP Tool Registration Design

Date: 2026-08-12
Status: approved for implementation planning; no runtime implementation in this commit

## Decision summary

Travis234 remains an MCP client. Configured MCP servers remain external servers.
The optional `travis234-mcp-adapter` will discover each configured server's MCP
tools and register them as ordinary Travis234 tools before the first model turn.

For a configured server named `ghost-os`, the model-visible surface includes
names such as:

```text
mcp__ghost-os__ghost_context
mcp__ghost-os__ghost_find
mcp__ghost-os__ghost_click
mcp__ghost-os__ghost_screenshot
mcp__ghost-os__ghost_run
```

The adapter will not expose Travis234 as an MCP server. It will not compress all
remote operations behind one call proxy. The existing literal `mcp` tool becomes
a compact activation and status boundary; remote operations use their native
schemas and direct generated names.

This design supersedes the proxy-only, lazy-discovery, and "direct MCP tools"
non-goal portions of these earlier designs:

- `docs/superpowers/specs/2026-08-08-travis234-mcp-adapter-design.md`
- `docs/superpowers/specs/2026-08-08-additive-mcp-cli-flag-design.md`

All unaffected configuration, trust, secret-resolution, transport, result,
cancellation, cleanup, packaging, and verification contracts remain in force.

## Goals

- Make a normal MCP server plug into Travis234 as a set of native tools.
- Preserve the additive meaning of `travis234 --mcp`.
- Make `--no-tools --mcp`, explicit tool selection, and MCP exclusion safe and
  internally consistent.
- Preserve exact remote server and tool identities when dispatching calls.
- Reuse the adapter's official MCP SDK runtime, cancellation, result conversion,
  output spill, trust, and credential boundaries.
- Keep discovery, model-visible metadata, tool results, and cleanup bounded.
- Support Ghost OS without Ghost-specific production code.

## Non-goals

- Exposing Travis234 tools, sessions, prompts, or turns through an MCP server.
- Adding `mcp` as a root Travis234 dependency.
- Changing agent-loop ordering, iteration budgets, provider retry policy,
  compaction, or the bounded tool-execution coordinator.
- Automatically importing configuration from another product's private state.
- Supporting MCP prompts, resources, sampling, elicitation, Apps/UI, OAuth, or
  legacy SSE in this increment.
- Persisting an MCP catalog or credentials in session JSONL.
- Automatically retrying an MCP tool call.
- Generating a dedicated Python function or source file for every remote tool.
- Adding Ghost-specific schemas, dispatch code, or instructions to Travis234.

## Evidence from Ghost OS

The disposable Ghost OS checkout was inspected at commit `991aa48`. Its MCP
entry point is a stdio server that implements initialization, `tools/list`, and
`tools/call`. The inspected build exposes 29 tools covering perception, actions,
recipes, and learning. Its complete `tools/list` payload is approximately 14 KiB,
including approximately 9 KiB of input schemas.

That surface is suitable for direct registration. Ghost also provides MCP
initialization instructions, text and image results, and action-oriented tools.
Those characteristics validate the need for native schemas, bounded server
guidance, image-aware result conversion, cancellation, and conservative
execution ordering.

Ghost's handwritten transport, blocking dispatch, incomplete timeout behavior,
and limited test coverage are not implementation patterns for Travis234. The
adapter continues to delegate protocol and transport correctness to `mcp>=2,<3`.

## Approaches considered

### Eager native registration with a tool family — selected

When MCP is active, discover configured servers with bounded concurrency,
construct one `ToolDefinition` per accepted remote tool, and activate those
definitions through an `mcp` tool family.

Advantages:

- every remote tool is visible to the model with its real input schema;
- calls use the same validation and execution machinery as ordinary Travis
  tools;
- all current providers receive a conventional tool surface;
- CLI activation and exclusion remain centralized and testable; and
- Ghost OS works without a discovery call from the model.

Costs:

- MCP activation performs bounded startup I/O;
- tool metadata consumes model context; and
- Travis needs one generic, narrowly scoped tool-family concept.

### Lazy native materialization after a proxy call

Keep the proxy visible initially and add remote tools after list, search, or
describe. Travis can represent newly added tools in tool results, and some
providers support deferred tool references.

Rejected as the primary design because the native tools are not available on
the first turn, provider behavior differs, and the model must still learn a
Travis-specific proxy workflow before using a normal MCP server.

### Discovery before application construction

Connect to servers in the root CLI, obtain all generated names, and pass them to
the existing literal allowlist before constructing `CodingApp`.

Rejected because it duplicates asynchronous runtime ownership, trust resolution,
secret handling, error shaping, and shutdown outside the adapter's session
lifecycle.

## Architecture and ownership

The feature remains split across two boundaries.

### Travis core: generic tool-family policy

`ToolDefinition` gains an optional `activation_group` string. It is generic
extension metadata, not MCP-specific runtime logic. A definition with
`activation_group="mcp"` is governed by both its concrete name and the `mcp`
family selector.

The session tool controller applies these rules:

1. A tool is allowed when the allowlist is absent, its concrete name is allowed,
   or its activation group is allowed.
2. A tool is excluded when either its concrete name or its activation group is
   excluded.
3. Selecting a group activates the concrete group tool, if present, followed by
   allowed registered members in stable registration order.
4. Selecting a concrete generated name activates only that definition.
5. Concrete tool names are deduplicated before they reach the agent.
6. Registry refresh never activates a group that was not already selected.

The family selector must remain recoverable while tools are registered and
unregistered dynamically. The literal `mcp` status definition serves as the
stable selected member during an active MCP session. This makes a post-discovery
refresh expand `mcp` without adding a second persisted selector or changing
session JSONL.

No generated MCP definition bypasses the ordinary allowlist, exclusion list, or
active-tool set.

### Adapter package: MCP-specific discovery and dispatch

The extension factory remains I/O-free. It registers the literal `mcp` status
definition and lifecycle handlers. It does not read configuration, resolve
secrets, connect, or spawn a child during extension import.

At `session_start`, the adapter:

1. clears definitions, diagnostics, spills, and connections from the previous
   generation;
2. loads authorized MCP configuration using the existing precedence and trust
   rules;
3. checks whether the `mcp` family is active;
4. creates the session-owned `McpRuntime`;
5. connects to configured servers and loads catalogs with bounded concurrency;
6. validates and translates accepted MCP tools into `ToolDefinition` objects;
7. registers each definition with `activation_group="mcp"`;
8. refreshes the selected `mcp` family once after the registration batch; and
9. refreshes the literal status definition with bounded diagnostics and
   server-provided guidance.

If the `mcp` family is not active, configuration may be validated for status but
the adapter does not open a transport. Installing the package alone therefore
does not start configured commands or HTTP sessions.

The adapter owns all mappings between model-visible generated names and exact
`(configured_server_name, remote_tool_name)` pairs. Generated names never become
transport identifiers.

## CLI contract

The existing commands retain their operator-level meaning, with `mcp` now
selecting a family:

| Command | Active tools |
| --- | --- |
| `travis234 --mcp` | normal default tools, `mcp` status, and discovered native MCP tools |
| `travis234 --no-tools --mcp` | `mcp` status and discovered native MCP tools only |
| `travis234 --tools read,bash --mcp` | `read`, `bash`, `mcp` status, and discovered native MCP tools |
| `travis234 --tools mcp` | `mcp` status and discovered native MCP tools only |

`--mcp --exclude-tools mcp` remains an error. `--exclude-tools mcp` excludes the
status definition and every member of the family.

Generated names are discovered after application construction. Passing a
generated name directly to the startup `--tools` or `--exclude-tools` options is
not supported in this increment because CLI unknown-name validation runs before
MCP session discovery. Per-server `includeTools` and `excludeTools` configuration
provide startup filtering. Interactive tool selection may select concrete names
after discovery.

If the adapter is absent, `--mcp` retains the existing installation error. If no
server is configured, startup succeeds with only the status definition and a
bounded configuration hint.

## Native name mapping

The preferred visible form is:

```text
mcp__<configured-server-name>__<remote-tool-name>
```

The configured key, not self-reported server metadata, owns the server segment.
This prevents a server update from silently renaming its tools.

Names use the cross-provider-safe subset `[A-Za-z0-9_-]` and a maximum of 64
characters. When both original segments already use that subset, are non-empty,
and fit the limit, their spelling is retained. Thus Ghost's `ghost_context`
becomes exactly `mcp__ghost-os__ghost_context`.

Otherwise the adapter:

1. replaces each run of unsupported characters with `_`;
2. trims unsupported leading or trailing separators;
3. supplies `server` or `tool` for an empty normalized segment;
4. appends the first ten lowercase hexadecimal characters of a SHA-256 digest
   derived from the exact configured server name, a NUL separator, and the exact
   remote tool name; and
5. truncates only the readable prefix needed to keep the complete result within
   64 characters.

Hashing every normalized or truncated name makes the mapping independent of
catalog order. Duplicate exact remote names, a remaining generated-name
collision, or a collision with an existing Travis/extension tool is rejected for
that tool and shown in status. Registration never overwrites another tool.

## Schema and definition conversion

For each accepted MCP tool:

- `name` is the generated safe name;
- `label` identifies the configured server and original tool;
- `description` is the bounded remote description prefixed with its MCP source;
- `parameters` is the original MCP `inputSchema` after structural validation;
- the execution closure captures the exact server and remote name;
- result rendering uses the adapter's existing MCP result conversion; and
- adapter-owned result details record generated name, exact remote name, server,
  error state, and spill metadata without secrets.

An invalid schema rejects only the affected tool. It does not prevent healthy
tools on the same server from registering. The schema is compiled through the
ordinary Travis `AgentTool` validation path before activation.

Descriptions are limited to 4 KiB of UTF-8 text per tool. The adapter does not
copy MCP annotations or metadata wholesale into the provider schema.

Execution mode defaults to `sequential`. A tool may use `parallel` only when its
MCP annotations explicitly set `readOnlyHint: true`. Parallel calls still pass
through Travis's existing bounded coordinator; the adapter does not create an
unbounded tool-call scheduler.

## Catalog and context bounds

Proxy-era catalog limits were designed for metadata hidden from the model and
are too large for native registration. Native exposure applies all of these
limits after configuration filters:

- at most 64 tools from one server;
- at most 128 tools across the active session;
- at most 64 KiB for one serialized input schema;
- at most 256 KiB of serialized schemas from one server; and
- at most 512 KiB of serialized schemas across the active session.

A server that exceeds a per-server count or byte budget is rejected as one unit
instead of exposing an arbitrary prefix. If accepting the next server would
exceed an aggregate budget, that server is rejected. Servers are admitted in
lexicographic configured-name order, so precedence merges cannot make the
decision depend on dictionary insertion history.

Server entries may add exact-name filters:

```json
{
  "mcpServers": {
    "large-server": {
      "command": "large-mcp",
      "includeTools": ["search", "read_item"],
      "excludeTools": ["delete_item"]
    }
  }
}
```

`includeTools`, when present, admits only listed exact remote names.
`excludeTools` is applied afterward and wins. Values are unique, non-empty
strings; globbing and regular expressions are not supported. Missing included
names and redundant exclusions produce bounded status diagnostics but do not
disable otherwise valid tools.

No catalog is persisted. `/reload`, session replacement, or a new process
performs fresh authorized discovery.

## Initialization instructions

The MCP SDK exposes server-provided initialization instructions after the
handshake. The adapter includes them only when that server has at least one
registered native tool in the active MCP family. Interactive selection of an
individual generated tool does not dynamically rewrite the instruction block;
the next MCP discovery or reload rebuilds it from the registered catalog.

Instructions are bounded to 8 KiB per server and 32 KiB in aggregate. They are
added once through the active `mcp` status definition's prompt guidance rather
than copied onto every generated tool. Each block is identified by configured
server name and explicitly framed as server-provided operational guidance that
cannot override system messages, user requests, project instructions, trust,
tool policy, or credential rules.

Control characters are sanitized. Truncation is explicit. Instructions are
session memory only and are not written to configuration or JSONL.

## Discovery lifecycle

Native tools must exist before the first provider request, so MCP activation is
eager at the session boundary while ordinary tool calls remain connection-reuse
based.

Discovery uses at most four concurrent server tasks. The complete registration
phase has a 30-second wall-clock budget. A smaller positive
`requestTimeoutMs` still wins for an individual server operation. The 30-second
startup budget does not impose a timeout on later MCP tool calls.

A server that does not initialize and finish bounded pagination in time is
closed, marked unavailable, and omitted from native registration. Healthy
servers still register. Configuration-file validation remains fail-closed for
the whole adapter because a malformed higher-precedence file may represent an
intended override.

The runtime retains each successful server connection for the Travis session.
`/reload`, session replacement, shutdown, or application exit unregisters its
generated definitions, cancels active work, closes SDK and transport contexts,
removes session-owned spill files, and clears catalogs, diagnostics, instructions,
and resolved secret-bearing values. Cleanup is generation-guarded and idempotent.

## Tool-list changes

When a server advertises tool-list change notifications, the runtime records a
bounded dirty marker. It does not mutate the active tool registry from the MCP
transport thread or during a running Travis tool batch.

At the next `before_agent_start` boundary, the adapter rediscovers only dirty
servers, reapplies filters and budgets, replaces that server's native definitions
as one safe-boundary batch, and rebuilds the active family once before the
provider request. No model request or tool batch can observe the intermediate
registry state. If refresh fails, the previous definitions are removed because
their advertised schemas may be stale; status reports the failure. Notifications
received during reconciliation coalesce into one additional later refresh.

Servers without change notifications use the session-start snapshot until
`/reload` or session replacement.

## Call path and execution guarantees

A native tool execution follows this path:

```text
provider tool call
  -> normal Travis argument validation
  -> generated ToolDefinition closure
  -> exact configured server actor
  -> MCP tools/call with exact remote name and arguments
  -> MCP result conversion and output guard
  -> normal Travis ordered tool continuation
```

The adapter reuses the existing lazy per-server connection guard if a retained
connection was discarded after cancellation or timeout. A later invocation may
reconnect, but the failed invocation is never replayed. MCP annotations do not
authorize automatic retry.

Cancellation remains linked to the Travis abort signal. Timeout or cancellation
discards the affected connection because completion state is uncertain. Other
servers remain usable. One server failure cannot change tool results already
ordered by the core agent loop.

The literal `mcp` tool has an empty-object schema and accepts no remote-call
operation. A call such as `mcp({})` reports configured
servers, connection state, registered native names, skipped tools, budget and
schema failures, ignored untrusted project sources, and bounded recovery advice.
Removing the proxy call path ensures native filters cannot be bypassed and keeps
one canonical invocation path.

## Result and media bounds

Existing conversion remains authoritative for text, structured content, images,
resource links, embedded textual resources, binary placeholders, audio
placeholders, and MCP `isError` propagation.

The existing 50 KiB/2,000-line aggregate text limit and `0600` session spill
files remain unchanged. Native calls add media limits before constructing model
content:

- at most eight image blocks per tool result;
- at most 10 MiB decoded data for one image;
- at most 20 MiB decoded image data across one result; and
- only MIME types accepted by Travis's existing image content path.

An oversized or unsupported image becomes a bounded textual placeholder with
type and size. Excess image blocks become one summary placeholder. The adapter
does not spill binary media to disk in this increment.

Structured content synthesized as text passes through the same aggregate text
guard. Result details contain no raw headers, expanded environment values,
transport objects, or unbounded protocol payloads.

## Error behavior

The literal status tool is the recovery surface. Diagnostics are bounded,
control-character sanitized, and grouped by configured server.

- invalid authorized configuration disables the adapter for that session;
- missing secret references identify variable names but never values;
- one server's initialization or pagination failure omits only that server;
- one invalid tool schema omits only that tool;
- per-server budget overflow omits that server as one unit;
- aggregate budget overflow omits the next whole server in stable order;
- name collisions never overwrite a registered tool;
- cancellation remains cancellation rather than becoming a tool error;
- MCP `isError` remains a Travis tool error with bounded server content; and
- stale generations cannot publish registrations or results.

Stdio stderr remains suppressed or bounded and sanitized according to the
existing adapter contract. Commands, resolved environment values, headers, and
protocol payloads are never echoed in status or logs.

## Compatibility and release boundary

This is an intentional `0.x` adapter behavior change. The adapter advances from
`0.1.1` to `0.2.0`: `mcp` becomes status-only and native generated tools become
the remote invocation surface. Documentation must identify the change and show
the new names. No compatibility alias or alternate state path is introduced.

The generic `activation_group` field changes Travis core and therefore requires
a root Travis234 patch release decision with aligned Python, npm launcher, and
container artifacts. The adapter remains an independently packaged optional
distribution. Publishing artifacts, pushing tags, or changing remote Git state
requires separate user authorization.

## Test strategy and implementation order

Feature work follows test-driven development. Every bug found during the work
starts with a focused failing regression before its fix.

Implementation order:

1. Add failing core tests for generic activation-group allow, exclude, expansion,
   refresh, deduplication, and concrete selection.
2. Implement the minimal `ToolDefinition` and session-controller family policy.
3. Add failing adapter tests for deterministic native names and schema conversion.
4. Implement native definition construction and exact reverse mapping.
5. Add failing lifecycle tests for inactive, active, partial failure, reload,
   generation, and bounded discovery behavior.
6. Implement session discovery, registration, status, and cleanup.
7. Add failing tests for configuration filters, catalog budgets, initialization
   guidance, list-change reconciliation, and media guards.
8. Implement each focused boundary without changing the agent loop.
9. Update package, CLI, distribution, documentation, and verification records.
10. Run installed-wheel and container smoke scenarios.

### Focused core coverage

- a group selector admits and activates dynamic members;
- `--no-tools --mcp` exposes no unrelated built-ins or extension tools;
- group exclusion removes the status tool and every member;
- a concrete generated selection does not activate its siblings;
- repeated refresh preserves order and does not duplicate tools;
- dynamic removal removes the concrete active tool;
- extensions without `activation_group` retain existing behavior;
- generated names remain visible to the agent and provider translations; and
- architecture tests preserve RuntimeFacade and owner boundaries.

### Focused adapter coverage

- factory load performs no file, process, or network I/O;
- inactive MCP performs no transport I/O;
- Ghost-shaped names and schemas register as native tools;
- unsafe, long, normalized, duplicate, and colliding names are deterministic;
- exact remote names and arguments reach `tools/call`;
- invalid schemas and server failures are isolated as specified;
- filters and all count/byte budgets fail deterministically;
- discovery concurrency never exceeds four and startup never exceeds its budget;
- initialization guidance is bounded, sanitized, framed, and deduplicated;
- read-only annotations select parallel mode and all other tools are sequential;
- list-change notifications reconcile only at safe boundaries;
- text, structured, image, resource, error, spill, and media limits hold;
- timeout and cancellation do not replay calls;
- reload and shutdown unregister definitions and clean processes and spills;
- credentials do not appear in output, exceptions, details, traces, or JSONL; and
- stdio and Streamable HTTP fixtures both support native discovery and calls.

### Repository-level verification

Before implementation is reported complete:

- run focused core and adapter tests;
- run the complete Python test suite;
- run npm launcher tests;
- build and check root wheel and sdist;
- build and check adapter wheel and sdist;
- install both clean wheels and verify extension discovery;
- run relevant container smoke checks; and
- verify no disposable checkout, credentials, local environment, or spill file is
  tracked or staged.

### Ghost OS smoke scenario

With the disposable Ghost binary configured as an authorized stdio MCP server,
an installed Travis234 and adapter must prove:

1. `travis234 --no-tools --mcp` exposes `mcp` plus all 29 accepted native Ghost
   tools and no ordinary Travis tools;
2. the model-visible name for `ghost_context` is exactly
   `mcp__ghost-os__ghost_context`;
3. a read-only perception call and an action call reach their exact MCP names;
4. a screenshot result follows native image conversion and media bounds;
5. cancellation or controlled failure leaves no replayed action; and
6. exit closes the Ghost child and removes adapter-owned spill files.

The smoke harness independently checks tool lists, calls, process state, and
cleanup. It does not rely on the model's narrative and does not require live
credentials. macOS Accessibility and Screen Recording permissions remain an
external prerequisite for real desktop actions; protocol-level fixture checks
must still run when those permissions are unavailable.

## Success criteria

The design succeeds when any conforming configured MCP server can appear in an
MCP-enabled Travis234 session as bounded native `mcp__server__tool` definitions,
with exact schema-driven calls and ordinary Travis result handling. Ghost OS must
appear as 29 native tools, not as eight calls and not as a Travis-hosted MCP
server. The adapter must preserve trust, secrets, cancellation, cleanup, and
bounded execution while leaving non-MCP sessions unchanged.
