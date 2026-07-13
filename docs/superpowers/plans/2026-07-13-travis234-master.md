# Travis234 Rebrand and Hardening Master Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the approved hard-cutover rebrand, all eleven validated fixes, cross-zone preservation checks, and the real 21-prompt TUI acceptance run, then publish the proven result to the public `travis234` repository.

**Architecture:** Execute seven independently testable subsystem plans in dependency order inside one isolated Git worktree. Each behavior change follows strict red/green/refactor, each coherent task is committed, and the final acceptance matrix must prove every explicit requirement from current-state evidence.

**Tech Stack:** Python 3.13 managed by `uv`, pytest, Node.js/npm, Setuptools, Docker CLI, SQLite, PTY evaluation driver, GitHub CLI.

## Global Constraints

- Public repository `htooayelwinict/travis234` already exists and is public.
- Product/distribution/command/config identity is Travis234/`travis234`; Python import/container user identity is `travis`.
- No legacy runtime compatibility aliases, names, environment prefixes, or state paths survive.
- Agent-loop and compaction algorithms are red zones with characterization, differential, and normalized-AST gates.
- Every behavior fix begins with a test observed failing for the intended reason.
- `.env` is edited only after automated implementation passes, remains ignored/mode `0600`, and is never printed or committed.
- Completion requires 21 externally verified prompts in one actual installed-entry TUI session.

---

### Task 1: Create the isolated Python 3.13 execution worktree

**Files:**
- Existing ignore: `.gitignore` contains `.worktrees/`.
- Worktree: `.worktrees/travis234-hardening`
- Branch: `feature/travis234-hardening`

**Interfaces:**
- Produces clean isolated worktree and passing imported baseline.

- [ ] **Step 1: Detect current isolation and verify worktree ignore**

Run:

```bash
GIT_DIR=$(cd "$(git rev-parse --git-dir)" && pwd -P)
GIT_COMMON=$(cd "$(git rev-parse --git-common-dir)" && pwd -P)
git rev-parse --show-superproject-working-tree
git check-ignore -q .worktrees
```

Expected: current root has equal Git/common directories, is not a submodule, and `.worktrees` is ignored.

- [ ] **Step 2: Create the feature worktree**

Run: `git worktree add .worktrees/travis234-hardening -b feature/travis234-hardening`

Expected: worktree is created from the committed plan HEAD.

- [ ] **Step 3: Create Python 3.13 environment and install baseline**

Run:

```bash
cd .worktrees/travis234-hardening
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -e appV2.3.1 pytest build pyyaml packaging
```

Expected: `.venv/bin/python --version` reports Python 3.13 and installation succeeds.

- [ ] **Step 4: Verify the imported baseline**

Run: `PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest appV2.3.1/tests -q`

Expected: all 1,360 imported Python tests pass before implementation.

If the count differs, record the actual collected count and investigate before
any source change; do not waive a baseline failure.

### Task 2: Execute rebrand and packaging plan

**Files:**
- Plan: `docs/superpowers/plans/2026-07-13-travis234-rebrand-packaging.md`

**Interfaces:**
- Produces final root layout/imports/names, installed metadata, canonical launcher, images, AGENTS/skills/session paths, and package artifacts.

- [ ] **Step 1: Execute all six plan tasks in order**

Use strict red/green/commit steps from the linked plan. After moving to the root
layout, reinstall editable package:

Run: `uv pip install --python .venv/bin/python -e . pytest build pyyaml packaging`

Expected: install succeeds under distribution `travis234`, import `travis`.

- [ ] **Step 2: Run rebrand acceptance**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_brand_contract.py tests/test_distribution_contract.py tests/test_installed_metadata.py tests/test_sandbox_launcher.py tests/test_release_workflow.py -q`

Expected: PASS with no forbidden runtime identity.

### Task 3: Execute runtime lifecycle and state plans

**Files:**
- Plan: `docs/superpowers/plans/2026-07-13-process-cancellation-shutdown.md`
- Plan: `docs/superpowers/plans/2026-07-13-provider-session-policy.md`

**Interfaces:**
- Produces process ownership/stdin acknowledgement/Ctrl-C/bounded shutdown fixes, provider isolation, bounded session indexing, and advisory classifier.

- [ ] **Step 1: Execute process plan tasks 1–5**

Run every named red test before its implementation and every focused green suite
afterward. Do not edit agent-loop or compaction files in this step.

- [ ] **Step 2: Execute provider/session/policy tasks 1–6**

Run every named red/green command. Require exact session-index byte/record
counters, not timing-only evidence.

- [ ] **Step 3: Run combined yellow-zone regression**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_process_service.py tests/test_process_tools.py tests/test_tui_user_commands.py tests/test_session_commands.py tests/test_tui_shutdown.py tests/test_provider_control_plane.py tests/test_session_catalog.py tests/test_session_catalog_performance.py tests/policies/test_bash_classification.py -q`

Expected: PASS.

### Task 4: Execute compaction and facade decomposition plans

**Files:**
- Plan: `docs/superpowers/plans/2026-07-13-compaction-orchestration.md`
- Plan: `docs/superpowers/plans/2026-07-13-facade-decomposition.md`

**Interfaces:**
- Produces one public compaction transaction path and small composition facades with behavior parity.

- [ ] **Step 1: Execute compaction tasks 1–5**

Require characterization green before moving orchestration. Run parity and
normalized-AST red-zone gates after migration.

- [ ] **Step 2: Execute facade tasks 1–8**

Extract pure provider/TUI leaves first, then session collaborators, then
interactive controllers. Run owner characterization after every extraction and
enable hard size/dependency gates only when the corresponding facade reaches its
final boundary.

- [ ] **Step 3: Run red-zone cross-check**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_agent_loop.py tests/test_agent_runtime_hardening.py tests/test_compaction.py tests/test_compaction_timing.py tests/test_compaction_integration.py tests/compaction tests/architecture/test_red_zone.py -q`

Expected: PASS with normalized control flow unchanged.

### Task 5: Execute cleanup plan

**Files:**
- Plan: `docs/superpowers/plans/2026-07-13-compatibility-and-test-cleanup.md`

**Interfaces:**
- Produces zero unused dependencies/camel compatibility/duplicate groups and test files no larger than 2,000 lines.

- [ ] **Step 1: Execute cleanup tasks 1–6**

Record pre-split collection, remove callers before aliases, consolidate helpers
without forwarding wrappers, then split tests while preserving node identity.

- [ ] **Step 2: Run hygiene gate**

Run: `PYTHONPATH=. .venv/bin/python scripts/check_repository_hygiene.py`

Expected: exit 0 with an empty report.

### Task 6: Execute full and live acceptance plan

**Files:**
- Plan: `docs/superpowers/plans/2026-07-13-cross-zone-live-acceptance.md`

**Interfaces:**
- Produces complete matrix, full build/install results, migrated ignored `.env`, 21-prompt artifacts, and public `origin/main`.

- [ ] **Step 1: Execute acceptance tasks 1–6**

Do not edit `.env` until focused/full automated checks pass. Run the installed
console entry in the PTY background scenario, poll without exposing credentials,
and require both feature audit and independent fixture verifiers.

- [ ] **Step 2: Review every task commit**

For each commit since the design commit, verify scope, test evidence, no secret,
and no unexpected red-zone algorithm diff. Run `git diff --check` and inspect the
aggregate diff against `d2ede00`.

- [ ] **Step 3: Execute acceptance task 7 and publish**

Finalize verification records, run current-commit audit, fast post-commit gates,
push `feature/travis234-hardening`, fast-forward `main` after review, and push
public `origin/main`. Confirm public visibility/default branch/HEAD.

## Plan self-review coverage

| Requirement | Owning plan/task |
| --- | --- |
| Public remote created first | Completed before implementation; design spec records repository/visibility |
| Complete Travis234/`travis` hard cutover | Rebrand plan Tasks 1–6 |
| AGENTS, skills, and sessions paths | Rebrand plan Tasks 1, 4, and 6 |
| Monitor failure process ownership | Process plan Task 1 |
| Stdin failure acknowledgement | Process plan Task 2 |
| Repeated Ctrl-C escalation | Process plan Task 3 |
| Installed metadata/resources | Rebrand plan Task 3 |
| Bounded shutdown | Process plan Tasks 4–5 |
| God-object decomposition | Facade plan Tasks 1–8 |
| Provider ownership | Provider/session/policy plan Tasks 1–3 |
| Session discovery scaling | Provider/session/policy plan Tasks 4–5 |
| Compaction transaction duplication | Compaction plan Tasks 1–5 |
| Advisory bash classifier | Provider/session/policy plan Task 6 |
| Dependency/compatibility/duplication/test debt | Cleanup plan Tasks 1–6 |
| Red/yellow/green cross-check | Acceptance plan Tasks 1–3 |
| Safe `.env` migration | Acceptance plan Task 4 |
| Actual 21-prompt single-session TUI run | Acceptance plan Tasks 5–6 |
| Public publication and completion audit | Acceptance plan Task 7 |

Self-review found no unowned spec requirement, no placeholder implementation
step, and no conflicting public interface names across the seven subsystem plans.
