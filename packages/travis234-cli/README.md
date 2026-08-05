# @htooayelwinict/travis234-offsec

Thin npm launcher for the Travis234 OffSec Kali image.

## Start

Run directly with `npx`:

```bash
npx @htooayelwinict/travis234-offsec --cwd ~/agent-work -- --target 10.129.1.23
```

Or install the launcher globally:

```bash
npm install -g @htooayelwinict/travis234-offsec
travis234 --cwd ~/agent-work -- --target 10.129.1.23
```

The launcher pulls the image tag matching its installed npm version (currently
`ghcr.io/htooayelwinict/travis234-offsec:2.4.0-offsec.4`), mounts
only the chosen `--cwd` read-write at `/workspace`, and mounts
`~/.travis234/sandbox-home` at `/travis-home`. Sessions therefore persist at
`/travis-home/agent/sessions/` when a disposable container exits. The host `.env`
and provider credentials are not mounted or forwarded; use `/login` inside the
TUI. The image runs as the unprivileged `travis` user.

Host-native Python is preferred when a lab VPN route or local evidence must be
visible directly. The Kali container is optional and must already have network
reachability supplied by the operator's environment.

For an OpenAI-compatible proxy such as 9router, explicitly pass a dotenv file:

```bash
npx @htooayelwinict/travis234-offsec \
  --cwd ~/agent-work \
  --dotenv ~/.config/travis/9router.env
```

The launcher passes only that selected file through Docker `--env-file`; it does
not mount the dotenv file or persist its contents. Without `--dotenv`, npx does
not forward host provider credentials.

## Launcher options

Launcher flags precede `--`; all following arguments go to Travis234:

```bash
travis234 --cwd /path/to/workspace
travis234 --cwd . --dry-run
travis234 --cwd . --dotenv ~/.config/travis/9router.env
travis234 --cwd . --no-pull
travis234 --cwd . --pull
travis234 --cwd . --image ghcr.io/htooayelwinict/travis234-offsec:2.4.0-offsec.4
travis234 --cwd . -- --continue
travis234 --cwd . -- --resume
travis234 --cwd . -- --session <path-or-session-id>
travis234 --cwd . -- --no-session
```

The selected workspace is never replaced with a broad host mount. The launcher
copies a user-created `~/.travis234/agent/AGENTS.md` into container agent context
when present and copies user skills from `~/.travis234/agent/skills/`.

## Terminal strategy

The agent uses `bash` for finite commands, `bash` plus `process` for an interactive
PTY and follow-up input in the current app session, and `tmux` for listeners,
callbacks, relays, servers, long waits, or work that must survive turns.

`process.wait` waits for terminal state and does not change the command timeout.
If its deadline expires, the command is not killed; wait again from the returned
cursor. Output is bounded to 64 MiB per process and reports `output_limit` when a
producer crosses that bound. Travis234 cannot reattach a running process after an application restart.
User `!command` and `!!command` run asynchronously; `!!`
output is excluded from model context.

See the full [operator manual](../../docs/offsec/manual.md) and
[seven-scenario TUI protocol](../../docs/offsec/tui-test-protocol.md).

## Skills

The package seeds bundled `investigating-security-targets`,
`triaging-security-incidents`, `validating-security-findings`,
`subagent-delegation`, and `web-search` skills only when missing. A same-named user skill takes precedence
over its bundled fallback. Installed Python resources
carry the same lazy skill set.

## Extensions

Global extensions are discovered from `~/.travis234/agent/extensions/`; project
extensions are discovered from `.travis234/extensions/`. Use `/reload` after a
change. Travis JavaScript extensions do not run directly in the Python extension
runtime and require a Python adapter.

Ask the agent to author one in ordinary language. It sees the installed extension guide,
can validate it with `python -m py_compile`, and can use `/reload` to test
it. No extension-authoring skill is required.

Explicit `--extension PATH`, `--skill PATH`, `--prompt-template PATH`, and
`--theme PATH` flags are forwarded after `--`. Session modes and controls are
forwarded the same way.
