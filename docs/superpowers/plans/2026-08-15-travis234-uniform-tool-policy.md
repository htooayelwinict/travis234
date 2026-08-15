# Travis234 Uniform Tool Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Repository guidance prohibits subagents unless the user explicitly requests them, so implementation and review are inline by default.

**Goal:** Give every coding-session tool a declared effect contract and apply one auditable, session-scoped policy without changing generic agent-loop ordering or making the TUI a runtime dependency.

**Architecture:** Extend `ToolDefinition` with immutable effect metadata, classify built-ins and extension/MCP boundaries conservatively, then compose a `ToolPolicyEngine` into `SessionPolicyController` after extension argument mutation and immediately before execution. Audit mode records decisions without changing outcomes; enforce mode can consume an injected approval broker and keeps grants only for the current session.

**Tech Stack:** Python 3.13 dataclasses and protocols, existing tool hooks and extension lifecycle, native TUI dispatcher, sanitized evaluation events, pytest async contract tests, npm launcher tests, and Python package builds.

## Global Constraints

- Start from the verified Phase 1C commit on branch `codex/uniform-tool-policy`; do not edit the Phase 1C branch in place.
- Do not modify `travis/agent/agent_loop.py`, iteration accounting, cancellation, steering, follow-up, or bounded-parallel scheduling.
- Run extension `before_tool_call` hooks first. Evaluate policy against the final tool name and final mutated arguments.
- Never persist approvals beyond the current `AgentSession`; never place grants in JSONL, settings, or `~/.travis234` files.
- A trusted project may make policy stricter and reduce auto-allowed effects, but cannot disable policy or widen a global/user allow set.
- Treat undeclared effects as an audit diagnostic in audit mode and a denial in enforce mode.
- Do not log raw arguments, environment values, command text, file contents, approval keystrokes, or credentials.
- Default to audit mode so upgrading Travis234 does not introduce surprise prompts or denials.
- Keep the policy engine independent of TUI and façade classes. The TUI satisfies an injected async protocol.
- Begin every behavior change with a failing test and keep new focused collaborators at or below 750 lines.
- Do not build or smoke a container in this phase.

---

### Task 1: Define effects, modes, decisions, and strict settings

**Files:**
- Create: `travis/coding_agent/policy/__init__.py`
- Create: `travis/coding_agent/policy/types.py`
- Modify: `travis/coding_agent/tools/types.py`
- Modify: `travis/coding_agent/settings_manager.py`
- Test: `tests/test_tool_policy_types.py`
- Test: `tests/test_tool_policy_settings.py`

**Interfaces:**
- Add `ToolEffect = Literal["read", "write", "execute", "network"]`.
- Add `ToolPolicyMode = Literal["disabled", "audit", "enforce"]`.
- Add immutable `ToolPolicySettings(mode, auto_allow_effects)` and `ToolPolicyDecision` with stable reason codes.
- Add `ToolDefinition.effects: frozenset[ToolEffect] = frozenset()` and optional `policy_context` callback for source compatibility.
- Add `SettingsManager.get_tool_policy_settings()`; accept only `disabled|audit|enforce` and known effects.

- [ ] **Step 1: Write failing type/default tests**

```python
def test_tool_definition_defaults_to_undeclared_effects():
    tool = ToolDefinition(
        name="legacy", label="Legacy", description="", parameters={}, execute=lambda *_: None
    )
    assert tool.effects == frozenset()

def test_policy_settings_default_to_audit_read():
    settings = SettingsManager.in_memory()
    assert settings.get_tool_policy_settings() == {
        "mode": "audit",
        "autoAllowEffects": ["read"],
    }
```

Also cover duplicate normalization, non-list values, unknown effects, unknown modes, immutability, global-enforce/project-audit staying enforce, and project auto-allow intersection rather than union.

- [ ] **Step 2: Run the tests and observe the contract failure**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_tool_policy_types.py tests/test_tool_policy_settings.py -q
```

Expected: failures for the missing metadata and getter, not unrelated collection errors.

- [ ] **Step 3: Implement the narrow contracts**

Use canonical effect order `read, write, execute, network` when returning settings or diagnostics. Apply mode strictness order `disabled < audit < enforce`; only global/user settings can select `disabled` or widen `autoAllowEffects`, while trusted project settings can raise strictness and intersect the allowed set. Reject malformed configured policy with the existing settings-error mechanism; do not silently widen it. Preserve positional construction compatibility by appending new fields after existing fields.

- [ ] **Step 4: Re-run focused tests and architecture limits**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_tool_policy_types.py tests/test_tool_policy_settings.py \
  tests/test_coding_tools_and_subagents.py -q
wc -l travis/coding_agent/policy/types.py
```

Expected: green and the focused module is below 750 lines.

- [ ] **Step 5: Commit the contract slice**

```bash
git add travis/coding_agent/policy travis/coding_agent/tools/types.py \
  travis/coding_agent/settings_manager.py tests/test_tool_policy_types.py \
  tests/test_tool_policy_settings.py
git commit -m "feat(policy): define coding tool effects"
```

### Task 2: Classify built-ins and require extension metadata

**Files:**
- Modify: `travis/coding_agent/tools/read.py`
- Modify: `travis/coding_agent/tools/ls.py`
- Modify: `travis/coding_agent/tools/find.py`
- Modify: `travis/coding_agent/tools/grep.py`
- Modify: `travis/coding_agent/tools/edit.py`
- Modify: `travis/coding_agent/tools/write.py`
- Modify: `travis/coding_agent/tools/bash.py`
- Modify: `travis/coding_agent/tools/process.py`
- Modify: `travis/coding_agent/tools/tmux.py`
- Modify: `travis/coding_agent/session_subagents.py`
- Modify: `travis/coding_agent/extensions.py`
- Test: `tests/test_tool_effect_inventory.py`
- Test: `tests/test_extension_tool_effects.py`

**Interfaces:**
- Pure inspection tools declare `read`.
- File mutation tools declare `write`.
- Shell/process/tmux entry points conservatively declare all four effects.
- `spawn_subagent` declares all four; list/wait/result declare `read`; cancel declares `execute`.
- Extension tool registration accepts `effects`; omission stays load-compatible but is visibly undeclared.
- Built-ins provide allowlisted `policy_context`: workspace-relative path/action where safe, first executable plus command fingerprint for shell tools, and role/backend without subagent goal. Extension context remains optional and centrally redacted.

- [ ] **Step 1: Add an exact inventory test before changing tools**

```python
EXPECTED = {
    "read": {"read"}, "ls": {"read"}, "find": {"read"}, "grep": {"read"},
    "edit": {"write"}, "write": {"write"},
    "bash": {"read", "write", "execute", "network"},
    "process": {"read", "write", "execute", "network"},
    "tmux": {"read", "write", "execute", "network"},
}
```

Assert exact sets, not subset membership. Add extension tests for declared, unknown, duplicate, and omitted effects. Seed credential/path/command values and prove safe context includes useful action/target metadata without the secret or full shell command.

- [ ] **Step 2: Prove the inventory is red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_tool_effect_inventory.py tests/test_extension_tool_effects.py -q
```

- [ ] **Step 3: Annotate factories and normalize extension declarations**

Keep names, schemas, descriptions, execute functions, and registration order unchanged. Validate extension metadata at load time. An omitted declaration must not prevent audit-mode loading; an unknown declaration must produce the existing shaped extension load error.

- [ ] **Step 4: Verify inventory and registration parity**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_tool_effect_inventory.py tests/test_extension_tool_effects.py \
  tests/test_coding_tools_and_subagents.py tests/test_coding_policy_and_extensions.py -q
```

- [ ] **Step 5: Commit classifications**

```bash
git add travis/coding_agent/tools/read.py travis/coding_agent/tools/ls.py \
  travis/coding_agent/tools/find.py travis/coding_agent/tools/grep.py \
  travis/coding_agent/tools/edit.py travis/coding_agent/tools/write.py \
  travis/coding_agent/tools/bash.py travis/coding_agent/tools/process.py \
  travis/coding_agent/tools/tmux.py travis/coding_agent/session_subagents.py \
  travis/coding_agent/extensions.py tests/test_tool_effect_inventory.py \
  tests/test_extension_tool_effects.py
git commit -m "feat(policy): classify coding tool effects"
```

### Task 3: Implement deterministic decisions and session-only grants

**Files:**
- Create: `travis/coding_agent/policy/engine.py`
- Create: `travis/coding_agent/policy/approval.py`
- Test: `tests/test_tool_policy_engine.py`

**Interfaces:**
- Add `ToolApprovalRequest(tool_name, effects, argument_fingerprint, safe_context, reason_code)`.
- Add async `ToolApprovalBroker.request(request, signal) -> ApprovalResponse` protocol.
- Add `SessionGrantSet` keyed by exact tool name plus exact effect set.
- Add `ToolPolicyEngine.evaluate(tool, arguments) -> ToolPolicyDecision` and `authorize(...)`.
- Stable codes: `policy_disabled`, `auto_allowed`, `session_grant`, `approval_required`, `approval_denied`, `approval_unavailable`, `undeclared_effects`, `approval_cancelled`.

- [ ] **Step 1: Write the decision table as failing parametrized tests**

Cover disabled, audit, enforce, read auto-allow, multi-effect prompt, exact grant reuse, changed effect set, undeclared effects, absent broker, broker denial, cancellation, deterministic SHA-256 argument fingerprints, and centrally redacted/truncated safe context. Assert decisions contain no raw argument values.

- [ ] **Step 2: Run the decision tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_tool_policy_engine.py -q
```

- [ ] **Step 3: Implement policy as pure decisions plus one async authorization edge**

`audit` always returns an allow outcome while retaining the decision it would have made. `enforce` denies undeclared effects and unavailable approval. Support response scopes `once`, `session`, and `deny`; only `session` mutates `SessionGrantSet`. Canonicalize JSON-compatible arguments before hashing and hash a type marker for non-JSON values. Core `policy_context` callbacks expose only allowlisted facts such as workspace-relative path, action, server, or role; apply the central credential redactor and 512-byte cap to every callback result. An absent/failing callback yields no context rather than blocking the tool.

- [ ] **Step 4: Verify cancellation and secret-shaping tests**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_tool_policy_engine.py -q -k 'cancel or fingerprint or secret or grant'
```

- [ ] **Step 5: Commit the engine**

```bash
git add travis/coding_agent/policy/engine.py travis/coding_agent/policy/approval.py \
  tests/test_tool_policy_engine.py
git commit -m "feat(policy): add session-scoped authorization engine"
```

### Task 4: Integrate policy after extension mutation without loop changes

**Files:**
- Modify: `travis/coding_agent/session_policy_controller.py`
- Modify: `travis/coding_agent/agent_session.py`
- Modify: `travis/coding_agent/session_events.py`
- Test: `tests/test_tool_policy_integration.py`
- Test: `tests/coding_agent/test_agent_session_characterization.py`

**Interfaces:**
- `SessionPolicyController` receives policy engine, tool lookup, approval broker, and sanitized decision sink.
- `_before_tool_call` runs extension hook, then policy against the resulting arguments.
- `_after_tool_call` behavior and result ordering remain unchanged.
- Add sanitized event `tool_policy_decision` with tool, sorted effects, mode, allow flag, and reason code only.

- [ ] **Step 1: Capture hook ordering with a failing integration test**

```python
async def test_policy_observes_extension_mutated_arguments():
    extension.before_tool_call = mutate_path
    await session.run_tool("read", {"path": "before"})
    assert broker.requests[0].argument_fingerprint == fingerprint({"path": "after"})
```

Also assert an extension denial short-circuits policy, audit mode preserves the exact result even when the diagnostic sink fails, enforce denial never calls execute, concurrent read tools retain source-ordered results, and steering/cancellation still work.

- [ ] **Step 2: Run the integration tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_tool_policy_integration.py \
  tests/coding_agent/test_agent_session_characterization.py -q
```

- [ ] **Step 3: Wire the collaborator at composition time**

Look up metadata by final tool name and pass the final arguments returned by the extension hook. Shape denial through the existing `BeforeToolCallResult` path. Isolate diagnostic-sink failures so audit mode is behavior-neutral. Do not call the broker from `_after_tool_call`; do not add a lock around the entire batch; do not change the generic loop.

- [ ] **Step 4: Prove ordering and budget parity**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_tool_policy_integration.py tests/test_coding_policy_and_extensions.py \
  tests/test_agent_loop_ordering.py tests/test_agent_loop_parallel.py -q
git diff --exit-code HEAD -- travis/agent/agent_loop.py
```

Use the actual existing agent-loop ordering/parallel test filenames if repository discovery shows a renamed equivalent; do not create duplicate characterization suites.

- [ ] **Step 5: Commit session integration**

```bash
git add travis/coding_agent/session_policy_controller.py \
  travis/coding_agent/agent_session.py travis/coding_agent/session_events.py \
  tests/test_tool_policy_integration.py tests/coding_agent/test_agent_session_characterization.py
git commit -m "feat(policy): enforce effects at the session boundary"
```

### Task 5: Add an injected native-TUI approval broker

**Files:**
- Create: `travis/tui/interactive_tool_approval.py`
- Modify: `travis/tui/interactive_mode.py`
- Modify: `travis/tui/interactive_view.py`
- Modify: `travis/tui/interactive_shutdown.py`
- Test: `tests/tui/test_interactive_tool_approval.py`
- Test: `tests/tui/test_interactive_owner_boundaries.py`

**Interfaces:**
- Add `InteractiveToolApprovalBroker` implementing the policy protocol.
- Render tool name, declared effects, centrally sanitized safe context when available, and a short stable fingerprint.
- Choices are `allow once`, `allow for session`, and `deny`; Escape/cancellation resolve to deny.
- Marshal all component mutation through the existing dispatcher owner thread.

- [ ] **Step 1: Write failing broker lifecycle tests**

Test each response, simultaneous requests queued in arrival order, cancellation before display, cancellation while displayed, an internal-child request labeled with child role/task ID, shutdown with parent and child prompts pending, and an assertion that no raw arguments enter rendered text.

- [ ] **Step 2: Run the TUI tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/tui/test_interactive_tool_approval.py \
  tests/tui/test_interactive_owner_boundaries.py -q
```

- [ ] **Step 3: Implement the broker as a focused TUI collaborator**

Inject it when constructing the session-facing controller. Internal child sessions receive the same app-owned broker with child identity, but keep their own `SessionGrantSet`; no parent/child grant is inherited. Resolve every pending future during shutdown. Non-interactive CLI/RPC sessions pass no broker, so enforce mode returns `approval_unavailable` instead of hanging or reading stdin.

- [ ] **Step 4: Verify native TUI ownership and shutdown**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/tui/test_interactive_tool_approval.py tests/tui/test_interactive_owner_boundaries.py \
  tests/tui/test_interactive_shutdown_characterization.py tests/test_tui_dispatcher.py -q
wc -l travis/tui/interactive_tool_approval.py
```

- [ ] **Step 5: Commit the broker**

```bash
git add travis/tui/interactive_tool_approval.py travis/tui/interactive_mode.py \
  travis/tui/interactive_view.py travis/tui/interactive_shutdown.py \
  tests/tui/test_interactive_tool_approval.py tests/tui/test_interactive_owner_boundaries.py
git commit -m "feat(tui): broker tool policy approvals"
```

### Task 6: Carry effect metadata through the optional MCP adapter

**Files:**
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/extension.py`
- Modify: `packages/travis234-mcp-adapter/tests/test_extension.py`
- Modify: `packages/travis234-mcp-adapter/README.md`

**Interfaces:**
- The one `mcp` proxy declares all four effects because a remote server's method semantics are not locally provable.
- Its safe approval context includes only configured server name and proxy operation, never tool arguments, headers, environment references, or resolved secrets.
- No generated `mcp__server__tool` names are introduced.
- Adapter loading remains compatible with audit-mode hosts and fails clearly on hosts too old to accept effect metadata.

- [ ] **Step 1: Add a failing exact-metadata test**

Assert the adapter registers exactly one proxy tool and its effect set is `{read, write, execute, network}`. Keep existing bounded schema assertions.

- [ ] **Step 2: Run the adapter test red, then add metadata**

```bash
PYTHONPATH=packages/travis234-mcp-adapter:. \
  /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  packages/travis234-mcp-adapter/tests/test_extension.py -q
```

- [ ] **Step 3: Run the complete adapter suite and build**

```bash
PYTHONPATH=packages/travis234-mcp-adapter:. \
  /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  packages/travis234-mcp-adapter/tests -q
adapter_dist=$(mktemp -d /tmp/travis234-policy-adapter.XXXXXX)
uv build --clear --out-dir "$adapter_dist" packages/travis234-mcp-adapter
```

- [ ] **Step 4: Commit adapter compatibility**

```bash
git add packages/travis234-mcp-adapter/travis234_mcp_adapter/extension.py \
  packages/travis234-mcp-adapter/tests/test_extension.py \
  packages/travis234-mcp-adapter/README.md
git commit -m "feat(mcp): declare proxy tool effects"
```

### Task 7: Document policy behavior and add parity acceptance

**Files:**
- Modify: `docs/architecture/contract-parity.md`
- Modify: `docs/settings.md`
- Modify: `README.md`
- Modify: `scripts/verify_acceptance.py`
- Test: `tests/architecture/test_facade_boundaries.py`
- Test: `tests/architecture/test_acceptance_matrix.py`

**Interfaces:**
- Document default audit behavior, exact effect meanings, grant lifetime, non-interactive denial, and trust interaction.
- Acceptance output reports inventory completeness and policy mode without arguments or grants.

- [ ] **Step 1: Add failing acceptance assertions**

Require the parity JSON to expose `toolPolicy.mode`, counts per effect, and an `undeclaredToolCount`. Assert the architecture test rejects imports from `travis.coding_agent.policy` to `travis.tui` or `travis.agent`.

- [ ] **Step 2: Run the documentation/acceptance slice red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/architecture/test_acceptance_matrix.py \
  tests/architecture/test_facade_boundaries.py -q
```

- [ ] **Step 3: Implement sanitized acceptance and explain migration semantics**

Make clear that audit mode diagnoses legacy extension tools, enforce mode denies them, project policy is honored only after project trust, and no grant survives resume/fork/restart.

- [ ] **Step 4: Commit docs and acceptance**

```bash
git add README.md docs/architecture/contract-parity.md docs/settings.md \
  scripts/verify_acceptance.py \
  tests/architecture/test_acceptance_matrix.py \
  tests/architecture/test_facade_boundaries.py
git commit -m "docs: define uniform tool policy contract"
```

### Task 8: Phase 1D repository and installed-wheel qualification

**Files:**
- No source changes expected.

**Interfaces:**
- Consumes: complete Phase 1D branch.
- Produces: fresh non-container evidence and the exact base commit for Phase 2.

- [ ] **Step 1: Run focused policy, ordering, and TUI suites**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_tool_policy_types.py tests/test_tool_policy_settings.py \
  tests/test_tool_effect_inventory.py tests/test_extension_tool_effects.py \
  tests/test_tool_policy_engine.py tests/test_tool_policy_integration.py \
  tests/tui/test_interactive_tool_approval.py -q
```

- [ ] **Step 2: Run complete non-container verification**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest tests -q
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
root_dist=$(mktemp -d /tmp/travis234-phase1d.XXXXXX)
uv build --clear --out-dir "$root_dist" .
/Users/htooayelwin/orca/travis234/.venv/bin/python scripts/verify_acceptance.py --parity-json
```

Run the full adapter suite/build again because its package changed.

- [ ] **Step 3: Run installed-wheel native-TUI policy scenarios**

Install the exact wheel in an isolated Python 3.13 environment. With a dotenv file supplied only through the documented `--dotenv` boundary, prove: audit mode runs a write without prompting and emits a sanitized audit event; enforce mode auto-allows read; write presents allow-once/session/deny; a session grant is reused in that session; resume has no grant; Escape and shutdown deny without hanging. Do not print dotenv values.

- [ ] **Step 4: Audit scope and record the phase gate**

```bash
git diff --check
git diff --exit-code codex/durable-artifacts...HEAD -- \
  travis/agent/agent_loop.py travis/ai/providers
git status --short --branch
```

Do not run a container build or smoke. Record exact test/build/TUI evidence and use the verified `HEAD` as the only Phase 2 base.
