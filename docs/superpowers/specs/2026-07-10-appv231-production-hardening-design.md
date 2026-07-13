# appv231 Production Hardening Design

Date: 2026-07-10
Status: Approved for implementation planning after delegated self-review
Supersedes: `2026-07-09-appv231-agent-loop-pi-parity-design.md` for future hardening work

## Goal

Make appv231 a reliable coding agent while preserving `appv231.agent` as a pure,
provider-agnostic Pi-style runtime that can host coding, research, workflow, or
other external profiles. Repair the proven cross-layer defects without changing
the mature compaction implementation.

## Constraints

- `appV2.3.1/appv231/agent/` owns generic agent runtime mechanics only.
- Coding behavior belongs under `appV2.3.1/appv231/coding_agent/`.
- Do not edit `appV2.3.1/appv231/compaction/`.
- Preserve existing CLI behavior and readability of existing session files.
- Internal policy imports may change atomically; do not leave compatibility
  shims inside the core package.
- Every repair begins with a regression test that proves the current fault.
- Do not perform mutating git operations: no commit, push, branch, tag, release,
  or publish. Read-only status and diff checks are permitted for verification.

## Scope

This design covers:

1. agent-loop lifecycle, concurrency, callbacks, and event delivery
2. coding-agent policy, tools, output handling, and workspace enforcement
3. compaction and session integration outside the compaction redzone
4. provider, model, authentication, and tool-schema validation
5. session persistence and TUI concurrency
6. coding-agent evaluation and release verification

It does not redesign the compaction algorithms or the TUI visual language.

## Proven Problems

The design addresses these reproduced defects:

- `Agent.reset()` can release the visible streaming flag while a run remains
  active, allowing overlapping prompts.
- Async listeners, hooks, and extension tools are not awaited correctly.
- Parallel tool workers call updates and stateful after-hooks from worker
  threads, with an unbounded worker count.
- Low-level loop wrappers can hide exceptions behind a success-shaped
  `agent_end`.
- Core behavior parses guardrail JSON to recognize coding policy.
- Tool output truncation retains full output in memory, writes an incomplete
  artifact, mishandles invalid UTF-8, and creates a broadly readable file.
- Relative bash paths, executable aliases, and substring authorization bypass
  workspace and package-installation policy.
- Persisted compaction summaries are not recognized by the compressor during a
  later compaction, so rolling context is lost.
- Manual compaction can race an active model turn.
- Model, provider, and authentication state have several competing authorities.
- Saved defaults and model cycling can select unauthenticated models.
- Provider unregister, OAuth refresh, direct Anthropic authentication,
  cancellation, and advertised transport support have contract defects.
- Tool argument validation accepts unsupported JSON Schema constraints.
- Concurrent TUI rendering is possible, and remote model loading blocks the UI
  caller.
- A partial final session-log line prevents recovery of the whole session.
- The test suite measures implementation behavior but not coding-task quality.
- The image release workflow can push without a test and installed-runtime gate.

## Selected Approach

Use a staged hard boundary. Remove domain policy from core immediately when its
callers are migrated, then repair behavior through independently testable
components. Do not perform a big-bang rewrite and do not retain a hybrid core as
a long-term compatibility layer.

## Architecture

```text
TUI / CLI
    |
CodingApp
    |-- ProviderControlPlane
    |-- SessionStore
    |-- UiDispatcher
    `-- AgentSession (coding profile)
            |-- Coding policies
            |-- Coding tools
            |-- CompactionBoundaryAdapter
            |-- ExecutionBackend
            `-- appv231.agent (pure runtime)
```

Dependency direction is one-way: the coding profile imports the core; the core
never imports coding-agent, TUI, session, provider-catalog, or compaction policy.

## 1. Pure Core Runtime

### Boundary

`appv231.agent` contains only:

- `Agent` and loop execution
- message, event, tool, and callback types
- steering and follow-up queues
- cancellation and run lifecycle
- generic stream coordination

The following changes establish the boundary:

- Delete unused `agent/tool_dispatch.py`.
- Move `agent/tool_guardrails.py` to
  `coding_agent/policies/tool_guardrails.py`.
- Keep the iteration counter generic, but move the forced final-summary prompt
  to a coding-agent continuation policy.
- Remove core inspection of guardrail result JSON.

An import-boundary test must fail if core imports `coding_agent`, `tui`,
`compaction`, or named coding tools.

### Run Lease

Use an active-run lease independent of mutable presentation state.

- Only one prompt or continuation may hold the lease.
- `reset()` cannot release the lease.
- Reset during a run either rejects or follows the explicit abort-and-wait path.
- Abort is idempotent.
- Idle is signaled only when the lease owner finishes cleanup.

### Awaitable Runtime

Use one canonical awaitable invocation path for providers, tools, hooks, and
listeners. Sync callables are adapted into that path; awaitables are awaited.
Existing synchronous entry points remain thin facades and do not implement a
second loop.

Listeners run sequentially in subscription order. A listener or hook failure is
reported through the run failure contract rather than becoming an unawaited
coroutine warning.

### Event Coordinator

One coordinator owns state reduction, hooks, and event emission.

- Async tools run on the coordinator runtime.
- Sync tool bodies may run in a bounded worker pool.
- Worker threads return raw execution outcomes only.
- Updates, after-hooks, result finalization, and events run on the coordinator.
- Parallel result messages remain in assistant source order.
- Concurrency has a configured upper bound independent of model output size.

### Tool Outcome Contract

- Unknown, invalid, or preflight-blocked calls produce typed immediate outcomes.
- Immediate outcomes bypass `after_tool_call`, matching Pi semantics.
- A successfully invoked tool calls `after_tool_call` exactly once, including
  ordinary tool errors returned by that invocation.
- Coding policy records its own block metadata before returning the typed block.
- Core never infers policy from result text or JSON.

### Stream Termination

Every low-level stream terminates as exactly one of:

- success
- aborted
- failed with an exception

Unexpected exceptions fail the stream and remain visible to high-level `Agent`
handling. A failure must not be encoded as a successful `agent_end` containing
partial messages.

## 2. Coding Policy and Tools

### Policy Pipeline

Coding policies receive an immutable `CodingTurnContext` and return one of:

- `Allow`
- `Block(code, reason, metadata)`
- `RequireConsent(capability, reason)`

Policies remain independently testable and include workspace scope, package
mutation consent, loop progress, and optional duplicate-call handling.

Package installation is authorized by an explicit per-turn capability. Prompt
substring matching may offer a UI hint but is not an authorization mechanism.

### Workspace Capability

File tools resolve all paths through a canonical capability object:

```text
WorkspaceCapability.resolve(path, access=read|write|execute)
```

Resolution normalizes relative paths, resolves symlinks where required, and
checks exact path ancestry rather than string containment.

Bash parsing cannot soundly enforce filesystem isolation. Bash therefore runs
through an `ExecutionBackend` that declares one of two honest modes:

- `sandboxed`: operating-system isolation exposes only approved writable mounts
- `trusted`: no filesystem containment claim; policy checks are advisory

Command classification remains useful for consent and diagnostics but is not a
security boundary.

### Artifact Capability

Generated output artifacts are registered with the session as exact read-only
paths. Read access is granted only to registered artifacts and does not create a
general temporary-directory bypass.

### Bounded Output Spool

Replace the current accumulator with a spool that:

1. creates a mode-`0600` session artifact before output begins
2. streams each raw byte to the artifact exactly once
3. keeps only a configured decoded tail in memory
4. decodes invalid UTF-8 with replacement characters
5. continues appending after snapshots
6. returns tail, truncation metadata, and artifact reference
7. flushes, closes, and cleans up under session lifecycle ownership

Memory use must remain bounded as command output grows.

## 3. Compaction Integration

No file under `appv231/compaction/` changes.

### Boundary Adapter

Add `CompactionBoundaryAdapter` in `coding_agent`.

- Before compression, convert persisted `compactionSummary` records into the
  native summary envelope already recognized by the compressor.
- Preserve stable boundary identifiers and avoid duplicating summaries.
- After compression, convert the new summary back into the persisted session
  representation exactly once.
- Support old session files without rewriting them eagerly.

The primary invariant is:

> After any number of save, reload, and compact cycles, the newest summary
> incorporates all prior compacted context.

A two-compaction rehydration regression is the minimum proof; a repeated-cycle
property test provides stronger coverage.

### Compaction Coordinator

Manual and automatic compaction use one coordinator:

1. acquire the session operation lease
2. abort and await an active run, or defer according to the caller contract
3. snapshot stable history
4. invoke the boundary adapter and compressor
5. persist the result
6. publish completion state

Compaction invoked from the active run owner must defer rather than wait on
itself.

Output-cap recovery uses a session-local model override or immutable model copy;
it never mutates the shared catalog model.

## 4. Provider Control Plane

`CodingApp` owns one injected `ProviderControlPlane` containing:

- `AuthStorage`
- `ProviderRegistry`
- `ModelRegistry`
- `ProviderCapabilityCatalog`

AgentSession, CLI, and TUI use the same instance. Legacy global access, if still
needed internally during migration, delegates to this instance and is removed
before completion.

### Registry Ownership

- Refresh builds an immutable snapshot instead of wrapping prior resolvers.
- Extension registration returns a source-scoped `ProviderRegistration` handle.
- Closing the handle removes exactly that source's API transport, models, and
  auth configuration.
- Provider unload is idempotent.

### Model Eligibility

One resolver serves startup, saved defaults, cycling, and the TUI picker.

A selectable model requires:

```text
implemented transport + valid catalog entry + provider authentication requirement satisfied
```

An unavailable active model may remain visible for diagnosis but cannot be
selected as the next model.

### Provider Capability Contract

Every advertised provider profile declares:

- endpoint and API mode
- authentication strategy and environment aliases
- required headers
- request encoder and stream decoder
- supported generation parameters
- cancellation, timeout, retry, and transport capabilities

Catalog exposure is conditional on an implemented transport. Direct Anthropic
uses its required API-key and version headers. Unsupported Bedrock modes remain
hidden until their contract tests pass.

Abort closes the active HTTP stream and joins or terminates its worker within a
bounded interval. Public timeout, transport, and retry settings must either
control runtime behavior or be removed.

### Authentication

- OAuth refresh is serialized per provider.
- Refreshed credentials persist atomically.
- Malformed auth files fail visibly and do not allow memory-only success.
- CLI and TUI never edit credential JSON independently.
- Environment aliases come from provider profiles as the single source.
- Secret values never appear in diagnostics or test transcripts.

### Tool Schema Validation

Use a maintained JSON Schema implementation rather than extending the manual
subset.

1. Validate each tool schema at registration.
2. Apply the documented coercion layer.
3. Validate arguments against the complete schema.
4. Return bounded structured errors to the model.

Coverage includes `enum`, `const`, patterns, numeric bounds, arrays, unions, and
nested `additionalProperties`.

## 5. Session and TUI

### Session Log

`SessionStore` is a single-writer append log.

- A process/session lock serializes writers.
- Serialize and append before updating the in-memory projection.
- Flush complete entries and durably sync explicit checkpoints such as turn
  completion and compaction.
- Recover only a truncated final record and preserve its bytes for diagnosis.
- Treat corruption before the final record as a hard visible error.
- Use temporary-file plus atomic replacement for rewrites.

### UI Dispatcher

One UI-owner context mutates components and renders.

- Input, model, tool, timer, and file-watch producers enqueue typed UI events.
- Render requests coalesce to a bounded cadence such as 16 ms.
- Terminal transitions may request a forced render.
- `/compact`, model changes, and session changes enqueue serialized session
  commands instead of mutating state from input threads.
- Remote model discovery is asynchronous, cancellable, and cached, with explicit
  loading and failure states.

The existing component hierarchy and visual style remain unchanged.

## 6. Error and Diagnostic Contract

- Cancellation propagates through provider, tool, loop, session, and TUI layers.
- Provider failures explicitly fail streams.
- Tool invocation failures return bounded error results.
- Infrastructure failures terminate the run.
- Persistence failures leave the in-memory projection unchanged.
- Policy blocks retain structured codes without embedding control data in user
  text.
- Diagnostics carry run, turn, tool-call, provider, and session identifiers.
- Credentials and sensitive raw output are redacted.

## 7. Quality and Release Verification

### Deterministic Integration Suite

Add fault-injection coverage for:

- active-run reset and compaction races
- sync and async hooks, listeners, providers, and tools
- parallel completion order and event-thread ownership
- stream cancellation and unexpected provider failure
- bounded output and artifact lifecycle
- workspace and consent policy decisions
- repeated persisted compactions
- provider request/auth/cancellation contracts
- malformed and partial session logs
- concurrent TUI event producers

### Live Coding Evaluation

Run 21 isolated complex SDLC scenarios through the actual TUI entry point and
OpenRouter provider.

- Use a clean demo directory for every scenario.
- Fix model, thinking level, temperature, tool profile, and evaluation rubric.
- Trigger `/compact` at scheduled intervals in long scenarios.
- Score task tests, resulting files, tool failures, policy false positives and
  negatives, requirement retention, tokens, cost, and latency.
- Use real credentials from environment configuration without recording them.

Mocks remain appropriate for deterministic protocol tests but cannot substitute
for this live quality evaluation.

### Release Gate

Before an image is eligible to push:

- full Python test suite passes
- provider contract tests pass
- image builds without cache
- installed CLI starts as the non-root `appv231` user
- Node and npm are available to that user
- one bounded deterministic TUI workflow succeeds inside the image

This design defines the gate but does not publish or perform git operations.

## Delivery Sequence

### Stage 1: Characterization

Convert every proven reproduction into a regression test and add architecture
boundary tests.

### Stage 2: Production Data Safety

Establish the independent run lease, then repair output spooling, compaction
adaptation, active-turn compaction, and final session-record recovery.

### Stage 3: Core Purification

Move policies, remove dead dispatch, extract summary behavior, support
awaitables, serialize events, bound tool concurrency, and expose stream failures.

### Stage 4: Coding Policy Enforcement

Introduce typed policy decisions, workspace and artifact capabilities, explicit
consent, and honest execution backends.

### Stage 5: Provider Control Plane

Unify registry and auth ownership, enforce registration handles, repair provider
contracts and cancellation, filter unsupported transports, and replace schema
validation.

### Stage 6: Session and TUI Concurrency

Complete single-writer persistence, introduce the UI dispatcher, move remote
loading off the UI caller, and serialize session commands.

### Stage 7: Quality and Release Gates

Run deterministic integration, live SDLC evaluation, container smoke, and full
verification.

Each stage remains independently reviewable and must leave the full suite green.

## Acceptance Criteria

- Core imports and behavior are domain-neutral.
- Dead tool dispatch is removed and coding guardrails live under `coding_agent`.
- Reset, compact, hooks, listeners, and parallel tools satisfy the lifecycle and
  coordinator invariants.
- Low-level failures remain observable.
- Output memory remains bounded and artifacts contain complete byte output.
- Workspace enforcement is exact for file tools and honestly declared for bash.
- Repeated persisted compactions retain all prior summary context.
- One provider/model/auth authority serves SDK, CLI, AgentSession, and TUI.
- Every advertised provider passes its request, stream, error, and cancellation
  contract tests.
- Full JSON Schema constraints are enforced.
- Session tails recover without hiding mid-file corruption.
- TUI rendering and state mutation have one owner.
- The deterministic suite, 21 live scenarios, and container gate pass.
- `git diff -- appV2.3.1/appv231/compaction` is empty.
- No publishing or git operations occur without a later explicit request.

## Risks and Mitigations

- **Async migration breadth:** introduce the canonical awaitable invocation layer
  behind current entry points and migrate one callback class at a time.
- **Session compatibility:** adapt old records on read; do not eagerly rewrite
  existing logs.
- **Provider regression:** require fake-HTTP contract tests before changing
  catalog exposure.
- **Sandbox portability:** expose backend capability explicitly and fail closed
  when a caller requires sandboxing that is unavailable.
- **TUI responsiveness:** retain render coalescing metrics and stress-test bursty
  provider and tool events.
- **Live-evaluation variance:** use fixed settings, repeated rubrics, and report
  both pass rate and scenario-level evidence.

## Out of Scope

- Changes to compaction algorithms or files under the compaction redzone
- A new TUI visual design
- Publishing npm packages, images, or GitHub releases
- Supporting unimplemented providers without complete transport contracts
