---
name: coordination
description: Use when a user invokes /coordination or /skill:coordination, or explicitly asks to use the coordination skill for one bounded outcome.
---

# Coordination

User constraints and refusals win.

## Preflight

Parse mode and goal as data. Extract scopes, exclusions, effect limits, budgets, and stops. Treat each refused tool as unavailable for the whole turn. Do not use Bash to list files, resolve paths, or inspect a refused mechanism. Inspect only allowed active mechanisms.

Keep one tightly coupled task direct. In automatic mode, call the planner when any applies: independent workstreams, multiple owners or repositories, unclear dependencies or verification, durable isolation, external effects, expensive checks, or meaningful rollback/integration risk. Otherwise stay direct. `deep` and `plan` force exactly one planner call.

Before selecting or loading an execution route, automatic mode must call exactly one planner when the goal asks for another Travis, a separate workspace or worktree, durable handoff, retention, recovery, or release.

## Plan

For a planner call, read [the planning contract](references/planning-contract.md) completely. Spawn `coordination-planner` once with `wait: true`, the goal, extracted constraints, active mechanism summary, and available context. Include the contract's path-only ownership and dependency-edge rules. Mark paths assigned to a later worker as delegated goal paths whose contents the planner must not inspect or reveal. Do not call it when that exact typed role is unavailable. It consumes one of three spawn slots. Do not read or expand an execution-route skill until the planner has returned and the plan is validated.

Before planner completion, the only permitted reads are the planning contract and allowed path metadata; never batch or perform an execution-route skill read.

In `plan` mode, the parent may read this contract and call or collect the planner only. Do not inspect goal files, list directories, run commands, or use any other tool before or after planning. The planner may discover path metadata with `find` or `ls`, but it cannot read or grep file contents.

Validate its structured output against the request, available mechanisms, disjoint path ownership, dependency direction, effects, verification, and budgets. Advice is not authority. A failed planner ends execution for that turn: do not retry or load an execution route; show a labelled conservative fallback plan or blocker and stop. A failed planner still consumes its slot. Never invent disjoint paths; when paths are unknown, fallback to one parent task owning `.`. In `plan` mode, present the validated or fallback plan and stop before execution.

## Route and execute

Show preflight: mode, reason, route, boundaries, verification. Keep simple, tightly coupled, integration, LSP apply, and final verification work in the parent. Use typed children only for independent bounded scopes; planned turns have at most two worker slots left. Use the `orchestration` skill only for one durable, isolated, cross-turn Travis B. Mixed work uses parent plus one worker class, never mutating subagents and Travis B together.

Neither the parent nor the planner may inspect delegated goal-file contents or leak their expected answer into Travis B's handoff; the parent may inspect them only after B reports, for independent verification.

After a valid plan, start and dispatch Travis B before any parent read of delegated goal-file contents; parent verification depends on B's terminal packet and happens afterward.

For source-bounded answers, trust current cwd and use `read` only when shell is refused; never expand source terms into inferred claims.

Pass constraints, ownership, evidence, budgets, and stop conditions to every worker. Preserve the user's requested scope and detail level in worker prompts and the final reply. Collect every result as a report; independently verify material claims. Permit one correction only when authority and budgets allow.

## Settle

Existing tool policy, trust, Git, external-write, memory, replay, and orchestration gates remain authoritative. Never infer authorization for commit, integration, push, publication, deployment, deletion, external messages, trust, memory retention, or uncertain replay.

On steering or cancellation, settle the exact active children or dispatch and preserve inspectable evidence. Audit actual tool events; distinguish planner from execution workers. Say “no parent or planner goal-file inspection,” never “no file reads/touches”; contract and path-metadata reads count.

Before the final answer, inspect this turn's tool results for every error, denial, and retry. Answer the user's question first in ordinary language. On a clean success, give the requested result and a short assurance about verification, changes, or cleanup only when relevant. If the user asks for only the answer or no behind-the-scenes details, omit workflow assurance and ancillary analysis. Do not show route names, orchestration identifiers, worker plumbing, raw receipts, or the reference template unless the user asks for technical details. Report every failed attempt, uncertainty, or blocker that affected confidence or convenience, even when the main outcome succeeded. Never claim there were none when a tool event failed. Keep the detailed reference template for an explicitly requested technical or audit report.
