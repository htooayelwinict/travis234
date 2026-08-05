from __future__ import annotations

import hashlib
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from travis.ai.validation import compile_tool_schema
from travis.coding_agent.tools.tmux import (
    TMUX_SCHEMA,
    TmuxOperations,
    create_tmux_tool_definition,
)


class TmuxRecorder:
    def __init__(
        self,
        responses: list[tuple[int, str, str]] | None = None,
        *,
        executable: str | None = "/usr/bin/tmux",
    ) -> None:
        self.responses = list(responses or [])
        self.executable = executable
        self.calls: list[list[str]] = []

    def which(self, name: str) -> str | None:
        assert name == "tmux"
        return self.executable

    def run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(argv))
        if not self.responses:
            raise AssertionError(f"unexpected tmux command: {argv}")
        returncode, stdout, stderr = self.responses.pop(0)
        return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def recorded_tmux_definition(
    tmp_path: Path,
    responses: list[tuple[int, str, str]] | None = None,
    *,
    executable: str | None = "/usr/bin/tmux",
):
    recorder = TmuxRecorder(responses, executable=executable)
    definition = create_tmux_tool_definition(
        str(tmp_path),
        operations=TmuxOperations(which=recorder.which, run=recorder.run),
    )
    return definition, recorder


def result_text(result) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


def test_tmux_schema_accepts_supported_action_shapes() -> None:
    schema = compile_tool_schema(TMUX_SCHEMA)

    assert not schema.errors({"action": "start", "name": "listener", "command": "nc -lvnp 4444"})
    assert not schema.errors({"action": "send", "name": "listener", "input": "whoami", "enter": True})
    assert not schema.errors({"action": "capture", "name": "listener", "lines": 200})
    assert not schema.errors({"action": "list"})
    assert not schema.errors({"action": "stop", "name": "listener"})


def test_tmux_definition_describes_coding_workloads(tmp_path: Path) -> None:
    definition, _ = recorded_tmux_definition(tmp_path)

    assert "development servers" in definition.description
    assert "test loops" in definition.description
    assert "reverse connections" not in definition.description


def test_tmux_executes_all_actions_with_direct_argument_vectors(tmp_path: Path) -> None:
    digest = hashlib.sha256(str(tmp_path.resolve()).encode("utf-8")).hexdigest()[:12]
    session_name = f"travis234-{digest}-callback"
    definition, recorder = recorded_tmux_definition(
        tmp_path,
        [
            (1, "", "can't find session"),
            (0, "", ""),
            (0, "", ""),
            (0, "", ""),
            (0, "", ""),
            (0, "", ""),
            (0, "", ""),
            (0, "", ""),
            (0, "TMUX-OK\n", ""),
            (0, f"{session_name}\nforeign-session\n", ""),
            (0, "", ""),
            (0, "", ""),
        ],
    )

    started = definition.execute(
        "start",
        {"action": "start", "name": "callback", "command": "nc -lvnp 4444"},
    )
    sent = definition.execute(
        "send",
        {"action": "send", "name": "callback", "input": "whoami", "enter": True},
    )
    captured = definition.execute(
        "capture", {"action": "capture", "name": "callback", "lines": 200}
    )
    listed = definition.execute("list", {"action": "list"})
    stopped = definition.execute("stop", {"action": "stop", "name": "callback"})

    assert recorder.calls == [
        ["/usr/bin/tmux", "has-session", "-t", session_name],
        [
            "/usr/bin/tmux",
            "new-session",
            "-d",
            "-s",
            session_name,
            "-c",
            str(tmp_path.resolve()),
        ],
        [
            "/usr/bin/tmux",
            "set-option",
            "-w",
            "-t",
            session_name,
            "remain-on-exit",
            "on",
        ],
        [
            "/usr/bin/tmux",
            "respawn-pane",
            "-k",
            "-t",
            session_name,
            "-c",
            str(tmp_path.resolve()),
            "--",
            "nc -lvnp 4444",
        ],
        ["/usr/bin/tmux", "has-session", "-t", session_name],
        ["/usr/bin/tmux", "send-keys", "-t", session_name, "-l", "--", "whoami"],
        ["/usr/bin/tmux", "send-keys", "-t", session_name, "Enter"],
        ["/usr/bin/tmux", "has-session", "-t", session_name],
        ["/usr/bin/tmux", "capture-pane", "-p", "-t", session_name, "-S", "-200"],
        ["/usr/bin/tmux", "list-sessions", "-F", "#{session_name}"],
        ["/usr/bin/tmux", "has-session", "-t", session_name],
        ["/usr/bin/tmux", "kill-session", "-t", session_name],
    ]
    assert started.details == {
        "action": "start",
        "name": "callback",
        "sessionName": session_name,
        "cwd": str(tmp_path.resolve()),
    }
    assert sent.details["sessionName"] == session_name
    assert captured.details["lines"] == 200
    assert result_text(captured) == "TMUX-OK\n"
    assert listed.details == {"action": "list", "sessions": [session_name]}
    assert stopped.details["sessionName"] == session_name


def test_tmux_accepts_its_returned_resolved_name_for_followup_actions(
    tmp_path: Path,
) -> None:
    digest = hashlib.sha256(str(tmp_path.resolve()).encode("utf-8")).hexdigest()[:12]
    resolved = f"travis234-{digest}-callback"
    definition, recorder = recorded_tmux_definition(
        tmp_path,
        [
            (1, "", "can't find session"),
            (0, "", ""),
            (0, "", ""),
            (0, "", ""),
            (0, "", ""),
            (0, "TMUX-RESOLVED-NAME-OK\n", ""),
            (0, "", ""),
            (0, "", ""),
        ],
    )

    started = definition.execute(
        "start",
        {"action": "start", "name": "callback", "command": "sleep 10"},
    )
    captured = definition.execute(
        "capture",
        {"action": "capture", "name": started.details["sessionName"]},
    )
    stopped = definition.execute(
        "stop",
        {"action": "stop", "name": started.details["sessionName"]},
    )

    assert "Logical name: callback." in result_text(started)
    assert f"Resolved native name: {resolved}." in result_text(started)
    assert result_text(captured) == "TMUX-RESOLVED-NAME-OK\n"
    assert captured.details["name"] == "callback"
    assert captured.details["sessionName"] == resolved
    assert stopped.details["name"] == "callback"
    assert stopped.details["sessionName"] == resolved
    targets = [call[call.index("-t") + 1] for call in recorder.calls if "-t" in call]
    assert targets == [resolved] * 7


def test_tmux_rejects_a_resolved_name_from_another_workspace(tmp_path: Path) -> None:
    definition, recorder = recorded_tmux_definition(tmp_path)

    with pytest.raises(ValueError, match="belongs to another workspace"):
        definition.execute(
            "capture",
            {
                "action": "capture",
                "name": "travis234-deadbeefcafe-callback",
            },
        )

    assert recorder.calls == []


def test_tmux_accepts_a_returned_resolved_name_for_the_longest_logical_name(
    tmp_path: Path,
) -> None:
    logical_name = "x" * 48
    digest = hashlib.sha256(str(tmp_path.resolve()).encode("utf-8")).hexdigest()[:12]
    resolved = f"travis234-{digest}-{logical_name}"
    definition, _ = recorded_tmux_definition(
        tmp_path,
        [
            (1, "", "can't find session"),
            (0, "", ""),
            (0, "", ""),
            (0, "", ""),
            (0, "", ""),
            (0, "", ""),
        ],
    )

    started = definition.execute(
        "start",
        {"action": "start", "name": logical_name, "command": "sleep 10"},
    )
    stopped = definition.execute(
        "stop",
        {"action": "stop", "name": started.details["sessionName"]},
    )

    assert started.details["sessionName"] == resolved
    assert stopped.details["sessionName"] == resolved


@pytest.mark.parametrize("name", ["", "two words", "bad:name", "../escape", "x" * 49])
def test_tmux_rejects_invalid_names(name, tmp_path) -> None:
    definition, recorder = recorded_tmux_definition(tmp_path)
    with pytest.raises(ValueError, match="tmux name must match"):
        definition.execute("call-1", {"action": "capture", "name": name})
    assert recorder.calls == []


@pytest.mark.parametrize("lines", [True, 0, -1, 2001, "200"])
def test_tmux_rejects_invalid_capture_lines(lines, tmp_path) -> None:
    definition, recorder = recorded_tmux_definition(tmp_path)
    with pytest.raises(ValueError, match="lines must be an integer from 1 to 2000"):
        definition.execute(
            "call-1", {"action": "capture", "name": "callback", "lines": lines}
        )
    assert recorder.calls == []


@pytest.mark.parametrize(
    "arguments, message",
    [
        ({"action": "unknown"}, "unknown tmux action"),
        ({"action": "list", "name": "extra"}, "list does not accept name"),
        ({"action": "start", "name": "callback", "command": ""}, "start requires command"),
        ({"action": "send", "name": "callback", "input": 3}, "send requires input"),
        ({"action": "send", "name": "callback", "input": "x", "enter": 1}, "enter must be a boolean"),
    ],
)
def test_tmux_rejects_invalid_action_shapes_before_execution(
    tmp_path: Path, arguments: dict[str, object], message: str
) -> None:
    definition, recorder = recorded_tmux_definition(tmp_path)

    with pytest.raises(ValueError, match=message):
        definition.execute("invalid", arguments)

    assert recorder.calls == []


def test_tmux_start_requires_existing_directory(tmp_path: Path) -> None:
    file_path = tmp_path / "evidence.txt"
    file_path.write_text("evidence", encoding="utf-8")
    definition, recorder = recorded_tmux_definition(tmp_path)

    with pytest.raises(NotADirectoryError, match="tmux cwd is not a directory"):
        definition.execute(
            "start",
            {
                "action": "start",
                "name": "callback",
                "command": "pwd",
                "cwd": "evidence.txt",
            },
        )
    with pytest.raises(FileNotFoundError, match="tmux cwd does not exist"):
        definition.execute(
            "start",
            {
                "action": "start",
                "name": "callback",
                "command": "pwd",
                "cwd": "missing",
            },
        )
    assert recorder.calls == []


@pytest.mark.parametrize(
    "responses",
    [
        [
            (1, "", "can't find session"),
            (0, "", ""),
            (2, "", "set-option failed"),
            (0, "", ""),
        ],
        [
            (1, "", "can't find session"),
            (0, "", ""),
            (0, "", ""),
            (2, "", "respawn-pane failed"),
            (0, "", ""),
        ],
    ],
)
def test_tmux_start_cleans_up_when_persistent_pane_setup_fails(
    tmp_path: Path,
    responses: list[tuple[int, str, str]],
) -> None:
    definition, recorder = recorded_tmux_definition(tmp_path, responses)

    with pytest.raises(RuntimeError, match="tmux command failed"):
        definition.execute(
            "start",
            {"action": "start", "name": "callback", "command": "sleep 10"},
        )

    assert recorder.calls[-1][1:3] == ["kill-session", "-t"]


def test_tmux_reports_missing_executable_without_running(tmp_path: Path) -> None:
    definition, recorder = recorded_tmux_definition(tmp_path, executable=None)

    with pytest.raises(RuntimeError, match="tmux executable not found; install tmux and retry"):
        definition.execute("list", {"action": "list"})

    assert recorder.calls == []


def test_tmux_duplicate_and_absent_session_errors_include_resolved_name(tmp_path: Path) -> None:
    digest = hashlib.sha256(str(tmp_path.resolve()).encode("utf-8")).hexdigest()[:12]
    resolved = f"travis234-{digest}-callback"
    duplicate, _ = recorded_tmux_definition(tmp_path, [(0, "", "")])
    absent, _ = recorded_tmux_definition(tmp_path, [(1, "", "can't find session")])

    with pytest.raises(RuntimeError, match=resolved):
        duplicate.execute(
            "start",
            {"action": "start", "name": "callback", "command": "sleep 10"},
        )
    with pytest.raises(RuntimeError, match=resolved):
        absent.execute(
            "capture", {"action": "capture", "name": "callback"}
        )


def test_tmux_stop_is_idempotent_and_list_handles_missing_server(tmp_path: Path) -> None:
    definition, recorder = recorded_tmux_definition(
        tmp_path,
        [
            (1, "", "can't find session"),
            (1, "", "no server running on /tmp/tmux-501/default"),
        ],
    )

    stopped = definition.execute("stop", {"action": "stop", "name": "callback"})
    listed = definition.execute("list", {"action": "list"})

    assert stopped.details["alreadyAbsent"] is True
    assert listed.details == {"action": "list", "sessions": []}
    assert all("kill-session" not in call for call in recorder.calls)


def test_tmux_capture_and_failure_output_are_bounded(tmp_path: Path) -> None:
    huge_output = "\n".join(f"line-{index}-" + ("x" * 40) for index in range(2500))
    huge_error = "\n".join(f"error-{index}-" + ("y" * 400) for index in range(50))
    definition, _ = recorded_tmux_definition(
        tmp_path,
        [
            (0, "", ""),
            (0, huge_output, ""),
            (2, "", huge_error),
        ],
    )

    captured = definition.execute(
        "capture", {"action": "capture", "name": "callback", "lines": 2000}
    )
    assert len(result_text(captured).encode("utf-8")) <= 50 * 1024
    assert captured.details["truncation"]["truncated"] is True
    with pytest.raises(RuntimeError) as failure:
        definition.execute("list", {"action": "list"})
    error_text = str(failure.value)
    assert len(error_text.encode("utf-8")) <= 4000
    assert len(error_text.splitlines()) <= 20


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_real_tmux_round_trip(tmp_path: Path) -> None:
    name = f"smoke-{uuid.uuid4().hex[:8]}"
    definition = create_tmux_tool_definition(str(tmp_path))
    try:
        started = definition.execute(
            "start",
            {
                "action": "start",
                "name": name,
                "command": "printf 'TMUX-SMOKE-OK\\n'; sleep 2",
            },
        )
        deadline = time.monotonic() + 2
        output = ""
        while "TMUX-SMOKE-OK" not in output and time.monotonic() < deadline:
            output = result_text(
                definition.execute(
                    "capture", {"action": "capture", "name": name, "lines": 50}
                )
            )
            time.sleep(0.05)
        listed = definition.execute("list", {"action": "list"})
        assert "TMUX-SMOKE-OK" in output
        assert started.details["sessionName"] in listed.details["sessions"]
    finally:
        definition.execute("stop", {"action": "stop", "name": name})


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_real_tmux_preserves_fast_command_output_until_explicit_stop(
    tmp_path: Path,
) -> None:
    name = f"fast-{uuid.uuid4().hex[:8]}"
    definition = create_tmux_tool_definition(str(tmp_path))
    try:
        definition.execute(
            "start",
            {
                "action": "start",
                "name": name,
                "command": "printf 'TMUX-FAST-OK\\n'",
            },
        )
        time.sleep(0.2)

        captured = definition.execute(
            "capture",
            {"action": "capture", "name": name, "lines": 50},
        )
        listed = definition.execute("list", {"action": "list"})

        assert "TMUX-FAST-OK" in result_text(captured)
        assert captured.details["sessionName"] in listed.details["sessions"]
    finally:
        definition.execute("stop", {"action": "stop", "name": name})


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")
def test_child_tmux_session_is_visible_to_parent_in_same_workspace(tmp_path: Path) -> None:
    name = f"child-{uuid.uuid4().hex[:8]}"
    parent = create_tmux_tool_definition(str(tmp_path))
    child = create_tmux_tool_definition(str(tmp_path))
    try:
        started = child.execute(
            "start",
            {
                "action": "start",
                "name": name,
                "command": "printf 'CHILD-TMUX-OK\\n'; sleep 5",
            },
        )
        deadline = time.monotonic() + 2
        output = ""
        while "CHILD-TMUX-OK" not in output and time.monotonic() < deadline:
            output = result_text(
                parent.execute("capture", {"action": "capture", "name": name, "lines": 50})
            )
            time.sleep(0.05)
        listed = parent.execute("list", {"action": "list"})

        assert "CHILD-TMUX-OK" in output
        assert started.details["sessionName"] in listed.details["sessions"]
    finally:
        parent.execute("stop", {"action": "stop", "name": name})
