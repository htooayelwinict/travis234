from __future__ import annotations

import re
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]  # appV2.3/

_TOKENS = ("appv21", "appV2.1")
_FORBIDDEN_IMPORTS = (
    re.compile(r"^\s*(?:from|import)\s+pi(?:\b|\.)", re.MULTILINE),
    re.compile(r"^\s*(?:from|import)\s+hermes_agent(?:\b|\.)", re.MULTILINE),
)
_REMOVED_LEGACY_PATHS = (
    "appv23/runtime",
    "appv23/context",
    "appv23/extensions",
    "appv23/state",
    "appv23/tools",
    "appv23/prompts",
    "appv23/providers",
    "appv23_ui",
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


def test_no_legacy_pi_or_hermes_imports_in_source() -> None:
    offenders: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path.name == "test_no_appv21_coupling.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in _FORBIDDEN_IMPORTS):
            offenders.append(str(path.relative_to(APP_ROOT)))
    assert offenders == [], f"legacy pi/hermes imports remain: {offenders}"


def test_legacy_divergent_paths_removed() -> None:
    offenders = [path for path in _REMOVED_LEGACY_PATHS if (APP_ROOT / path).exists()]
    assert offenders == []


def test_new_ai_provider_returns_null_when_disabled(tmp_path: Path, monkeypatch) -> None:
    from appv23.ai.providers.appv2_env import create_appv2_env_provider
    from appv23.ai.types import Context, Model

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
