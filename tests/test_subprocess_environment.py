from __future__ import annotations


def test_model_tool_environment_strips_provider_credentials(monkeypatch) -> None:
    from travis.coding_agent.tools.bash import get_shell_env

    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")
    monkeypatch.setenv("STEPFUN_API_KEY", "stepfun-secret")
    monkeypatch.setenv("TRAVIS234_WORKER_LLM_API_KEY", "worker-secret")
    monkeypatch.setenv("TRAVIS234_COMPRESSION_LLM_API_KEY", "compression-secret")
    monkeypatch.setenv("PROJECT_SETTING", "visible")

    env = get_shell_env()

    assert "OPENROUTER_API_KEY" not in env
    assert "STEPFUN_API_KEY" not in env
    assert "TRAVIS234_WORKER_LLM_API_KEY" not in env
    assert "TRAVIS234_COMPRESSION_LLM_API_KEY" not in env
    assert env["PROJECT_SETTING"] == "visible"


def test_model_tool_environment_allows_explicit_named_passthrough(monkeypatch) -> None:
    from travis.coding_agent.tools.bash import get_shell_env

    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")
    monkeypatch.setenv("STEPFUN_API_KEY", "stepfun-secret")
    monkeypatch.setenv("TRAVIS234_TOOL_ENV_PASSTHROUGH", " OPENROUTER_API_KEY,NOT_PRESENT ")

    env = get_shell_env()

    assert env["OPENROUTER_API_KEY"] == "openrouter-secret"
    assert "STEPFUN_API_KEY" not in env
    assert "TRAVIS234_TOOL_ENV_PASSTHROUGH" not in env


def test_operator_shell_environment_can_inherit_provider_credentials(monkeypatch) -> None:
    from travis.coding_agent.tools.bash import get_shell_env

    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-secret")

    env = get_shell_env(sanitize_credentials=False)

    assert env["OPENROUTER_API_KEY"] == "openrouter-secret"
    assert "TRAVIS234_TOOL_ENV_PASSTHROUGH" not in env
