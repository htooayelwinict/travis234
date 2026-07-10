# appv231 Production Hardening Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the approved appv231 production-hardening design without contaminating the generic agent core or changing the compaction redzone.

**Architecture:** Work is split into six dependency-ordered implementation plans. Each plan produces independently testable software and must pass its local gate before the next plan begins. The coding profile composes policy, providers, persistence, and UI over a domain-neutral agent runtime.

**Tech Stack:** Python 3.13, pytest, asyncio, httpx, Pydantic, JSON Schema, append-only JSONL sessions, differential terminal UI, Docker/Buildx, Node.js test runner.

## Global Constraints

- Do not edit `appV2.3.1/appv231/compaction/`.
- Do not perform mutating git operations: no commit, push, branch, tag, release, or publish. Read-only status and diff checks are permitted.
- Do not use subagents unless Lewis explicitly activates the subagent workflow.
- Use test-driven development: reproduce each defect before implementing its repair.
- Preserve existing CLI behavior and readability of existing session files.
- Keep `appV2.3.1/appv231/agent/` free of coding-agent, TUI, session, compaction-policy, provider-catalog, and named-tool dependencies.
- Do not leave compatibility shims for coding policy inside `appv231.agent`.
- Never read or print secrets while running provider or live-evaluation tests.
- Every stage must leave the full existing suite green.

---

## Plan Set

| Order | Plan | Deliverable | Depends on |
| --- | --- | --- | --- |
| 1 | `2026-07-10-appv231-01-data-safety.md` | Run lease, bounded output spool, compaction adapter/coordinator, recoverable session tail | none |
| 2 | `2026-07-10-appv231-02-core-runtime.md` | Pure awaitable core, serialized events, bounded parallelism, explicit failures | Plan 1 run lease |
| 3 | `2026-07-10-appv231-03-coding-policy.md` | Moved guardrails, typed policies, exact path/artifact capabilities, honest execution backend | Plan 2 typed core outcomes |
| 4 | `2026-07-10-appv231-04-provider-control-plane.md` | Unified model/provider/auth authority, request contracts, cancellation, full schema validation | Plan 2 awaitable/cancellation contract |
| 5 | `2026-07-10-appv231-05-session-tui.md` | Single-writer session persistence and single-owner/coalesced TUI | Plans 1, 2, and 4 |
| 6 | `2026-07-10-appv231-06-quality-release.md` | Deterministic gates, 21-scenario live evaluation, no-cache image smoke gate | Plans 1-5 |

## Specification Coverage

| Proven defect or requirement | Owning task |
| --- | --- |
| Reset permits overlapping runs | Plan 1, Task 1 |
| Unbounded/incomplete/insecure command output | Plan 1, Task 2; Plan 3, Task 4 |
| Persisted rolling summary disappears | Plan 1, Task 3 |
| `/compact` races active run | Plan 1, Task 4 |
| Truncated session tail prevents all recovery | Plan 1, Task 5 |
| Hermes coding policy resides in core | Plan 2, Tasks 1-2 |
| Async hooks/listeners/tools are not awaited | Plan 2, Task 3 |
| Worker threads mutate hooks/events; pool unbounded | Plan 2, Task 4 |
| Immediate blocks invoke after-hook and core parses policy JSON | Plan 2, Task 5 |
| Low-level exceptions look like successful completion | Plan 2, Task 6 |
| Ambiguous hard-stop modes and monolithic guardrails | Plan 3, Task 1 |
| Package consent uses fragile prose and misses absolute executables | Plan 3, Task 2 |
| Relative/symlink/substr path bypasses | Plan 3, Task 3 |
| Advertised full-output artifacts cannot be read safely | Plan 3, Task 4 |
| Bash guard is represented as stronger than its enforcement | Plan 3, Task 5 |
| Write/edit interruption can corrupt a file | Plan 3, Task 6 |
| JSON Schema subset accepts invalid arguments | Plan 4, Task 1 |
| Auth mutation diverges from malformed disk state; OAuth remains stale | Plan 4, Task 2 |
| Competing model/provider/auth authorities | Plan 4, Task 3 |
| Extension provider unregister leaks state; fallback resolvers chain | Plan 4, Tasks 3-4 |
| Saved defaults/cycling can select unavailable models | Plan 4, Task 5 |
| Anthropic auth, cancellation, aliases, dead settings, unsupported Bedrock | Plan 4, Task 6 |
| Session writes are memory-first and unsynchronized | Plan 5, Task 1 |
| Session/TUI commands race turn state | Plan 5, Task 2 |
| TUI renders concurrently from producer threads | Plan 5, Task 3 |
| Remote model discovery blocks UI and duplicates eligibility | Plan 5, Task 4 |
| Output-cap recovery mutates shared model catalog | Plan 5, Task 5 |
| Coding quality is unmeasured | Plan 6, Tasks 1-6 |
| Release image can push without tests/runtime smoke | Plan 6, Tasks 7-8 |
| Compaction redzone must remain unchanged | Every plan gate and roadmap final verification |

## Baseline

- [ ] **Step 1: Confirm the source baseline**

Run:

```bash
git status --short --branch
```

Expected: only previously known untracked artifacts and the new design/plan documents; no tracked source diff.

- [ ] **Step 2: Run the current full suite**

Run:

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests
```

Expected: all existing tests pass before implementation begins.

- [ ] **Step 3: Capture the redzone checksum manifest**

Run:

```bash
find appV2.3.1/appv231/compaction -type f -print0 | sort -z | xargs -0 shasum -a 256
```

Expected: save the command output in the execution notes. The same manifest must match after every plan.

## Stage Gates

### Gate 1: Data Safety

- [ ] Run the focused tests from Plan 1.
- [ ] Confirm output memory remains bounded for at least 10 MiB of command output.
- [ ] Confirm two persisted compactions retain the first summary.
- [ ] Confirm active-turn compaction aborts and awaits without deadlock.
- [ ] Confirm a truncated final JSONL record is quarantined and the valid prefix loads.

### Gate 2: Core Runtime

- [ ] Run the focused tests from Plan 2.
- [ ] Confirm sync and async listeners/hooks/tools are settled exactly once.
- [ ] Confirm all state reduction and events occur on the coordinator thread.
- [ ] Confirm parallel result messages remain in assistant source order.
- [ ] Confirm unexpected low-level exceptions fail the stream visibly.
- [ ] Confirm the core-boundary import test passes.

### Gate 3: Coding Policy

- [ ] Run the focused tests from Plan 3.
- [ ] Confirm `agent/tool_dispatch.py` is absent.
- [ ] Confirm guardrails import only from `coding_agent/policies`.
- [ ] Confirm relative and symlinked file-tool escapes are rejected.
- [ ] Confirm package mutation requires a structured capability.
- [ ] Confirm sandboxed and trusted execution modes are reported honestly.

### Gate 4: Provider Control Plane

- [ ] Run the focused tests from Plan 4.
- [ ] Confirm SDK, CLI, AgentSession, and TUI use one injected control plane.
- [ ] Confirm saved defaults and cycling cannot select unauthenticated models.
- [ ] Confirm extension unload removes exactly its provider registrations.
- [ ] Confirm Anthropic headers and cancellation pass fake-HTTP contracts.
- [ ] Confirm unsupported transport profiles are not advertised.
- [ ] Confirm complete JSON Schema constraints are enforced.

### Gate 5: Session and TUI

- [ ] Run the focused tests from Plan 5.
- [ ] Confirm only the UI-owner thread renders or mutates component state.
- [ ] Confirm render bursts coalesce without dropping terminal states.
- [ ] Confirm `/compact` and model changes use serialized session commands.
- [ ] Confirm remote model loading never blocks the UI-owner loop.
- [ ] Confirm session append failure leaves memory unchanged.

### Gate 6: Quality and Release

- [ ] Run the focused tests from Plan 6.
- [ ] Run all 21 live scenarios through the actual TUI entry point.
- [ ] Record scenario-level evidence without credentials.
- [ ] Build the release image without cache.
- [ ] Verify CLI, Node, npm, and a deterministic TUI workflow as `appv231`.
- [ ] Confirm the release workflow cannot push before all gates pass.

## Final Verification

- [ ] **Step 1: Run all Python tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests
```

Expected: zero failures.

- [ ] **Step 2: Run npm-wrapper tests**

```bash
node --test packages/appv231-cli/test/appv231-cli.test.js
```

Expected: zero failures.

- [ ] **Step 3: Prove core dependency direction**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_agent_core_boundary.py
```

Expected: pass.

- [ ] **Step 4: Prove the redzone is unchanged**

```bash
git diff --exit-code -- appV2.3.1/appv231/compaction
```

Expected: exit code `0` and no output. Re-run the checksum command from Baseline and compare it byte-for-byte.

- [ ] **Step 5: Review tracked scope without changing git state**

```bash
git diff --stat
```

Expected: only files named by the six implementation plans; no release, publish, or unrelated metadata changes beyond the explicit release-gate task.

## Completion Condition

The roadmap is complete only when every plan-level gate and the final verification pass. A green unit suite alone does not satisfy the live-evaluation, image-runtime, redzone, or architecture-boundary requirements.
