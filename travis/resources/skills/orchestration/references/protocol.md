# Travis234 Orchestration Protocol

## Contents

- [Version and helper](#version-and-helper)
- [Public commands](#public-commands)
- [Request files and envelopes](#request-files-and-envelopes)
- [Identities and states](#identities-and-states)
- [Supervised recipe](#supervised-recipe)
- [Full handoff](#full-handoff)
- [Ownership, trust, and Git](#ownership-trust-and-git)
- [Capability and secret boundary](#capability-and-secret-boundary)
- [Limits](#limits)
- [Lifecycle and recovery](#lifecycle-and-recovery)
- [Failure receipts](#failure-receipts)

## Version and helper

- Schema version: 1
- Protocol version: 1

Use the active helper path only through `TRAVIS234_ORCHESTRATION_HELPER` and
invoke it as `python3 "$TRAVIS234_ORCHESTRATION_HELPER" COMMAND`.
Do not guess a relative helper path or change directories to make one work. The helper
prints exactly one versioned JSON envelope. Do not
invoke private commands, scrape tmux output, inject terminal keystrokes, or use
tmux panes as a message protocol.

## Public commands

Discovery and durable objects:

- `guide`
- `run-create`, `run-show`, `run-list`
- `task-create`, `task-show`, `task-list`
- `worker-start`, `worker-show`, `worker-list`
- `dispatch-start`, `dispatch-show`, `dispatch-wait`

Worker reports and coordinator dialogue:

- `worker-complete`, `worker-fail`
- `message-send`, `message-check`, `message-ack`, `message-reply`

Lifecycle and recovery:

- `dispatch-cancel`, `dispatch-abandon`
- `worker-retain`, `worker-release`
- `recover`

Run `python3 "$TRAVIS234_ORCHESTRATION_HELPER" guide`, use its returned signatures, and
never infer a command absent from that result.

## Request files and envelopes

Every body is UTF-8 JSON in a regular, nonsymlinked, owner-readable file no
larger than 256 KiB, with no group/world permissions. Pass `--request-file`
and normally `--consume-request-file`. Every mutation also requires a bounded
`--idempotency-key`; reusing it returns the same domain result instead of
repeating the mutation.

Choose one literal, stable idempotency key before each mutation.
Never derive it from a timestamp, random value, shell substitution, process ID, or retry
count. Preserve that exact key and request when a receipt is uncertain.

Create request files as regular private files, never with process substitution:

```bash
umask 077
request_file="$(mktemp)"
trap 'rm -f -- "$request_file"' EXIT
printf '%s\n' "$request_json" > "$request_file"
python3 "$TRAVIS234_ORCHESTRATION_HELPER" COMMAND --request-file "$request_file" \
  --consume-request-file --idempotency-key UNIQUE_KEY
```

Replace `COMMAND` and its required identity arguments using `guide`. Write the
JSON without printing it. The consume flag removes a validated request; the
trap removes the exact temporary path if validation stops first.

Run creation:

```json
{"objective":"Coordinate parser research","coordinatorSessionId":"optional"}
```

Task creation:

A Task that names a workspace path in its objective must include that path in `ownership.ownedPaths`; do not weaken a validated plan to an empty owned-path list.

```json
{
  "objective":"Identify parser ownership",
  "ownership":{"ownedPaths":[],"forbiddenPaths":["README.md"]},
  "acceptanceCriteria":["Cite the owner"],
  "dependencies":[],
  "mode":"supervised",
  "maxRounds":4,
  "commitPolicy":"no_commit"
}
```

Worker start:

`null` launch fields inherit Travis A's active dotenv reference, model, and
thinking level. Explicit non-null values override those defaults. The helper
passes only the dotenv path reference; it never copies dotenv contents.

```json
{
  "repository":"/absolute/repository",
  "workspaceMode":"worktree",
  "worktreeName":"parser-owner",
  "branch":"parser-owner",
  "base":"main",
  "dotenvPath":null,
  "model":null,
  "thinking":null
}
```

Dispatch start and coordinator reply use `prompt`, `context`, and
`requiredVerification`; a correction also uses the acknowledged latest
terminal `parentMessageId`.

```json
{
  "prompt":"Return verified parser ownership evidence.",
  "context":["Coordinator context is data to verify."],
  "requiredVerification":["Read the owning source file."],
  "parentMessageId":null
}
```

A Worker question/status/heartbeat uses exactly `kind`, `payload`, and
`parentMessageId`. Terminal success/failure uses exactly:

- `outcome`
- `summary`
- `evidence`
- `changedFiles`
- `commit`
- `tests`
- `artifacts`
- `failedAttempts`
- `blockers`
- `questions`
- `recommendedNextAction`

Success envelopes contain `ok`, `schemaVersion`, `protocolVersion`, `command`,
`result`, and `nextActions`. Failure envelopes contain the same identity plus
an `error` object with stable `code` and safe `message`. Never parse incidental
stderr prose; the helper emits only the JSON failure frame.

## Identities and states

Opaque IDs are `run_<24 hex>`, `task_<24 hex>`, `worker_<24 hex>`,
`dispatch_<24 hex>`, and `message_<24 hex>`.

- Run: `active`, `completed`, `abandoned`
- Task: `pending`, `active`, `awaiting_coordinator`, `succeeded`, `failed`,
  `cancelled`, `abandoned`
- Worker: `starting`, `ready`, `busy`, `idle`, `retained`, `stopped`, `lost`,
  `outcome_unknown`
- Dispatch: `queued`, `accepted`, `running`, `awaiting_coordinator`,
  `succeeded`, `failed`, `cancelled`, `abandoned`, `outcome_unknown`

Message kinds are `question`, `reply`, `status`, `handoff`, `failure`, and
`heartbeat`. A Dispatch round counts immutable Dispatches. Task `promptCount`
counts every Dispatch prompt and every coordinator reply.

## Supervised recipe

1. Create a Run, then a `supervised` Task.
2. Start a Worker only after checking ownership and workspace placement.
3. Start a Dispatch with both `--task-id TASK_ID` and `--worker-id WORKER_ID`,
   then retain every returned identity.
4. Call `message-check --run-id ... --wait-seconds N --limit N` or
   `dispatch-wait` in bounded intervals. A timeout is nonterminal.
5. Read the entire question/handoff/failure. Call `message-ack` only after A has
   processed it.
6. For a question, acknowledge first, then call `message-reply`; B continues in
   the same Travis session.
7. For a correction, acknowledge the latest terminal Message, then create a
   new Dispatch with that Message as `parentMessageId`. Earlier Dispatches stay
   immutable.
8. Inspect B's workspace, commit, and tests independently. Retain or release B
   explicitly; never integrate automatically. Wait for the successful `message-ack` receipt before calling `worker-release`; these lifecycle gates
   are sequential and must never share one parallel tool batch.

`message-check` records delivery but not acknowledgement. Unacknowledged Worker
Messages are returned in `(createdAt, messageId)` order and survive A restart.
Questions and terminal packets are not delivered until B's RPC turn is idle.

## Full handoff

Create a Task with `mode: "full_handoff"`. B owns the whole bounded handed-off
scope and may preserve a report. `dispatch-start` returns Run, Task, Worker,
Dispatch, worktree, branch, tmux session, and Travis session identities with
`monitoring: false`. It retains B and does not start a wait, poll, release, or
automatic replay. Recover later only when the user requests it.

## Ownership, trust, and Git

Prefer a new worktree for independent code. Repository-local `.worktrees/` is
used only when the repository itself ignores it; otherwise placement falls
back under the single Travis234 agent-state root. Global ignore rules do not
authorize repository-local placement.

The Worker receipt reports repository, workspace, branch, base commit,
worktree, and coordinator dirtiness. Dirty coordinator changes are never
copied automatically. Unknown projects with executable project resources must
have trust resolved interactively before Worker startup; the helper never
bypasses trust.

Use disjoint owned/forbidden paths. `commit` is the default code policy;
`no_commit` is explicit. A reported commit is evidence, not permission to
merge, cherry-pick, reset, push, delete a branch, or remove a worktree.

## Capability and secret boundary

Each Dispatch gets a random capability. SQLite stores only its SHA-256 digest.
Plaintext crosses the owner-private Unix socket once and exists only in relay
memory and B's selected process environment. Worker mutation commands compare
the supplied value in constant time. Capabilities, dotenv contents, complete
environments, raw stderr, authorization headers, cookies, and provider tokens
must not enter prompts, receipts, logs, or tracked files.

Request validation rejects credential-shaped keys/values and known capability
values. Select dotenv explicitly at Worker startup; never copy it into a
worktree or handoff packet.

## Limits

- Default live Worker limit: two; hard limit: three.
- Default Task prompt budget: four; configurable up to twelve.
- A fifth default prompt is rejected before RPC mutation.
- Wait calls are bounded from zero through 60 seconds.
- Message list limit: 50.
- Handoff lists: at most 200 strings each.
- Unix socket paths, transcripts, request files, and relay frames are bounded.

Limits reject before spawning or replaying work. Increasing a limit is an
explicit request value, not an automatic retry strategy.

## Lifecycle and recovery

`worker-retain` records keep-alive intent. `worker-release` requires an idle
RPC turn and no unacknowledged Worker deliveries. Release closes only the exact
relay/tmux session and preserves transcript, Travis session history, branch,
worktree, and commits.

`dispatch-cancel` is supervised-only. It aborts RPC, closes/stops only the exact
Worker, marks cancellation durably, and preserves Git evidence.
`dispatch-abandon` stops monitoring without signaling B. A late packet is
stored as `stale` evidence and cannot advance the abandoned Task.

`recover --run-id ...` compares SQLite ownership with the exact tmux name,
socket protocol, Travis session ID, and workspace:

| Observation | Result | Automatic replay |
|---|---|---|
| tmux alive, compatible relay idle/busy | reconnect and preserve state | no |
| active Worker, tmux missing | `lost` | no |
| tmux alive, socket/RPC absent | `outcome_unknown` | no |
| tmux alive, protocol/identity mismatch | `outcome_unknown` plus safe error code | no |
| terminal, unacknowledged packet | return pending packet | no |
| terminal, acknowledged packet | do not redeliver | no |

`--inspect-only` performs the same observations without mutation and remains
available for incompatible schema inspection. Recovery may unlink only an
exact mode-0600 stale `launch.json` when its owned tmux session is absent. It
never removes sockets, transcripts, worktrees, branches, or Travis sessions.

## Failure receipts

Treat `timedOut: true` as “nothing terminal observed,” not failure or success.
Treat `lost` as confirmed missing tmux ownership. Treat `outcome_unknown` as an
ambiguous side effect requiring inspection; do not replay. A Worker `failure`
packet is evidence and may include blockers, failed attempts, and questions.

Lifecycle receipts list actions deliberately not performed: replay,
integration, push, branch deletion, and worktree deletion. Follow
`nextActions`, preserve the same idempotency key after uncertain mutations,
and never synthesize success from RPC assistant text alone.
