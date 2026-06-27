# appv23

Pi-style coding agent with Hermes-style compaction, branched from the sealed `appV2.2` baseline.

appv23 directly ports and adapts implementation work from Pi and Hermes Agent through the `appV2.2` baseline. See `NOTICE.md` for upstream attribution and `LICENSE` for the MIT license terms preserved from those projects.

## Status

`appV2.3` is the active next-version workspace. Keep `appV2.2/` sealed except for bug fixes, security fixes, test hardening, and documentation corrections. Put new advanced agent work in `appV2.3/`.

This initial scaffold intentionally preserves the `APPV2_*` environment variable prefix for compatibility with the sealed baseline. Rename environment prefixes later only if the migration is planned and tested.

## Requirements

- Python 3.13
- `uv` for local development from the repository root
- Optional provider credentials in `.env` for live LLM runs

## Run from the repository

```bash
uv run python appV2.3/scripts/appv23_tui.py --cwd .
```

Or use the root npm wrapper:

```bash
npm run tui -- --cwd .
```

Version-specific wrappers are also available:

```bash
npm run tui:v22 -- --dotenv ../.env --cwd ..
npm run tui:v23 -- --cwd .
```

When `--dotenv` is omitted, `appv23` searches the working directory (`--cwd`) and parent directories for `.env`, so the root `.env` works even when the npm wrapper runs from `appV2.3/`. Pass `--dotenv path/to/.env` to force a specific file.
The sealed `appV2.2` wrapper does not use the appv23 resolver, so its example keeps explicit parent paths.

## Install locally from a wheel

```bash
uv build appV2.3
uv tool install dist/appv23-*.whl
```

Then run:

```bash
appv23 --cwd .
```

## Test

From the package directory:

```bash
cd appV2.3
uv run --with pytest python -m pytest tests -q
```

Expected current `appV2.3` suite: `680 passed`.

## Production verification gates

Before calling appv23 production-ready, verify these current-state gates:

1. Focused subagent skill smoke: the prompt explicitly invokes `subagent-delegation`, reads the skill, spawns one child, and reports `taskId`, `role`, `status`, and `summary`.
2. Focused web-search skill smoke: the prompt explicitly invokes `web-search`, reads the skill, uses Google News RSS first, avoids the `curl | python3 <<'PY'` stdin trap, and returns at most five bounded `RESULT` rows or one concise blocker row.
3. Runtime regression: run the focused tests for files touched in the current change set.
4. Full suite: run `cd appV2.3 && uv run --with pytest python -m pytest tests -q`.
5. Build: run `cd appV2.3 && uv build`.
6. Gitops: inspect the final diff, commit only intended files, and push the active branch when Lewis asks.

## User-side subagent smoke

Start the app:

```bash
npm run tui:v23
```

`/subagents` is a prompt-level trigger, not a required runtime slash command. The supported production contract is: explicitly ask for the `subagent-delegation` skill, then ask for the bounded child task.

Then ask with explicit subagent wording so the `subagent-delegation` skill owns the workflow:

```text
Use the subagent-delegation skill. Spawn a reviewer subagent to inspect docs/report/appv22_qa_scan_2026-06-26.md. Show me the child task id, child role, child status, and child summary.
```

Working output should first show the skill read, then a `spawn_subagent` tool call plus child lifecycle evidence:

```text
[skill] subagent-delegation
subagent_start
child_subagent_id: subagent-...
child_role: reviewer
subagent_stop
status: completed
summary: ...
```

The `spawn_subagent` tool result should also include `taskId`, `role`, `status`, and `summary`.

## User-side web-search skill smoke

The `web-search` skill is a bounded Google News/current-facts lookup, not a general crawler. Start the app and ask:

```text
Use the web-search skill. Search Google News for worldcup 2026 results and show at most five source rows.
```

Working output should include:

```text
[skill] web_search
RESULT  1  ...
```

It should not print raw HTML/XML, and if Google blocks or parsing fails it should report one concise `ERROR`, `NO_RESULTS`, or `NO_PARSE_RESULTS` row.

## Environment

Copy the root template and set the worker provider values needed for live model calls:

```bash
cp .env.example .env
```

Minimum live-run settings:

```text
APPV2_WORKER_LLM_ENABLED=true
APPV2_WORKER_LLM_API_KEY=...
APPV2_WORKER_LLM_BASE_URL=https://openrouter.ai/api/v1
```

## Subagent workforce

AppV2.3 includes a backend-agnostic subagent supervisor for delegating focused work from an active `AgentSession`.

- Subagent tools are skill-governed. The main agent should act normally unless Lewis explicitly asks for subagents or invokes the `subagent-delegation` skill.
- `/subagents` is treated as a prompt-level skill trigger. Use `/delegate` for the existing runtime slash command path.
- `/agents` lists delegated workers and their current status.
- `/delegate <role> <task>` runs an internal read-only AppV2.3 worker and returns its summary to the parent session.
- `/delegate --backend codex <role> <task>` runs `codex exec --json` in a read-only sandbox when the Codex CLI is installed and authenticated. Model and non-`off` reasoning settings are forwarded to Codex when supplied.
- `/cancel-agent <task-id> [reason]` records a terminal cancellation result for a delegated worker and prevents late child output from overwriting the parent-observed state.

The default safety model is intentionally conservative: subagents run at depth `1`, use read-only tools by default, cap concurrent workers at `3`, return structured summaries instead of silently mutating parent state, and record parent-observed timeouts as terminal `timeout` results. Session shutdown records active workers as cancelled, rejects new subagent spawns, and tears down the supervisor executor. Default Codex subagent runs persist raw child logs under the session-local `subagents/<session-id>` directory.
