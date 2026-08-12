# Travis234 2.4.5 Bundled Ghost MCP Qualification

Date: 2026-08-12 (Asia/Yangon)

This record qualifies the optional `travis234-ghost-mcp` computer-use add-on
through exact locally built and installed distributions. The committed source
under test was `6afae08` (`docs: release bundled Ghost MCP add-on`). Publication,
tagging, pushing, and external registry changes were intentionally excluded.

## Platform and artifacts

- Host: macOS 26.5.2 (25F84), Apple Silicon (`arm64`).
- Python: 3.13.13.
- Swift: Apple Swift 6.3.3, target `arm64-apple-macosx26.0`.
- Node/npm: 26.4.0 / 11.17.0.
- Travis234: `2.4.5`.
- MCP adapter: `0.1.2`, with `mcp>=2,<3`.
- Ghost add-on: `0.1.0`, macOS 14+ Apple Silicon.
- Vendored Ghost source: upstream commit
  `991aa4831295aaff6beef04cc809d0f0b53dc024`, version `2.2.1+6`.

The exact local build artifacts were:

- `travis234-2.4.5-py3-none-any.whl` and `travis234-2.4.5.tar.gz`;
- `travis234_mcp_adapter-0.1.2-py3-none-any.whl` and
  `travis234_mcp_adapter-0.1.2.tar.gz`;
- `travis234_ghost_mcp-0.1.0-py3-none-macosx_14_0_arm64.whl` and
  `travis234_ghost_mcp-0.1.0.tar.gz`.

The root and adapter wheels were platform-neutral. The Ghost wheel was correctly
tagged `macosx_14_0_arm64`; its embedded `ghost` executable was executable,
strictly code-signature-valid, and identified as Mach-O arm64. Inspection also
confirmed the exact recipes, instruction asset, optional vision-sidecar assets,
upstream provenance, license, and third-party notices. No built executable or
archive was tracked.

## Automated verification

| Scope | Command | Result |
| --- | --- | --- |
| Root Python | `.venv/bin/python -m pytest -q` | 1,952 passed in 278.14s |
| MCP adapter Python | `cd packages/travis234-mcp-adapter && ../../.venv/bin/python -m pytest -q` | 69 passed in 50.81s |
| Ghost add-on Python | `.venv/bin/python -m pytest -q packages/travis234-ghost-mcp/tests` | 36 passed in 170.89s |
| Vendored Swift | `swift test` with explicit Command Line Tools `Testing.framework` and interop-library search/rpath flags | 15 tests in 4 suites passed; test execution 0.013s, timed incremental invocation 3.29s |
| npm launcher | `npm --prefix packages/travis234-cli test` | 23 passed in about 0.20s |
| npm package | `npm --prefix packages/travis234-cli run pack:dry-run` | passed; package `@htooayelwinict/travis234@2.4.5` contained the expected 5 files |

This Command Line Tools installation does not discover the installed Apple
`Testing.framework` automatically. A bare repeat demonstrated that host lookup
failure; the explicit framework and `lib_TestingInterop.dylib` search/rpath
invocation passed the complete unchanged Swift suite above.

The root, adapter, and add-on wheel/sdist builds all completed successfully.
Because the repository's ignored top-level `build/` directory shadows the Python
build frontend when invoked from the repository root, the same `.venv` Python was
invoked from the parent directory with each repository/package path supplied
explicitly. `twine check` accepted all six artifacts.

The real embedded-binary protocol suite and smoke verified initialization,
exactly 29 Ghost tools with complete schemas, an explicit tool call, cancellation,
and child cleanup. The bounded installed-wheel smoke returned:

```json
{"child_reaped": true, "configured": false, "legacy_state_created": false, "server": "ghost-os", "tool_count": 29}
```

## Clean package-manager installation

A Python 3.13 isolated environment installed the exact Travis234 wheel and passed
`pip check`. With network package resolution disabled and a local wheelhouse,
the installed `travis234` CLI then installed the exact Ghost wheel through the
normal Travis234 package manager. `travis234 list` reported the Ghost add-on;
its adapter dependency was co-located in the same isolated resource package.

The installed resource loader resolved exactly `ghost_mcp.py` and
`mcp_adapter.py`. Runtime registration exposed one `mcp` proxy tool, the
`/ghost-setup` and `/ghost-doctor` command pair, and an in-memory `ghost-os`
server with 29 tools. It did not read or create `mcp.json` and did not require a
separate Ghost installation, archive extraction, Claude configuration, or
Homebrew setup.

The isolated runtime state was exclusively under
`~/.travis234/ghost-mcp`. Setup installed the four packaged recipes without
installing the optional vision model. No `.ghost-os` path or migration alias was
created.

## Container qualification

`docker build --no-cache -f Dockerfile.release -t
travis234:bundled-ghost-mcp .` completed successfully. The resulting local image
was Linux arm64 with ID
`sha256:f9f16cad6a675303eaed3dc1da82971622da26d9d831eacfc4dce634a5460754`.
`evals/container_smoke.py --image travis234:bundled-ghost-mcp` exited zero under
the release image's unprivileged runtime contract. The add-on host-guard suite
separately passed 7 tests in 1.52s and rejected non-macOS execution without
creating state; the macOS wheel was not executed inside Linux.

## Installed TUI and computer-use acceptance

The provider-backed manual test used only the installed wheels, an isolated
`HOME` and Travis234 agent directory, and this launch shape:

```text
travis234 --cwd <isolated-workspace> --provider openrouter \
  --model xiaomi/mimo-v2.5-pro --no-tools --mcp
```

Authentication was copied mode 0600 into the disposable agent directory for the
session only. No credential value was printed or recorded.

The first `/ghost-doctor` reported Accessibility and input control available,
Screen Recording unavailable, zero recipes, and optional vision unavailable.
`/ghost-setup` installed four recipes without vision. The user approved adding
the installed embedded `ghost` executable to macOS Screen & System Audio
Recording. After restarting the installed Travis process, `/ghost-doctor`
reported Accessibility, input control, and Screen Recording available, four
recipes, and vision still intentionally absent.

The `mcp` status operation reported `ghost-os: disconnected` before first use,
proving connection-free discovery. A server listing then returned exactly 29
tools through the single `mcp` proxy.

The exact acceptance prompt submitted to Travis was:

```text
Using only the bundled ghost-os MCP computer-use server, open a browser, open YouTube, open Rick Astley's Never Gonna Give You Up official video, and play it. Do not use shell tools. Verify the final URL, page title, and playback/audio state before reporting success.
```

Travis used the `mcp` proxy and embedded `ghost-os` server to activate Safari,
navigate to YouTube, and start the requested video. A final read-only Ghost check
reported:

- URL: `https://www.youtube.com/watch?v=dQw4w9WgXcQ`;
- title: `Rick Astley - Never Gonna Give You Up (Official Video) (4K Remaster) - YouTube`;
- player: `playing-mode`, with no `paused-mode`;
- audio: player control named `Mute (m)`, proving it was unmuted, and Safari's
  tab audio indicator present, proving active sound output.

At the user's follow-up request, Travis used only the same bundled Ghost MCP to
send fifteen YouTube volume-down steps. It left playback active and audio
unmuted, and reported a quiet, audible level of approximately 25%. The read-only
final check did not change playback or volume.

## Shutdown and hygiene

The installed TUI exited through `/exit`, restored the terminal, and reaped its
Ghost child. The browser and playing video were deliberately left open. The
post-exit isolated-state audit found:

- 0 Ghost child processes;
- 0 `mcp.json` or Claude desktop configuration files;
- 0 legacy `.ghost-os` directories;
- 0 spill files;
- 0 Homebrew setup files;
- 4 expected recipe files under `~/.travis234/ghost-mcp/recipes`.

The repository hygiene checks found no tracked `.disposable` content and no
tracked built `travis234_ghost_mcp/bin/ghost`. The disposable Ghost reference
clone remained excluded from status and staging. The qualification tree,
including its temporary mode-0600 auth copy, was deleted after these results were
recorded and cannot be recovered. No user data under the real `~/.travis234`
tree was removed.
