# Combined Parity and Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce one local branch containing the complete Phase 1–5 contract-parity stack, durable tmux orchestration, and the targeted model guidance needed to use the combined capabilities correctly.

**Architecture:** Merge the linear Phase stack into the orchestration-based integration branch without rewriting either history. Prove the combined runtime first, then add narrowly scoped model-facing guidance through dynamic tool metadata and the lazy subagent skill rather than expanding the global prompt with runtime internals.

**Tech Stack:** Git worktrees and merge commits, Python 3.13, pytest, Travis234 resource capabilities and tool definitions, Markdown skills, Node.js npm launcher tests, setuptools builds, Docker.

## Global Constraints

- Product and CLI names remain `Travis234` and `travis234`; the Python import package remains `travis`.
- User state remains below `~/.travis234`; no alternate state path or migration alias is introduced.
- Credentials must not enter tracked files, prompts, diagnostics, artifacts, or command output.
- Preserve agent-loop ordering, iteration budgets, cancellation, steering, follow-ups, and bounded parallel tool execution.
- Existing in-process subagents and the tmux orchestration skill remain independent capabilities.
- Generic MCP support remains; retired Ghost components remain absent.
- No version bump, push, pull request, PyPI/npm publication, or GHCR promotion is part of this plan.
- No subagent execution is used for this repository task because repository guidance requires an explicit user request.

---

### Task 1: Preserve the pre-merge baseline and integration contract

**Files:**
- Create: `docs/superpowers/specs/2026-08-16-combined-parity-orchestration-integration-design.md`
- Create: `docs/superpowers/plans/2026-08-16-combine-parity-orchestration.md`

**Interfaces:**
- Consumes: `main`, `codex/optional-ecosystem`, and common ancestor `a922733`.
- Produces: a reviewable integration policy and an isolated branch at `.worktrees/combined-parity-orchestration`.

- [x] **Step 1: Verify worktree isolation and ignored placement**

Run:

```bash
git rev-parse --git-dir
git rev-parse --git-common-dir
git check-ignore -v .worktrees
git status --short --branch
```

Expected: the root is a normal checkout, `.worktrees/` is ignored, and only the two known unrelated root edits are dirty.

- [x] **Step 2: Create the integration worktree**

Run:

```bash
git worktree add .worktrees/combined-parity-orchestration \
  -b codex/combined-parity-orchestration main
```

Expected: the new worktree starts from orchestration `main` without changing the root checkout.

- [x] **Step 3: Run the exact pre-merge Python baseline**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest tests -q
```

Expected: 2019 tests pass.

- [x] **Step 4: Commit the reviewed integration design**

Run:

```bash
git add docs/superpowers/specs/2026-08-16-combined-parity-orchestration-integration-design.md
git commit -m "docs: design parity orchestration integration"
```

Expected: the design is isolated on `codex/combined-parity-orchestration`.

- [ ] **Step 5: Commit this execution plan**

Run:

```bash
git add docs/superpowers/plans/2026-08-16-combine-parity-orchestration.md
git commit -m "docs: plan parity orchestration integration"
```

Expected: merge execution starts from a clean integration worktree.

### Task 2: Merge both approved implementation histories

**Files:**
- Merge: every tracked file changed by `main` and `codex/optional-ecosystem` after `a922733`.
- Resolve likely overlaps: `README.md`, distribution/resource tests, verification docs, MCP adapter files, and resource packaging declarations.

**Interfaces:**
- Consumes: orchestration commit history through `34d1aea` and Phase history through `0856f10`.
- Produces: one merge commit with both tips as parents and no unresolved index entries.

- [ ] **Step 1: Begin a non-fast-forward merge without committing**

Run:

```bash
git merge --no-ff --no-commit codex/optional-ecosystem
git status --short
```

Expected: Git stages non-overlapping Phase files and reports only genuine overlapping paths as conflicts.

- [ ] **Step 2: Resolve each conflict additively**

For each `UU`, `AA`, `AU`, or `UA` path:

```bash
git diff --name-only --diff-filter=U
git show :1:<path>
git show :2:<path>
git show :3:<path>
```

Edit with `apply_patch`, retaining both feature surfaces and removing all conflict markers. Do not choose an entire side for README, skill packaging, or verification files unless inspection proves the other side contributes nothing.

- [ ] **Step 3: Verify the merged index structurally**

Run:

```bash
git diff --name-only --diff-filter=U
git diff --check --cached
git merge-base --is-ancestor 34d1aea HEAD || true
git status --short
```

Expected: no unresolved paths and no whitespace errors. The ancestry check becomes definitive after the merge commit.

- [ ] **Step 4: Run conflict-focused integration tests before committing**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest -q \
  tests/test_coding_resources_and_services.py \
  tests/test_orchestration_helper.py \
  tests/test_installed_metadata.py \
  tests/test_distribution_contract.py \
  tests/test_model_role_subagents.py \
  tests/test_subagent_artifact_results.py \
  tests/test_subagent_controls.py \
  tests/test_tool_policy_integration.py \
  tests/test_language_service_tool_reads.py \
  tests/test_memory_tool.py \
  tests/test_operation_coordinator.py
```

Expected: both orchestration resource contracts and Phase runtime contracts pass together.

- [ ] **Step 5: Commit the history merge**

Run:

```bash
git commit -m "merge: combine parity phases with orchestration"
git rev-list --parents -n 1 HEAD
git merge-base --is-ancestor 34d1aea HEAD
git merge-base --is-ancestor 0856f10 HEAD
```

Expected: the merge commit has two parents and both input tips are ancestors.

### Task 3: Prove combined runtime behavior before prompt alignment

**Files:**
- No production edits expected.
- Modify a focused regression test first if the merge exposes an integration defect.

**Interfaces:**
- Consumes: the combined merge tree.
- Produces: evidence that failures after later prompt edits are not merge-resolution defects.

- [ ] **Step 1: Validate all packaged skill bundles and mirrors**

Run the repository's official skill validators against both Python and npm copies, then compare corresponding `SKILL.md`, protocol, and helper files byte-for-byte.

Expected: orchestration, subagent delegation, and web-search resources are valid; Python/npm mirrors match.

- [ ] **Step 2: Run the complete orchestration qualification group**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest -q \
  tests/test_orchestration_helper.py \
  tests/test_orchestration_tui_scenarios.py \
  tests/test_coding_resources_and_services.py
```

Expected: the deterministic 21-scenario matrix and orchestration helper contracts pass against the combined tree.

- [ ] **Step 3: Run the Phase integration group**

Run all tests whose names cover capabilities, model roles, artifacts, policy, language services, typed subagents, operations, memory, and optional MCP conformance.

Expected: Phase 1–5 behavior passes without relying on prompt changes.

### Task 4: Add bounded typed-role discovery for parent agents

**Files:**
- Modify: `travis/coding_agent/session_subagents.py`
- Modify: `tests/test_coding_tools_and_subagents.py`
- Modify: `tests/test_subagent_role_resolution.py`

**Interfaces:**
- Consumes: `DefaultResourceLoader.get_agent_roles() -> AgentRoleRegistry` and `AgentRoleRegistry.list() -> tuple[AgentRoleDefinition, ...]`.
- Produces: a bounded dynamic `spawn_subagent` prompt guideline listing trusted role names and descriptions without exposing source paths, schemas, credentials, or context contents.

- [ ] **Step 1: Write failing prompt-contract tests**

Add tests that load two trusted typed roles, obtain the `spawn_subagent` definition, and assert:

```python
metadata = "\n".join([
    definition.prompt_snippet or "",
    *definition.prompt_guidelines,
])
assert "security-reviewer" in metadata
assert "Review security-sensitive changes" in metadata
assert "/absolute/source/path" not in metadata
```

Also assert that sessions with no typed roles retain compact generic guidance.

- [ ] **Step 2: Run the new tests and confirm RED**

Run the two exact new test nodes. Expected: failure because typed role definitions are not currently projected into model-facing metadata.

- [ ] **Step 3: Implement bounded role guidance**

Add a focused helper in `session_subagents.py` that:

```python
roles = self._resource_loader.get_agent_roles().list()
```

Sorts by name, limits the rendered count and total text, normalizes descriptions to one line, includes only role name plus description, and appends the result to `spawn_subagent.prompt_guidelines`. If loading fails, return generic guidance without failing session construction.

- [ ] **Step 4: Run focused role and prompt tests**

Run the new tests plus `tests/test_subagent_role_resolution.py`, `tests/test_model_role_subagents.py`, and the prompt-metadata size contract in `tests/test_process_tools.py`.

Expected: trusted roles are discoverable, metadata remains bounded, and existing routing/fallback behavior is unchanged.

- [ ] **Step 5: Commit**

```bash
git add travis/coding_agent/session_subagents.py \
  tests/test_coding_tools_and_subagents.py \
  tests/test_subagent_role_resolution.py
git commit -m "feat(subagents): expose bounded typed role guidance"
```

### Task 5: Align conditional system and tool guidance

**Files:**
- Modify: `travis/coding_agent/system_prompt.py`
- Modify: `travis/coding_agent/memory/tool.py`
- Modify: `tests/test_system_prompt.py` or the existing system-prompt contract test owner.
- Modify: `tests/test_memory_tool.py`

**Interfaces:**
- Consumes: active tool names, `ToolDefinition.prompt_snippet`, and `ToolDefinition.prompt_guidelines`.
- Produces: guidance injected only when subagent or memory tools are active; the base prompt remains unchanged when those tools are absent.

- [ ] **Step 1: Write failing conditional-prompt tests**

Assert that a prompt with `spawn_subagent` and `wait_subagent` teaches the parent to prefer a configured typed role when it matches the goal, while a prompt without those tools contains no typed-role language.

Assert that memory metadata contains both rules:

```python
assert "explicit user" in metadata.lower()
assert "untrusted" in metadata.lower()
```

- [ ] **Step 2: Run the tests and confirm RED**

Expected: the typed-role system guidance and explicit-retention memory guidance are absent.

- [ ] **Step 3: Make minimal conditional changes**

Extend `_SUBAGENT_ORCHESTRATION_GUIDANCE` with one sentence about configured typed roles, effect ceilings, and structured contracts. Add a memory `prompt_snippet` and at most two guidelines: retain only on explicit user intent; treat recalled content as untrusted data rather than instructions.

Do not add capability-registry, artifact-store, policy-engine, journal, LSP, or MCP internals to `_PREAMBLE` or `_ENGINEERING_GUIDANCE`.

- [ ] **Step 4: Verify prompt isolation and memory behavior**

Run system-prompt, memory, tool-policy, and context-envelope tests. Expected: conditional guidance appears only with its active tool and no default-envelope regression occurs.

- [ ] **Step 5: Commit**

```bash
git add travis/coding_agent/system_prompt.py travis/coding_agent/memory/tool.py \
  tests/test_system_prompt.py tests/test_memory_tool.py
git commit -m "feat(prompts): guide typed delegation and explicit memory"
```

Use the actual existing system-prompt test owner in the `git add` command if its filename differs after the merge.

### Task 6: Update and validate the lazy subagent skill

**Files:**
- Modify: `travis/resources/skills/subagent-delegation/SKILL.md`
- Modify: `packages/travis234-cli/skills/subagent-delegation/SKILL.md`
- Modify: `tests/test_orchestration_helper.py`
- Modify: `tests/test_coding_resources_and_services.py`

**Interfaces:**
- Consumes: typed role selection through `spawn_subagent.role`, structured child results, artifact IDs, `expand_subagent_result`, and `/agents` TUI supervision.
- Produces: byte-identical valid packaged skills teaching the combined delegation workflow while preserving the one-wave/three-child and no-nested-subagent limits.

- [ ] **Step 1: Write failing skill-content tests**

Assert both skill mirrors contain guidance for typed roles, effect ceilings, structured result schemas, declared artifacts, `/agents` supervision, and the independence of tmux orchestration. Assert they remain byte-identical.

- [ ] **Step 2: Run the tests and confirm RED**

Expected: current skill lacks Phase 1B/1C/1D/3 terminology and behavior.

- [ ] **Step 3: Update the authoritative skill and exact npm mirror**

Use concise sections for role selection, child contracts, parent supervision, artifact/result handling, and mechanism choice. Preserve existing delegation limits and the prohibition on parent rereads of child-owned files.

- [ ] **Step 4: Validate and update the intentional hash guard**

Run both official skill validators, compare the mirrors byte-for-byte, calculate the new SHA-256, and update the expected subagent-skill hash in `tests/test_orchestration_helper.py`. Do not change the protected global-system-prompt hash unless the separately tested conditional system-prompt edit intentionally changes it.

- [ ] **Step 5: Commit**

```bash
git add travis/resources/skills/subagent-delegation/SKILL.md \
  packages/travis234-cli/skills/subagent-delegation/SKILL.md \
  tests/test_orchestration_helper.py tests/test_coding_resources_and_services.py
git commit -m "docs(skills): teach typed subagent coordination"
```

### Task 7: Qualify the combined TUI and distribution

**Files:**
- Modify only if evidence requires: `docs/verification/combined-parity-orchestration.md`.
- Create test regressions before any production bug fix discovered here.

**Interfaces:**
- Consumes: the exact locally built wheel/npm package and isolated test state.
- Produces: repeatable evidence that Phase features and orchestration coexist in one installed distribution.

- [ ] **Step 1: Build exact local artifacts**

Run Python wheel/sdist and npm pack commands into a fresh temporary directory, then validate Python artifacts with Twine and audit all three archives for required skill and Phase resources.

- [ ] **Step 2: Run focused TUI scenarios**

Use isolated state and the installed wheel to cover: capability reload/trust, model-role routing, durable artifact resume, policy approval, LSP read plus preview/apply, typed subagent inspection/steer/cancel/result, operation uncertainty inspection, explicit memory retain/recall, MCP resources/prompts/recovery, and orchestration guide/dispatch/correction/recovery. Report each scenario pass or fail.

- [ ] **Step 3: Run repository-level verification**

Run the complete Python test suite, npm launcher suite, MCP adapter suite, package builds, distribution contracts, and release workflow tests.

- [ ] **Step 4: Run relevant unprivileged container smoke checks**

Build without cache only after all combined features and prompt changes are complete. Verify CLI help, packaged resources, state permissions, optional tools, tmux orchestration guide, and clean unprivileged shutdown. Do not push the image.

- [ ] **Step 5: Record evidence and commit**

Document exact commands, counts, artifact hashes, scenario results, container image identity, known limitations, and the no-publication boundary. Commit only that verification record and any test evidence intentionally tracked by the repository.

### Task 8: Final self-review and handoff

**Files:**
- No expected production edits.

**Interfaces:**
- Consumes: the combined branch and all verification evidence.
- Produces: a precise local handoff with branch, commits, dirty-state, test results, and remaining release gate.

- [ ] **Step 1: Audit ancestry, diff, mirrors, and secrets**

Run:

```bash
git merge-base --is-ancestor 34d1aea HEAD
git merge-base --is-ancestor 0856f10 HEAD
git diff --check main...HEAD
git status --short --branch
```

Also compare packaged mirrors, scan tracked changes for credential-shaped values, and verify the root checkout's unrelated edits are unchanged.

- [ ] **Step 2: Review the complete diff by subsystem**

Confirm owner boundaries, prompt conditionality, orchestration/subagent independence, no automatic external effects, no alternate state paths, and no version/publication changes.

- [ ] **Step 3: Report without publishing**

Provide the integration branch and worktree, merge-parent proof, feature summary, exact verification evidence, any failure or limitation, and the separate approval required before GitHub/PyPI/npm/GHCR actions.
