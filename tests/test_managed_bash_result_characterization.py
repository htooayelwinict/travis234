"""Direct characterization coverage for managed bash result assembly."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

from travis.agent.types import AgentToolResult
from travis.ai.types import TextContent
from travis.coding_agent.artifact_store import ArtifactPromotionError
from travis.coding_agent.artifacts import ArtifactRef, ArtifactRegistry
from travis.coding_agent.processes.service import ProcessSessionService
from travis.coding_agent.processes.types import ProcessOwner, ProcessSnapshot, ProcessState
from travis.coding_agent.tools import bash as bash_tool_module
from travis.coding_agent.tools.truncate import TruncationResult

_SESSION_ID = "proc_0123456789abcdef0123456789abcdef"


def _result_text(result: AgentToolResult) -> str:
    assert len(result.content) == 1
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


def _tail(*, content: str = "tail output", truncated: bool = False) -> TruncationResult:
    return TruncationResult(
        content=content,
        truncated=truncated,
        truncated_by="bytes" if truncated else None,
        output_lines=3 if truncated else 1,
        total_lines=10 if truncated else 1,
        first_line_exceeds_limit=False,
        total_bytes=999 if truncated else len(content.encode("utf-8")),
        output_bytes=len(content.encode("utf-8")),
        last_line_partial=truncated,
        max_lines=2_000,
        max_bytes=51_200,
    )


def _snapshot(
    *,
    state: ProcessState = ProcessState.EXITED,
    output: str = "snapshot output",
    exit_code: int | None = 0,
    durable_output: bool = False,
    full_output_path: str | None = None,
    failure_code: str | None = None,
) -> ProcessSnapshot:
    return ProcessSnapshot(
        session_id=_SESSION_ID,
        state=state,
        output=output,
        cursor=7,
        next_cursor=11,
        output_size=99,
        exit_code=exit_code,
        tty=True,
        elapsed_ms=123,
        command="python worker.py",
        cwd="/workspace",
        suggested_poll_delay_ms=250,
        durable_output=durable_output,
        full_output_path=full_output_path,
        failure_code=failure_code,
    )


class _RecordedProcessService(ProcessSessionService):
    def __init__(
        self,
        directory: Path,
        tail: TruncationResult,
        events: list[tuple[object, ...]],
        *,
        export_path: Path | None = None,
    ) -> None:
        super().__init__(directory=directory)
        self.recorded_tail = tail
        self.events = events
        self.export_path = export_path

    def tail_snapshot(
        self,
        owner: ProcessOwner,
        session_id: str,
    ) -> TruncationResult:
        self.events.append(("tail", owner, session_id))
        return self.recorded_tail

    def export_output(
        self,
        owner: ProcessOwner,
        session_id: str,
        directory: str | Path,
    ) -> Path:
        self.events.append(("export", owner, session_id, str(directory)))
        if self.export_path is None:
            raise AssertionError("unexpected export")
        self.export_path.write_text("complete output", encoding="utf-8")
        return self.export_path


_PromotionFailure = Literal["artifact", "blank_artifact", "long_artifact", "os"]


class _RecordedArtifacts(ArtifactRegistry):
    def __init__(
        self,
        *,
        durable: bool,
        events: list[tuple[object, ...]],
        failure: _PromotionFailure | None = None,
    ) -> None:
        super().__init__()
        self.recorded_durable = durable
        self.events = events
        self.failure = failure

    @property
    def is_durable(self) -> bool:
        self.events.append(("is_durable", self.recorded_durable))
        return self.recorded_durable

    def register(
        self,
        path: Path,
        kind: str,
        access: Literal["read"] = "read",
        remove_on_close: bool = True,
    ) -> ArtifactRef:
        self.events.append(("register", path, kind, access, remove_on_close))
        return ArtifactRef(
            id="artifact-transient",
            path=path,
            kind=kind,
            access=access,
            remove_on_close=remove_on_close,
        )

    def promote(
        self,
        path: Path,
        kind: str,
        *,
        session_entry_id: str | None = None,
        tool_call_id: str | None = None,
        retained: bool = False,
    ) -> ArtifactRef:
        self.events.append(
            (
                "promote",
                path,
                kind,
                session_entry_id,
                tool_call_id,
                retained,
                path.exists(),
            )
        )
        if self.failure in {"artifact", "blank_artifact", "long_artifact"}:
            messages = {
                "artifact": " Artifact   storage\nlimit reached ",
                "blank_artifact": " \n ",
                "long_artifact": "x" * 300,
            }
            raise ArtifactPromotionError("physical_limit", messages[self.failure])
        if self.failure == "os":
            raise OSError("disk unavailable")
        return ArtifactRef(
            id="artifact-durable",
            path=path,
            kind=kind,
            remove_on_close=False,
        )


@pytest.mark.parametrize("exit_code", [None, 0])
def test_terminal_success_reads_tail_and_uses_exact_empty_fallback(
    tmp_path: Path,
    exit_code: int | None,
) -> None:
    owner = ProcessOwner("app", "/workspace", "agent")
    events: list[tuple[object, ...]] = []
    service = _RecordedProcessService(tmp_path / "processes", _tail(content=""), events)
    try:
        result = bash_tool_module._managed_bash_result(
            service,
            owner,
            _snapshot(output="ignored snapshot", exit_code=exit_code),
            None,
            None,
            None,
            tool_call_id="bash-call",
            input_open=False,
        )
    finally:
        service.close()

    assert events == [("tail", owner, _SESSION_ID)]
    assert _result_text(result) == "(no output)"
    assert result.details == {
        "status": "exited",
        "sessionId": _SESSION_ID,
        "cursor": 7,
        "nextCursor": 11,
        "outputSize": 99,
        "exitCode": exit_code,
        "tty": True,
        "elapsedMs": 123,
        "suggestedPollDelayMs": 250,
    }


@pytest.mark.parametrize(
    ("state", "exit_code", "failure_code", "timeout", "aborted", "expected"),
    [
        (
            ProcessState.EXITED,
            17,
            None,
            None,
            False,
            "tail output\n\nCommand exited with code 17",
        ),
        (
            ProcessState.TIMED_OUT,
            None,
            None,
            None,
            False,
            "tail output\n\nCommand timed out",
        ),
        (
            ProcessState.TIMED_OUT,
            None,
            None,
            1.25,
            False,
            "tail output\n\nCommand timed out after 1.25 seconds",
        ),
        (
            ProcessState.TERMINATED,
            None,
            None,
            None,
            True,
            "tail output\n\nCommand aborted",
        ),
        (
            ProcessState.TERMINATED,
            None,
            None,
            None,
            False,
            "tail output\n\nCommand terminated",
        ),
        (
            ProcessState.FAILED,
            None,
            "output_limit",
            None,
            False,
            "tail output\n\nCommand stopped after reaching the sanitized-output budget (not a timeout)",
        ),
        (
            ProcessState.FAILED,
            None,
            "spawn",
            None,
            False,
            "tail output\n\nCommand failed to execute",
        ),
    ],
)
def test_terminal_failure_branches_preserve_exact_runtime_error(
    tmp_path: Path,
    state: ProcessState,
    exit_code: int | None,
    failure_code: str | None,
    timeout: float | None,
    aborted: bool,
    expected: str,
) -> None:
    owner = ProcessOwner("app", "/workspace", "agent")
    events: list[tuple[object, ...]] = []
    service = _RecordedProcessService(tmp_path / "processes", _tail(), events)

    @dataclass(frozen=True)
    class _Signal:
        aborted: bool

    try:
        with pytest.raises(RuntimeError) as raised:
            bash_tool_module._managed_bash_result(
                service,
                owner,
                _snapshot(
                    state=state,
                    exit_code=exit_code,
                    failure_code=failure_code,
                ),
                _Signal(aborted),
                None,
                timeout,
                tool_call_id="bash-call",
                input_open=False,
            )
    finally:
        service.close()

    assert type(raised.value) is RuntimeError
    assert str(raised.value) == expected
    assert events == [("tail", owner, _SESSION_ID)]


@pytest.mark.parametrize(
    "state",
    [
        ProcessState.STARTING,
        ProcessState.RUNNING,
        ProcessState.STOPPING,
        ProcessState.DRAINING,
    ],
)
@pytest.mark.parametrize("input_open", [False, True])
def test_nonterminal_result_skips_tail_and_uses_snapshot_output_for_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: ProcessState,
    input_open: bool,
) -> None:
    owner = ProcessOwner("app", "/workspace", "agent")
    events: list[tuple[object, ...]] = []
    service = _RecordedProcessService(tmp_path / "processes", _tail(), events)
    snapshot = _snapshot(state=state, output="", exit_code=None)
    handoffs: list[tuple[ProcessSnapshot, bool]] = []

    def record_handoff(observed: ProcessSnapshot, *, input_open: bool) -> str:
        handoffs.append((observed, input_open))
        return "RECORDED HANDOFF"

    monkeypatch.setattr(bash_tool_module, "format_process_bash_handoff", record_handoff)
    try:
        result = bash_tool_module._managed_bash_result(
            service,
            owner,
            snapshot,
            None,
            None,
            9,
            tool_call_id="bash-call",
            input_open=input_open,
        )
    finally:
        service.close()

    assert events == []
    assert handoffs == [(snapshot, input_open)]
    assert _result_text(result) == (
        f"Process {_SESSION_ID} is {state.value}; command continues in the background. RECORDED HANDOFF"
    )
    assert "(no output)" not in _result_text(result)
    assert result.details == {
        "status": state.value,
        "sessionId": _SESSION_ID,
        "cursor": 7,
        "nextCursor": 11,
        "outputSize": 99,
        "exitCode": None,
        "tty": True,
        "elapsedMs": 123,
        "suggestedPollDelayMs": 250,
    }


@pytest.mark.parametrize(
    ("durable_output", "has_path", "borrowed"),
    [
        (True, True, True),
        (False, True, False),
        (True, False, False),
    ],
)
def test_truncated_output_without_registry_preserves_borrow_or_export_semantics(
    tmp_path: Path,
    durable_output: bool,
    has_path: bool,
    borrowed: bool,
) -> None:
    owner = ProcessOwner("app", "/workspace", "agent")
    durable_path = tmp_path / "durable.log"
    durable_path.write_text("durable output", encoding="utf-8")
    exported_path = tmp_path / "exported.log"
    events: list[tuple[object, ...]] = []
    service = _RecordedProcessService(
        tmp_path / "processes",
        _tail(content="last three", truncated=True),
        events,
        export_path=exported_path,
    )
    try:
        result = bash_tool_module._managed_bash_result(
            service,
            owner,
            _snapshot(
                durable_output=durable_output,
                full_output_path=str(durable_path) if has_path else None,
            ),
            None,
            None,
            None,
            tool_call_id="bash-call",
            input_open=False,
        )
    finally:
        service.close()

    selected_path = durable_path if borrowed else exported_path
    expected_events: list[tuple[object, ...]] = [("tail", owner, _SESSION_ID)]
    if not borrowed:
        expected_events.append(("export", owner, _SESSION_ID, tempfile.gettempdir()))
    assert events == expected_events
    assert _result_text(result) == (f"last three\n\n[Showing lines 8-10 of 10. Full output: {selected_path}]")
    assert result.details == {
        "status": "exited",
        "sessionId": _SESSION_ID,
        "cursor": 7,
        "nextCursor": 11,
        "outputSize": 99,
        "exitCode": 0,
        "tty": True,
        "elapsedMs": 123,
        "suggestedPollDelayMs": 250,
        **({"durableOutput": True} if durable_output else {}),
        **({"fullOutputPath": str(durable_path)} if has_path else {}),
        "truncation": {
            "content": "last three",
            "truncated": True,
            "truncatedBy": "bytes",
            "totalLines": 10,
            "totalBytes": 999,
            "outputLines": 3,
            "outputBytes": 10,
            "lastLinePartial": True,
            "firstLineExceedsLimit": False,
            "maxLines": 2_000,
            "maxBytes": 51_200,
        },
        "fullOutputPath": str(selected_path),
        "artifactId": None,
        "artifactUnavailable": None,
    }


@pytest.mark.parametrize("borrowed", [False, True])
def test_transient_registry_registers_with_exact_ownership_and_exposes_path(
    tmp_path: Path,
    borrowed: bool,
) -> None:
    owner = ProcessOwner("app", "/workspace", "agent")
    durable_path = tmp_path / "durable.log"
    durable_path.write_text("durable output", encoding="utf-8")
    exported_path = tmp_path / "exported.log"
    events: list[tuple[object, ...]] = []
    service = _RecordedProcessService(
        tmp_path / "processes",
        _tail(content="last three", truncated=True),
        events,
        export_path=exported_path,
    )
    artifacts = _RecordedArtifacts(durable=False, events=events)
    try:
        result = bash_tool_module._managed_bash_result(
            service,
            owner,
            _snapshot(
                durable_output=borrowed,
                full_output_path=str(durable_path) if borrowed else None,
            ),
            None,
            artifacts,
            None,
            tool_call_id="transient-call",
            input_open=False,
        )
    finally:
        service.close()

    selected_path = durable_path if borrowed else exported_path
    expected_events: list[tuple[object, ...]] = [("tail", owner, _SESSION_ID)]
    if not borrowed:
        expected_events.append(("export", owner, _SESSION_ID, tempfile.gettempdir()))
    expected_events.extend(
        [
            ("is_durable", False),
            ("register", selected_path, "bash-output", "read", not borrowed),
            ("is_durable", False),
        ]
    )
    assert events == expected_events
    assert result.details["fullOutputPath"] == str(selected_path)
    assert result.details["artifactId"] == "artifact-transient"
    assert result.details["artifactUnavailable"] is None
    assert _result_text(result) == (
        "last three\n\n[Showing lines 8-10 of 10. Full output artifact: "
        "artifact-transient. Use read with path=artifact-transient, byte_offset=0, "
        "byte_limit=51200.]"
    )
    assert selected_path.exists()


@pytest.mark.parametrize("borrowed", [False, True])
def test_durable_registry_promotes_then_cleans_only_owned_export(
    tmp_path: Path,
    borrowed: bool,
) -> None:
    owner = ProcessOwner("app", "/workspace", "agent")
    durable_path = tmp_path / "durable.log"
    durable_path.write_text("durable output", encoding="utf-8")
    exported_path = tmp_path / "exported.log"
    events: list[tuple[object, ...]] = []
    service = _RecordedProcessService(
        tmp_path / "processes",
        _tail(content="last three", truncated=True),
        events,
        export_path=exported_path,
    )
    artifacts = _RecordedArtifacts(durable=True, events=events)
    try:
        result = bash_tool_module._managed_bash_result(
            service,
            owner,
            _snapshot(
                durable_output=borrowed,
                full_output_path=str(durable_path) if borrowed else None,
            ),
            None,
            artifacts,
            None,
            tool_call_id="durable-call",
            input_open=False,
        )
    finally:
        service.close()

    selected_path = durable_path if borrowed else exported_path
    expected_events: list[tuple[object, ...]] = [("tail", owner, _SESSION_ID)]
    if not borrowed:
        expected_events.append(("export", owner, _SESSION_ID, tempfile.gettempdir()))
    expected_events.extend(
        [
            ("is_durable", True),
            ("promote", selected_path, "bash-output", None, "durable-call", False, True),
            ("is_durable", True),
        ]
    )
    assert events == expected_events
    assert result.details["fullOutputPath"] is None
    assert result.details["artifactId"] == "artifact-durable"
    assert result.details["artifactUnavailable"] is None
    assert _result_text(result) == (
        "last three\n\n[Showing lines 8-10 of 10. Full output artifact: "
        "artifact-durable. Use read with path=artifact-durable, byte_offset=0, "
        "byte_limit=51200.]"
    )
    assert selected_path.exists() is borrowed


@pytest.mark.parametrize("borrowed", [False, True])
@pytest.mark.parametrize(
    ("failure", "expected_unavailable"),
    [
        (
            "artifact",
            {
                "code": "physical_limit",
                "message": "Artifact storage limit reached",
            },
        ),
        (
            "blank_artifact",
            {
                "code": "physical_limit",
                "message": "Artifact storage is unavailable",
            },
        ),
        (
            "long_artifact",
            {
                "code": "physical_limit",
                "message": "x" * 240,
            },
        ),
        (
            "os",
            {
                "code": "unavailable",
                "message": "Artifact storage is unavailable",
            },
        ),
    ],
)
def test_durable_promotion_failure_preserves_error_shape_and_cleanup_ownership(
    tmp_path: Path,
    borrowed: bool,
    failure: _PromotionFailure,
    expected_unavailable: dict[str, str],
) -> None:
    owner = ProcessOwner("app", "/workspace", "agent")
    durable_path = tmp_path / "durable.log"
    durable_path.write_text("durable output", encoding="utf-8")
    exported_path = tmp_path / "exported.log"
    events: list[tuple[object, ...]] = []
    service = _RecordedProcessService(
        tmp_path / "processes",
        _tail(content="last three", truncated=True),
        events,
        export_path=exported_path,
    )
    artifacts = _RecordedArtifacts(durable=True, events=events, failure=failure)
    try:
        result = bash_tool_module._managed_bash_result(
            service,
            owner,
            _snapshot(
                durable_output=borrowed,
                full_output_path=str(durable_path) if borrowed else None,
            ),
            None,
            artifacts,
            None,
            tool_call_id="failed-call",
            input_open=False,
        )
    finally:
        service.close()

    selected_path = durable_path if borrowed else exported_path
    expected_events: list[tuple[object, ...]] = [("tail", owner, _SESSION_ID)]
    if not borrowed:
        expected_events.append(("export", owner, _SESSION_ID, tempfile.gettempdir()))
    expected_events.extend(
        [
            ("is_durable", True),
            ("promote", selected_path, "bash-output", None, "failed-call", False, True),
            ("is_durable", True),
        ]
    )
    assert events == expected_events
    assert result.details["fullOutputPath"] is None
    assert result.details["artifactId"] is None
    assert result.details["artifactUnavailable"] == expected_unavailable
    assert _result_text(result) == (
        f"last three\n\n[Showing lines 8-10 of 10. Full output artifact unavailable ({expected_unavailable['code']})]"
    )
    assert selected_path.exists() is borrowed
