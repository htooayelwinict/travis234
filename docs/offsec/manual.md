# Travis234 OffSec operator manual

Travis234 OffSec is one tactical security agent for operator-directed CTF, lab,
security-research, DFIR, incident-response, malware-analysis, and engineering
work. It uses the existing Travis234 runtime, providers, sessions, compaction,
extensions, and skills. There is no profile to select and no engagement manifest
to create.

## Start in the environment that can reach the target

For a source checkout:

```bash
uv sync
uv run travis234 --cwd ~/agent-work --target 10.129.1.23
```

For the published Python distribution:

```bash
uv tool install --python 3.13 travis234-offsec
travis234 --cwd ~/agent-work --target 10.129.1.23
```

For the optional Kali image through npm:

```bash
npx @htooayelwinict/travis234-offsec --cwd ~/agent-work -- --target 10.129.1.23
```

`--target` is optional, repeatable context supplied by the operator. It does not
validate, permit, block, route, or connect anything. Omit it for local artifact
analysis, or repeat it for related hosts, files, case identifiers, and lab context.
No manifest is required.

Prefer host-native execution on the Kali box or other host that already owns a
VPN route. This lets commands see `tun0`, lab-only address space, local evidence,
and installed tools directly. Docker is optional and is most useful when its
network can already reach the lab; the npm launcher runs the published Kali image.

## Authentication and state

Use `/login` in the TUI, normal provider environment variables, or `--dotenv`.
The npm launcher does not copy the host `.env` or provider credentials into the
container. Never put keys in tracked files or prompts.

All durable state remains under `~/.travis234`. Sessions are stored under
`~/.travis234/agent/sessions/`; global skills and extensions live under
`~/.travis234/agent/skills/` and `~/.travis234/agent/extensions/`. Project-local
extensions live under `.travis234/extensions/`. In the npm container,
`~/.travis234/sandbox-home` is mounted at `/travis-home`, so its sessions are at
`/travis-home/agent/sessions/`.

## Tactical operating loop

Give a concrete outcome and available evidence. The agent works through Orient,
Acquire, Analyze, Act, Verify, and Record. It distinguishes facts from hypotheses,
records failed attempts, checks Kali tool availability with `command -v`, and
does not claim findings or impact without target-derived evidence.

Terminal selection is deliberate:

- Use `bash` for finite commands expected to finish promptly.
- Use `bash` plus `process` for an interactive program in the current session.
  The process service can poll, wait, send follow-up input or keystrokes, resize a
  PTY, interrupt, terminate, and kill the spawned shell.
- Use `tmux` for listeners, reverse connections, OOB callbacks, relays, servers,
  long waits, or work that must survive agent turns. Capture evidence and stop the
  session explicitly.

`process.wait` waits for terminal state for 1 to 900 seconds and does not change
the command timeout. When its wait deadline expires, the command is not killed;
another wait can continue from the returned cursor. Output is bounded to 64 MiB
per process by default, and a producer crossing the limit reports `output_limit`.
Travis234 cannot reattach a running process after an application restart, which
is why turn-persistent work belongs in tmux. User `!command` and `!!command` run
asynchronously; `!!` output stays outside model context.

The tmux tool returns a resolved workspace-namespaced session name. Copy that
exact value when inspecting it natively, for example:

```bash
tmux attach -t travis234-a1b2c3d4e5f6-callback-check
tmux capture-pane -p -t travis234-a1b2c3d4e5f6-callback-check -S -200
```

`a1b2c3d4e5f6` is only an example workspace digest. Do not guess it; use the
resolved name returned by the tool.

## Delegation

The parent may run at most three children concurrently. Children inherit the
workspace, targets, managed process service, and a workspace-write tool set:
`read`, `grep`, `find`, `ls`, `bash`, `process`, `edit`, `write`, and `tmux`.
They cannot spawn more subagents. Delegate disjoint file ownership, request
evidence and stop conditions, and have the parent reconcile every result pack.

The bundled lazy skills are `investigating-security-targets`,
`triaging-security-incidents`, `validating-security-findings`,
`subagent-delegation`, and `web-search`. A same-named user skill takes precedence
over the bundled fallback.

## Sessions and compaction

Use `/session` to inspect the active JSONL session, `/compact [focus]` to compact
older context, `/resume` to choose a session, and `--continue` to resume the most
recent compatible session at startup. `/exit` closes owned managed processes.
Detached tmux sessions are explicit external work and must be stopped when done.

## Extensions

Global extensions are discovered from `~/.travis234/agent/extensions/`; project
extensions are discovered from `.travis234/extensions/`. Use `/reload` after a
change. Travis JavaScript extensions do not run directly in the Python extension
runtime and require a Python adapter.

You can ask the agent to author an extension in ordinary language. It sees the
installed extension guide, can validate Python with `python -m py_compile`, and
can reload with `/reload`. No extension-authoring skill is required.

For an exact runtime qualification, follow the
[seven-scenario TUI protocol](tui-test-protocol.md).
