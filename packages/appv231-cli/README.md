# appv231

Thin npm launcher for the appv231 Docker sandbox.

## Usage

Run with `npx`:

```bash
npx @htooayelwinict/appv231 --cwd .
```

Or install globally:

```bash
npm install -g @htooayelwinict/appv231
appv231 --cwd .
```

The launcher pulls and runs:

```text
ghcr.io/htooayelwinict/appv231:production
```

It mounts only the selected `--cwd` as `/workspace`, stores sandbox state in `~/.appv231/sandbox-home`, copies host `~/.appv231/agent/AGENTS.md` into the sandbox agent context, and copies host `~/.appv231/agent/skills` into the sandbox.

On startup, the package restores compact default agent files only when they are missing:

- `~/.appv231/agent/AGENTS.md`
- `~/.appv231/agent/skills/web-search/SKILL.md`
- bundled package skills such as `subagent-delegation`

Existing user files are never overwritten.

## Options

```bash
appv231 --cwd /path/to/workspace
appv231 --cwd . --dry-run
appv231 --cwd . --no-pull
appv231 --cwd . --image ghcr.io/htooayelwinict/appv231:production
```

The host `.env` file is not mounted or passed automatically. Use `/login` inside the TUI for API keys.

## Sessions

The launcher mounts `~/.appv231/sandbox-home` at `/agent-home`, so JSONL
sessions persist when the disposable container exits.

```bash
appv231 --cwd . -- --continue
appv231 --cwd . -- --resume
appv231 --cwd . -- --session <path-or-session-id>
appv231 --cwd . -- --no-session
```

Default startup creates a new persistent session. Inside the TUI, use `/resume`
to switch, `/new` to start fresh, and `/session` to inspect the active file and
session ID.

## Managed commands

Coding-agent `bash` calls wait up to 10 seconds for a normal result. A command
still running after that window receives an opaque `proc_...` handle and
continues in the same app instance. The window is not a timeout. Use an explicit
command timeout or process controls to stop it; without either, it can run until
natural exit or app shutdown.

Use `/processes` to refresh, interrupt, terminate, or kill workspace-owned
processes. The agent can also poll, write input, resize an opt-in PTY, and list
processes through its `process` tool. Pipe mode remains the default.

Managed processes survive model turns and in-process `/new` or `/resume`
changes, but appv231 terminates them on exit. They cannot be resumed after an
application or container restart. User `!command` and `!!command` shortcuts are
still synchronous.
