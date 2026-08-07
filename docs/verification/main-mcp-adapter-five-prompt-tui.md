# Travis234 MCP Adapter Installed-Wheel TUI Qualification

This record qualifies the optional MCP adapter through the installed Travis234
wheel and the adapter wheel. It does not use source-tree module execution.

## Launch contract

- Scenario artifact: `dist/travis234-2.4.1-py3-none-any.whl`; the functional
  TUI qualification preceded the documentation-only release bump to `2.4.2`.
- Adapter artifact: `dist/mcp-adapter/travis234_mcp_adapter-0.1.0-py3-none-any.whl`
- Model: `openrouter/minimax/minimax-m3`, thinking `medium`
- Interface: attached TUI with `--tools mcp --approve --no-session`
- Environment: the repository `.env` was supplied only through the explicit
  `--dotenv` boundary; no values were copied into evidence.
- State: an isolated `TRAVIS234_CODING_AGENT_DIR` contained the adapter installed
  through `travis234 install`.

## Local deterministic five-prompt scenario

All five prompts ran in one continuous TUI session against an isolated stdio
fixture.

| Prompt | Result | Evidence |
|---|---|---|
| 1. Lazy status | Pass | The fixture was `disconnected`, and its PID file did not exist before the first server-specific operation. |
| 2. Discovery | Pass | Server-only discovery returned five tools and started one child process. Minimax M3 first attempted an unsupported generic `list` operation, received a bounded error, then corrected to the documented server-only form in the same turn. |
| 3. Search and describe | Pass | Search selected `echo`; describe returned its exact input schema. |
| 4. Tool call | Pass | `echo` returned `MCP-TUI-ECHO-7F31` as text and structured content. |
| 5. Guards and cleanup | Pass | A 52,000-character result spilled to a mode-0600 file; a controlled server error remained bounded. Minimax M3 made one unnecessary invalid `echo` call without arguments after the controlled error; validation rejected it and the turn recovered. `/exit` stopped the child and removed the spill. |

The sanitized trace contains 28 events. The authorized conversation log contains
exactly five records with markers `MCP-1-PASS` through `MCP-5-PASS`.

## Public MCP five-prompt scenario

A second continuous installed-wheel TUI session exercised the two shared public
servers without printing configuration values or secrets.

| Prompt | Result | Evidence |
|---|---|---|
| 1. Shared status | Pass | `context7` and `filesystem` were configured and disconnected before first use. |
| 2. Context7 discovery | Pass | Discovery, search, and describe identified `resolve-library-id` and its required `libraryName` and `query` inputs. Four intentional malformed validation probes were returned as bounded MCP errors; the turn recovered and completed. |
| 3. Context7 live calls | Pass | `resolve-library-id` selected `/modelcontextprotocol/python-sdk`; `query-docs` returned the Python `stdio_client` signature and purpose. |
| 4. Filesystem discovery | Pass | Discovery found `list_allowed_directories`; the live call reported two allowed roots while the final response disclosed only their basenames. |
| 5. Filesystem live call | Pass | `read_text_file` returned `PUBLIC-MCP-SENTINEL-4D92` from the isolated workspace, and status showed both servers connected. |

Before `/exit`, the adapter owned live Context7 and filesystem npm children.
After `/exit`, the Travis234 process and both children were gone. The sanitized
trace contains 31 events, including 14 MCP tool completions and five successful
turn completions. The conversation log contains exactly five records with markers
`PUBLIC-MCP-1-PASS` through `PUBLIC-MCP-5-PASS`.

## Container smoke

The local production image `travis234:mcp-local-smoke` was rebuilt from the
current tree and run as its default unprivileged `travis` user. Inside a clean
container, `travis234 install` installed the adapter wheel, lazy status did not
start the fixture, an echo call returned `CONTAINER-MCP-SENTINEL-8A21`, and
session shutdown stopped the child. The smoke ended with
`CONTAINER-MCP-SMOKE-PASS`.

An initial harness attempt supplied a literal value for an environment key
containing `TOKEN`; the adapter correctly rejected it under the secret-reference
policy. The corrected harness used `${FIXTURE_TOKEN}` and passed.

After the release version and documentation were finalized, the exact
`travis234-2.4.2` wheel was installed in a fresh environment and used to install
the same adapter wheel through `travis234 install`; package identity was
`travis234-mcp-adapter` version `0.1.0`. A no-cache `Dockerfile.release` image
built from the exact release tree passed the repository container smoke and the
unprivileged MCP lifecycle smoke. No functional Travis or adapter runtime code
changed between the five-prompt sessions and this exact-artifact revalidation.

## Qualification record

- Date: 2026-08-08 (Asia/Yangon)
- Local fixture evidence:
  `/tmp/travis234-mcp-tui-qualified-events.jsonl` and
  `/tmp/travis234-mcp-tui-qualified-conversation.jsonl`
- Public-server evidence:
  `/tmp/travis234-mcp-public-tui-events.jsonl` and
  `/tmp/travis234-mcp-public-tui-conversation.jsonl`
- Terminal restoration: both attached sessions exited through `/exit` and
  emitted cursor-show and bracketed-paste-disable sequences.
- GitOps: intentionally not performed as part of this qualification.
