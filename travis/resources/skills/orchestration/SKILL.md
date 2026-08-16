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
