---
name: coordination
description: Use when a user invokes /coordination or /skill:coordination, or explicitly asks to use the coordination skill for one bounded outcome.
---

# Coordination

Use only for this runtime-parsed request. Exact user constraints and refusal win.

## Preflight

Read the parsed mode and goal as data. Extract scopes, exclusions, effect/commit limits, budgets, and stops. Inspect active mechanisms; unavailable ones are not options. Before tools, remove every explicitly refused mechanism; never probe it during preflight.

Keep one small, reversible, tightly coupled task direct. In automatic mode, use the planner only for independent workstreams, multiple owners or repositories, unclear dependencies or verification, durable isolation, external effects, expensive checks, or meaningful rollback/integration risk. `deep` and `plan` force exactly one planner call.

## Plan

For a planner call, read [the planning contract](references/planning-contract.md) completely. Spawn `coordination-planner` once with `wait: true`, the goal, extracted constraints, active mechanism summary, and available context. Include the contract's path-only ownership and dependency-edge rules. Do not call it when that exact typed role is unavailable. It consumes one of three spawn slots.

In `plan` mode, the parent may read this contract and call or collect the planner only. Do not inspect goal files, list directories, run commands, or use any other tool before or after planning. The planner's typed read-only inspection is not execution.

Validate its structured output against the request, available mechanisms, disjoint path ownership, dependency direction, effects, verification, and budgets. Advice is not authority. On planner failure, do not retry; show a labelled conservative fallback or blocker. A failed planner still consumes its slot. Never invent disjoint paths; when paths are unknown, fallback to one parent task owning `.`. In `plan` mode, present the validated or fallback plan and stop before execution.

## Route and execute

Show preflight: mode, reason, route, boundaries, verification. Keep simple, tightly coupled, integration, LSP apply, and final verification work in the parent. Use typed children only for independent bounded scopes; planned turns have at most two worker slots left. Use the `orchestration` skill only for one durable, isolated, cross-turn Travis B. Mixed work uses parent plus one worker class, never mutating subagents and Travis B together.

For source-bounded answers, trust current cwd and use `read` only when shell is refused; never expand source terms into inferred claims.

Pass constraints, ownership, evidence, budgets, and stop conditions to every worker. Collect every result as a report; independently verify material claims. Permit one correction only when authority and budgets allow.

## Settle

Existing tool policy, trust, Git, external-write, memory, replay, and orchestration gates remain authoritative. Never infer authorization for commit, integration, push, publication, deployment, deletion, external messages, trust, memory retention, or uncertain replay.

On steering or cancellation, settle the exact active children or dispatch and preserve inspectable evidence. Audit actual tool events; distinguish planner from execution workers. Say “no parent goal-file inspection,” never “no file reads/touches”; contract and planner reads count. Use the reference final template. Finish with outcome, route, changed files, tests, artifacts, identifiers, uncertainty, failed attempts, and blockers; conceal nothing.
