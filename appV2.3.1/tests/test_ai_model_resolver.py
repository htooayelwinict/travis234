from __future__ import annotations

from appv231.ai.model_resolver import (
    ScopedModel,
    find_exact_model_reference_match,
    find_initial_model,
    parse_model_pattern,
    resolve_cli_model,
    resolve_model_scope,
)
from appv231.ai.types import Model


def _model(
    provider: str,
    model_id: str,
    *,
    name: str | None = None,
    reasoning: bool = False,
) -> Model:
    return Model(
        id=model_id,
        name=name or model_id,
        api="faux",
        provider=provider,
        base_url=f"https://{provider}.example.test",
        reasoning=reasoning,
    )


OPENAI_GPT4O = _model("openai", "gpt-4o", name="GPT-4o")
ANTHROPIC_SONNET = _model("anthropic", "claude-sonnet-4-5", name="Claude Sonnet 4.5", reasoning=True)
OPENROUTER_QWEN_EXACTO = _model("openrouter", "qwen/qwen3-coder:exacto", name="Qwen3 Coder Exacto")
OPENROUTER_GPT4O_EXTENDED = _model("openrouter", "openai/gpt-4o:extended", name="GPT-4o Extended")
ALL_MODELS = [ANTHROPIC_SONNET, OPENAI_GPT4O, OPENROUTER_QWEN_EXACTO, OPENROUTER_GPT4O_EXTENDED]


class Registry:
    def __init__(self, models: list[Model], available: list[Model] | None = None, authenticated: set[tuple[str, str]] | None = None):
        self._models = models
        self._available = available if available is not None else models
        self._authenticated = authenticated

    def get_all(self) -> list[Model]:
        return self._models

    getAll = get_all

    def get_available(self) -> list[Model]:
        return self._available

    getAvailable = get_available

    def find(self, provider: str, model_id: str) -> Model | None:
        return next((model for model in self._models if model.provider == provider and model.id == model_id), None)

    def has_configured_auth(self, model: Model) -> bool:
        if self._authenticated is None:
            return True
        return (model.provider, model.id) in self._authenticated

    hasConfiguredAuth = has_configured_auth


def test_find_exact_model_reference_match_handles_canonical_and_ambiguous_bare_ids() -> None:
    duplicate_gpt4o = _model("other", "gpt-4o")
    models = [OPENAI_GPT4O, duplicate_gpt4o, OPENROUTER_GPT4O_EXTENDED]

    assert find_exact_model_reference_match("openrouter/openai/gpt-4o:extended", models) is OPENROUTER_GPT4O_EXTENDED
    assert find_exact_model_reference_match(" openai/GPT-4O ", models) is OPENAI_GPT4O
    assert find_exact_model_reference_match("gpt-4o", models) is None


def test_parse_model_pattern_handles_colon_model_ids_and_thinking_suffixes() -> None:
    exact = parse_model_pattern("openrouter/qwen/qwen3-coder:exacto", ALL_MODELS)
    with_thinking = parse_model_pattern("qwen/qwen3-coder:exacto:high", ALL_MODELS)
    invalid_suffix = parse_model_pattern("qwen/qwen3-coder:exacto:random", ALL_MODELS)

    assert exact.model is OPENROUTER_QWEN_EXACTO
    assert exact.thinking_level is None
    assert exact.warning is None
    assert with_thinking.model is OPENROUTER_QWEN_EXACTO
    assert with_thinking.thinking_level == "high"
    assert with_thinking.warning is None
    assert invalid_suffix.model is OPENROUTER_QWEN_EXACTO
    assert invalid_suffix.thinking_level is None
    assert 'Invalid thinking level "random"' in invalid_suffix.warning


def test_resolve_cli_model_prefers_provider_split_but_preserves_raw_openrouter_ids() -> None:
    zai = _model("zai", "glm-5")
    gateway = _model("vercel-ai-gateway", "zai/glm-5")
    registry = Registry([*ALL_MODELS, zai, gateway])

    provider_split = resolve_cli_model(cli_model="zai/glm-5", model_registry=registry)
    raw_openrouter_id = resolve_cli_model(cli_model="openai/gpt-4o:extended", model_registry=registry)

    assert provider_split.error is None
    assert provider_split.model is zai
    assert raw_openrouter_id.error is None
    assert raw_openrouter_id.model is OPENROUTER_GPT4O_EXTENDED


def test_resolve_cli_model_builds_custom_provider_model_and_strips_valid_thinking_suffix() -> None:
    base = _model("neuralwatt", "some-base-model")
    registry = Registry([*ALL_MODELS, base])

    result = resolve_cli_model(cli_model="neuralwatt/zai-org/GLM-5.1-FP8:high", model_registry=registry)

    assert result.error is None
    assert result.model is not None
    assert result.model.provider == "neuralwatt"
    assert result.model.id == "zai-org/GLM-5.1-FP8"
    assert result.model.name == "zai-org/GLM-5.1-FP8"
    assert result.model.reasoning is True
    assert result.thinking_level == "high"


def test_find_initial_model_uses_cli_scoped_saved_then_available_default_order() -> None:
    openrouter_default = _model("openrouter", "moonshotai/kimi-k2.6")
    fallback = _model("custom", "first")
    registry = Registry([fallback, openrouter_default], available=[fallback, openrouter_default])

    cli = find_initial_model(
        cli_provider="openrouter",
        cli_model="moonshotai/kimi-k2.6",
        scoped_models=[],
        is_continuing=False,
        model_registry=registry,
    )
    scoped = find_initial_model(
        scoped_models=[ScopedModel(model=fallback, thinking_level="medium")],
        is_continuing=False,
        default_thinking_level="high",
        model_registry=registry,
    )
    saved = find_initial_model(
        scoped_models=[ScopedModel(model=fallback, thinking_level="medium")],
        is_continuing=True,
        default_provider="openrouter",
        default_model_id="moonshotai/kimi-k2.6",
        default_thinking_level="high",
        model_registry=registry,
    )
    available_default = find_initial_model(scoped_models=[], is_continuing=False, model_registry=registry)

    assert cli.model is openrouter_default
    assert cli.thinking_level == "off"
    assert scoped.model is fallback
    assert scoped.thinking_level == "medium"
    assert saved.model is openrouter_default
    assert saved.thinking_level == "high"
    assert available_default.model is openrouter_default


def test_resolve_model_scope_matches_patterns_globs_thinking_and_dedupes(capsys) -> None:
    dated_sonnet = _model("anthropic", "claude-sonnet-4-5-20250929", name="Claude Sonnet dated", reasoning=True)
    registry = Registry([ANTHROPIC_SONNET, dated_sonnet, OPENAI_GPT4O, OPENROUTER_QWEN_EXACTO])

    scoped = resolve_model_scope(
        [
            "sonnet:high",
            "anthropic/*sonnet*:low",
            "openrouter/qwen/qwen3-coder:exacto:medium",
            "missing-model",
        ],
        registry,
    )

    assert [(item.model.provider, item.model.id, item.thinking_level) for item in scoped] == [
        ("anthropic", "claude-sonnet-4-5", "high"),
        ("anthropic", "claude-sonnet-4-5-20250929", "low"),
        ("openrouter", "qwen/qwen3-coder:exacto", "medium"),
    ]
    captured = capsys.readouterr()
    assert 'Warning: No models match pattern "missing-model"' in captured.err
