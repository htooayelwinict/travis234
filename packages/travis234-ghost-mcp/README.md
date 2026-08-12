# Travis234 Ghost MCP

`travis234-ghost-mcp` bundles the Ghost macOS computer-use MCP server for
Travis234. It supports macOS 14 or newer on Apple Silicon and installs the
separately packaged `travis234-mcp-adapter` as a dependency.

## Install and launch

From Travis234's command line, install the add-on and start a new MCP-enabled
session:

```bash
travis234 install travis234-ghost-mcp
travis234 --mcp
```

The add-on registers its embedded server directly with Travis234. It does not
need a Ghost entry in any MCP configuration file, a separate Ghost install,
Homebrew, archive extraction, or configuration for another MCP client.

## Setup and permissions

In the Travis234 TUI, first inspect the installed package without changing it:

```text
/ghost-doctor
```

Then request missing permissions and install the four bundled recipes:

```text
/ghost-setup
```

Accessibility is required for computer actions. Screen Recording is required
for screenshots. Input Monitoring is required only for learning workflows.
After granting a missing permission, restart Travis234 before testing the
corresponding tools.

Visual grounding uses a large optional model that is not included in the
package. Download it only through this explicit setup action:

```text
/ghost-setup vision
```

## Packaging and state

The signed arm64 Ghost executable, instructions, recipes, and vision-sidecar
launcher are embedded in the wheel and executed in place. There is no runtime
archive extraction and no Ghost `mcp.json` entry. All mutable data is kept only
under `~/.travis234/ghost-mcp`.

Removing the add-on deletes package code while preserving user-created data:

```bash
travis234 remove travis234-ghost-mcp
```

Recipes and optional models intentionally remain under
`~/.travis234/ghost-mcp` after removal.

This package contains adapted source from
[Ghost OS](https://github.com/ghostwright/ghost-os). See `UPSTREAM.json`,
`THIRD_PARTY_NOTICES.md`, and `LICENSE` for provenance and licensing. Ghost OS
is MIT-licensed and pinned to upstream commit
`991aa4831295aaff6beef04cc809d0f0b53dc024` (upstream version `2.2.1+6`).
