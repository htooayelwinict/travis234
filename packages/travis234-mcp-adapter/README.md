# Travis234 MCP Adapter

`travis234-mcp-adapter` is an optional Travis234 extension that connects the single `mcp` proxy tool to explicitly configured Model Context Protocol servers. It uses the official MCP Python SDK v2 and keeps MCP dependencies out of the core `travis234` distribution.

The adapter is designed for controlled coding-agent use:

- one stable Travis tool schema regardless of how many MCP servers are configured;
- lazy, one-server-at-a-time connections;
- explicit project trust and tool allowlists;
- environment-reference enforcement for credential-shaped fields;
- bounded discovery and result handling; and
- session-owned cancellation, shutdown, and temporary-file cleanup.

The single `mcp` proxy declares `read`, `write`, `execute`, and `network`
effects because a remote server's operation cannot be proven locally. In
Travis234's enforcing tool-policy mode, the approval prompt identifies only the
configured server and normalized proxy operation (`status`, `list`, `search`,
`describe`, or `call`). Tool arguments, headers, environment references, and
resolved secrets are never approval context. Hosts that predate tool-effect
metadata fail adapter loading explicitly instead of silently running the proxy
as an undeclared tool.

It is an MCP client adapter, not an MCP server and not a general compatibility layer for every MCP client feature.

## Requirements

- Travis234 2.4.3 or newer
- Python 3.13
- the command runtime required by each stdio server, such as Node.js for `npx` servers
- network access for remote servers and for package runners that download on first use

Travis234's npm sandbox image already includes Python, Node.js, and npm. Native installations must provide their own server runtimes.

## Install

```bash
travis234 install travis234-mcp-adapter
```

Start a new Travis234 process after installation, or use `/reload` if the adapter has not already been imported in the current process. After `/update`, restart Travis234 so Python cannot reuse the previous adapter package from its module cache.

Installing the adapter does not force the `mcp` tool into a turn. Enable it for the current process with `--mcp`; the flag adds MCP to the tools that would otherwise be active and does not modify any MCP configuration.

Launch with the default Travis234 tools plus MCP:

```bash
travis234 --cwd . --mcp
```

Launch an MCP-only session:

```bash
travis234 --cwd . --no-tools --mcp
```

Or combine MCP with an advanced explicit subset:

```bash
travis234 --cwd . --tools read,bash --mcp
```

The generic `--tools mcp` form remains supported as an explicit MCP-only allowlist.

Manage the separately installed package with the normal Travis234 package commands:

```bash
travis234 list
travis234 update travis234-mcp-adapter
travis234 remove travis234-mcp-adapter
```

For a reproducible installation, pin the adapter source:

```bash
travis234 install 'travis234-mcp-adapter==0.1.3'
```

## Trusted packaged servers

An installed trusted Travis234 extension may register an executable it ships
through the adapter's package-owned server API. A packaged descriptor is
immutable, must name an executable inside its package root, and wins an
exact-name collision with file configuration while status reports the shadowed
entry.

Packaged-server registration is an in-process extension interface, not a user
configuration format. Configure ordinary stdio and HTTP servers through the
MCP configuration files below. Installing an extension remains an
executable-code trust decision.

## Configuration

The adapter reads these files from lowest to highest precedence:

1. `~/.config/mcp/mcp.json`
2. `~/.travis234/agent/mcp.json`
3. project `.mcp.json`
4. project `.travis234/mcp.json`

A higher-precedence server definition replaces the whole lower-precedence definition. Fields are not merged. Project files are ignored until the Travis project is trusted; after `/trust`, use `/reload`. The adapter never reads `~/.pi` and never writes these files.

Each file has this shape:

```json
{
  "mcpServers": {
    "server-name": {
      "command": "server-command"
    }
  }
}
```

Server names must be non-empty strings. Configuration is strict: unknown top-level or server fields are errors rather than silently ignored settings.

Stdio example:

```json
{
  "mcpServers": {
    "local-tools": {
      "command": "example-mcp",
      "args": ["--stdio"],
      "cwd": "/optional/path",
      "env": {
        "SERVICE_TOKEN": "${SERVICE_TOKEN}",
        "LOG_LEVEL": "info"
      },
      "requestTimeoutMs": 1800000
    }
  }
}
```

Streamable HTTP example:

```json
{
  "mcpServers": {
    "remote-tools": {
      "url": "https://example.com/mcp",
      "headers": {
        "Authorization": "Bearer $env:REMOTE_TOKEN"
      }
    }
  }
}
```

### Public server recipes

Context7 over Streamable HTTP:

```json
{
  "mcpServers": {
    "context7": {
      "url": "https://mcp.context7.com/mcp",
      "headers": {
        "CONTEXT7_API_KEY": "$env:CONTEXT7_API_KEY"
      }
    }
  }
}
```

The Context7 API key is optional at the service level but recommended for higher limits. Export it before starting Travis234 if the header is present. Remove the entire `headers` object for anonymous access; do not leave a reference to an unset variable.

Context7 over stdio:

```json
{
  "mcpServers": {
    "context7": {
      "command": "npx",
      "args": ["-y", "@upstash/context7-mcp"]
    }
  }
}
```

Official filesystem server with one deliberately narrow allowed root:

```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "/absolute/path/to/allowed-workspace"
      ],
      "lifecycle": "lazy"
    }
  }
}
```

The filesystem server exposes write-capable tools. Never grant it a home directory, filesystem root, credential directory, or broader tree than the task requires. Pin third-party npm package versions when repeatable behavior matters.

On Windows, stdio servers launched through npm normally use `"command": "cmd"` and prepend `"/c", "npx"` to `args`.

### Server field reference

| Field | Transport | Meaning |
|---|---|---|
| `command` | stdio | Non-empty executable name or path. Mutually exclusive with `url`. |
| `args` | stdio | Array of literal command arguments. Defaults to an empty array. |
| `cwd` | stdio | Optional child working directory. |
| `env` | stdio | String-to-string child environment additions with supported variable expansion. |
| `url` | HTTP | Non-empty Streamable HTTP endpoint. Mutually exclusive with `command`. |
| `headers` | HTTP | String-to-string request headers with supported variable expansion. |
| `lifecycle` | both | Optional compatibility declaration. Only `lazy` is accepted, and it is a no-op. |
| `requestTimeoutMs` | both | Optional integer timeout for initialize, discovery, and tool-call operations. |

Each server must specify exactly one non-empty `command` or `url`. The MVP supports stdio and Streamable HTTP only. Legacy SSE, OAuth, prompts, resource discovery, sampling, elicitation, scripting, direct per-server Travis tools, and MCP Apps/UI are not supported.

For compatibility with shared Pi-style files, a server may declare `"lifecycle": "lazy"`; this is a no-op because the adapter is always lazy. Eager, keep-alive, and other lifecycle modes remain unsupported and are rejected.

## Secrets and consent

Secret values stay in the Travis234 process environment. Use `${SERVICE_TOKEN}` inside a value or exact `$env:SERVICE_TOKEN`; expansion happens only when that server connects and is non-recursive. The adapter does not load `.env` files. Native users export variables before launch. Container users may use the existing explicit `--dotenv /path/to/file` launcher boundary, which supplies process environment without changing adapter behavior.

Literal values are allowed for non-secret settings. Token-, secret-, password-, OAuth-, credential-, and API-key-shaped stdio environment keys require references. `Authorization`, `Cookie`, and `Proxy-Authorization` headers also require references. Resolved values are not written to configuration, status, errors, tool details, or session JSONL.

Installing and activating this trusted extension, listing a server in an authorized configuration file, and leaving the `mcp` tool enabled make the proxy available. Travis234 audit mode preserves existing calls. Enforce mode applies the normal tool-policy approval because the proxy declares all four effects; an approval shows only the configured server and normalized operation.

Treat an MCP server like any other executable or network integration:

1. review its publisher, source, package name, and requested access;
2. restrict filesystem and network scope;
3. keep credentials in the process environment;
4. pin versions where supply-chain reproducibility matters; and
5. remove or disable servers that are not needed for the current task.

## One proxy tool

The proxy connects lazily and always targets one explicit server:

```json
{}
{"server":"local-tools"}
{"server":"local-tools","search":"issue"}
{"server":"local-tools","describe":"search_issues"}
{"server":"local-tools","tool":"search_issues","args":{"query":"open"}}
```

- `{}` reports configured servers without connecting.
- `server` alone lists tools.
- `search` returns at most 20 deterministic matches.
- `describe` returns one tool's full input schema.
- `tool` calls the original MCP tool name once; `args` defaults to `{}`.

Typical TUI workflow:

1. Ask Travis234 to report MCP status. This uses `{}` and does not connect.
2. Ask it to list one named server. This connects that server and returns compact tool summaries.
3. Search when a server has many tools.
4. Describe the chosen tool before calling it when the input shape is unfamiliar.
5. Call the tool with explicit arguments.

Example prompts:

```text
Use MCP status and tell me which servers are configured. Do not connect yet.
Use MCP on context7. Find the official Python MCP SDK and explain stdio_client.
Use MCP on filesystem. List allowed directories before reading anything.
Use MCP on filesystem to read README.md inside the allowed workspace.
```

For direct automation, the same proxy object is supplied by the model as the `mcp` tool arguments. There are no generated `mcp__server__tool` names and no implicit cross-server dispatch.

The adapter does not fan out across servers and does not retry failed calls. `requestTimeoutMs` is optional and applies only to MCP initialize, discovery, and call operations. It does not change model-call, Travis tool, process, or subagent timeouts. Omitted or non-positive values retain official SDK transport defaults.

Catalog discovery rejects repeated cursors, more than 100 pages, or more than 10,000 tools. Aggregate model-visible text is limited to 50 KiB and 2,000 lines. Larger results receive a compact preview and a random mode-`0600` temporary spill path usable with ordinary Travis `read` or `grep`; session shutdown removes adapter-owned spills.

## Results, errors, and cancellation

Text, images, audio, embedded resources, and structured tool output are converted into Travis tool-result blocks. Unsupported content is represented by bounded metadata rather than passed through as an arbitrary object. A server's MCP `isError` result becomes a Travis tool error while preserving bounded server-provided text.

Configuration and connection failures identify the server and safe error class without including resolved headers or environment values. One broken server does not prevent another configured server from connecting.

The adapter does not retry calls because it cannot know whether a remote action is safe to repeat. User cancellation, `/reload`, session replacement, and `/exit` cancel adapter-owned work and close connected clients. Stdio children and spill files are owned by the session and cleaned during shutdown.

## Updating configuration

- Global configuration changes: use `/reload` or start a new process.
- Project configuration changes: trust the project first, then use `/reload`.
- First adapter installation: start a new process, or use `/reload` before the package has been imported.
- Adapter package update: restart Travis234 to guarantee the new Python modules are loaded.

Ordinary startup never installs or updates packages automatically. `--offline` allows already-installed local resources but blocks network package acquisition and server operations that require the network.

## Troubleshooting

### The model cannot see `mcp`

Installation registers the extension but does not override the active-tool policy. Start Travis234 with `--mcp`, or use `--no-tools --mcp` for an MCP-only session. Check `travis234 list` to confirm the adapter package is installed, then restart or `/reload` as described above.

### A project server is missing

Project `.mcp.json` and `.travis234/mcp.json` files are ignored until the project is trusted. Use `/trust`, approve the project, then `/reload`. Global servers remain available independently.

### A server remains disconnected

Disconnected is the normal lazy state. Status alone never starts a server. Name the server to list, search, describe, or call its tools. For stdio, verify the command is installed in the same native or container environment that runs Travis234.

### Configuration reports a missing environment variable

Every referenced variable must exist and be non-empty when that server connects. Export it before starting Travis234, or pass an explicitly selected dotenv file through the existing launcher boundary. Do not replace the reference with a literal credential.

### An npm server works in the host but not the sandbox

The server command runs inside the sandbox. Ensure its runtime is present there, use container-visible filesystem paths, and use `host.docker.internal` rather than `localhost` for a service running on the Docker host.

### A result points to a spill file

The result exceeded the inline safety limit. Use an enabled Travis `read` or `grep` tool on the reported mode-`0600` path during the same session. The adapter removes its spill files at session shutdown.

### A call times out

Set `requestTimeoutMs` on that server if its MCP operations legitimately need longer. This setting does not extend the provider model call or any Travis process/subagent timeout. Avoid automatic retries for tools with side effects.

## Supported surface

| Capability | Status |
|---|---|
| stdio client transport | Supported |
| Streamable HTTP client transport | Supported |
| Lazy per-server connection | Supported |
| Tool listing, search, schema description, and calls | Supported |
| Text, media, embedded-resource, and structured results | Supported with Travis conversion and bounds |
| Legacy SSE | Not supported |
| MCP OAuth | Not supported |
| MCP prompts and resource discovery | Not supported |
| Sampling and elicitation | Not supported |
| MCP Apps/UI | Not supported |
| Eager or keep-alive lifecycle | Not supported |

## Further reading

- [Travis234 main guide](https://github.com/htooayelwinict/travis234#optional-mcp-adapter)
- [Model Context Protocol](https://modelcontextprotocol.io/)
- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Official filesystem server](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem)
- [Context7 MCP server](https://github.com/upstash/context7)
