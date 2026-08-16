# Travis234 Orchestration Protocol

- Schema version: 1
- Protocol version: 1

The helper returns one JSON envelope per command. Stable opaque IDs identify
Runs, Tasks, Workers, Dispatches, and Messages. Private request files must be
regular, nonsymlinked, owner-readable files with no group/other permissions;
use `--consume-request-file` when the helper should unlink the validated file.

## Implemented lifecycle

- `run-create|run-show|run-list` define the coordinator objective.
- `task-create|task-show|task-list` define ownership, acceptance criteria,
  supervised/full-handoff mode, prompt budget, and commit policy.
- `worker-start|worker-show|worker-list` create an isolated current/worktree
  workspace and report readiness only after tmux plus Travis RPC are ready.
- `dispatch-start` rotates the idle RPC child into the same durable Travis
  session with a one-Dispatch capability, then returns after prompt acceptance.
- `dispatch-show|dispatch-wait` expose durable status and the structured
  terminal packet. Each wait is bounded to 60 seconds.
- `worker-complete|worker-fail` are Worker-only terminal operations. They
  authenticate the process capability, accept one exact packet, and create one
  durable handoff/failure Message transactionally.
- `message-send` lets the active Worker deposit one authenticated question,
  status, or heartbeat. A question becomes deliverable only after the RPC turn
  is idle.
- `message-check --run-id ... --wait-seconds N` returns unacknowledged Worker
  Messages in stable creation/ID order and records delivery without implying
  acknowledgement.
- `message-ack` records that Travis A processed a complete delivery.
- `message-reply` accepts only an acknowledged active question and resumes the
  same Worker session after prompt-budget and idle checks.
- `worker-retain` records explicit keep-alive intent. `worker-release` requires
  an idle RPC turn and acknowledged deliveries, closes only the exact relay and
  tmux session, and preserves Git/session evidence.
- `dispatch-cancel` is supervised-only and aborts/stops the exact Worker without
  deleting its branch or worktree. `dispatch-abandon` stops monitoring without
  signaling B; a late terminal packet is durable stale evidence and cannot
  advance the abandoned Task.
- `recover --run-id ... [--inspect-only]` reconciles SQLite ownership with the
  exact tmux session, private relay protocol, Travis session ID, and workspace.
  It marks missing workers `lost`, ambiguous live workers `outcome_unknown`,
  redelivers unacknowledged packets, and never sends/replays a prompt.

`full_handoff` changes coordinator behavior, not transport safety: B owns the
handed-off scope, the Worker is retained, `dispatch-start` returns
`monitoring:false`, and no implicit wait or release follows.

The Task prompt budget counts every Dispatch prompt and every coordinator
reply. Dispatch `roundNumber` counts immutable Dispatches only. A correction
requires `parentMessageId` to identify the acknowledged latest terminal
handoff; it never mutates the earlier Dispatch. The default budget is four and
the hard maximum is twelve.

The terminal packet has exactly these fields: `outcome`, `summary`, `evidence`,
`changedFiles`, `commit`, `tests`, `artifacts`, `failedAttempts`, `blockers`,
`questions`, and `recommendedNextAction`.

Only the SHA-256 digest of a capability is durable. Plaintext exists only in
relay memory and the selected Worker process environment. A terminal packet
never causes automatic Git integration; `mayHaveFilesOrCommits` is a mandatory
inspection warning, not a cleanliness assertion.

Lifecycle receipts explicitly report that replay, integration, push, branch
deletion, and worktree deletion were not performed. Recovery may remove only
an exact owner-private stale `launch.json` whose tmux session is absent; it does
not remove sockets, transcripts, worktrees, branches, or Travis sessions.

The private relay is helper-owned. Do not invoke it directly or use terminal
screen scraping as a message protocol.
