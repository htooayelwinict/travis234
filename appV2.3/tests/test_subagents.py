from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from appv23.agent.types import AbortSignal
from appv23.ai.types import Model
from appv23.coding_agent.config import ENV_AGENT_DIR
from appv23.coding_agent.agent_session import AgentSession
from appv23.coding_agent.subagents import (
    CallableSubagentBackend,
    CodexExecBackend,
    SubagentResult,
    SubagentSupervisor,
    SubagentTask,
)


def faux_model() -> Model:
    return Model(
        id="faux/test",
        name="Faux Test",
        api="openai-completions",
        provider="faux",
        base_url="https://example.invalid",
        context_window=1000,
        max_tokens=256,
        reasoning=False,
    )


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
    assert events[0]["child_role"] == "researcher"
    assert events[1]["status"] == "completed"


def test_supervisor_stop_event_includes_result_observability_fields(tmp_path):
    events = []
    raw_log_path = str(tmp_path / "raw.json")

    def backend(task):
        return SubagentResult(
            task_id=task.id,
            backend=task.backend,
            role=task.role,
            status="failed",
            summary="blocked",
            final_response="full details",
            files_changed=["app.py"],
            artifacts=["report.md"],
            errors=["boom"],
            usage={"input_tokens": 10},
            raw_log_path=raw_log_path,
            started_at_ms=100,
            ended_at_ms=160,
        )

    supervisor = SubagentSupervisor(max_threads=1, event_sink=events.append)
    supervisor.register_backend(CallableSubagentBackend("internal", backend))

    task_id = supervisor.spawn(SubagentTask(role="reviewer", goal="inspect", cwd=str(tmp_path)))
    supervisor.wait(task_id, timeout=2)

    stop_event = events[-1]
    assert stop_event["type"] == "subagent_stop"
    assert stop_event["raw_log_path"] == raw_log_path
    assert stop_event["files_changed"] == ["app.py"]
    assert stop_event["artifacts"] == ["report.md"]
    assert stop_event["errors"] == ["boom"]
    assert stop_event["usage"] == {"input_tokens": 10}
    assert stop_event["started_at_ms"] == 100
    assert stop_event["ended_at_ms"] == 160


def test_supervisor_rejects_mismatched_backend_result_identity(tmp_path):
    def backend(task):
        return SubagentResult(
            task_id="subagent-other",
            backend=task.backend,
            role=task.role,
            status="completed",
            summary="wrong task",
        )

    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(CallableSubagentBackend("internal", backend))

    task_id = supervisor.spawn(SubagentTask(id="subagent-fixed", role="reviewer", goal="inspect", cwd=str(tmp_path)))
    result = supervisor.wait(task_id, timeout=2)

    assert result.task_id == task_id
    assert result.status == "failed"
    assert any("mismatched task_id" in error for error in result.errors)


def test_supervisor_backend_exception_reports_backend_failure(tmp_path):
    def backend(task):
        raise RuntimeError("boom")

    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(CallableSubagentBackend("internal", backend))

    task_id = supervisor.spawn(SubagentTask(role="reviewer", goal="inspect", cwd=str(tmp_path)))
    result = supervisor.wait(task_id, timeout=2)

    assert result.status == "failed"
    assert result.summary == "Subagent backend failed: boom"
    assert result.errors == ["Subagent backend failed: boom"]


def test_subagent_result_rejects_unsupported_status():
    for status in ("unknown", ["completed"]):
        try:
            SubagentResult(
                task_id="subagent-fixed",
                backend="internal",
                role="reviewer",
                status=status,
                summary="done",
            )
        except ValueError as error:
            assert "Unsupported subagent status" in str(error)
        except Exception as error:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected ValueError, got {type(error).__name__}") from error
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected status {status!r} to fail")


def test_subagent_result_rejects_unsafe_identity_fields():
    cases = (
        ("task_id", "../escape", "Unsupported subagent task id"),
        ("backend", "bad/backend", "Unsupported subagent backend"),
        ("role", "bad role", "Unsupported subagent role"),
    )
    for field, value, message in cases:
        payload = {
            "task_id": "subagent-fixed",
            "backend": "internal",
            "role": "reviewer",
            "status": "completed",
            "summary": "done",
        }
        payload[field] = value
        try:
            SubagentResult(**payload)
        except ValueError as error:
            assert message in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected {field}={value!r} to fail")


def test_subagent_result_rejects_invalid_timestamps():
    cases = (
        ({"started_at_ms": -1, "ended_at_ms": 0}, "Subagent timestamps must be non-negative"),
        ({"started_at_ms": 0, "ended_at_ms": -1}, "Subagent timestamps must be non-negative"),
        ({"started_at_ms": True, "ended_at_ms": 0}, "Subagent timestamps must be non-negative"),
        ({"started_at_ms": "now", "ended_at_ms": 0}, "Subagent timestamps must be non-negative"),
        ({"started_at_ms": 200, "ended_at_ms": 100}, "Subagent ended_at_ms cannot be before started_at_ms"),
    )
    for overrides, message in cases:
        payload = {
            "task_id": "subagent-fixed",
            "backend": "internal",
            "role": "reviewer",
            "status": "completed",
            "summary": "done",
            **overrides,
        }
        try:
            SubagentResult(**payload)
        except ValueError as error:
            assert message in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected timestamps {overrides!r} to fail")


def test_subagent_result_rejects_malformed_collection_fields():
    cases = (
        ({"files_changed": "app.py"}, "Subagent files_changed must be a list of strings"),
        ({"artifacts": "report.md"}, "Subagent artifacts must be a list of strings"),
        ({"errors": "boom"}, "Subagent errors must be a list of strings"),
        ({"files_changed": ["app.py", 7]}, "Subagent files_changed must be a list of strings"),
        ({"usage": [("input_tokens", 10)]}, "Subagent usage must be a dict"),
    )
    for overrides, message in cases:
        payload = {
            "task_id": "subagent-fixed",
            "backend": "internal",
            "role": "reviewer",
            "status": "completed",
            "summary": "done",
            **overrides,
        }
        try:
            SubagentResult(**payload)
        except ValueError as error:
            assert message in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected collection fields {overrides!r} to fail")


def test_subagent_result_rejects_malformed_text_fields():
    cases = (
        ({"summary": ""}, "Subagent summary is required"),
        ({"summary": 7}, "Subagent summary is required"),
        ({"final_response": 7}, "Subagent final_response must be a string"),
        ({"child_session_id": 7}, "Subagent child_session_id must be a string when set"),
        ({"raw_log_path": 7}, "Subagent raw_log_path must be a string when set"),
    )
    for overrides, message in cases:
        payload = {
            "task_id": "subagent-fixed",
            "backend": "internal",
            "role": "reviewer",
            "status": "completed",
            "summary": "done",
            **overrides,
        }
        try:
            SubagentResult(**payload)
        except ValueError as error:
            assert message in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected text fields {overrides!r} to fail")


def test_supervisor_event_sink_failure_does_not_break_task(tmp_path):
    seen_event_types = []

    def failing_event_sink(event):
        seen_event_types.append(event["type"])
        raise RuntimeError("observer failed")

    supervisor = SubagentSupervisor(max_threads=1, event_sink=failing_event_sink)
    supervisor.register_backend(CallableSubagentBackend("internal", lambda task: "done"))

    task_id = supervisor.spawn(SubagentTask(role="reviewer", goal="inspect", cwd=str(tmp_path)))
    result = supervisor.wait(task_id, timeout=2)

    assert result.status == "completed"
    assert result.summary == "done"
    assert seen_event_types == ["subagent_start", "subagent_stop"]
    assert supervisor.observer_errors() == [
        "event sink failed for subagent_start: observer failed",
        "event sink failed for subagent_stop: observer failed",
    ]


def test_supervisor_rejects_malformed_constructor_options():
    cases = (
        ({"max_threads": "many"}, "max_threads must be at least 1"),
        ({"max_threads": True}, "max_threads must be at least 1"),
        ({"max_depth": "deep"}, "max_depth must be at least 1"),
        ({"max_depth": True}, "max_depth must be at least 1"),
        ({"event_sink": "not callable"}, "event_sink must be callable"),
    )
    for kwargs, message in cases:
        try:
            SubagentSupervisor(**kwargs)
        except ValueError as error:
            assert message in str(error)
        except Exception as error:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected ValueError, got {type(error).__name__}") from error
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected constructor options {kwargs!r} to fail")


def test_callable_backend_rejects_malformed_name():
    for name in ("", "bad/backend", 7):
        try:
            CallableSubagentBackend(name, lambda task: "done")
        except ValueError as error:
            assert "Unsupported subagent backend" in str(error)
        except Exception as error:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected ValueError, got {type(error).__name__}") from error
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected backend name {name!r} to fail")


def test_callable_backend_rejects_malformed_handler_result(tmp_path):
    task = SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path))
    for output in (None, ""):
        backend = CallableSubagentBackend("internal", lambda task, output=output: output)
        try:
            backend.run(task)
        except ValueError as error:
            assert "Subagent backend handler must return a non-empty string or SubagentResult" in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected malformed handler result {output!r} to fail")


def test_subagent_task_prompt_prevents_parent_no_subagent_fallback(tmp_path):
    task = SubagentTask(role="reviewer", goal="inspect README.md", cwd=str(tmp_path))

    prompt = task.prompt()

    assert "You are already the delegated child subagent" in prompt
    assert "Do not answer `subagent tool unavailable`" in prompt


def test_subagent_task_prompt_contains_child_system_contract(tmp_path):
    task = SubagentTask(role="reviewer", goal="inspect appv23/agent/agent_loop.py", cwd=str(tmp_path))

    prompt = task.prompt()

    assert "Subagent system contract" in prompt
    assert f"Current working directory: {tmp_path}" in prompt
    assert "Use paths relative to the current working directory" in prompt
    assert "Do not drop leading project directories from paths in the Goal" in prompt
    assert "Allowed tools are the complete tool catalog for this child" in prompt
    assert "For file discovery, use find or ls" in prompt
    assert "glob" not in prompt.lower()
    assert "After two failed attempts for the same path or unavailable tool, stop retrying" in prompt


def test_subagent_task_rejects_boolean_runtime_options(tmp_path):
    cases = (
        ({"timeout_seconds": True}, "Subagent timeout_seconds must be positive"),
        ({"depth": True}, "Subagent depth must be at least 1"),
    )
    for kwargs, message in cases:
        try:
            SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path), **kwargs)
        except ValueError as error:
            assert message in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected runtime options {kwargs!r} to fail")


def test_subagent_task_rejects_malformed_allowed_tools_container(tmp_path):
    for allowed_tools in ("read", 7):
        try:
            SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path), allowed_tools=allowed_tools)
        except ValueError as error:
            assert "Subagent allowed_tools must be a sequence of strings" in str(error)
        except Exception as error:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected ValueError, got {type(error).__name__}") from error
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected allowed_tools {allowed_tools!r} to fail")


def test_subagent_task_rejects_malformed_parent_metadata(tmp_path):
    cases = (
        ({"parent_session_id": 7}, "Subagent parent_session_id must be a string when set"),
        ({"parent_turn_id": 7}, "Subagent parent_turn_id must be a string when set"),
    )
    for kwargs, message in cases:
        try:
            SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path), **kwargs)
        except ValueError as error:
            assert message in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected parent metadata {kwargs!r} to fail")


def test_supervisor_rejects_malformed_spawn_task():
    supervisor = SubagentSupervisor(max_threads=1)

    try:
        supervisor.spawn(None)
    except ValueError as error:
        assert "Subagent task must be a SubagentTask" in str(error)
    except Exception as error:  # pragma: no cover - assertion path
        raise AssertionError(f"Expected ValueError, got {type(error).__name__}") from error
    else:  # pragma: no cover - assertion path
        raise AssertionError("Expected malformed spawn task to fail")


def test_supervisor_rejects_malformed_task_id_references():
    supervisor = SubagentSupervisor(max_threads=1)
    cases = (
        (lambda: supervisor.wait(["subagent-fixed"]), "wait"),
        (lambda: supervisor.cancel(["subagent-fixed"]), "cancel"),
        (lambda: supervisor.get_result(["subagent-fixed"]), "get_result"),
    )
    for operation, name in cases:
        try:
            operation()
        except ValueError as error:
            assert "Unsupported subagent task id" in str(error)
        except Exception as error:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected ValueError from {name}, got {type(error).__name__}") from error
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected malformed task id in {name} to fail")


def test_supervisor_timeout_keeps_capacity_until_backend_finishes(tmp_path):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def backend(task):
        started.set()
        release.wait(2)
        finished.set()
        return "done"

    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(CallableSubagentBackend("internal", backend))

    task_id = supervisor.spawn(SubagentTask(role="reviewer", goal="slow", cwd=str(tmp_path)))
    assert started.wait(1)
    try:
        result = supervisor.wait(task_id, timeout=0.01)
        assert result.status == "timeout"

        try:
            supervisor.spawn(SubagentTask(role="reviewer", goal="second", cwd=str(tmp_path)))
        except RuntimeError as error:
            assert "Subagent thread limit reached" in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError("Expected unfinished timed-out backend to keep thread capacity")

        release.set()
        assert finished.wait(1)
        second_id = supervisor.spawn(SubagentTask(role="reviewer", goal="second", cwd=str(tmp_path)))
        second_result = supervisor.wait(second_id, timeout=2)
        assert second_result.status == "completed"
    finally:
        release.set()
        supervisor.shutdown(wait=True)


def test_supervisor_cancel_keeps_capacity_until_backend_finishes(tmp_path):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def backend(task):
        started.set()
        release.wait(2)
        finished.set()
        return "done"

    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(CallableSubagentBackend("internal", backend))

    task_id = supervisor.spawn(SubagentTask(role="reviewer", goal="slow", cwd=str(tmp_path)))
    assert started.wait(1)
    try:
        result = supervisor.cancel(task_id)
        assert result.status == "cancelled"

        try:
            supervisor.spawn(SubagentTask(role="reviewer", goal="second", cwd=str(tmp_path)))
        except RuntimeError as error:
            assert "Subagent thread limit reached" in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError("Expected unfinished cancelled backend to keep thread capacity")

        release.set()
        assert finished.wait(1)
        second_id = supervisor.spawn(SubagentTask(role="reviewer", goal="second", cwd=str(tmp_path)))
        second_result = supervisor.wait(second_id, timeout=2)
        assert second_result.status == "completed"
    finally:
        release.set()
        supervisor.shutdown(wait=True)


def test_supervisor_rejects_string_wait_all_task_ids():
    supervisor = SubagentSupervisor(max_threads=1)

    try:
        supervisor.wait_all("subagent-fixed")
    except ValueError as error:
        assert "task_ids must be a sequence" in str(error)
    except Exception as error:  # pragma: no cover - assertion path
        raise AssertionError(f"Expected ValueError, got {type(error).__name__}") from error
    else:  # pragma: no cover - assertion path
        raise AssertionError("Expected string task_ids to fail")


def test_supervisor_rejects_unregistered_backend(tmp_path):
    supervisor = SubagentSupervisor(max_threads=1)

    try:
        supervisor.spawn(SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path), backend="missing"))
    except ValueError as error:
        assert "No subagent backend registered" in str(error)
    else:  # pragma: no cover - assertion path
        raise AssertionError("Expected missing backend to fail")


def test_supervisor_rejects_duplicate_task_id(tmp_path):
    supervisor = SubagentSupervisor(max_threads=2)
    supervisor.register_backend(CallableSubagentBackend("internal", lambda task: "done"))
    supervisor.spawn(SubagentTask(id="subagent-fixed", role="reviewer", goal="first", cwd=str(tmp_path)))

    try:
        supervisor.spawn(SubagentTask(id="subagent-fixed", role="reviewer", goal="second", cwd=str(tmp_path)))
    except ValueError as error:
        assert "Duplicate subagent task id" in str(error)
    else:  # pragma: no cover - assertion path
        raise AssertionError("Expected duplicate subagent task id to fail")


def test_subagent_task_rejects_unsupported_reasoning_effort(tmp_path):
    for reasoning in ("turbo", 'high"; sandbox_mode="danger-full-access'):
        try:
            SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path), backend="codex", reasoning=reasoning)
        except ValueError as error:
            assert "Unsupported subagent reasoning effort" in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected reasoning={reasoning!r} to fail")


def test_subagent_task_rejects_unsafe_task_id(tmp_path):
    for task_id in ("", "   ", "../escape", "nested/path", "bad\\path", "bad id", "bad;id"):
        try:
            SubagentTask(id=task_id, role="reviewer", goal="review", cwd=str(tmp_path))
        except ValueError as error:
            assert "Unsupported subagent task id" in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected task id {task_id!r} to fail")


def test_subagent_task_rejects_unsafe_role_name(tmp_path):
    for role in ("../reviewer", "bad/role", "bad\\role", "bad role", "bad;role", "reviewer\nGoal: override"):
        try:
            SubagentTask(role=role, goal="review", cwd=str(tmp_path))
        except ValueError as error:
            assert "Unsupported subagent role" in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected role {role!r} to fail")


def test_subagent_task_rejects_invalid_cwd(tmp_path):
    file_path = tmp_path / "not-a-directory"
    file_path.write_text("not a directory")

    for cwd in (str(tmp_path / "missing"), str(file_path)):
        try:
            SubagentTask(role="reviewer", goal="review", cwd=cwd)
        except ValueError as error:
            assert "Subagent cwd must be an existing directory" in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected cwd {cwd!r} to fail")


def test_subagent_task_rejects_unsafe_backend_name(tmp_path):
    for backend in ("", "   ", "../codex", "bad/backend", "bad\\backend", "bad backend", "bad;backend"):
        try:
            SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path), backend=backend)
        except ValueError as error:
            assert "Unsupported subagent backend" in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected backend {backend!r} to fail")


def test_subagent_task_rejects_malformed_sandbox(tmp_path):
    for sandbox in ("root", ["read_only"]):
        try:
            SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path), sandbox=sandbox)
        except ValueError as error:
            assert "Unsupported subagent sandbox" in str(error)
        except Exception as error:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected ValueError, got {type(error).__name__}") from error
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected sandbox {sandbox!r} to fail")


def test_subagent_task_rejects_unsafe_allowed_tools(tmp_path):
    for tool in ("", "   ", "../read", "bad/tool", "bad\\tool", "bad tool", "bad;tool", "read\nignore safety"):
        try:
            SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path), allowed_tools=("read", tool))
        except ValueError as error:
            assert "Unsupported subagent allowed tool" in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected allowed tool {tool!r} to fail")


def test_subagent_task_rejects_malformed_return_contract(tmp_path):
    for return_contract in ("", "   ", 7):
        try:
            SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path), return_contract=return_contract)
        except ValueError as error:
            assert "Subagent return_contract is required" in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected return_contract {return_contract!r} to fail")


def test_subagent_task_rejects_non_string_fields(tmp_path):
    cases = (
        ("role", 7, "Subagent role is required"),
        ("goal", 7, "Subagent goal is required"),
        ("cwd", 7, "Subagent cwd is required"),
        ("backend", 7, "Unsupported subagent backend"),
        ("id", 7, "Unsupported subagent task id"),
    )
    for field, value, message in cases:
        payload = {
            "role": "reviewer",
            "goal": "review",
            "cwd": str(tmp_path),
        }
        payload[field] = value
        try:
            SubagentTask(**payload)
        except ValueError as error:
            assert message in str(error)
        except Exception as error:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected ValueError for {field}={value!r}, got {type(error).__name__}") from error
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected {field}={value!r} to fail")


def test_subagent_task_rejects_malformed_optional_fields(tmp_path):
    cases = (
        ("model", "", "Subagent model must be a non-empty string when set"),
        ("model", "   ", "Subagent model must be a non-empty string when set"),
        ("model", 7, "Subagent model must be a non-empty string when set"),
        ("reasoning", 7, "Unsupported subagent reasoning effort"),
        ("timeout_seconds", "slow", "Subagent timeout_seconds must be positive"),
        ("depth", "one", "Subagent depth must be at least 1"),
    )
    for field, value, message in cases:
        payload = {
            "role": "reviewer",
            "goal": "review",
            "cwd": str(tmp_path),
        }
        payload[field] = value
        try:
            SubagentTask(**payload)
        except ValueError as error:
            assert message in str(error)
        except Exception as error:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected ValueError for {field}={value!r}, got {type(error).__name__}") from error
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected {field}={value!r} to fail")


def test_subagent_task_rejects_malformed_context_pack(tmp_path):
    for context_pack in (None, 7):
        try:
            SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path), context_pack=context_pack)
        except ValueError as error:
            assert "Subagent context_pack must be a string" in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected context_pack {context_pack!r} to fail")


def test_supervisor_rejects_unsafe_registered_backend_name():
    supervisor = SubagentSupervisor(max_threads=1)

    class UnsafeBackend:
        name = "bad/backend"

        def run(self, task):
            return SubagentResult(
                task_id=task.id,
                backend=self.name,
                role=task.role,
                status="completed",
                summary="done",
            )

    try:
        supervisor.register_backend(UnsafeBackend())
    except ValueError as error:
        assert "Unsupported subagent backend" in str(error)
    else:  # pragma: no cover - assertion path
        raise AssertionError("Expected unsafe backend name to fail")


def test_supervisor_rejects_malformed_registered_backend_interface():
    supervisor = SubagentSupervisor(max_threads=1)

    class MissingNameBackend:
        def run(self, task):
            return "done"

    class NonStringNameBackend:
        name = 7

        def run(self, task):
            return "done"

    class MissingRunBackend:
        name = "missing-run"

    class NonCallableRunBackend:
        name = "bad-run"
        run = "not callable"

    cases = (
        (MissingNameBackend(), "Unsupported subagent backend"),
        (NonStringNameBackend(), "Unsupported subagent backend"),
        (MissingRunBackend(), "Subagent backend must define a callable run method"),
        (NonCallableRunBackend(), "Subagent backend must define a callable run method"),
    )
    for backend, message in cases:
        try:
            supervisor.register_backend(backend)
        except ValueError as error:
            assert message in str(error)
        except Exception as error:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected ValueError, got {type(error).__name__}") from error
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected malformed backend {backend!r} to fail")


def test_callable_backend_rejects_unsafe_name():
    for name in ("../internal", "bad/backend", "bad\\backend", "bad backend", "bad;backend"):
        try:
            CallableSubagentBackend(name, lambda task: "done")
        except ValueError as error:
            assert "Unsupported subagent backend" in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected backend name {name!r} to fail")


def test_callable_backend_rejects_non_callable_handler():
    try:
        CallableSubagentBackend("internal", "not callable")
    except ValueError as error:
        assert "Subagent backend handler must be callable" in str(error)
    else:  # pragma: no cover - assertion path
        raise AssertionError("Expected non-callable handler to fail")


def test_codex_exec_backend_rejects_malformed_constructor_options():
    cases = (
        ({"codex_bin": ""}, "codex_bin must be a non-empty string"),
        ({"codex_bin": "   "}, "codex_bin must be a non-empty string"),
        ({"codex_bin": 7}, "codex_bin must be a non-empty string"),
        ({"runner": "not callable"}, "runner must be callable"),
        ({"log_dir": 7}, "log_dir must be a path string or Path"),
    )
    for kwargs, message in cases:
        try:
            CodexExecBackend(**kwargs)
        except ValueError as error:
            assert message in str(error)
        except Exception as error:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected ValueError, got {type(error).__name__}") from error
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected constructor options {kwargs!r} to fail")


def test_supervisor_wait_timeout_records_terminal_result(tmp_path):
    events = []
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def slow_backend(task):
        started.set()
        release.wait(1)
        finished.set()
        return "late summary"

    supervisor = SubagentSupervisor(max_threads=1, event_sink=events.append)
    supervisor.register_backend(CallableSubagentBackend("internal", slow_backend))

    task_id = supervisor.spawn(SubagentTask(role="researcher", goal="slow work", cwd=str(tmp_path)))
    assert started.wait(1)

    result = supervisor.wait(task_id, timeout=0.01)

    assert result.status == "timeout"
    assert result.summary == "Subagent timed out."
    assert result.task_id == task_id
    assert result.started_at_ms > 0
    assert result.ended_at_ms >= result.started_at_ms
    assert supervisor.get_result(task_id).status == "timeout"

    release.set()
    assert finished.wait(1)
    assert supervisor.get_result(task_id).status == "timeout"
    assert [event["type"] for event in events] == ["subagent_start", "subagent_stop"]
    assert events[-1]["status"] == "timeout"


def test_supervisor_rejects_invalid_wait_timeouts(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def slow_backend(task):
        started.set()
        release.wait(1)
        return "late summary"

    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(CallableSubagentBackend("internal", slow_backend))
    task_id = supervisor.spawn(SubagentTask(role="reviewer", goal="review slowly", cwd=str(tmp_path)))
    assert started.wait(1)

    try:
        for timeout in (-1, "soon", float("nan"), float("inf")):
            try:
                supervisor.wait(task_id, timeout=timeout)
            except ValueError as error:
                assert "timeout must be non-negative" in str(error)
            except Exception as error:  # pragma: no cover - assertion path
                raise AssertionError(f"Expected ValueError, got {type(error).__name__}") from error
            else:  # pragma: no cover - assertion path
                raise AssertionError(f"Expected timeout {timeout!r} to fail")
    finally:
        release.set()


def test_supervisor_cancel_records_terminal_result_and_event(tmp_path):
    events = []
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def slow_backend(task):
        started.set()
        release.wait(1)
        finished.set()
        return "late summary"

    supervisor = SubagentSupervisor(max_threads=1, event_sink=events.append)
    supervisor.register_backend(CallableSubagentBackend("internal", slow_backend))

    task_id = supervisor.spawn(SubagentTask(role="reviewer", goal="review slowly", cwd=str(tmp_path)))
    assert started.wait(1)

    result = supervisor.cancel(task_id, reason="user requested")

    assert result.status == "cancelled"
    assert result.summary == "Subagent cancelled."
    assert result.errors == ["user requested"]
    assert result.started_at_ms > 0
    assert result.ended_at_ms >= result.started_at_ms
    assert supervisor.get_result(task_id).status == "cancelled"

    release.set()
    assert finished.wait(1)
    assert supervisor.get_result(task_id).status == "cancelled"
    assert [event["type"] for event in events] == ["subagent_start", "subagent_stop"]
    assert events[-1]["status"] == "cancelled"


def test_supervisor_rejects_malformed_cancel_reason(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def slow_backend(task):
        started.set()
        release.wait(1)
        return "late summary"

    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(CallableSubagentBackend("internal", slow_backend))
    task_id = supervisor.spawn(SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path)))
    assert started.wait(1)

    try:
        try:
            supervisor.cancel(task_id, reason=7)
        except ValueError as error:
            assert "cancel reason must be a string" in str(error)
        else:  # pragma: no cover - assertion path
            raise AssertionError("Expected non-string cancel reason to fail")
    finally:
        release.set()


def test_supervisor_concurrent_cancel_emits_one_terminal_event(tmp_path):
    events = []
    started = threading.Event()
    release = threading.Event()

    class SlowEmptyResults(dict):
        def get(self, key, default=None):
            value = super().get(key, default)
            if key == "subagent-fixed" and value is None:
                time.sleep(0.02)
            return value

    def slow_backend(task):
        started.set()
        release.wait(1)
        return "late summary"

    supervisor = SubagentSupervisor(max_threads=1, event_sink=events.append)
    supervisor.register_backend(CallableSubagentBackend("internal", slow_backend))
    task_id = supervisor.spawn(
        SubagentTask(id="subagent-fixed", role="reviewer", goal="review slowly", cwd=str(tmp_path))
    )
    supervisor._results = SlowEmptyResults(supervisor._results)
    assert started.wait(1)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: supervisor.cancel(task_id, reason="not needed"), range(8)))
    release.set()

    assert {result.status for result in results} == {"cancelled"}
    stop_events = [event for event in events if event["type"] == "subagent_stop"]
    assert len(stop_events) == 1


def test_supervisor_shutdown_cancels_running_tasks_and_rejects_new_spawns(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def slow_backend(task):
        started.set()
        release.wait(1)
        return "late summary"

    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(CallableSubagentBackend("internal", slow_backend))
    task_id = supervisor.spawn(SubagentTask(role="reviewer", goal="review slowly", cwd=str(tmp_path)))
    assert started.wait(1)

    results = supervisor.shutdown(wait=False, reason="session shutdown")
    release.set()

    assert [result.status for result in results] == ["cancelled"]
    assert supervisor.get_result(task_id).status == "cancelled"
    try:
        supervisor.spawn(SubagentTask(role="reviewer", goal="next", cwd=str(tmp_path)))
    except RuntimeError as error:
        assert "shut down" in str(error)
    else:  # pragma: no cover - assertion path
        raise AssertionError("Expected shutdown supervisor to reject new tasks")


def test_supervisor_rejects_malformed_shutdown_options():
    cases = (
        ({"reason": 7}, "shutdown reason must be a string"),
        ({"wait": "yes"}, "shutdown wait must be a bool"),
    )
    for kwargs, message in cases:
        supervisor = SubagentSupervisor(max_threads=1)
        try:
            supervisor.shutdown(**kwargs)
        except ValueError as error:
            assert message in str(error)
        except Exception as error:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected ValueError, got {type(error).__name__}") from error
        else:  # pragma: no cover - assertion path
            raise AssertionError(f"Expected shutdown options {kwargs!r} to fail")


def test_codex_exec_backend_parses_jsonl_final_agent_message(tmp_path):
    calls = []

    def fake_runner(args, cwd, timeout, text, capture_output):
        calls.append((args, cwd, timeout, text, capture_output))
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": '{"type":"item.completed","item":{"type":"agent_message","text":"final summary"}}\n',
                "stderr": "",
            },
        )()

    backend = CodexExecBackend(runner=fake_runner)
    result = backend.run(SubagentTask(role="codex", goal="review", cwd=str(tmp_path), backend="codex"))

    assert result.status == "completed"
    assert result.summary == "final summary"
    assert calls[0][0][:4] == ["codex", "exec", "--json", "--sandbox"]
    assert "read-only" in calls[0][0]
    assert calls[0][1] == str(tmp_path)


def test_codex_exec_backend_forwards_model_and_reasoning_effort(tmp_path):
    calls = []

    def fake_runner(args, cwd, timeout, text, capture_output):
        calls.append(args)
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": '{"type":"item.completed","item":{"type":"agent_message","text":"final summary"}}\n',
                "stderr": "",
            },
        )()

    backend = CodexExecBackend(runner=fake_runner)
    result = backend.run(
        SubagentTask(
            role="codex",
            goal="review",
            cwd=str(tmp_path),
            backend="codex",
            model="gpt-5-codex",
            reasoning="high",
        )
    )

    assert result.status == "completed"
    assert "--model" in calls[0]
    assert "gpt-5-codex" in calls[0]
    assert "-c" in calls[0]
    assert 'model_reasoning_effort="high"' in calls[0]


def test_codex_exec_backend_rejects_custom_allowed_tools_before_running(tmp_path):
    called = False

    def fake_runner(args, cwd, timeout, text, capture_output):
        nonlocal called
        called = True
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": '{"type":"item.completed","item":{"type":"agent_message","text":"final summary"}}\n',
                "stderr": "",
            },
        )()

    backend = CodexExecBackend(runner=fake_runner)
    result = backend.run(
        SubagentTask(
            role="codex",
            goal="review",
            cwd=str(tmp_path),
            backend="codex",
            allowed_tools=("read", "bash"),
        )
    )

    assert called is False
    assert result.status == "failed"
    assert result.summary == "Codex backend does not enforce custom allowed tools."
    assert result.errors == ["Codex backend does not enforce custom allowed tools."]


def test_codex_exec_backend_persists_raw_log_when_configured(tmp_path):
    def fake_runner(args, cwd, timeout, text, capture_output):
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": '{"type":"item.completed","item":{"type":"agent_message","text":"final summary"}}\n',
                "stderr": "",
            },
        )()

    backend = CodexExecBackend(runner=fake_runner, log_dir=str(tmp_path / "logs"))
    result = backend.run(
        SubagentTask(id="subagent-fixed", role="codex", goal="review", cwd=str(tmp_path), backend="codex")
    )

    assert result.status == "completed"
    assert result.raw_log_path is not None
    raw_log_path = Path(result.raw_log_path)
    assert raw_log_path.parent == tmp_path / "logs"
    payload = json.loads(raw_log_path.read_text())
    assert payload["taskId"] == "subagent-fixed"
    assert payload["returncode"] == 0
    assert "final summary" in payload["stdout"]


def test_codex_exec_backend_keeps_success_when_raw_log_write_fails(tmp_path):
    def fake_runner(args, cwd, timeout, text, capture_output):
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": '{"type":"item.completed","item":{"type":"agent_message","text":"final summary"}}\n',
                "stderr": "",
            },
        )()

    blocked_log_dir = tmp_path / "not-a-directory"
    blocked_log_dir.write_text("file blocks mkdir")
    backend = CodexExecBackend(runner=fake_runner, log_dir=blocked_log_dir)

    result = backend.run(
        SubagentTask(id="subagent-log-fail", role="codex", goal="review", cwd=str(tmp_path), backend="codex")
    )

    assert result.status == "completed"
    assert result.summary == "final summary"
    assert result.raw_log_path is None
    assert any("Failed to write raw subagent log" in error for error in result.errors)


def test_codex_exec_backend_reports_nonzero_exit(tmp_path):
    def fake_runner(args, cwd, timeout, text, capture_output):
        return type("Completed", (), {"returncode": 2, "stdout": "", "stderr": "bad auth"})()

    backend = CodexExecBackend(runner=fake_runner)
    result = backend.run(SubagentTask(role="codex", goal="review", cwd=str(tmp_path), backend="codex"))

    assert result.status == "failed"
    assert result.errors == ["bad auth"]


def test_codex_exec_backend_persists_raw_log_for_failed_runs(tmp_path):
    def fake_runner(args, cwd, timeout, text, capture_output):
        return type("Completed", (), {"returncode": 2, "stdout": "partial output", "stderr": "bad auth"})()

    backend = CodexExecBackend(runner=fake_runner, log_dir=str(tmp_path / "logs"))
    result = backend.run(
        SubagentTask(id="subagent-failed", role="codex", goal="review", cwd=str(tmp_path), backend="codex")
    )

    assert result.status == "failed"
    assert result.raw_log_path is not None
    payload = json.loads(Path(result.raw_log_path).read_text())
    assert payload["returncode"] == 2
    assert payload["stdout"] == "partial output"
    assert payload["stderr"] == "bad auth"


def test_agent_session_delegate_command_spawns_subagent_and_returns_summary(tmp_path):
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", lambda task: f"summary for {task.goal}"))

    messages = session.prompt("/delegate researcher inspect tests")

    assert any("summary for inspect tests" in getattr(message, "content", "") for message in messages)
    assert session.subagents.list_results()[0].role == "researcher"


def test_agent_session_spawn_subagent_tool_returns_bounded_parent_payload(tmp_path):
    noisy_summary = "summary-start " + ("NOISE " * 600) + "summary-end"
    tool_trace = [
        {
            "toolCallId": f"call-{index}",
            "toolName": "read",
            "status": "ok",
            "argsPreview": f"file-{index}.md",
            "resultPreview": "RESULT " * 100,
            "startedAtMs": index,
            "endedAtMs": index + 1,
            "elapsedMs": 1,
        }
        for index in range(12)
    ]

    def backend(task):
        return SubagentResult(
            task_id=task.id,
            backend=task.backend,
            role=task.role,
            status="failed",
            summary=noisy_summary,
            final_response=noisy_summary,
            errors=["Subagent stopped by tool guardrail: idempotent_no_progress_block (ls)"],
            tool_trace=tool_trace,
            guardrail={"code": "idempotent_no_progress_block", "tool_name": "ls", "count": 3},
        )

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", backend))

    result = session._execute_spawn_subagent_tool(
        "tool-call",
        {"role": "explorer", "goal": "scan broad docs", "wait": True},
    )

    rendered = "\n".join(block.text for block in result.content)
    assert len(rendered) < 2500
    assert "summary-end" not in rendered
    assert "toolTrace:" not in rendered
    assert "filesChanged:" not in rendered
    assert result.details["status"] == "failed"
    assert "finalResponse" not in result.details
    assert len(result.details["summary"]) < 1200
    assert "summary-end" not in result.details["summary"]
    assert len(result.details["toolTrace"]) <= 3
    assert result.details["toolTraceCount"] == 12


def test_agent_session_spawn_subagent_tool_normalizes_safe_human_role(tmp_path):
    seen_roles: list[str] = []

    def backend(task):
        seen_roles.append(task.role)
        return f"summary for {task.role}"

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", backend))

    result = session._execute_spawn_subagent_tool(
        "tool-call",
        {"role": "provider-loop reviewer", "goal": "inspect appv23/agent/agent_loop.py", "wait": True},
    )

    assert result.details["status"] == "completed"
    assert result.details["role"] == "provider-loop-reviewer"
    assert seen_roles == ["provider-loop-reviewer"]


def test_agent_session_spawn_subagent_tool_rejects_read_only_child_file_mutation_goal(tmp_path):
    spawned_goals: list[str] = []

    def backend(task):
        spawned_goals.append(task.goal)
        return "should not run"

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", backend))

    result = session._execute_spawn_subagent_tool(
        "tool-call",
        {
            "role": "reviewer",
            "goal": "Inspect the project and write REVIEW.md with the top 3 concrete risks.",
            "wait": True,
        },
    )

    rendered = "\n".join(block.text for block in result.content)
    assert result.details["status"] == "blocked"
    assert result.details["reason"] == "read_only_subagent_file_mutation_goal"
    assert "Subagents are read-only" in rendered
    assert "spawn the child for inspection only" in rendered
    assert "parent should write" in rendered
    assert "No subagent task was spawned" in rendered
    assert spawned_goals == []
    assert session.subagents.list_tasks() == []


def test_agent_session_spawn_subagent_tool_allows_negated_file_mutation_instruction(tmp_path):
    spawned_goals: list[str] = []

    def backend(task):
        spawned_goals.append(task.goal)
        return "No blockers."

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", backend))

    result = session._execute_spawn_subagent_tool(
        "tool-call",
        {
            "role": "reviewer",
            "goal": "Inspect ledger.py and tests/test_ledger.py only. Summarize blockers only. Do not write files.",
            "wait": True,
        },
    )

    rendered = "\n".join(block.text for block in result.content)
    assert result.details["status"] == "completed"
    assert "No blockers." in rendered
    assert spawned_goals == [
        "Inspect ledger.py and tests/test_ledger.py only. Summarize blockers only. Do not write files."
    ]
    assert len(session.subagents.list_tasks()) == 1


def test_agent_session_expand_subagent_result_returns_bounded_full_child_output(tmp_path):
    noisy_summary = "summary-start " + ("NOISE " * 280) + "summary-end"

    def backend(task):
        return SubagentResult(
            task_id=task.id,
            backend=task.backend,
            role=task.role,
            status="completed",
            summary=noisy_summary,
            final_response=noisy_summary,
        )

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", backend))

    spawn_result = session._execute_spawn_subagent_tool(
        "tool-call",
        {"role": "explorer", "goal": "scan broad docs", "wait": True},
    )
    task_id = spawn_result.details["taskId"]

    assert "summary-end" not in "\n".join(block.text for block in spawn_result.content)
    assert "finalResponse" not in spawn_result.details

    expanded = session._execute_expand_subagent_result_tool(
        "expand-call",
        {"taskId": task_id, "section": "final_response", "budget": "medium"},
    )

    rendered = "\n".join(block.text for block in expanded.content)
    assert "summary-end" in rendered
    assert "Let me read the key files directly" not in rendered
    assert expanded.details["taskId"] == task_id
    assert expanded.details["section"] == "final_response"
    assert expanded.details["truncated"] is False
    assert expanded.details["nextOffset"] is None
    assert "final_response" in expanded.details["availableSections"]


def test_agent_session_expand_subagent_result_pages_long_output(tmp_path):
    final_response = "A" * 1800 + "TAIL"

    def backend(task):
        return SubagentResult(
            task_id=task.id,
            backend=task.backend,
            role=task.role,
            status="completed",
            summary="short summary",
            final_response=final_response,
        )

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", backend))
    spawn_result = session._execute_spawn_subagent_tool(
        "tool-call",
        {"role": "explorer", "goal": "scan broad docs", "wait": True},
    )
    task_id = spawn_result.details["taskId"]

    first_page = session._execute_expand_subagent_result_tool(
        "expand-call-1",
        {"taskId": task_id, "section": "final_response", "budget": "short", "offset": 0},
    )

    assert first_page.details["truncated"] is True
    assert first_page.details["nextOffset"] > 0
    assert "TAIL" not in first_page.details["text"]

    second_page = session._execute_expand_subagent_result_tool(
        "expand-call-2",
        {
            "taskId": task_id,
            "section": "final_response",
            "budget": "short",
            "offset": first_page.details["nextOffset"],
        },
    )

    assert second_page.details["truncated"] is False
    assert second_page.details["nextOffset"] is None
    assert "TAIL" in second_page.details["text"]


def test_agent_session_spawn_subagent_tool_blocks_extra_model_wave_in_same_turn(tmp_path):
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", lambda task: f"summary {task.goal}"))

    for index in range(3):
        result = session._execute_spawn_subagent_tool(
            f"tool-call-{index}",
            {"role": "explorer", "goal": f"scan area {index}", "wait": True},
        )
        assert result.details["status"] == "completed"

    blocked = session._execute_spawn_subagent_tool(
        "tool-call-blocked",
        {"role": "explorer", "goal": "scan extra wave", "wait": True},
    )

    rendered = "\n".join(block.text for block in blocked.content)
    assert blocked.details["status"] == "blocked"
    assert blocked.details["reason"] == "subagent_spawn_limit_per_turn"
    assert "already spawned 3 subagents in this turn" in rendered
    assert len(session.subagents.list_tasks()) == 3

    session._reset_model_subagent_turn_budget()
    allowed = session._execute_spawn_subagent_tool(
        "tool-call-next-turn",
        {"role": "explorer", "goal": "scan next turn", "wait": True},
    )
    assert allowed.details["status"] == "completed"
    assert len(session.subagents.list_tasks()) == 4


def test_agent_session_default_codex_backend_persists_raw_log(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_AGENT_DIR, str(tmp_path / "agent-home"))

    def fake_runner(args, cwd, timeout, text, capture_output):
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": '{"type":"item.completed","item":{"type":"agent_message","text":"codex summary"}}\n',
                "stderr": "",
            },
        )()

    monkeypatch.setattr("appv23.coding_agent.subagents.subprocess.run", fake_runner)
    session_path = tmp_path / "sessions" / "parent.jsonl"
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(session_path),
        session_id="session-fixed",
    )

    session.prompt("/delegate --backend codex reviewer inspect logs")

    result = session.subagents.list_results()[0]
    assert result.status == "completed"
    assert result.raw_log_path is not None
    raw_log_path = Path(result.raw_log_path)
    assert raw_log_path.parent == session_path.parent / "subagents" / "session-fixed"
    payload = json.loads(raw_log_path.read_text())
    assert payload["taskId"] == result.task_id
    assert "codex summary" in payload["stdout"]


def test_agent_session_agents_command_lists_completed_subagents(tmp_path):
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", lambda task: "done"))
    session.prompt("/delegate reviewer scan code")

    messages = session.prompt("/agents")

    rendered = "\n".join(str(getattr(message, "content", "")) for message in messages)
    assert "reviewer" in rendered
    assert "completed" in rendered


def test_agent_session_cancel_agent_command_cancels_subagent(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def slow_backend(task):
        started.set()
        release.wait(1)
        return "late summary"

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", slow_backend))
    task_id = session.subagents.spawn(SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path)))
    assert started.wait(1)

    messages = session.prompt(f"/cancel-agent {task_id}")
    release.set()

    rendered = "\n".join(str(getattr(message, "content", "")) for message in messages)
    assert "cancelled" in rendered
    assert session.subagents.get_result(task_id).status == "cancelled"


def test_agent_session_cancel_agent_command_reports_unknown_task_without_keyerror_quotes(tmp_path):
    session = AgentSession(cwd=str(tmp_path), model=faux_model())

    messages = session.prompt("/cancel-agent subagent-deadbeef0000 user test")

    rendered = "\n".join(str(getattr(message, "content", "")) for message in messages)
    assert "Unknown subagent task: subagent-deadbeef0000" in rendered
    assert "'Unknown subagent task:" not in rendered


def test_supervisor_cancel_invokes_backend_cancel_hook(tmp_path):
    started = threading.Event()
    release = threading.Event()
    cancelled_task_ids: list[str] = []

    class CancellableBackend:
        name = "cancellable"

        def run(self, task):
            started.set()
            release.wait(1)
            return SubagentResult(
                task_id=task.id,
                backend=task.backend,
                role=task.role,
                status="completed",
                summary="late summary",
            )

        def cancel(self, task_id: str) -> None:
            cancelled_task_ids.append(task_id)
            release.set()

    supervisor = SubagentSupervisor()
    supervisor.register_backend(CancellableBackend())
    task_id = supervisor.spawn(SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path), backend="cancellable"))
    assert started.wait(1)

    result = supervisor.cancel(task_id, reason="not needed")

    assert result.status == "cancelled"
    assert cancelled_task_ids == [task_id]


def test_codex_backend_honors_cancel_requested_before_process_registration(tmp_path, monkeypatch):
    terminated: list[tuple[int, object]] = []

    class FakeProcess:
        pid = 4242
        returncode = -15

        def poll(self):
            return None

        def communicate(self, timeout=None):
            return "", ""

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(
        "appv23.coding_agent.subagents.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(),
    )
    monkeypatch.setattr(
        "appv23.coding_agent.subagents.os.killpg",
        lambda pid, signal: terminated.append((pid, signal)),
    )

    backend = CodexExecBackend(codex_bin="codex")
    task = SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path), backend="codex")

    backend.cancel(task.id)
    backend.run(task)

    assert terminated
    assert terminated[0][0] == 4242


def test_agent_session_spawn_subagent_tool_cancels_wait_on_parent_abort(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def slow_backend(task):
        started.set()
        release.wait(1)
        return "late summary"

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", slow_backend))
    signal = AbortSignal()
    result_holder: dict[str, object] = {}
    errors: list[BaseException] = []

    def run_tool() -> None:
        try:
            result_holder["result"] = session._execute_spawn_subagent_tool(
                "tool-call",
                {"role": "reviewer", "goal": "review", "wait": True, "timeoutSeconds": 30},
                signal=signal,
            )
        except BaseException as error:  # noqa: BLE001 - surfaced below for regression clarity.
            errors.append(error)

    thread = threading.Thread(target=run_tool)
    thread.start()
    assert started.wait(1)
    signal.abort()
    thread.join(timeout=2)
    release.set()

    assert not thread.is_alive()
    assert errors == []
    result = result_holder["result"]
    assert result.details["status"] == "cancelled"
    assert result.details["errors"] == ["Cancelled by parent abort."]
    assert session.subagents.get_result(str(result.details["taskId"])).status == "cancelled"


def test_agent_session_wait_subagent_tool_cancels_wait_on_parent_abort(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def slow_backend(task):
        started.set()
        release.wait(1)
        return "late summary"

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", slow_backend))
    task_id = session.subagents.spawn(SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path)))
    assert started.wait(1)

    signal = AbortSignal()
    signal.abort()
    result = session._execute_wait_subagent_tool("tool-call", {"taskId": task_id}, signal=signal)
    release.set()

    assert result.details["status"] == "cancelled"
    assert result.details["errors"] == ["Cancelled by parent abort."]
    assert session.subagents.get_result(task_id).status == "cancelled"


def test_agent_session_delegate_command_cancels_wait_on_parent_abort(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def slow_backend(task):
        started.set()
        release.wait(1)
        return "late summary"

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", slow_backend))
    messages_holder: dict[str, list[object]] = {}
    errors: list[BaseException] = []

    def run_command() -> None:
        try:
            messages_holder["messages"] = session.prompt("/delegate reviewer review")
        except BaseException as error:  # noqa: BLE001 - surfaced below for regression clarity.
            errors.append(error)

    thread = threading.Thread(target=run_command)
    thread.start()
    assert started.wait(1)
    session.agent.abort()
    thread.join(timeout=2)
    release.set()

    assert not thread.is_alive()
    assert errors == []
    rendered = "\n".join(str(getattr(message, "content", "")) for message in messages_holder["messages"])
    assert "cancelled" in rendered
    assert session.subagents.list_results()[0].status == "cancelled"


def test_agent_session_delegate_command_uses_fresh_abort_signal_after_previous_abort(tmp_path):
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", lambda task: "done"))
    session.agent.abort()

    messages = session.prompt("/delegate reviewer review")

    rendered = "\n".join(str(getattr(message, "content", "")) for message in messages)
    assert "completed" in rendered
    assert session.subagents.list_results()[0].status == "completed"


def test_agent_session_extension_context_get_signal_refreshes_idle_abort_signal(tmp_path):
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.agent.abort()

    signal = session.create_replaced_session_context().getSignal()

    assert signal is session.agent.signal
    assert signal.aborted is False


def test_agent_session_extension_context_get_signal_preserves_fresh_idle_signal(tmp_path):
    session = AgentSession(cwd=str(tmp_path), model=faux_model())

    first = session.create_replaced_session_context().getSignal()
    second = session.create_replaced_session_context().getSignal()

    assert first is second
    assert second is session.agent.signal
    assert second.aborted is False


def test_agent_session_extension_context_get_signal_preserves_active_command_signal(tmp_path):
    session = AgentSession(cwd=str(tmp_path), model=faux_model())

    def inspect_signal(active_signal):
        context_signal = session.create_replaced_session_context().getSignal()
        return active_signal, context_signal, session.agent.signal

    active_signal, context_signal, agent_signal = session._with_command_abort_signal(inspect_signal)

    assert active_signal is context_signal
    assert context_signal is agent_signal
    assert context_signal.aborted is False


def test_agent_session_extension_command_get_signal_is_stable_while_command_runs(tmp_path):
    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    seen_signals: list[object] = []

    def handler(_args, ctx):
        seen_signals.append(ctx.getSignal())
        seen_signals.append(ctx.getSignal())
        seen_signals.append(session.agent.signal)
        return []

    session.extension_runner.register_command("signal", {"description": "Inspect signal", "handler": handler})

    session.prompt("/signal")

    assert len(seen_signals) == 3
    assert seen_signals[0] is seen_signals[1]
    assert seen_signals[1] is seen_signals[2]


def test_agent_session_extension_context_can_cancel_subagent(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def slow_backend(task):
        started.set()
        release.wait(1)
        return "late summary"

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", slow_backend))
    task_id = session.subagents.spawn(SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path)))
    assert started.wait(1)

    result = session.create_replaced_session_context().cancelSubagent(task_id, "not needed")
    release.set()

    assert result["status"] == "cancelled"
    assert result["errors"] == ["not needed"]


def test_agent_session_shutdown_cancels_subagent_supervisor(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def slow_backend(task):
        started.set()
        release.wait(1)
        return "late summary"

    session = AgentSession(cwd=str(tmp_path), model=faux_model())
    session.subagents.register_backend(CallableSubagentBackend("internal", slow_backend))
    task_id = session.subagents.spawn(SubagentTask(role="reviewer", goal="review", cwd=str(tmp_path)))
    assert started.wait(1)

    session.shutdown()
    release.set()

    assert session.subagents.get_result(task_id).status == "cancelled"
