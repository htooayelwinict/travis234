# Travis234 Ghost MCP

`travis234-ghost-mcp` bundles the Ghost macOS computer-use MCP server for
Travis234. It supports macOS 14 or newer on Apple Silicon and depends on the
separately packaged `travis234-mcp-adapter`.

The add-on registers its embedded server directly with Travis234. It does not
need a Ghost entry in any MCP configuration file, a separate Ghost install,
Homebrew, archive extraction, or configuration for another MCP client.

Run `/ghost-setup` in the Travis234 TUI to request the required macOS
permissions and install the bundled recipes. Optional vision support is
installed only when explicitly requested. All mutable data stays under
`~/.travis234/ghost-mcp`.

This package contains adapted source from
[Ghost OS](https://github.com/ghostwright/ghost-os). See `UPSTREAM.json`,
`THIRD_PARTY_NOTICES.md`, and `LICENSE` for provenance and licensing.
