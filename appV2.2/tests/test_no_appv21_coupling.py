from __future__ import annotations

from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]  # appV2.2/

_TOKENS = ("appv21", "appV2.1")
_REMOVED_LEGACY_PATHS = (
    "appv22/runtime",
    "appv22/context",
    "appv22/extensions",
    "appv22/state",
    "appv22/tools",
    "appv22/prompts",
    "appv22/providers",
    "appv22_ui",
)


def test_no_appv21_references_in_source() -> None:
    offenders: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.name == "test_no_appv21_coupling.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(token in text for token in _TOKENS):
            offenders.append(str(path.relative_to(APP_ROOT)))
    assert offenders == [], f"appv21 references remain: {offenders}"


def test_legacy_divergent_paths_removed() -> None:
    offenders = [path for path in _REMOVED_LEGACY_PATHS if (APP_ROOT / path).exists()]
    assert offenders == []


def test_new_ai_provider_returns_null_when_disabled(tmp_path: Path, monkeypatch) -> None:
    from appv22.ai.providers.appv2_env import create_appv2_env_provider
    from appv22.ai.types import Context, Model

    for key in ("APPV2_WORKER_LLM_ENABLED", "APPV2_WORKER_LLM_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("OPENROUTER_API_KEY=k\n", encoding="utf-8")  # not enabled
    provider = create_appv2_env_provider(dotenv_path=str(env))
    model = Model(id="m", name="m", api="openai-completions", provider="openrouter", base_url="")
    stream = provider.stream(model, Context(messages=[]), None)
    message = stream.result_sync()
    assert message.stop_reason == "error"
    assert "not configured" in (message.error_message or "")
