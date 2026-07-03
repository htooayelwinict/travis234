# appv231

```text
        _   _   _   _   _   _   _   _   _
       / \ / \ / \ / \ / \ / \ / \ / \ / \
      ( a | p | p | v | 2 | 3 | . | t | u )
       \_/ \_/ \_/ \_/ \_/ \_/ \_/ \_/ \_/

      a sandbox-first coding-agent TUI
      with explicit subagents, compact memory, and portable npm launch
```

appv231 is the active next-generation agent workspace after sealed appv22. It is a terminal coding agent built for real user-side operation: run it from any project with `npx`, keep API keys out of project files, mount only the selected workspace into Docker, and invoke subagents only when the workflow actually needs delegation.

It directly ports and adapts implementation work from Pi and Hermes Agent through the `appV2.2` baseline. See `NOTICE.md` for upstream attribution and `LICENSE` for the preserved MIT license terms.

## What makes appv231 different

- Sandbox-first distribution: normal users run the public GHCR image through a tiny npm launcher.
- Explicit subagents: child agents are not a default habit; they are skill-triggered or command-triggered for larger workflows.
- Agentic TUI workflow: `/login`, `/model`, `/compact`, `/delegate`, `/agents`, and `/cancel-agent` are designed for long-running coding sessions.
- Compact memory: Hermes-style compaction keeps long sessions usable without forcing the user to restart every time context grows.
- Pi-style tool discipline: coding-agent tools, loop guards, read/write boundaries, and recovery prompts are designed to reduce runaway tool loops.
- Portable install: `npx @htooayelwinict/appv231@latest --cwd .` works from any project directory once Docker is available.
- Isolated credentials: `/login` stores API keys under the sandbox home, not inside the mounted project.
- Recoverable skills: the npm package bundles default `AGENTS.md`, `web-search`, and `subagent-delegation` assets for first-run or accidental `~/.agents` deletion.
- Appv231-only production image: Pi and Hermes remain reference sources in the repo, but the public image runs the appv231 runtime only.

## Recommended user entrypoint

Run from any project directory:

```bash
npx --yes @htooayelwinict/appv231@latest --cwd . --pull
```

Use `--pull` when you want the newest `ghcr.io/htooayelwinict/appv231:production` image immediately. Normal launches use an automatic pull cache to avoid pulling every time.

Install a persistent global command:

```bash
npm install -g @htooayelwinict/appv231@latest
appv231 --cwd .
```

The npm package is a launcher, not the full Python app. Runtime code comes from:

```text
ghcr.io/htooayelwinict/appv231:production
```

## Distribution model

appv231 ships as three layers:

```text
appV2.3.1/                                  Python source, tests, local dev entrypoints
Dockerfile.appv231.release                production image builder
packages/appv231-cli/                     npx/global Docker launcher
```

Runtime path:

```text
user shell -> npm launcher -> Docker sandbox -> appv231 Python TUI
```

This split is intentional. npm stays small and fast to publish. Runtime fixes normally ship by rebuilding and pushing the GHCR image. Publish npm only when the launcher, bundled skills, bundled `AGENTS.md`, CLI flags, or package metadata changes.

## First run

Start the sandbox:

```bash
npx --yes @htooayelwinict/appv231@latest --cwd . --pull
```

Inside the TUI:

```text
/login
/model
hi
```

Use `/login` to store an API key. Use `/model` to choose a provider/model. API keys entered through `/login` are stored at:

```text
$HOME/.appv231/sandbox-home/agent/auth.json
```

Inside the container this is visible as:

```text
/agent-home/agent/auth.json
```

Project `.env` files are intentionally not mounted or forwarded through the npm/Docker path. For user-side sandbox runs, use `/login` and `/model`.

## Common commands

```bash
appv231 --cwd /path/to/project
appv231 --cwd . --pull
appv231 --cwd . --no-pull
appv231 --cwd . --dry-run
appv231 --cwd . --no-network
appv231 --cwd . --image ghcr.io/htooayelwinict/appv231:production
appv231 --cwd . --agents-file ./AGENTS.md
appv231 --cwd . --with-skills ~/.agents/skills
appv231 --cwd . --no-user-skills
```

Use `--dry-run` to inspect the Docker command without starting the container.

## TUI command map

Inside appv231:

```text
/login                 configure provider credentials
/logout                remove stored provider credentials
/model                 choose a provider/model
/compact               compact the current conversation
/compact deep          stronger compaction pass
/delegate              spawn a delegated worker through the runtime command path
/agents                list delegated workers and status
/cancel-agent <id>     mark a delegated worker cancelled
/exit                  leave the TUI
```

Subagent skill workflows can also be triggered in natural language. For example:

```text
Use the subagent-delegation skill. Spawn a reviewer subagent to inspect docs/report/appv22_qa_scan_2026-06-26.md. Show me the child task id, child role, child status, and child summary.
```

## Subagents: what they are for

Subagents are for bounded delegation, not for every prompt.

Use them when:

- A task has independent review, research, or inspection work.
- You want a child summary without contaminating the parent with every file read.
- You need a reviewer, explorer, security pass, QA pass, or web-search worker.
- The user explicitly asks to spawn, delegate, hand off, verify through subagents, or use `/delegate`.

Do not use them when:

- The task is a simple edit or direct question.
- The parent can answer with already-loaded context.
- The child would need broad, unbounded repo scanning.
- The user did not ask for delegation and no large workflow requires it.

The intended behavior is simple:

```text
normal prompt -> main agent works normally
explicit subagent request -> subagent-delegation skill or /delegate owns the workflow
```

## Subagent modes

appv231 supports two practical delegation paths.

### Prompt-level skill delegation

Use this when you want the model to follow the bundled `subagent-delegation` skill.

Example:

```text
Use the subagent-delegation skill. Spawn a reviewer subagent only. The child should inspect README.md and return status. Show task id, role, status, and summary.
```

Expected signs:

```text
[skill] subagent-delegation
spawn_subagent(...)
subagent_start
subagent_stop
status: completed
summary: ...
```

The parent should not re-read all child files when the child summary is enough. If the child result is truncated, the parent should ask for a narrower follow-up child or report the truncation clearly.

### Runtime slash-command delegation

Use this when you want the runtime command path directly:

```text
/delegate reviewer inspect README.md and summarize risks
/agents
/cancel-agent subagent-123 user stopped the run
```

The runtime supervisor is conservative by default:

- child depth is capped at `1`
- internal workers are read-only by default
- concurrent workers are capped
- child results are summarized back to the parent
- parent-observed timeouts are recorded as terminal timeout results
- shutdown records active workers as cancelled

### Codex backend delegation

When Codex CLI is installed and authenticated, appv231 can delegate through Codex:

```text
/delegate --backend codex reviewer inspect README.md and summarize risks
```

Codex backend runs use a read-only sandbox by default and persist raw child logs under the session-local `subagents/<session-id>` directory.

## User-side subagent smoke test

Start appv231:

```bash
npx --yes @htooayelwinict/appv231@latest --cwd . --pull
```

Then ask:

```text
Use the subagent-delegation skill. Spawn a reviewer subagent only. The child should inspect README.md and return its status. Show me child task id, child role, child status, and child summary. If no subagent tool is available, say "subagent tool unavailable" and do nothing else.
```

Good output includes:

```text
child_task_id: subagent-...
child_role: reviewer
child_status: completed
child_summary: ...
```

If the app says `subagent tool unavailable`, the skill loaded but the runtime tool was not exposed in that session. That is a real feature availability failure, not a README problem.

## Web-search skill

The bundled `web-search` skill is a small, bounded current-info workflow. It is not a giant crawler.

Use it explicitly:

```text
Use the web-search skill. Search Google News for worldcup 2026 results and show at most five source rows.
```

Expected behavior:

- uses bounded `curl`-style retrieval
- avoids massive raw HTML/XML output
- returns at most five concise rows
- reports one concise blocker row if parsing or network access fails

## Sandboxing model

The npm launcher runs Docker with a narrow mount model.

Mounted:

```text
selected --cwd                 -> /workspace
$HOME/.appv231/sandbox-home     -> /agent-home
```

Not mounted:

```text
host home directory
host repo root unless selected as --cwd
project .env by default
provider API-key environment variables
Docker socket
```

Instruction imports are copied, not live-mounted:

```text
host ~/.agents/AGENTS.md       -> sandbox /agent-home/agent/AGENTS.md
host ~/.agents/skills          -> sandbox /agent-home/.agents/skills
```

You can skip host skills:

```bash
appv231 --cwd . --no-user-skills
```

You can add explicit files or skill directories:

```bash
appv231 --cwd . --agents-file ./AGENTS.md --with-skills ./skills
```

The npm package also includes compact default assets:

```text
packages/appv231-cli/agents/AGENTS.md
packages/appv231-cli/skills/web-search/SKILL.md
packages/appv231-cli/skills/subagent-delegation/SKILL.md
```

On startup, the launcher restores those defaults into host `~/.agents` only when the matching file or skill directory is missing. Existing user files are not overwritten.

## Direct Docker usage

The image entrypoint is `appv231`.

```bash
docker run --rm -it \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --pids-limit 512 \
  --user "$(id -u):$(id -g)" \
  -v "$PWD:/workspace:rw" \
  -v "$HOME/.appv231/sandbox-home:/agent-home:rw" \
  -e HOME=/agent-home \
  -e APPV231_CODING_AGENT_DIR=/agent-home/agent \
  -e APPV231_SANDBOX=1 \
  -e APPV231_NO_VENV_REEXEC=1 \
  ghcr.io/htooayelwinict/appv231:production \
  --cwd /workspace
```

## Local development from this repo

Run the TUI locally:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python appV2.3.1/scripts/appv231_tui.py --cwd .
```

Use a specific `.env` only for local development:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python appV2.3.1/scripts/appv231_tui.py --dotenv .env --cwd docs
```

Root npm wrappers:

```bash
npm run tui -- --cwd .
npm run tui:sandbox -- --cwd docs
```

Build a local development image:

```bash
npm run tui:sandbox -- --cwd docs
```

Install from wheel:

```bash
uv build appV2.3.1
uv tool install dist/appv231-*.whl
appv231 --cwd .
```

Optional browser automation dependencies are not installed in the production image. For browser automation development:

```bash
cd appV2.3.1
python -m pip install ".[browser]"
```

## Release image flow

The production image is built from `next/appv23.1` using `Dockerfile.appv231.release`.

Commit and push runtime changes first:

```bash
git push upstream next/appv23.1
```

Then build without stale branch cache:

```bash
docker build --no-cache --pull=false \
  -f Dockerfile.appv231.release \
  -t ghcr.io/htooayelwinict/appv231:production \
  .
```

Push GHCR:

```bash
docker push ghcr.io/htooayelwinict/appv231:production
```

Inspect the published image:

```bash
docker buildx imagetools inspect ghcr.io/htooayelwinict/appv231:production
```

Force users onto the newest image:

```bash
appv231 --cwd . --pull
```

## Npm package release rule

Do not publish npm for every runtime fix.

Publish npm only when one of these changes:

- `packages/appv231-cli/bin/appv231.js`
- bundled `agents/AGENTS.md`
- bundled `skills/**/SKILL.md`
- package metadata or version
- launcher README
- CLI flags or Docker run behavior

Runtime-only Python fixes ship through GHCR.

## Repository structure

```text
appV2.3.1/
  appv231/                  Python runtime: TUI, agent loop, tools, auth, models, compaction, subagents.
  scripts/                 Local entrypoints: appv231_tui.py, appv231_sandbox.py.
  tests/                   Unit and integration tests.
  Dockerfile.appv231        Local development sandbox image.
  README.md                This guide.

packages/appv231-cli/
  bin/appv231.js            Public npm launcher used by npx/global install.
  agents/AGENTS.md         Default agent kernel restored only when host ~/.agents/AGENTS.md is missing.
  skills/                  Bundled default skills restored only when missing and copied into sandbox.
  test/                    npm launcher tests.

Dockerfile.appv231.release  Production image builder for GHCR.
package.json               Repo-level helpers for image release and sandbox wrappers.
```

## QA gates

Run the full appv231 suite:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests
```

Current expected suite result:

```text
749 passed
```

Focused subagent/TUI trace check:

```bash
PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest \
  appV2.3.1/tests/test_app_integration.py -k 'subagent or tool_trace or guardrail' \
  appV2.3.1/tests/test_tui.py -k 'subagent_tool_trace or successful_subagent_tool_trace or guardrail' \
  appV2.3.1/tests/test_subagents.py
```

Launcher QA:

```bash
npm --prefix packages/appv231-cli test
npm --prefix packages/appv231-cli run pack:dry-run
```

## Production readiness checklist

Before calling a build production-ready:

1. Runtime tests pass for the touched appv231 scope.
2. Full appv231 Python suite passes.
3. Subagent skill smoke returns `taskId`, `role`, `status`, and `summary`.
4. Web-search skill smoke returns bounded rows or a concise blocker.
5. Npm launcher dry-run shows the expected Docker command.
6. GHCR image is rebuilt without stale branch cache when runtime code changed.
7. Published image behavior is verified with `docker run` or a user-side TUI smoke.
8. Only intended files are committed.
9. Runtime-only fixes are shipped through GHCR; npm is published only for launcher/package changes.

## Environment variables

Local development can use `.env`:

```bash
cp .env.example .env
```

Minimum live-run values for the local dev path:

```text
APPV231_WORKER_LLM_ENABLED=true
APPV231_WORKER_LLM_API_KEY=...
APPV231_WORKER_LLM_BASE_URL=https://openrouter.ai/api/v1
```

Sandbox users should prefer `/login` and `/model` instead of `.env`.

## Status

`appV2.3.1` is the active next-version workspace. Keep `appV2.2/` sealed except for bug fixes, security fixes, test hardening, and documentation corrections. Put advanced agent work in `appV2.3.1/`.

The `APPV2_*` environment prefix is still preserved for compatibility with the sealed baseline. Rename prefixes only through a planned and tested migration.
