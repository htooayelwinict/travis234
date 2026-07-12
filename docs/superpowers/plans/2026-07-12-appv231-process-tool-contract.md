# appv231 Process Tool Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the model-facing process schema match runtime behavior and recover common wait/poll argument mistakes without consuming the agent loop.

**Architecture:** Keep the managed-process runtime unchanged. Repair the coding-agent boundary in the process tool, its prompt handoff, its loop-recovery guidance, and focused tests.

**Tech Stack:** Python, JSON Schema, pytest, actual appv231 TUI, OpenRouter Mimo.

## Global Constraints

- Never modify `appV2.3.1/appv231/agent/`.
- Never modify `appV2.3.1/appv231/compaction/`.
- Use TDD for every behavior change.
- Do not change guardrail thresholds, warnings, or blockers.
- Do not publish npm because the launcher package is unchanged.

---

### Task 1: Lock the Model Contract

**Files:**
- Modify: `appV2.3.1/tests/test_process_tools.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/process.py`

**Interfaces:**
- Produces: action-discriminated `PROCESS_SCHEMA` and `prepare_process_arguments`.

- [ ] Add failing tests for valid action branches, missing required fields, mixed fields, camel-case aliases, and wait/poll timing normalization.
- [ ] Run the focused tests and confirm contract failures.
- [ ] Implement the schema and minimal preparer.
- [ ] Run focused tests and confirm they pass.

### Task 2: Make Correct Intent Obvious

**Files:**
- Modify: `appV2.3.1/tests/test_process_tools.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/bash.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/process.py`

**Interfaces:**
- Produces: canonical argument guidance and sanitized process-call rendering.

- [ ] Add failing prompt, handoff, and rendering tests.
- [ ] Run tests and confirm expected failures.
- [ ] Add exact wait/poll shapes and sanitized call metadata.
- [ ] Run focused tests and confirm they pass.

### Task 3: Prove Integration and Publish

**Files:**
- Verify only after Tasks 1 and 2.

**Interfaces:**
- Consumes: repaired coding-agent process boundary.
- Produces: source, TUI, Git, and GHCR evidence.

- [ ] Run process, coding-policy, coding-agent, and AI-validation tests.
- [ ] Run the complete Python suite.
- [ ] Prove no redzone path changed and `git diff --check` passes.
- [ ] Run the actual TUI with Mimo on a 40-second managed command and require one valid wait, zero malformed process calls, terminal exit zero, and clean `/exit`.
- [ ] Commit and push Git.
- [ ] Build and push a no-cache multi-arch production GHCR image.
- [ ] Confirm npm local and registry versions remain equal; do not publish.
