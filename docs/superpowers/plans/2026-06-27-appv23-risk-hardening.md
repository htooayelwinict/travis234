# appv23 Risk Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden post-seal appv23 residual risks around subagent delegation, Codex allowed-tool semantics, command-backed provider secrets, and observer diagnostics.

**Architecture:** Keep the changes local to existing seams. Enforce fail-closed policy where the runtime cannot guarantee safety. Preserve existing successful read-only subagent and provider env-template flows.

**Tech Stack:** Python 3.13, pytest, existing appv23 AgentSession/SubagentSupervisor/provider model registry code.

---

### Task 1: Extension subagent safety override rejection

**Files:**
- Modify: `appV2.3/tests/test_coding_agent.py`
- Modify: `appV2.3/appv23/coding_agent/agent_session.py`

- [ ] **Step 1: Write failing tests**

Add tests proving extension subagent calls cannot override `cwd`, `sandbox`, or `allowedTools`.

- [ ] **Step 2: Verify red**

Run: `PYTHONPATH=appV2.3 .venv/bin/python -m pytest appV2.3/tests/test_coding_agent.py::test_extension_spawn_subagent_rejects_safety_overrides -q`

Expected: fail because extension options currently allow these overrides.

- [ ] **Step 3: Implement policy**

In `_build_subagent_task`, reject `cwd`, reject non-`read_only` sandbox, and reject allowed-tools values different from the read-only default.

- [ ] **Step 4: Verify green**

Run the focused test again and expect pass.

### Task 2: Codex allowed-tools fail-closed behavior

**Files:**
- Modify: `appV2.3/tests/test_subagents.py`
- Modify: `appV2.3/appv23/coding_agent/subagents.py`

- [ ] **Step 1: Write failing test**

Add a test proving `CodexExecBackend.run()` returns a failed result and does not invoke the runner when `allowed_tools` is customized beyond the default read-only tuple.

- [ ] **Step 2: Verify red**

Run: `PYTHONPATH=appV2.3 .venv/bin/python -m pytest appV2.3/tests/test_subagents.py::test_codex_exec_backend_rejects_custom_allowed_tools_before_running -q`

Expected: fail because the runner is currently invoked.

- [ ] **Step 3: Implement policy**

Before constructing `codex exec` args, compare `task.allowed_tools` to the default read-only tuple and return `SubagentResult(status="failed")` if different.

- [ ] **Step 4: Verify green**

Run the focused test again and expect pass.

### Task 3: Provider command config without shell

**Files:**
- Modify: `appV2.3/tests/test_ai_models.py`
- Modify: `appV2.3/appv23/ai/models.py`

- [ ] **Step 1: Write failing tests**

Update the command env test to use an argv-safe command and add a no-shell metacharacter test.

- [ ] **Step 2: Verify red**

Run: `PYTHONPATH=appV2.3 .venv/bin/python -m pytest appV2.3/tests/test_ai_models.py::test_get_api_key_and_headers_resolves_command_api_key_on_each_request appV2.3/tests/test_ai_models.py::test_get_api_key_and_headers_does_not_execute_shell_metacharacters -q`

Expected: at least the no-shell test fails under `shell=True`.

- [ ] **Step 3: Implement policy**

Use `shlex.split(value[1:])` and `subprocess.run(argv, shell=False, ...)`. Return `None` on parse errors or empty argv.

- [ ] **Step 4: Verify green**

Run the focused tests again and expect pass.

### Task 4: Observer failure diagnostics

**Files:**
- Modify: `appV2.3/tests/test_subagents.py`
- Modify: `appV2.3/tests/test_coding_agent.py`
- Modify: `appV2.3/appv23/coding_agent/subagents.py`
- Modify: `appV2.3/appv23/coding_agent/agent_session.py`

- [ ] **Step 1: Write failing tests**

Assert `SubagentSupervisor` records event sink failures and `AgentSession` records extension event emit failures.

- [ ] **Step 2: Verify red**

Run focused observer tests and expect fail because no diagnostics API exists.

- [ ] **Step 3: Implement diagnostics**

Add `observer_errors()` methods that return copied lists. Append concise diagnostic strings in catch blocks while keeping task execution non-fatal.

- [ ] **Step 4: Verify green and full suite**

Run focused tests, `git diff --check`, full `appV2.3/tests`, and `uv build appV2.3`.
