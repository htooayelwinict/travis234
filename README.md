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

Provider credentials should be configured through `/login` or the provider's standard environment variable. Do not commit credentials or project-local auth state.

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

## Development

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests -q
npm --prefix packages/travis234-cli test
python -m build
```

The core iteration-budgeting and ordered tool-result loop is behavior-sensitive. Changes in that area require focused regression and boundary tests.

## License

Travis234 is distributed under the MIT license. See [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md).
