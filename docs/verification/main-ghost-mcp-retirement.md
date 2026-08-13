# Ghost MCP Retirement Verification

- Date: 2026-08-14
- Release source SHA: `6e9d416ed3fc5f75c103f71c111f243c29d32f28`
- Travis234 version: `2.4.6`
- `travis234-mcp-adapter` version: `0.1.3`
- Scope: retire the bundled `travis234-ghost-mcp` add-on while preserving the general MCP adapter and all `~/.travis234` user data.

## Source and test-driven changes

The final release source is two fresh commits directly on `main`, authored as `htooayelwinict`. It contains no commit ancestry from the disposable `htooakalewis/mcp-addons` branch, and that branch had no remote ref at this gate.

The retirement work began with failing contract tests: the bundled package still existed, the adapter was still `0.1.2`, Travis234 was still `2.4.5`, and the release workflow lacked a separate promotion path. Those contracts passed after the focused changes.

During integrated-main qualification, the 1 MB foreground-output coalescing test repeatedly reached its one-second handoff boundary under full-suite load and returned the valid intermediate `DRAINING` state. The test-only handoff allowance was increased to ten seconds without changing its output, state, or callback assertions. It then passed five repeated focused runs and the complete 42-test process-service suite.

## Verification results

- Root Python suite: 1,951 passed in 486.43 seconds.
- npm launcher suite: 23 passed.
- npm package dry run: `@htooayelwinict/travis234@2.4.6`, five files.
- MCP adapter suite: 68 passed in 51.62 seconds.
- Focused process stabilization: five repeated passes; process-service suite: 42 passed.
- Python distributions: all four files passed `twine check`.
- Clean Python 3.13 wheel install: Travis234 `2.4.6`; `pip check` passed.
- Isolated Travis234 package install: `travis234-mcp-adapter` `0.1.3` listed successfully.
- Archive integrity and retired-payload audit: passed; no Ghost package, extension, or executable was present.
- No-cache release container build and `evals/container_smoke.py`: passed.
- Local release image ID: `sha256:f51fe6609a226407b81cc5fbd8a3492510793240b60dd92d8193ae1543e799e5`.
- Active-source audit: no Ghost implementation reference remained outside retirement records and explicit negative distribution assertions.

## Release artifacts

Artifacts were built from the release source SHA under `/tmp/travis234-retirement-2.4.6-6e9d416ed3fc5f75c103f71c111f243c29d32f28`.

| Artifact | SHA-256 |
| --- | --- |
| `travis234-2.4.6-py3-none-any.whl` | `1cfd99b8271aa11dd4e8d221c183d2aff3c109f8ae697bfa35fc7617ac5a19c9` |
| `travis234-2.4.6.tar.gz` | `b0e5d6af5ea6e1b1a3940974fc98e553426aded2f3763a8700c68da9b7935d5f` |
| `travis234_mcp_adapter-0.1.3-py3-none-any.whl` | `09ff8da7619c931cc95f37e83361ee18616a1c8efc4d42d733afdc5aebe6b467` |
| `travis234_mcp_adapter-0.1.3.tar.gz` | `69ab6d72b56f7f58681514f191d481b28797124540a9e4432aebb80d0bf0bc2f` |
| `htooayelwinict-travis234-2.4.6.tgz` | `f1b12bbd0ab002a4a484c9a86af614fead01f811a2964fa2bd22ea722bbe2a01` |

The inventory contains exactly two root Python distributions, two adapter distributions, and one scoped npm launcher tarball.

## Pending public operations

At this local gate, no commit had been pushed and no package or image had been published. PyPI publication, GHCR candidate publication and promotion, npm publication, yanking `travis234-ghost-mcp`, local Ghost add-on uninstall, and final deletion of the disposable local branch remained pending. The ignored main-worktree `.env` was not read, printed, or staged during local verification.
