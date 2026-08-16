from __future__ import annotations

from pathlib import Path

from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.app import CodingApp
from travis.tui.terminal import FakeTerminal
from tests._provider_runtime import register_api_provider, reset_api_providers


def setup_function() -> None:
    reset_api_providers()


def test_tui_unrelated_turn_preserves_orchestration_lazy_discovery(tmp_path: Path) -> None:
    captured_prompts: list[str] = []

    def provider(model, context):
        captured_prompts.append(context.system_prompt or "")
        return text_response_events(model, "Ordinary TUI reply")

    register_api_provider(create_faux_provider(provider))
    agent_dir = tmp_path / "agent"
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        terminal=FakeTerminal(columns=100, rows=30),
        agent_dir=str(agent_dir),
        project_trust_override=False,
    )
    try:
        app.run_turn("Reply normally without loading any skill")

        assert len(captured_prompts) == 1
        prompt = captured_prompts[0]
        assert "<name>orchestration</name>" in prompt
        assert "Coordinate another durable Travis234 session" not in prompt
        assert "# Local Tmux Orchestration" not in prompt
        assert "Run the private relay" not in prompt
        assert not (agent_dir / "orchestration").exists()
        assert not (tmp_path / ".worktrees").exists()
        assert "Ordinary TUI reply" in "\n".join(app.tui.render(100))
    finally:
        app.close()
