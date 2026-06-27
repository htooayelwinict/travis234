from __future__ import annotations

from pathlib import Path

from appv23.sandbox_launcher import (
    SandboxConfig,
    build_docker_command,
    prepare_sandbox_imports,
    resolve_host_path,
    resolve_sandbox_config,
)


def test_resolve_sandbox_config_uses_workspace_and_isolated_home(tmp_path: Path) -> None:
    workspace = tmp_path / "docs"
    workspace.mkdir()
    app_root = tmp_path / "appV2.3"
    app_root.mkdir()

    config = resolve_sandbox_config(workspace=workspace, app_root=app_root, extra_args=["--model", "openrouter/test"])

    assert config.workspace == workspace.resolve()
    assert config.app_root == app_root.resolve()
    assert config.container_workspace == "/workspace"
    assert config.container_agent_home == "/agent-home"
    assert config.extra_args == ["--model", "openrouter/test"]


def test_resolve_host_path_uses_base_dir_for_relative_paths(tmp_path: Path) -> None:
    assert resolve_host_path("docs", base_dir=tmp_path) == (tmp_path / "docs").resolve()


def test_docker_command_mounts_only_workspace_app_and_agent_home(tmp_path: Path) -> None:
    workspace = tmp_path / "docs"
    app_root = tmp_path / "appV2.3"
    agent_home = tmp_path / "agent-home"
    workspace.mkdir()
    app_root.mkdir()
    agent_home.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text("OPENROUTER_API_KEY=secret\n", encoding="utf-8")

    config = SandboxConfig(
        workspace=workspace,
        app_root=app_root,
        agent_home=agent_home,
        image="python:3.13-slim",
        extra_args=["--dotenv", str(env_file), "--plain", "hi"],
    )

    command = build_docker_command(config)
    joined = "\0".join(command)

    assert command[:5] == ["docker", "run", "--rm", "-it", "--name"]
    assert f"{workspace.resolve()}:/workspace:rw" in command
    assert f"{agent_home.resolve()}:/agent-home:rw" in command
    assert str(app_root.resolve()) not in joined
    assert "--env-file" not in command
    assert str(env_file) not in joined
    assert "OPENROUTER_API_KEY" not in joined
    assert "PI_CODING_AGENT_DIR=/agent-home/agent" in command
    assert "APPV23_SANDBOX=1" in command
    assert "--cwd" in command
    assert "/workspace" in command
    assert "--dotenv" not in command


def test_docker_command_rejects_parent_dotenv_args(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    app_root = tmp_path / "appV2.3"
    agent_home = tmp_path / "agent-home"
    workspace.mkdir()
    app_root.mkdir()
    agent_home.mkdir()

    config = SandboxConfig(
        workspace=workspace,
        app_root=app_root,
        agent_home=agent_home,
        image="python:3.13-slim",
        extra_args=["--dotenv", "../.env", "--plain", "hi"],
    )

    command = build_docker_command(config)

    assert "--dotenv" not in command
    assert "../.env" not in command
    assert command[-2:] == ["--plain", "hi"]


def test_prepare_sandbox_imports_copies_user_agents_skills(tmp_path: Path, monkeypatch) -> None:
    host_home = tmp_path / "host-home"
    user_skills = host_home / ".agents" / "skills"
    user_skills.mkdir(parents=True)
    (user_skills / "web_search.md").write_text("---\nname: web-search\n---\nUse curl.\n", encoding="utf-8")
    (user_skills / ".env").write_text("SECRET=not-copied\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(host_home))

    config = resolve_sandbox_config(
        workspace=tmp_path / "workspace",
        app_root=tmp_path / "appV2.3",
        agent_home=tmp_path / "sandbox-home",
    )

    prepare_sandbox_imports(config)

    imported = config.agent_home / ".agents" / "skills" / "web_search.md"
    assert imported.read_text(encoding="utf-8") == "---\nname: web-search\n---\nUse curl.\n"
    assert not (config.agent_home / ".agents" / "skills" / ".env").exists()


def test_prepare_sandbox_imports_writes_explicit_agents_file(tmp_path: Path) -> None:
    agents_file = tmp_path / "AGENTS.md"
    agents_file.write_text("Stay inside cwd.\n", encoding="utf-8")
    config = resolve_sandbox_config(
        workspace=tmp_path / "workspace",
        app_root=tmp_path / "appV2.3",
        agent_home=tmp_path / "sandbox-home",
        agents_files=[agents_file],
        import_user_skills=False,
    )

    prepare_sandbox_imports(config)

    imported = config.agent_home / "agent" / "AGENTS.md"
    text = imported.read_text(encoding="utf-8")
    assert "appv23-sandbox-imported-agents" in text
    assert "Stay inside cwd." in text
