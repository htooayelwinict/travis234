# appv231 Process Orchestration v2 Design

Date: 2026-07-12
Status: Approved for implementation planning
Supersedes: the deferred/non-goal portions of `2026-07-11-appv231-managed-process-sessions-design.md`

## Goal

Make long-running commands, user shell controls, persistent sessions, and
compaction work as one production-grade coding-agent subsystem without changing
the generic Pi-style agent runtime or the Hermes compaction implementation.

The design keeps both required command modes:

1. Detach and continue: the model starts a command, receives an opaque handle,
   and performs independent work.
2. Await required result: the host waits for meaningful completion while the
   TUI remains responsive and no provider call is spent on each output chunk.

## Non-Negotiable Constraints

- Do not modify any file under `appV2.3.1/appv231/agent/`.
- Do not modify any file under `appV2.3.1/appv231/compaction/`.
- Keep subprocess, shell, PTY, policy, persistence, and TUI behavior in the
  coding-agent profile and application layers.
- Preserve the public Pi-style agent contracts and event ordering.
- Preserve the Hermes compressor and its dual-layer compaction algorithms.
- Preserve legacy synchronous behavior for direct SDK tools, custom
  `BashOperations`, and internal subagents that do not receive the app-owned
  process service.
- Preserve command prefix, shell path, execution backend, spawn hook, extension
  hooks, package-consent policy, and workspace ownership.
- Never expose OS PIDs, process groups, environment values, raw descriptors, or
  host-wide process listings to the model.
- A foreground yield or wait deadline is not a process execution timeout.
- An omitted process timeout continues to mean no automatic kill deadline.
- Running OS processes are not reattached after an application or container
  restart. Only their terminal records and sanitized output may be recovered.
- No tmux, Zellij, Ghostty, WezTerm, cmux, systemd, or external daemon is a
  required production dependency.

## Evidence Being Addressed

The design covers every proven failure from the process/session audit and the
production JSONL analysis:

| Finding | Required outcome |
| --- | --- |
| Repeated process polling consumes model iterations | One host-side wait can cover a long quiet or chatty interval without another provider request. |
| Generic guardrails warned on legitimate cooperative waits | Busy-poll protection remains, but normal waits use process-specific semantics. |
| Terminal output disappeared after the 15-minute in-memory TTL | Terminal metadata and sanitized output are durable before live-record eviction. |
| Session JSONL retained stale `running` handles | Every provider request receives a transient reconciled process ledger. |
| Compaction omitted active handles | Coding-agent compaction details include the reconciled process ledger. |
| `!command` and `/allow` blocked behind the active turn | Both controls return immediately from the TUI input thread. |
| Repeated Ctrl-C was needed while the UI was blocked | Cancellation is routed once to the focused operation without waiting on the session executor. |
| Concurrent steering could lose a message | Coding-agent steering enters a thread-safe mailbox and is flushed on the run thread. |
| SessionStore reparsed the whole JSONL on every append | Appends synchronize only the unseen file suffix under the existing file lock. |
| A spool write failure could still publish `exited` | Output failure deterministically fails and stops the managed job. |
| Active managed output could grow until the filesystem filled | Sanitized output has explicit per-process and app-wide live-spool budgets; crossing one fails and stops only the producing job. |
| Hidden jobs from another workspace consumed the only active slots | Quotas are per owner scope with a separate app-wide safety ceiling. |
| A child that escaped the process group could survive timeout | Local containment tracks and terminates descendants in addition to the process group. |
| Large detached output had no terminal artifact contract | Every truncated terminal result exposes a durable sanitized artifact. |
| `process` had no explicit execution mode | Process actions execute sequentially in provider order. |
| Iteration-limit summary is an additional provider call | The core behavior remains unchanged; process waiting stops routine jobs from reaching it through polling. |

## Chosen Architecture

```text
unchanged appv231.agent loop       unchanged Hermes compaction
              |                              |
              | ordinary tool/context hooks  | coding adapter only
              v                              v
      AgentSession coding profile ---- ProcessContextOverlay
              |                      \       |
              |                       \      +-- compaction details
              |                        \     +-- transient provider context
              v                         v
   bash/process tool adapters      CodingTurnMailbox
              |
              v
      ProcessSessionService
       |       |        |
       |       |        +-- owner-aware quotas
       |       +----------- host-side terminal wait
       +------------------- deterministic state/output budgets
              |
      +-------+-------------------+
      |                           |
      v                           v
Local ProcessTransport     ProcessCompletionStore
group + descendants        indexed metadata + atomic output
      |
      v
pipe or PTY subprocess

TUI input thread
      |
      +-- prompt/steering -> CodingTurnMailbox
      +-- /allow ---------> thread-safe TurnCapabilities
      +-- !/!! -----------> UserCommandController -> ProcessSessionService
```

The generic loop still sees ordinary tool calls and results. No scheduler,
process state, or coding policy moves into the redzone.

## Component Boundaries

### 1. ProcessSessionService v2

The service remains the sole in-memory lifecycle authority. It gains five
capabilities while preserving its current public state machine:

- `wait_terminal`: wait for terminal state or a host deadline while ignoring
  intermediate output wakeups.
- owner-aware reservation: enforce four active jobs per
  `(app_instance_id, workspace_key, origin)` and sixteen active jobs app-wide.
- completion sink: persist a terminal record before emitting terminal events or
  allowing in-memory eviction.
- deterministic output failure: a spool/read failure claims failure, stops the
  process tree, drains what remains, and publishes `failed`, never `exited`.
- bounded live output: defaults cap one sanitized spool at 64 MiB and all live
  spools at 512 MiB. Crossing either cap publishes `failed` with
  `failureCode=output_limit`, preserves the already captured sanitized output,
  and stops that process tree. Command duration alone never triggers this cap.

The new service interface is:

```python
def wait_terminal(
    self,
    owner: ProcessOwner,
    session_id: str,
    cursor: int,
    *,
    wait_ms: int = 60_000,
    max_bytes: int = 51_200,
    signal: object | None = None,
    on_update: Callable[[ProcessSnapshot], None] | None = None,
) -> ProcessSnapshot: ...
```

`wait_terminal` wakes its caller only for terminal publication, cancellation of
the wait, or `wait_ms`. Output readers may call the throttled `on_update`
callback for TUI rendering, but new output alone does not return the tool result
to the model. Cancelling this wait does not kill an already detached process.

`poll` retains its current cursor-driven semantics for interactive commands and
quick observations. It may return as soon as output advances.

### 2. Model Tool Contract

The companion `process` tool adds one action:

| Action | Required | Optional | Behavior |
| --- | --- | --- | --- |
| `wait` | `session_id`, `cursor` | `wait_time_ms`, `max_bytes` | Wait for terminal state or host deadline, ignoring output-only wakeups. |

`wait_time_ms` defaults to 60,000 and accepts 1,000 through 900,000. This is a
host wait, not a process timeout. Existing `poll.yield_time_ms` remains 0 through
30,000 for compatibility.

Prompt guidance becomes explicit:

- Use `poll` for interactive input, a quick status check, or intentionally
  incremental output.
- Use `wait` when the command result is required before task completion.
- Continue independent work before waiting when useful work exists.
- Detach indefinitely only for servers/watchers or when the user requested it.
- Set `bash.timeout` only when an actual execution deadline is intended.

The `process` tool definition sets `execution_mode="sequential"`. If one model
response emits poll, input, and termination actions, provider order is the
execution order.

### 3. ProcessCompletionStore

Terminal results move from a best-effort temp spool to a bounded durable coding
artifact before live eviction. The store lives below:

```text
<agent-dir>/process-results/index.sqlite3
<agent-dir>/process-results/objects/<workspace-hash>/<process-id>-<object-id>.log
```

Directories are mode 0700 and database/output files are mode 0600. SQLite uses
indexed transactions, full synchronization, a busy timeout, and an explicit
schema version; output uses temp-file `fsync` plus atomic replacement. Output
is already UTF-8 decoded and terminal-control sanitized by
`SanitizedOutputSpool`.

Metadata contains only:

- opaque process ID;
- canonical workspace ownership digest and origin;
- launch session ID when available;
- terminal status, exit code, output size, and monotonic elapsed duration;
- completion wall-clock timestamp;
- whether output persistence succeeded.

It does not contain environment values, an OS PID, or raw command text.

Defaults are seven-day retention, a 256 MiB app-wide output ceiling, and 10,000
terminal records, with indexed oldest-terminal eviction. Retention work is
`O(log n + k log n)` for `k` evictions rather than a directory scan on every
completion. The existing fifteen-minute/64-record in-memory cache remains a
fast path. `poll` and `wait` fall back to the durable store after live eviction
or application restart, using workspace and origin checks rather than the
obsolete app-instance ID. A running process missing from both stores is
reported as unavailable after restart, not as still running.

If terminal output exceeds the normal tool-result limits, one terminal tool
result contains the bounded unread tail, advances `nextCursor` to the terminal
output size, and includes a durable `fullOutputPath` plus session-scoped
`artifactId`. The durable path is registered as a borrowed artifact: the read
tool can authorize it, but closing a session artifact registry cannot delete
completion-store data. A resumed session registers a new borrowed reference
only after an owner-authorized poll/wait resolves that process. The model never
receives the private live-spool path. Durable tail extraction reads backward in
bounded blocks and does not load a full 64 MiB artifact into memory.

### 4. Local Process Containment

Process-group signaling remains the first and cheapest control. A local
descendant tracker supplements it for commands whose children call `setsid()`
or otherwise leave the original group.

The tracker uses a proven process-tree library internally and records only
process identity needed for lifecycle cleanup. It snapshots descendants before
TERM, signals the group and tracked descendants, rescans during the grace
period, and sends KILL to survivors. PID data never enters snapshots, JSONL,
logs, tool output, or extension events.

The production guarantee is cleanup of the launched process and descendants
observable to the local OS process tree. Deliberately daemonized processes that
escape both ancestry and the container boundary are outside trusted-local
guarantees; production container teardown remains the final isolation boundary.

### 5. ProcessContextOverlay

Persistent conversation messages are historical facts, not the live process
registry. Before each provider request, the coding profile scans referenced
opaque process IDs and queries live service plus completion store. It appends one
transient, non-displayed custom context message such as:

```text
<managed-process-state>
proc_abcd status=running cursor=4182 outputSize=4182
proc_ef01 status=exited exitCode=0 cursor=9021 outputSize=9021 durableOutput=true
proc_2345 status=unavailable reason=application-restarted
</managed-process-state>
```

No command text or output is duplicated into this overlay. It is not persisted
as a new JSONL entry and does not trigger an LLM turn. It only corrects stale
historical state in the context being sent to the provider.

The same reconciled records are merged into coding-agent compaction details as
`managedProcesses`. `compaction_summary_with_details` renders a bounded
`<managed-processes>` section. This changes only
`coding_agent/compaction_adapter.py` and the call sites that construct details;
the compressor package is untouched.

At most sixteen process records are included. Preference order is running,
stopping/draining, recently terminal but not fully observed, then unavailable
handles referenced in the retained context. The scanner selects at most 64
structured candidates by historical state/recency, then resolves durable rows
in one batch query; provider preparation never performs one database query per
old handle.

### 6. CodingTurnMailbox

The generic `PendingMessageQueue` remains untouched. `AgentSession.steer()` and
`follow_up()` enqueue typed messages into a coding-profile mailbox protected by
one lock. Messages have stable queue IDs, so duplicate text cannot remove the
wrong item from the TUI queue display.

`AgentSession._prepare_next_turn()` runs on the active agent thread. It drains
the mailbox and transfers messages into the unchanged agent queue immediately
before the loop's normal steering callback. This removes the cross-thread
enqueue/drain race without changing core queue implementation.

Internal guardrail steering generated on the agent thread may still enter the
core queue directly. External TUI and extension messages use the mailbox.

### 7. Responsive TUI Control Plane

The TUI input thread must never call `Future.result()` for work queued behind an
active turn.

`/allow package-install` calls the already thread-safe `TurnCapabilities.grant`
directly and renders acknowledgement immediately. The active policy pipeline
can consume the grant on its next protected tool call in the same turn.

`!command` and `!!command` use a new `UserCommandController` backed by
`ProcessSessionService` with `origin="user"`:

1. Capture the current session binding and exclusion flag.
2. Return a local command handle before extension dispatch or process launch.
3. Resolve `user_bash` extension results/operations on the controller worker;
   default execution launches through the managed process service.
4. Drain output on a controller worker and post bounded chunks through the TUI
   dispatcher.
5. On terminal state, render completion immediately.
6. Queue JSONL/session-state recording without blocking the input thread.

`!` remains visible to future model context; `!!` remains excluded. Completion
is recorded against the launch session even if the user switches sessions
while the command runs.

Cancellation routing is deterministic:

1. A focused modal handles its own cancellation.
2. A focused user command receives one interrupt request.
3. Otherwise an active agent turn receives one abort request.
4. Only an idle second Ctrl-C inside the existing exit window exits the TUI.

The user command controller supports multiple jobs, but only the focused/latest
job receives shortcut cancellation. `/processes` remains the explicit selector
for controlling any other agent- or user-origin job. The controller accepts at
most four active user commands, including extension-backed custom operations,
so those operations cannot bypass process-service owner quotas.

### 8. Incremental SessionStore Synchronization

The existing file lock and append-only JSONL format remain authoritative.
SessionStore adds:

```python
self._disk_offset: int
self._disk_identity: tuple[int, int] | None

def _sync_from_disk(self) -> None: ...
```

Under `SessionFileLock`, `_sync_from_disk` compares device/inode and file size:

- unchanged identity and size at or beyond `_disk_offset`: read and parse only
  the unseen suffix;
- file shrink, replacement, or recovery rewrite: perform one full reload;
- malformed non-tail record: preserve the current corruption error;
- incomplete final record: preserve quarantine and atomic recovery behavior.

`_append_entry` records whether the selected parent followed the prior disk
leaf, synchronizes the unseen suffix, then selects either the new disk leaf or
the explicit branch parent exactly as today. Concurrent SessionStore instances
therefore retain no-lost-append behavior without reparsing the historical
prefix.

The performance acceptance gate for a single writer is total bytes parsed no
more than three times final file size across 2,000 small appends. Concurrent
writer and recovery tests remain behavioral gates, not benchmarks only.

## Data Flows

### Required long-running result

1. Model calls `bash`; it exceeds the foreground yield and returns `proc_x`.
2. Model performs independent work if available.
3. Model calls `process(action="wait", session_id="proc_x", cursor=N)`.
4. Host waits without another provider request; output updates may render in
   the TUI.
5. Terminal snapshot returns once, including final tail and durable artifact.
6. The next provider request consumes one tool result and continues.

### Detached server or watcher

1. Model calls `bash` with immediate handoff.
2. It reports the process ID and current status only when detachment was
   requested or the result is not required.
3. `/processes`, poll, write, resize, and signals remain available.
4. App close terminates the process tree.

### Terminal eviction and resume

1. Process reaches terminal state.
2. Service finalizes sanitized output and commits completion metadata/output.
3. Service emits the TUI event and may later evict its live record.
4. A later poll or resumed session resolves the terminal snapshot from durable
   storage.
5. A prior running handle with no live or terminal record is overlaid as
   unavailable after restart.

### Compaction

1. CodingApp/AgentSession requests compaction through existing APIs.
2. Coding adapter snapshots process context before persistence.
3. Existing compressor runs unchanged.
4. AgentSession merges process details with compressor details and appends the
   ordinary compaction JSONL entry.
5. Rebuilt context renders file and process detail sections.

## Error Handling

- Spool append/read/finish failure: claim process failure, terminate the tree,
  retain a sanitized failure code, and publish `failed`.
- Live output budget exceeded: preserve the bounded sanitized prefix/artifact,
  terminate that process tree, and publish `failed` with `output_limit`; do not
  reinterpret the event as an execution timeout.
- Completion persistence failure: keep the live terminal record and output;
  expose `durableOutput=false`; do not lie that recovery is available.
- Durable row/log corruption: quarantine or delete the invalid row/object,
  return a bounded unavailable error, and never execute or trust stored text as
  instructions. A corrupt SQLite index is moved aside with its journal files
  and replaced by an empty versioned index; orphan logs are cleaned only under
  the store's interprocess maintenance lock.
- Wait cancellation: return/raise cancellation for the tool wait without
  terminating a detached job.
- Process execution timeout: terminate the process tree and publish
  `timed_out`; host wait duration never changes this state.
- Owner quota exceeded: reject only the affected owner scope unless the
  app-wide sixteen-job ceiling is reached.
- Background user-command persistence failure: keep the rendered result, show a
  persistence error, and do not block or crash the TUI.
- Mailbox shutdown: reject new messages and drain already accepted messages
  before session disposal.
- Session incremental-sync mismatch: fall back to full reload under lock.

## Compatibility

- Existing JSONL files load without migration.
- New completion artifacts are separate from JSONL and optional during loading.
- Existing `process.poll`, input, resize, signal, and list arguments remain
  valid.
- Direct SDK/custom bash paths remain synchronous.
- Existing session branch, fork, resume, export, and recovery behavior remains.
- Extensions continue to receive ordinary tool and result events. New process
  metadata is bounded and contains no command environment.
- `user_bash` handler order and payload remain stable, but those handlers and
  custom operations execute on the user-command worker so the TUI input owner
  stays responsive; custom operations must honor their abort signal.
- The npm launcher requires no API change. A Python dependency change, if used
  by local descendant containment, is included automatically in the image
  installation and does not require npm package publication by itself.

## Rollout Sequence

1. Complete process runtime v2: output failure/budgets, owner quotas,
   descendant cleanup, terminal artifacts, completion persistence, host-side
   wait, sequential execution, and model guidance.
2. Add the coding mailbox and asynchronous TUI control plane.
3. Replace full SessionStore append reload with suffix synchronization, then add
   context reconciliation and coding-adapter compaction details.
4. Run focused, full-suite, source-TUI, and production-container verification.

Each stage is independently revertible and must leave both redzones with zero
diff.

## Verification Gates

Implementation is not complete until tests prove:

1. A chatty five-minute fake job requires one `process.wait` tool result, not a
   provider call per output event.
2. Poll remains cursor deterministic and suitable for interactive commands.
3. Wait cancellation leaves a detached process alive.
4. Spool failure publishes `failed` and never `exited`.
5. Per-process/app-wide spool budgets bound disk growth and publish
   `output_limit` without confusing it with a timeout.
6. Per-owner quotas isolate workspaces/origins while the global ceiling holds.
7. Timeout, terminate, kill, and app close remove process-group and escaped
   descendant fixtures on the production Linux path.
8. Terminal output survives live TTL eviction and a new CodingApp instance.
9. Large detached output returns one bounded terminal result plus a readable
   borrowed artifact that remains after session-registry close.
10. Old running transcript handles become terminal-from-store or unavailable,
   never falsely live.
11. Compaction retains bounded process state with zero edits under
    `appv231/compaction/`.
12. `!` and `/allow` complete or acknowledge while an agent tool wait is active.
13. One Ctrl-C cancels the focused user shell or active turn and the TUI remains
    responsive.
14. Concurrent steering messages are neither lost nor merged by equal text.
15. Two SessionStore instances still append without lost/torn records.
16. Two thousand single-writer appends stay within the suffix-parse budget.
17. `process.execution_mode` is sequential and conflicting batched controls
    execute in provider order.
18. Full Python tests, package build, source TUI protocol, and production-image
    TUI protocol pass.
19. `git diff --name-only` contains no path under either redzone.

## Non-Goals

- No change to generic iteration-limit semantics or summary-call accounting.
- No change to generic agent queue implementation.
- No change to Hermes compression algorithms or storage code.
- No reattachment to a still-running OS process after app/container restart.
- No automatic model wake-up solely because a background job completed.
- No arbitrary PID attachment or host process browser.
- No external terminal multiplexer dependency.
- No attempt to turn lexical command policy into a security sandbox.
- No GHCR push, npm publication, or GitHub release as part of implementation
  unless separately requested.
