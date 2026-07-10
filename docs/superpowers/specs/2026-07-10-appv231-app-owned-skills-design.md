# appv231 App-Owned Skills Design

## Decision

appv231 uses one canonical user resource root: `get_agent_dir()`. Skills live at
`<agent-dir>/skills`, which defaults to `~/.appv231/agent/skills` and resolves to
`/agent-home/agent/skills` in the npm-launched container.

Legacy `~/.agents` paths are not read, written, or migrated.

## Runtime

`DefaultPackageManager` discovers skills, prompts, and themes below the injected
`agent_dir`. Project and explicitly configured resources keep their existing
behavior. User-level skill metadata reports the app-owned agent directory as its
base.

## npm Launcher

The package tarball continues to include the default `AGENTS.md` and bundled
skills. On launcher startup it:

1. Seeds missing defaults into host `~/.appv231/agent` without overwriting files.
2. Combines bundled skills, app-owned host skills, and explicit `--with-skills`
   sources using the existing precedence order.
3. Materializes the sandbox profile under `<sandbox-home>/agent`.
4. Mounts `<sandbox-home>` at `/agent-home` and sets
   `APPV231_CODING_AGENT_DIR=/agent-home/agent`.

## Verification

- Focused Python tests prove app-owned skills load and legacy home skills do not.
- Node tests prove host and sandbox seeding uses only app-owned paths.
- A freshly packed npm artifact is run with a clean real app-owned skill directory
  against a no-cache local image.
- The five-prompt direct TUI subagent scenario is rerun from Prompt 1.
