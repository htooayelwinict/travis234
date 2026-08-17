---
name: orchestration
description: Use when Travis A should start, supervise, ping-pong with, recover, or hand off work to another independent Travis B as a durable Travis234 session in tmux, especially in a Git worktree.
---

# Local Tmux Orchestration

Travis A owns the user conversation. Travis B owns the handed-off scope
as an independent Travis234 session, not a subagent or extension of A's turn.

Explicit coordination may select this route; its exact helper, protocol,
identities, acknowledgement, recovery, retention, release, and no-integration
rules then apply.

Confirm `bash`. Do not probe tmux availability; `worker-start` is authoritative and returns
a structured failure receipt. Without `bash`, report orchestration unavailable;
do not improvise a background worker.
Invoke only the active version-matched helper as `python3
"$TRAVIS234_ORCHESTRATION_HELPER" COMMAND`. Do not print or probe the helper path,
guess another path, or change directories to find one.
For each JSON mutation, run `umask 077`, create a regular file with `mktemp`
(never process substitution), and pass it with `--consume-request-file`.

| Mode | Choose when | Coordinator behavior |
|---|---|---|
| supervised | A must review questions, evidence, or corrections | Poll boundedly, acknowledge every processed delivery, then retain or release B |
| full handoff | The user transfers the whole bounded scope to B | Return acceptance identities with monitoring off; recover only when requested |

Follow this coordinator recipe:

1. Create a Run and Task with explicit ownership, acceptance criteria, mode,
   prompt budget, and commit policy. Copy every named planned scope into the Task's `ownedPaths`; never replace a named path with an empty list.
2. Select a safe current workspace or isolated Git worktree. Start B and verify
   its Worker, tmux, Travis session, branch, base, workspace, and dirty-state
   receipt before dispatch. Null launch fields inherit A's active dotenv
   reference, model, and thinking level; never copy dotenv contents.
3. Start one Dispatch. In supervised mode, use bounded message checks, process
   each complete question or terminal packet, then acknowledge it. Reply to an
   acknowledged question; create a parent-linked Dispatch for a correction.
4. Review reported files, commits, and tests independently. A packet is a
   report, not proof. Keep writes disjoint. Require committed code unless the
   user chose `no_commit`. Do not perform automatic integration.
5. Stop on success, failure, cancellation, a blocking question, or the prompt
   limit. Retain B when evidence must remain live; otherwise release only after
   B is idle and all deliveries are acknowledged.

Never call the tmux tool or tmux executable directly, including list-sessions, capture-pane, send-keys, or screen scraping.
A timeout does not permit bypassing the helper; use only helper receipts, bounded waits, recovery, cancellation, retention, and release.
Also forbid direct B-to-A prompt injection, silent
replay, trust bypass, capability exposure, automatic Git actions, unverified
worker/worktree deletion, and subagent substitution.

For example: “Have Travis B inspect and report.”

Wait for any required coordination plan to validate, then read [the
protocol](references/protocol.md) completely.
Before the first mutation, run `python3 "$TRAVIS234_ORCHESTRATION_HELPER" guide` exactly once and retain its version-matched command signatures.
Treat protocol read -> guide -> mutation as a gate: do not infer request fields, guess a request body, or retry a guessed body.
`dispatch-start` always requires both `--task-id TASK_ID` and `--worker-id WORKER_ID`. Never run `message-ack` and `worker-release` concurrently; wait for
the acknowledgement's success receipt before release.
