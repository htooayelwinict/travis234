---
name: orchestration
description: Use when Travis A should start, supervise, ping-pong with, recover, or hand off work to another independent Travis B as a durable Travis234 session in tmux, especially in a Git worktree.
---

# Local Tmux Orchestration

Travis A owns the user conversation and coordination decisions. Travis B owns
only the handed-off scope. Treat B as an independent Travis234 session, not a
subagent or an extension of A's turn.

Explicit coordination may select this route, but then this skill's exact
helper, protocol, identities, acknowledgement, recovery, retention, release,
and no-integration rules apply without substitution.

Before acting, read [the protocol](references/protocol.md). Confirm the `bash`
and `tmux` tools are available. If tmux is excluded, explain that durable local
orchestration is unavailable; do not improvise a bash-only background worker.
For each JSON mutation, run `umask 077`, create a regular file with `mktemp`
(never process substitution), and pass it with `--consume-request-file`.

| Mode | Choose when | Coordinator behavior |
|---|---|---|
| supervised | A must review questions, evidence, or corrections | Poll boundedly, acknowledge every processed delivery, then retain or release B |
| full handoff | The user transfers the whole bounded scope to B | Return acceptance identities with monitoring off; recover only when requested |

Follow this coordinator recipe:

1. Create a Run and Task with explicit ownership, acceptance criteria, mode,
   prompt budget, and commit policy.
2. Select a safe current workspace or isolated Git worktree. Start B and verify
   its Worker, tmux, Travis session, branch, base, workspace, and dirty-state
   receipt before dispatch.
3. Start one Dispatch. In supervised mode, use bounded message checks, process
   each complete question or terminal packet, then acknowledge it. Reply to an
   acknowledged question; create a parent-linked Dispatch for a correction.
4. Review reported files, commits, and tests independently. A packet is a
   report, not proof. Keep writes disjoint. Require committed code unless the
   user chose `no_commit`. Do not perform automatic integration.
5. Stop on success, failure, cancellation, a blocking question, or the prompt
   limit. Retain B when evidence must remain live; otherwise release only after
   B is idle and all deliveries are acknowledged.

Avoid raw tmux keystrokes, screen scraping, direct B-to-A prompt injection,
silent replay, trust bypass, capability exposure, automatic Git actions,
unverified worker/worktree deletion, and subagent substitution.

For example: “Start another Travis in a new worktree, have it inspect parser
ownership, bring its evidence back for my review, then release it safely.”

Run `python3 scripts/orchestrate.py guide` for version-matched command signatures.
