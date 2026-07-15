# Travis234 Production-Safe Pi Parity Design

**Status:** Approved direction; implementation authorized without Git operations

**Goal:** Make Travis234 safe as a company-wide default on arbitrary repositories, then deliver broad Pi behavioral parity while preserving Travis234's Python-native runtime, stronger process management, provider-neutral control plane, and Pi-compatible session format.

## Source hierarchy

The three local reference trees have different authority. They are design and test oracles, never runtime dependencies.

1. **Travis234 is authoritative for product identity and preserved invariants.** The repository root is the only active application tree. State remains under `~/.travis234`. The agent-loop ordering, iteration budgets, bounded parallel execution, provider ownership, managed processes, session locking/indexing, and JSONL v3 persistence remain intact.
2. **Pi commit `1f0dbc008c9b3e88017d42e8a1b46d416ad2b6b6` is authoritative for coding-agent behavior.** Trust resolution, extension events, prompt and skill command expansion, themes, package workflows, CLI modes, session commands, and SDK contracts are ported semantically into Python.
3. **Hermes commit `af250d84948179834820a62bfd870c0df6f264a1` is authoritative for context compression.** Full-request pressure, prompt-only real usage, replay-envelope tail sizing, summary boundaries, decaying protected head, cooldowns, and auxiliary-model capacity calibration are ported into the Travis compaction transaction boundary.
4. **`appv231/` is a historical cross-reference only.** It is used to identify inherited behavior and prove regressions. Code is not copied from it when Pi or Hermes has a newer contract.

When sources disagree, this order applies:

- Safety and preserved Travis invariants outrank upstream parity.
- Hermes outranks Pi for compaction pressure and summarization lifecycle.
- Pi outranks Hermes for coding-agent sessions, resources, extension behavior, CLI/TUI, and SDK shape.
- `appv231/` never wins a disagreement.

## Delivery model

The work ships in independent phases. A phase must be usable and verifiable on its own. Production safety is a release gate; broad Pi parity does not delay correcting an unsafe default.

1. Production trust and route-capacity safety
2. Canonical request-envelope accounting
3. Hermes-aligned compaction correctness
4. Python-native extension and resource parity
5. CLI, TUI, and session behavioral parity
6. Public SDK and automation parity
7. Company-wide release qualification

## Preserved invariants

- No alternate state directory or migration alias is introduced.
- Existing Pi-format session JSONL remains readable without migration.
- Compaction entries retain `summary`, `firstKeptEntryId`, and `tokensBefore`.
- The compaction coordinator remains the only durable compaction transaction owner.
- Provider adapters remain outside session and compaction policy.
- Provider credentials never enter model-driven subprocesses unless explicitly allowlisted.
- Tool results remain persisted in source order while lifecycle events may reflect completion order.
- Parallel tool execution remains bounded.
- Project context files may be read without trust, but executable or behavior-changing project resources require trust.
- Python is the native extension language; direct execution of Pi JavaScript extensions is out of scope.
- No implementation step performs `git add`, `git commit`, `git checkout`, `git reset`, `git switch`, `git worktree`, or any equivalent state-changing Git operation.

## Architecture

### 1. Project trust control plane

Create `travis/coding_agent/project_trust.py` as the single owner of project trust policy. It contains:

- `ProjectTrustStore`, persisted at `~/.travis234/agent/trust.json`
- canonical path normalization and nearest-parent decision lookup
- `has_trust_requiring_project_resources(cwd)`
- `resolve_project_trust(...)`
- trust choices for the project, its immediate parent, and session-only decisions

Trust-requiring resources are:

- `.travis234/settings.json`
- `.travis234/extensions/`
- `.travis234/skills/`
- `.travis234/prompts/`
- `.travis234/themes/`
- `.travis234/SYSTEM.md`
- `.travis234/APPEND_SYSTEM.md`
- project or ancestor `.agents/skills/`, excluding the user's global `~/.agents/skills/`

Resolution order is deterministic:

1. `--approve` or `--no-approve` process override
2. a yes/no result from a trusted user/global or explicit CLI extension's `project_trust` handler
3. nearest saved decision in `trust.json`
4. global `defaultProjectTrust`: `always`, `never`, or `ask`
5. interactive prompt when policy is `ask` and a UI exists
6. untrusted when no UI or no decision exists

`DefaultResourceLoader.reload()` becomes a two-pass load:

1. force untrusted settings and load only global/user and explicit CLI extensions
2. resolve trust
3. reload settings and resources with the resolved trust state

Project Python is never imported or executed during the bootstrap pass. `ExtensionContext.is_project_trusted()` reads the active `SettingsManager` value instead of returning a constant. The shared event bus is passed to every `ExtensionRunner`.

The interactive TUI exposes `/trust`. The CLI exposes `-a/--approve` and `-na/--no-approve`. Non-interactive modes never prompt.

### 2. Route-capacity contract

`travis/ai/catalog_generation.py` becomes the canonical OpenRouter live-capability merge. It chooses:

- `top_provider.context_length` before model-level `context_length`
- `top_provider.max_completion_tokens` for the maximum output
- existing catalog values only when route-specific values are absent or invalid

The selected model binding carries the route capacity through model selection, context display, output clamping, and compaction recalibration. Explicit operator overrides remain authoritative.

Generated model parity is checked against the pinned local Pi catalog. The test reports missing IDs, extra IDs, context-window differences, and maximum-output differences separately. Route capacity cannot exceed model architecture capacity, and maximum output cannot silently consume the complete route window.

### 3. Canonical request-envelope accounting

`travis/ai/context_estimate.py` is extended rather than duplicated. It becomes the one estimator used by provider output clamping, compaction pressure, post-compaction telemetry, session context display, and evaluation traces.

It exposes two distinct concepts:

- `calculate_prompt_tokens(usage)`: `input + cache_read + cache_write`; this is the next-request prefix represented by provider usage.
- `calculate_total_tokens(usage)`: provider total, or input, output, and cache components; this is for billing and aggregate statistics only.

`estimate_context_tokens(Context)` returns a componentized immutable value:

- system-prompt tokens
- tool-schema tokens
- message tokens
- tokens sourced from real provider prompt usage
- trailing estimated tokens after the real usage point
- total prompt-envelope tokens
- confidence: `provider_real`, `estimated_full_request`, or `estimated_trailing`

When real usage exists, only tools dynamically added after that usage point are added again. Provider replay metadata, tool-call names and arguments, reasoning fields, images, and serialized tool-result envelopes are included in message estimates.

The post-compaction footer immediately displays a full-request estimate. The next provider response replaces the estimate with prompt-only real usage. A small follow-up must not appear to create the system prompt or tool schemas a second time.

### 4. Compaction policy and lifecycle

Create `travis/compaction/policy.py` so threshold policy is separate from transcript transformation. `CompactionPolicy` receives model ID, context window, maximum output, static envelope estimate, and optional summarizer capacity. It returns:

- effective input window
- trigger tokens
- recent-tail target
- recent-tail soft ceiling
- summary budget
- calibration reason

The default follows current Hermes behavior:

- reserve the selected model's maximum output from the input window
- 50% base threshold
- raise sub-512K windows to 75%
- apply the Hermes small-window fallback when the 64K floor cannot fit
- apply model-specific overrides only when represented by explicit tested policy data
- size the recent tail at 20% of the trigger with the Hermes minimum-message rule

Travis continues supporting route windows below Hermes's agent-level minimum. Such models use the calibrated small-window fallback rather than being rejected solely for being below 64K.

`ContextCompressor` consumes canonical prompt-envelope estimates. Its tail estimator includes complete tool calls, tool results, reasoning/replay fields, and images. The initial `protect_first_n` applies only until a previous compaction summary exists; the system prompt remains protected by request construction rather than fossilizing early user messages.

Summary insertion uses an explicit structured boundary. When role collision requires one physical message, rehydration removes the summary and marker wherever the marker occurs before retained tail text. The previous summary passed to iterative summarization never contains retained user tail content.

Summary failure has one cooldown owner. `should_compress()` respects it, automatic preflight and post-response checks do not rewrite context during cooldown, and manual `/compact` may explicitly clear it. Cooldown and relevant failure state persist in session compaction details.

The deterministic fallback records a recovered historical ask once and labels it non-authoritative. It does not repeat the ask as current state, pending work, and continuation guidance. Recent-user focus is derived separately.

An auxiliary summarizer is validated against its own route capacity. If it cannot accept the middle transcript, the live trigger is lowered before the main request reaches that size. A summarizer below the required minimum is rejected with a diagnostic before repeated overflow calls begin.

### 5. Python-native extension parity

Travis keeps `ExtensionRunner` and adds the four missing Pi emissions:

- `project_trust`
- `session_info_changed`
- `model_select`
- `thinking_level_select`

Event payloads and ordering match the pinned Pi definitions. The shared event bus is injected into every runner so extension-to-extension communication works across reloads. Duplicate command registrations receive stable `name`, `name:1`, `name:2` invocation names rather than overwriting earlier commands.

General extension CLI flags are represented as Python registrations with validation before argument parsing completes. JavaScript extension execution is not added.

### 6. Resources and package workflows

Resource loading is separated into focused modules:

- `project_trust.py`: trust decisions
- `package_manager.py`: configured sources and install/remove/update operations
- `prompt_templates.py`: parsing and expansion
- `skills.py`: discovery, ignore handling, validation, and command generation
- `themes.py`: theme discovery and registration
- `resource_loader.py`: orchestration and cached results

Frontmatter uses safe YAML parsing with a runtime `PyYAML>=6,<7` dependency. Skills honor `.gitignore`, `.ignore`, and `.fdignore`, enforce Pi's 1,024-character description limit, and preserve name validation.

Prompt templates expand before model submission with explicit opt-out for internal calls. When `enableSkillCommands` is true, skills receive deterministic `/skill:<name>` commands. Discovered themes are registered with the TUI, and `/reload` changes effective prompts, skill commands, and themes—not only cached properties.

`DefaultPackageManager` supports Python-native resource packages from local paths, Git URLs, and package indexes. It implements install, remove, update, list, configured sources, scopes, version checks, and ignore rules. Project-scoped changes require resolved project trust. Package subprocesses receive no provider credentials.

### 7. CLI, TUI, and session parity

Parity is delivered in bounded bundles:

1. **Automation modes:** text/print, JSON event stream, and RPC
2. **Execution controls:** tool allow/deny, offline startup, trust flags, and explicit extension/resource paths
3. **Input parity:** `@file`, image arguments, and extension-defined flags
4. **Session UX:** name, fork, clone, tree, switch, import/export, share/copy where a Python-native implementation exists
5. **TUI commands:** `/trust`, resource/package commands, theme selection, and Pi-equivalent hotkeys where the terminal framework supports them

All modes use the same `CodingApp` and `AgentSession` owners. JSON and RPC are presentation transports, not alternate agent loops. Non-interactive modes are fail-closed for trust and deterministic about output framing.

### 8. Public SDK parity

Add a generic Python `AgentHarness` that composes existing owners rather than replacing them. It exposes resources, compaction, branch summaries, session navigation, prompt templates, skills, hooks, and stream options.

The existing synchronous `Models` API gains an async counterpart; synchronous entry points no longer invoke `asyncio.run()` inside a running loop. A `stream_proxy` helper and image-generation model/API registry are added as independent optional surfaces.

SDK parity is behavioral and Pythonic. Exact TypeScript signatures and JavaScript binary compatibility are not goals.

## Data flow

### Startup

1. Parse mode, trust overrides, explicit resources, and provider/model options.
2. Load global settings only.
3. Detect trust-requiring project resources.
4. Load trusted bootstrap extensions.
5. Resolve project trust.
6. Reload project settings and resources only if trusted.
7. Resolve route-specific model capacity.
8. Construct the session, canonical request estimator, and compaction policy.
9. Start the selected UI or automation mode.

### Turn

1. Expand prompt templates, skill commands, `@file`, and image inputs.
2. Emit input and pre-agent extension hooks.
3. Build one `Context` containing the exact system prompt, messages, and tools.
4. Estimate the full prompt envelope.
5. Compact transactionally if pressure requires it and cooldown permits it.
6. Clamp output from the same envelope estimate.
7. Send the provider request.
8. Record prompt-only real usage for context pressure and total usage for billing.
9. Emit ordered lifecycle events and persist session entries.
10. Refresh footer/evaluation telemetry from the canonical authority.

### Compaction

1. Calculate full-request pressure.
2. Ask `CompactionPolicy` for trigger and budgets.
3. Select a role-safe cut point using complete envelope estimates.
4. Validate the summarizer route capacity.
5. Generate or recover a bounded summary.
6. Assemble summary plus tail with a reversible boundary.
7. Persist one Pi-compatible compaction entry through `CompactionCoordinator`.
8. Publish a full-request rough estimate and await real prompt usage.

## Error handling

- Unknown trust fails closed; malformed trust files produce an actionable startup diagnostic and do not trust the project.
- Extension errors are isolated and reported with their source path; project extensions are never imported before trust.
- Invalid route capacity retains the last valid catalog value and reports the invalid source.
- Context estimation failures use bounded conservative estimates and a degraded-confidence label.
- Summary-model overflow triggers calibration or fallback, not an unbounded retry loop.
- Automatic compaction respects persisted cooldown; manual compaction remains an explicit operator action.
- Package operations are transactional: download or clone to a temporary path, validate resources, then replace the installed target.
- JSON/RPC modes return structured errors and never intermix human TUI text.

## Verification strategy

Each defect begins with a failing regression test. Reference parity tests read pinned local source fixtures or generated snapshots without importing Pi, Hermes, or `appv231` at runtime.

Required focused suites include:

- project trust resolution, bootstrap loading, CLI overrides, and no-UI fail-closed behavior
- OpenRouter route-capacity generation and all known catalog differences
- canonical request-envelope component accounting across every provider usage shape
- post-compaction estimate-to-real-usage continuity
- merged-summary second-compaction rehydration
- decaying protected head
- cooldown persistence and no-rewrite behavior
- auxiliary summarizer capacity calibration
- all 33 Pi extension event emissions
- shared event-bus communication
- prompt, skill-command, theme, YAML, and ignore behavior
- package install/remove/update and trust isolation
- text, JSON, and RPC golden streams
- session name/fork/clone/tree behavior
- async SDK operation inside an active event loop

Final release evidence requires:

- focused tests for every phase
- full Python suite
- npm launcher tests and pack dry-run
- Python wheel and sdist builds
- clean installed-entry smoke
- release container build and unprivileged smoke
- untrusted-repository smoke proving project Python does not execute
- provider-faux long-session compaction smoke
- a recorded acceptance-matrix update

## Compatibility and rollout

Existing sessions load unchanged. Corrected context accounting may cause immediate compaction on the next turn when the real system/tool envelope already exceeds the calibrated trigger. This is expected and does not rewrite historical session files outside the normal compaction transaction.

Trust is intentionally behavior-changing. Repositories with project resources prompt once in interactive mode and remain untrusted in non-interactive mode unless policy or flags decide otherwise. Global user extensions continue loading.

The rollout produces two product milestones:

1. **Production-safe core:** trust, route capacity, canonical envelope, and compaction correctness pass company-wide gates.
2. **Broad Pi parity:** Python-native extensions, resources, package workflows, CLI/TUI/session surfaces, and SDK behaviors pass pinned Pi characterization tests.

The core agent loop is not replaced in either milestone.
