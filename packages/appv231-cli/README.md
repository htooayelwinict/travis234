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
