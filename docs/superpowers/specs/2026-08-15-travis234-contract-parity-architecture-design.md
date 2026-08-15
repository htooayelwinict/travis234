# Travis234 Contract-Parity Architecture Design

**Status:** Strategic direction approved; written specification awaiting user review

**Date:** 2026-08-15

**Reference snapshots:**

- Travis234 `b1d7893a4d812f4577449418db0b3462b501b1f1`
- Pi `086c32e74530564922d011ade23ff582c9d63116`
- oh-my-pi `ffd53ff92a6f575d499730475a73460dd7cc2eea`

## Executive decision

Travis234 will pursue **contract parity**, not source parity, command parity, or raw
feature-count parity with oh-my-pi (OMP).

Contract parity means that a user can complete the same important coding workflows
with equivalent safety, observability, resumability, and output quality even when the
commands, implementation language, and internal component layout differ.

The three codebases have different authority:

1. **Travis234 owns product architecture and safety.** Its Python-native composition,
   trust boundary, ordered agent loop, iteration budget, bounded parallel execution,
   provider control plane, session behavior, and architectural size limits remain
   authoritative.
2. **OMP is the product-capability reference.** Its capability discovery, model roles,
   LSP/DAP integration, typed task agents, artifacts, supervision UX, and optional
   memory show which workflows are worth supporting.
3. **Current Pi is the durability reference.** Its modern harness separates immutable
   entries, mutable registers, usage accounting, and durable operation state. Its
   intent/effect/settlement model is a stronger long-term reliability target than
   either OMP's or Travis234's current conversation persistence.

Neither Pi nor OMP becomes a Travis234 runtime dependency. Their ignored local trees
are research oracles only.

## Why this design

OMP demonstrates unusually broad coding-agent functionality, but it also concentrates
too much ownership in central runtime classes. Its coding-agent session and interactive
controller have grown into multi-thousand-line surfaces. Porting those structures would
weaken Travis234's enforced façade/controller separation and make later reliability work
harder.

Current Pi has moved beyond the historical Pi foundation on which OMP was built. It is
therefore a better reference for crash recovery and operation durability, while OMP is
the better reference for end-user coding capabilities.

The recommended design combines those strengths without importing their accumulated
compatibility layers:

```text
CLI / TUI / RPC
       |
   CodingApp                         composition only
       |
 AgentSession                       bounded façade
       |
 +---------------- parity control plane ----------------+
 | CapabilityRegistry | ModelRoleRouter | ToolPolicy     |
 | DurableArtifactStore + ResourceRefResolver             |
 +--------------------------------------------------------+
       |
 Existing session controllers, providers, processes, and tools
       |
 Existing ordered agent loop                             unchanged
       |
 Optional later OperationStore intent/effect/settlement journal
```

## Goals

- Add high-value OMP workflows without turning Travis234 into an OMP port.
- Make capability origin, precedence, trust, model routing, and tool effects explicit.
- Make large tool and subagent outputs durable across session resume and fork.
- Deliver language intelligence through one bounded LSP surface.
- Improve typed delegation and live supervision using the existing subagent owner.
- Add crash-aware operation durability only after the capability foundation is proven.
- Keep advanced memory, MCP lifecycle, browser, and desktop capabilities optional.

## Non-goals

- JavaScript or Bun extension compatibility.
- Matching every OMP command, search backend, internal URI, or built-in tool.
- Replacing Python with Rust without measured evidence of a bottleneck.
- Rewriting `travis/agent/agent_loop.py` for feature parity.
- Turning Travis234 RPC into a multi-tenant network service.
- Generating one Travis tool for every MCP server tool.
- Automatic memory retention or continuous background review by default.
- DAP, persistent evaluation, browser control, and computer use in the first slice.
- Migrating existing JSONL sessions to a new format during the parity foundation work.

## Preserved invariants

- The product and CLI remain `Travis234` and `travis234`; imports remain under `travis`.
- User state remains under `~/.travis234` through existing path owners. No alternate
  state root or migration alias is introduced.
- Existing JSONL sessions remain readable and remain durable conversation history.
- Provider credentials never enter tracked files, diagnostics, artifacts, model prompts,
  or child environments unless an existing explicit allowlist permits them.
- Provider ownership stays outside the generic agent loop.
- Agent-loop event ordering, iteration budgeting, continuation behavior, cancellation,
  steering, follow-ups, and source-ordered tool-result persistence remain unchanged.
- Parallel tool execution remains bounded.
- Project code and behavior-changing project resources remain trust-gated.
- Generic MCP support and the separately packaged bounded MCP adapter remain supported.
- `AgentSession` and TUI façade limits remain enforced. New collaborator modules remain
  below the existing 750-line architecture limit and do not import application façades.

## Approaches considered

### A. Contract parity in vertical slices — selected

Add a small parity control plane, then deliver one complete LSP workflow before moving
to richer coordination and durability.

**Advantages:** preserves Travis234 architecture, yields reusable contracts, allows each
slice to be tested and reverted independently, and produces visible value before the
largest runtime change.

**Costs:** apparent feature parity arrives more slowly; some foundational work is not
immediately visible; adapters are required around existing loaders and registries.

### B. OMP feature-parity sprint — rejected

Port OMP tools and commands one by one until the public inventories approximately match.

**Advantages:** fastest route to demos and comparison checklists.

**Costs:** duplicates policy, expands prompt schemas, encourages large session owners,
imports compatibility debt, and makes safety semantics inconsistent. It optimizes the
wrong metric.

### C. Pi durability first — deferred, then adopted in Phase 4

Replace or wrap the current runtime with a transactional operation harness before adding
product capabilities.

**Advantages:** strongest crash model and cleanest theoretical runtime foundation.

**Costs:** high migration and regression risk, little immediate user-visible value, and
too much pressure on the behavior-sensitive agent loop. The durability concepts remain
valuable after the lower-risk control plane has established stable contracts.

## Delivery decomposition

This document is an umbrella architecture specification. It deliberately decomposes the
program into independently designed and released subprojects. Each subproject receives
its own focused implementation specification and plan; approval of this document does
not authorize implementing all phases as one change.

| Phase | Deliverable | User-visible value | Principal tradeoff |
|---|---|---|---|
| 1A | Capability registry | Explainable, deterministic resources | New abstraction around working loaders |
| 1B | Model role router | Better cost/capability routing | More configuration and fallback policy |
| 1C | Durable artifacts and resource refs | Outputs survive resume and fork | Disk retention and garbage collection |
| 1D | Uniform tool policy | Consistent effect controls | Approval latency and integration complexity |
| 2 | Bounded LSP vertical slice | Semantic coding intelligence | Language-server lifecycle burden |
| 3 | Typed roles and supervisor UX | Better delegation and review | More state and UI surface |
| 4 | Durable operation journal | Crash-aware recovery | Dual persistence and uncertain effects |
| 5 | Optional ecosystem capabilities | Memory and richer integrations | Privacy, context, and operational cost |

The first implementation plan after this architecture is approved will cover only Phase
1A. Later phases require their own design checkpoint.

## Phase 1A: capability registry

### Responsibility

Introduce a typed internal registry beneath `DefaultResourceLoader`. The loader remains
the public façade so existing callers, settings, package behavior, and extension behavior
do not change in the first release.

The registry owns:

- provider registration;
- load context, including working directory, trust state, offline state, and generation;
- immutable capability records with type, canonical key, value, source, priority, and
  enabled state;
- collision and failure diagnostics;
- deterministic deduplication;
- atomic snapshot replacement on reload;
- origin and precedence introspection.

Initial capability kinds are context files, skills, prompt templates, themes, extensions,
tools, and agent-role definitions. MCP connections are not moved into this registry.

### Interfaces

The focused owner belongs under `travis/coding_agent/capabilities/` and exposes concepts
equivalent to:

- `CapabilityProvider.load(context) -> CapabilityLoadResult`
- `CapabilityRecord(kind, key, value, source, priority, enabled)`
- `CapabilityDiagnostic(severity, provider, source, code, message)`
- `CapabilitySnapshot.records(kind)`
- `CapabilityRegistry.reload(context) -> CapabilitySnapshot`

Provider failures become diagnostics and do not partially replace the live snapshot.
Reload first builds a candidate snapshot, validates it, then swaps it under a lock.
Consumers never observe half-reloaded resources.

### Precedence

Phase 1A preserves current effective order exactly. It does not use the new abstraction
to redefine which package, configured path, explicit path, or bundled resource wins.
The registry records why a winner was selected and reports collisions that current code
silently or locally resolves.

Project providers are not invoked before project trust is resolved. Filtering an item
removes it before deduplication; disabling a provider removes that provider's entire
contribution. OMP's more complicated suppression semantics are excluded until a concrete
Travis234 use case requires them.

### Tradeoffs

- An adapter layer temporarily coexists with existing resource-specific return shapes.
  This is intentional; changing all consumers in one patch would create an unsafe flag day.
- Immutable snapshots use more short-lived memory during reload but make concurrency and
  failure semantics substantially easier to reason about.
- Preserving precedence limits immediate cleanup, but eliminates surprise behavior changes.

## Phase 1B: model role router

### Responsibility

Add a session-scoped `ModelRoleRouter` that resolves a purpose to a model binding without
duplicating `ModelRegistry`, provider authentication, or transport selection.

Initial roles are:

- `primary`: the model selected for the active conversation;
- `compression`: the existing optional compression model;
- `worker`: bounded subagent work;
- `reviewer`: explicit review/advisor work;
- `vision`: inputs that require image capability.

The router returns an immutable result containing the role, selected `ScopedModel`, source
of the selection, and fallback trace. Consumers request a role; they do not inspect model
IDs or provider credentials themselves.

Resolution order is:

1. explicit call-scoped override supplied by trusted application code;
2. session/user role mapping from existing settings ownership;
3. compatible role fallback;
4. active primary model when it satisfies the required capabilities;
5. a typed unavailable result.

`compression` preserves current explicit compression-model behavior. `reviewer` falls
back to `worker`, then `primary`. `vision` never falls back to a model that cannot accept
images. Model switches immediately update the `primary` route but do not silently rewrite
explicit role mappings.

### Tradeoffs

- Role routing allows cost and quality specialization, but troubleshooting becomes harder.
  The fallback trace is therefore part of every resolution and evaluation event.
- Five roles cover current needs without importing OMP's larger role taxonomy.
- Automatic credential rotation and path-scoped routing are excluded; they add policy and
  security complexity without being necessary for the first workflows.

## Phase 1C: durable artifacts and resource references

### Responsibility

Replace deletion-on-session-close behavior for promoted artifacts with a durable store
while keeping temporary spools ephemeral.

`DurableArtifactStore` owns immutable sanitized objects below the existing Travis234 agent
directory:

```text
~/.travis234/agent/artifacts/objects/<sha256-prefix>/<sha256>
```

Each session receives an append-only artifact manifest beside its existing JSONL file.
The manifest maps the existing opaque `artifact-<uuid>` identifier to object digest,
kind, byte size, creation time, and producing session entry/tool call. It contains no
credentials and no unsanitized command environment.

Only sessions with an existing durable JSONL path receive a durable manifest. In-memory
and test sessions keep the current ephemeral registry unless their existing persistence
owner first creates a durable session; artifact promotion must not create a hidden session.

`ArtifactRegistry` remains the session-facing owner and becomes an adapter over ephemeral
and durable stores. An output is promoted only when it is truncated, explicitly retained,
or returned as a declared subagent artifact. Ordinary small output remains in JSONL and
does not create a durable object.

Initial default storage limits are 64 MiB for one promoted object, 512 MiB of logical
artifact references for one session, and 2 GiB of physical objects for the installation.
Settings may lower these limits. Raising them requires an explicit user setting and remains
subject to filesystem free-space checks. When a limit is reached, promotion stops without
evicting a referenced object.

A focused `ResourceRefResolver` resolves registered artifact and subagent references. It
does not become a process-global URI router and does not initially implement OMP's broad
internal URL scheme.

### Resume, fork, and cleanup

- Resume reloads the session manifest and restores exact artifact IDs.
- Fork copies manifest references, not object bytes. Parent and child may read the shared
  immutable objects.
- Moving or exporting a session includes its manifest; exporting object contents remains
  an explicit operation.
- Automatic age-based object deletion is disabled initially. Explicit session deletion or
  artifact maintenance may run a conservative garbage collector, which deletes an object
  only after successfully scanning every manifest and proving that no reference or
  retention hold remains. An unreadable manifest makes collection fail closed.
- Existing per-read byte pagination remains mandatory. Host object paths are never placed
  in model-visible output.

If persistence fails, the originating tool still returns its bounded sanitized result and
an explicit artifact-unavailable diagnostic. Artifact failure must not convert an otherwise
successful command into a false command failure.

### Tradeoffs

- Content-addressed storage provides fork-safe deduplication and integrity checking, but
  requires manifest management and eventual garbage collection.
- Session-local copies would simplify deletion but duplicate large outputs and complicate
  forks; they are rejected.
- Retaining no artifacts avoids disk growth but makes resumed tool references dishonest;
  it is rejected.
- Conservative initial retention prefers recoverability over aggressive disk reclamation.

## Phase 1D: uniform tool policy

### Responsibility

Formalize existing trust, tool filtering, and execution restrictions behind one coding-
agent policy contract without moving policy into the generic agent loop.

Each `ToolDefinition` receives a non-empty set of declarative effects drawn from `read`,
`write`, `execute`, and `network`; a tool may have more than one. The existing
`execution_mode` continues to mean scheduling concurrency and is not overloaded with
security meaning.

`ToolPolicyEngine.decide(tool, arguments, context)` returns `allow`, `deny`, or `prompt`
with a stable reason code. Known built-ins receive explicit classifications. Missing or
invalid third-party metadata is diagnosed during audit mode and is denied once enforcement
is enabled; it is never silently treated as read-only.

Enforcement wraps tool execution at the coding-agent bridge. The generic agent loop still
receives ordinary `AgentTool` objects and retains its current scheduling and ordering.
Non-interactive sessions fail closed when a decision requires prompting. Interactive
approval is supplied by an injected session-owned broker, not by TUI imports in the policy
module.

The first release runs classification and decision telemetry against existing behavior
before enabling any new prompt. Enforcement begins only after the audit shows that core,
extension, MCP, and subagent tools have complete classifications.

### Tradeoffs

- Per-call decisions can add latency and interaction friction, particularly for repetitive
  edits. Decisions therefore support narrowly scoped session grants rather than permanent
  implicit trust.
- Audit-first rollout delays enforcement but prevents accidental breakage of established
  automation.
- Tool schemas stay small because effect metadata is application policy, not model-facing
  prompt text.

## Phase 2: bounded LSP vertical slice

### Product surface

Expose one `lsp` tool with an `action` discriminator instead of one tool per language-
server method. Initial actions are:

- diagnostics;
- document and workspace symbols;
- hover;
- definition;
- references;
- rename preview and apply;
- code-action preview and apply.

One bounded schema costs less prompt context and keeps action discovery explicit. DAP and
persistent Python/JavaScript evaluation are separate future slices.

### Architecture

`travis/coding_agent/language_services/` owns:

- language-server configuration and workspace matching;
- bounded server process lifecycle;
- JSON-RPC framing and request correlation;
- document version tracking;
- response and diagnostic normalization;
- cancellation, timeout, restart, and output limits;
- conversion of `WorkspaceEdit` into Travis234 mutation operations.

Project-provided server commands and configuration require project trust. Travis234 does
not auto-install language servers. The manager enforces a small active-server ceiling and
terminates its children during application shutdown.

Read actions return bounded normalized data with spill-to-artifact behavior. Rename and
code-action changes are two-step operations: preview produces a normalized edit set and
opaque revision token; apply validates that document versions and workspace hashes still
match before using existing write/edit ownership. Paths outside the trusted workspace,
unsupported resource operations, and stale previews are rejected.

The filesystem cannot provide a portable atomic transaction across multiple files. Apply
therefore preflights every edit, stages original content, writes in deterministic order,
and attempts rollback if a later write fails. The result identifies every changed,
restored, and unresolved path. A partial failure is never reported as success, but the
design does not claim crash-proof all-or-nothing filesystem semantics.

### Tradeoffs

- One tool is less directly discoverable than many specialized tools, but materially
  reduces schema cost and policy duplication.
- User-managed language servers reduce installation convenience but avoid supply-chain
  and platform packaging risk.
- Preview/apply is slower than immediate edits but makes multi-file semantic mutations
  reviewable and race-safe.
- Building a focused JSON-RPC client adds maintenance cost; embedding OMP's Bun/Rust stack
  would add much more operational and packaging complexity.

## Phase 3: typed coordination and supervision

### Agent roles

Agent-role definitions become a capability kind with a validated schema containing:

- stable name and description;
- model role;
- allowed tool set and tool-effect ceiling;
- bounded spawn permission and maximum depth;
- optional skill/context preload;
- result JSON schema;
- default timeout and artifact policy.

`SubagentSupervisor` remains the lifecycle and concurrency owner. Role definitions configure
the existing supervisor rather than creating a second task engine. Existing depth and
parallelism defaults remain unchanged until a separate capacity evaluation justifies an
increase.

### Supervisor UX

The TUI adds focused collaborator modules for a live roster, status, transcript summary,
steer, cancel, and result inspection. These modules consume supervisor snapshots and events;
they do not move lifecycle state into `InteractiveMode`.

Reviewer/advisor behavior is an explicit role invoked by the user, plan, or trusted setting.
It is not an always-on background model call. Worktree creation remains explicit and opt-in.

### Tradeoffs

- Typed roles improve repeatability and structured handoff but increase configuration and
  validation surface.
- Explicit reviewers use fewer tokens and avoid surprise blockers, but provide less ambient
  guidance than OMP's continuous advisor model.
- Preserving current concurrency limits may underuse large machines, but protects provider
  budgets and workspace ownership until isolation contracts improve.

## Phase 4: durable operation journal

### Responsibility

Add crash awareness without replacing JSONL conversation history. A new `OperationStore`
uses SQLite at `~/.travis234/agent/operations.sqlite3`, resolved through the existing agent
directory owner, and records:

- operation identity, kind, session, and durable program counter;
- mutable operation registers;
- provider/tool effect intent;
- settlement or explicitly uncertain outcome;
- tool replay policy (`safe` or `never`);
- usage ledger entries.

The runtime boundary follows the current Pi effect sandwich:

1. commit intent;
2. execute the external provider or tool effect;
3. commit settlement;
4. append conversation-visible results in existing source order.

The first release is observe-only: it records operations but performs no automatic replay.
Recovery is enabled later only for effects whose adapters declare a tested safe replay
policy. Destructive or externally mutating tools default to `never`. An intent without a
settlement is reported as uncertain; it is not silently retried.

The coordinator wraps existing turn and tool boundaries through session-owned hooks. It
does not reorder the generic agent loop or promise exactly-once execution.

### Tradeoffs

- A separate journal avoids a risky session-format migration but creates two coordinated
  persistence authorities: JSONL for conversation and SQLite for operation state.
- The intent/settlement window identifies uncertainty but cannot prove an external side
  effect did or did not happen.
- Observe-only rollout delays automatic recovery but gives real evidence about operation
  boundaries before replay can cause harm.

## Phase 5: optional ecosystem capabilities

### Memory

Memory is opt-in, project-scoped by default, and begins with explicit recall and retain.
Each fact records provenance, timestamp, scope, and optional expiry. Retrieved memory is
bounded and treated as untrusted context, never as higher-priority instructions. Automatic
retention and reflection remain off until privacy, staleness, and prompt-injection
evaluations pass.

### MCP

Retain the generic separately packaged MCP adapter and its single bounded `mcp` proxy.
Possible additions are background connection status, bounded reconnect, OAuth, resources,
and prompts behind that proxy. Travis234 does not generate `mcp__server__tool` names for
every remote method.

### Browser and computer use

Browser and desktop tools remain optional packages or MCP servers. They do not become core
dependencies or bypass the tool-policy layer.

### Native acceleration

Python remains the default implementation language. Rust or another native component is
introduced only when a reproducible benchmark identifies a hot path that cannot be solved
adequately with existing subprocess tools or focused optimization.

## End-to-end data flows

### Startup and resource reload

1. `CodingApp` resolves settings, explicit inputs, and project trust using current owners.
2. Trusted capability providers load into a candidate registry snapshot.
3. Validation and deterministic deduplication complete off to the side.
4. A successful candidate atomically replaces the live snapshot; a failed candidate leaves
   the previous snapshot active and publishes diagnostics.
5. `DefaultResourceLoader` projects the snapshot into its existing public return shapes.
6. `ModelRoleRouter` binds the active primary and configured auxiliary roles.
7. Session-owned artifact, policy, process, and optional language-service owners start.

### Tool call

1. The unchanged agent loop selects and schedules tools under its current bounded rules.
2. The coding-agent tool wrapper asks `ToolPolicyEngine` for a decision.
3. Denial returns a stable tool error; an unavailable non-interactive prompt fails closed.
4. Allowed execution proceeds through the current tool implementation.
5. Oversized sanitized output is promoted to `DurableArtifactStore` and referenced by ID.
6. Tool results are still persisted in source order.

### LSP semantic edit

1. The model requests a preview action through the single `lsp` tool.
2. `LanguageServiceManager` selects a trusted configured server and synchronizes the file.
3. The server returns a `WorkspaceEdit`; Travis234 normalizes and bounds it.
4. The tool returns a preview plus revision token.
5. Apply revalidates document versions, workspace containment, and tool policy.
6. Existing mutation ownership applies the staged edit; failure triggers rollback and an
   explicit partial-state report.

### Resume and fork

1. Existing JSONL reconstructs the conversation branch.
2. The artifact manifest reconstructs allowed artifact IDs and integrity metadata.
3. The durable store verifies an object's digest before first read.
4. Forked sessions copy references into a new manifest without copying immutable bytes.
5. Missing objects remain explicit unavailable references; they are never replaced with a
   different file found at a similar path.

### Later crash recovery

1. Startup finds an operation with committed intent and no settlement.
2. The operation is marked uncertain and inspected through its adapter replay policy.
3. `never` effects require user or caller resolution.
4. A `safe` effect may be replayed only after its dedicated fault-injection suite proves
   idempotent recovery.
5. Settlement resumes normal source-ordered conversation persistence.

## Error handling

- Capability-provider exceptions are isolated, sanitized, and attributed to their source.
- A failed registry reload cannot erase the last valid snapshot.
- Invalid role configuration yields a typed unavailable route and fallback trace; it never
  crosses provider credentials or chooses a capability-incompatible model.
- Artifact writes use temporary files, fsync where supported, digest verification, and
  atomic rename. Partial objects are not registered.
- Artifact reads remain session-authorized, paginated, and size-bounded.
- Language-server crashes fail only the active request, use bounded restart policy, and do
  not kill the coding session.
- LSP protocol logs redact document text and credentials by default.
- Tool-policy broker cancellation is a denial and cannot leave a waiting tool call alive.
- Operation-journal corruption disables recovery with an actionable diagnostic; it does
  not mutate or discard JSONL history.
- No layer implements unbounded retry.

## Security and privacy

- Project capability providers, language-server commands, and role definitions are loaded
  only after project trust resolution.
- Provider auth remains owned by `ModelRegistry`/`AuthStorage`; role records carry model
  references, never credentials.
- Artifacts store sanitized tool output with restrictive filesystem permissions. Secrets
  detected by existing sanitization are not recoverable through artifact reads.
- Resource references resolve only within the current session's manifest or an explicitly
  imported reference set.
- Tool decisions include stable reason codes for audit without recording sensitive raw
  arguments.
- Memory retrieval is bounded, provenance-marked, and lower authority than system, user,
  repository, and skill instructions.

## Verification strategy

Every bug fix begins with a failing regression test. Each phase also begins with contract
tests for the new boundary before consumers are migrated.

### Phase 1A

- deterministic precedence matches current resource behavior;
- trust-gated providers never run during pre-trust loading;
- provider failures leave the previous snapshot active;
- collisions and sources are explainable;
- concurrent readers observe only complete snapshots;
- reload disposal does not leak extension modules or event handlers.

### Phase 1B

- every role resolution records its source and fallback trace;
- active model switching updates only the implicit primary route;
- capability-incompatible vision fallback is rejected;
- provider credentials follow the resolved model binding;
- missing optional roles degrade without breaking the primary turn.

### Phase 1C

- truncated output survives close, restart, resume, and fork;
- small output remains ephemeral and does not create an object;
- digest mismatch, missing object, unauthorized session, and over-limit reads fail closed;
- concurrent promotion of identical output produces one valid object;
- manifest and object writes recover cleanly from injected interruption;
- shutdown cleans temporary files without deleting promoted objects.

### Phase 1D

- every built-in and packaged tool has effect metadata;
- extension and MCP tools cannot obtain a less restrictive default by omitting metadata;
- interactive prompt, deny, cancellation, and non-interactive fail-closed paths are tested;
- policy wrapping does not change tool scheduling or result order;
- audit and enforcement decisions are stable and sanitized.

### Phase 2

- fragmented JSON-RPC frames, cancellation, timeout, crash, and bounded restart;
- workspace selection and trusted configuration;
- diagnostics and navigation result normalization;
- preview/apply race detection, rollback, and explicit partial-state reporting;
- path traversal and unsupported resource operations rejected;
- oversized results spill through durable artifacts.

### Phases 3–5

- role schema, depth, tool ceiling, structured output, and supervisor lifecycle;
- TUI owner-thread behavior and façade-size boundaries;
- intent/settlement crash injection before, during, and after effects;
- unsafe effects never replay automatically;
- memory scope, provenance, expiry, injection resistance, and explicit deletion;
- MCP reconnect/auth remains bounded and preserves the one-proxy contract.

Before any implemented phase is reported complete, run focused tests plus the repository's
required Python suite, npm launcher tests, package builds, and relevant container smoke
checks.

## Success criteria

The program is successful when:

- every loaded capability can explain its source, trust decision, and precedence;
- worker, reviewer, compression, and vision work can route independently without credential
  leakage or model-ID logic in consumers;
- artifact references remain valid across restart and fork;
- the LSP slice performs safe semantic navigation and reviewed multi-file edits without an
  agent-loop change;
- subagents return schema-validated results and can be inspected, steered, and cancelled
  through the existing supervisor;
- fault-injection tests prove that unsafe unsettled effects are never automatically replayed;
- optional memory and integrations can be completely disabled without changing core turns;
- architecture tests continue preventing OMP-style god objects.

## Rollout and rollback boundaries

- Phase 1A is an internal compatibility adapter and must be behavior-neutral before legacy
  loading internals are removed.
- Phase 1B consumers migrate role by role; each can fall back to its current direct model
  selection during rollback.
- Phase 1C promotes only newly created artifacts. Existing sessions need no migration.
- Phase 1D ships audit-only before enforcement and can disable enforcement independently.
- LSP, enhanced supervision, operation recovery, memory, and richer MCP lifecycle each have
  separate enablement and can be removed without changing the core loop.
- Feature switches are rollout mechanisms, not permanent duplicate implementations. Legacy
  paths are removed only after the corresponding contract suite and release gates pass.

## Final tradeoff position

This design deliberately trades near-term feature-count velocity for long-term coherence.
It accepts additional small contracts, immutable snapshots, manifests, and an eventual
operation journal so that high-value OMP workflows do not leak policy into the agent loop
or accumulate inside Travis234 façades.

The most consequential choices are:

- **one bounded tool over many schemas** for LSP and MCP;
- **explicit roles over scattered model selection**;
- **durable immutable artifacts over temporary-path convenience**;
- **audit before enforcement** for uniform tool policy;
- **observe before replay** for operation durability;
- **explicit, scoped memory over automatic ambient memory**;
- **Python-native boundaries over compatibility and native-code breadth**.

Those choices will make Travis234 appear to advance more slowly than OMP on a checklist,
but they make each delivered capability safer, independently testable, and easier to keep.
