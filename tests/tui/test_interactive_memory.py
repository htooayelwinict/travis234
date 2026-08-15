from __future__ import annotations

from pathlib import Path

from tests._support_tui import CodingApp, FakeTerminal, faux_model
from travis.coding_agent import AgentSession, SettingsManager
from travis.tui import visible_width
from travis.tui.components import Text
from travis.tui.interactive_memory import MemoryInspector
from travis.tui.interactive_mode import InteractiveMode


def _settings(*, enabled: bool, global_scope: bool = False) -> SettingsManager:
    scopes = ["project", "global"] if global_scope else ["project"]
    return SettingsManager.in_memory(
        {"memory": {"enabled": enabled, "allowedScopes": scopes}}
    )


def _session(
    cwd: Path,
    agent_dir: Path,
    *,
    enabled: bool = True,
    global_scope: bool = False,
) -> AgentSession:
    return AgentSession(
        cwd=str(cwd),
        agent_dir=str(agent_dir),
        model=faux_model(),
        session_id="session-private-identity",
        settings_manager=_settings(enabled=enabled, global_scope=global_scope),
    )


def test_disabled_status_does_not_create_memory_database(tmp_path: Path) -> None:
    session = _session(tmp_path / "project", tmp_path / "agent", enabled=False)

    lines = MemoryInspector.from_session(session).status()

    assert lines[0] == "Memory: disabled; store=not-open"
    assert "Allowed scopes: project" in lines
    assert "Counts: unavailable" in lines
    assert "Automatic retention: false" in lines
    assert "Automatic injection: false" in lines
    assert not list(tmp_path.rglob("memory.sqlite3"))
    session.dispose()


def test_enabled_status_reports_only_counts_scopes_and_effective_limits(
    tmp_path: Path,
) -> None:
    session = _session(
        tmp_path / "project",
        tmp_path / "agent",
        global_scope=True,
    )
    session._memory_store.retain(
        "PRIVATE project fact",
        tags=["private-tag"],
        scope="project",
        project_key=session._memory_project_key,
        provenance="user_requested",
        now_ms=1,
    )
    session._memory_store.retain(
        "PRIVATE global fact",
        tags=["private-tag"],
        scope="global",
        project_key=session._memory_project_key,
        provenance="user_requested",
        now_ms=1,
    )

    rendered = "\n".join(MemoryInspector.from_session(session).status())

    assert "Memory: enabled; store=available" in rendered
    assert "Allowed scopes: project, global" in rendered
    assert "Counts: project=1 global=1" in rendered
    assert "factBytes=65536 factsPerScope=5000" in rendered
    assert "totalBytes=1073741824 recallRecords=20 recallBytes=32768" in rendered
    assert "PRIVATE" not in rendered
    assert "private-tag" not in rendered
    assert session._memory_project_key not in rendered
    assert str(tmp_path) not in rendered
    assert "session-private-identity" not in rendered
    session.dispose()


def test_unavailable_store_is_reported_without_exception_or_content(
    tmp_path: Path,
) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir(parents=True)
    (agent_dir / "memory.sqlite3").write_bytes(b"corrupt-memory-store")
    session = _session(tmp_path / "project", agent_dir)

    lines = MemoryInspector.from_session(session).status()

    assert lines[0] == "Memory: enabled; store=unavailable"
    assert "Counts: unavailable" in lines
    assert "corrupt" not in "\n".join(lines).lower()
    session.dispose()


def test_project_switch_uses_the_active_project_count_only(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    first = _session(tmp_path / "project-a", agent_dir)
    first._memory_store.retain(
        "alpha",
        tags=["boundary"],
        scope="project",
        project_key=first._memory_project_key,
        provenance="user_requested",
        now_ms=1,
    )
    assert "Counts: project=1 global=0" in MemoryInspector.from_session(first).status()
    first.dispose()

    second = _session(tmp_path / "project-b", agent_dir)

    assert "Counts: project=0 global=0" in MemoryInspector.from_session(second).status()
    second.dispose()


def test_status_lines_render_safely_at_narrow_terminal_width(tmp_path: Path) -> None:
    session = _session(tmp_path / "project", tmp_path / "agent")

    rendered = [
        output
        for line in MemoryInspector.from_session(session).status()
        for output in Text(line).render(20)
    ]

    assert rendered
    assert all(visible_width(line) <= 20 for line in rendered)
    session.dispose()


def test_memory_status_tui_projection_runs_on_dispatcher_owner(tmp_path: Path) -> None:
    app = CodingApp(
        cwd=str(tmp_path / "project"),
        agent_dir=str(tmp_path / "agent"),
        model=faux_model(),
        terminal=FakeTerminal(),
        enable_tui=True,
        settings_manager=_settings(enabled=True),
    )
    mode = InteractiveMode(app)

    assert mode.tui.dispatcher.is_owner_thread()
    mode._run_memory_status_command()
    rendered = "\n".join(mode.history.render(80))

    assert "Memory status" in rendered
    assert "Memory: enabled; store=available" in rendered
    app.close()
