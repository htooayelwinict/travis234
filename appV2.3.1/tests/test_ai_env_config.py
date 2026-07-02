from __future__ import annotations

from pathlib import Path

from appv231.ai.env_config import (
    DEFAULT_MODEL_PER_PROVIDER,
    find_env_keys,
    get_default_model_for_provider,
    load_dotenv_values,
    load_model_config,
)
from appv231.ai.providers.params import GenerationParams


PARAM_ENV_KEYS = (
    "APPV2_WORKER_LLM_TEMPERATURE",
    "APPV2_WORKER_LLM_TOP_P",
    "APPV2_WORKER_LLM_FREQUENCY_PENALTY",
    "APPV2_WORKER_LLM_PRESENCE_PENALTY",
    "APPV2_WORKER_LLM_SEED",
    "APPV2_WORKER_LLM_STOP",
    "APPV2_WORKER_LLM_PROVIDER_SORT",
    "APPV2_WORKER_LLM_MAX_TOKENS",
    "APPV2_WORKER_LLM_TIMEOUT_SECONDS",
    "OPENROUTER_PROVIDER_SORT",
)


def _clear_param_env(monkeypatch) -> None:
    for key in PARAM_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_load_dotenv_values_strips_quotes_and_comments(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        'APPV2_WORKER_LLM_API_KEY="secret"  # inline comment\n'
        "APPV2_WORKER_LLM_MODEL=acme/model-x\n"
        "# full comment line\n",
        encoding="utf-8",
    )
    values = load_dotenv_values(env)
    assert values["APPV2_WORKER_LLM_API_KEY"] == "secret"
    assert values["APPV2_WORKER_LLM_MODEL"] == "acme/model-x"


def test_load_model_config_resolves_prefix_then_fallbacks(tmp_path: Path, monkeypatch) -> None:
    _clear_param_env(monkeypatch)
    env = tmp_path / ".env"
    env.write_text(
        "APPV2_WORKER_LLM_ENABLED=true\n"
        "OPENROUTER_API_KEY=fallback-key\n"
        "APPV2_WORKER_LLM_MODEL=acme/model-x\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("APPV2_WORKER_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = load_model_config("APPV2_WORKER_LLM", env)
    assert config.enabled is True
    assert config.api_key == "fallback-key"
    assert config.model == "acme/model-x"
    assert config.base_url == "https://openrouter.ai/api/v1"


def test_disabled_when_flag_absent(tmp_path: Path, monkeypatch) -> None:
    _clear_param_env(monkeypatch)
    monkeypatch.delenv("APPV2_WORKER_LLM_ENABLED", raising=False)
    monkeypatch.delenv("APPV2_WORKER_LLM_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    env = tmp_path / ".env"
    env.write_text("OPENROUTER_API_KEY=k\n", encoding="utf-8")
    config = load_model_config("APPV2_WORKER_LLM", env)
    assert config.enabled is False
    assert config.model == "moonshotai/kimi-k2.6"


def test_default_model_per_provider_tracks_pi_defaults() -> None:
    assert DEFAULT_MODEL_PER_PROVIDER["openai"] == "gpt-5.4"
    assert DEFAULT_MODEL_PER_PROVIDER["openai-codex"] == "gpt-5.5"
    assert DEFAULT_MODEL_PER_PROVIDER["zai"] == "glm-5.1"
    assert DEFAULT_MODEL_PER_PROVIDER["minimax"] == "MiniMax-M2.7"
    assert DEFAULT_MODEL_PER_PROVIDER["minimax-cn"] == "MiniMax-M2.7"
    assert DEFAULT_MODEL_PER_PROVIDER["cerebras"] == "zai-glm-4.7"
    assert DEFAULT_MODEL_PER_PROVIDER["ant-ling"] == "Ring-2.6-1T"
    assert DEFAULT_MODEL_PER_PROVIDER["vercel-ai-gateway"] == "zai/glm-5.1"
    assert get_default_model_for_provider("openrouter") == "moonshotai/kimi-k2.6"
    assert get_default_model_for_provider("unknown-provider") is None


def test_stepfun_env_metadata_is_registered(monkeypatch) -> None:
    monkeypatch.delenv("STEPFUN_API_KEY", raising=False)

    assert get_default_model_for_provider("stepfun") == "step-3.7-flash"
    assert find_env_keys("stepfun") is None
    monkeypatch.setenv("STEPFUN_API_KEY", "test-key")
    assert find_env_keys("stepfun") == ["STEPFUN_API_KEY"]


def test_model_config_exposes_generation_params(tmp_path: Path, monkeypatch) -> None:
    _clear_param_env(monkeypatch)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "APPV2_WORKER_LLM_ENABLED=true",
                "APPV2_WORKER_LLM_API_KEY=test-key",
                "APPV2_WORKER_LLM_PROVIDER_SORT=throughput",
                "APPV2_WORKER_LLM_TEMPERATURE=0.2",
                "APPV2_WORKER_LLM_TOP_P=0.9",
                "APPV2_WORKER_LLM_MAX_TOKENS=4096",
                "APPV2_WORKER_LLM_STOP=END,STOP",
            ]
        ),
        encoding="utf-8",
    )

    config = load_model_config("APPV2_WORKER_LLM", dotenv)

    assert config.generation_params == GenerationParams(
        temperature=0.2,
        top_p=0.9,
        max_tokens=4096,
        stop=("END", "STOP"),
        provider_sort="throughput",
        sources={
            "temperature": "env",
            "top_p": "env",
            "max_tokens": "env",
            "stop": "env",
            "provider_sort": "env",
        },
    )


def test_generation_params_process_env_overrides_dotenv(tmp_path: Path, monkeypatch) -> None:
    _clear_param_env(monkeypatch)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "APPV2_WORKER_LLM_TEMPERATURE=0.2",
                "APPV2_WORKER_LLM_MAX_TOKENS=4096",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("APPV2_WORKER_LLM_TEMPERATURE", "0.4")
    monkeypatch.setenv("APPV2_WORKER_LLM_MAX_TOKENS", "8192")

    config = load_model_config("APPV2_WORKER_LLM", dotenv)

    assert config.generation_params.temperature == 0.4
    assert config.generation_params.max_tokens == 8192


def test_model_config_generation_params_do_not_source_legacy_defaults(tmp_path: Path, monkeypatch) -> None:
    _clear_param_env(monkeypatch)
    env = tmp_path / ".env"
    env.write_text("OPENROUTER_API_KEY=k\n", encoding="utf-8")

    config = load_model_config("APPV2_WORKER_LLM", env)

    assert config.temperature == 0
    assert config.provider_sort == "latency"
    assert config.generation_params == GenerationParams()


def test_generation_params_do_not_make_legacy_temperature_invalid(tmp_path: Path, monkeypatch) -> None:
    _clear_param_env(monkeypatch)
    env = tmp_path / ".env"
    env.write_text("APPV2_WORKER_LLM_TEMPERATURE=3\n", encoding="utf-8")

    config = load_model_config("APPV2_WORKER_LLM", env)

    assert config.temperature == 3
    assert config.generation_params == GenerationParams()


def test_invalid_generation_param_does_not_drop_valid_sibling_params(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _clear_param_env(monkeypatch)
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "APPV2_WORKER_LLM_TEMPERATURE=3",
                "APPV2_WORKER_LLM_MAX_TOKENS=4096",
            ]
        ),
        encoding="utf-8",
    )

    config = load_model_config("APPV2_WORKER_LLM", env)

    assert config.generation_params == GenerationParams(
        max_tokens=4096,
        sources={"max_tokens": "env"},
    )
