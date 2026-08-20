"""Direct characterization coverage for the read execution owner."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

import pytest

from travis.agent.types import AgentToolResult
from travis.ai.types import ImageContent, TextContent
from travis.coding_agent.artifacts import ARTIFACT_READ_BYTE_LIMIT, ArtifactRegistry
from travis.coding_agent.capabilities import CapabilityViolation, WorkspaceCapability
from travis.coding_agent.resource_refs import ResourceReadResolution
from travis.coding_agent.tools import read as read_tool_module
from travis.coding_agent.tools.read import ReadImageResizeResult, ReadOperations
from travis.coding_agent.tools.types import ToolContext

_ARTIFACT_ID = "artifact-0123456789abcdef0123456789abcdef"


@dataclass
class _Signal:
    aborted: bool = False

    def abort(self) -> None:
        self.aborted = True


@dataclass(frozen=True)
class _Model:
    input: list[Literal["text", "image"]]


class _RecordedWorkspace(WorkspaceCapability):
    events: list[tuple[object, ...]]
    failure_call: int | None
    signal: _Signal | None
    abort_call: int | None
    resolve_calls: int

    def __init__(
        self,
        root: Path,
        events: list[tuple[object, ...]],
        *,
        failure_call: int | None = None,
        signal: _Signal | None = None,
        abort_call: int | None = None,
    ) -> None:
        super().__init__(root)
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "failure_call", failure_call)
        object.__setattr__(self, "signal", signal)
        object.__setattr__(self, "abort_call", abort_call)
        object.__setattr__(self, "resolve_calls", 0)

    def resolve(self, path: str, access: Literal["read", "write", "execute"]) -> Path:
        call = self.resolve_calls + 1
        object.__setattr__(self, "resolve_calls", call)
        self.events.append(("workspace.resolve", path, access))
        if self.signal is not None and self.abort_call == call:
            self.signal.abort()
        if self.failure_call == call:
            error = CapabilityViolation("outside_workspace", path, self.root / "outside.txt")
            self.events.append(("workspace.error", error))
            raise error
        return super().resolve(path, access)


class _RecordedArtifacts(ArtifactRegistry):
    def __init__(
        self,
        events: list[tuple[object, ...]],
        *,
        readable: bool,
        resource: ResourceReadResolution | None = None,
        resolved_path: Path | None = None,
        signal: _Signal | None = None,
        abort_during: Literal["resource", "path"] | None = None,
    ) -> None:
        super().__init__()
        self.events = events
        self.readable = readable
        self.resource = resource
        self.resolved_path = resolved_path
        self.signal = signal
        self.abort_during = abort_during

    def is_readable_reference(self, identifier: str) -> bool:
        self.events.append(("artifact.is_readable", identifier))
        return self.readable

    def resolve_resource_read(
        self,
        identifier: str,
        *,
        byte_offset: int,
        byte_limit: int,
    ) -> ResourceReadResolution | None:
        self.events.append(("artifact.resolve_resource", identifier, byte_offset, byte_limit))
        if self.signal is not None and self.abort_during == "resource":
            self.signal.abort()
        return self.resource

    def resolve_read(self, path_or_id: str) -> Path | None:
        self.events.append(("artifact.resolve_path", path_or_id))
        if self.signal is not None and self.abort_during == "path":
            self.signal.abort()
        return self.resolved_path


_OperationStep = Literal["access", "detect", "read"]


def _operations(
    events: list[tuple[object, ...]],
    *,
    data: bytes = b"alpha\nbeta\n",
    mime_type: str | None = None,
    signal: _Signal | None = None,
    abort_during: _OperationStep | None = None,
) -> ReadOperations:
    def access(path: str) -> None:
        events.append(("access", path))
        if signal is not None and abort_during == "access":
            signal.abort()

    def detect(path: str) -> str | None:
        events.append(("detect", path))
        if signal is not None and abort_during == "detect":
            signal.abort()
        return mime_type

    def read_file(path: str) -> bytes:
        events.append(("read", path))
        if signal is not None and abort_during == "read":
            signal.abort()
        return data

    return ReadOperations(
        read_file=read_file,
        access=access,
        detect_image_mime_type=detect,
    )


def _default_resizer(data: bytes, mime_type: str) -> ReadImageResizeResult:
    return ReadImageResizeResult(
        data=base64.b64encode(data).decode("ascii"),
        mime_type=mime_type,
    )


def _run(
    tmp_path: Path,
    args: dict[str, object],
    *,
    events: list[tuple[object, ...]] | None = None,
    workspace: WorkspaceCapability | None = None,
    artifacts: ArtifactRegistry | None = None,
    operations: ReadOperations | None = None,
    auto_resize_images: bool = False,
    image_resizer: Callable[[bytes, str], ReadImageResizeResult | None] = _default_resizer,
    signal: _Signal | None = None,
    on_update: Callable[[AgentToolResult], None] | None = None,
    ctx: ToolContext | None = None,
) -> AgentToolResult:
    recorded = events if events is not None else []
    return read_tool_module._execute_read(
        str(tmp_path),
        workspace or _RecordedWorkspace(tmp_path, recorded),
        artifacts,
        operations or _operations(recorded),
        auto_resize_images,
        image_resizer,
        "read-call-id",
        args,
        signal,
        on_update,
        ctx,
    )


def _text(result: AgentToolResult) -> str:
    content = result.content[0]
    assert isinstance(content, TextContent)
    return content.text


def test_initial_abort_precedes_argument_and_dependency_access(tmp_path: Path) -> None:
    events: list[tuple[object, ...]] = []
    signal = _Signal(aborted=True)
    artifacts = _RecordedArtifacts(events, readable=True)

    with pytest.raises(RuntimeError) as raised:
        _run(tmp_path, {}, events=events, artifacts=artifacts, signal=signal)

    assert type(raised.value) is RuntimeError
    assert str(raised.value) == "Operation aborted"
    assert events == []


def test_mixed_pagination_rejection_precedes_artifact_resolution(tmp_path: Path) -> None:
    events: list[tuple[object, ...]] = []
    artifacts = _RecordedArtifacts(events, readable=True)

    with pytest.raises(ValueError) as raised:
        _run(
            tmp_path,
            {
                "path": _ARTIFACT_ID,
                "offset": 1,
                "limit": 2,
                "byte_offset": 0,
                "byte_limit": 4,
            },
            events=events,
            artifacts=artifacts,
        )

    assert str(raised.value) == (
        "Cannot combine line pagination (offset/limit) with byte pagination (byte_offset/byte_limit)"
    )
    assert events == []


def test_artifact_line_mode_rejection_has_exact_retry_and_order(tmp_path: Path) -> None:
    events: list[tuple[object, ...]] = []
    artifacts = _RecordedArtifacts(events, readable=True)

    with pytest.raises(ValueError) as raised:
        _run(
            tmp_path,
            {"path": _ARTIFACT_ID, "offset": 1, "limit": 2},
            events=events,
            artifacts=artifacts,
        )

    assert str(raised.value) == (
        f"Virtual artifacts require byte pagination. Retry read with path={_ARTIFACT_ID}, "
        "byte_offset=0, byte_limit=51200; do not use offset/limit."
    )
    assert events == [("artifact.is_readable", _ARTIFACT_ID)]


@pytest.mark.parametrize(
    ("args", "expected_offset", "expected_limit"),
    [
        ({"path": "plain.txt", "byte_offset": None}, 0, ARTIFACT_READ_BYTE_LIMIT),
        ({"path": "plain.txt", "byte_limit": None}, 0, ARTIFACT_READ_BYTE_LIMIT),
        ({"path": "plain.txt", "byte_offset": True, "byte_limit": False}, 0, ARTIFACT_READ_BYTE_LIMIT),
        ({"path": "plain.txt", "byte_offset": 2.9, "byte_limit": 3.8}, 2, 3),
    ],
)
def test_byte_mode_defaults_and_number_conversion(
    tmp_path: Path,
    args: dict[str, object],
    expected_offset: int,
    expected_limit: int,
) -> None:
    events: list[tuple[object, ...]] = []
    operations = _operations(events, data=b"abcdef")

    result = _run(tmp_path, args, events=events, operations=operations)

    end = min(6, expected_offset + expected_limit)
    assert result.details == {
        "byteRange": {
            "start": expected_offset,
            "endExclusive": end,
            "totalBytes": 6,
        }
    }
    assert events == [
        ("workspace.resolve", "plain.txt", "read"),
        ("workspace.resolve", str(tmp_path / "plain.txt"), "read"),
        ("access", str(tmp_path / "plain.txt")),
        ("detect", str(tmp_path / "plain.txt")),
        ("read", str(tmp_path / "plain.txt")),
    ]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"path": "plain.txt", "byte_offset": -1}, "byte_offset must be non-negative"),
        ({"path": "plain.txt", "byte_limit": 0}, "byte_limit must be between 1 and 51200"),
        ({"path": "plain.txt", "byte_limit": -1}, "byte_limit must be between 1 and 51200"),
        ({"path": "plain.txt", "byte_limit": 51201}, "byte_limit must be between 1 and 51200"),
    ],
)
def test_byte_bounds_fail_before_resource_or_workspace_resolution(
    tmp_path: Path,
    arguments: dict[str, object],
    message: str,
) -> None:
    events: list[tuple[object, ...]] = []
    artifacts = _RecordedArtifacts(events, readable=False)

    with pytest.raises(ValueError) as raised:
        _run(tmp_path, arguments, events=events, artifacts=artifacts)

    assert str(raised.value) == message
    assert events == [("artifact.is_readable", "plain.txt")]


def test_artifact_defaults_to_durable_byte_page_and_preserves_utf8_boundary(
    tmp_path: Path,
) -> None:
    events: list[tuple[object, ...]] = []
    resolution = ResourceReadResolution(
        available=True,
        artifact_id=_ARTIFACT_ID,
        content=b"A\xe2\x82",
        next_offset=1,
        total_bytes=5,
    )
    artifacts = _RecordedArtifacts(events, readable=True, resource=resolution)
    updates: list[AgentToolResult] = []

    result = _run(
        tmp_path,
        {"path": _ARTIFACT_ID},
        events=events,
        artifacts=artifacts,
        on_update=updates.append,
    )

    assert events == [
        ("artifact.is_readable", _ARTIFACT_ID),
        ("artifact.resolve_resource", _ARTIFACT_ID, 0, ARTIFACT_READ_BYTE_LIMIT),
    ]
    assert updates == []
    assert _text(result) == "A\n\n[Showing bytes 0-0 of 5. Use byte_offset=1 to continue.]"
    assert result.details == {"byteRange": {"start": 0, "endExclusive": 1, "totalBytes": 5}}


@pytest.mark.parametrize("error_code", [None, "integrity_error"])
def test_unavailable_durable_artifact_uses_exact_error_fallback(
    tmp_path: Path,
    error_code: str | None,
) -> None:
    events: list[tuple[object, ...]] = []
    artifacts = _RecordedArtifacts(
        events,
        readable=True,
        resource=ResourceReadResolution(
            available=False,
            artifact_id=_ARTIFACT_ID,
            error_code=error_code,
        ),
    )

    with pytest.raises(ValueError) as raised:
        _run(
            tmp_path,
            {"path": _ARTIFACT_ID, "byte_offset": 7, "byte_limit": 9},
            events=events,
            artifacts=artifacts,
        )

    assert str(raised.value) == f"Artifact {_ARTIFACT_ID} is unavailable ({error_code or 'unavailable'})"
    assert events == [
        ("artifact.is_readable", _ARTIFACT_ID),
        ("artifact.resolve_resource", _ARTIFACT_ID, 7, 9),
    ]


def test_durable_resource_return_does_not_recheck_abort_after_resolution(tmp_path: Path) -> None:
    events: list[tuple[object, ...]] = []
    signal = _Signal()
    artifacts = _RecordedArtifacts(
        events,
        readable=True,
        resource=ResourceReadResolution(
            available=True,
            artifact_id=_ARTIFACT_ID,
            content=b"ok",
            total_bytes=2,
        ),
        signal=signal,
        abort_during="resource",
    )

    result = _run(
        tmp_path,
        {"path": _ARTIFACT_ID},
        events=events,
        artifacts=artifacts,
        signal=signal,
    )

    assert signal.aborted is True
    assert _text(result) == "ok\n\n[Showing bytes 0-1 of 2. End of file.]"
    assert events == [
        ("artifact.is_readable", _ARTIFACT_ID),
        ("artifact.resolve_resource", _ARTIFACT_ID, 0, ARTIFACT_READ_BYTE_LIMIT),
    ]


def test_transient_artifact_resolution_bypasses_workspace_and_preserves_operation_order(
    tmp_path: Path,
) -> None:
    events: list[tuple[object, ...]] = []
    resolved = tmp_path / "artifact.log"
    artifacts = _RecordedArtifacts(
        events,
        readable=True,
        resource=None,
        resolved_path=resolved,
    )
    operations = _operations(events, data=b"payload")

    result = _run(
        tmp_path,
        {"path": _ARTIFACT_ID, "byte_offset": 0, "byte_limit": 20},
        events=events,
        artifacts=artifacts,
        operations=operations,
    )

    assert events == [
        ("artifact.is_readable", _ARTIFACT_ID),
        ("artifact.resolve_resource", _ARTIFACT_ID, 0, 20),
        ("artifact.resolve_path", _ARTIFACT_ID),
        ("access", str(resolved)),
        ("detect", str(resolved)),
        ("read", str(resolved)),
    ]
    assert _text(result) == "payload\n\n[Showing bytes 0-6 of 7. End of file.]"


def test_workspace_resolution_calls_resolve_read_path_between_capability_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[object, ...]] = []
    workspace = _RecordedWorkspace(tmp_path, events)
    operations = _operations(events, data=b"plain")
    original = read_tool_module.resolve_read_path

    def record_resolve_read_path(path: str, cwd: str) -> str:
        events.append(("resolve_read_path", path, cwd))
        return original(path, cwd)

    monkeypatch.setattr(read_tool_module, "resolve_read_path", record_resolve_read_path)

    result = _run(
        tmp_path,
        {"path": "notes.txt"},
        events=events,
        workspace=workspace,
        operations=operations,
    )

    absolute = str(tmp_path / "notes.txt")
    assert events == [
        ("workspace.resolve", "notes.txt", "read"),
        ("resolve_read_path", absolute, str(tmp_path)),
        ("workspace.resolve", absolute, "read"),
        ("access", absolute),
        ("detect", absolute),
        ("read", absolute),
    ]
    assert _text(result) == "plain"
    assert result.details is None


def test_missing_image_detector_preserves_access_then_read_order(tmp_path: Path) -> None:
    events: list[tuple[object, ...]] = []

    def access(path: str) -> None:
        events.append(("access", path))

    def read_file(path: str) -> bytes:
        events.append(("read", path))
        return b"plain"

    result = _run(
        tmp_path,
        {"path": "plain.txt"},
        events=events,
        operations=ReadOperations(
            read_file=read_file,
            access=access,
            detect_image_mime_type=None,
        ),
    )

    absolute = str(tmp_path / "plain.txt")
    assert events == [
        ("workspace.resolve", "plain.txt", "read"),
        ("workspace.resolve", absolute, "read"),
        ("access", absolute),
        ("read", absolute),
    ]
    assert _text(result) == "plain"


@pytest.mark.parametrize("failure_call", [1, 2])
def test_capability_violation_is_propagated_at_exact_resolution_call(
    tmp_path: Path,
    failure_call: int,
) -> None:
    events: list[tuple[object, ...]] = []
    workspace = _RecordedWorkspace(tmp_path, events, failure_call=failure_call)

    with pytest.raises(CapabilityViolation) as raised:
        _run(tmp_path, {"path": "outside.txt"}, events=events, workspace=workspace)

    expected_prefix = [("workspace.resolve", "outside.txt", "read")]
    if failure_call == 2:
        expected_prefix.append(("workspace.resolve", str(tmp_path / "outside.txt"), "read"))
    assert events[:-1] == expected_prefix
    assert events[-1] == ("workspace.error", raised.value)
    assert str(raised.value) == (f"outside_workspace: {events[-2][1]} resolves to {tmp_path / 'outside.txt'}")


@pytest.mark.parametrize(
    ("abort_stage", "expected_suffix"),
    [
        ("workspace", [("workspace.resolve", "plain.txt", "read"), ("workspace.resolve", "ABS", "read")]),
        (
            "access",
            [("workspace.resolve", "plain.txt", "read"), ("workspace.resolve", "ABS", "read"), ("access", "ABS")],
        ),
        (
            "detect",
            [
                ("workspace.resolve", "plain.txt", "read"),
                ("workspace.resolve", "ABS", "read"),
                ("access", "ABS"),
                ("detect", "ABS"),
            ],
        ),
        (
            "read",
            [
                ("workspace.resolve", "plain.txt", "read"),
                ("workspace.resolve", "ABS", "read"),
                ("access", "ABS"),
                ("detect", "ABS"),
                ("read", "ABS"),
            ],
        ),
    ],
)
def test_abort_checkpoints_preserve_dependency_cutoff(
    tmp_path: Path,
    abort_stage: Literal["workspace", "access", "detect", "read"],
    expected_suffix: list[tuple[object, ...]],
) -> None:
    events: list[tuple[object, ...]] = []
    signal = _Signal()
    workspace = _RecordedWorkspace(
        tmp_path,
        events,
        signal=signal,
        abort_call=1 if abort_stage == "workspace" else None,
    )
    operation_abort: _OperationStep | None = None
    if abort_stage == "access":
        operation_abort = "access"
    elif abort_stage == "detect":
        operation_abort = "detect"
    elif abort_stage == "read":
        operation_abort = "read"
    operations = _operations(
        events,
        signal=signal,
        abort_during=operation_abort,
    )

    with pytest.raises(RuntimeError) as raised:
        _run(
            tmp_path,
            {"path": "plain.txt"},
            events=events,
            workspace=workspace,
            operations=operations,
            signal=signal,
        )

    assert str(raised.value) == "Operation aborted"
    absolute = str(tmp_path / "plain.txt")
    assert events == [tuple(absolute if item == "ABS" else item for item in event) for event in expected_suffix]


def test_transient_artifact_abort_after_path_resolution_precedes_access(tmp_path: Path) -> None:
    events: list[tuple[object, ...]] = []
    signal = _Signal()
    artifacts = _RecordedArtifacts(
        events,
        readable=True,
        resolved_path=tmp_path / "artifact.txt",
        signal=signal,
        abort_during="path",
    )

    with pytest.raises(RuntimeError, match="^Operation aborted$"):
        _run(
            tmp_path,
            {"path": _ARTIFACT_ID},
            events=events,
            artifacts=artifacts,
            signal=signal,
        )

    assert events == [
        ("artifact.is_readable", _ARTIFACT_ID),
        ("artifact.resolve_resource", _ARTIFACT_ID, 0, ARTIFACT_READ_BYTE_LIMIT),
        ("artifact.resolve_path", _ARTIFACT_ID),
    ]


def test_nonresized_image_uses_exact_base64_and_nonvision_note(tmp_path: Path) -> None:
    events: list[tuple[object, ...]] = []
    data = b"raw-image"
    operations = _operations(events, data=data, mime_type="image/png")
    context = ToolContext(cwd=str(tmp_path), model=_Model(input=["text"]))

    result = _run(
        tmp_path,
        {"path": "pixel.png"},
        events=events,
        operations=operations,
        auto_resize_images=False,
        ctx=context,
    )

    absolute = str(tmp_path / "pixel.png")
    assert events == [
        ("workspace.resolve", "pixel.png", "read"),
        ("workspace.resolve", absolute, "read"),
        ("access", absolute),
        ("detect", absolute),
        ("read", absolute),
    ]
    assert result.details is None
    assert _text(result) == (
        "Read image file [image/png]\n"
        "[Current model does not support images. The image will be omitted from this request.]"
    )
    image = result.content[1]
    assert isinstance(image, ImageContent)
    assert image.data == base64.b64encode(data).decode("ascii")
    assert image.mime_type == "image/png"


@pytest.mark.parametrize(
    ("resize_result", "expected_lines"),
    [
        (
            ReadImageResizeResult(
                data="resized",
                mime_type="image/webp",
                was_resized=True,
                original_width=4000,
                original_height=2000,
                width=2000,
                height=1000,
            ),
            [
                "Read image file [image/webp]",
                "[Image: original 4000x2000, displayed at 2000x1000. Multiply coordinates by 2.00 to map to original image.]",
            ],
        ),
        (
            ReadImageResizeResult(
                data="resized",
                mime_type="image/webp",
                was_resized=True,
                original_width=4000,
                original_height=None,
                width=2000,
                height=1000,
            ),
            ["Read image file [image/webp]"],
        ),
        (
            ReadImageResizeResult(data="resized", mime_type="image/webp", was_resized=False),
            ["Read image file [image/webp]"],
        ),
    ],
)
def test_resized_image_success_preserves_dimension_note_rules(
    tmp_path: Path,
    resize_result: ReadImageResizeResult,
    expected_lines: list[str],
) -> None:
    events: list[tuple[object, ...]] = []
    operations = _operations(events, data=b"raw", mime_type="image/png")

    def resize(data: bytes, mime_type: str) -> ReadImageResizeResult:
        events.append(("resize", data, mime_type))
        return resize_result

    result = _run(
        tmp_path,
        {"path": "pixel.png"},
        events=events,
        operations=operations,
        auto_resize_images=True,
        image_resizer=resize,
        ctx=ToolContext(cwd=str(tmp_path), model=_Model(input=["text", "image"])),
    )

    assert events[-2:] == [("read", str(tmp_path / "pixel.png")), ("resize", b"raw", "image/png")]
    assert _text(result).splitlines() == expected_lines
    image = result.content[1]
    assert isinstance(image, ImageContent)
    assert image.data == "resized"
    assert image.mime_type == "image/webp"
    assert result.details is None


def test_resize_failure_returns_exact_omission_and_nonvision_note(tmp_path: Path) -> None:
    events: list[tuple[object, ...]] = []
    operations = _operations(events, data=b"raw", mime_type="image/png")

    def fail_resize(data: bytes, mime_type: str) -> None:
        events.append(("resize", data, mime_type))
        return None

    result = _run(
        tmp_path,
        {"path": "pixel.png"},
        events=events,
        operations=operations,
        auto_resize_images=True,
        image_resizer=fail_resize,
        ctx=ToolContext(cwd=str(tmp_path), model=_Model(input=["text"])),
    )

    assert _text(result) == (
        "Read image file [image/png]\n"
        "[Image omitted: could not be resized below the inline image size limit.]\n"
        "[Current model does not support images. The image will be omitted from this request.]"
    )
    assert len(result.content) == 1
    assert result.details is None
    assert events[-1] == ("resize", b"raw", "image/png")


def test_abort_after_resize_precedes_image_result(tmp_path: Path) -> None:
    events: list[tuple[object, ...]] = []
    signal = _Signal()
    operations = _operations(events, data=b"raw", mime_type="image/png")

    def resize(data: bytes, mime_type: str) -> ReadImageResizeResult:
        events.append(("resize", data, mime_type))
        signal.abort()
        return ReadImageResizeResult(data="ignored", mime_type="image/png")

    with pytest.raises(RuntimeError, match="^Operation aborted$"):
        _run(
            tmp_path,
            {"path": "pixel.png"},
            events=events,
            operations=operations,
            auto_resize_images=True,
            image_resizer=resize,
            signal=signal,
        )

    assert events[-1] == ("resize", b"raw", "image/png")


@pytest.mark.parametrize(
    ("byte_offset", "byte_limit", "expected_text", "expected_range"),
    [
        (
            0,
            2,
            "ab\n\n[Showing bytes 0-1 of 6. Use byte_offset=2 to continue.]",
            {"start": 0, "endExclusive": 2, "totalBytes": 6},
        ),
        (
            2,
            4,
            "cdef\n\n[Showing bytes 2-5 of 6. End of file.]",
            {"start": 2, "endExclusive": 6, "totalBytes": 6},
        ),
        (
            6,
            1,
            "\n\n[Showing bytes 6-5 of 6. End of file.]",
            {"start": 6, "endExclusive": 6, "totalBytes": 6},
        ),
    ],
)
def test_ordinary_byte_pages_preserve_exact_content_and_ranges(
    tmp_path: Path,
    byte_offset: int,
    byte_limit: int,
    expected_text: str,
    expected_range: dict[str, int],
) -> None:
    result = _run(
        tmp_path,
        {"path": "plain.txt", "byte_offset": byte_offset, "byte_limit": byte_limit},
        operations=_operations([], data=b"abcdef"),
    )

    assert _text(result) == expected_text
    assert result.details == {"byteRange": expected_range}


def test_empty_byte_page_preserves_negative_end_display(tmp_path: Path) -> None:
    result = _run(
        tmp_path,
        {"path": "empty.txt", "byte_offset": 0, "byte_limit": 1},
        operations=_operations([], data=b""),
    )

    assert _text(result) == "\n\n[Showing bytes 0--1 of 0. End of file.]"
    assert result.details == {"byteRange": {"start": 0, "endExclusive": 0, "totalBytes": 0}}


def test_byte_offset_beyond_end_raises_after_read_with_exact_message(tmp_path: Path) -> None:
    events: list[tuple[object, ...]] = []

    with pytest.raises(ValueError) as raised:
        _run(
            tmp_path,
            {"path": "plain.txt", "byte_offset": 7, "byte_limit": 1},
            events=events,
            operations=_operations(events, data=b"abcdef"),
        )

    assert str(raised.value) == "byte_offset 7 is beyond end of file (6 bytes total)"
    assert events[-1] == ("read", str(tmp_path / "plain.txt"))


def test_line_offset_out_of_range_raises_after_read_with_exact_message(tmp_path: Path) -> None:
    events: list[tuple[object, ...]] = []

    with pytest.raises(ValueError) as raised:
        _run(
            tmp_path,
            {"path": "plain.txt", "offset": 4},
            events=events,
            operations=_operations(events, data=b"one\ntwo"),
        )

    assert str(raised.value) == "Offset 4 is beyond end of file (2 lines total)"
    assert events[-1] == ("read", str(tmp_path / "plain.txt"))


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"path": "plain.txt", "offset": 2, "limit": 1}, "two\n\n[2 more lines in file. Use offset=3 to continue.]"),
        ({"path": "plain.txt", "offset": -3, "limit": 1}, "one\n\n[3 more lines in file. Use offset=2 to continue.]"),
        ({"path": "plain.txt", "limit": 0}, "\n\n[4 more lines in file. Use offset=1 to continue.]"),
    ],
)
def test_line_offsets_limits_and_remaining_hint_preserve_exact_output(
    tmp_path: Path,
    arguments: dict[str, object],
    expected: str,
) -> None:
    result = _run(
        tmp_path,
        arguments,
        operations=_operations([], data=b"one\ntwo\nthree\nfour"),
    )

    assert _text(result) == expected
    assert result.details is None


def test_first_line_too_large_preserves_exact_message_and_details(tmp_path: Path) -> None:
    data = b"x" * 51_201

    result = _run(
        tmp_path,
        {"path": "huge.txt"},
        operations=_operations([], data=data),
    )

    assert _text(result) == ("[Line 1 is 50.0KB, exceeds 50.0KB limit. Use bash: sed -n '1p' huge.txt | head -c 51200]")
    assert result.details == {
        "truncation": {
            "content": "",
            "truncated": True,
            "truncatedBy": "bytes",
            "totalLines": 1,
            "totalBytes": 51_201,
            "outputLines": 0,
            "outputBytes": 0,
            "lastLinePartial": False,
            "firstLineExceedsLimit": True,
            "maxLines": 2_000,
            "maxBytes": 51_200,
        }
    }


def test_line_count_truncation_preserves_notice_and_details(tmp_path: Path) -> None:
    lines = [f"line-{index}" for index in range(1, 2_002)]
    data = "\n".join(lines).encode("utf-8")

    result = _run(
        tmp_path,
        {"path": "many.txt"},
        operations=_operations([], data=data),
    )

    assert _text(result) == (
        "\n".join(lines[:2_000]) + "\n\n[Showing lines 1-2000 of 2001. Use offset=2001 to continue.]"
    )
    assert result.details == {
        "truncation": {
            "content": "\n".join(lines[:2_000]),
            "truncated": True,
            "truncatedBy": "lines",
            "totalLines": 2_001,
            "totalBytes": len(data),
            "outputLines": 2_000,
            "outputBytes": len("\n".join(lines[:2_000]).encode("utf-8")),
            "lastLinePartial": False,
            "firstLineExceedsLimit": False,
            "maxLines": 2_000,
            "maxBytes": 51_200,
        }
    }


def test_byte_truncated_lines_preserve_size_notice_and_details(tmp_path: Path) -> None:
    first = "a" * 30_000
    second = "b" * 30_000
    data = f"{first}\n{second}".encode("utf-8")

    result = _run(
        tmp_path,
        {"path": "wide.txt"},
        operations=_operations([], data=data),
    )

    assert _text(result) == (first + "\n\n[Showing lines 1-1 of 2 (50.0KB limit). Use offset=2 to continue.]")
    assert result.details == {
        "truncation": {
            "content": first,
            "truncated": True,
            "truncatedBy": "bytes",
            "totalLines": 2,
            "totalBytes": 60_001,
            "outputLines": 1,
            "outputBytes": 30_000,
            "lastLinePartial": False,
            "firstLineExceedsLimit": False,
            "maxLines": 2_000,
            "maxBytes": 51_200,
        }
    }


def test_exact_unpaginated_text_result_has_no_details_or_updates(tmp_path: Path) -> None:
    updates: list[AgentToolResult] = []
    result = _run(
        tmp_path,
        {"path": "plain.txt"},
        operations=_operations([], data=b"alpha\nbeta\n"),
        on_update=updates.append,
    )

    assert _text(result) == "alpha\nbeta\n"
    assert result.details is None
    assert updates == []


def test_final_abort_checkpoint_runs_after_line_truncation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signal = _Signal()
    original = read_tool_module.truncate_head

    def abort_during_truncation(content: str):
        result = original(content)
        signal.abort()
        return result

    monkeypatch.setattr(read_tool_module, "truncate_head", abort_during_truncation)

    with pytest.raises(RuntimeError, match="^Operation aborted$"):
        _run(
            tmp_path,
            {"path": "plain.txt"},
            operations=_operations([], data=b"plain"),
            signal=signal,
        )
