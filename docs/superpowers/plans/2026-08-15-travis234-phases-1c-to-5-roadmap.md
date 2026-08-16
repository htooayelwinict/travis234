# Travis234 Phases 1C–5 Execution Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this roadmap phase-by-phase. Steps use checkbox (`- [ ]`) syntax for tracking. Repository guidance prohibits subagents unless the user explicitly requests them, so execution and review are inline by default.

**Goal:** Deliver the six remaining contract-parity phases in dependency order without changing Travis234's generic agent loop, state root, trust boundary, or bounded execution semantics.

**Architecture:** Each phase is a separately testable stacked branch based on its verified predecessor. Phases introduce focused collaborators around existing owners: durable artifact storage, coding-session tool policy, one bounded LSP service, typed subagent roles and supervision, an observe-only operation journal, then optional memory and MCP lifecycle additions. Core and optional features remain independently disableable.

**Tech Stack:** Python 3.13, pytest, JSONL session sidecars, SQLite, stdio JSON-RPC/LSP, the existing native TUI, npm launcher tests, Python wheel/sdist builds, and the separately packaged MCP Python SDK v2 adapter.

## Global Constraints

- Product and CLI names remain `Travis234` and `travis234`; the Python import package remains `travis`.
- The repository root is the only active application tree; execution uses isolated Git worktrees created from the verified predecessor commit.
- User state remains below `~/.travis234` through the existing agent-directory owner. No alternate state path or migration alias is introduced.
- Credentials never enter tracked files, diagnostics, artifacts, memory, operation records, prompts, or command output.
- Preserve agent-loop event ordering, iteration budgets, cancellation, steering, follow-ups, provider ownership, source-ordered tool-result persistence, and bounded parallel execution.
- Project code, project role definitions, project language-server commands, and behavior-changing project configuration remain trust-gated.
- Generic MCP support remains one bounded `mcp` proxy tool in the separately packaged adapter.
- Every bug fix and behavior change begins with a failing regression or contract test.
- New focused collaborators stay at or below 750 lines and do not import `AgentSession`, `CodingApp`, or TUI façades.
- No version bump, merge, push, publish, tag, permission change, or release promotion is part of these plans.
- Per the user's verification instruction, do not build or smoke a release container after intermediate phases. Run the container gate once, after the selected Phase 5 scope is complete.

---

## Program decomposition

Phase 1A (capability registry) and Phase 1B (model-role router) are the verified code starting point at `ec53c69`. The planning documents are committed on top of that code-only commit in `codex/model-role-router`; Phase 1C branches from that planning HEAD so every isolated worktree contains the executable plans. Before implementation, verify the planning-only delta from `ec53c69` contains only these plan files. Six phase gates remain.

| Order | Phase | Branch | Depends on | Independently disableable boundary |
|---|---|---|---|---|
| 1 | 1C Durable artifacts | `codex/durable-artifacts` | Planning HEAD on Phase 1B code `ec53c69` | Durable promotion; ephemeral registry remains |
| 2 | 1D Uniform tool policy | `codex/uniform-tool-policy` | Verified 1C | `toolPolicy.mode=disabled|audit|enforce` |
| 3 | 2 Bounded LSP | `codex/bounded-lsp` | Verified 1D | No `languageServers` means no server/tool |
| 4 | 3 Typed coordination | `codex/typed-coordination` | Verified Phase 2 | Missing role definitions preserve current subagents |
| 5 | 4 Operation journal | `codex/operation-journal` | Verified Phase 3 | `operations.mode=disabled|observe`; no replay |
| 6 | 5 Optional ecosystem | `codex/optional-ecosystem` | Verified Phase 4 | Memory and MCP additions have separate switches/packages |

The branches are stacked so each phase can consume the prior phase's public contracts without temporary compatibility shims. A failed phase is rolled back by returning to its predecessor branch; it does not trigger reverse edits across earlier verified phases.

## Selected approaches and rejected alternatives

### Storage and persistence

Use content-addressed immutable artifact objects with append-only per-session manifests. Session-local copies were rejected because forks duplicate bytes; retaining temporary spool paths was rejected because resume would expose dead references. Existing JSONL remains conversation history and is not migrated.

### Tool policy

Add declarative effects to coding-agent `ToolDefinition`, run audit-only first, and enforce at the existing session hook after extension argument mutation. Moving policy into `travis/agent` was rejected because it would couple the generic loop to application trust and UI approval.

### LSP

Expose one `lsp` tool and user-managed language-server commands. One tool per LSP method and automatic server installation were rejected because they inflate prompt schemas and supply-chain scope.

### Coordination

Validate agent-role capabilities and configure the existing `SubagentSupervisor`. A second task engine and always-on reviewer were rejected because they duplicate lifecycle state and create surprise token spend.

### Crash awareness

Add a separate SQLite operation journal in observe-only mode. Replacing JSONL or enabling automatic replay immediately was rejected because uncertain external effects cannot safely be inferred from conversation history.

### Optional ecosystem

Ship explicit, opt-in project memory and bounded MCP resource/prompt/reconnect support. Browser and desktop integrations stay optional tools/MCP servers behind policy. OAuth remains outside this scope until a credential-broker design is separately approved. Native code is not planned unless the benchmark gate demonstrates a reproducible Python hot path.

## Cross-phase contracts

| Producer | Contract | Consumers |
|---|---|---|
| 1C | `ArtifactRegistry.promote()` and `ResourceRefResolver` | LSP results, typed subagent artifacts, optional memory exports |
| 1D | `ToolEffects`, `ToolPolicyEngine`, approval broker | LSP mutations, role tool ceilings, memory and MCP tools |
| 2 | `LanguageServiceManager` and preview tokens | Typed reviewer workflows and TUI status |
| 3 | `AgentRoleDefinition`, `SupervisorSnapshot`, control handles | Operation records and optional ecosystem agents |
| 4 | `OperationCoordinator` observe hooks | Final diagnostics and future separately approved replay work |
| 5 | Explicit memory and bounded MCP extensions | Final program acceptance only |

Later phases consume only these named contracts. They must not reach into predecessor private dictionaries, SQLite connections, manifest internals, or TUI component state.

## Phase execution gates

### Phase 1C

- [ ] Verify `ec53c69...codex/model-role-router` contains planning documents only, then execute `2026-08-15-travis234-durable-artifacts.md` from `codex/model-role-router` in a new worktree.
- [ ] Prove promotion, resume, fork, corruption, limits, concurrency, and fail-closed garbage collection.
- [ ] Run focused and full Python tests, npm tests, Python package builds, and installed-wheel TUI artifact read/resume.
- [ ] Record the verified commit as the only base for Phase 1D.

### Phase 1D

- [ ] Execute `2026-08-15-travis234-uniform-tool-policy.md` from verified 1C.
- [ ] Ship audit mode first; prove it does not alter tool scheduling, ordering, or outcomes.
- [ ] Enable enforcement only in explicit tests/settings; verify TUI approval and non-interactive denial.
- [ ] Run focused and full non-container qualification and record the verified commit.

### Phase 2

- [ ] Execute `2026-08-15-travis234-bounded-lsp.md` from verified 1D.
- [ ] Use deterministic fixture language servers; do not require global Pyright/TypeScript installation for tests.
- [ ] Prove preview/apply races, rollback reporting, process shutdown, output bounds, and artifact spill.
- [ ] Run installed-wheel native-TUI LSP navigation and reviewed rename scenarios.

### Phase 3

- [ ] Execute `2026-08-15-travis234-typed-coordination.md` from verified Phase 2.
- [ ] Preserve existing max depth and parallelism until capacity evidence says otherwise.
- [ ] Prove role schema, effect ceilings, structured results, steering/cancellation, and TUI owner-thread behavior.
- [ ] Run one installed-wheel TUI worker/reviewer supervision session.

### Phase 4

- [ ] Execute `2026-08-15-travis234-operation-journal.md` from verified Phase 3.
- [ ] Keep recovery observe-only and all automatic replay disabled.
- [ ] Fault-inject before intent, after intent, during effect, after effect, and after settlement.
- [ ] Prove journal failure cannot corrupt or suppress JSONL conversation persistence.

### Phase 5

- [ ] Execute `2026-08-15-travis234-optional-ecosystem.md` from verified Phase 4.
- [ ] Keep memory disabled by default and explicit; do not auto-retain or auto-inject facts.
- [ ] Preserve one MCP proxy while adding only bounded resources, prompts, status, and reconnect behavior.
- [ ] Prove optional browser/desktop tools cannot bypass Phase 1D policy.
- [ ] Run the native-acceleration benchmark gate; add no native dependency when thresholds are not met.

## Verification policy

Every phase runs:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest tests -q
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
uv build --clear --out-dir "$(mktemp -d /tmp/travis234-phase.XXXXXX)" .
/Users/htooayelwin/orca/travis234/.venv/bin/python scripts/verify_acceptance.py --parity-json
```

When a phase touches the MCP adapter, also run its complete suite and build its wheel/sdist. Each phase installs its exact root wheel into an isolated Python 3.13 environment and performs the phase-specific TUI scenario. Dotenv use is limited to the established `--dotenv` boundary and its values are never printed.

After Phase 5 only, run the deferred final container gate:

```bash
docker build --no-cache -f Dockerfile.release -t travis234:contract-parity .
/Users/htooayelwin/orca/travis234/.venv/bin/python evals/container_smoke.py \
  --image travis234:contract-parity
```

The final container gate must include artifacts across restart, policy enforcement, one fixture LSP server, typed supervision, observe-only operation state, memory disabled/enabled isolation, MCP fixture cleanup, unprivileged user checks, and zero credential forwarding.

Keep the root release image adapter-free. Exercise MCP in a derived, test-only image built from the exact adapter wheel through `packages/travis234-mcp-adapter/Dockerfile.smoke`; do not publish or promote either qualification tag.

## Aggregate blast radius

| Area | 1C | 1D | 2 | 3 | 4 | 5 |
|---|---:|---:|---:|---:|---:|---:|
| `travis/agent` | none | none | none | none | none | none |
| Session collaborators | high | medium | medium | high | high | low |
| Settings/resource loading | medium | medium | high | high | medium | medium |
| Filesystem/process tools | high | high | high | medium | medium | low |
| TUI | low | medium | medium | high | low | medium |
| JSONL format | unchanged | unchanged | unchanged | unchanged | unchanged | unchanged |
| New sidecar/SQLite state | manifest/CAS | none | preview memory only | result artifacts | operations DB | memory DB |
| MCP adapter | none | metadata only | none | none | none | high |
| npm launcher | tests only | tests only | tests only | tests only | tests only | tests only |

## Program stop conditions

Stop execution and report a blocker instead of improvising across these boundaries:

- A phase requires changing `travis/agent/agent_loop.py` ordering or scheduling.
- A project resource would execute before trust resolution.
- A durable artifact, memory record, or operation record would contain raw credentials or unsanitized environments.
- LSP apply would claim crash-proof atomicity across multiple files.
- A typed role would increase the existing supervisor depth/parallel defaults.
- Operation replay would be enabled without a separately approved replay design and adapter-specific idempotency tests.
- MCP work would generate one Travis tool per server method or restore a retired Ghost integration.
- Browser/desktop integration would become a mandatory root dependency.
- A native dependency would be introduced without benchmark evidence and a separate packaging review.

## Final execution handoff

One explicit execution approval may authorize sequential inline implementation of all six plans. Execution still pauses on a stop condition or failed verification gate. Local commits are permitted within the stacked worktrees; merging, pushing, publishing, tagging, versioning, and permission changes remain separate decisions.
