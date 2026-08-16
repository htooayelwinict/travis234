---
name: subagent-delegation
description: Use when the user explicitly requests in-process subagents, child agents, parallel workers or reviewers, bounded delegation, /delegate, or /subagents without a separate durable Travis worktree.
---

# Subagent Delegation

Use this skill for the current request only. Explicit user refusal always wins.

## Choose the mechanism

- Choose in-process subagents for bounded work or review inside the current
  Travis session.
- Choose the independent `orchestration` skill when the user needs another
  independent Travis B in tmux, a separate worktree, durable ping-pong,
  recovery after restart, or full handoff. Do not substitute one mechanism for
  the other.

## Delegate

1. Use one wave with at most 3 children. A later wave needs a new explicit user
   request. Give each child one bounded objective, stop condition, output
   budget, and disjoint file ownership.
2. Read the `spawn_subagent` metadata. When a configured typed role matches the
   objective, pass its exact name. Typed roles can only narrow the selected
   worker or reviewer model, allowed tools, tool and effect ceilings, timeout,
   structured result schema, and artifact policy. Otherwise use a short
   descriptive role such as `reviewer` or `researcher`.
3. Pass exact user-provided targets to the child. Do not pre-read, find, list,
   grep, or resolve a delegated target in the parent. Never expand a vague
   request into an unbounded workspace sweep.
4. Start independent children together with `wait: false`. Continue only
   useful parent-owned work, then collect every child before finalizing. Do not
   let a child spawn another subagent.

## Child contract

Children receive their working directory, bounded goal, complete allowed-tool
catalog, context pack, and return contract. They must use only those tools,
respect concurrent file ownership, execute rather than merely plan, verify the
outcome, and distinguish evidence, uncertainty, failed attempts, and blockers.

A typed role may require a JSON-schema structured result and may allow a
declared artifact list. Declare only workspace-relative UTF-8 regular files. The runtime
validates the result and promotes permitted files to durable `artifact-...`
references; it never trusts arbitrary host paths. Generic children report a
concise summary, changed files, evidence, failed attempts, artifacts, live tmux
sessions, and blockers without full tool traces.

## Supervise and integrate

The human can use `/agents status`, `/agents inspect <id>`, `/agents steer <id>
<message>`, and `/agents cancel <id>`. These TUI commands observe or control the
same session-owned supervisor; they do not create another task engine.

Treat every child result as a report, not proof. Verify material claims and
resolve ownership conflicts. For truncated output, use
`expand_subagent_result`, then page with `offset`; do not reconstruct child work
by re-reading child-owned files. Read returned artifact IDs through the normal
bounded `read` tool.

Report each task ID, role, status, summary, changed files, artifacts, and
blockers. Cancellation, timeout, schema failure, duplicate suppression, and
the three-child limit are terminal outcomes. Do not conceal or automatically
retry them.
