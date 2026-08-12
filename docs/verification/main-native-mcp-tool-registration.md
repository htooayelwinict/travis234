# Travis234 2.4.5 Native MCP Tool Registration Qualification

Date: 2026-08-12 (Asia/Yangon)

This record qualifies `travis234-mcp-adapter` 0.2.0 as an optional MCP client
add-on for Travis234. It replaces the former proxy operation API with ordinary
native definitions named `mcp__server__tool`. It does not turn Travis234 into
an MCP server.

## Scope under verification

- Base: `717a9d3` (`release harden agent runtime and Travis234 2.4.4`)
- Design: `c36a2db`
- Implementation plan: `20ba6fb`
- Implementation and release commits: `2a47860` through `8be312a`
- Installed-distribution evaluator correction: `a241d2e`
- Ghost native-call and child-cleanup evidence: `c6860c1`

The core changes are limited to generic extension tool-family activation and
tool registration batching. No agent-loop, provider, or compaction owner file
changed. MCP discovery, filtering, catalog bounds, calls, result conversion,
notifications, lifecycle, and status remain owned by the separately packaged
adapter.

## Behavioral evidence

- `--mcp` retains the otherwise selected Travis234 tools and activates the
  `mcp` family. `--no-tools --mcp` exposes only `mcp` plus its discovered native
  children.
- `mcp({})` is status-only. The former list/search/describe/call proxy module and
  tests were removed, with no compatibility alias.
- Exact `includeTools` and `excludeTools` filtering, deterministic bounded name
  conversion, schema validation, per-server and per-session catalog budgets,
  bounded startup concurrency, generation isolation, safe between-turn catalog
  reconciliation, request cancellation, secret references, spill cleanup, and
  stdio/HTTP transports have focused regression coverage.
- Text, structured content, resources, errors, and supported images are
  converted through bounded native results. Image count, per-image decoded
  size, aggregate decoded size, MIME type, and malformed-base64 limits have
  regression coverage.

Focused core tests reported `103 passed in 10.12s`. The adapter suite reported
`102 passed in 18.18s`, including real stdio and HTTP transport integration.
Focused native core/CLI coverage separately reported `76 passed`; focused
adapter runtime, extension, and Ghost coverage reported `28 passed`.

## Ghost OS protocol evidence

The disposable Ghost checkout remained at `.disposable/ghost-os`, ignored by
the shared Git exclude rule. The locally built binary at
`.disposable/ghost-os/.build/arm64-apple-macosx/debug/ghost` advertised exactly
29 tools through stdio MCP.

The Ghost regression proved:

- an MCP-only Travis234 session exposed `mcp` plus exactly 29 Ghost native tools
  and no ordinary Travis234 tools;
- `mcp__ghost-os__ghost_context` and
  `mcp__ghost-os__ghost_screenshot` had their exact expected names;
- `mcp__ghost-os__ghost_recipes` dispatched to the exact remote name
  `ghost_recipes` and returned ordinary bounded Travis234 result metadata; and
- session shutdown terminated the independently observed Ghost child process.

The final focused Ghost command reported `1 passed in 2.80s`. No live desktop
action, screenshot, Accessibility request, or Screen Recording request was
performed. Those permissioned macOS operations remain an external prerequisite;
the protocol-safe catalog, native call, and cleanup checks do not require them.

## Complete repository and launcher verification

- Full root Python suite: `1955 passed in 127.40s`.
- Full adapter Python suite: `102 passed in 18.18s`.
- npm launcher suite: `23 passed`.
- npm dry-run package: `@htooayelwinict/travis234@2.4.5`, five files,
  `htooayelwinict-travis234-2.4.5.tgz`, package size 9.3 kB, SHA-1
  `9131224b56d3952b6e273cbb8516e62121511d94`.

These gates are rerun after the final verification record is committed; the
fresh terminal results are the completion authority.

## Built distributions

Both projects built wheel and sdist artifacts, and Twine accepted all four:

| Artifact | SHA-256 |
| --- | --- |
| `travis234-2.4.5-py3-none-any.whl` | `05f84bb90cac657955bda2970463bd0710d039a811462ef4343030615ba8d439` |
| `travis234-2.4.5.tar.gz` | `178684f94609803213be5a99ba0dda7d76ab32093ebb82589c63de49f73d8da1` |
| `travis234_mcp_adapter-0.2.0-py3-none-any.whl` | `b238ff1572ab4614e0bfcab7358de1c4212ff9f9fdd14c00a216f928afd6c98b` |
| `travis234_mcp_adapter-0.2.0.tar.gz` | `e1033a454aa783866fa2b775393e8f83948b73f12496a2f41db419c1e93835bc` |

The adapter wheel contained `catalog`, `native_tool`, `status_tool`, runtime,
configuration, result, output-guard, and extension modules; it did not contain
the deleted proxy module.

## Clean installation and package-manager evidence

A clean Python 3.13.13 virtual environment installed the exact root and adapter
wheels and reported `travis234` 2.4.5 and `travis234-mcp-adapter` 0.2.0. Installed
CLI help exposed additive `--mcp` and all supported runtime modes.

The repository CLI has no `--version` option; invoking it produces the existing
argparse unsupported-option error. Release identity was therefore verified from
installed distribution metadata rather than adding unrelated CLI scope.

The real Travis234 package manager installed the adapter into an isolated
`HOME` under `~/.travis234/agent/packages` using the PEP 508 direct reference:

```text
travis234 install 'travis234-mcp-adapter @ file:///.../travis234_mcp_adapter-0.2.0-py3-none-any.whl'
```

A bare wheel pathname is interpreted by the existing manager as a package
directory, and `--offline` intentionally prohibits Python package acquisition,
including a local direct reference. The successful qualification therefore used
the manager's supported direct-reference form without `--offline`; no network
credential or user state was used.

The evaluator then ran against the package-manager-installed entry, without
writing or injecting an extension wrapper. Its bounded JSON evidence was:

```json
{"activeNames":["mcp","mcp__fixture__echo","mcp__fixture__configured_secret_name","mcp__fixture__slow","mcp__fixture__large_output","mcp__fixture__controlled_error","mcp__fixture__emit_tools_changed"],"providerTools":["mcp","mcp__fixture__echo","mcp__fixture__configured_secret_name","mcp__fixture__slow","mcp__fixture__large_output","mcp__fixture__controlled_error","mcp__fixture__emit_tools_changed"],"text":"installed-native-mcp"}
```

No built-in `read`, `bash`, `edit`, or `write` tool appeared, and the generated
secret was absent from both the evidence and serialized session.

## Release container

`docker build --no-cache -f Dockerfile.release -t
travis234:native-mcp-tools .` produced image
`sha256:a58a93ed9041a385990e10ba27ef289a5ddb0a654a9053c7a953bc86fc23da3e`.
`python3 evals/container_smoke.py --image travis234:native-mcp-tools` exited zero.
The smoke covers the installed non-root CLI, print/JSON/RPC/TUI faux paths,
compaction, managed-process cleanup, npm launcher behavior, and credential
absence.

## Hygiene and exclusions

- `git check-ignore -v .disposable/ghost-os` resolves to the shared
  `/.disposable/` exclude rule.
- `git ls-files .disposable` prints nothing.
- No `.env`, credential, spill, package cache, or disposable checkout is tracked
  or staged.
- The temporary build and isolated install tree is removed only after final
  verification. The ignored Ghost checkout is retained.
- No publication, push, pull request, or other GitOps action was performed.
