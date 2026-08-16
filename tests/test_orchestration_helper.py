from __future__ import annotations

import importlib.util
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

import pytest


ROOT = Path(__file__).parents[1]
HELPER = ROOT / "travis/resources/skills/orchestration/scripts/orchestrate.py"
SKILL = ROOT / "travis/resources/skills/orchestration/SKILL.md"
PROTOCOL = ROOT / "travis/resources/skills/orchestration/references/protocol.md"


def load_helper():
    spec = importlib.util.spec_from_file_location("travis234_orchestration_helper", HELPER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def write_request(path: Path, payload: object, *, mode: int = 0o600) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(mode)
    return path


def run_helper(agent_dir: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["TRAVIS234_CODING_AGENT_DIR"] = str(agent_dir)
    return subprocess.run(
        [sys.executable, str(HELPER), *arguments],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
    )


def parse_success(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert completed.stdout.count("\n") == 1
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    return payload


def parse_failure(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr.count("\n") == 1
    assert "Traceback" not in completed.stderr
    assert "usage:" not in completed.stderr.lower()
    payload = json.loads(completed.stderr)
    assert payload["ok"] is False
    return payload


def test_skill_instruction_shape_encodes_observed_safety_guards() -> None:
    source = SKILL.read_text(encoding="utf-8")
    _, frontmatter_text, body = source.split("---", 2)
    frontmatter = dict(
        line.split(": ", 1)
        for line in frontmatter_text.strip().splitlines()
    )

    assert frontmatter["name"] == "orchestration"
    assert frontmatter["description"].startswith("Use when ")
    assert len(frontmatter["description"]) < 500
    assert set(frontmatter) == {"name", "description"}
    assert len(body.split()) <= 500
    assert "references/protocol.md" in body
    assert "supervised" in body and "full handoff" in body
    assert "subagent" in body and "independent" in body
    assert "Do not" in body and "automatic" in body
    assert "Travis A owns the user conversation" in body
    assert "bash" in body and "tmux" in body
    assert "_relay" not in body
    assert body.count("For example") == 1


def test_protocol_reference_is_the_detailed_single_owner() -> None:
    source = PROTOCOL.read_text(encoding="utf-8")
    assert source.startswith("# Travis234 Orchestration Protocol\n\n## Contents")
    for section in (
        "Public commands",
        "Request files and envelopes",
        "Identities and states",
        "Supervised recipe",
        "Full handoff",
        "Ownership, trust, and Git",
        "Capability and secret boundary",
        "Limits",
        "Lifecycle and recovery",
        "Failure receipts",
    ):
        assert section in source
    module = load_helper()
    for command in module.GUIDE_COMMANDS:
        assert f"`{command}`" in source
    for status in (
        module.RUN_STATUSES
        | module.TASK_STATUSES
        | module.WORKER_STATUSES
        | module.DISPATCH_STATUSES
    ):
        assert f"`{status}`" in source
    for packet_field in module.HANDOFF_KEYS:
        assert f"`{packet_field}`" in source
    assert "60 seconds" in source
    assert "two" in source.lower() and "twelve" in source.lower()
    assert "automatic replay" in source.lower()


def test_orchestration_does_not_modify_system_prompt_or_subagent_skill() -> None:
    protected = {
        ROOT / "travis/coding_agent/system_prompt.py": "a0dab588bf45707a9eb8307907120753e63750b9fd015ca9508a5ce52d71e505",
        ROOT / "travis/resources/skills/subagent-delegation/SKILL.md": "2417a6dab69057d16b1f8f687382e887a057788f522aaffe4497c46519d3d838",
    }
    for path, expected in protected.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected


def test_guide_emits_one_stable_versioned_json_envelope() -> None:
    completed = subprocess.run(
        [sys.executable, str(HELPER), "guide"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["schemaVersion"] == 1
    assert payload["protocolVersion"] == 1
    assert payload["command"] == "guide"
    assert payload["result"] == {
        "commands": [
            "guide",
            "run-create",
            "run-show",
            "run-list",
            "task-create",
            "task-show",
            "task-list",
            "worker-start",
                "worker-show",
                "worker-list",
                "dispatch-start",
                "dispatch-show",
                "dispatch-wait",
                "worker-complete",
                "worker-fail",
                "message-send",
                "message-check",
                "message-ack",
                "message-reply",
                "dispatch-cancel",
                "dispatch-abandon",
                "worker-retain",
                "worker-release",
                "recover",
            ],
        "invocation": "python3 scripts/orchestrate.py <command> [arguments]",
    }
    assert payload["nextActions"] == []


def test_state_store_uses_private_existing_agent_root_and_versioned_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = load_helper()
    agent_dir = tmp_path / "agent"
    monkeypatch.setenv("TRAVIS234_CODING_AGENT_DIR", str(agent_dir))

    state = module.StateStore.open()
    try:
        assert state.root == agent_dir / "orchestration"
        assert stat.S_IMODE(state.root.stat().st_mode) == 0o700
        assert stat.S_IMODE((state.root / "sockets").stat().st_mode) == 0o700
        assert stat.S_IMODE((state.root / "runs").stat().st_mode) == 0o700
        assert stat.S_IMODE(state.path.stat().st_mode) == 0o600
        assert state.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert state.connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert state.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        tables = {
            row[0]
            for row in state.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "meta",
            "runs",
            "tasks",
            "workers",
            "dispatches",
            "messages",
            "idempotency",
        } <= tables
        assert dict(state.connection.execute("SELECT key, value FROM meta")) == {
            "protocol_version": "1",
            "schema_version": "1",
        }
        for path in state.root.rglob("*"):
            expected = 0o700 if path.is_dir() else 0o600
            assert stat.S_IMODE(path.stat().st_mode) == expected, path
    finally:
        state.close()


def test_run_and_task_creation_are_idempotent_with_stable_state_receipts(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    run_request = write_request(
        tmp_path / "run.json",
        {"objective": "Inspect the parser", "coordinatorSessionId": "session-alpha"},
    )
    first_run = parse_success(
        run_helper(
            agent_dir,
            "run-create",
            "--request-file",
            str(run_request),
            "--idempotency-key",
            "run-once",
        )
    )
    second_run = parse_success(
        run_helper(
            agent_dir,
            "run-create",
            "--request-file",
            str(run_request),
            "--idempotency-key",
            "run-once",
        )
    )
    first_run_result = first_run["result"]
    second_run_result = second_run["result"]
    assert isinstance(first_run_result, dict) and isinstance(second_run_result, dict)
    assert first_run_result["effect"] == "created"
    assert second_run_result["effect"] == "reused"
    run = first_run_result["run"]
    reused_run = second_run_result["run"]
    assert isinstance(run, dict) and isinstance(reused_run, dict)
    assert run["runId"] == reused_run["runId"]
    assert str(run["runId"]).startswith("run_")

    task_request = write_request(
        tmp_path / "task.json",
        {
            "objective": "Report parser ownership",
            "ownership": {"ownedPaths": ["parser/"], "forbiddenPaths": ["README.md"]},
            "acceptanceCriteria": ["Cite the owning module"],
            "dependencies": [],
            "mode": "supervised",
            "maxRounds": 4,
            "commitPolicy": "no_commit",
        },
    )
    first_task = parse_success(
        run_helper(
            agent_dir,
            "task-create",
            "--run-id",
            str(run["runId"]),
            "--request-file",
            str(task_request),
            "--idempotency-key",
            "task-once",
        )
    )
    second_task = parse_success(
        run_helper(
            agent_dir,
            "task-create",
            "--run-id",
            str(run["runId"]),
            "--request-file",
            str(task_request),
            "--idempotency-key",
            "task-once",
        )
    )
    first_task_result = first_task["result"]
    second_task_result = second_task["result"]
    assert isinstance(first_task_result, dict) and isinstance(second_task_result, dict)
    assert first_task_result["effect"] == "created"
    assert second_task_result["effect"] == "reused"
    task = first_task_result["task"]
    reused_task = second_task_result["task"]
    assert isinstance(task, dict) and isinstance(reused_task, dict)
    assert task["taskId"] == reused_task["taskId"]
    assert task["runId"] == run["runId"]
    assert task["mode"] == "supervised"
    assert task["maxRounds"] == 4
    assert task["promptCount"] == 0
    assert task["ownership"] == {
        "ownedPaths": ["parser/"],
        "forbiddenPaths": ["README.md"],
    }
    assert task["acceptanceCriteria"] == ["Cite the owning module"]

    database = sqlite3.connect(agent_dir / "orchestration" / "state.sqlite3")
    try:
        assert database.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        assert database.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1
        assert database.execute("SELECT count(*) FROM idempotency").fetchone()[0] == 2
    finally:
        database.close()

    shown_run = parse_success(
        run_helper(agent_dir, "run-show", "--run-id", str(run["runId"]))
    )
    shown_task = parse_success(
        run_helper(agent_dir, "task-show", "--task-id", str(task["taskId"]))
    )
    assert shown_run["result"] == {"run": reused_run}
    assert shown_task["result"] == {"task": reused_task}


@pytest.mark.parametrize(
    ("request_payload", "command", "expected_code"),
    [
        ({"objective": "valid", "unknown": True}, "run-create", "invalid_request"),
        (
            {
                "objective": "valid",
                "ownership": {},
                "acceptanceCriteria": ["done"],
                "mode": "recursive_swarm",
                "commitPolicy": "no_commit",
            },
            "task-create",
            "invalid_request",
        ),
        (
            {
                "objective": "valid",
                "ownership": {},
                "acceptanceCriteria": ["done"],
                "mode": ["supervised"],
                "commitPolicy": "no_commit",
            },
            "task-create",
            "invalid_request",
        ),
        (
            {
                "objective": "valid",
                "ownership": {},
                "acceptanceCriteria": ["done"],
                "mode": "supervised",
                "maxRounds": 13,
                "commitPolicy": "no_commit",
            },
            "task-create",
            "invalid_request",
        ),
        ({"objective": "valid", "apiKey": "not-printed"}, "run-create", "secret_like_input"),
        ({"objective": "Bearer abcdefghijklmnopqrstuvwxyz"}, "run-create", "secret_like_input"),
    ],
)
def test_request_schema_and_secret_validation_fail_closed(
    tmp_path: Path,
    request_payload: dict[str, object],
    command: str,
    expected_code: str,
) -> None:
    agent_dir = tmp_path / "agent"
    request_file = write_request(tmp_path / "request.json", request_payload)
    arguments = [
        command,
        "--request-file",
        str(request_file),
        "--idempotency-key",
        "validation-case",
    ]
    if command == "task-create":
        arguments[1:1] = ["--run-id", "run_000000000000000000000000"]

    result = parse_failure(run_helper(agent_dir, *arguments))

    assert result["error"]["code"] == expected_code
    assert "not-printed" not in json.dumps(result)
    assert "abcdefghijklmnopqrstuvwxyz" not in json.dumps(result)


def test_request_schema_rejects_oversize_and_malformed_identifiers(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b'{"objective":"' + (b"x" * (256 * 1024)) + b'"}')
    oversized.chmod(0o600)

    size_failure = parse_failure(
        run_helper(
            agent_dir,
            "run-create",
            "--request-file",
            str(oversized),
            "--idempotency-key",
            "oversized",
        )
    )
    id_failure = parse_failure(run_helper(agent_dir, "run-show", "--run-id", "run-nope"))

    assert size_failure["error"]["code"] == "request_too_large"
    assert id_failure["error"]["code"] == "invalid_id"


def test_consume_request_file_removes_only_validated_regular_private_file(
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / "agent"
    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not-json", encoding="utf-8")
    malformed.chmod(0o600)

    malformed_result = parse_failure(
        run_helper(
            agent_dir,
            "run-create",
            "--request-file",
            str(malformed),
            "--consume-request-file",
            "--idempotency-key",
            "malformed",
        )
    )
    assert malformed_result["error"]["code"] == "invalid_json"
    assert not malformed.exists()

    public = write_request(tmp_path / "public.json", {"objective": "valid"}, mode=0o644)
    public_result = parse_failure(
        run_helper(
            agent_dir,
            "run-create",
            "--request-file",
            str(public),
            "--consume-request-file",
            "--idempotency-key",
            "public",
        )
    )
    assert public_result["error"]["code"] == "unsafe_request_file"
    assert public.exists()

    target = write_request(tmp_path / "target.json", {"objective": "valid"})
    link = tmp_path / "link.json"
    link.symlink_to(target)
    link_result = parse_failure(
        run_helper(
            agent_dir,
            "run-create",
            "--request-file",
            str(link),
            "--consume-request-file",
            "--idempotency-key",
            "link",
        )
    )
    assert link_result["error"]["code"] == "unsafe_request_file"
    assert link.is_symlink()
    assert target.exists()


def test_incompatible_schema_is_read_only_and_invalid_status_is_safe(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    request_file = write_request(tmp_path / "run.json", {"objective": "stable"})
    created = parse_success(
        run_helper(
            agent_dir,
            "run-create",
            "--request-file",
            str(request_file),
            "--idempotency-key",
            "schema-seed",
        )
    )
    run = created["result"]["run"]
    assert isinstance(run, dict)
    database_path = agent_dir / "orchestration" / "state.sqlite3"
    database = sqlite3.connect(database_path)
    try:
        database.execute("UPDATE meta SET value = '999' WHERE key = 'schema_version'")
        database.execute("DROP TABLE messages")
        database.commit()
    finally:
        database.close()

    shown = parse_success(run_helper(agent_dir, "run-show", "--run-id", str(run["runId"])))
    blocked = parse_failure(
        run_helper(
            agent_dir,
            "run-create",
            "--request-file",
            str(request_file),
            "--idempotency-key",
            "schema-blocked",
        )
    )
    assert shown["result"]["run"]["runId"] == run["runId"]
    assert blocked["error"]["code"] == "incompatible_state"

    database = sqlite3.connect(database_path)
    try:
        assert database.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
        assert database.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
        ).fetchone()[0] == 0
        database.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")
        database.execute("UPDATE runs SET status = 'invented' WHERE run_id = ?", (run["runId"],))
        database.commit()
    finally:
        database.close()
    invalid_status = parse_failure(
        run_helper(agent_dir, "run-show", "--run-id", str(run["runId"]))
    )
    assert invalid_status["error"]["code"] == "invalid_state"


def test_malformed_cli_and_secret_like_idempotency_key_emit_one_safe_json_frame(
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / "agent"
    malformed = parse_failure(run_helper(agent_dir, "run-show", "--unexpected", "value"))
    assert malformed["error"]["code"] == "invalid_arguments"

    secret_value = "sk-proj-thismustneverappearinoutput"
    request_file = write_request(tmp_path / "run.json", {"objective": "safe"})
    completed = run_helper(
        agent_dir,
        "run-create",
        "--request-file",
        str(request_file),
        "--idempotency-key",
        secret_value,
    )
    secret = parse_failure(completed)
    assert secret["error"]["code"] == "secret_like_input"
    assert secret_value not in completed.stderr
