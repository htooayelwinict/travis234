# Travis234 MCP Adapter

`travis234-mcp-adapter` 0.2.0 is the optional MCP client extension for Travis234. It uses the official MCP Python SDK v2 (`mcp>=2,<3`) and keeps MCP dependencies outside the core distribution.

Travis234 remains the MCP client. At session start, the adapter discovers authorized configured servers and registers each admitted remote tool as a native Travis definition with its real input schema. It does not make Travis234 an MCP server.

## Install and activate

```bash
travis234 install 'travis234-mcp-adapter==0.2.0'
```

Restart Travis234 after the first installation. After an update, restart or run `/reload`.

```bash
# Default Travis234 tools plus native MCP tools
travis234 --cwd . --mcp

# Native MCP tools only
travis234 --cwd . --no-tools --mcp

# Explicit built-ins plus native MCP tools
travis234 --cwd . --tools read,bash --mcp

# Generic MCP-only selector
travis234 --cwd . --tools mcp
```

`--mcp` is additive and process-local. It neither installs the adapter nor edits MCP configuration. `--mcp --exclude-tools mcp` is rejected.

The literal `mcp` definition is a status controller. It accepts exactly an empty object:

```json
{}
```

It reports configured and connected servers, registered native names, bounded diagnostics, and ignored untrusted project sources. It has no list, search, describe, tool, or args proxy fields and status does not connect when the family is inactive.

## Native names

The preferred generated form is:

```text
mcp__<configured-server-name>__<remote-tool-name>
```

The configured key—not server-reported metadata—owns the server segment. Safe names use `[A-Za-z0-9_-]` and are at most 64 characters. Unsafe or long names are normalized and receive a deterministic hash suffix. Examples:

```text
mcp__ghost-os__ghost_context
mcp__ghost-os__ghost_screenshot
mcp__filesystem__read_text_file
```

Generated names are discovered after startup CLI validation. `--tools mcp__server__tool` is therefore intentionally unsupported. Select `mcp`, then use `includeTools` and `excludeTools` for startup filtering. Interactive selection may choose concrete native names after discovery.

## Configuration and trust

The adapter reads `mcpServers` from four files in increasing precedence:

1. `~/.config/mcp/mcp.json`
2. `~/.travis234/agent/mcp.json`
3. project `.mcp.json`
4. project `.travis234/mcp.json`

Project files are ignored until that project is trusted. A higher-precedence server entry replaces the complete lower-precedence entry; fields are not merged. The adapter never writes these files and introduces no alternate Travis234 state path.

### stdio

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/absolute/allowed/path"],
      "cwd": "/absolute/working/directory",
      "env": {
        "SERVICE_TOKEN": "${SERVICE_TOKEN}",
        "LOG_LEVEL": "warning"
      },
      "requestTimeoutMs": 30000,
      "lifecycle": "lazy",
      "includeTools": ["list_allowed_directories", "read_text_file"],
      "excludeTools": ["write_file"]
    }
  }
}
```

### Streamable HTTP

```json
{
  "mcpServers": {
    "context7": {
      "url": "https://mcp.context7.com/mcp",
      "headers": {
        "Authorization": "Bearer ${CONTEXT7_TOKEN}"
      },
      "requestTimeoutMs": 30000
    }
  }
}
```

Each entry must specify exactly one non-empty `command` or `url`. `args`, `cwd`, and `env` are stdio-only; `headers` are HTTP-only. The only accepted lifecycle declaration is `"lazy"`.

`includeTools`, when present, is an exact-name allowlist. An explicit empty array admits no tools. `excludeTools` is applied afterward and wins. Both fields require arrays of unique non-empty strings. Globs, regular expressions, and partial matching are not supported. Missing included names and redundant exclusions appear only as bounded diagnostics.

## Credentials

Secrets remain in the process environment. Values may use `${NAME}` interpolation or exact `$env:NAME`. Expansion is non-recursive and applies only to `env` and `headers` values. Sensitive stdio environment keys and the `Authorization`, `Cookie`, and `Proxy-Authorization` headers must use an environment reference; literal secrets are rejected.

The adapter does not load `.env`, serialize resolved credentials, place them in tool details, or print them. The npm launcher can forward only an explicitly selected file through its existing `--dotenv /path/to/.env` boundary.

## Discovery and context bounds

Native exposure is deterministic and bounded after configuration filters:

- 64 tools per server and 128 per session;
- 64 KiB for one serialized input schema;
- 256 KiB of schemas per server and 512 KiB per session;
- 4 KiB UTF-8 descriptions per tool;
- 100 pagination pages and 10,000 raw tools as protocol guards;
- four concurrent server discoveries and a 30-second total discovery phase;
- 8 KiB initialization guidance per server and 32 KiB per session.

Invalid individual schemas are skipped. A server exceeding its accepted tool or schema budget is rejected as a whole. Session admission processes configured names lexicographically and never truncates a server to an arbitrary prefix. Name collisions never overwrite another Travis or extension tool.

Server instructions are included only for servers with admitted native tools. Each block is labeled as MCP server-provided operational guidance, has control characters sanitized, and cannot override system, user, project, trust, tool-policy, or credential instructions.

Tool-list change notifications are coalesced. Catalogs reconcile only at the next safe `before_agent_start` boundary, never during an active tool batch. Catalogs and instructions live in session memory and are rediscovered after reload or process restart.

## Calls, concurrency, and results

Every generated closure captures the exact configured server and original remote tool name. Calls are at-most-once. A timeout, cancellation, or uncertain transport completion discards the affected connection and never automatically replays the call. A later explicit model call may reconnect.

Execution is sequential by default. Only MCP `readOnlyHint: true` selects Travis234's existing `parallel` execution mode, which remains subject to the core bounded parallel coordinator.

`requestTimeoutMs` applies to initialize, discovery, and tool calls for that server. It does not modify provider, process, subagent, or workflow timeouts. A smaller positive per-server timeout wins within the 30-second discovery phase.

Result conversion preserves text, supported images, structured content, resource summaries, MCP error state, and adapter-owned identity details. Bounds are:

- 50 KiB or 2,000 text lines inline, with a `0600` session-owned spill file beyond either limit;
- eight accepted images;
- 10 MiB decoded bytes per image and 20 MiB per result;
- PNG, JPEG, GIF, and WebP only.

Malformed, unsupported, oversized, and excess images become bounded placeholders without exposing base64 data. Spill files are deleted on reload and shutdown.

## Security model

Configured servers are operator-authorized executables or network integrations. Review packages, pin versions when reproducibility matters, restrict filesystem roots, and provide the minimum credentials. Project configuration remains trust-gated. The adapter shapes server and transport failures without echoing arguments, headers, environment values, or raw exception messages.

The supported transports are stdio and Streamable HTTP. This release does not implement legacy SSE, MCP OAuth, prompts, resource discovery, sampling, elicitation, scripting, Apps/UI, or background workflow scheduling.

## Migration from 0.1.x

Version 0.2.0 replaces the single list/search/describe/call proxy with native tool definitions. There is deliberately no proxy compatibility alias. Use `mcp({})` for status and call generated tools directly. Existing authorized configuration paths, trust behavior, environment references, and transports remain unchanged.

## Troubleshooting

- If `--mcp` says the adapter is missing, run the pinned install command and restart.
- If a project server is absent, approve the project and run `/reload`.
- If a native tool is absent, inspect `mcp({})` for filter, schema, collision, budget, connection, or discovery diagnostics.
- If a call times out, set a justified positive `requestTimeoutMs`; do not add automatic side-effect retries.
- If Ghost desktop actions fail while its tools register, run `ghost doctor`. Accessibility and Screen Recording permission checks are external to MCP protocol discovery.

References: [MCP specification](https://modelcontextprotocol.io/specification), [official Python SDK](https://github.com/modelcontextprotocol/python-sdk).
