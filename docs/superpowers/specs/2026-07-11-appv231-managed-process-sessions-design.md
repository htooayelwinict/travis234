# appv231 Managed Process Sessions Design

Date: 2026-07-11
Status: Approved for implementation planning

## Goal

Give appv231 Codex-like long-running command behavior without changing the
existing agent loop. A coding-agent `bash` call waits briefly for a normal
result, then yields an opaque process handle when the command is still running.
The model and the TUI can inspect or control that process while the unchanged
agent loop continues ordinary turns.

## Non-Negotiable Constraints

- Treat all of `appV2.3.1/appv231/agent/` as redzone. Do not modify the loop,
  coordinator, agent types, cancellation, event ordering, or iteration logic.
- Treat all of `appV2.3.1/appv231/compaction/` as redzone.
- Keep managed-process implementation in the coding profile. The generic agent
  runtime must not import subprocess, PTY, shell, or process-session code.
- Preserve the existing synchronous behavior for custom `BashOperations`,
  direct SDK tool construction, and internal subagents that do not receive the
  app-owned service.
- Preserve command prefix, shell path, spawn hook, execution backend, workspace,
  extension hook, package-consent, and tool-loop policy behavior.
- Jobs survive model turns and in-process `/new` or `/resume` replacement, but
  they are not persisted or recoverable after application or container restart.
  Normal app shutdown terminates them; an uncatchable host crash relies on the
  OS or container process boundary for final cleanup.
- Omitted `timeout` means no runtime deadline. The 10-second default is only the
  initial foreground yield window and never kills a command.

## Existing Behavior and Design Boundary

Today `coding_agent/tools/bash.py` calls `BashOperations.exec`, waits until the
child exits, and returns only then. `ToolCoordinator` runs the synchronous call
off the UI thread, so rendering remains responsive, but the current model turn
cannot advance until the command finishes.

No agent-loop change is necessary. A managed `bash` implementation can return a
normal `AgentToolResult` with `status=running` after the yield window. From the
loop's perspective the tool call completed normally. The app-owned service,
not the loop, retains the subprocess and output stream.

## Chosen Architecture

```text
unchanged appv231.agent loop
        |
        | ordinary tool call/result
        v
coding_agent.tools.bash       coding_agent.tools.process
        |                               |
        +---------- policy-aware -------+
                        |
                        v
        coding_agent.processes.ProcessSessionService
              | owner: CodingApp instance
              | scope: canonical workspace + origin
              | state, output, input, timeout, signals
                        |
                        v
        coding_agent.execution_backend.ExecutionBackend
              | pipe transport (default)
              | POSIX PTY transport (explicit)
                        |
                        v
                 subprocess group
```

`CodingApp` creates exactly one `ProcessSessionService` and injects it into each
replacement `AgentSession`. Session disposal must not close the shared service.
`CodingApp.close()` is the sole lifetime authority and terminates all remaining
process groups before deleting service-owned spools.

The service is backend-neutral within the coding profile: it owns state and I/O
but receives process creation through the existing declared
`ExecutionBackend`. No tmux, Zellij, terminal-emulator, or shell implementation
is a v1 dependency.

## Model Tool Contract

### `bash`

The production coding profile extends the existing schema:

| Argument | Type | Default | Contract |
| --- | --- | --- | --- |
| `command` | string | required | Shell command after existing prefix/hook resolution. |
| `yield_time_ms` | integer, 0..30000 | `10000` | Initial wait only; `0` requests immediate background handoff. |
| `timeout` | positive number in seconds | none | Maximum total runtime from successful spawn. |
| `tty` | boolean | `false` | Allocate a PTY only when interactive terminal behavior is required. |
| `rows` | integer, 2..200 | `24` | Initial PTY height; rejected when `tty=false`. |
| `cols` | integer, 20..500 | `80` | Initial PTY width; rejected when `tty=false`. |

If the process reaches a terminal state before `yield_time_ms`, `bash` preserves
the current contract: exit zero returns normally, nonzero exit is a tool error,
timeout is a tool error, and abort is a tool error. Existing tail truncation and
full-output artifact behavior remain available for this completed fast path.

If the process is still nonterminal at the yield boundary, `bash` returns a
successful result containing output from cursor zero and these structured
details:

```json
{
  "status": "running",
  "sessionId": "proc_<128-bit-random-id>",
  "cursor": 0,
  "nextCursor": 4182,
  "outputSize": 4182,
  "exitCode": null,
  "tty": false,
  "elapsedMs": 10001,
  "suggestedPollDelayMs": 1000
}
```

The returned text must say that the command is still running and name the
process handle. It must not imply that `yield_time_ms` is a timeout.

### `process`

Use one companion tool rather than one tool per operation. Its flat schema is
portable across providers; the executor performs action-specific validation.

| Action | Required arguments | Optional arguments | Result |
| --- | --- | --- | --- |
| `poll` | `session_id`, `cursor` | `yield_time_ms`, `max_bytes` | New output from the cursor or terminal status. |
| `write` | `session_id`, `input` | `eof`, `yield_time_ms` | Ordered input acceptance plus current status/output. |
| `resize` | `session_id`, `rows`, `cols` | none | Updated PTY dimensions; rejected for pipes. |
| `interrupt` | `session_id` | `yield_time_ms` | Send SIGINT/control equivalent to the process group. |
| `terminate` | `session_id` | `yield_time_ms` | Send TERM, then escalate after the service grace period. |
| `kill` | `session_id` | none | Send KILL immediately to the process group. |
| `list` | none | none | Registry entries visible to the active workspace and origin. |

Defaults and limits:

- `poll.yield_time_ms`: 1000, range 0..30000. Poll waits for output, terminal
  state, or this window; it is not a process timeout.
- `poll.max_bytes`: 51200, range 1024..51200.
- `write.input`: at most 16384 UTF-8 bytes per call and at most 65536 pending
  bytes per process. Writes enter an ordered, bounded input pump so a blocked
  child cannot block the agent tool thread.
- `eof=true` queues stdin closure after the supplied input.

Every operation returns the same snapshot envelope. Public states are
`running`, `stopping`, `draining`, `exited`, `timed_out`, `terminated`, and
`failed`.
Terminal observations through `process.poll` are successful observations even
when `exitCode` is nonzero; the failed command is distinct from a failed poll.
Invalid handles, owner mismatch, invalid state, unsupported resize, and input
queue exhaustion are tool errors.

The model never receives an OS PID, process-group ID, environment block, raw
file descriptor, or host-wide process listing.

## State Machine and Race Rules

```text
starting -> running -> draining -> exited
    |          |           |
    |          +-> stopping+-> timed_out
    |          |           +-> terminated
    |          +----------------> failed
    +----------------------------> failed
```

- `starting` covers spool/pipe/PTY allocation and spawn. Spawn failure closes
  every allocated descriptor and creates no public handle.
- `running` accepts input, resize, and signals.
- `stopping` records the first accepted terminal cause and rejects new input.
- `draining` means the OS process exited but readers are collecting final bytes.
  A terminal state is published only after both output readers reach EOF or the
  bounded drain deadline expires.
- Terminal states are immutable.

All state transitions, cursor bounds, and termination-cause claims occur under
one per-process lock and condition. The first actor that observes a live process
and claims termination wins. Later timeout, user signal, abort, or shutdown
requests are idempotent and cannot relabel the terminal cause.

At the initial yield boundary, the same lock decides ownership:

1. Terminal already published: return the completed fast path.
2. Tool abort observed while foreground-owned: claim `abort_before_yield`, kill
   the group, drain, and return the current abort error.
3. A timeout or shutdown cause claimed while still foreground-owned keeps the
   call foreground-owned until bounded stop/drain completes, then returns its
   terminal error even if this crosses the yield deadline.
4. Otherwise set `detached=true` and return `status=running`. After this atomic
   handoff, aborting the completed model turn does not kill the process.

Timeout uses `time.monotonic()` from successful spawn. If the monitor observes
an exit before it claims timeout, natural exit wins. Otherwise timeout records
the cause, sends TERM, waits 2 seconds, and escalates the whole group to KILL.
Explicit `terminate` and app shutdown use the same 2-second escalation. `kill`
has no grace period. `interrupt` does not claim termination unless the process
actually exits in response.

## Output and Cursor Contract

Each job owns a mode-0600 append-only spool inside a mode-0700 app-instance temp
directory. The directory is not a workspace path and is never presented as an
arbitrary model-writable target.

Pipe mode has one reader per stdout/stderr pipe. PTY mode has one master reader.
All readers append through a single locked writer, which defines the observable
merged order. Exact kernel-level ordering between stdout and stderr is not
promised, matching the current merged-output behavior.

Before storage, output is incrementally decoded as UTF-8 with replacement and
sanitized. Preserve printable text plus tab, newline, and carriage-return
normalization; remove CSI, OSC (including OSC 52), device-control, and unsafe
C0/C1 sequences. Neither model results nor TUI rendering receive raw terminal
control sequences.

A cursor is a nonnegative byte offset into the sanitized UTF-8 spool. `poll`
returns `[cursor, nextCursor)` and never advances hidden server-side reader
state. Repeating the same cursor is deterministic. Reads stop on a valid UTF-8
boundary and never exceed `max_bytes`. Empty output does not mean EOF; only a
terminal `status` does.

For a command that completes before handoff, the adapter exports a private copy
to the current session artifact registry when existing truncation rules require
`fullOutputPath`. Active or detached jobs are read in bounded cursor chunks;
their live service spool path is never exposed.

## Pipe and PTY Modes

Pipe mode is the default because it is deterministic, inexpensive, and suitable
for builds, tests, servers, and package commands. It uses stdin/stdout/stderr
pipes and a new process group/session.

PTY mode is explicit. It connects stdin/stdout/stderr to one slave, sets the
initial window size before spawn, and closes the parent's slave immediately.
The monitor treats Linux PTY `EIO` after child exit as EOF. Master, slave, input
pump, reader, and process descriptors are closed exactly once on every success
and failure path. PTY support is POSIX-only in v1; unsupported platforms return
a clear validation error rather than silently falling back to pipes.

The service API leaves room for later tmux, Zellij, Ghostty, WezTerm, or cmux
adapters, but those products are presentation or multiplexing integrations, not
the lifecycle authority. A future adapter must implement the same state,
ownership, output, and cleanup contract.

## Ownership, Policy, and Guardrails

Each process record has immutable ownership:

```text
ProcessOwner(
  app_instance_id,
  workspace_key=canonical resolved cwd,
  origin="agent" | "user"
)
```

Model operations can see only `origin=agent` jobs owned by the current canonical
workspace. Switching to another workspace hides the old jobs without killing
them; switching back restores access. Even possession of a valid opaque handle
from another workspace returns the same not-found response as an unknown ID.
`list` queries only the service registry and never invokes `ps`.

The existing `before_tool_call` path remains the policy entry point because
both `bash` and `process` are ordinary coding-agent tools. Required policy work:

- Keep extension `tool_call` and `tool_result` hooks active for every action.
- Evaluate package consent for initial `bash.command` and obvious package
  mutation submitted through `process(action=write, input=...)`.
- Classify `process.poll` and `process.list` as observations; classify input,
  resize, and signals as mutations.
- Give repeated polls a semantic signature of `(session_id, cursor)` so the
  existing no-progress guardrail can stop unchanged busy-poll loops.
- Consume package consent only when the protected operation is accepted.
- Never claim these lexical policies make trusted-local execution a sandbox.
  Filesystem/process containment remains the declared backend's responsibility.

Inspection of `process.write` is explicitly best-effort: fragmented, encoded,
or program-interpreted input cannot be proven safe by lexical policy. Initial
`bash.command` authorization and the declared execution backend remain the
actual launch and containment boundaries.

The process service inherits the same environment produced by existing
`get_shell_env()` and does not add an environment-inspection API. This preserves
developer-tool compatibility without widening current exposure. Registry and
diagnostic output must redact environment values and bound displayed commands
to 200 characters.

Resource defaults are app-wide: at most four active jobs, at most 16 KiB input
per call, at most 64 KiB pending input per process, and at most 50 KiB output per
tool result. Active jobs have no automatic TTL because that would contradict the
no-default-timeout contract.
Terminal records and spools are retained for 15 minutes, capped at 64 records,
then evicted oldest-first. Active jobs are never evicted.

## App, Session, and TUI Lifecycle

- `CodingApp` creates and owns the service before creating its first session.
- Every runtime replacement receives the same service reference.
- `AgentSession.dispose()` never shuts down an injected app-owned service.
- Direct/child `AgentSession` instances without that service retain synchronous
  bash and do not expose `process`.
- `CodingApp.close()` is idempotent: unsubscribe process observers, terminate
  process groups, dispose the current session runtime, and remove process spools.
- CLI prompt, plain, and TUI paths call `CodingApp.close()` in `finally`.

The TUI subscribes to service events through its UI dispatcher. It shows one
bounded completion status when a detached job reaches a terminal state. This is
strictly presentational: it does not change agent state, inject an unsolicited
model message, or start an LLM turn. An idle agent stays idle until the user
sends another prompt; an active model observes completion only through an
ordinary `process.poll` result. `/processes`
opens a current-workspace registry selector with status, elapsed time, PTY mode,
and a shortened command. The selected job can be refreshed, interrupted,
terminated, or killed through the same service methods. No terminal pane or
external multiplexer is required.

Existing user `!command`/`!!command` persistence remains synchronous in v1. It
may migrate to the shared service after its cross-session message ownership is
designed separately; this avoids silently changing `bashExecution` JSONL
semantics in this feature.

## Compatibility and Rollout

1. Land the service, deterministic fake backend, pipe transport, and tests with
   no tool registration change.
2. Integrate managed `bash` only when `CodingApp` injects the service. Keep the
   legacy operations path for SDK/custom/subagent callers.
3. Register `process` in the production coding profile and add policy coverage.
4. Add PTY/input/resize after pipe-mode lifecycle gates pass.
5. Add TUI notifications and `/processes` management.
6. Enable by default only after source and production-container TUI smoke tests.

A temporary feature flag may be used during implementation, but the completed
production contract has one behavior, not split production modes.

## MCP Review Reconciliation

Three independent MCP reviews covered concurrency, security, and tool/TUI UX.
The design adopts their evidence-backed requirements: locked state transitions,
drain-before-terminal publication, process-group escalation, PTY descriptor
cleanup, ordered nonblocking input, opaque workspace ownership, ANSI/OSC
sanitization, resource limits, bounded polling, and explicit completion events.

The design rejects three suggestions that conflict with approved requirements
or current architecture:

- No mandatory active-job TTL; explicit `timeout` and app shutdown are the
  lifetime controls.
- No transcript rewriting or virtual viewport inside the agent/compaction
  redzones; bounded ordinary tool results are used instead.
- No OS process-table reconstruction after app restart; restart persistence is
  an explicit non-goal.

## Verification Gates

Implementation is not complete until tests prove:

1. Short, detached, nonzero, timeout, and abort-before/after-handoff behavior.
2. First-wins races for exit vs timeout, terminate vs exit, and shutdown vs kill.
3. Cursor determinism, UTF-8 boundaries, output flood bounds, final-byte drain,
   and control-sequence removal.
4. Pipe input ordering, EOF, broken pipe, PTY detection, resize, EIO-as-EOF, and
   descriptor cleanup.
5. Whole process-tree TERM/KILL behavior with no live child after app close.
6. Same-workspace `/resume` retains handles; cross-workspace access is hidden.
7. Package-consent and no-progress guardrails cover process actions.
8. Custom operations, command prefix, spawn hook, direct SDK tools, and internal
   subagent synchronous behavior remain compatible.
9. TUI completion events and `/processes` mutate UI only through the dispatcher.
10. Full Python/npm suites, actual source TUI smoke, and production-container TUI
    smoke pass with zero diff under both redzones.

## Non-Goals

- No modifications to the generic agent loop or compaction implementation.
- No process survival or reattachment after app/container restart.
- No guarantee that Python cleanup runs after uncatchable host `SIGKILL` or
  machine failure; container/OS process isolation is responsible in that case.
- No host PID attachment, daemon manager, shell service, or remote execution.
- No automatic LLM wake-up when a process completes.
- No tmux/Zellij/Ghostty/WezTerm/cmux runtime dependency in v1.
- No cross-workspace process control.
- No replacement of OS/container sandboxing with command-string inspection.
- No migration of user `!`/`!!` JSONL behavior in this feature.
