from __future__ import annotations

import appv231.ai as ai
from appv231.ai.register_builtins import register_builtin_providers
from appv231.ai.stream import get_api_provider, reset_api_providers


def setup_function() -> None:
    reset_api_providers()


def test_register_builtins_registers_openai_completions(tmp_path) -> None:
    env = tmp_path / ".env"
    env.write_text("OPENROUTER_API_KEY=k\n", encoding="utf-8")
    register_builtin_providers(dotenv_path=str(env))
    provider = get_api_provider("openai-completions")
    assert provider.api == "openai-completions"


def test_barrel_reexports_public_surface() -> None:
    assert hasattr(ai, "AssistantMessage")
    assert hasattr(ai, "stream")
    assert hasattr(ai, "stream_simple")
    assert hasattr(ai, "create_assistant_message_event_stream")
    assert hasattr(ai, "is_context_overflow")
    assert hasattr(ai, "calculate_cost")
