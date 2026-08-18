from __future__ import annotations

import json
from pathlib import Path

from travis.ai.types import Model
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.subagents import CallableSubagentBackend, SubagentResult, SubagentTask


def _model() -> Model:
    return Model(
        id="faux/test",
        name="Faux",
        api="openai-completions",
        provider="faux",
        base_url="https://invalid",
        context_window=1000,
        max_tokens=100,
    )


def _typed_task(tmp_path: Path, *, policy: str) -> SubagentTask:
    return SubagentTask(
        role="typed",
        goal="produce",
        cwd=str(tmp_path),
        role_definition_name="typed",
        result_schema={},
        artifact_policy=policy,
    )


def test_none_policy_ignores_declared_artifacts(tmp_path: Path) -> None:
    report = tmp_path / "report.txt"
    report.write_text("evidence", encoding="utf-8")
    session = AgentSession(cwd=str(tmp_path), model=_model(), agent_dir=str(tmp_path / "agent"))
    task = _typed_task(tmp_path, policy="none")
    session.subagents.register_backend(
        CallableSubagentBackend(
            "typed-backend",
            lambda current: SubagentResult(
                task_id=current.id,
                backend=current.backend,
                role=current.role,
                status="completed",
                summary="done",
                artifacts=["report.txt"],
            ),
        )
    )
    task = SubagentTask(**{**task.__dict__, "backend": "typed-backend"})
    try:
        task_id = session.subagents.spawn(task)
        result = session._prepare_public_subagent_result(session.subagents.wait(task_id, 2))
        assert result.artifacts == []
    finally:
        session.shutdown()


def test_declared_policy_promotes_only_regular_utf8_workspace_files(tmp_path: Path) -> None:
    good = tmp_path / "good.txt"
    good.write_text("evidence", encoding="utf-8")
    bad = tmp_path / "bad.bin"
    bad.write_bytes(b"\xff\xfe")
    directory = tmp_path / "artifact-dir"
    directory.mkdir()
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = tmp_path / "escape.txt"
    link.symlink_to(outside)
    session = AgentSession(cwd=str(tmp_path), model=_model(), agent_dir=str(tmp_path / "agent"))
    task = _typed_task(tmp_path, policy="declared")
    session.subagents.register_backend(CallableSubagentBackend("typed-backend", lambda _task: "unused"))
    task = SubagentTask(**{**task.__dict__, "backend": "typed-backend"})
    result = SubagentResult(
        task_id=task.id,
        backend=task.backend,
        role=task.role,
        status="completed",
        summary=f"paths {good} {bad} {directory} {link}",
        artifacts=["good.txt", "bad.bin", "artifact-dir", "escape.txt", "missing.txt"],
    )
    try:
        session.subagents.spawn(task)
        prepared = session._prepare_public_subagent_result(result)

        assert len(prepared.artifacts) == 1
        assert prepared.artifacts[0].startswith("artifact-")
        assert str(tmp_path) not in prepared.summary
        assert len(prepared.errors) == 4
        assert session._artifacts.resolve_read(prepared.artifacts[0]).read_text() == "evidence"
    finally:
        session.shutdown()


def test_declared_and_trace_promotes_sanitized_trace(tmp_path: Path) -> None:
    session = AgentSession(cwd=str(tmp_path), model=_model(), agent_dir=str(tmp_path / "agent"))
    task = _typed_task(tmp_path, policy="declared_and_trace")
    session.subagents.register_backend(CallableSubagentBackend("typed-backend", lambda _task: "unused"))
    task = SubagentTask(**{**task.__dict__, "backend": "typed-backend"})
    result = SubagentResult(
        task_id=task.id,
        backend=task.backend,
        role=task.role,
        status="completed",
        summary="done",
        tool_trace=[{"toolCallId": "1", "toolName": "read", "argsPreview": "bounded"}],
    )
    try:
        session.subagents.spawn(task)
        prepared = session._prepare_public_subagent_result(result)

        assert len(prepared.artifacts) == 1
        trace_path = session._artifacts.resolve_read(prepared.artifacts[0])
        payload = json.loads(trace_path.read_text(encoding="utf-8"))
        assert payload[0]["toolName"] == "read"
        assert set(payload[0]) <= {
            "toolCallId", "toolName", "status", "argsPreview", "resultPreview", "elapsedMs"
        }
    finally:
        session.shutdown()
