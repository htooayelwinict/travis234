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

It mounts only the selected `--cwd` as `/workspace`, stores sandbox state in `~/.appv231/sandbox-home`, copies host `~/.agents/AGENTS.md` into the sandbox agent context, and copies host `~/.agents/skills` into the sandbox.

On startup, the package restores compact default agent files only when they are missing:

- `~/.agents/AGENTS.md`
- `~/.agents/skills/web-search/SKILL.md`
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
