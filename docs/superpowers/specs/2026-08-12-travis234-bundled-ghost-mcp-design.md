# Bundled Ghost Computer-Use MCP Add-on Design

**Date:** 2026-08-12
**Status:** Approved in conversation; awaiting written-spec review
**Products:** Travis234, `travis234-mcp-adapter`, `travis234-ghost-mcp`

## Objective

Ship Ghost OS computer use as an optional, plug-and-play MCP add-on for Travis234. A user installs one Travis package and starts Travis with MCP enabled:

```bash
travis234 install travis234-ghost-mcp
travis234 --mcp
```

The installed add-on supplies and starts its own Ghost MCP server. Users do not clone Ghost OS, install it with Homebrew, extract a release archive, or create a Ghost entry in `mcp.json`.

This work does not turn Travis234 into an MCP server and does not replace Travis234's existing general MCP client adapter.

## Success Criteria

The first release is successful when all of the following are true:

1. `travis234-ghost-mcp` installs through the existing Travis package command on macOS 14 or newer running Apple Silicon.
2. The installed package contains an executable Travis-specific Ghost build and runs it directly from the installed package payload.
3. Starting `travis234 --mcp` admits the bundled `ghost-os` server without a Ghost entry in any MCP configuration file.
4. The server exposes the complete pinned 29-tool Ghost MCP catalog and performs real macOS computer-use actions.
5. All mutable add-on state is under `~/.travis234/ghost-mcp`; the add-on never creates or uses `~/.ghost-os`.
6. Setup does not edit Claude, Cursor, VS Code, Homebrew, project MCP files, or user MCP files.
7. Missing permissions, unsupported platforms, timeouts, cancellation, and shutdown are bounded and actionable.
8. A clean, installed-wheel Travis TUI session can open a browser, navigate to the Rick Astley YouTube video, start playback, and verify the resulting browser state.

## Scope

### Included

- A new optional Python distribution named `travis234-ghost-mcp`, initially version 0.1.0.
- A pinned, MIT-licensed Ghost OS source snapshot adapted for Travis234.
- A prebuilt Ghost executable in the macOS 14+ Apple Silicon wheel.
- Ghost's MCP instructions and bundled recipes.
- In-memory registration of the packaged `ghost-os` server with the existing MCP adapter.
- Travis TUI commands for setup and diagnostics.
- Accessibility-tree computer use and Ghost's existing screenshot support.
- An explicit optional setup path for Ghost's local vision sidecar and model.

### Excluded

- Intel macOS, Linux, and Windows execution in the first release.
- Turning Travis234 into an MCP server.
- Replacing or redesigning the generic MCP adapter.
- Writing a Ghost definition to `mcp.json`.
- Installing Ghost through Homebrew or downloading/extracting a Ghost release at runtime.
- Bundling the multi-gigabyte optional vision model in the Python wheel.
- A persistent Ghost daemon or process reattachment after Travis exits.
- Changing the core agent-loop order, iteration budget, or parallel coordinator.

## Chosen Approach

Create a separate platform-specific add-on package and add a narrow packaged-server registration interface to `travis234-mcp-adapter`.

The alternatives are rejected for these reasons:

- Putting Ghost inside `travis234-mcp-adapter` would impose a large macOS-only payload on unrelated MCP users and mix server distribution with generic client behavior.
- Requiring a separately installed Homebrew Ghost would not meet the self-contained installation requirement.
- Reimplementing Ghost in Python would duplicate a working MIT-licensed implementation, increase behavioral drift, and substantially enlarge the scope.

## Architecture

The runtime path is:

```text
travis234 --mcp
    -> travis234-ghost-mcp extension
    -> packaged-server registration in travis234-mcp-adapter
    -> embedded Ghost executable, invoked with `mcp`
    -> stdio MCP
    -> macOS Accessibility, ScreenCaptureKit, and input APIs
```

### Generic MCP adapter

The adapter remains the general MCP client. The branch's unneeded native-tool-registration redesign is reverted to the behavior on the handed-off base commit. A focused additive interface then permits an installed, trusted Travis extension to register an immutable packaged MCP server descriptor in memory.

The descriptor contains a validated server name, an absolute executable path within the installed package payload, literal arguments, and non-secret environment additions owned by the package. It cannot name a shell command, rely on `PATH`, or inject credential values. The adapter applies its existing transport lifecycle, request timeout, cancellation, error shaping, output bounds, and cleanup rules.

Normal configuration loading remains available for unrelated MCP servers. The bundled Ghost registration does not consult MCP configuration to find or configure Ghost. If a user has an obsolete configured server named `ghost-os`, the packaged registration wins and adapter status reports that the external duplicate was ignored. This prevents an old configuration from silently substituting another executable for the bundled server.

Registration is idempotent so installing the adapter separately and also receiving it as a Python dependency cannot register duplicate flags, tools, or lifecycle handlers.

### Ghost add-on package

`travis234-ghost-mcp` contains:

- its Python extension and package metadata;
- the adapted Ghost Swift source and pinned dependency resolution used to reproduce builds;
- the compiled `ghost` executable in platform wheels;
- `GHOST-MCP.md`, bundled recipes, and vision-sidecar launcher sources;
- the upstream Ghost MIT license and dependency notices; and
- a machine-readable record of the pinned upstream commit and Travis-specific patches.

The extension resolves the executable relative to its own installed location and registers it directly. It never extracts an archive or copies the executable to a global bin directory. A released wheel is platform-tagged for macOS 14+ arm64 and is non-pure. Building a wheel from source requires the pinned supported Swift toolchain, while installing the released wheel does not.

On unsupported platforms, installation or activation fails with a concise platform-specific diagnostic. It does not partially configure Ghost or affect other Travis tools and MCP servers.

### Travis-specific Ghost changes

The vendored implementation preserves Ghost's MCP schemas and computer-use behavior while changing host integration:

- Mutable recipes, models, vision environments, logs, and other owned files resolve beneath `~/.travis234/ghost-mcp`.
- The code contains no fallback, migration alias, or compatibility read for `~/.ghost-os`.
- The setup wizard does not detect or modify Claude configuration and does not add permissions for other MCP clients.
- Resource discovery resolves packaged instructions, recipes, and vision-sidecar files relative to the add-on payload rather than Homebrew share directories.
- Diagnostic text uses Travis234 product and command names where host-specific guidance is required.

Upstream functionality unrelated to Travis integration is changed only when required by state ownership, resource resolution, deterministic packaging, safety, or a focused regression test.

## Installation and Setup Experience

Installing the package is non-interactive and transactional through the existing package manager. It does not run an unbounded post-install script or request macOS permissions during package acquisition.

After installation, `travis234 --mcp` loads the add-on. Two TUI commands provide host setup:

- `/ghost-setup` checks required and optional permissions, opens the relevant macOS Privacy & Security panes when needed, installs bundled recipes idempotently under the Travis234 state root, and reports whether the terminal or Travis process must restart.
- `/ghost-doctor` checks the embedded executable and signature, MCP protocol startup, permissions, recipe state, and optional vision availability without changing MCP configuration.

The setup path is repeatable and safe after partial completion. Core accessibility-tree operations do not require the optional vision model. Vision setup is an explicit action because it creates an environment and downloads a large third-party model; the wheel does not perform that download automatically.

## Runtime and Data Flow

1. Travis loads installed trusted extensions and activates MCP through its existing `--mcp` rules.
2. The Ghost extension validates the host platform and its packaged executable.
3. It registers the reserved `ghost-os` packaged descriptor in memory.
4. The MCP adapter starts the embedded executable lazily when discovery or a call requires it.
5. The adapter performs MCP initialization, bounded tool discovery, and explicit calls through stdio.
6. Ghost performs the requested macOS action and returns MCP content.
7. The adapter bounds and shapes the result using its existing safeguards.
8. Cancellation or session shutdown closes the MCP session and terminates the child process within the existing deadline.

No step writes an MCP server configuration. No daemon survives the owning Travis session.

## State and Security

The canonical mutable state root is:

```text
~/.travis234/ghost-mcp/
```

Expected children include recipes, optional vision assets, and bounded diagnostic artifacts. Tests isolate `HOME` while preserving the same relative state contract. Production code does not accept an alternate legacy state path.

The packaged executable path is resolved and verified inside the installed package root. The server descriptor does not accept user-provided command substitution. Credentials are neither required for core Ghost operation nor stored in tracked files, configuration, logs, status, or tool output.

Computer-use tools are inherently powerful. They remain disabled unless MCP is activated through Travis's existing tool controls. Project trust behavior is unchanged. The add-on does not add another unbounded parallel executor; MCP calls continue through Travis's bounded coordinator and the adapter's lifecycle.

## Error Handling

- **Unsupported host:** report the required macOS and architecture before attempting to spawn Ghost.
- **Missing or invalid executable:** fail activation with the expected package version and repair command; do not fall back to `PATH` or Homebrew.
- **Missing Accessibility permission:** return a bounded error directing the user to `/ghost-setup`.
- **Missing Screen Recording permission:** accessibility-only tools remain usable; screenshot-dependent calls return an actionable bounded error.
- **Missing Input Monitoring permission:** learning functionality reports the optional permission requirement without disabling unrelated tools.
- **Vision unavailable:** visual-grounding calls explain the explicit vision setup action; accessibility-based calls remain usable.
- **Protocol failure or timeout:** use the adapter's safe error shaping and configured request deadline without leaking command output or environment values.
- **Cancellation or exit:** cancel the active request, close stdio, terminate the child, and escalate to a forced kill only within the existing bounded shutdown policy.
- **Obsolete external `ghost-os` config:** ignore it in favor of the packaged server and surface a status warning without rewriting the file.

## Testing Strategy

Implementation follows test-driven development. Each behavioral change begins with a failing focused test.

### Python and adapter tests

- Packaged-server registration works without any MCP configuration file.
- Registration is immutable, path-bounded, credential-safe, and idempotent.
- An obsolete external `ghost-os` definition cannot replace the embedded executable.
- Other configured MCP servers retain their existing behavior.
- Unsupported-platform and missing-binary failures are bounded and non-destructive.
- Package manifests, platform tags, included assets, license notices, and distribution contents are correct.
- Setup and doctor commands use only the Travis234 state root and do not modify external configuration.

### Swift and protocol tests

- Run the vendored Ghost Swift test suite against the adapted implementation.
- Add regressions proving every mutable path is beneath `~/.travis234/ghost-mcp` and no `.ghost-os` compatibility path exists.
- Verify setup omits all Claude and Homebrew configuration writes.
- Build the release executable and exercise MCP initialize, discovery, representative perception/action calls, controlled errors, cancellation, and shutdown.
- Assert the pinned complete 29-tool catalog and schema identities.

### Installed-package integration

- Build the sdist and macOS arm64 wheel from the repository source.
- Install the wheel through the real `travis234 install` command into an isolated home.
- Start Travis with only the installed adapter/add-on resources and no Ghost MCP configuration.
- Verify protocol calls, child cleanup, bounded output, and absence of spill files.
- Assert no files are created under `.ghost-os`, Claude, project MCP, user MCP, or Homebrew paths.

### Manual TUI acceptance

Using the installed Travis234, adapter, and Ghost add-on distributions in a real PTY:

1. Run `/ghost-doctor` and `/ghost-setup` as needed.
2. Start one continuous `travis234 --mcp` TUI session.
3. Ask Travis to use only the bundled Ghost MCP server to open a browser, open YouTube, navigate to the Rick Astley video, and play it.
4. Confirm through Ghost context that the exact YouTube watch URL and title are active and that playback/audio state is present.
5. Exit Travis and confirm the Ghost child is gone and no temporary spill artifacts remain.

### Repository release gates

Before reporting implementation complete, run and record:

- the complete root Python test suite;
- the complete adapter and Ghost add-on test suites;
- the vendored Swift tests;
- npm launcher tests;
- all Python package builds and distribution validation;
- clean installs through the package manager; and
- relevant container smoke checks proving generic Travis/MCP behavior is unchanged and the macOS-only add-on fails predictably on unsupported hosts.

## Versioning and Compatibility

The initial add-on release is `travis234-ghost-mcp` 0.1.0. The restored generic adapter receives only the smallest compatible version increment required for packaged registration. Travis234 receives the next patch version because its package, documentation, and verification surface changes.

The add-on pins the Ghost upstream revision used for the source snapshot. Updating Ghost is a deliberate future task requiring license review, patch reconciliation, protocol/catalog tests, package rebuilds, and the full acceptance flow.

## Documentation

User documentation will state:

- exact platform support and install commands;
- that Ghost is embedded and no separate Ghost installation or MCP configuration is required;
- macOS permission requirements and restart behavior;
- the difference between core accessibility operation and optional visual grounding;
- the only mutable state root;
- removal/update behavior and retained user data; and
- upstream Ghost attribution and the source revision used.

Verification records will distinguish automated protocol checks from permission-dependent manual UI acceptance.
