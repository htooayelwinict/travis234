# Travis234 Typed Coordination Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Repository guidance prohibits subagents unless the user explicitly requests them, so implementation and review are inline by default.

**Goal:** Turn existing Travis234 subagents into explicit worker/reviewer roles with capability ceilings, structured results, observable lifecycle, and native-TUI steering/cancellation while retaining the current supervisor and default behavior.

**Architecture:** Load trust-aware JSON role resources into an `AgentRoleRegistry`, resolve an optional role when constructing `SubagentTask`, and intersect role limits with the parent's already-filtered tools and Phase 1D effects. Extend the existing `SubagentSupervisor` with immutable snapshots, subscriptions, and backend control handles instead of adding a second scheduler. Validate optional child output schemas as result data and project declared files into Phase 1C artifact IDs. A focused TUI owner renders and controls the same supervisor.

**Tech Stack:** Python 3.13 dataclasses/protocols, existing package/resource loader, `jsonschema`, Phase 1A capability records, Phase 1B model roles, Phase 1C artifacts, Phase 1D effects, existing thread-bounded supervisor, native TUI dispatcher, pytest, npm, and wheel/sdist builds.

## Global Constraints

- Start from the verified Phase 2 commit on branch `codex/typed-coordination`.
- Preserve `SubagentSupervisor(max_threads=3, max_depth=1)` defaults and existing capacity errors.
- Absence of a matching role definition preserves current task construction and result behavior.
- A role can only narrow the current session's tool catalog, policy effects, model role, timeout, context, and artifact return policy. It cannot re-enable a CLI-excluded or trust-blocked capability.
- Project role definitions load only after project trust. Package roles obey existing package trust and provenance rules.
- `canSpawn` defaults false and cannot defeat the supervisor's current depth-one ceiling.
- Do not add an always-on reviewer, automatic delegation, background token spend, or a second task graph.
- Internal child steering uses the existing `AgentSession.steer` path; unsupported external backends return an explicit capability error.
- Schema validation failure produces a failed `SubagentResult`; it never crashes or corrupts the parent turn.
- TUI mutation occurs only on the dispatcher owner thread, and shutdown settles every subscriber/control future.
- Do not build or smoke a container in this phase.

---

### Task 1: Define and validate agent-role resources

**Files:**
- Create: `travis/coding_agent/agent_roles.py`
- Modify: `travis/coding_agent/package_manager.py`
- Modify: `travis/coding_agent/resource_loader.py`
- Modify: `travis/coding_agent/resource_candidates.py`
- Modify: `travis/coding_agent/settings_manager.py`
- Test: `tests/test_agent_roles.py`
- Test: `tests/test_resource_agent_role_loader.py`

**Interfaces:**
- Add immutable `AgentRoleDefinition` fields: `name`, `description`, `model_role`, optional `allowed_tools`, optional `allowed_effects`, `can_spawn`, `max_depth`, `skills`, `context`, `result_schema`, `default_timeout_seconds`, and `artifact_policy`.
- Supported `modelRole` values are `worker` and `reviewer`; use Phase 1B resolution later.
- Supported `artifactPolicy` values are `none`, `declared`, and `declared_and_trace`.
- Add `AgentRoleRegistry.get(name)` and `list()` as a typed projection over Phase 1A `CapabilityRegistry` records of kind `AGENT_ROLE`; do not introduce a second precedence engine.
- Add `roles` to package `RESOURCE_TYPES`; accept only `.json` files.
- Discover global `~/.travis234/agent/roles/*.json`, trusted-project `.travis234/roles/*.json`, and package-manifest role files through the existing candidate pipeline.

- [ ] **Step 1: Write failing schema, provenance, and trust tests**

```python
def test_role_defaults_are_narrow_and_bounded():
    role = AgentRoleDefinition.from_mapping({"name": "reviewer"}, source=source)
    assert role.model_role == "worker"
    assert role.can_spawn is False
    assert role.max_depth == 1
    assert role.default_timeout_seconds == 1800
```

Cover the 1..3600 timeout range, unique valid names, known effects, string-list fields, valid Draft 2020-12 result schema, 64 KiB serialized schema/32-level nesting limits, unknown keys, invalid JSON, duplicate scope precedence, package manifests, project trust/revocation, failed reload preserving the previous capability snapshot, and diagnostic provenance without file contents.

- [ ] **Step 2: Run role loading tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_agent_roles.py tests/test_resource_agent_role_loader.py -q
```

- [ ] **Step 3: Implement strict role loading**

Load built-in/package/global/project sources through the existing source-info model and publish `CapabilityRecord` candidates so Phase 1A owns precedence, collisions, diagnostics, and atomic reload. Do not execute role files. Reject absolute skill/context file escapes. Normalize effect order and preserve schema as a defensive deep copy.

- [ ] **Step 4: Verify package/resource regressions and size**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_agent_roles.py tests/test_resource_agent_role_loader.py \
  tests/test_resource_extension_loader.py tests/test_package_manager.py -q
wc -l travis/coding_agent/agent_roles.py
```

Use the repository's actual package-manager test filename discovered at execution time if it is consolidated into another existing suite.

- [ ] **Step 5: Commit role resources**

```bash
git add travis/coding_agent/agent_roles.py travis/coding_agent/package_manager.py \
  travis/coding_agent/resource_loader.py travis/coding_agent/resource_candidates.py \
  travis/coding_agent/settings_manager.py \
  tests/test_agent_roles.py tests/test_resource_agent_role_loader.py
git commit -m "feat(subagents): load typed agent roles"
```

### Task 2: Resolve role ceilings into existing subagent tasks

**Files:**
- Create: `travis/coding_agent/subagent_roles.py`
- Modify: `travis/coding_agent/session_subagents.py`
- Modify: `travis/coding_agent/subagents.py`
- Test: `tests/test_subagent_role_resolution.py`
- Test: `tests/test_model_role_subagents.py`

**Interfaces:**
- Add `ResolvedAgentRole` containing final tool tuple, effect ceiling, model selector, context pack, timeout, schema, and artifact policy.
- Add `resolve_agent_role(definition, parent_tools, definitions_by_name, requested_timeout)`.
- Extend `SubagentTask` with optional `role_definition_name`, `allowed_effects`, `result_schema`, and `artifact_policy` while retaining current defaults.
- Resolve `worker`/`reviewer` through the Phase 1B model-role router; explicit parent-authorized task model remains subject to current validation.

- [ ] **Step 1: Write a failing intersection matrix**

Test: role tool list intersected with the parent's active tools; excluded tools never return; role effects filter tools whose declared effects exceed the ceiling; undeclared extension tools denied for typed roles; an omitted tool/effect field inherits the parent ceiling while an explicit empty list grants none; role timeout can lower but not raise an explicit lower timeout; reviewer model resolution; missing role preserving legacy behavior; unknown requested role producing a shaped tool error.

- [ ] **Step 2: Run role resolution tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_subagent_role_resolution.py tests/test_model_role_subagents.py -q
```

- [ ] **Step 3: Implement monotonic narrowing**

Build from the already-active parent tool definitions. A role's empty `allowedTools` means no tools, not all tools. A missing field means inherit the parent ceiling. Reject any typed role tool with missing Phase 1D metadata. Prepend role context/skills to the existing context pack within current prompt bounds and mark source provenance. Freeze the resolved role into the task at spawn; a later resource reload affects new tasks only and cannot widen a running child.

- [ ] **Step 4: Verify old subagent contracts remain green**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_subagent_role_resolution.py tests/test_model_role_subagents.py \
  tests/test_subagents.py tests/test_coding_tools_and_subagents.py -q
```

- [ ] **Step 5: Commit task resolution**

```bash
git add travis/coding_agent/subagent_roles.py travis/coding_agent/session_subagents.py \
  travis/coding_agent/subagents.py tests/test_subagent_role_resolution.py \
  tests/test_model_role_subagents.py
git commit -m "feat(subagents): enforce typed role ceilings"
```

### Task 3: Validate structured child results and promote declared artifacts

**Files:**
- Modify: `travis/coding_agent/subagents.py`
- Modify: `travis/coding_agent/session_subagents.py`
- Modify: `travis/coding_agent/subagent_trace.py`
- Test: `tests/test_subagent_structured_results.py`
- Test: `tests/test_subagent_artifact_results.py`

**Interfaces:**
- Extend `SubagentResult` with `structured_output: object | None` and `validation_errors: list[str]`.
- Define child response envelope keys `summary`, `output`, and `artifacts`; legacy plain text remains valid when no schema is configured.
- Validate `output` with `jsonschema.Draft202012Validator` when a role declares a schema.
- Resolve declared workspace-relative artifact paths through Phase 1C promotion and return only artifact IDs to the parent.

- [ ] **Step 1: Write failing structured-result tests**

Cover valid objects/arrays/scalars, malformed or over-256-KiB JSON envelope, schema mismatch with bounded JSON paths/error count, invalid UTF-8 file, missing file, directory, symlink escape, object/session limit, duplicate artifact bytes, `none` policy ignoring declarations, and `declared_and_trace` promoting a sanitized trace. Assert the parent receives no host artifact path.

- [ ] **Step 2: Run result tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_subagent_structured_results.py tests/test_subagent_artifact_results.py -q
```

- [ ] **Step 3: Implement validation as settlement data**

Perform validation after backend completion and before storing the final supervisor result. On failure set status `failed`, retain a bounded summary, attach validation errors, and do not throw into `wait()`. Promote only files explicitly declared in the envelope and contained in the child's workspace. Sanitize traces using existing trace shaping before promotion.

- [ ] **Step 4: Verify persistence and artifact lifecycle**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_subagent_structured_results.py tests/test_subagent_artifact_results.py \
  tests/test_subagents.py tests/test_durable_artifact_store.py -q
```

- [ ] **Step 5: Commit typed results**

```bash
git add travis/coding_agent/subagents.py travis/coding_agent/session_subagents.py \
  travis/coding_agent/subagent_trace.py tests/test_subagent_structured_results.py \
  tests/test_subagent_artifact_results.py
git commit -m "feat(subagents): validate typed results and artifacts"
```

### Task 4: Add immutable supervisor snapshots and subscriptions

**Files:**
- Create: `travis/coding_agent/subagent_supervision.py`
- Modify: `travis/coding_agent/subagents.py`
- Test: `tests/test_subagent_supervisor_snapshots.py`

**Interfaces:**
- Add immutable `SubagentSnapshot(task_id, role, backend, status, started_at_ms, ended_at_ms, summary_preview, controllable)`.
- Add immutable `SupervisorSnapshot(revision, active_count, capacity, tasks)`.
- Add `SubagentSupervisor.snapshot()` and `subscribe(callback) -> unsubscribe`.
- Emit monotonically increasing revisions for queued, running, completed, failed, timeout, cancelled, and shutdown transitions.

- [ ] **Step 1: Write failing transition/subscriber tests**

Assert exact transition sequences, a late subscriber's initial snapshot, callback exception isolation, unsubscribe, concurrent completions, result-before-snapshot visibility, bounded summary preview, no goal/context leakage, and no callbacks while holding the supervisor lock.

- [ ] **Step 2: Run snapshot tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_subagent_supervisor_snapshots.py -q
```

- [ ] **Step 3: Implement snapshots around the existing scheduler**

Do not replace futures, executor, task maps, or capacity accounting. Capture callbacks under the lock and invoke them after release. Store only bounded previews in snapshots; full results remain available through existing result accessors.

- [ ] **Step 4: Verify race behavior and commit**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_subagent_supervisor_snapshots.py tests/test_subagents.py -q
wc -l travis/coding_agent/subagent_supervision.py travis/coding_agent/subagents.py
git diff --numstat codex/bounded-lsp...HEAD -- travis/coding_agent/subagents.py
git add travis/coding_agent/subagent_supervision.py travis/coding_agent/subagents.py \
  tests/test_subagent_supervisor_snapshots.py
git commit -m "feat(subagents): expose supervisor snapshots"
```

The pre-existing `subagents.py` is already a large compatibility owner, so do not apply the 750-line new-collaborator limit retroactively. Keep its Phase 3 net growth at or below 30 lines by moving snapshot projection/lifecycle logic into `subagent_supervision.py`, which must remain below 750 lines.

### Task 5: Attach control handles for internal steering and cancellation

**Files:**
- Modify: `travis/coding_agent/subagent_supervision.py`
- Modify: `travis/coding_agent/subagents.py`
- Modify: `travis/coding_agent/session_subagents.py`
- Test: `tests/test_subagent_controls.py`

**Interfaces:**
- Add `SubagentControlHandle.steer(message) -> ControlResult` and `.cancel(reason) -> ControlResult` protocol.
- Add supervisor `steer(task_id, message)` and keep existing `cancel(task_id, reason)` semantics.
- Internal backend attaches a handle backed by child `AgentSession.steer` and cancellation signal.
- External backends without steering return code `steering_unsupported`; completed tasks return `task_settled`.

- [ ] **Step 1: Write failing control race tests**

Cover queued, running, between-turn, completed, timed-out, unknown, internal and unsupported external tasks; blank/oversized steering text; steer-vs-cancel race; parent abort; shutdown; backend exception. Assert steering follows the child's established steering queue and does not mutate the parent.

- [ ] **Step 2: Run control tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_subagent_controls.py -q
```

- [ ] **Step 3: Implement handles without changing supervisor bounds**

Register the handle as soon as the internal child session exists and detach it during settlement. Sanitize messages from traces; retain only a fingerprint/length in supervisor events. `cancel` remains idempotent and produces one terminal transition.

- [ ] **Step 4: Verify steering/follow-up invariants**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_subagent_controls.py tests/test_subagents.py \
  tests/test_agent_loop.py tests/test_agent_loop_compatibility.py -q
```

- [ ] **Step 5: Commit controls**

```bash
git add travis/coding_agent/subagent_supervision.py travis/coding_agent/subagents.py \
  travis/coding_agent/session_subagents.py tests/test_subagent_controls.py
git commit -m "feat(subagents): steer and cancel supervised children"
```

### Task 6: Add a live native-TUI subagent roster

**Files:**
- Create: `travis/tui/interactive_subagents.py`
- Create: `travis/tui/components/subagent_roster.py`
- Modify: `travis/tui/components/__init__.py`
- Modify: `travis/tui/interactive_mode.py`
- Modify: `travis/tui/interactive_command_dispatcher.py`
- Modify: `travis/tui/interactive_shutdown.py`
- Modify: `travis/tui/user_commands.py`
- Test: `tests/tui/test_interactive_subagents.py`
- Test: `tests/tui/test_component_owners.py`
- Test: `tests/tui/test_interactive_owner_boundaries.py`

**Interfaces:**
- Preserve the existing `/subagents` prompt-level skill trigger unchanged. Add a separate `/agents` control surface with bounded actions `status`, `inspect <id>`, `steer <id> <message>`, and `cancel <id>`.
- `SubagentRoster` renders role/status/elapsed/control capability from `SupervisorSnapshot` only.
- Full result inspection uses supervisor public APIs and Phase 1C resource references.

- [ ] **Step 1: Write failing render, command, and threading tests**

Test zero/one/three tasks, revision coalescing, terminal width truncation, active-to-terminal transition, unknown IDs, steering unsupported, cancel confirmation, snapshot callback from a worker thread, session switch, and shutdown with active tasks. Assert goals, context packs, raw tool traces, and host paths are absent from roster text.

- [ ] **Step 2: Run TUI tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/tui/test_interactive_subagents.py tests/tui/test_component_owners.py \
  tests/tui/test_interactive_owner_boundaries.py -q
```

- [ ] **Step 3: Implement the focused owner**

Subscribe during TUI initialization, marshal snapshots through `tui.dispatcher`, and unsubscribe before session teardown. Reuse existing command/controller patterns. Do not add subagent lifecycle code to `InteractiveMode`; it remains composition only.

- [ ] **Step 4: Verify TUI shutdown and size limits**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/tui/test_interactive_subagents.py tests/tui/test_interactive_shutdown_characterization.py \
  tests/tui/test_interactive_owner_boundaries.py -q
wc -l travis/tui/interactive_subagents.py travis/tui/components/subagent_roster.py
```

- [ ] **Step 5: Commit the roster**

```bash
git add travis/tui/interactive_subagents.py travis/tui/components/subagent_roster.py \
  travis/tui/components/__init__.py travis/tui/interactive_mode.py \
  travis/tui/interactive_command_dispatcher.py travis/tui/interactive_shutdown.py \
  travis/tui/user_commands.py tests/tui/test_interactive_subagents.py \
  tests/tui/test_component_owners.py tests/tui/test_interactive_owner_boundaries.py
git commit -m "feat(tui): supervise typed subagents"
```

### Task 7: Document typed roles and add acceptance coverage

**Files:**
- Modify: `docs/architecture/contract-parity.md`
- Modify: `docs/settings.md`
- Modify: `README.md`
- Modify: `scripts/verify_acceptance.py`
- Test: `tests/architecture/test_acceptance_matrix.py`
- Test: `tests/architecture/test_facade_boundaries.py`

**Interfaces:**
- Document role JSON fields, precedence/trust, monotonic ceilings, schema failure, artifacts, model roles, capacity, and TUI controls.
- Acceptance reports role names/provenance and supervisor limits, never role context, goals, result bodies, or model credentials.

- [ ] **Step 1: Add failing acceptance and owner assertions**

Require `agentRoles` and `subagentSupervisor` sections in parity JSON. Assert new coordination modules do not import TUI, app/session façades, or generic agent-loop internals.

- [ ] **Step 2: Run acceptance tests red, implement, and verify**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/architecture/test_acceptance_matrix.py \
  tests/architecture/test_facade_boundaries.py -q
```

- [ ] **Step 3: Commit documentation and acceptance**

```bash
git add README.md docs/architecture/contract-parity.md docs/settings.md \
  scripts/verify_acceptance.py tests/architecture/test_acceptance_matrix.py \
  tests/architecture/test_facade_boundaries.py
git commit -m "docs: define typed coordination contract"
```

### Task 8: Phase 3 repository and installed-wheel qualification

**Files:**
- No source changes expected.

**Interfaces:**
- Consumes: complete Phase 3 branch.
- Produces: fresh non-container evidence and the exact base commit for Phase 4.

- [ ] **Step 1: Run complete role/supervisor/TUI slices**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_agent_roles.py tests/test_resource_agent_role_loader.py \
  tests/test_subagent_role_resolution.py tests/test_subagent_structured_results.py \
  tests/test_subagent_artifact_results.py tests/test_subagent_supervisor_snapshots.py \
  tests/test_subagent_controls.py tests/test_subagents.py \
  tests/tui/test_interactive_subagents.py -q
```

- [ ] **Step 2: Run complete non-container qualification**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest tests -q
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
phase3_dist=$(mktemp -d /tmp/travis234-phase3.XXXXXX)
uv build --clear --out-dir "$phase3_dist" .
/Users/htooayelwin/orca/travis234/.venv/bin/python scripts/verify_acceptance.py --parity-json
```

- [ ] **Step 3: Run installed-wheel native-TUI worker/reviewer scenario**

Install the exact wheel in an isolated Python 3.13 environment and use only the documented `--dotenv` boundary. Load one global worker and one trusted-project reviewer role. Spawn both up to the existing capacity, observe roster transitions, steer the internal worker, cancel the reviewer, run a valid structured reviewer result with a declared artifact, read that artifact, prove an invalid schema becomes a failed result, then exit with zero child/process leaks. Do not print dotenv values.

- [ ] **Step 4: Audit scope and record the phase gate**

```bash
git diff --check
git diff --exit-code codex/bounded-lsp...HEAD -- \
  travis/agent/agent_loop.py travis/ai/providers \
  packages/travis234-cli packages/travis234-mcp-adapter
git status --short --branch
```

Do not build or smoke a container. Record exact evidence and use the verified `HEAD` as the only Phase 4 base.
