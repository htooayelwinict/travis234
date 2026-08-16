# Travis234 Local Tmux Orchestration Skill Design

**Date:** 2026-08-16

**Status:** Approved; implementation plan complete, execution not started

## Goal

Add a built-in `orchestration` skill that lets a user-facing Travis234 session
coordinate other local Travis234 sessions through tmux. The primary workflow is
a supervised round trip:

1. Travis A receives the user's request.
2. Travis A creates an isolated Git worktree when isolation is useful.
3. Travis A starts Travis B in that worktree inside a durable tmux session.
4. Travis A sends a bounded handoff prompt to Travis B.
5. Travis B performs the assigned coding, research, data, review, or operational
   work.
6. Travis B commits verified code changes on its worktree branch when the task
   changes code.
7. Travis B deposits a structured handoff packet in durable local orchestration
   state.
8. Travis A retrieves the packet, reviews the evidence, and either answers the
   user, sends a focused correction back to Travis B, or performs an explicitly
   authorized integration.

The skill also supports a true full handoff. In that mode Travis A transfers
ownership to Travis B, confirms that the prompt was accepted, reports the
worktree and worker identifiers, and stops monitoring.

## Product Boundary

This capability is a built-in skill bundle, not a new core orchestration engine
inside Travis234.

- It does not use `spawn_subagent`, `wait_subagent`, or any other subagent tool.
- It does not depend on the existing `subagent-delegation` skill.
- It does not replace or mutate existing subagent tasks or results.
- It does not change the agent loop, iteration budget, steering order,
  follow-up behavior, or bounded parallel tool execution.
- It does not add a global system-prompt policy.
- It does not add a native TUI command or extension in the first version.
- It uses the existing `bash` and tmux capabilities plus Travis234's existing
  RPC transport.

The independent `subagent-delegation` skill needs a later modernization for the
new subagent role, routing, result, artifact, and lifecycle features. That work
has a separate design and test cycle.

## Terminology

### Coordinator

Travis A is the user-facing session. It owns the user's conversation, decides
whether to dispatch work, processes worker questions and results, and remains
responsible for the final answer.

### Worker

Travis B is a separate Travis234 process with its own conversation session. It
runs inside a named tmux session and works in either the coordinator's current
workspace or an explicitly created Git worktree.

### Supervised round trip

Travis A temporarily delegates a bounded task and waits for a structured result.
Travis A remains the owner of the overall request. Multiple focused A-to-B and
B-to-A exchanges are allowed within a strict round budget.

### Full handoff

Travis A transfers ownership and does not wait for completion. Starting a
worktree worker is not automatically a full handoff; the user's requested
ownership semantics decide the mode.

### Handoff packet

A durable, structured report from Travis B containing its outcome, concise
summary, evidence, changed files, commit, tests, artifacts, failed attempts,
blockers, questions, and recommended next action.

## User Experience

Users operate the feature in ordinary language. Examples include:

- "Create another Travis in a new worktree and have it implement this, then
  bring the result back."
- "Ask another Travis to research this while we continue here."
- "Let Travis B review this approach and send its findings back."
- "Keep sending the review and corrections between the two Travis sessions
  until they agree, with at most four exchanges."
- "Hand this entire task to a new worktree Travis and leave it running."

The explicit `/skill:orchestration` command remains available, but users do not
need to know the helper script or its command grammar.

When the task matches the skill, Travis A reads the skill, chooses supervised
round trip or full handoff, and invokes the bundled helper. Normal tool results
show receipts and handoff packets in the TUI.

## Packaged Skill Layout

The Python and npm distributions contain byte-equivalent copies:

```text
travis/resources/skills/orchestration/
  SKILL.md
  references/protocol.md
  scripts/orchestrate.py

packages/travis234-cli/skills/orchestration/
  SKILL.md
  references/protocol.md
  scripts/orchestrate.py
```

`SKILL.md` stays concise. It defines triggering conditions, mode selection,
ownership rules, the coordinator loop, limits, and when to read the protocol
reference.

`references/protocol.md` defines the handoff envelope, handoff packet, statuses,
recovery rules, and helper command recipes. It is loaded only when orchestration
is actually used.

`scripts/orchestrate.py` is the deterministic local control plane. It uses the
Python standard library, Git, tmux, and the installed Travis234 CLI. It exposes
machine-readable JSON and a version-matched `guide` or `--help` surface. The
skill resolves the script relative to its own `SKILL.md`; no separate global
binary or package is installed.

## Control-Plane Model

The helper persists five concepts.

### Run

A durable namespace for one user objective and its coordinator mailbox.

### Task

A bounded work item with an objective, ownership boundary, acceptance criteria,
dependencies, and status.

### Worker

A local Travis234 RPC process, its tmux identity, workspace, session identity,
and observed lifecycle state.

### Dispatch

One attempt to assign one task to one worker. Dispatch identity prevents an old
completion or question from being accepted for a newer attempt.

### Message

An append-only coordinator/worker delivery such as `question`, `reply`,
`status`, `handoff`, `failure`, or `heartbeat`. Deliveries are acknowledged
only after Travis A has processed them.

The first version supports one local coordinator, at most two active workers by
default, an explicit hard maximum of three, and no nested orchestration from a
worker unless the original user explicitly authorized that worker to become a
coordinator.

Every Dispatch receives an unguessable, dispatch-scoped capability. The worker
relay places it in the worker process environment and the completion helper
reads it from there; it is never placed in a prompt, command argument, receipt,
transcript, or database field in plaintext. Question, completion, failure, and
heartbeat mutations require this capability. A worker therefore cannot settle
another worker's Dispatch merely by learning its IDs.

## Durable State

The helper uses SQLite in the existing Travis234 application state:

```text
<get_agent_dir()>/orchestration/
  state.sqlite3
  sockets/<bounded-worker-hash>.sock
  runs/<run-id>/
    workers/<worker-id>/
      rpc.jsonl
      stderr.log
```

The default root is `~/.travis234/agent/orchestration`. Existing
`TRAVIS234_CODING_AGENT_DIR` behavior remains authoritative through
`get_agent_dir()`; the skill introduces no alternate state root or migration
alias.

The directory is owner-only and database/log files are private. SQLite uses
transactions, foreign keys, a busy timeout, and WAL mode. Requests have stable
IDs so a repeated coordinator command can return the existing receipt instead
of creating duplicate workers, tasks, messages, or commits.

Socket names use bounded hashes in a flat private directory to minimize Unix
socket path length. If the existing agent-state root still makes the encoded
path exceed the conservative platform limit, startup fails before tmux
mutation and directs the operator to the existing agent-directory override;
the helper does not invent another state or socket root. Worker transcripts are
size-bounded and rotated without deleting the final handoff or the bounded tail
needed for diagnosis.

The state store never records API keys, authorization headers, dotenv contents,
dispatch capabilities, or full process environments. An explicitly selected
dotenv path may be passed to a worker launch but is omitted from public receipts
and logs.

## Worker Transport

Each worker tmux session runs a small relay mode from the bundled helper. The
relay:

1. Starts `travis234 --mode rpc` in the selected workspace.
2. Owns the worker's RPC stdin and stdout.
3. Exposes a private local Unix-domain socket to helper control invocations.
4. Writes credential-safe RPC frames to a bounded transcript.
5. Serializes mutating prompts because one RPC turn owns the session at a time.
6. Keeps the worker process and its Travis234 session available across
   coordinator turns and coordinator restarts.

The coordinator never parses an interactive TUI screen to determine completion.
tmux supplies process durability; Travis RPC supplies structured prompt/result
transport; SQLite supplies coordination identity and recovery.

The worker relay is local Unix functionality because tmux is the required
backend. Remote workers, Windows-without-tmux support, and cross-machine routing
are outside the first version.

The relay and coordinator negotiate a protocol and state-schema version before
mutation. A newer helper may inspect incompatible state, but it fails closed
with an exact recovery message instead of upgrading or mutating a live worker
implicitly.

## Supervised Worktree Round Trip

### Start

Travis A resolves the Git repository and inspects current worktree state before
mutation. The helper:

- validates the requested branch, worktree name, and base;
- rejects a conflicting branch, path, or existing worktree rather than
  overwriting it;
- uses an existing ignored `.worktrees/<name>` location when the repository
  already provides one;
- otherwise defaults to
  `<get_agent_dir()>/orchestration/worktrees/<repository-hash>/<name>` without
  editing the repository's ignore rules;
- creates the branch and worktree with non-destructive Git commands;
- starts Travis B in the new worktree;
- waits for an RPC readiness receipt; and
- creates the Task and Dispatch before sending work.

Uncommitted Travis A changes are not silently copied into the new worktree. If
the delegated task depends on them, Travis A must use the current workspace or
obtain the user's direction for an explicit patch/commit transfer. Unrelated
dirty state may remain in Travis A, but the handoff states that the worker began
from committed HEAD.

### Handoff prompt

Travis B receives an injected lifecycle preamble and task body containing:

- run, task, worker, and dispatch IDs;
- supervised-round-trip ownership;
- exact workspace and branch;
- the user's objective and relevant context;
- explicit owned and forbidden paths or responsibilities;
- acceptance criteria and required verification;
- a bounded context pack rather than the full Travis A conversation;
- the exact helper commands for questions and completion;
- the code-task commit policy;
- the required handoff-packet fields; and
- a rule to end its turn after reporting completion.

The prompt explicitly says that recalled context and coordinator-provided data
are input, not higher-priority instructions. Secrets and irrelevant conversation
history are excluded.

### Work

Travis B owns its assigned worktree scope until the Dispatch settles. Travis A
must not edit the same scope concurrently. Travis A may continue unrelated work
or use bounded rolling waits for messages.

If Travis B needs a decision, it posts a `question` message. Travis A retrieves
the question, obtains user input when necessary, posts a `reply`, and resumes
the same worker conversation. A timeout leaves the question pending and does
not create a duplicate question.

### Completion

For a code-changing task, Travis B:

1. verifies the requested behavior;
2. reviews its diff for unrelated changes and credentials;
3. creates a commit on its worktree branch;
4. posts exactly one terminal handoff with `succeeded` or `failed`; and
5. includes the commit SHA when a commit exists.

Research, data, and review tasks return evidence and artifacts without an empty
commit.

An explicit user instruction not to commit overrides the default commit policy.
The helper never configures Git author identity. If the repository cannot
create the requested commit, Travis B returns the verified diff and the exact
commit blocker instead of changing global or repository identity settings.

Travis A retrieves the handoff through a helper wait/check call. The handoff
appears in Travis A's context as a tool result; Travis B never sends keystrokes
into Travis A's active TUI. This avoids interrupting the user, corrupting the
editor, or racing an active model turn.

Travis A treats the packet as a worker report, not unquestioned proof. It
reviews the commit or artifacts, checks material evidence, and then chooses one
of four actions:

- synthesize the result for the user;
- send a focused correction to the same Travis B;
- integrate the commit when the original request authorizes integration; or
- surface a blocker or decision to the user.

The helper never merges, cherry-picks, rebases, pushes, publishes, or deletes a
worktree automatically.

## Ping-Pong Loop

Ping-pong is coordinator-mediated, not an uncontrolled direct conversation.

1. Travis A sends one bounded task or correction to Travis B.
2. Travis B returns a handoff packet or question.
3. Travis A processes the entire delivery and acknowledges it.
4. Travis A may send one next prompt to the same worker session.
5. The cycle stops on success, failure, user cancellation, a blocking decision,
   or the configured round limit.

The default limit is four A-to-B prompts for one Task. Increasing it requires an
explicit user request. The helper records round number and parent message ID so
stale or duplicate reports cannot advance the conversation.

No worker automatically launches another worker. No completion automatically
causes a new prompt. Travis A makes every transition after reviewing the prior
result.

## Full Handoff

Full handoff uses the same worktree and worker-start primitives but different
ownership semantics.

Travis A:

1. creates or selects the worktree;
2. starts Travis B;
3. sends a complete ownership-transfer prompt;
4. confirms RPC acceptance;
5. reports the branch, worktree, worker, Travis session, and tmux identifiers;
   and
6. stops monitoring and editing the handed-off scope.

The prompt contains no `worker_done` obligation to a waiting coordinator.
Travis B may still preserve a final report in orchestration state for later
inspection, but Travis A does not wait for it unless the user changes the task
to supervised work.

## Helper Command Surface

The exact spelling is finalized during implementation planning, but the public
surface must cover these operations with JSON output:

- `guide`: print the version-matched protocol guide.
- `run-create`, `run-show`, and `run-list`.
- `task-create`, `task-show`, and `task-list`.
- `worker-start`, `worker-show`, `worker-list`, `worker-retain`, and
  `worker-release`.
- `dispatch-start`, `dispatch-show`, `dispatch-wait`, `dispatch-cancel`, and
  `dispatch-abandon`.
- `message-send`, `message-check`, `message-ack`, and `message-reply`.
- `worker-complete` and `worker-fail`, callable from the injected worker
  preamble.
- `recover`, which reconciles SQLite state, tmux sessions, Unix sockets, Git
  worktrees, and Travis RPC state without replaying work.

Every mutating command accepts or generates an idempotency key. Receipts include
schema version, created/reused effects, current status, opaque identifiers, and
safe next actions.

## Lifecycle And Recovery

- A missing tmux executable is a hard error. There is no silent process or
  subagent fallback.
- Orchestration requires both `bash` and `tmux` to be available to Travis A. The
  skill does not invoke native tmux through bash when the user or tool policy
  excluded the tmux capability.
- A worker launch does not bypass project trust. It reuses a verifiable existing
  trust decision or returns a blocked startup receipt; it never adds
  `--approve` merely to suppress a trust prompt.
- A startup timeout leaves an honest `starting` or `outcome_unknown` record.
  It does not automatically start a duplicate worker.
- If SQLite says a worker is active but tmux or its private socket is absent,
  reconciliation marks it `lost`.
- If tmux is alive but RPC readiness is uncertain, reconciliation reports
  `outcome_unknown` and preserves the worktree and transcript.
- A lost or uncertain Dispatch is never automatically replayed because the
  worker may already have changed files or committed.
- Reusing a worker requires it to be idle and the previous Dispatch to be
  settled and acknowledged.
- Cancellation stops only the exact supervised worker and marks the Dispatch.
  It does not delete the branch or worktree.
- Releasing a worker stops an idle owned tmux session after preserving its
  transcript. Retaining it records the user's intent to keep it live.
- Worktree and branch cleanup is a separate explicit destructive action and is
  outside automatic release.
- Coordinator restart recovery reopens the Run, replays unacknowledged
  deliveries, and reconnects to live worker relays.
- Host restart recovery can preserve SQLite, Git, sessions, and transcripts,
  but tmux workers are honestly marked lost because tmux does not survive a
  host restart.

## Limits And Safety

- Default active-worker limit: two.
- Hard active-worker limit: three.
- Default ping-pong prompt limit per Task: four.
- One active RPC mutation per worker.
- No recursive worker creation without explicit user authorization.
- No automatic merge, cherry-pick, rebase, push, publish, branch deletion, or
  worktree deletion.
- No raw environment or secret values in prompts, SQLite, transcripts, tool
  results, or test output.
- No dispatch capability in prompts, command arguments, receipts, or logs.
- No direct keystroke injection into Travis A.
- No blind trust in a worker's success claim.
- No overlapping write ownership between Travis A and Travis B.

## Failure Presentation

All failures are data rather than hidden retries. The TUI-facing receipt states:

- which Run, Task, Worker, and Dispatch were affected;
- the last confirmed lifecycle stage;
- whether the worker or worktree may still be live;
- whether files or commits may already exist;
- the bounded error;
- safe inspection or recovery actions; and
- actions that were deliberately not performed.

Provider errors, tool failures, test failures, and a worker-declared failed
outcome do not crash Travis A. They produce a failed or uncertain handoff for A
to evaluate.

## TUI Qualification Scenarios

Deterministic tests use fake providers and isolated temporary state before any
live model qualification.

### Scenario 1: lazy discovery

Start the TUI and make an unrelated request. Verify that the orchestration skill
metadata is discoverable but its body and protocol are not loaded.

### Scenario 2: natural-language worktree round trip

Ask Travis A to create a new-worktree Travis B for a bounded research task.
Verify skill loading, worktree creation, tmux/RPC readiness, prompt acceptance,
handoff delivery, acknowledgement, and an A response grounded in B's evidence.

### Scenario 3: verified code return

Assign a small code change to Travis B. Verify that B changes only its owned
scope, runs the requested tests, commits the result, and returns the branch,
commit, files, and test evidence. Verify no automatic integration.

### Scenario 4: question and reply

Make B encounter a real decision. Verify that one durable question reaches A,
the answer resumes the same worker, and a timeout/retry does not duplicate the
question.

### Scenario 5: bounded ping-pong correction

Have A send B a correction after reviewing the first handoff. Verify reuse of
the same worker conversation, ordered rounds, and termination at success or the
round limit.

### Scenario 6: full handoff

Ask A to transfer all ownership to a new-worktree B. Verify prompt acceptance,
identifier reporting, no completion wait, and no later monitoring by A.

### Scenario 7: coordinator restart

Start B, close and recreate A, reopen the Run, and verify that an
unacknowledged handoff is delivered exactly once after acknowledgement.

### Scenario 8: failure and cancellation

Exercise missing tmux, RPC startup failure, worker-declared failure, cancellation,
and a lost worker. Verify honest statuses, bounded diagnostics, retained Git
state, and no automatic replay or deletion.

### Scenario 9: two-worker bound

Start two independent workers and attempt to exceed the configured limit.
Verify bounded concurrency, disjoint ownership, and a clear rejection for the
extra worker.

### Scenario 10: live TUI prompt

After deterministic scenarios pass, run an installed-wheel TUI using the
operator-authorized dotenv without printing its contents. Ask for the primary
A-to-B worktree round trip and verify the visible receipts and final handoff.
Container smoke remains deferred until the complete approved feature set is
implemented.

## Automated Verification

Implementation uses red-green tests for behavior and skill tests for instruction
quality:

- metadata validation and lazy skill discovery;
- Python/npm skill-tree byte parity;
- wheel and npm package inclusion of the skill, reference, and helper;
- helper argument and JSON schema tests;
- SQLite transaction, idempotency, delivery, and restart tests;
- Git worktree safety tests in temporary repositories;
- fake tmux and fake RPC lifecycle tests;
- real installed-tmux start/send/wait/reconnect/release smoke;
- secret-redaction and private-permission tests;
- deterministic TUI scenario tests;
- installed-wheel live TUI qualification;
- full Python suite, npm launcher suite, and package builds before completion.

No implementation is complete based solely on a Markdown skill test or a mocked
tmux test.

## Code Blast Radius

Expected additions and focused updates:

- Add the Python and npm `orchestration` skill trees.
- Add focused orchestration helper and TUI tests.
- Update built-in-skill inventory and Python/npm parity expectations.
- Update package-data or npm file inclusion only if recursive skill resources
  are not already included.
- Document the user-facing capability after implementation.

Protected from this feature:

- `travis/agent/agent_loop.py`
- `travis/compaction/`
- provider streaming and model catalogs
- current subagent runtime, result packs, and existing subagent skill
- global system-prompt policy
- generic MCP behavior
- RPC concurrency semantics
- existing session JSONL semantics

If implementation reveals that a native TUI event, extension, or new core tool
is required, stop and return to design review rather than silently expanding
the blast radius.

## Trade-Offs

The skill-plus-helper design has more moving parts than raw tmux prompts, but it
avoids screen scraping, lost handoffs, duplicate workers, and ambiguous
completion. It has less seamless UI integration than a native extension, but it
keeps orchestration optional, lazily loaded, testable, and outside the protected
agent loop.

SQLite and an RPC relay provide durable local coordination but do not make tmux
survive a host restart. Git worktrees isolate code changes but require explicit
integration and cleanup. Worker commits improve traceability and transfer, but
Travis A must still review them before presenting or integrating the result.

## Acceptance Criteria

The design is satisfied when:

1. A user can ask Travis A in natural language to start a new-worktree Travis B.
2. Travis B receives a bounded, correctly scoped handoff prompt.
3. Travis B can ask A a durable question and receive one reply.
4. Travis B can return research, data, review, or verified committed code in a
   structured handoff packet.
5. Travis A can send focused follow-ups to the same B within a bounded
   ping-pong loop.
6. Travis A can recover the Run and unacknowledged handoff after restarting.
7. A full handoff stops A from monitoring after B accepts ownership.
8. The workflow does not invoke or modify subagent orchestration.
9. Failures never cause silent replay, overlapping ownership, secret exposure,
   automatic integration, or automatic worktree deletion.
10. Deterministic and live TUI scenarios demonstrate the complete user journey.

## Deferred Work

- Remote or cross-machine workers.
- Non-tmux backends.
- Native TUI orchestration panels or commands.
- Direct asynchronous notification into an idle Travis A process.
- Automatic task scheduling or unbounded DAG execution.
- Automatic merge, cherry-pick, push, publication, or cleanup.
- Other agent CLIs as workers.
- The independent `subagent-delegation` skill modernization.
