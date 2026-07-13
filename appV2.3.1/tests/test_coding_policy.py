from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError

import pytest

from appv231.coding_agent.capabilities import CapabilityViolation, WorkspaceCapability
from appv231.coding_agent.artifacts import ArtifactRegistry
from appv231.coding_agent.execution_backend import ContainerSandboxBackend, TrustedLocalBackend
from appv231.coding_agent.tools.atomic_file import atomic_replace_text
from appv231.coding_agent.policies.package_consent import PackageMutationPolicy
from appv231.coding_agent.policies.pipeline import PolicyPipeline
from appv231.coding_agent.policies.types import (
    Allow,
    Block,
    CodingTurnContext,
    RequireConsent,
    ToolCallView,
    TurnCapabilities,
)
from appv231.coding_agent.policies.tool_guardrails import (
    ToolCallGuardrailConfig,
    ToolCallGuardrailController,
    _tool_call_may_change_state,
)


class StubPolicy:
    def __init__(self, name, decision, visited) -> None:
        self.name = name
        self.decision = decision
        self.visited = visited

    def evaluate(self, call, context):
        self.visited.append(self.name)
        return self.decision


def _context(capabilities: TurnCapabilities | None = None) -> CodingTurnContext:
    return CodingTurnContext(
        cwd="/workspace",
        latest_user_message="document npm install without running it",
        capabilities=capabilities or TurnCapabilities(),
        tool_catalog=("bash", "read"),
        run_id="run-1",
        turn_id="turn-1",
    )


def _bash(command: str) -> ToolCallView:
    return ToolCallView(id="call-1", name="bash", args={"command": command})


def test_policy_pipeline_returns_first_non_allow_decision() -> None:
    visited: list[str] = []
    pipeline = PolicyPipeline(
        [
            StubPolicy("first", Allow(), visited),
            StubPolicy("consent", RequireConsent("package_mutation", "approval required"), visited),
            StubPolicy("never", Block("late", "must not run"), visited),
        ]
    )

    assert pipeline.evaluate(_bash("npm install x"), _context()) == RequireConsent(
        "package_mutation", "approval required"
    )
    assert visited == ["first", "consent"]


def test_coding_turn_context_is_immutable() -> None:
    context = _context()
    with pytest.raises(FrozenInstanceError):
        context.cwd = "/elsewhere"  # type: ignore[misc]


@pytest.mark.parametrize(
    "command",
    [
        "npm install left-pad",
        "/usr/bin/npm install left-pad",
        "env npm install left-pad",
        "python -m pip install requests",
    ],
)
def test_package_mutation_requires_structured_capability(command: str) -> None:
    decision = PackageMutationPolicy().evaluate(_bash(command), _context())
    assert isinstance(decision, RequireConsent)


def test_package_mutation_consumes_exactly_one_grant() -> None:
    capabilities = TurnCapabilities()
    capabilities.grant("package_mutation", uses=1)
    policy = PackageMutationPolicy()

    assert isinstance(policy.evaluate(_bash("npm install x"), _context(capabilities)), Allow)
    assert isinstance(policy.evaluate(_bash("npm install y"), _context(capabilities)), RequireConsent)


def test_concurrent_grant_is_consumed_by_next_same_turn_package_call() -> None:
    capabilities = TurnCapabilities()
    policy = PackageMutationPolicy()
    ready = threading.Event()
    evaluate = threading.Event()
    decisions = []

    def protected_call() -> None:
        ready.set()
        assert evaluate.wait(timeout=1)
        decisions.append(policy.evaluate(_bash("npm install x"), _context(capabilities)))

    worker = threading.Thread(target=protected_call)
    worker.start()
    assert ready.wait(timeout=1)
    capabilities.grant("package_mutation", uses=1)
    evaluate.set()
    worker.join(timeout=1)

    assert decisions == [Allow()]
    assert capabilities.remaining("package_mutation") == 0


def test_process_write_package_mutation_requires_and_consumes_capability() -> None:
    capabilities = TurnCapabilities()
    policy = PackageMutationPolicy()
    call = ToolCallView(
        id="process-write",
        name="process",
        args={"action": "write", "session_id": "proc_x", "input": "npm install left-pad\n"},
    )

    assert isinstance(policy.evaluate(call, _context(capabilities)), RequireConsent)
    capabilities.grant("package_mutation", uses=1)
    assert isinstance(policy.evaluate(call, _context(capabilities)), Allow)
    assert isinstance(policy.evaluate(call, _context(capabilities)), RequireConsent)


def test_process_non_package_input_and_observations_do_not_consume_capability() -> None:
    capabilities = TurnCapabilities()
    capabilities.grant("package_mutation", uses=1)
    policy = PackageMutationPolicy()

    for call in (
        ToolCallView(id="poll", name="process", args={"action": "poll", "session_id": "proc_x", "cursor": 0}),
        ToolCallView(id="write", name="process", args={"action": "write", "session_id": "proc_x", "input": "y\n"}),
    ):
        assert isinstance(policy.evaluate(call, _context(capabilities)), Allow)

    assert isinstance(policy.evaluate(_bash("npm install left-pad"), _context(capabilities)), Allow)


def test_process_write_package_detection_is_explicitly_best_effort_for_fragments() -> None:
    call = ToolCallView(
        id="fragment",
        name="process",
        args={"action": "write", "session_id": "proc_x", "input": "npm inst"},
    )

    assert isinstance(PackageMutationPolicy().evaluate(call, _context()), Allow)


def test_prompt_text_never_grants_package_mutation() -> None:
    context = _context()
    assert "npm install" in context.latest_user_message
    assert isinstance(PackageMutationPolicy().evaluate(_bash("npm install x"), context), RequireConsent)


def test_blocking_disabled_disables_loop_halts_but_keeps_guidance() -> None:
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(blocking_enabled=False, guidance_enabled=True)
    )
    decisions = [
        controller.after_call("read", {"path": "missing"}, "File not found: missing", failed=True)
        for _ in range(12)
    ]

    assert any(decision.action == "warn" for decision in decisions)
    assert all(decision.action not in {"block", "halt"} for decision in decisions)
    assert controller.before_call("read", {"path": "missing"}).action == "allow"


def test_process_poll_and_list_are_observations_but_controls_are_mutations() -> None:
    assert _tool_call_may_change_state("process", {"action": "poll", "session_id": "proc_x", "cursor": 0}) is False
    assert _tool_call_may_change_state("process", {"action": "wait", "session_id": "proc_x", "cursor": 0}) is False
    assert _tool_call_may_change_state("process", {"action": "list"}) is False
    for action in ("write", "resize", "interrupt", "terminate", "kill"):
        assert _tool_call_may_change_state("process", {"action": action}) is True


def test_process_cooperative_same_cursor_polls_do_not_trigger_no_progress() -> None:
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            no_progress_warn_after=2,
            no_progress_block_after=3,
            consecutive_no_progress_warn_after=99,
            consecutive_no_progress_block_after=99,
        )
    )
    args = {"action": "poll", "session_id": "proc_x", "cursor": 10}

    first = controller.after_call("process", args, "still running", failed=False)
    second = controller.after_call("process", args, "still running", failed=False)
    third = controller.after_call("process", {**args, "yield_time_ms": 3000}, "still running", failed=False)

    assert first.action == "allow"
    assert second.action == "allow"
    assert third.action == "allow"


def test_process_zero_wait_same_cursor_busy_poll_warns_then_halts() -> None:
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(
            no_progress_warn_after=2,
            no_progress_block_after=3,
            consecutive_no_progress_warn_after=99,
            consecutive_no_progress_block_after=99,
        )
    )
    args = {"action": "poll", "session_id": "proc_x", "cursor": 10, "yield_time_ms": 0}

    first = controller.after_call("process", args, "still running", failed=False)
    second = controller.after_call("process", args, "still running", failed=False)
    third = controller.after_call("process", args, "still running", failed=False)

    assert first.action == "allow"
    assert second.action == "warn"
    assert second.code == "idempotent_no_progress_warning"
    assert third.action == "halt"
    assert third.code == "idempotent_no_progress_block"


def test_process_advancing_cursor_is_progress_even_when_status_text_matches() -> None:
    controller = ToolCallGuardrailController(
        ToolCallGuardrailConfig(no_progress_warn_after=2, no_progress_block_after=3)
    )

    first = controller.after_call(
        "process",
        {"action": "poll", "session_id": "proc_x", "cursor": 0},
        "still running",
        failed=False,
    )
    second = controller.after_call(
        "process",
        {"action": "poll", "session_id": "proc_x", "cursor": 20},
        "still running",
        failed=False,
    )

    assert first.action == "allow"
    assert second.action == "allow"


@pytest.mark.parametrize("requested", ["../outside.txt", "sub/../../outside.txt"])
def test_workspace_capability_rejects_relative_escape(tmp_path, requested: str) -> None:
    workspace = tmp_path / "work"
    workspace.mkdir()
    with pytest.raises(CapabilityViolation, match="outside_workspace"):
        WorkspaceCapability(workspace).resolve(requested, access="read")


def test_workspace_capability_uses_path_ancestry_not_prefix(tmp_path) -> None:
    root = tmp_path / "a"
    root.mkdir()
    sibling = tmp_path / "abc" / "secret.txt"
    sibling.parent.mkdir()
    sibling.write_text("secret", encoding="utf-8")

    with pytest.raises(CapabilityViolation, match="outside_workspace"):
        WorkspaceCapability(root).resolve(str(sibling), access="read")


@pytest.mark.parametrize("access", ["read", "write"])
def test_workspace_capability_rejects_symlink_escape(tmp_path, access: str) -> None:
    root = tmp_path / "work"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(CapabilityViolation, match="outside_workspace"):
        WorkspaceCapability(root).resolve("link/file.txt", access=access)


def test_artifact_registry_authorizes_only_exact_registered_path(tmp_path) -> None:
    registry = ArtifactRegistry()
    artifact = tmp_path / "tool-output"
    artifact.write_text("complete", encoding="utf-8")
    other = tmp_path / "other"
    other.write_text("private", encoding="utf-8")

    ref = registry.register(artifact, kind="bash-output", access="read")

    assert registry.resolve_read(ref.id) == artifact.resolve()
    assert registry.resolve_read(str(artifact)) == artifact.resolve()
    assert registry.resolve_read(str(other)) is None


def test_artifact_registry_cleanup_is_explicit(tmp_path) -> None:
    artifact = tmp_path / "tool-output"
    artifact.write_text("complete", encoding="utf-8")
    registry = ArtifactRegistry()
    registry.register(artifact, kind="bash-output", access="read")

    registry.close(remove_files=False)
    assert artifact.exists()

    cleanup = ArtifactRegistry()
    cleanup.register(artifact, kind="bash-output", access="read")
    cleanup.close(remove_files=True)
    assert not artifact.exists()


def test_local_backend_never_claims_filesystem_containment() -> None:
    backend = TrustedLocalBackend()
    assert backend.mode == "trusted"
    assert backend.filesystem_contained is False


def test_container_backend_requires_sandbox_marker(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("APPV231_SANDBOX", raising=False)
    with pytest.raises(RuntimeError, match="sandbox marker"):
        ContainerSandboxBackend(tmp_path / "workspace", tmp_path / "agent-home")


def test_atomic_write_failure_preserves_original(tmp_path, monkeypatch) -> None:
    target = tmp_path / "file.txt"
    target.write_text("original", encoding="utf-8")

    def interrupted(*_args):
        raise OSError("interrupted")

    monkeypatch.setattr("appv231.coding_agent.tools.atomic_file.os.replace", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        atomic_replace_text(target, "replacement")
    assert target.read_text(encoding="utf-8") == "original"


def test_atomic_write_preserves_executable_mode(tmp_path) -> None:
    target = tmp_path / "script.sh"
    target.write_text("old", encoding="utf-8")
    target.chmod(0o755)

    atomic_replace_text(target, "new")

    assert target.read_text(encoding="utf-8") == "new"
    assert target.stat().st_mode & 0o777 == 0o755
