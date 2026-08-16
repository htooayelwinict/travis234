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

The terminal packet has exactly these fields: `outcome`, `summary`, `evidence`,
`changedFiles`, `commit`, `tests`, `artifacts`, `failedAttempts`, `blockers`,
`questions`, and `recommendedNextAction`.

Only the SHA-256 digest of a capability is durable. Plaintext exists only in
relay memory and the selected Worker process environment. A terminal packet
never causes automatic Git integration; `mayHaveFilesOrCommits` is a mandatory
inspection warning, not a cleanliness assertion.

The private relay is helper-owned. Do not invoke it directly or use terminal
screen scraping as a message protocol.
