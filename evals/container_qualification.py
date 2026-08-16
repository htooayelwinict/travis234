"""Credential-free runtime qualification used by the release-container smoke."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import hashlib
import json
import os
import sys
import shlex
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import psutil

from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.ai.types import UserMessage
from travis.app import CodingApp
from travis.coding_agent.execution_backend import TrustedLocalBackend
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.language_services.jsonrpc import JsonRpcStdioClient
from travis.coding_agent.language_services.types import LanguageServiceLimits
from travis.coding_agent.memory import MemorySettings, MemoryStore
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.operations import OperationStore
from travis.coding_agent.operations.recovery import OperationRecovery
from travis.coding_agent.policy import ToolPolicyEngine, ToolPolicySettings
from travis.coding_agent.processes.local import create_local_process_transport
from travis.coding_agent.processes.service import ProcessSessionService
from travis.coding_agent.processes.types import ProcessLaunchRequest, ProcessOwner
from travis.coding_agent.subagents import (
    CallableSubagentBackend,
    SubagentSupervisor,
    SubagentTask,
)
from travis.coding_agent.tools.types import ToolDefinition


_CREDENTIAL_NAMES = (
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "TRAVIS_COMPRESSION_API_KEY",
    "TRAVIS234_COMPRESSION_LLM_API_KEY",
    "TRAVIS234_WORKER_LLM_API_KEY",
)


@dataclass(frozen=True)
class ContainerQualification:
    user: str
    home: str
    credential_env_absent: bool
    manual_compaction: bool
    automatic_compaction: bool
    managed_process_reaped: bool
    artifact_restart: bool
    policy_audit: bool
    policy_enforce_denial: bool
    lsp_fixture_clean_shutdown: bool
    typed_supervision_cancel: bool
    operation_uncertain_no_replay: bool
    memory_disabled: bool
    memory_project_isolation: bool

    @property
    def passed(self) -> bool:
        return (
            self.credential_env_absent
            and self.manual_compaction
            and self.automatic_compaction
            and self.managed_process_reaped
            and self.artifact_restart
            and self.policy_audit
            and self.policy_enforce_denial
            and self.lsp_fixture_clean_shutdown
            and self.typed_supervision_cancel
            and self.operation_uncertain_no_replay
            and self.memory_disabled
            and self.memory_project_isolation
        )


def run_container_qualification(
    workspace: str | Path,
    *,
    require_container: bool,
) -> ContainerQualification:
    root = Path(workspace).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    manual, automatic = _exercise_compaction(root)
    policy_audit, policy_enforce_denial = _exercise_policy()
    memory_disabled, memory_project_isolation = _exercise_memory(root)
    result = ContainerQualification(
        user=getpass.getuser(),
        home=str(Path.home()),
        credential_env_absent=not any(name in os.environ for name in _CREDENTIAL_NAMES),
        manual_compaction=manual,
        automatic_compaction=automatic,
        managed_process_reaped=_exercise_process_cleanup(root),
        artifact_restart=_exercise_artifact_restart(root),
        policy_audit=policy_audit,
        policy_enforce_denial=policy_enforce_denial,
        lsp_fixture_clean_shutdown=_exercise_lsp_cleanup(root),
        typed_supervision_cancel=_exercise_typed_supervision(root),
        operation_uncertain_no_replay=_exercise_operation_recovery(root),
        memory_disabled=memory_disabled,
        memory_project_isolation=memory_project_isolation,
    )
    if require_container and (result.user != "travis" or result.home != "/travis-home"):
        raise RuntimeError(
            f"release container identity mismatch: user={result.user!r}, home={result.home!r}"
        )
    if not result.passed:
        raise RuntimeError(f"release container qualification failed: {asdict(result)}")
    return result


def _exercise_compaction(root: Path) -> tuple[bool, bool]:
    def make_app(name: str) -> CodingApp:
        workspace = root / name
        workspace.mkdir(parents=True, exist_ok=True)
        registry = ModelRegistry.in_memory()
        registry.runtime.clear_providers()
        registry.runtime.set_provider(
            create_faux_provider(
                lambda model, _context: text_response_events(model, "qualification ok")
            )
        )
        model = faux_model()
        model.context_window = 32_000
        model.max_tokens = 4_096
        return CodingApp(
            cwd=str(workspace),
            model=model,
            context_length=model.context_window,
            summarizer=lambda _prompt: "## Goal\nPreserve the qualification handoff.",
            enable_tui=False,
            project_trust_override=False,
            model_registry=registry,
        )

    def seed(app: CodingApp) -> int:
        messages = [
            UserMessage(content=f"qualification history {index} " + ("x" * 2_000))
            for index in range(72)
        ]
        app.session.agent.state.messages.extend(messages)
        return len(messages)

    manual_app = make_app("manual-compaction")
    try:
        before = seed(manual_app)
        manual_app.session.compact(focus="container qualification")
        manual = manual_app.compressor.compression_count > 0 and len(manual_app.messages) < before
    finally:
        manual_app.close()

    automatic_app = make_app("automatic-compaction")
    try:
        seed(automatic_app)
        automatic_app.run_turn("finish the qualification")
        automatic = automatic_app.compressor.compression_count > 0
    finally:
        automatic_app.close()
    return manual, automatic


def _exercise_artifact_restart(root: Path) -> bool:
    workspace = root / "artifact-restart"
    workspace.mkdir(parents=True, exist_ok=True)
    session_path = workspace / "session.jsonl"
    agent_dir = workspace / "agent"
    source = workspace / "complete.log"
    source.write_text("container artifact restart", encoding="utf-8")
    session = AgentSession(
        cwd=str(workspace),
        model=faux_model(),
        tools=[],
        session_path=str(session_path),
        agent_dir=str(agent_dir),
    )
    try:
        reference = session._artifacts.promote(  # noqa: SLF001 - qualification probes the durable boundary.
            source, "qualification", retained=True
        )
    finally:
        session.dispose()
    resumed = AgentSession(
        cwd=str(workspace),
        model=faux_model(),
        tools=[],
        session_path=str(session_path),
        agent_dir=str(agent_dir),
    )
    try:
        resolved = resumed._artifacts.resolve_read(reference.id)  # noqa: SLF001
        return (
            resolved is not None
            and resolved.read_text(encoding="utf-8") == "container artifact restart"
        )
    finally:
        resumed.dispose()


def _policy_tool(*, effects: frozenset[str]) -> ToolDefinition:
    return ToolDefinition(
        name="qualification_probe",
        label="qualification probe",
        description="Credential-free policy qualification",
        parameters={"type": "object"},
        execute=lambda *_args, **_kwargs: None,
        effects=effects,
    )


def _exercise_policy() -> tuple[bool, bool]:
    undeclared = _policy_tool(effects=frozenset())
    audit = ToolPolicyEngine(
        ToolPolicySettings(mode="audit", auto_allow_effects=frozenset())
    ).evaluate(undeclared, {})
    network = _policy_tool(effects=frozenset({"network"}))
    denied = ToolPolicyEngine(
        ToolPolicySettings(mode="enforce", auto_allow_effects=frozenset({"read"}))
    ).evaluate(network, {})
    return (
        audit.allow and audit.reason_code == "undeclared_effects",
        not denied.allow and denied.reason_code == "approval_required",
    )


_LSP_FIXTURE = r'''
import json, sys
def frame(value):
    body = json.dumps(value, separators=(",", ":")).encode()
    return f"Content-Length: {len(body)}\r\n\r\n".encode() + body
while True:
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            raise SystemExit(0)
        if line == b"\r\n":
            break
        name, value = line.decode("ascii").split(":", 1)
        headers[name.casefold()] = value.strip()
    request = json.loads(sys.stdin.buffer.read(int(headers["content-length"])))
    if request.get("method") == "exit":
        raise SystemExit(0)
    if "id" in request:
        result = None if request.get("method") == "shutdown" else request.get("params")
        sys.stdout.buffer.write(frame({"jsonrpc":"2.0","id":request["id"],"result":result}))
        sys.stdout.buffer.flush()
'''


def _exercise_lsp_cleanup(root: Path) -> bool:
    async def scenario() -> bool:
        client = JsonRpcStdioClient(
            sys.executable,
            ("-u", "-c", _LSP_FIXTURE),
            cwd=root,
            limits=LanguageServiceLimits(request_timeout_seconds=2),
        )
        await client.start()
        pid = client.process_id
        if pid is None:
            await client.close()
            return False
        response = await client.request("fixture/echo", {"ok": True})
        client.initialized = True
        await client.close()
        return (
            response == {"ok": True}
            and client.pending_request_count == 0
            and client.process_id is None
            and not psutil.pid_exists(pid)
        )

    return asyncio.run(scenario())


def _exercise_typed_supervision(root: Path) -> bool:
    entered = threading.Event()
    release = threading.Event()
    supervisor = SubagentSupervisor(max_threads=1)
    supervisor.register_backend(
        CallableSubagentBackend(
            "internal",
            lambda _task: (
                entered.set(),
                release.wait(2),
                json.dumps(
                    {"summary": "done", "output": {"ok": True}, "artifacts": []}
                ),
            )[2],
        )
    )
    task_id = supervisor.spawn(
        SubagentTask(
            role="worker",
            goal="qualification",
            cwd=str(root),
            role_definition_name="qualification-worker",
            result_schema={
                "type": "object",
                "required": ["ok"],
                "properties": {"ok": {"const": True}},
            },
        )
    )
    if not entered.wait(1):
        release.set()
        supervisor.shutdown(wait=True)
        return False
    cancelled = supervisor.cancel(task_id, "qualification cancellation")
    snapshot = supervisor.snapshot()
    release.set()
    supervisor.shutdown(wait=True)
    item = next((candidate for candidate in snapshot.tasks if candidate.task_id == task_id), None)
    return (
        cancelled.status == "cancelled"
        and item is not None
        and item.status == "cancelled"
        and item.controllable is False
    )


def _exercise_operation_recovery(root: Path) -> bool:
    store = OperationStore(root / "operation-recovery.sqlite3")
    runtime_id = "a" * 32
    try:
        store.open_runtime(runtime_id, 999_999_999, 1.0, 1)
        operation = store.create_operation(runtime_id, "b" * 64, "turn", 2)
        store.begin_effect(operation.operation_id, "tool", "probe", "c" * 64, 3)
        report = OperationRecovery.inspect(
            store,
            now_ms=100_000,
            process_lookup=lambda pid: (_ for _ in ()).throw(psutil.NoSuchProcess(pid)),
        )
        snapshot = store.snapshot(operation.operation_id)
        return (
            report.uncertain_effect_count == 1
            and report.uncertain_operation_count == 1
            and snapshot is not None
            and snapshot.operation.state == "uncertain"
            and len(snapshot.effects) == 1
            and snapshot.effects[0].state == "uncertain"
            and snapshot.effects[0].replay_policy == "never"
        )
    finally:
        store.close()


def _exercise_memory(root: Path) -> tuple[bool, bool]:
    disabled_path = root / "disabled-memory.sqlite3"
    disabled = MemorySettings().enabled is False and not disabled_path.exists()
    store = MemoryStore(
        root / "enabled-memory.sqlite3",
        settings=MemorySettings(enabled=True),
    )
    project_a = hashlib.sha256(b"qualification-project-a").hexdigest()
    project_b = hashlib.sha256(b"qualification-project-b").hexdigest()
    try:
        retained = store.retain(
            "benign qualification fact",
            tags=["qualification"],
            scope="project",
            project_key=project_a,
            provenance="user_requested",
            now_ms=1,
        )
        visible = store.recall(
            "qualification", project_key=project_a, now_ms=2
        )
        hidden = store.recall(
            "qualification", project_key=project_b, now_ms=2
        )
        isolated = [fact.memory_id for fact in visible] == [retained.memory_id] and not hidden
    finally:
        store.close()
    return disabled, isolated


def _exercise_process_cleanup(root: Path) -> bool:
    process_root = root / "managed-process"
    process_root.mkdir(parents=True, exist_ok=True)
    pid_path = process_root / "child.pid"
    service = ProcessSessionService(
        directory=process_root / "state",
        termination_grace_seconds=0.2,
        drain_timeout_seconds=0.2,
    )
    owner = ProcessOwner("container-qualification", str(process_root), "agent")
    request = ProcessLaunchRequest(
        command=f"printf '%s' $$ > {shlex.quote(str(pid_path))}; exec /bin/sleep 30",
        cwd=str(process_root),
        env={"PATH": "/usr/bin:/bin"},
        shell_path="/bin/bash",
    )
    backend = TrustedLocalBackend()
    try:
        service.start(
            owner,
            request,
            lambda launch: create_local_process_transport(launch, backend),
            yield_time_ms=100,
        )
        deadline = time.monotonic() + 2
        while not pid_path.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not pid_path.is_file():
            return False
        pid = int(pid_path.read_text(encoding="utf-8"))
    finally:
        service.close()

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.02)
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--require-container", action="store_true")
    args = parser.parse_args(argv)
    result = run_container_qualification(
        args.workspace,
        require_container=args.require_container,
    )
    print(json.dumps({**asdict(result), "passed": result.passed}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
