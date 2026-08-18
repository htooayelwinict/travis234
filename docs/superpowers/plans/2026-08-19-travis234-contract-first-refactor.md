# Travis234 Contract-First Refactor Master Plan

> **For implementation agents:** REQUIRED SUB-SKILLS: use
> `superpowers:executing-plans`, `superpowers:test-driven-development`,
> `superpowers:systematic-debugging` for any unexpected result, and
> `superpowers:verification-before-completion`. Execute phases in order and stop at
> every review checkpoint.

**Status:** Design approved; execution plan self-reviewed and ready for isolated work

**Goal:** Refactor Travis234's session composition, TUI composition, provider ownership,
quality gates, performance, packaging, and documentation while leaving the generic
agent loop and all user-facing contracts behaviorally stable.

**Architecture:** Preserve `AgentSession`, `InteractiveMode`, provider entry points, and
`RuntimeFacade` compatibility. Replace mixin/shared-`self` implementation with explicit
collaborators and typed dependency records. Split provider transports by API family.
Establish truthful gates before moving behavior and qualify every phase through an
installed wheel.

**Tech stack:** Python 3.13, pytest, Pyright, Ruff, coverage.py, uv, the native Travis234
TUI, Node/npm launcher tests, wheel/sdist builds, Docker only at the final gate.

**Design:**
`docs/superpowers/specs/2026-08-19-travis234-contract-first-refactor-design.md`

**Reference commit:** `7838749452b567940bd5b69a715b6184b8f9f13e`

**Protected file SHA-256:**
`travis/agent/agent_loop.py = b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`

## Global constraints

- Product and CLI remain `Travis234` and `travis234`; imports remain under `travis`.
- The assigned worktree is the only implementation tree. Do not edit the main checkout.
- Preserve all user data under `~/.travis234`; use isolated test agent directories.
- Never print, copy, stage, or fixture `.env` values or authentication state.
- Do not edit `travis/agent/agent_loop.py`. Stop and report if a phase appears to require
  it.
- Preserve ordering, iteration budgets, cancellation, steering, follow-ups, continuation,
  source-ordered tool results, and bounded parallel execution.
- Preserve JSONL session compatibility, extension lifecycle order, trust gating, tool
  names, commands, provider IDs, and public façade entry points.
- Generic MCP support and `packages/travis234-mcp-adapter` remain supported.
- Begin every bug fix with a failing regression test. Begin every behavior move with a
  characterization test that passes before the move and after it.
- Use `apply_patch` for edits. Preserve unrelated user changes.
- Keep new collaborators below 750 lines, preferably below 400.
- Commit one bounded task at a time. Do not combine behavior moves with cleanup.
- Do not merge, push, publish, tag, bump versions, change permissions, or promote images.
- Do not build a container until the final Phase 5 gate.

## Plan files and dependency order

1. `2026-08-19-travis234-refactor-phase-0-guardrails.md`
2. `2026-08-19-travis234-refactor-phase-1-contracts.md`
3. `2026-08-19-travis234-refactor-phase-2-session-tui.md`
4. `2026-08-19-travis234-refactor-phase-3-providers.md`
5. `2026-08-19-travis234-refactor-phase-4-quality.md`
6. `2026-08-19-travis234-refactor-phase-5-qualification.md`

Each phase consumes only the verified commit produced by its predecessor. Do not start
a later phase with a red gate or an unreviewed diff.

## Phase ledger

Create `docs/verification/contract-first-refactor.md` in Phase 0. After each task append:

```text
Task:
Commit:
RED command and expected failure:
Focused GREEN command:
Phase suite command:
Installed-wheel TUI scenario:
Protected-loop SHA-256:
Notes/remaining risks:
```

Never record secrets, environment values, raw provider headers, or private filesystem
content. The ledger records commands and summarized outcomes only.

## Baseline commands

Run from the assigned worktree before Task 0.1:

```bash
git status --short --branch
git rev-parse HEAD
shasum -a 256 travis/agent/agent_loop.py
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  -q -p no:cacheprovider tests
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Expected starting evidence:

- clean feature worktree at the planning commit descended from `7838749`;
- protected hash exactly
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`;
- 2,650 root Python tests pass at the audited source baseline;
- 24 npm launcher tests pass;
- npm dry-pack succeeds with the expected package inventory.

If the exact test count differs only because planning-only tests were added, inspect and
record the delta. Any source-test failure stops implementation.

## Phase checkpoint protocol

At the end of every phase:

```bash
git diff --check
git status --short --branch
shasum -a 256 travis/agent/agent_loop.py
git diff --exit-code 7838749452b567940bd5b69a715b6184b8f9f13e \
  -- travis/agent/agent_loop.py
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  -q -p no:cacheprovider tests
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Then build the exact phase wheel to a fresh temporary directory, install it into a fresh
Python 3.13 environment with an isolated agent directory, and run the phase's TUI
scenarios. Delete only the validated temporary directories created by that task.

Report the commit, commands, pass counts, protected hash, TUI outcomes, and unresolved
risks to the coordinating agent. Wait for review before starting the next phase.

## Commit sequence

Use narrow Conventional Commit messages. Expected groups are:

```text
test(refactor): characterize protected runtime contracts
fix(tools): normalize runtime python path aliases
ci: add truthful source qualification
refactor(session): introduce explicit composition contracts
refactor(session): extract persistence and tool collaborators
refactor(tui): extract model and process controllers
refactor(providers): isolate chat completions transport
chore(quality): expand typed and linted ownership
perf(startup): defer application imports for metadata paths
docs: reconcile contract-first refactor behavior
test(qualification): record contract-first final evidence
```

Do not use one commit for an entire phase.

## Program stop conditions

Stop and report instead of improvising if:

- a required change reaches `travis/agent/agent_loop.py`;
- a characterization test reveals that the written invariant is inaccurate;
- an extension depends on a dynamic façade member that cannot be preserved additively;
- session replacement cannot keep the old runtime active after candidate failure;
- a provider family cannot match sanitized golden wire fixtures;
- type convergence requires hiding errors through broad `ignore`, `Any`, or disabled
  diagnostics;
- artifact cleanup would delete user data automatically;
- a TUI scenario requires a live credential that was not explicitly supplied for that
  isolated test;
- a phase has a nondeterministic failure after three root-cause attempts;
- container, publication, remote permissions, or version changes are requested by a
  test or release script before separate authorization.

## Plan self-review outcomes

The executable plan was reviewed against the approved design, repository guidance,
current Context7 references, existing tests, and the actual README TUI protocol. The
review made these corrections before handoff:

1. Root dependency locking moved into Phase 0 so source CI cannot claim reproducibility
   while resolving an untracked dependency graph. Phase 5 validates and exports the same
   lock for distribution/container use.
2. Every phase-level TUI result uses the installed `travis234` console entry in a real
   PTY. Fake terminals and scripted drivers remain unit-test aids only.
3. Provider implementations move under `transport_families/`; creating a package named
   `transports` would collide with the required compatibility module `transports.py`.
4. `RuntimeFacade` remains a compatibility bridge. The plan inventories and explicitly
   delegates supported APIs instead of deleting dynamic compatibility.
5. Automatic artifact collection remains forbidden. Quality work documents the dormant
   explicit maintenance primitive instead of attaching deletion to startup or shutdown.
6. System-prompt, tool-schema, and built-in-skill semantics are snapshot-checked and are
   not changed merely to describe internal refactoring.
7. Container work occurs once, after source, wheel, and actual-TUI qualification.
8. The implementation assignment begins with Phase 0 and stops at its review checkpoint;
   later phases consume only reviewed predecessor commits.
9. Commands use concrete paths or task-owned temporary variables. Abstract executable
   placeholders were removed.
10. The protected loop uses both a pinned SHA-256 and a Git diff gate, so a passing test
    suite cannot conceal an accidental loop edit.

## Program completion gate

The implementation is complete only when every acceptance criterion in the design is
checked, the six phase ledgers are current, all final qualification commands pass, the
protected file hash is unchanged, and the coordinating agent has reviewed the complete
diff. Completion does not authorize merge or release.
