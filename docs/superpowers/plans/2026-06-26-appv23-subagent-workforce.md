# appV2.3 Subagent Workforce Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add production-grade subagent orchestration to `appV2.3` so the TUI can delegate bounded work to internal appv23 workers and external Codex workers while preserving parent context quality.

**Architecture:** Build a backend-agnostic `SubagentSupervisor` in `appv23.coding_agent.subagents`, expose it through `AgentSession`, and register built-in extension commands (`/agents`, `/delegate`) so the existing TUI command/autocomplete path can control it. Keep child raw logs out of parent context; return structured summaries and stable lifecycle events.

**Tech Stack:** Python 3.13, appv23 AgentSession/ExtensionRunner/TUI, subprocess-backed `codex exec --json`, pytest, existing Pi-style event stream and Hermes-style observability conventions.

---

## File Structure

- Create `appV2.3/appv23/coding_agent/subagents.py`: dataclasses, validation, backend protocol, internal backend, Codex exec backend, supervisor, lifecycle events.
- Modify `appV2.3/appv23/coding_agent/agent_session.py`: instantiate supervisor, expose command-context methods, register built-in `/agents` and `/delegate` commands, emit custom summary messages.
- Modify `appV2.3/appv23/coding_agent/extensions.py`: expose subagent actions on extension contexts without breaking existing extension APIs.
- Modify `appV2.3/appv23/coding_agent/__init__.py`: export subagent primitives.
- Create `appV2.3/tests/test_subagents.py`: unit coverage for validation, supervisor lifecycle, internal backend, Codex JSONL parsing, and AgentSession command integration.
- Modify `appV2.3/README.md`: document `/agents`, `/delegate`, safety defaults, and Codex adapter requirements.

## Task 1: Subagent domain model and supervisor

**Files:**
- Create: `appV2.3/appv23/coding_agent/subagents.py`
- Test: `appV2.3/tests/test_subagents.py`

- [ ] **Step 1: Write failing tests**

```python
from appv23.coding_agent.subagents import SubagentSupervisor, SubagentTask, CallableSubagentBackend


def test_supervisor_runs_callable_backend_and_records_lifecycle_events(tmp_path):
    events = []
    supervisor = SubagentSupervisor(max_threads=2, event_sink=events.append)
    supervisor.register_backend(CallableSubagentBackend("internal", lambda task: f"done: {task.goal}"))

    task_id = supervisor.spawn(SubagentTask(role="researcher", goal="inspect docs", cwd=str(tmp_path)))
    result = supervisor.wait(task_id, timeout=2)

    assert result.status == "completed"
    assert result.summary == "done: inspect docs"
    assert result.task_id == task_id
    assert [event["type"] for event in events] == ["subagent_start", "subagent_stop"]
```

- [ ] **Step 2: Verify red**

Run: `PYTHONPATH=appV2.3 .venv/bin/python -m pytest appV2.3/tests/test_subagents.py::test_supervisor_runs_callable_backend_and_records_lifecycle_events -q`
Expected: FAIL with `ModuleNotFoundError` or missing `SubagentSupervisor`.

- [ ] **Step 3: Implement minimal model and supervisor**

Create `SubagentTask`, `SubagentResult`, `CallableSubagentBackend`, and `SubagentSupervisor` with max-thread enforcement, lifecycle event emission, and wait/list APIs.

- [ ] **Step 4: Verify green**

Run the same pytest command. Expected: PASS.

## Task 2: Codex exec backend

**Files:**
- Modify: `appV2.3/appv23/coding_agent/subagents.py`
- Test: `appV2.3/tests/test_subagents.py`

- [ ] **Step 1: Write failing tests**

```python
from appv23.coding_agent.subagents import CodexExecBackend, SubagentTask


def test_codex_exec_backend_parses_jsonl_final_agent_message(tmp_path):
    calls = []

    def fake_runner(args, cwd, timeout, text, capture_output):
        calls.append((args, cwd, timeout, text, capture_output))
        return type("Completed", (), {
            "returncode": 0,
            "stdout": '{"type":"item.completed","item":{"type":"agent_message","text":"final summary"}}\n',
            "stderr": "",
        })()

    backend = CodexExecBackend(runner=fake_runner)
    result = backend.run(SubagentTask(role="codex", goal="review", cwd=str(tmp_path), backend="codex"))

    assert result.status == "completed"
    assert result.summary == "final summary"
    assert calls[0][0][:4] == ["codex", "exec", "--json", "--sandbox"]
    assert "read-only" in calls[0][0]
```

- [ ] **Step 2: Verify red**

Run: `PYTHONPATH=appV2.3 .venv/bin/python -m pytest appV2.3/tests/test_subagents.py::test_codex_exec_backend_parses_jsonl_final_agent_message -q`
Expected: FAIL with missing `CodexExecBackend`.

- [ ] **Step 3: Implement Codex backend**

Use `subprocess.run([...], cwd=task.cwd, timeout=task.timeout_seconds, text=True, capture_output=True)`, never shell strings. Parse JSONL `item.completed` agent messages and fallback safely on stdout/stderr. Map `read_only`, `workspace_write`, and `full_access` to Codex sandbox flags.

- [ ] **Step 4: Verify green**

Run the same pytest command. Expected: PASS.

## Task 3: AgentSession integration and commands

**Files:**
- Modify: `appV2.3/appv23/coding_agent/agent_session.py`
- Modify: `appV2.3/appv23/coding_agent/extensions.py`
- Test: `appV2.3/tests/test_subagents.py`

- [ ] **Step 1: Write failing tests**

```python
from appv23.coding_agent.agent_session import AgentSession
from appv23.coding_agent.subagents import CallableSubagentBackend, SubagentTask
from appv23.tests.helpers import faux_model


def test_agent_session_delegate_command_spawns_subagent_and_returns_summary(tmp_path):
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", lambda task: f"summary for {task.goal}"))

    messages = session.prompt('/delegate researcher inspect tests')

    assert any("summary for inspect tests" in getattr(message, "content", "") for message in messages)
    assert session.subagents.list_results()[0].role == "researcher"
```

- [ ] **Step 2: Verify red**

Run the specific test. Expected: FAIL because `subagents` or `/delegate` is missing.

- [ ] **Step 3: Implement integration**

Instantiate `SubagentSupervisor`, register default internal and Codex backends, register `/agents` and `/delegate` as built-in extension commands, and expose `spawnSubagent`, `listSubagents`, and `getSubagentResult` on extension contexts.

- [ ] **Step 4: Verify green**

Run the specific test. Expected: PASS.

## Task 4: Documentation and production QA

**Files:**
- Modify: `appV2.3/README.md`
- Test: full appV2.3 suite

- [ ] **Step 1: Document command surface**

Add `/agents` and `/delegate <role> <task>` docs, safety defaults, Codex dependency, and current limitations.

- [ ] **Step 2: Run targeted tests**

Run: `PYTHONPATH=appV2.3 .venv/bin/python -m pytest appV2.3/tests/test_subagents.py -q`
Expected: all pass.

- [ ] **Step 3: Run full appV2.3 suite**

Run: `PYTHONPATH=appV2.3 .venv/bin/python -m pytest appV2.3/tests -q`
Expected: all pass.

- [ ] **Step 4: Commit verified work**

Run: `git add docs/superpowers/plans/2026-06-26-appv23-subagent-workforce.md appV2.3 && git commit -m "Add appV2.3 subagent workforce foundation"`

## Self-Review

- Spec coverage: The plan covers internal subagents, Codex exec adapter, AgentSession/TUI command access through existing extension commands, tests, docs, and QA.
- Placeholder scan: No placeholder/TBD implementation steps remain.
- Type consistency: Task, result, backend, supervisor, and command names are consistent across tasks.
