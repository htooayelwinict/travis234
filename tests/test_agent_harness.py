from __future__ import annotations

import asyncio
from pathlib import Path

from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.ai.types import AssistantMessage
from travis.coding_agent import AgentHarness, AgentHarnessConfig
from tests._provider_runtime import register_api_provider, reset_api_providers, reset_models


def setup_function() -> None:
    reset_api_providers()
    reset_models()


def test_agent_harness_composes_existing_owners_inside_async_context(tmp_path: Path) -> None:
    register_api_provider(
        create_faux_provider(lambda model, context: text_response_events(model, "harness answer"))
    )

    async def scenario() -> None:
        events: list[dict[str, object]] = []
        async with AgentHarness.create(
            AgentHarnessConfig(
                cwd=str(tmp_path),
                model=faux_model(),
                agent_dir=str(tmp_path / "agent"),
                persist_session=False,
                trust_override=False,
                offline=True,
            )
        ) as harness:
            unsubscribe = harness.subscribe(events.append)
            result = await harness.prompt("hello")
            unsubscribe()

            assert isinstance(result, AssistantMessage)
            assert result.stop_reason == "stop"
            assert harness.session.cwd == str(tmp_path.resolve())
            assert harness.resource_loader is harness.session.resource_loader
            assert harness.session.session_path is None
            assert [skill.name for skill in harness.list_skills()] == [
                "subagent-delegation",
                "web-search",
            ]
            assert any(event.get("type") == "message_end" for event in events)

        assert harness.closed is True
        await harness.close()

    asyncio.run(scenario())

def test_agent_harness_delegates_session_tree_clone_and_rename(tmp_path: Path) -> None:
    responses = iter(["first reply", "second reply"])
    register_api_provider(
        create_faux_provider(lambda model, context: text_response_events(model, next(responses)))
    )

    async def scenario() -> None:
        harness = AgentHarness.create(
            AgentHarnessConfig(
                cwd=str(tmp_path),
                model=faux_model(),
                agent_dir=str(tmp_path / "agent"),
                persist_session=True,
                trust_override=False,
            )
        )
        try:
            await harness.prompt("first")
            await harness.prompt("second")
            await harness.rename_session("SDK session")
            tree = harness.session_tree()
            source = Path(harness.session.session_path)
            source_bytes = source.read_bytes()

            result = await harness.clone_session()

            assert result == {"cancelled": False}
            assert harness.session.session_name == "SDK session"
            assert any(node["summary"] == "user: second" for node in tree)
            assert Path(harness.session.session_path) != source
            assert source.read_bytes() == source_bytes
        finally:
            await harness.close()

    asyncio.run(scenario())
