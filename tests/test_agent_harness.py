from __future__ import annotations

import asyncio
from pathlib import Path
import threading

import travis.coding_agent.agent_harness as agent_harness_module
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
                "orchestration",
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


def test_agent_harness_listener_failure_does_not_stop_later_listener(tmp_path: Path) -> None:
    register_api_provider(
        create_faux_provider(lambda model, context: text_response_events(model, "harness answer"))
    )

    async def scenario() -> None:
        harness = AgentHarness.create(
            AgentHarnessConfig(
                cwd=str(tmp_path),
                model=faux_model(),
                agent_dir=str(tmp_path / "agent"),
                persist_session=False,
                trust_override=False,
                offline=True,
            )
        )
        later_events: list[dict[str, object]] = []

        def failing_listener(event: dict[str, object]) -> None:
            if event.get("type") == "message_end":
                raise RuntimeError("harness observer exploded")

        harness.subscribe(failing_listener)
        harness.subscribe(later_events.append)
        try:
            result = await harness.prompt("hello")
        finally:
            await harness.close()

        assert result.content[0].text == "harness answer"
        assert any(event.get("type") == "message_end" for event in later_events)

    asyncio.run(scenario())


def test_agent_harness_close_times_out_and_remains_retryable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(agent_harness_module, "_CLOSE_TIMEOUT_SECONDS", 0.05, raising=False)

    async def scenario() -> None:
        harness = AgentHarness.create(
            AgentHarnessConfig(
                cwd=str(tmp_path),
                model=faux_model(),
                agent_dir=str(tmp_path / "agent"),
                persist_session=False,
                trust_override=False,
                offline=True,
            )
        )
        owner_entered = threading.Event()
        release_owner = threading.Event()

        def blocking_owner() -> None:
            owner_entered.set()
            assert release_owner.wait(timeout=2)

        owner_task = asyncio.create_task(harness._run_owner(blocking_owner))
        assert await asyncio.to_thread(owner_entered.wait, 1)
        close_task = asyncio.create_task(harness.close())
        done, _pending = await asyncio.wait({close_task}, timeout=0.2)
        completed_with_own_deadline = close_task in done
        close_error = close_task.exception() if completed_with_own_deadline else None
        open_after_timeout = harness.closed is False

        release_owner.set()
        await owner_task
        if not close_task.done():
            await close_task
        elif isinstance(close_error, TimeoutError):
            await harness.close()

        assert completed_with_own_deadline is True
        assert isinstance(close_error, TimeoutError)
        assert open_after_timeout is True
        assert harness.closed is True

    asyncio.run(scenario())
