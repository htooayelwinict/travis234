# Travis234 Contract-First Architecture Refactor Design

**Status:** Approved for implementation planning

**Date:** 2026-08-19

**Reference commit:** `7838749452b567940bd5b69a715b6184b8f9f13e`

## Executive decision

Travis234 will use a **contract-first incremental extraction** to remove composition,
provider, quality-gate, and delivery debt without rewriting the behavior-sensitive
agent loop.

The public `AgentSession` and `InteractiveMode` façades remain compatibility surfaces.
Their internal mixin runtimes are replaced in stages by explicit collaborators with
typed, narrow dependencies. Provider profiles, transport contracts, serializers,
parsers, and runtime request execution are separated behind the existing provider
entry points. Quality and release gates are made truthful before the largest
extractions begin.

This is a refactor program, not a feature rewrite. Existing sessions, user state,
commands, tools, provider wire behavior, extension lifecycle events, and agent-loop
semantics are preserved unless a separately identified bug has a failing regression
test first.

## Evidence baseline

The design responds to the read-only audit of the reference commit:

- The normal root suite passes: 2,650 tests.
- The optional MCP adapter host suite passes: 125 tests, with 89.01% combined
  coverage in the audit run.
- The npm launcher suite passes: 24 tests; npm dry-pack contains 11 expected files.
- Clean root and adapter builds, Twine validation, and clean-wheel installation pass.
- Root coverage diagnostics measured 83.95% statement coverage, 68.51% branch
  coverage, and 80.03% combined coverage.
- One real platform defect was reproduced: `/tmp` and `/private/tmp` aliases are
  compared lexically in `travis/coding_agent/tools/bash.py`, allowing the runtime
  virtual-environment bin directory to remain in tool `PATH` on macOS.
- `_SessionRuntime` inherits from 12 behavior owners and receives about 50 constructor
  parameters. Its initialization complexity is concentrated in one shared mutable
  object.
- `_InteractiveRuntime` inherits from 14 behavior owners and relies on star imports
  and implicit shared attributes.
- `RuntimeFacade` forwards arbitrary reads and writes dynamically, hiding the actual
  public dependency surface from static analysis.
- Provider ownership is concentrated in a 2,326-line transport module. Provider base
  and transport imports form a cycle, as do session construction modules.
- Pyright reports thousands of root errors, dominated by unknown attributes created
  by mixin composition. Four public type-hint resolutions raise `NameError`.
- Ruff's high-volume findings are dominated by unused imports, but undefined-name,
  import-structure, and unreachable-code findings also exist.
- The default acceptance verifier can report success without current-commit evidence;
  strict verification currently exposes missing evidence.
- Main lacks a normal pull-request/push CI gate. Release automation is not a substitute
  for continuous integration.
- CLI import and help startup perform eager work. Provider model refresh is uncapped,
  and model-catalog response reading is unbounded.
- Several symbols are unreferenced. `ArtifactGarbageCollector` is tested but not owned
  by a normal product lifecycle.

These measurements are baselines, not permanent thresholds. Each phase records fresh
evidence before changing its target area.

## Goals

- Preserve the ordered agent loop while reducing architectural coupling around it.
- Replace implicit mixin/shared-`self` contracts with explicit collaborator contracts.
- Make the supported session and TUI façade surfaces discoverable and type-checkable.
- Remove session and provider import cycles.
- Split provider wire responsibilities into bounded, independently testable modules.
- Fix confirmed bugs through regression-first development.
- Make static analysis, coverage, acceptance, packaging, and CI results truthful.
- Improve startup and provider-catalog performance without changing interactive
  semantics.
- Remove proven dead code and explicitly classify intentionally dormant components.
- Reconcile source, tests, README, architecture rules, verification records, and
  release commands.

## Non-goals

- Rewriting `travis/agent/agent_loop.py` or changing its state machine.
- Changing iteration budgets, cancellation, steering, follow-ups, continuation
  ordering, or bounded parallel tool execution.
- Changing the JSONL session format or moving data away from `~/.travis234`.
- Removing generic MCP support or changing the separately packaged MCP adapter.
- Removing `RuntimeFacade` compatibility in this program.
- Renaming existing tools, commands, extension hooks, public provider IDs, or model IDs.
- Introducing automatic artifact deletion.
- Replacing the sync provider stream bridge as part of this refactor.
- Adopting every strict lint or type rule in one flag day.
- Publishing packages, pushing images, or changing remote permissions as part of local
  implementation.

## Protected invariants

The following behavior is frozen for the entire program:

1. Agent-loop event ordering and reducer ordering.
2. Iteration accounting and exhaustion behavior.
3. Cancellation propagation and abort observation.
4. Steering and follow-up queue semantics.
5. Source-ordered tool-result persistence.
6. Bounded parallel tool execution.
7. Provider retry ownership outside the generic agent loop.
8. Trust gating before project-owned code or resources execute.
9. Session resume, fork, clone, compaction, and JSONL readability.
10. Extension registration and lifecycle event ordering.
11. Existing TUI command names, aliases, shortcuts, and normal-user flows.
12. Credential redaction and the rule that credentials do not enter tracked files,
    logs, fixtures, prompts, or child environments without an existing explicit
    allowlist.

During implementation, every phase checkpoint compares its branch against reference
commit `7838749` and must show no diff to `travis/agent/agent_loop.py`. Existing focused
agent-loop invariant tests run at every phase boundary. A proposed change to that file
stops this program and requires a new design approval.

## Approaches considered

### A. Contract-first incremental extraction — selected

Introduce characterization and architecture gates, then replace one implicit ownership
boundary at a time while retaining compatibility façades.

**Advantages**

- Small rollback units.
- Behavior is observable before implementation moves.
- External and extension callers retain their current entry points.
- Static typing becomes useful incrementally instead of requiring a repository-wide
  suppression baseline.
- Provider families can be migrated and compared independently.

**Costs**

- Old and new composition styles coexist temporarily.
- Some forwarding remains until all internal callers use explicit services.
- The clean endpoint takes more commits than a rewrite.

### B. Big-bang clean-architecture rewrite — rejected

Replace session, TUI, provider, and façade ownership in one coordinated cutover.

**Advantages**

- Less temporary compatibility code.
- The target architecture appears sooner.

**Costs**

- Failures cannot be localized reliably.
- Extension and test dependencies on dynamic attributes are easy to miss.
- Provider wire regressions and session lifecycle regressions would overlap.
- Rollback would discard unrelated validated work.
- It creates pressure to modify the protected loop to accommodate the rewrite.

### C. Quality-gates-only cleanup — rejected as the final design

Fix the confirmed bug, lint failures, CI, docs, and packaging without changing
composition.

**Advantages**

- Lowest immediate runtime risk.
- Faster first green CI result.

**Costs**

- Leaves the shared-`self` dependency graph and provider concentration intact.
- Type errors quickly return as features are added.
- Large owners remain difficult to test, review, and optimize.

Quality gates are still delivered first, but they protect the extraction rather than
replace it.

## Target architecture

```text
CLI / RPC / TUI / extensions / tests
                 |
       stable compatibility façades
       AgentSession / InteractiveMode
                 |
      explicit application composition
       +---------+----------+
       |                    |
 session collaborators   TUI collaborators
       |                    |
       +---------+----------+
                 |
         Agent public contract
                 |
       protected agent loop unchanged
                 |
 provider runtime -> transport -> translation -> normalized events
```

The direction of dependency is inward toward small contracts. Leaf collaborators do
not import application façades. Composition roots may import collaborators; collaborators
may import contracts and domain types; contracts do not import concrete runtimes.

### Contract rules

- Use `typing.Protocol` only for real substitution boundaries, test seams, or cycle
  breaks. Protocols are static structural contracts, not a replacement for every class.
- Do not use `@runtime_checkable` unless production code genuinely performs an
  `isinstance` check. Runtime protocol checks verify attribute presence, not complete
  signatures.
- Prefer immutable `@dataclass(frozen=True, slots=True)` configuration records where
  mutation is not part of the domain.
- Keep mutable turn/session state in its existing owner until a characterized extraction
  moves it deliberately.
- Public annotations must resolve through `typing.get_type_hints` in a clean interpreter.
- Avoid `Any` at new boundary contracts. Existing dynamic extension payloads may retain
  `object` or narrowly documented mappings.
- New collaborators remain below the existing 750-line repository architecture limit;
  the preferred target is below 400 lines.

## Session composition

### Current problem

`_SessionRuntime` obtains behavior through 12 base classes. Those classes implicitly
require hundreds of attributes that are initialized elsewhere. The type checker cannot
see the contract, initialization order is difficult to prove, and a controller can
mutate another controller's state without an explicit dependency.

### Target

`_SessionRuntime` becomes a small composition owner rather than a multiple-inheritance
host. Construction is divided into three explicit records:

- `SessionBootstrapOptions`: caller-supplied values and compatibility aliases normalized
  once at the boundary.
- `SessionDependencies`: long-lived concrete dependencies such as model registry,
  settings, resource loader, extension runner, process service, approval broker, and
  operation runtime.
- `SessionControllers`: the explicitly constructed event, tool, extension, turn,
  subagent, model, persistence, bash, policy, operation, and compaction collaborators.

Controller names may retain their present public vocabulary, but their implementation
changes from inherited methods to owned objects. Each collaborator receives only the
state and ports it uses. Cross-domain calls go through a narrow protocol or an injected
callable, not through arbitrary access to the complete runtime.

`AgentSession` remains the stable public entry point. As a method or property is moved,
it receives an explicit delegating member on the façade or on a typed façade contract.
`RuntimeFacade.__getattr__` remains as a compatibility fallback. An architecture test
prevents new public APIs from existing only through the fallback, but the fallback is
not removed in this program.

### Session construction and cycles

`agent_session_services.py` owns dependency creation and option normalization.
`agent_session.py` owns the public façade and runtime composition. Session replacement
code depends on an injected factory protocol or callable rather than importing the
concrete `AgentSession` back through the services layer.

`session_types.py` becomes types-only. Runtime constructors, command implementations,
and large compatibility imports leave that module. Explicit imports and `__all__`
replace star imports while preserving supported re-exports.

### Lifecycle transaction

New, resumed, forked, and cloned sessions follow the existing externally visible event
sequence:

```text
request replacement
      |
emit before-switch/fork hook -- cancelled --> retain current session
      |
build complete candidate runtime
      |
validate required owners and restored state
      |
atomically bind replacement to app/TUI
      |
emit existing activation/start hooks
      |
dispose previous optional owners exactly once
```

If candidate construction fails, the active session remains usable and the error uses
the existing diagnostic channel. Cleanup and shutdown remain idempotent.

## TUI composition

### Current problem

`_InteractiveRuntime` obtains behavior from 14 base classes. Star imports make names
and ownership ambiguous, and controller modules communicate through undocumented
attributes on the shared runtime.

### Target

`InteractiveMode` remains the stable façade. `_InteractiveRuntime` becomes a bounded
composition object containing:

- `InteractiveState`: prompt, history, status, generation parameters, selection, and
  display state with deliberate mutation points.
- `InteractiveServices`: app/session access, terminal renderer, theme, extension host,
  process display, approval broker, and input/output ports.
- `InteractiveControllers`: command dispatch, turn handling, session commands, model
  authentication, processes, subagents, LSP, memory, operations, view, and shutdown.

Controllers receive the minimum service/state views they require. Command registration
is explicit and ordered. The dispatcher retains current command names, aliases, help
text, precedence, extension override behavior, and line-input behavior.

The refactor removes star imports from `interactive_mode.py` and `component.py`.
Compatibility re-exports use explicit imports and `__all__` so existing imports remain
valid and static analysis can resolve them.

### TUI behavioral contract

Characterization scenarios cover:

- startup, help, and a normal prompt;
- model selection and `/login` entry behavior without exposing credentials;
- generation parameters and thinking level;
- session new/resume/fork/clone;
- process start/status/output/cancel;
- subagent spawn/wait/result/expand;
- LSP and memory commands when available;
- `/coordination` skill discovery and invocation;
- tool approval, cancellation, steering, and follow-up display;
- extension commands and theme/resource reload;
- shutdown during idle and active work.

Every TUI phase uses an installed wheel in an isolated state directory for user-style
scenarios. Direct class tests alone do not qualify as TUI acceptance.

## Provider architecture

### Current problem

Provider profiles, transport interfaces, transport implementations, message conversion,
response normalization, and runtime request behavior are coupled through large modules
and cycles. This makes wire changes hard to review and prevents focused type checking.

### Target ownership

- `provider_contracts`: normalized tool calls, usage, responses, and static transport
  protocols. It imports no concrete transport.
- `provider_profiles`: declarative provider facts only. Profile availability is queried
  through the registry rather than importing transports from a property.
- `transport_registry`: maps `api_mode` to a concrete transport and retains the current
  `get_transport` compatibility function.
- API-family transport modules: chat completions, Anthropic messages, Google generative
  APIs, Bedrock converse, Mistral conversations, Codex responses, OpenAI responses, and
  Azure responses.
- Translation modules: request messages/tools and response/event normalization.
- Runtime request modules: authentication, URL resolution, HTTP/WebSocket lifecycle,
  retries, cancellation observation, and sanitized errors.

Public imports currently exposed through `travis.ai.providers` continue to resolve via
explicit compatibility exports. No provider ID or `api_mode` changes during extraction.

### Wire-preservation strategy

Before moving a provider family, sanitized golden fixtures capture:

- endpoint selection and URL normalization;
- headers after secret placeholders replace values;
- request JSON, including omitted versus explicit fields;
- tool schema and tool-call ID normalization;
- reasoning and image transformations;
- streaming event sequence and normalized final response;
- retry classification and redacted error shape.

The old and new implementation are compared against the same fixtures during each
family migration. Fixtures never contain live tokens, account IDs, session IDs, or
unredacted response bodies.

### Catalog hardening

Remote model discovery receives a bounded response reader and explicit URL policy.
Remote endpoints use HTTPS. Loopback development endpoints may use HTTP. Any broader
plain-HTTP support requires an explicit trusted configuration and tests; it is not
silently inferred. Redirect and size behavior is characterized before enforcement.

Model refresh uses a bounded worker pool with cancellation-aware collection. Provider
order in the displayed catalog remains deterministic regardless of completion order.

## Core turn data flow

The refactor does not insert a new queue, persistence layer, or scheduling boundary in
the active turn:

```text
user/TUI input
   -> explicit TUI turn controller
   -> AgentSession turn collaborator
   -> existing Agent public API
   -> existing agent loop
   -> existing provider stream contract
   -> existing agent reducer/session persistence
   -> session event collaborator
   -> TUI view controller
```

The current sync-to-async event adaptation remains unchanged. Python's documented
`asyncio.to_thread` behavior confirms that blocking I/O can be bridged this way, while
the measured per-event overhead makes a single-producer stream bridge a plausible later
optimization. Because that optimization would affect cancellation and event ordering,
it is explicitly excluded and requires a separate benchmark-backed design.

## Error handling

- Normalize invalid compatibility options once at construction boundaries and fail with
  typed, actionable errors.
- Do not partially install session or TUI collaborator graphs. Build and validate a
  candidate before exposing it.
- Preserve the active session if replacement construction or rebinding fails.
- Make collaborator `close`/`dispose` operations idempotent and preserve the current
  shutdown order.
- Route controller exceptions through the existing session/TUI diagnostic mechanisms;
  leaf controllers do not exit the process.
- Provider errors retain current user-facing categories and redact credentials before
  formatting or fixture capture.
- Bounded catalog reads report an ordinary provider diagnostic and retain the previous
  catalog snapshot.
- Architecture, lint, type, or evidence failures fail CI; they are not converted into
  advisory success.

## Quality and verification architecture

### Regression-first rule

Every confirmed bug starts with a test that fails for the expected reason. The
implementation agent records the red test command and failure, applies the smallest
fix, runs the focused green test, then runs its phase suite. Pure moves with no behavior
change use characterization tests written before the move.

### Static analysis rollout

Pyright has no assumed repository-wide baseline suppression in this design. Rollout uses
explicit execution environments and include scopes:

1. New contract modules and the macOS PATH fix are strict first.
2. Each extracted session/TUI/provider family joins the checked scope when migrated.
3. The scope only expands; it never drops a migrated module to regain green status.
4. Public annotations must pass clean `get_type_hints` checks.

Ruff begins with repository-wide syntax and undefined-name rules, then enforces the
normal rule set on new and migrated modules. Compatibility re-exports use explicit
`__all__`, not blanket unused-import suppression.

### Architecture gates

Tests enforce:

- no new star imports;
- no collaborator-to-façade imports;
- no cycles between session construction owners;
- no cycles between provider contracts/profiles and concrete transports;
- bounded module size and façade method growth;
- no new dynamic-only public façade members;
- no new high-complexity functions in migrated modules;
- no change to the protected loop file;
- deterministic provider and command registries.

### Coverage

The first CI coverage gate records the reproducible clean baseline without temporary
audit artifacts. Statement and branch metrics are reported separately. The gate starts
at or below the verified baseline, then ratchets upward only when a phase actually adds
covered behavior. A phase may not reduce coverage in its owned modules.

The MCP adapter source suite and its coverage run independently from the root package so
an installed copy cannot create a false pass.

### Acceptance evidence

Acceptance rows distinguish automated-required, live-required, manual, and informational
evidence. CI uses strict current-commit verification. A missing or stale required record
fails; blocked external evidence is reported as blocked rather than passed. Release-only
evidence does not make ordinary source CI green.

## Performance design

Performance work is evidence-driven and behavior-preserving:

- Replace eager root-package exports with lazy compatibility exports where import
  behavior can be proven equivalent.
- Parse help and version requests before loading extensions or constructing application
  services, while preserving extension-aware help when explicitly requested by the
  current CLI contract.
- Bound provider model refresh concurrency and response bytes.
- Avoid new threads, queues, or locks on the active event path.
- Do not batch operation-journal writes unless a separate crash-consistency test proves
  identical durability.

Phase 5 records cold CLI import, `--help`, installed-extension help, peak RSS, provider
refresh latency, and the existing mixed runtime benchmark. Required outcome: no metric
regresses by more than 10% without an explained environment variance, and at least one
identified startup bottleneck improves materially. A 20% median improvement is the
target, not a reason to weaken correctness.

## Dead and dormant code policy

Unreferenced code is classified before deletion:

- **Dead:** no supported caller, no serialized compatibility role, and no planned owner;
  remove with import/API tests.
- **Compatibility:** externally importable or serialized; retain through explicit export
  and document the owner.
- **Dormant:** intentionally not invoked automatically for safety; document why and add
  an explicit ownership test or roadmap reference.

Known dead candidates include `oauth_credential_is_expired`, `ModelRegistryLike`,
`ProviderTransport`, `ProviderResponse`, and `openrouter_min_coding_score`; each is
revalidated after provider extraction before removal.

`ArtifactGarbageCollector` is not wired into shutdown or startup. Automatic collection
could delete user data and is outside this refactor's authority. It remains only if the
repository documents a future explicit user-invoked retention workflow; otherwise the
unused implementation and its tests are removed. No implicit garbage collection is
introduced.

Generated or mirrored skill resources keep one canonical source and a deterministic
sync/check command. CI fails when checked-in mirrors diverge.

## Packaging, CI, and documentation

- Commit and maintain `uv.lock` for development and CI resolution.
- CI installs with `uv sync --locked --all-extras --dev` or the repository's equivalent
  locked dependency groups.
- Root and adapter artifacts are built with `uv build` in their actual package roots.
- CI validates wheels and source distributions, installs wheels in clean environments,
  and runs npm launcher tests and dry-pack checks.
- Pull requests and pushes receive source CI. Release publishing remains a separately
  authorized workflow.
- Container build and smoke run only at the final program gate, after all design phases
  are implemented, consistent with the repository's current test policy.
- `README.md`, `rules.md`, architecture documents, acceptance records, and release
  instructions name paths and commands that exist in the committed tree.
- The local ignored oh-my-pi research clone remains ignored and is neither packaged nor
  tracked.

## Delivery phases

### Phase 0 — Truthful guardrails and confirmed regression

Deliverables:

- characterization tests for protected session, TUI, provider, and façade behavior;
- protected-loop diff and focused invariant gates;
- failing macOS alias-path regression, minimal canonical-path fix, and focused tests;
- clean coverage configuration without audit-artifact contamination;
- repository-wide undefined-name/syntax lint gate;
- strict current-commit acceptance semantics for CI;
- ordinary pull-request/push CI skeleton;
- baseline measurements captured in a durable verification record.

Rollback boundary: one guardrail/bugfix commit series; no composition extraction.

### Phase 1 — Contracts and composition shell

Deliverables:

- session bootstrap/dependency/controller records;
- narrow static protocols for cycle breaks and test seams;
- explicit supported façade inventory and compatibility tests;
- types-only session contract modules;
- removal of the session construction cycle;
- strict type scope for new contracts;
- no runtime behavior moved yet except mechanical option normalization proven by
  characterization tests.

Rollback boundary: new contracts can be removed without changing the old runtime.

### Phase 2 — Session and TUI collaborator extraction

Deliverables:

- replace `_SessionRuntime` mixin inheritance one domain at a time;
- replace `_InteractiveRuntime` mixin inheritance one controller at a time;
- explicit controller dependencies and command registration;
- remove star imports while preserving compatibility exports;
- preserve replacement, shutdown, extension, process, subagent, LSP, memory, and
  operation behavior;
- wheel-based TUI scenario evidence after every extraction slice.

Suggested extraction order follows lowest coupling first: display/view and static
commands, model parameters/auth, process/LSP/memory/operation adapters, persistence and
models, tools/extensions, then turn/session lifecycle. Turn orchestration moves last and
still calls the unchanged `Agent` interface.

Rollback boundary: one collaborator domain per commit group.

### Phase 3 — Provider ownership and wire isolation

Deliverables:

- leaf provider contracts and profile ownership;
- transport registry with compatibility exports;
- one module family per provider API mode;
- sanitized wire golden tests before each family move;
- removal of provider import cycles;
- bounded catalog reads and refresh concurrency;
- removal or explicit compatibility ownership of dead provider symbols;
- strict type and focused coverage scope for migrated families.

Rollback boundary: one provider family per commit group; registry can point a family
back to its prior implementation during migration.

### Phase 4 — Repository-wide quality convergence

Deliverables:

- expand Pyright and Ruff scopes to every migrated owner;
- resolve public `get_type_hints` failures;
- reduce high-complexity functions through tested pure helpers;
- classify/remove dead and orphan code;
- canonicalize mirrored resources and add drift checks;
- ratchet statement/branch coverage;
- make acceptance and verification records current and enforceable.

Rollback boundary: separate commits for typing, complexity, dead code, and generated
resource ownership; no mixed behavioral cleanup.

### Phase 5 — Performance, packaging, docs, and final qualification

Deliverables:

- lazy import/help improvements with benchmarks;
- locked uv workflow and reproducible package commands;
- root, adapter, npm, and clean-wheel verification;
- comprehensive wheel-based TUI scenarios, including 21 normal-user prompts focused on
  refactored behavior;
- final container build and smoke, but no publication;
- README and architecture documentation reconciliation;
- final blast-radius report and protected-loop diff proof.

Rollback boundary: performance changes remain separate from packaging/docs changes.

## Phase verification ladder

Every phase must pass, in order:

1. The intended new test fails for the intended reason.
2. Focused tests for the changed owner pass.
3. Architecture, type, lint, and coverage gates for the phase pass.
4. Root Python suite passes.
5. Relevant adapter suite passes when shared packaging or MCP behavior is touched.
6. An actual built wheel passes the phase's normal-user TUI scenarios.
7. npm launcher tests and dry-pack pass when CLI/package surfaces are touched.
8. Package builds and metadata validation pass.
9. The protected agent-loop diff is empty.

Only the final Phase 5 gate adds the relevant container build and smoke. Publication,
GitHub release creation, GHCR promotion, PyPI upload, and npm publish require a separate
explicit user approval.

## Blast radius

Estimated total tracked-file impact remains 60–100 files across the full program:

| Area | Estimated files | Runtime risk | Agent loop |
|---|---:|---|---|
| Guardrails and confirmed bug | 8–15 | Low | Untouched |
| Session contracts/composition | 18–28 | Medium-high | Untouched |
| TUI composition | 15–24 | Medium-high | Untouched |
| Provider isolation | 20–32 | High wire risk | Untouched |
| Quality/dead code/performance | 12–24 | Medium | Untouched |
| CI, packaging, docs | 8–16 | Low runtime risk | Untouched |

Counts overlap because tests and architecture gates cover multiple phases. The dangerous
areas are session lifecycle binding, TUI command dispatch, and provider wire encoding—not
the generic loop. Risk is controlled through characterization fixtures and domain-sized
rollback units.

## Tradeoffs and risk controls

### Temporary dual architecture

Keeping compatibility forwarding while adding explicit delegation creates temporary
duplication. The mitigation is an explicit façade inventory and a ban on new dynamic-only
members. Removing the bridge is a future API-version decision, not hidden cleanup.

### Protocol ceremony

Protocols can become nominal interfaces in disguise. The mitigation is to require a
real substitute, test seam, or cycle break for each protocol. Concrete internal helpers
remain concrete.

### Characterization tests preserving bugs

Golden tests may freeze accidental behavior. Confirmed defects receive a separate red
regression and documented corrected expectation. Other behavior remains fixed until a
product change is explicitly approved.

### Provider fixture drift

Provider APIs evolve. Golden fixtures test Travis234's emitted/accepted contract, while
live smoke tests verify selected real providers when credentials are explicitly supplied.
Fixtures contain no secrets and do not replace live compatibility checks.

### Incremental typing blind spots

Scoped Pyright leaves untouched areas dynamic temporarily. The checked scope is monotonic,
and architecture boundaries prevent checked modules from depending on unchecked façades
through `Any`.

### One-agent execution fatigue

A monolithic implementation assignment would recreate the review risk this design seeks
to remove. The program uses one shared worktree and master plan, but implementation is
dispatched in bounded phase tasks with a fresh context and a review checkpoint at every
phase. No agent receives authority to skip red tests or continue past a failed phase gate.

### Artifact retention

Automatically activating a dormant collector would create deletion risk under
`~/.travis234`. This design explicitly refuses that shortcut.

## Self-review outcomes

The design was reviewed against audit evidence, repository rules, Superpowers workflow,
and current Python/Pyright/uv documentation. The review changed the initial proposal in
these ways:

1. **Kept `RuntimeFacade` as a compatibility bridge.** Immediate removal was judged too
   likely to break extensions and tests that use dynamic attributes.
2. **Split the work into five executable phases plus Phase 0.** One giant plan was judged
   unreviewable and difficult to roll back.
3. **Moved truthful gates before extraction.** Otherwise architecture regressions could
   be introduced before CI knew how to detect them.
4. **Excluded sync-stream optimization.** Its performance opportunity is real, but it
   crosses the protected cancellation/event-ordering boundary.
5. **Rejected automatic artifact GC.** Orphan cleanup does not authorize deletion of user
   state.
6. **Selected scoped Pyright rollout.** Current documentation supports execution
   environments and rule configuration; the design does not assume a nonexistent global
   error-baseline feature.
7. **Required installed-wheel TUI tests after each behavioral phase.** Direct unit tests
   cannot reveal packaging, resource, terminal, or normal-user discovery failures.
8. **Separated publication from qualification.** Passing local and container gates does
   not authorize release-side effects.

No unresolved design issue requires changing the protected agent loop.

## Acceptance criteria

The program is complete only when:

- `_SessionRuntime` and `_InteractiveRuntime` no longer use behavior mixin inheritance.
- Their collaborators have explicit typed dependencies and obey ownership boundaries.
- Existing public façade use remains compatible, with explicit supported-surface tests.
- Session and provider import cycles are removed.
- Provider transports are split into bounded family owners with sanitized golden tests.
- The macOS path-alias regression passes.
- Public type hints resolve; migrated modules pass their strict Pyright scope.
- Repository-wide syntax/undefined-name checks and migrated-module Ruff checks pass.
- Coverage and acceptance gates report current, reproducible evidence.
- Root Python, relevant adapter, npm, package, clean-wheel, TUI, and final container gates
  pass.
- README and architecture documentation match real commands and paths.
- The diff for `travis/agent/agent_loop.py` against the reference commit is empty.
- No credential or user-state migration is introduced.
- No package or image has been published without separate approval.

## Documentation references

The design uses current official guidance retrieved through Context7:

- Python 3.13 `Protocol` and structural subtyping:
  <https://docs.python.org/3.13/library/typing.html#typing.Protocol>
- Python 3.13 runtime-checkable protocol limitations:
  <https://docs.python.org/3.13/library/typing.html#typing.runtime_checkable>
- Python 3.13 asynchronous iterators:
  <https://docs.python.org/3.13/reference/datamodel.html#asynchronous-iterators>
- Python 3.13 `asyncio.to_thread`:
  <https://docs.python.org/3.13/library/asyncio-task.html#asyncio.to_thread>
- Pyright configuration, strictness, and execution environments:
  <https://github.com/microsoft/pyright/blob/main/docs/configuration.md>
- Pyright file-level diagnostics:
  <https://github.com/microsoft/pyright/blob/main/docs/comments.md>
- uv GitHub Actions integration and locked sync:
  <https://docs.astral.sh/uv/guides/integration/github/>
- uv Docker integration:
  <https://docs.astral.sh/uv/guides/integration/docker/>
- uv package build workflow:
  <https://docs.astral.sh/uv/guides/package/>
