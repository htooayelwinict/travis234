from __future__ import annotations

import os
from pathlib import Path

import pytest

from travis.ai.types import UserMessage, now_ms
from travis.coding_agent.session_catalog import (
    InvalidSessionError,
    SessionAmbiguousError,
    SessionCatalog,
    SessionNotFoundError,
)
from travis.coding_agent.session_store import SessionStore


def _write_session(
    catalog: SessionCatalog,
    cwd: Path,
    *,
    session_id: str,
    message: str,
    modified_ns: int,
    name: str | None = None,
    model: tuple[str, str] | None = None,
) -> Path:
    cwd.mkdir(parents=True, exist_ok=True)
    session_path, resolved_id = catalog.new_session_path(str(cwd), session_id=session_id)
    assert resolved_id == session_id
    store = SessionStore(session_path, cwd=str(cwd.resolve()), session_id=session_id)
    if model is not None:
        store.append_model_change(*model)
    if name is not None:
        store.append_session_info(name)
    store.append_message(UserMessage(content=message, timestamp=now_ms()))
    path = Path(session_path)
    os.utime(path, ns=(modified_ns, modified_ns))
    return path


def test_new_session_path_preserves_app_owned_workspace_layout(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    cwd = tmp_path / "repo"
    cwd.mkdir()
    catalog = SessionCatalog(str(agent_dir))

    path, session_id = catalog.new_session_path(str(cwd), session_id="fixed-id")

    safe_cwd = f"--{str(cwd.resolve()).lstrip(os.sep).replace(os.sep, '-').replace(':', '-')}--"
    assert Path(path).parent == agent_dir / "sessions" / safe_cwd
    assert Path(path).name.endswith("_fixed-id.jsonl")
    assert session_id == "fixed-id"


def test_continue_recent_returns_latest_valid_session_for_exact_cwd(tmp_path: Path) -> None:
    catalog = SessionCatalog(str(tmp_path / "agent"))
    project = tmp_path / "project"
    other = tmp_path / "other"
    older = _write_session(catalog, project, session_id="older", message="old", modified_ns=10)
    newer = _write_session(catalog, project, session_id="newer", message="new", modified_ns=20)
    _write_session(catalog, other, session_id="other", message="other", modified_ns=30)

    listed = catalog.list_for_cwd(str(project))

    assert [info.path for info in listed] == [newer, older]
    assert catalog.continue_recent(str(project)).path == newer


def test_catalog_orders_submicrosecond_updates_by_mtime_ns(tmp_path: Path) -> None:
    catalog = SessionCatalog(str(tmp_path / "agent"))
    project = tmp_path / "project"
    older = _write_session(catalog, project, session_id="older", message="old", modified_ns=10)
    newer = _write_session(catalog, project, session_id="newer", message="new", modified_ns=20)
    older = older.rename(older.with_name("z-older.jsonl"))
    newer = newer.rename(newer.with_name("a-newer.jsonl"))

    assert [info.path for info in catalog.list_for_cwd(str(project))] == [newer, older]


def test_catalog_extracts_bounded_display_metadata(tmp_path: Path) -> None:
    catalog = SessionCatalog(str(tmp_path / "agent"))
    project = tmp_path / "project"
    path = _write_session(
        catalog,
        project,
        session_id="metadata",
        message="x" * 400,
        modified_ns=20,
        name="Release repair",
        model=("openrouter", "xiaomi/mimo-v2.5-pro"),
    )

    info = catalog.resolve(str(path), cwd=str(project), launch_dir=str(tmp_path))

    assert info.session_id == "metadata"
    assert info.cwd == project.resolve()
    assert info.name == "Release repair"
    assert info.model == "openrouter/xiaomi/mimo-v2.5-pro"
    assert info.preview.endswith("...")
    assert len(info.preview) <= 123


def test_resolve_accepts_relative_path_from_launch_directory(tmp_path: Path) -> None:
    catalog = SessionCatalog(str(tmp_path / "agent"))
    project = tmp_path / "project"
    path = _write_session(catalog, project, session_id="relative", message="kept", modified_ns=10)

    info = catalog.resolve(
        os.path.relpath(path, tmp_path),
        cwd=str(project),
        launch_dir=str(tmp_path),
    )

    assert info.path == path


def test_resolve_prefers_current_workspace_id_match(tmp_path: Path) -> None:
    catalog = SessionCatalog(str(tmp_path / "agent"))
    current = tmp_path / "current"
    elsewhere = tmp_path / "elsewhere"
    expected = _write_session(catalog, current, session_id="same", message="current", modified_ns=10)
    _write_session(catalog, elsewhere, session_id="same", message="elsewhere", modified_ns=20)

    info = catalog.resolve("same", cwd=str(current), launch_dir=str(tmp_path))

    assert info.path == expected


def test_resolve_rejects_ambiguous_global_id(tmp_path: Path) -> None:
    catalog = SessionCatalog(str(tmp_path / "agent"))
    current = tmp_path / "current"
    current.mkdir()
    first = _write_session(catalog, tmp_path / "first", session_id="same", message="first", modified_ns=10)
    second = _write_session(catalog, tmp_path / "second", session_id="same", message="second", modified_ns=20)

    with pytest.raises(SessionAmbiguousError, match="same") as raised:
        catalog.resolve("same", cwd=str(current), launch_dir=str(tmp_path))

    assert set(raised.value.paths) == {first, second}


def test_explicit_corrupt_target_fails_but_listing_keeps_valid_sessions(tmp_path: Path) -> None:
    catalog = SessionCatalog(str(tmp_path / "agent"))
    project = tmp_path / "project"
    valid = _write_session(catalog, project, session_id="valid", message="valid", modified_ns=10)
    corrupt_path, _ = catalog.new_session_path(str(project), session_id="corrupt")
    corrupt = Path(corrupt_path)
    corrupt.write_text('{"type":"session","version":3,"id":"corrupt","cwd":"x"}\nnot-json\n', encoding="utf-8")

    listed = catalog.list_for_cwd(str(project))

    assert [info.path for info in listed] == [valid]
    assert len(catalog.diagnostics) == 1
    assert str(corrupt) in catalog.diagnostics[0]
    with pytest.raises(InvalidSessionError, match="corrupt"):
        catalog.resolve(str(corrupt), cwd=str(project), launch_dir=str(tmp_path))


def test_continue_recent_does_not_fall_back_to_new_session(tmp_path: Path) -> None:
    catalog = SessionCatalog(str(tmp_path / "agent"))
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(SessionNotFoundError, match="No previous session for this workspace"):
        catalog.continue_recent(str(project))

    assert list((tmp_path / "agent").rglob("*.jsonl")) == []


def test_resolve_missing_target_reports_original_value(tmp_path: Path) -> None:
    catalog = SessionCatalog(str(tmp_path / "agent"))
    project = tmp_path / "project"
    project.mkdir()

    with pytest.raises(SessionNotFoundError, match="missing-session"):
        catalog.resolve("missing-session", cwd=str(project), launch_dir=str(tmp_path))
