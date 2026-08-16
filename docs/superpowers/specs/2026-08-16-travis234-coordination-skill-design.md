# Travis234 Coordination Skill Design

**Date:** 2026-08-16

**Status:** Approved design; written specification awaiting user review

**Base:** `codex/combined-parity-orchestration`

## Executive decision

Add an opt-in built-in `coordination` skill that lets a user provide one outcome
without naming Travis234's internal mechanisms. For that request only, Travis A
may select direct execution, typed in-process subagents, or one independent
tmux/worktree Travis B. It may use LSP, MCP, durable artifacts, operation
inspection, and memory within their existing contracts.

Complex, ambiguous, risky, or parallelizable requests receive one bounded
second-opinion planning call through a packaged read-only typed role named
`coordination-planner`. Simple work bypasses the extra call. Travis A validates
every plan and remains the sole coordinator; planner output is advice, never
authority.

The feature is invoked explicitly and never becomes an always-on system-prompt
mode. It adds no new authority for writes, network effects, Git integration,
publishing, deletion, replay, trust, or memory retention.

## Goals

- Let non-expert users access the combined parity and orchestration stack with
  one named command and a natural-language outcome.
- Improve route selection and decomposition for complex work with one bounded
  planning-model call.
- Keep direct work direct and avoid planner latency on simple requests.
- Preserve existing ownership boundaries, tool policy, subagent ceilings,
  operation uncertainty, and orchestration recovery semantics.
- Show a short inspectable route, scope, and verification contract without
  exposing hidden chain-of-thought.
- Degrade safely when planning, delegation, MCP, LSP, tmux, or a configured
  model role is unavailable.

## Non-goals

- No always-on autonomous mode or silent coordination trigger.
- No workflow scheduler, DAG runtime, recurring automation, or durable replay
  engine.
- No second raw provider path inside `agent_loop.py`.
- No automatic merge, commit, push, publication, deployment, branch deletion,
  worktree deletion, trust grant, external message, or memory retention.
- No nested subagents and no substitution between in-process subagents and an
  explicitly required Travis B.
- No direct port of Oh My Pi's file-edit implementation. Travis keeps its
  existing Pi-lineage exact-replacement edit tool and the reviewed LSP
  preview/apply contract already added for semantic multi-file changes.

## User experience

The convenience command is:

```text
/coordination <goal>
```

The canonical resource command remains:

```text
/skill:coordination <goal>
```

Supported modes are:

```text
/coordination <goal>         automatic complexity decision, then execution
/coordination --deep <goal>  force one planner call, then execution
/coordination --plan <goal>  force planning, show the plan, and stop
```

`--plan` implies the planning call. Combining `--plan` and `--deep` is allowed
and has the same behavior as `--plan`; it is not an error. Arguments after the
mode flag remain the user's exact goal. Mode flags are recognized only at the
start of the command; `--` ends mode parsing. An empty goal or an unknown
leading coordination flag is rejected before any model or tool call.

The alias is registered only when the effective `coordination` skill is loaded,
model-invocable, and skill commands are enabled. Disabling skills or skill
commands makes the alias unavailable; it must not bypass resource trust or
collision rules. `/skill:coordination` continues to use the generic skill
command path and is likewise unavailable when skill commands are disabled.

Before execution, the TUI shows a compact summary such as:

```text
Coordination preflight
  Mode: planned
  Reason: three independent workstreams and shared integration risk
  Route: parent integrator plus two typed workers
  Boundaries: no commit, push, publication, or memory retention
  Verification: focused tests plus the affected repository suite
```

Phase updates use ordinary model/tool events rather than a new coordinator
state machine:

```text
[planning]   one bounded planner call
[executing]  selected workers and parent work
[verifying]  independent evidence and scope checks
[complete]   outcome, changed files, tests, artifacts, and blockers
```

Worker, dispatch, artifact, and operation identifiers remain available in
normal tool output, `/agents`, orchestration records, and the final evidence
report. The default summary does not require the user to understand them.

## Architecture

```text
/coordination [--deep | --plan] <goal>
                         |
                         v
               coordination/SKILL.md
                         |
                         v
                  bounded preflight
       user constraints + active tools/roles + trust/policy
                         |
                         v
                rule-based complexity gate
                 /                       \
          simple/reversible       complex/ambiguous/risky
                 |                       |
                 |                       v
                 |           coordination-planner child
                 |           read-only reviewer role
                 |           strict structured result
                 |                       |
                 +-----------+-----------+
                             |
                     Travis A validation
                             |
          +------------------+------------------+
          |                  |                  |
       direct         typed subagents     one Travis B
          |                  |             tmux/worktree
          +------------------+------------------+
                             |
                 policy gates and supervision
                             |
                  independent verification
                             |
              success / bounded correction / blocker
```

The trigger list is rule-based and explicit in the skill. The mode flags are
deterministic. Automatic classification of a natural-language goal remains a
model judgment; the design does not falsely claim a deterministic workflow
compiler. Prompt-contract tests and live scenarios qualify that judgment.

### New packaged resources

```text
travis/resources/
├── skills/
│   └── coordination/
│       ├── SKILL.md
│       └── references/
│           └── planning-contract.md
└── roles/
    └── coordination-planner.json
```

The npm launcher mirrors the skill and role under its package resources. The
Python package-data contract includes the role JSON. Default resource discovery
adds the packaged role directory at the same built-in precedence class as
packaged skills; normal trusted resource collision behavior remains intact.

The `coordination-planner` role is supporting configuration, not another
user-facing skill. Its default contract is:

```text
modelRole: reviewer
allowedTools: read, grep, find, ls
allowedEffects: read
canSpawn: false
maxDepth: 1
defaultTimeoutSeconds: 120
artifactPolicy: none
```

Its result schema requires:

```text
route              direct | subagents | travis-b | mixed
rationale          concise decision summary, not hidden reasoning
tasks              1..6 bounded tasks
dependencies       explicit task edges
ownership          disjoint paths or read-only scopes
risks              bounded list
approvalGates      effects that require user or policy approval
verification       observable evidence for completion
stopConditions     success, failure, cancellation, and blocker boundaries
```

The schema forbids unknown fields and bounds every collection and string. The
standard typed-result envelope validates the plan; no planner artifact is
promoted.

### Existing owners reused

- `skills.py` and the resource loader discover and inject the selected skill.
- The capability registry preserves origin, trust, precedence, and reload
  behavior for the skill and role.
- `SessionSubagentController` and the existing supervisor perform the planner
  call and any in-process delegation.
- `ModelRoleRouter` resolves the planner through the reviewer route and current
  credentials.
- The existing `orchestration` skill and helper exclusively own Travis B,
  tmux/worktree lifecycle, messages, recovery, and correction dispatches.
- Tool policy owns read, write, execute, and network approvals.
- Durable artifacts own large evidence; the operation journal remains
  observe-only and never schedules or replays the workflow.
- LSP remains a parent-visible bounded tool. MCP remains the optional one-proxy
  adapter. Memory recall remains untrusted data and retention remains explicit.

The ordered agent loop, provider transports, compaction, JSONL session format,
and iteration budgeting do not gain coordination semantics.

## Preflight and planner selection

Travis A first extracts exact user constraints, including named paths, excluded
mechanisms, commit policy, external-effect limits, time or cost limits, and stop
conditions. Explicit refusal always wins, including `no subagents`, `no MCP`,
`no network`, `no commit`, and `local only`.

Automatic mode invokes the planner when any material signal is present:

- two or more plausible independent workstreams;
- multiple subsystems, repositories, or ownership domains;
- unclear dependencies, scope, mechanism, or verification strategy;
- a durable, isolated, cross-turn, or new-worktree requirement;
- relevant external/MCP effects;
- expensive or long-running verification;
- meaningful rollback, integration, or partial-failure risk.

One small sequential or tightly coupled task bypasses the planner. `--deep` and
`--plan` force it. The planner receives the exact goal, extracted constraints,
active mechanism summary, and bounded project context. It does not receive
credentials, recalled memory as authority, or permission to execute.

There is at most one planner call and no planner retry. Timeout, cancellation,
missing role/model, or invalid structured output produces a clearly labelled
fallback. Travis A either creates a conservative direct plan inside the
original authority or reports that safe coordination is unavailable. In
`--plan` mode it shows the fallback plan and stops.

## Plan validation

Travis A checks the structured plan before acting:

- every task fits the original goal and constraints;
- requested tools, roles, MCP, LSP, and tmux are actually available;
- mutating ownership does not overlap;
- dependencies are acyclic and bounded;
- no task obtains more authority than the parent;
- verification is observable rather than prose-only;
- approval gates cover material effects;
- the route respects worker, prompt, time, and effect budgets.

An invalid or unsafe recommendation is never partially executed. Travis A may
narrow it without another LLM call. Scope expansion or a materially different
goal requires the user.

## Execution routing

### Direct

Use the parent for small, sequential, tightly coupled, integration, or final
verification work. LSP semantic inspection and preview/apply remain parent
owned because LSP is not in the child tool catalog.

### Typed in-process subagents

Use children only for independent bounded scopes with exact roles, ownership,
evidence, budgets, and stop conditions. The planner is an ordinary supervised
child and consumes one of the existing three model-spawn slots for that turn.
Consequently a planned request may start at most two additional in-process
workers; a direct-plan request may use all three. This tradeoff preserves the
authoritative supervisor ceiling and avoids an unaccounted provider call.

Workers cannot spawn children. Travis A collects every result and independently
verifies material claims. A correction may steer an active child or use a
remaining spawn slot once; it never creates an unbounded new wave.

### Independent Travis B

Use one Travis B for durable cross-turn work, a separate worktree, long-lived
tmux execution, or full handoff. The coordination skill reads and follows the
existing orchestration protocol only after selecting this route. It never sends
raw tmux keystrokes, screen-scrapes B, bypasses trust, or automatically
integrates B's work.

Question/reply, terminal handoff, acknowledgement, correction, recovery,
retention, and release keep their existing protocol semantics. A correction is
parent-linked and bounded. Branch or worktree cleanup remains a separate
verified action, never an implicit result of coordination.

### Mixed route

In the first release, mixed means parent work plus exactly one worker class:
either in-process subagents or one Travis B. It does not run in-process editing
children and a mutating Travis B concurrently. Every mutating scope must be
disjoint; final integration and verification stay with Travis A.

## Authorization and safety

Invoking the skill authorizes one bounded coordination lifecycle for the named
goal: preflight, an optional planner, route selection, supervision, evidence
collection, and a bounded correction when remaining budgets permit.

It does not itself authorize any material tool effect. Existing policy still
decides whether write, execute, and network effects are automatically allowed,
denied, or presented for approval. The skill cannot grant trust or change
policy configuration.

The following remain explicit user or separate workflow decisions:

- commit, merge, cherry-pick, push, PR creation, publication, and deployment;
- destructive Git operations, branch deletion, and worktree deletion;
- external messages or writes;
- credential use outside existing provider/tool resolution;
- memory retention;
- replay of an uncertain or unsafe operation.

Recalled memory and MCP resources/prompts are untrusted data. They may inform a
plan only after Travis A validates them against the current request and project
evidence. They never expand authority or override user/project instructions.

## Failure and cancellation behavior

```text
planner unavailable, timed out, cancelled, or invalid
  -> no retry
  -> conservative labelled fallback or blocker

worker question answerable from exact user intent
  -> answer through the owning supervision protocol

worker question requiring new authority or a material choice
  -> pause and ask the user

worker success
  -> treat as a report and independently verify

worker failure
  -> one bounded correction only if scope and budgets permit
  -> otherwise report the blocker

conflicting evidence or uncertain effect
  -> do not integrate or replay
  -> identify uncertainty and stop

user steering or cancellation
  -> steer/cancel active in-process children
  -> cancel or safely settle the exact orchestration dispatch
  -> preserve inspectable evidence and state
```

The feature creates no new workspace plan files and no new durable coordinator
database. Conversation events, typed results, artifacts, supervisor snapshots,
operation inspection, and orchestration records remain the authoritative
evidence owners.

## Prompt and skill alignment

The global system prompt does not embed the coordination workflow. The new
skill's compact name and description make it discoverable, while explicit
selection injects the full contract for only that request.

Both packaged copies of `subagent-delegation/SKILL.md` receive a concise note
that an explicit coordination invocation authorizes its bounded planner and
execution lifecycle while preserving runtime spawn ceilings and explicit
refusal. Both orchestration skill copies receive a concise note that
coordination may select orchestration but cannot weaken or substitute its
protocol. These are routing clarifications, not merged skill semantics.

No tool prompt needs coordination-specific prose beyond exposing the effective
read-only typed planner role through the existing bounded role guidance.

## Test strategy

Every implementation bug follows regression-first repository policy.

### Resource and packaging contracts

- Discover the built-in skill and planner role with correct source/trust data.
- Prove Python and npm skill/role mirrors match byte-for-byte.
- Prove the wheel, sdist, and npm tarball contain complete resources.
- Prove `/coordination` resolves the effective skill and becomes unavailable
  when skills or skill commands are disabled.
- Prove `--deep`, `--plan`, and the exact goal survive command injection.
- Preserve user-resource precedence and all unrelated `~/.travis234` state.

### Planner safety and schema

- Freeze the planner to reviewer, read-only effects, the four read tools, no
  spawning, no artifacts, and a 120-second maximum.
- Accept a bounded valid plan and reject unknown routes, excessive tasks,
  cycles, overlaps, missing evidence, unknown fields, and oversized strings.
- Prove timeout, cancellation, missing model role, and invalid JSON terminate
  cleanly without execution or retry.
- Prove the planner consumes the existing per-turn spawn budget.

### Routing and authorization

- Simple work bypasses the planner; automatic complex work invokes it once.
- `--deep` invokes it once; `--plan` performs no execution.
- Tightly coupled work stays direct, independent work uses typed children, and
  durable isolated work selects the orchestration protocol.
- Mixed execution rejects overlapping ownership and simultaneous mutating
  subagents plus Travis B.
- Explicit `no subagents`, `no MCP`, `no network`, `no commit`, and `local only`
  constraints override recommendations and propagate to every worker.
- Policy denial stops the affected action. No path silently pushes, publishes,
  integrates, deletes, retains memory, grants trust, or replays uncertainty.

### Recovery and supervision

- Planner failure produces one labelled conservative fallback.
- Child summaries remain reports; parent verification catches false success.
- Steering, cancellation, terminal failure, one correction, and exhausted
  budgets settle visibly.
- Travis B recovery reconnects the exact worker/session and never replays or
  integrates automatically.

### Native TUI scenario matrix

1. novice one-command request;
2. simple direct route with no planner;
3. automatic planned route;
4. forced `--deep` route;
5. `--plan` with no execution;
6. typed parallel workers;
7. independent Travis B worktree;
8. policy approval denial;
9. planner timeout or invalid result;
10. bounded worker correction;
11. user steering and cancellation;
12. final evidence synthesis with no concealed failure.

Live qualification uses a configured real model in the native background TUI,
records pass/fail after each prompt, and fixes product defects regression-first.
Focused tests are followed by the complete Python suite, npm launcher tests,
adapter tests, package builds, clean installs, and relevant container smoke only
after the implementation is otherwise complete.

## Success criteria

- A user needs only an outcome and `/coordination`; no tool, schema, worker,
  tmux, or worktree knowledge is required.
- Simple work incurs no planner call. Complex or forced work incurs exactly one.
- The planner is structurally read-only and cannot execute or delegate.
- Every route is visible, bounded, policy-controlled, and independently
  verified.
- Explicit constraints reach all workers and override planner advice.
- The feature adds no coordination semantics to the agent loop, provider
  transports, compaction, operation replay, or session persistence.
- Disabling the skill removes the behavior without altering normal turns.

## Tradeoffs

The design accepts one extra model call and additional latency for work that
benefits from decomposition. The planner can still recommend a poor route, so
Travis A must validate rather than obey it. Using the supervised child pathway
costs one spawn slot but preserves cancellation, budgets, usage accounting, and
auditable results. A dedicated planner provider pathway would recover that slot
at substantially greater architectural and reliability cost.

Prompt-level automatic classification cannot be proven solely by unit tests;
live-model scenarios remain necessary. The design therefore keeps deterministic
mode flags, strict planner schemas, structural effect ceilings, and explicit
fallbacks around the model-judgment boundary.

This is deliberately an opt-in coordination policy, not a natural-language
workflow runtime. If future evidence justifies recurring jobs, persisted DAGs,
or multi-tenant scheduling, those belong in a separate reviewed subsystem.

## Rollout boundary

Implementation should proceed from the plan-only safe surface to execution:

1. package and validate the skill and planner role;
2. register `/coordination` and qualify `--plan`/`--deep` behavior;
3. enable direct and typed-subagent routing under existing policy;
4. add orchestration selection without changing its protocol;
5. run the complete native-TUI and repository qualification matrix.

Completion means the local implementation, documentation, packaged resources,
and tests satisfy this specification. It does not authorize merging, pushing,
publishing, deployment, version changes, or container promotion.
