---
name: orchestration
description: Use when the user asks one Travis234 session to start, supervise, ping-pong with, recover, or hand work to another durable Travis234 session in tmux, especially in a Git worktree.
---

# Local Tmux Orchestration

Coordinate another durable Travis234 session through the bundled helper and
the version-matched protocol reference. Keep coordinator and worker ownership
explicit, and use structured handoffs rather than terminal screen scraping.

Read [the protocol reference](references/protocol.md) before invoking the
helper. Run `python3 scripts/orchestrate.py guide` for its current command
surface.

Use this lifecycle:

1. Create a Run and a bounded Task.
2. Start one Worker only after checking its ownership and worktree receipt.
3. Start a Dispatch with a private request file; the helper supplies the
   worker capability without exposing it to the coordinator transcript.
4. Poll `dispatch-wait` for at most 60 seconds at a time. Continue useful
   coordinator work between polls.
5. Use `message-check` for durable Worker questions or terminal packets.
   Process the complete delivery, then call `message-ack`. Reply only to an
   acknowledged question. A focused correction is a new Dispatch whose
   `parentMessageId` is the acknowledged latest terminal Message.
6. Treat a terminal handoff as a worker claim. Inspect its evidence and Git
   state before any later integration or cleanup decision.
7. Retain a live Worker when its workspace/session must remain available.
   Release only an idle Worker with no unacknowledged deliveries. Use
   supervised cancellation for exact owned work; use abandonment when
   monitoring should stop without signaling B. Run `recover` after a
   coordinator restart or uncertain local state; recovery observes and never
   replays a prompt.

Never invoke `_relay`, scrape tmux output as a protocol, paste credentials or
capabilities into prompts, or merge/cherry-pick/delete a worktree merely
because a Worker reported success. Nested orchestration requires the user's
explicit authorization.
