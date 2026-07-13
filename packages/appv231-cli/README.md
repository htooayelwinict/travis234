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

Coding-agent `bash` calls use a default 10-second yield. The yield does not kill
the command and does not change the command timeout. A command still running at
the end of that window returns an opaque `proc_...` handle. An omitted execution
timeout lets the job run until natural exit, explicit process control, an output
limit, or appv231 shutdown.

Use `process.poll` for quick or interactive incremental observation. Use
`process.wait` to wait from 1 to 900 seconds for terminal state without returning
on every output chunk. The wait duration does not change the command timeout. If
the wait deadline expires first, it returns `running`; the command is not killed,
and another wait can continue from the returned cursor.

Live sanitized output is bounded to 64 MiB per process and 512 MiB app-wide by
default. Crossing a spool limit stops only that producer and reports `failed`
with `output_limit`; elapsed time alone never produces that failure. Terminal
metadata and output are recoverable for seven days, subject to a bounded 256 MiB
completion store. appv231 cannot reattach a running process after an application restart
or container restart.

The agent can poll, wait, write input, resize an opt-in PTY, interrupt, terminate,
kill, and list workspace-owned processes. `/processes` refreshes and controls both
agent and user jobs. Ctrl-C targets the focused user command first, then one active
agent turn, and only uses the idle-TUI exit behavior when neither is active.

User `!command` and `!!command` run asynchronously; `!!` output remains excluded
from model context. `/allow package-install` can grant bounded package capability
while an agent turn is active. Extension `user_bash` handlers preserve their
payload and launch order, run on the command worker, and custom operations must
honor cancellation.
