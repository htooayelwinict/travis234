# Travis234

Travis234 is a terminal coding agent with persistent sessions, bounded tool execution, provider selection, compaction, and an optional Docker sandbox.

## Install and run

Python 3.13 is required.

```bash
python3.13 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/travis234 --cwd .
```

Browser-development support is optional and is not required by the core runtime or test suite:

```bash
.venv/bin/pip install -e '.[browser]'
```

The published npm launcher is `@htooayelwinict/travis234` and exposes only the `travis234` command:

```bash
npx @htooayelwinict/travis234 --cwd .
```

Use `travis234 --help` for command-line options and `/help` inside the TUI.

## Identity contract

- Product, repository, and distribution: `Travis234` / `travis234`
- Python import package: `travis`
- CLI command: `travis234`
- npm package: `@htooayelwinict/travis234`
- container image: `ghcr.io/htooayelwinict/travis234`
- container user: `travis`
- environment prefix: `TRAVIS234_*`

This is a hard cutover. Runtime aliases, old state paths, and migration fallbacks are not supported.

## State layout

Host state is kept outside project workspaces:

```text
~/.travis234/agent/AGENTS.md
~/.travis234/agent/skills/
~/.travis234/agent/sessions/
~/.travis234/sandbox-home/
```

The sandbox uses `/travis-home` as its home directory. Session files are stored at `/travis-home/agent/sessions/`.

Relevant overrides are:

```text
TRAVIS234_CODING_AGENT_DIR
TRAVIS234_CODING_AGENT_SESSION_DIR
TRAVIS234_SANDBOX_HOME
TRAVIS234_IMAGE
TRAVIS234_SANDBOX_IMAGE
TRAVIS234_SHARE_VIEWER_URL
```

Provider credentials should be configured through `/login` or the provider's standard environment variable. Credentials read from `--dotenv` are registered only for the provider that declares that variable; switching models cannot reuse another provider's key. Do not commit credentials or project-local auth state.

The worker binding can be made explicit with `TRAVIS234_WORKER_LLM_PROVIDER`, `TRAVIS234_WORKER_LLM_MODEL`, and `TRAVIS234_WORKER_LLM_BASE_URL`. For a custom or newly released model whose catalog metadata is unavailable, set `TRAVIS234_WORKER_LLM_CONTEXT_WINDOW` to its documented context size so footer telemetry and auto-compaction use the correct denominator.

Auxiliary compaction is optional. Set `TRAVIS234_COMPRESSION_LLM_ENABLED=true` and configure `TRAVIS234_COMPRESSION_LLM_PROVIDER`, `TRAVIS234_COMPRESSION_LLM_MODEL`, and, when needed, `TRAVIS234_COMPRESSION_LLM_BASE_URL`, `TRAVIS234_COMPRESSION_LLM_API_KEY`, or `TRAVIS234_COMPRESSION_LLM_TIMEOUT_SECONDS`. The summary request uses that route without changing the active coding model. If no auxiliary route is enabled, compaction uses the active model.

Model-driven tool subprocesses do not inherit provider credential variables by default. If a trusted project command deliberately needs one, list its exact variable name in `TRAVIS234_TOOL_ENV_PASSTHROUGH` (comma-separated). Human-authored `!command` remains an operator shell and inherits the operator environment.

## Extensions

Travis234 discovers global extensions from `~/.travis234/agent/extensions/` and project extensions from `.travis234/extensions/`. Install the optional first-party Hypa adapter with:

```bash
travis234 --install-extension hypa
```

The installer refuses to replace an existing extension directory. Use `/reload` in the TUI after adding or changing extension code; a new process is not required.

Extensions run with the same permissions as Travis234, so install only trusted code. Travis JavaScript extensions do not run directly in the Python extension runtime and require a Python adapter.

## Sandbox

The release image runs as the unprivileged `travis` user. The npm launcher mounts only the selected workspace and isolated Travis234 state, drops Linux capabilities, enables `no-new-privileges`, and does not forward a dotenv file.

```bash
travis234 --cwd /path/to/project
```

The default image is `ghcr.io/htooayelwinict/travis234`.

## Managed processes

Long-running shell work can return a process handle instead of blocking the agent. `process.wait` waits for terminal state and does not change the command timeout. If the wait deadline expires, the command is not killed and a later wait can continue from the returned cursor.

Live output is bounded to 64 MiB per process. Crossing that limit reports `output_limit`. Completed process metadata is retained for bounded recovery, but Travis234 cannot reattach a running process after an application restart.

User `!command` and `!!command` run asynchronously so the TUI remains responsive; double-bang output is excluded from model context.

## Manual production TUI acceptance

Use the real console entry point in an attached background PTY. Do not use the eval runner, a scripted prompt driver, or `python -m travis.cli` as acceptance evidence. The following launch shape exercises the same entry point an installed developer uses:

```bash
TRAVIS234_CODING_AGENT_DIR=/tmp/travis234-acceptance/agent \
uv run travis234 \
  --cwd /tmp/travis234-acceptance/workspace \
  --dotenv .env \
  --temperature 0.2 \
  --thinking high \
  --event-trace /tmp/travis234-acceptance/events.jsonl \
  --conversation-log /tmp/travis234-acceptance/conversation.jsonl
```

Inside that TUI, run `/model mimo` and select `openrouter/xiaomi/mimo-v2.5-pro`. Confirm the selected provider/model and `high` thinking level in the rendered UI or sanitized event trace before starting the workload. A live-provider run can consume paid API credits and therefore requires explicit authorization and an isolated credential source.

Enter the 21 coding scenarios from `evals/scenarios.json` manually, one prompt at a time. After each prompt:

1. Wait until the turn is finished and the TUI is idle; do not queue the next prompt early.
2. Read the final answer and inspect whether the edits, tool choices, persistence, and recovery behavior satisfy the prompt.
3. Record footer context tokens, context-window percentage, and compaction state, then run that scenario's verifier outside the TUI.
4. Classify a failure as model quality, provider translation, context management, tool/runtime behavior, or fixture/environment behavior using the event trace and conversation log.

During the same session, have the agent create a minimal project extension, run `/reload`, and invoke it. Exercise a skill, a reviewer subagent, managed process polling/stdin/interrupt/cleanup, repeated Ctrl-C escalation against a SIGINT-ignoring command, `/session`, one early `/compact`, and natural auto-compaction. After auto-compaction, send at least one dependent follow-up prompt and verify that constraints, completed work, and pending work survive without read-loop repetition.

If the runtime, provider, or context layer is the smoking gun, stop the paid run, preserve the trace, fix the root cause with a focused regression, and restart from prompt 1. A weak model answer is recorded as model quality and does not by itself justify a runtime change. Exit through `/exit` and confirm no managed or shell process remains.

## Development

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests -q
npm --prefix packages/travis234-cli test
python -m build
```

The core iteration-budgeting and ordered tool-result loop is behavior-sensitive. Changes in that area require focused regression and boundary tests.

## License

Travis234 is distributed under the MIT license. See [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md).
