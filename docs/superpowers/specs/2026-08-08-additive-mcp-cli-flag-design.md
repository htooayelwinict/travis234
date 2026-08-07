# Additive MCP CLI Flag Design

Date: 2026-08-08
Status: Approved for implementation

## Context

The optional MCP adapter registers a single extension tool named `mcp`. Travis234 intentionally does not activate arbitrary installed extension tools by default, so the first release documented MCP activation through the generic tool allowlist:

```bash
travis234 --tools mcp
travis234 --tools read,bash,process,edit,write,mcp
```

This is technically consistent but poor operator ergonomics. Supplying `--tools` replaces the normal active-tool set, which forces users who want normal Travis234 behavior plus MCP to restate core tool names and track future changes to the defaults.

## Goal

Add a dedicated bare `--mcp` CLI flag that activates the adapter's `mcp` proxy alongside the tools that would otherwise be active. Preserve the generic `--tools` interface and the adapter's optional-package boundary.

## Non-goals

- Do not bundle the MCP SDK or adapter into the core distribution.
- Do not activate other extension tools by default.
- Do not change tool ordering, the agent loop, iteration budgets, compaction, provider payloads, or MCP transport behavior.
- Do not add boolean values such as `--mcp=true` or `--mcp yes`; presence of the flag is the boolean.
- Do not remove or deprecate `--tools mcp`.

## Approaches considered

### 1. Additive core CLI flag — selected

Parse `--mcp` in the core CLI and add the registered `mcp` tool to the resolved startup tool set. This gives the desired stable command while retaining the adapter as an optional install. If the adapter is absent, normal unknown-tool validation produces a startup error.

This is the smallest user-facing change and keeps activation policy in the CLI layer that already owns `--tools`, `--no-tools`, and `--exclude-tools`.

### 2. Adapter-defined extension flag

Have the adapter dynamically register `--mcp` and activate itself during extension startup. This would keep the flag implementation in the adapter, but it couples extension flag processing to session tool activation and makes help/error behavior dependent on package discovery order. It expands the extension lifecycle surface for one boolean convenience flag.

### 3. Generic additive `--enable-tool mcp`

Introduce a new general-purpose additive tool option. This is flexible, but it gives operators a second generic tool-selection language and does not deliver the obvious MCP-specific interface requested here. No other use case currently justifies it.

## CLI contract

`--mcp` is repeat-safe and additive:

| Command | Active tools |
| --- | --- |
| `travis234 --mcp` | Normal default tools plus `mcp` |
| `travis234 --no-tools --mcp` | `mcp` only |
| `travis234 --tools read,bash --mcp` | `read`, `bash`, and `mcp` |
| `travis234 --tools mcp` | `mcp` only, unchanged compatibility behavior |

The resolved list preserves existing tool order and appends `mcp` once when it is not already present.

`--mcp` and `--exclude-tools mcp` are contradictory explicit instructions and must fail during CLI validation with a clear parser error. If the adapter is not installed or loaded, `--mcp` must fail before model execution with the existing unknown-tool class of error and an MCP-specific installation hint.

The flag applies only to the current Travis234 process. It does not modify settings, package state, sessions, or any `mcp.json` file.

## Internal design and change radius

The change is limited to startup tool resolution:

1. `travis/cli.py` parses `--mcp`, merges `mcp` into an explicit allowlist, and passes a one-item additive activation request when default tools remain implicit.
2. `travis/app.py` carries that additive startup tool name into session construction.
3. `travis/coding_agent/agent_session.py` resolves initial active tools by taking the existing base selection and appending allowed, registered additive names once.

The additive parameter is internal startup state, not a new persisted session field or public configuration format. Existing callers that omit it retain the current tool-selection behavior.

Documentation changes replace the awkward full core-tool enumeration with `travis234 --mcp`, document `--no-tools --mcp` for MCP-only operation, and retain `--tools ...,mcp` as compatibility/advanced-selection syntax.

## Error handling

- Missing adapter: exit through `argparse` before executing a prompt and suggest `travis234 install travis234-mcp-adapter`.
- Explicit exclusion conflict: exit through `argparse` and identify `--mcp` versus `--exclude-tools mcp`.
- Duplicate activation through `--mcp` and `--tools ...,mcp`: deduplicate without warning.
- Installed adapter with no configured servers: startup succeeds; the existing MCP proxy returns its current configuration guidance when called.

## Test design

Add failing regression tests before implementation for:

1. `--mcp` preserving implicit default tools and adding `mcp` once.
2. `--no-tools --mcp` producing MCP-only activation.
3. `--tools read,bash --mcp` producing the explicit subset plus MCP.
4. `--tools mcp --mcp` deduplicating MCP.
5. Missing adapter failure and installation guidance.
6. `--mcp --exclude-tools mcp` conflict failure.
7. CLI help exposing `--mcp` with additive wording.

Run focused CLI/session tests, the full Python suite, npm launcher tests, package builds and checks, the relevant container smoke test, and a five-prompt Minimax M3 TUI scenario using the existing root `.env`. The TUI scenario must demonstrate default tools and public MCP tools coexisting in one session.
