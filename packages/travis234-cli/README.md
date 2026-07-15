# travis234

Thin npm launcher for the travis234 Docker sandbox.

## Usage

Run with `npx`:

```bash
npx @htooayelwinict/travis234 --cwd .
```

Or install globally:

```bash
npm install -g @htooayelwinict/travis234
travis234 --cwd .
```

The launcher pulls and runs:

```text
ghcr.io/htooayelwinict/travis234:production
```

It mounts only the selected `--cwd` as `/workspace`, stores sandbox state in `~/.travis234/sandbox-home`, copies a user-created host `~/.travis234/agent/AGENTS.md` into the sandbox agent context when present, and copies host `~/.travis234/agent/skills` into the sandbox.

On startup, the package restores bundled skills only when they are missing:

- `~/.travis234/agent/skills/web-search/SKILL.md`
- bundled package skills such as `subagent-delegation`

Existing user files are never overwritten.

## Options

```bash
travis234 --cwd /path/to/workspace
travis234 --cwd . --dry-run
travis234 --cwd . --no-pull
travis234 --cwd . --image ghcr.io/htooayelwinict/travis234:production
```

The host `.env` file is not mounted or passed automatically. Use `/login` inside the TUI for API keys.

## Extensions

Travis234 discovers global extensions from `~/.travis234/agent/extensions/` and project extensions from `.travis234/extensions/`. The Python CLI installs the optional first-party Hypa adapter with `travis234 --install-extension hypa`. Through this Docker launcher, pass the option to the in-container CLI:

```bash
travis234 --cwd . -- --install-extension hypa
```

The installer refuses to replace existing code. Use `/reload` in a running TUI after adding or changing an extension. Extensions execute with Travis234's permissions; install only trusted code. Unknown workspaces do not load project settings or executable resources until trust is resolved. Use `--approve` or `--no-approve` for a process-only decision, or `/trust` and then `/reload` for a saved decision. Travis JavaScript extensions do not run directly in the Python extension runtime and require a Python adapter.

## Sessions

The launcher mounts `~/.travis234/sandbox-home` at `/travis-home`, so JSONL
sessions persist when the disposable container exits.

```bash
travis234 --cwd . -- --continue
travis234 --cwd . -- --resume
travis234 --cwd . -- --session <path-or-session-id>
travis234 --cwd . -- --no-session
```

Default startup creates a new persistent session. Inside the TUI, use `/resume`
to switch, `/new` to start fresh, and `/session` to inspect the active file and
session ID.

## Managed commands

Coding-agent `bash` calls use a default 10-second yield. The yield does not kill
the command and does not change the command timeout. A command still running at
the end of that window returns an opaque `proc_...` handle. An omitted execution
timeout lets the job run until natural exit, explicit process control, an output
limit, or travis234 shutdown.

Use `process.poll` for quick or interactive incremental observation. Use
`process.wait` to wait from 1 to 900 seconds for terminal state without returning
on every output chunk. The wait duration does not change the command timeout. If
the wait deadline expires first, it returns `running`; the command is not killed,
and another wait can continue from the returned cursor.

Live sanitized output is bounded to 64 MiB per process and 512 MiB app-wide by
default. Crossing a spool limit stops only that producer and reports `failed`
with `output_limit`; elapsed time alone never produces that failure. Terminal
metadata and output are recoverable for seven days, subject to a bounded 256 MiB
completion store. travis234 cannot reattach a running process after an application restart
or container restart.

The agent can poll, wait, write input, resize an opt-in PTY, interrupt, terminate,
kill, and list workspace-owned processes. `/processes` refreshes and controls both
agent and user jobs. Ctrl-C targets the focused user command first, then one active
agent turn, and only uses the idle-TUI exit behavior when neither is active.

User `!command` and `!!command` run asynchronously; `!!` output remains excluded
from model context. Extension `user_bash` handlers preserve their
payload and launch order, run on the command worker, and custom operations must
honor cancellation.
