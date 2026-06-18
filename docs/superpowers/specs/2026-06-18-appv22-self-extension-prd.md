# PRD: AppV2.2 Self-Extension Creation and Runtime Reload

Date: 2026-06-18
Status: PRD only; no implementation in this document
Scope: AppV2.2, constrained to Pi-style extension/resource loading and Hermes-style dual context/session governance

## Summary

AppV2.2 has a useful extension foundation, but it cannot yet create a new extension from conversation and reload that extension into the current runtime the way Pi can. Today, AppV2.2 supports static, in-memory Python extension objects registered at service construction time. The UI layer hardcodes supported extension IDs. Runtime continuation persists selected world refs and summaries, but not enough durable session intent, failure lineage, extension resource metadata, or reload lifecycle state to safely support self-extension workflows.

The desired feature is a controlled self-extension pipeline: the agent can draft a new extension package, validate it, register its tools/skills/workflows as resources, and reload them into the active session without losing Pi/Hermes context or executing unreviewed generated code.

## Current Evidence

### Pi design signals

Pi exposes reload as a session command context capability: `reload(): Promise<void>` in `pi/packages/coding-agent/src/core/extensions/types.ts`.

Pi extensions can discover additional resources during startup and reload through `resources_discover`, returning `skillPaths`, `promptPaths`, and `themePaths`.

Pi extension APIs support runtime tool registration via `registerTool(...)`, active tool inspection and filtering through `getActiveTools`, `getAllTools`, and `setActiveTools`, and session lifecycle hooks including `session_start`, `session_before_compact`, `session_compact`, and `session_shutdown`.

Pi's `DefaultResourceLoader` owns resource state and reload. It merges configured, CLI, project, and extension-discovered resources; tracks source metadata; updates skills/prompts/themes from paths; and preserves trust decisions across reload.

### Hermes design signals

Hermes uses a central tool registry with self-registering tool modules, stable snapshots, generation counters, and cache invalidation boundaries. This matters for reload because tool metadata must not mutate under readers without a coherent generation/version model.

Hermes compaction is a dual-pass, dual-timing system: deterministic pruning plus LLM summary; preflight, post-response, overflow recovery, and manual compression triggers. It rotates or maintains session lineage so compacted context remains auditable.

Hermes treats session, memory, tool, and context engines as lifecycle participants. Reload/self-extension cannot be a file-only feature; it needs session hooks, context hooks, cache invalidation, visible status events, and failure isolation.

### AppV2.2 current state

AppV2.2 has these foundations:

- `RuntimeExtension` protocol with skill cards and before/after tool hooks.
- `ExtensionRegistry` for active skill/tool resolution and extension hooks.
- `ToolRegistry` and `ToolBroker` for tool definitions and execution.
- `ContextHarness`, `PromptBuilder`, `AgentContextCompressor`, and TUI context manager for Pi/Hermes-style prompt construction and compaction.
- Session persistence through `.appv22-ui/session.json`.

But the current system is static:

- `create_appv22_services(...)` constructs a new `ExtensionRegistry` and `ToolRegistry` per runtime and registers only extension objects passed by the caller.
- `RuntimeAdapter._runtime(...)` creates a fresh runtime per user turn.
- `create_ui_extensions(...)` supports only `file_management`; any other extension ID raises `ValueError`.
- There is no extension package loader, manifest, resource discovery event, reload operation, registry generation, source metadata, validation/sandbox phase, or session reload event.

## Live Back-And-Forth Findings

A live AppV2.2 chat/TUI probe was run against `plan/appv22-live-self-extension-prd-workspace`.

Scenario:

1. Ask AppV2.2 to inspect the workspace and answer whether it can create/reload extensions, with no implementation.
2. Continue after failure with a human-style follow-up.
3. Switch to the TUI entrypoint and repeat the original question.

Observed faults:

- The first CLI chat turn gathered evidence with `file_management.tree`, `file_management.read_file`, and `file_management.find_files`, then repeatedly proposed `finalize`. The runtime rejected finalization until `max_turns_exceeded` because selected action tools existed even though the request was analysis-only. This is a gate design bug: selected mutation tools must not imply required mutation evidence for non-mutating intent.
- The follow-up turn completed, but said no prior failure was recorded and lost the original question. The persisted session retained world refs, but did not preserve enough active intent and failed-turn lineage for faithful recovery.
- The TUI redraw loop flooded stdout while the provider call was pending. For long background runs, this makes observation and diagnostics hard. Interrupting the TUI marks the UI turn interrupted and ignores late results, but the worker is not actually cancelled.

These faults are directly relevant to self-extension: extension creation/reload will be multi-turn, failure-prone, and often interrupted. It needs durable intent, reload events, cancellable work, and precise action-vs-analysis gating before new extension code is introduced.

## Product Goals

1. Let AppV2.2 create a scoped extension package from conversation without executing unreviewed generated code.
2. Let the current runtime discover and reload approved extension resources without restarting the user workflow.
3. Keep all behavior inside the Pi-style model/tool/result loop.
4. Keep Hermes dual context intact: hot context for current turn state, compacted context for stable memory, durable world refs for evidence.
5. Make reload auditable, reversible, and observable.
6. Avoid domain-specific hardcoding in session files, prompts, or UI extension lists.

## Non-Goals

- Do not implement arbitrary Python code execution from model output without review.
- Do not add a separate hidden planning runtime.
- Do not pivot AppV2.2 into a generic plugin marketplace.
- Do not make session JSON a domain-specific extension database.
- Do not bypass AppV2.2 tool/result evidence contracts.

## User Stories

- As a user, I can ask the agent to draft a new extension for a narrow tool family, and it creates a reviewable package with manifest, skill cards, tool schemas, tests, and docs.
- As a user, I can approve a generated extension and ask AppV2.2 to reload it into the current session.
- As a user, I can ask what changed after reload and see exact extension IDs, tool IDs, skill IDs, source paths, validation status, and reload generation.
- As a user, I can continue a long self-extension workflow after compaction, interruption, or failed validation without the agent losing the original objective.
- As an operator, I can disable, roll back, or inspect extension resources by generation.

## Required Architecture

### 1. Resource loader

Add an AppV2.2 `ResourceLoader` equivalent, not a prompt-only workaround.

Responsibilities:

- Discover extension packages from configured roots.
- Load extension manifests.
- Load skill cards, tool definitions, workflow cards, and optional prompt snippets.
- Attach source metadata to every loaded resource.
- Validate schema and ID collisions before runtime activation.
- Support `reload(reason=...)` and produce a reload result with added, removed, changed, failed, and unchanged resources.

This should mirror Pi's resource loader shape, but stay Python-native.

### 2. Extension package contract

Each generated extension should be a directory with:

- `extension.yaml` manifest.
- `SKILL.md` or structured skill card file.
- `tools.py` or declarative tool schema plus handler adapter.
- `workflows.yaml` for future workflow cards.
- `tests/` for tool and skill activation tests.
- `README.md` with scope, risk, and usage.

The first implementation should support generated skill cards and tool schemas, then handler code only after validation gates are in place.

### 3. Runtime reload lifecycle

Add a reload API at the AppV2.2 service/session boundary:

- `resources_discover(startup|reload)`.
- `before_reload`.
- `reload`.
- `after_reload`.
- `reload_failed`.

Reload must:

- Quiesce the current turn or require idle state.
- Validate and load resources into a candidate generation.
- Swap registries atomically.
- Preserve existing session ID, world refs, summaries, and UI conversation state.
- Emit structured runtime/UI events.
- Keep previous generation available for rollback.

### 4. Registry generation and cache invalidation

AppV2.2 needs a generation-aware registry:

- Tool registry generation increments on register/remove/reload.
- Extension registry generation increments on extension set changes.
- Context selector and prompt builder cache by generation.
- Tool broker executes against a stable snapshot for a turn.
- Session persistence records active generation metadata, not domain payloads.

This follows the Hermes pattern of stable snapshots plus mutation generation.

### 5. Intent and failure lineage

Before self-extension, AppV2.2 must fix live-test continuity gaps:

- Persist active objective, current request, and failed-turn reason separately from `assistant_message`.
- Persist rejected finalize/pause/compact guidance when it affects next-turn recovery.
- Do not let a failed turn overwrite the active objective with a generic failure summary.
- Preserve original user ask across UI compaction as reference-only but recoverable task lineage.

### 6. Action-vs-analysis gating

The runtime must not require mutation evidence just because mutation tools are selected.

Needed distinction:

- Analysis intent: observe/read/search evidence is enough.
- Draft intent: write to an explicit draft path only after user asks for a draft artifact.
- Activation intent: reload only after validation and approval.
- Mutation intent: require action evidence.

This fixes the live fault where an analysis-only question failed because selected action tools existed.

### 7. Validation and safety gates

Generated extensions must pass:

- Manifest schema validation.
- Unique extension/tool/skill/workflow IDs.
- Tool argument/result schema validation.
- Import validation in an isolated candidate loader.
- Static safety checks for filesystem/network/process access.
- Unit tests in `.venv`.
- Optional human approval before activation.

Activation should default to "drafted but inactive" unless the user explicitly asks to reload.

### 8. UI/TUI behavior

The TUI needs operational fixes before long self-extension workflows:

- Do not redraw the full screen every 50 ms when no state changes.
- Display compact event deltas during long provider calls.
- Support cancellable worker/provider calls instead of only ignoring late results.
- Add `/reload`, `/extensions`, `/tools`, and `/rollback-extension` commands.
- Show active extension generation and reload diagnostics.

## Technical Debt To Address First

1. Static UI extension factory.
   - Current behavior only supports `file_management`.
   - Required fix: replace hardcoded factory with resource-loader-backed extension resolution.

2. Per-turn runtime reconstruction.
   - Current UI adapter creates a fresh runtime every call.
   - Required fix: separate session state from runtime construction and carry registry generation across turns.

3. No reload API.
   - Current registries have `register` but no load/unload/reload lifecycle.
   - Required fix: candidate registry build plus atomic swap.

4. No source metadata.
   - Current AppV2.2 tool/skill cards lack Pi-like source info.
   - Required fix: source path, origin, trust level, generation, and validation status for every resource.

5. Weak failure lineage.
   - Live test showed prior failure and original question were not recovered faithfully.
   - Required fix: durable objective/failure lineage in session state.

6. Over-broad mutation gate.
   - Live test showed analysis-only turns can fail because mutation tools are selected.
   - Required fix: classify request intent before enforcing action evidence.

7. TUI redraw and cancellation debt.
   - Live test showed redraw flooding and non-cancellable provider work.
   - Required fix: event-driven redraw and cancellation signal propagation.

## Implementation Plan

### Phase 0: Stabilize current runtime invariants

- Add tests for analysis-only requests when mutation tools are selected.
- Fix finalize gating to depend on request intent, not merely active action tools.
- Persist failed-turn lineage and active objective in session JSON.
- Add event-driven TUI redraw throttle.
- Add cancellation propagation to runtime/provider calls where supported.

Exit criteria:

- Live CLI/TUI back-and-forth can answer analysis-only extension-readiness questions without `max_turns_exceeded`.
- A vague follow-up can recover the original question and prior failure reason.

### Phase 1: Add resource model and loader

- Define `ExtensionManifest`, `ResourceSourceInfo`, `LoadedExtensionResource`, and `ResourceGeneration`.
- Implement filesystem discovery for extension package roots.
- Load skill cards and declarative tool schemas.
- Validate IDs, schemas, and collisions.
- Add diagnostics without activation.

Exit criteria:

- A test extension package can be discovered and validated.
- Invalid packages produce diagnostics and do not affect active tools.

### Phase 2: Add reload lifecycle

- Add `reload_resources(reason)` to AppV2.2 services.
- Build candidate extension/tool registries.
- Swap generation atomically when validation passes.
- Emit reload runtime events.
- Persist active generation metadata in session state.

Exit criteria:

- A new extension package can be added to disk and loaded into the current session by reload.
- Existing world refs and context summaries survive reload.

### Phase 3: Add self-extension drafting tools

- Add tools for `draft_extension_manifest`, `draft_skill_card`, `draft_tool_schema`, and `validate_extension_package`.
- Keep generated code inactive by default.
- Require explicit user approval before activation.

Exit criteria:

- Agent can draft a reviewable extension package from a user request.
- Agent cannot activate generated code without validation and approval.

### Phase 4: Add workflow cards

- Add workflow resource type after tools and skills are stable.
- Workflow cards should be declarative routing/sequence hints, not hidden execution plans.

Exit criteria:

- Workflows influence selected skills/tools while preserving one-loop Pi runtime.

## Test Plan

Unit tests:

- Manifest parsing and schema validation.
- Duplicate extension/tool/skill IDs.
- Candidate registry rollback on validation failure.
- Source metadata propagation.
- Reload generation increments.
- Prompt builder uses new generation.
- Session JSON records generation metadata without domain-specific payloads.

Integration tests:

- Start with only `file_management`.
- Add a local test extension package.
- Reload.
- Verify new skill/tool appears in selected resources.
- Continue prior session and verify world refs remain usable.
- Remove package and reload.
- Verify tool disappears and stale active-tool risks are stripped.

Live tests:

- Multi-turn human-style conversation where the user asks for an extension draft, asks vague follow-ups, changes scope, requests validation, then approves reload.
- Long TUI conversation that forces UI compaction before and after reload.
- Interrupt during validation and continue from the same session.
- Failed extension package reload followed by corrected package reload.

## Acceptance Criteria

- AppV2.2 can discover an extension package created during the current workflow.
- AppV2.2 can validate that package without activating it.
- AppV2.2 can reload approved resources into the current session.
- Reload emits structured evidence and diagnostics.
- Existing world refs, context summary, and UI conversation state survive reload.
- Self-extension stays inside the Pi-style one-loop runtime.
- Compaction preserves reload generation, active objective, and relevant evidence refs.
- No session JSON domain hardcoding is introduced.
- Invalid generated extensions fail closed.

## Recommendation

Implement this as a resource-loader and reload feature, not as more prompt guidance. The current AppV2.2 extension abstraction is good enough to evolve, but the missing layer is Pi's resource discovery/reload boundary plus Hermes-style generation, cache invalidation, lifecycle, and compaction lineage. The safest sequence is to fix the live-test runtime debts first, then add a read-only loader/validator, then add atomic reload, and only then let the agent draft extension packages.
