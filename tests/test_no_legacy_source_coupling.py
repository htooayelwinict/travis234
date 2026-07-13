from __future__ import annotations

import re
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]  # travis234/

_REMOVED_VERSION_TOKENS = ("app" + "v21", "app" + "V2.1")
_REMOVED_IMPORT_NAMES = ("p" + "i", "her" + "mes_agent")
_FORBIDDEN_IMPORTS = tuple(
    re.compile(rf"^\s*(?:from|import)\s+{re.escape(name)}(?:\b|\.)", re.MULTILINE)
    for name in _REMOVED_IMPORT_NAMES
)
_REMOVED_LEGACY_PATHS = (
    "travis/runtime",
    "travis/context",
    "travis/extensions",
    "travis/state",
    "travis/tools",
    "travis/prompts",
    "travis/providers",
    "travis_ui",
)


def test_no_removed_version_references_in_source() -> None:
    offenders: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path == Path(__file__):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(token in text for token in _REMOVED_VERSION_TOKENS):
            offenders.append(str(path.relative_to(APP_ROOT)))
    assert offenders == [], f"removed version references remain: {offenders}"


def test_no_removed_upstream_imports_in_source() -> None:
    offenders: list[str] = []
    for path in APP_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if path == Path(__file__):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if any(pattern.search(text) for pattern in _FORBIDDEN_IMPORTS):
            offenders.append(str(path.relative_to(APP_ROOT)))
    assert offenders == [], f"removed upstream imports remain: {offenders}"


def test_legacy_divergent_paths_removed() -> None:
    offenders = [path for path in _REMOVED_LEGACY_PATHS if (APP_ROOT / path).exists()]
    assert offenders == []


def test_new_ai_provider_returns_null_when_disabled(tmp_path: Path, monkeypatch) -> None:
    from travis.ai.providers.travis_env import create_travis_provider
    from travis.ai.types import Context, Model

    for key in ("TRAVIS234_WORKER_LLM_ENABLED", "TRAVIS234_WORKER_LLM_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("OPENROUTER_API_KEY=k\n", encoding="utf-8")  # not enabled
    provider = create_travis_provider(dotenv_path=str(env))
    model = Model(id="m", name="m", api="openai-completions", provider="openrouter", base_url="")
    stream = provider.stream(model, Context(messages=[]), None)
    message = stream.result_sync()
    assert message.stop_reason == "error"
    assert "not configured" in (message.error_message or "")
