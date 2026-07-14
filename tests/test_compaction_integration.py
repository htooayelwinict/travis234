from __future__ import annotations

import threading
from pathlib import Path

import pytest

from travis.ai.event_stream import create_assistant_message_event_stream
from travis.ai.providers.faux import faux_model
from travis.ai.providers.faux import text_response_events
from tests._provider_runtime import ApiProvider, register_api_provider
from travis.ai.types import (
    AssistantMessage,
    TextContent,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
    now_ms,
)
from travis.app import CodingApp
from travis.coding_agent import AgentSession
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.compaction_adapter import to_compressor_context, to_compressor_messages
from travis.coding_agent.extensions import ExtensionRunner
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.session_store import BashExecutionMessage, BranchSummaryMessage, CustomMessage
from travis.coding_agent.session_types import default_convert_to_llm
from travis.compaction import CompactionManager, ContextCompressor, estimate_tokens


def _large_messages(prefix: str, count: int = 12) -> list[UserMessage]:
    return [
        UserMessage(content=f"{prefix} message {index} " + ("x" * 80), timestamp=now_ms() + index)
        for index in range(count)
    ]


def test_compaction_converts_custom_session_messages_before_tokenizing_and_summarizing() -> None:
    messages = [
        BashExecutionMessage(
            command="python -c 'print(\"ROOT_CAUSE\")'",
            output="ROOT_CAUSE",
            exit_code=-9,
            cancelled=True,
            truncated=False,
            full_output_path=None,
            timestamp=now_ms(),
        ),
        CustomMessage(
            custom_type="live-check",
            content="EXTENSION_STATE:ready",
            display=True,
            details=None,
            timestamp=now_ms(),
        ),
        BranchSummaryMessage(
            summary="BRANCH_STATE:complete",
            from_id="branch-entry",
            timestamp=now_ms(),
        ),
        BashExecutionMessage(
            command="echo hidden",
            output="hidden",
            exit_code=0,
            cancelled=False,
            truncated=False,
            full_output_path=None,
            timestamp=now_ms(),
            exclude_from_context=True,
        ),
    ]

    context = to_compressor_context(messages)
    serialized = ContextCompressor()._serialize_for_summary(context.messages)

    assert context.source_indices == [0, 1, 2]
    assert [message.role for message in context.messages] == ["user", "user", "user"]
    assert estimate_tokens(context.messages) > 0
    assert "ROOT_CAUSE" in serialized
    assert "command cancelled" in serialized
    assert "EXTENSION_STATE:ready" in serialized
    assert "BRANCH_STATE:complete" in serialized
    assert "echo hidden" not in serialized


def test_durable_compaction_keeps_latest_causal_turn_together() -> None:
    messages = []
    for index in range(8):
        messages.extend(
            [
                UserMessage(content=f"old user {index} " + ("x" * 100), timestamp=now_ms()),
                AssistantMessage(
                    content=[TextContent(text=f"old assistant {index} " + ("y" * 100))],
                    api="faux",
                    provider="faux",
                    model="m",
                    usage=empty_usage(),
                    stop_reason="stop",
                    timestamp=now_ms(),
                ),
            ]
        )
    latest_user_index = len(messages)
    messages.extend(
        [
            UserMessage(content="LATEST CAUSAL REQUEST", timestamp=now_ms()),
            AssistantMessage(
                content=[ToolCall(id="latest-read", name="read", arguments={"path": "state.txt"})],
                api="faux",
                provider="faux",
                model="m",
                usage=empty_usage(),
                stop_reason="toolUse",
                timestamp=now_ms(),
            ),
            ToolResultMessage(
                tool_call_id="latest-read",
                tool_name="read",
                content=[TextContent(text="current state")],
                is_error=False,
                timestamp=now_ms(),
            ),
            AssistantMessage(
                content=[TextContent(text="LATEST CAUSAL REQUEST completed")],
                api="faux",
                provider="faux",
                model="m",
                usage=empty_usage(),
                stop_reason="stop",
                timestamp=now_ms(),
            ),
            CustomMessage(
                custom_type="live-check",
                content="EXTENSION_STATE:ready",
                display=True,
                details=None,
                timestamp=now_ms(),
            ),
        ]
    )
    compressor = ContextCompressor(
        context_length=100,
        protect_first_n=0,
        protect_last_n=3,
    )
    context = to_compressor_context(messages)

    result = compressor.compress(
        context.messages,
        summarizer=lambda _prompt: "## Goal\nOlder completed work",
        force=True,
        durable=True,
    )

    assert result.compressed is True
    assert context.source_indices[result.first_kept_message_index] == latest_user_index
    assert messages[latest_user_index].role == "user"


def test_compaction_strategy_is_owned_by_the_builtin_compaction_package() -> None:
    from travis.compaction import CompactionManager as BuiltinCompactionManager
    from travis.compaction import ContextCompressor as BuiltinContextCompressor

    assert CompactionManager is BuiltinCompactionManager
    assert ContextCompressor is BuiltinContextCompressor
    assert CompactionManager.__module__.startswith("travis.compaction")
    assert ContextCompressor.__module__.startswith("travis.compaction")


def test_runtime_has_no_compaction_extension_implementation() -> None:
    root = Path(__file__).resolve().parents[1] / "travis"
    violations: list[str] = []
    for path in root.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "travis.extensions.compaction" in source:
            violations.append(str(path.relative_to(root.parent)))

    assert violations == []
    assert not (root / "extensions" / "compaction" / "__init__.py").exists()


def test_provider_preflight_preserves_large_historical_tool_arguments_verbatim(tmp_path: Path) -> None:
    app = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        context_length=1_000_000,
        enable_tui=False,
        session_path=str(tmp_path / "provider-context.jsonl"),
    )
    large_replacement = "ORIGINAL-LARGE-EDIT\n" + ("x" * 2_000)
    messages = [
        UserMessage(content="apply the edit", timestamp=now_ms()),
        AssistantMessage(
            content=[
                ToolCall(
                    id="edit-large",
                    name="edit",
                    arguments={
                        "path": "src/example.py",
                        "oldText": "before",
                        "newText": large_replacement,
                    },
                )
            ],
            api="faux",
            provider="faux",
            model="faux",
            usage=empty_usage(),
            stop_reason="toolUse",
            timestamp=now_ms(),
        ),
        ToolResultMessage(
            tool_call_id="edit-large",
            tool_name="edit",
            content=[TextContent(text="Edit applied")],
            is_error=False,
            timestamp=now_ms(),
        ),
        *[
            UserMessage(content=f"later context {index}", timestamp=now_ms() + index)
            for index in range(24)
        ],
    ]

    try:
        transformed = app._transform_context(messages)
    finally:
        app.close()

    replayed_call = next(
        block
        for message in transformed
        if getattr(message, "role", None) == "assistant"
        for block in message.content
        if isinstance(block, ToolCall) and block.id == "edit-large"
    )
    assert replayed_call.arguments == {
        "path": "src/example.py",
        "oldText": "before",
        "newText": large_replacement,
    }
    assert isinstance(replayed_call.arguments["newText"], str)
    assert "_travis_omitted_tool_argument" not in repr(transformed)


def test_manual_compaction_emits_travis_before_hook_and_honors_cancel(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    before_events: list[dict[str, object]] = []

    def cancel(event: dict[str, object]) -> dict[str, bool]:
        before_events.append(event)
        return {"cancel": True}

    runner.on("session_before_compact", cancel)
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(tmp_path / "cancel-compaction.jsonl"),
        extension_runner=runner,
        compaction_manager=CompactionManager(
            ContextCompressor(context_length=40, protect_first_n=1, protect_last_n=1),
            summarizer=lambda _prompt: "should not run",
        ),
    )
    _append_messages(session, _large_messages("cancel"))
    lifecycle: list[object] = []
    session.subscribe(lifecycle.append)

    with pytest.raises(RuntimeError, match="Compaction cancelled"):
        session.compact(focus="preserve deployment state")

    assert len(before_events) == 1
    event = before_events[0]
    assert event["reason"] == "manual"
    assert event["willRetry"] is False
    assert event["customInstructions"] == "preserve deployment state"
    assert event["branchEntries"] == session.session_entries
    preparation = event["preparation"]
    assert preparation["tokensBefore"] > 0
    branch_ids = {entry["id"] for entry in session.session_entries if entry.get("id")}
    assert preparation["firstKeptEntryId"] in branch_ids
    assert 0 < len(preparation["messagesToSummarize"]) < len(session.messages)
    assert set(preparation["fileOps"]) == {"readFiles", "modifiedFiles"}
    assert event["signal"] is session.agent.signal
    assert session._compaction_manager.compressor.compression_count == 0  # noqa: SLF001
    assert [event.type for event in lifecycle] == ["compaction_start", "compaction_end"]
    assert lifecycle[-1].aborted is True


def test_manual_compaction_awaits_async_extension_hook(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    seen: list[str] = []

    async def cancel(event: dict[str, object]) -> dict[str, bool]:
        seen.append(str(event["reason"]))
        return {"cancel": True}

    runner.on("session_before_compact", cancel)
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        extension_runner=runner,
        compaction_manager=CompactionManager(
            ContextCompressor(context_length=40, protect_first_n=1, protect_last_n=1),
            summarizer=lambda _prompt: "should not run",
        ),
    )
    session.agent.state.messages.extend(_large_messages("async-cancel"))

    with pytest.raises(RuntimeError, match="Compaction cancelled"):
        session.compact()

    assert seen == ["manual"]


def test_manual_compaction_accepts_extension_summary_and_persists_post_event(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    summarizer_prompts: list[str] = []
    compact_events: list[dict[str, object]] = []

    def custom_compaction(event: dict[str, object]) -> dict[str, object]:
        preparation = event["preparation"]
        return {
            "compaction": {
                "summary": "## Goal\nretain the reviewed deployment plan",
                "firstKeptEntryId": preparation["firstKeptEntryId"],
                "tokensBefore": preparation["tokensBefore"],
                "details": {"owner": "first-party-test"},
            }
        }

    runner.on("session_before_compact", custom_compaction)
    runner.on("session_compact", compact_events.append)
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(tmp_path / "custom-compaction.jsonl"),
        extension_runner=runner,
        compaction_manager=CompactionManager(
            ContextCompressor(context_length=40, protect_first_n=1, protect_last_n=1),
            summarizer=lambda prompt: summarizer_prompts.append(prompt) or "wrong summary",
        ),
    )
    _append_messages(session, _large_messages("custom"))

    status = session.compact()

    assert summarizer_prompts == []
    assert status.compressed is True
    assert status.summary == "## Goal\nretain the reviewed deployment plan"
    assert session.messages[0].role == "compactionSummary"
    assert session.messages[0].summary == status.summary
    assert len(compact_events) == 1
    event = compact_events[0]
    assert event["fromExtension"] is True
    assert event["reason"] == "manual"
    assert event["willRetry"] is False
    assert event["compactionEntry"]["summary"] == status.summary


def test_builtin_dual_layer_compaction_emits_post_event_as_first_party_result(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    compact_events: list[dict[str, object]] = []
    runner.on("session_compact", compact_events.append)
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(tmp_path / "builtin-compaction.jsonl"),
        extension_runner=runner,
        compaction_manager=CompactionManager(
            ContextCompressor(context_length=40, protect_first_n=1, protect_last_n=1),
            summarizer=lambda _prompt: "## Goal\nbuilt-in dual-layer summary",
        ),
    )
    _append_messages(session, _large_messages("builtin"))

    status = session.compact()

    assert status.compressed is True
    assert len(compact_events) == 1
    assert compact_events[0]["fromExtension"] is False
    assert compact_events[0]["reason"] == "manual"
    assert compact_events[0]["willRetry"] is False
    assert compact_events[0]["compactionEntry"]["summary"] == status.summary


def test_threshold_compaction_hook_can_cancel_without_failing_the_turn(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    before_events: list[dict[str, object]] = []

    def cancel(event: dict[str, object]) -> dict[str, bool]:
        before_events.append(event)
        return {"cancel": True}

    runner.on("session_before_compact", cancel)
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(tmp_path / "cancel-auto-compaction.jsonl"),
        extension_runner=runner,
        compaction_manager=CompactionManager(
            ContextCompressor(context_length=40, protect_first_n=1, protect_last_n=1),
            summarizer=lambda _prompt: "should not run",
        ),
    )
    _append_messages(session, _large_messages("auto-cancel"))
    source = session.messages
    lifecycle: list[object] = []
    session.subscribe(lifecycle.append)

    outcome = session.compaction_transactions.preflight(source)

    assert outcome.compressed is False
    assert outcome.messages is source
    assert before_events[0]["reason"] == "threshold"
    assert before_events[0]["willRetry"] is False
    assert before_events[0]["customInstructions"] is None
    assert [event.type for event in lifecycle] == ["compaction_start", "compaction_end"]
    assert lifecycle[-1].aborted is True


def test_network_failed_auto_compaction_preserves_session_and_emits_aborted_end(tmp_path: Path) -> None:
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(tmp_path / "network-failed-compaction.jsonl"),
        compaction_manager=CompactionManager(
            ContextCompressor(context_length=40, protect_first_n=1, protect_last_n=1),
            summarizer=lambda _prompt: (_ for _ in ()).throw(TimeoutError("provider timed out")),
        ),
    )
    _append_messages(session, _large_messages("network-failure"))
    source = list(session.messages)
    branch_before = list(session.session_entries)
    lifecycle: list[object] = []
    session.subscribe(lifecycle.append)

    outcome = session.compaction_transactions.preflight(session.messages)

    assert outcome.compressed is False
    assert outcome.aborted is True
    assert session.messages == source
    assert session.session_entries == branch_before
    assert [event.type for event in lifecycle] == ["compaction_start", "compaction_end"]
    assert lifecycle[-1].aborted is True
    assert lifecycle[-1].result is not None
    assert not any(entry["type"] == "compaction" for entry in session.session_entries)


def test_threshold_compaction_accepts_extension_summary_and_emits_post_event(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    summarizer_prompts: list[str] = []
    compact_events: list[dict[str, object]] = []

    def custom(event: dict[str, object]) -> dict[str, object]:
        preparation = event["preparation"]
        return {
            "compaction": {
                "summary": "## Goal\nautomatic extension summary",
                "firstKeptEntryId": preparation["firstKeptEntryId"],
                "tokensBefore": preparation["tokensBefore"],
            }
        }

    runner.on("session_before_compact", custom)
    runner.on("session_compact", compact_events.append)
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(tmp_path / "custom-auto-compaction.jsonl"),
        extension_runner=runner,
        compaction_manager=CompactionManager(
            ContextCompressor(context_length=40, protect_first_n=1, protect_last_n=1),
            summarizer=lambda prompt: summarizer_prompts.append(prompt) or "wrong",
        ),
    )
    _append_messages(session, _large_messages("auto-custom"))

    outcome = session.compaction_transactions.preflight(session.messages)

    assert summarizer_prompts == []
    assert outcome.compressed is True
    assert session.messages[0].role == "compactionSummary"
    assert session.messages[0].summary == "## Goal\nautomatic extension summary"
    assert len(compact_events) == 1
    assert compact_events[0]["fromExtension"] is True
    assert compact_events[0]["reason"] == "threshold"
    assert compact_events[0]["willRetry"] is False


def test_threshold_builtin_compaction_emits_first_party_post_event(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    compact_events: list[dict[str, object]] = []
    runner.on("session_compact", compact_events.append)
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(tmp_path / "builtin-auto-compaction.jsonl"),
        extension_runner=runner,
        compaction_manager=CompactionManager(
            ContextCompressor(context_length=40, protect_first_n=1, protect_last_n=1),
            summarizer=lambda _prompt: "## Goal\nbuiltin automatic summary",
        ),
    )
    _append_messages(session, _large_messages("auto-builtin"))

    outcome = session.compaction_transactions.preflight(session.messages)

    assert outcome.compressed is True
    assert len(compact_events) == 1
    assert compact_events[0]["fromExtension"] is False
    assert compact_events[0]["reason"] == "threshold"
    assert compact_events[0]["willRetry"] is False


def test_post_response_threshold_hook_can_cancel_without_failing_the_turn(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    before_events: list[dict[str, object]] = []
    runner.on(
        "session_before_compact",
        lambda event: before_events.append(event) or {"cancel": True},
    )
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(tmp_path / "cancel-post-compaction.jsonl"),
        extension_runner=runner,
        compaction_manager=CompactionManager(
            ContextCompressor(context_length=100_000, protect_first_n=1, protect_last_n=1),
            summarizer=lambda _prompt: "should not run",
        ),
    )
    _append_messages(session, _large_messages("post-cancel"))
    source = session.messages
    lifecycle: list[object] = []
    session.subscribe(lifecycle.append)

    outcome = session.compaction_transactions.post_response(source, prompt_tokens=80_000)

    assert outcome.compressed is False
    assert outcome.messages is source
    assert before_events[0]["reason"] == "threshold"
    assert before_events[0]["willRetry"] is False
    assert [event.type for event in lifecycle] == ["compaction_start", "compaction_end"]
    assert lifecycle[-1].aborted is True


def test_overflow_compaction_accepts_extension_summary_and_retries_after_end_event(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    before_events: list[dict[str, object]] = []
    compact_events: list[dict[str, object]] = []
    sequence: list[str] = []

    def custom(event: dict[str, object]) -> dict[str, object]:
        before_events.append(event)
        preparation = event["preparation"]
        return {
            "compaction": {
                "summary": "## Goal\noverflow extension recovery",
                "firstKeptEntryId": preparation["firstKeptEntryId"],
                "tokensBefore": preparation["tokensBefore"],
            }
        }

    runner.on("session_before_compact", custom)
    runner.on("session_compact", lambda event: compact_events.append(event) or sequence.append("session_compact"))
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(tmp_path / "custom-overflow-compaction.jsonl"),
        extension_runner=runner,
        compaction_manager=CompactionManager(
            ContextCompressor(context_length=40, protect_first_n=1, protect_last_n=1),
            summarizer=lambda _prompt: "wrong",
        ),
    )
    _append_messages(session, _large_messages("overflow-custom"))
    session.subscribe(
        lambda event: sequence.append("compaction_end")
        if getattr(event, "type", None) == "compaction_end"
        else None
    )
    session.compaction_transactions._continue_agent = lambda **_kwargs: sequence.append("continue")  # noqa: SLF001

    outcome = session.compaction_transactions.recover_overflow(session.messages)

    assert outcome.recovered is True
    assert outcome.will_retry is True
    assert before_events[0]["reason"] == "overflow"
    assert before_events[0]["willRetry"] is True
    assert compact_events[0]["fromExtension"] is True
    assert compact_events[0]["reason"] == "overflow"
    assert compact_events[0]["willRetry"] is True
    assert sequence == ["session_compact", "compaction_end", "continue"]


def test_failed_turn_threshold_compaction_hook_can_cancel_without_crashing(tmp_path: Path) -> None:
    runner = ExtensionRunner()
    before_events: list[dict[str, object]] = []
    runner.on(
        "session_before_compact",
        lambda event: before_events.append(event) or {"cancel": True},
    )
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(tmp_path / "cancel-error-compaction.jsonl"),
        extension_runner=runner,
        compaction_manager=CompactionManager(
            ContextCompressor(context_length=40, protect_first_n=1, protect_last_n=1),
            summarizer=lambda _prompt: "should not run",
        ),
    )
    _append_messages(session, _large_messages("failed-turn-cancel"))

    outcome = session.compaction_transactions.compact_error_context(
        session.messages,
    )

    assert outcome.compressed is False
    assert before_events[0]["reason"] == "threshold"
    assert before_events[0]["willRetry"] is False


def _session_with_compaction(path: Path, prompts: list[str]) -> AgentSession:
    def summarize(prompt: str) -> str:
        prompts.append(prompt)
        return f"summary-{len(prompts)}"

    return AgentSession(
        cwd=str(path.parent),
        model=faux_model(),
        session_path=str(path),
        compaction_manager=CompactionManager(
            ContextCompressor(context_length=40, protect_first_n=1, protect_last_n=1),
            summarizer=summarize,
        ),
    )


def _append_messages(session: AgentSession, messages: list[UserMessage]) -> None:
    session.agent.state.messages.extend(messages)
    assert session._session_store is not None
    for message in messages:
        session._session_store.append_message(message)


def test_persisted_compaction_summarizes_every_message_it_discards(tmp_path: Path) -> None:
    prompts: list[str] = []
    session_path = tmp_path / "durable-head.jsonl"
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(session_path),
        compaction_manager=CompactionManager(
            ContextCompressor(
                context_length=500,
                protect_first_n=1,
                protect_last_n=2,
            ),
            summarizer=lambda prompt: prompts.append(prompt) or "## Goal\ndurable checkpoint",
        ),
    )
    _append_messages(
        session,
        [
            UserMessage(
                content="EARLY-DURABLE-FACT: /office-probe reloaded successfully",
                timestamp=now_ms(),
            ),
            *_large_messages("later", count=20),
        ],
    )

    status = session.compact()

    assert status.compressed is True
    assert len(prompts) == 1
    assert "EARLY-DURABLE-FACT" in prompts[0]


def test_persisted_compaction_does_not_resurrect_pruned_suffix(tmp_path: Path) -> None:
    session_path = tmp_path / "durable-suffix.jsonl"
    manager = CompactionManager(
        ContextCompressor(
            context_length=2_000,
            protect_first_n=1,
            protect_last_n=2,
        ),
        summarizer=lambda _prompt: "## Goal\ndurable checkpoint",
    )
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(session_path),
        compaction_manager=manager,
    )
    messages = [UserMessage(content="durable goal", timestamp=now_ms())]
    repeated_output = "REPEATED-TOOL-OUTPUT " * 500
    for index in range(8):
        call_id = f"read-{index}"
        messages.extend(
            [
                AssistantMessage(
                    content=[ToolCall(id=call_id, name="read", arguments={"path": f"file-{index}.txt"})],
                    api="faux",
                    provider="faux",
                    model="m",
                    usage=empty_usage(),
                    stop_reason="toolUse",
                    timestamp=now_ms() + index,
                ),
                ToolResultMessage(
                    tool_call_id=call_id,
                    tool_name="read",
                    content=[TextContent(text=repeated_output)],
                    is_error=False,
                    timestamp=now_ms() + index,
                ),
                UserMessage(content=f"continue {index}", timestamp=now_ms() + index),
            ]
        )
    messages.append(UserMessage(content="latest request", timestamp=now_ms() + 100))
    _append_messages(session, messages)

    status = session.compact()

    assert status.compressed is True
    expected_provider_tokens = manager.compression_ledger[-1].tokens_after
    restored_tokens = estimate_tokens(to_compressor_messages(session.messages))
    assert restored_tokens == expected_provider_tokens

    resumed = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(session_path),
        compaction_manager=CompactionManager(ContextCompressor(context_length=2_000)),
    )
    assert estimate_tokens(to_compressor_messages(resumed.messages)) == expected_provider_tokens


def test_persisted_compaction_header_does_not_resurrect_failed_turn_context(tmp_path: Path) -> None:
    session_path = tmp_path / "failed-turn-boundary.jsonl"
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(session_path),
        compaction_manager=CompactionManager(
            ContextCompressor(context_length=2_000, protect_first_n=1, protect_last_n=2),
            summarizer=lambda _prompt: "## Goal\nretain completed work only",
        ),
    )
    messages = []
    for index in range(6):
        messages.extend(
            [
                UserMessage(content=f"completed request {index} " + ("x" * 120), timestamp=now_ms() + index),
                AssistantMessage(
                    content=[TextContent(text=f"completed response {index}")],
                    api="faux",
                    provider="faux",
                    model="m",
                    usage=empty_usage(),
                    stop_reason="stop",
                    timestamp=now_ms() + index,
                ),
            ]
        )
    messages.extend(
        [
            UserMessage(content="FAILED-PARSER-PROMPT", timestamp=now_ms() + 100),
            AssistantMessage(
                content=[
                    TextContent(text="POISONED-CONTRADICTION-CLAIM"),
                    ToolCall(id="failed-edit", name="edit", arguments={"path": "parser.py"}),
                ],
                api="faux",
                provider="faux",
                model="m",
                usage=empty_usage(),
                stop_reason="toolUse",
                timestamp=now_ms() + 101,
            ),
            ToolResultMessage(
                tool_call_id="failed-edit",
                tool_name="edit",
                content=[TextContent(text="failed turn edited parser.py")],
                is_error=False,
                timestamp=now_ms() + 102,
            ),
            AssistantMessage(
                content=[],
                api="faux",
                provider="faux",
                model="m",
                usage=empty_usage(),
                stop_reason="error",
                timestamp=now_ms() + 103,
            ),
            AssistantMessage(
                content=[TextContent(text="POISONED-CONTRADICTION-CLAIM")],
                api="faux",
                provider="faux",
                model="m",
                usage=empty_usage(),
                stop_reason="aborted",
                timestamp=now_ms() + 104,
            ),
        ]
    )
    for index in range(6, 24):
        messages.extend(
            [
                UserMessage(content=f"completed request {index} " + ("y" * 120), timestamp=now_ms() + 200 + index),
                AssistantMessage(
                    content=[TextContent(text=f"completed response {index}")],
                    api="faux",
                    provider="faux",
                    model="m",
                    usage=empty_usage(),
                    stop_reason="stop",
                    timestamp=now_ms() + 200 + index,
                ),
            ]
        )
    _append_messages(session, messages)

    status = session.compact()

    assert status.compressed is True
    assert session.messages[0].role == "compactionSummary"
    assert status.first_kept_entry_id
    first_kept_entry = next(
        entry for entry in session.session_entries if entry.get("id") == status.first_kept_entry_id
    )
    assert "FAILED-PARSER-PROMPT" not in repr(first_kept_entry)
    assert "POISONED-CONTRADICTION-CLAIM" not in repr(first_kept_entry)

    resumed = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        session_path=str(session_path),
        compaction_manager=CompactionManager(ContextCompressor(context_length=2_000)),
    )
    next_prompt = UserMessage(content="NEW-ACTIVE-TASK", timestamp=now_ms() + 200)
    provider_context = default_convert_to_llm([*resumed.messages, next_prompt])
    rendered = repr(provider_context)
    assert "NEW-ACTIVE-TASK" in rendered
    assert "CONTEXT COMPACTION — REFERENCE ONLY" in rendered
    assert "Respond ONLY to the latest user message" in rendered
    assert rendered.index("END OF CONTEXT SUMMARY") < rendered.index("NEW-ACTIVE-TASK")
    assert "POISONED-CONTRADICTION-CLAIM" not in rendered
    assert "FAILED-PARSER-PROMPT" not in rendered


def test_auxiliary_compaction_crosses_pi_session_boundary_without_failed_turn_poison(
    tmp_path: Path,
) -> None:
    session_path = tmp_path / "auxiliary-cross-boundary.jsonl"
    provider_contexts: list[object] = []
    routed_models: list[str] = []

    def stream(model, context, options=None):
        del options
        routed_models.append(f"{model.provider}/{model.id}")
        if model.provider == "summary-provider":
            text = "## Goal\nretain only completed work\n## Remaining Work\ncontinue latest clean task"
        else:
            provider_contexts.append(context)
            text = "post-compaction continuity okay"
        result = create_assistant_message_event_stream()
        for event in text_response_events(model, text):
            result.push(event)
        return result

    provider = ApiProvider(api="capturing", stream=stream, stream_simple=stream)
    model_registry = ModelRegistry.in_memory(AuthStorage.in_memory())
    register_api_provider(provider)
    main_model = faux_model()
    main_model.api = "capturing"
    main_model.provider = "main-provider"
    main_model.id = "coding-model"
    compression_model = faux_model()
    compression_model.api = "capturing"
    compression_model.provider = "summary-provider"
    compression_model.id = "summary-model"
    first = CodingApp(
        cwd=str(tmp_path),
        model=main_model,
        context_length=2_000,
        enable_tui=False,
        session_path=str(session_path),
        model_registry=model_registry,
        compression_model=compression_model,
        compression_api_key="summary-test-key",
    )
    messages = _large_messages("completed-before", count=18)
    messages.extend(
        [
            UserMessage(content="FAILED-PARSER-PROMPT", timestamp=now_ms() + 100),
            AssistantMessage(
                content=[
                    TextContent(text="POISONED-CONTRADICTION-CLAIM"),
                    ToolCall(id="failed-read", name="read", arguments={"path": "parser.py"}),
                ],
                api="capturing",
                provider="main-provider",
                model="coding-model",
                usage=empty_usage(),
                stop_reason="toolUse",
                timestamp=now_ms() + 101,
            ),
            ToolResultMessage(
                tool_call_id="failed-read",
                tool_name="read",
                content=[TextContent(text="poisoned failed turn output")],
                is_error=False,
                timestamp=now_ms() + 102,
            ),
            AssistantMessage(
                content=[],
                api="capturing",
                provider="main-provider",
                model="coding-model",
                usage=empty_usage(),
                stop_reason="error",
                timestamp=now_ms() + 103,
            ),
            AssistantMessage(
                content=[TextContent(text="POISONED-CONTRADICTION-CLAIM")],
                api="capturing",
                provider="main-provider",
                model="coding-model",
                usage=empty_usage(),
                stop_reason="aborted",
                timestamp=now_ms() + 104,
            ),
        ]
    )
    messages.extend(_large_messages("completed-after", count=24))
    _append_messages(first.session, messages)

    status = first.session.compact()

    assert status.compressed is True
    assert routed_models == ["summary-provider/summary-model"]
    first.close()

    resumed = CodingApp(
        cwd=str(tmp_path),
        model=main_model,
        context_length=2_000,
        enable_tui=False,
        session_path=str(session_path),
        model_registry=model_registry,
        compression_model=compression_model,
        compression_api_key="summary-test-key",
    )
    resumed.run_turn("NEW-ACTIVE-TASK")

    assert routed_models[-1] == "main-provider/coding-model"
    rendered = repr(provider_contexts[-1])
    assert "NEW-ACTIVE-TASK" in rendered
    assert "retain only completed work" in rendered
    assert "FAILED-PARSER-PROMPT" not in rendered
    assert "POISONED-CONTRADICTION-CLAIM" not in rendered
    resumed.close()


def test_second_persisted_compaction_receives_previous_summary(tmp_path: Path) -> None:
    prompts: list[str] = []
    session_path = tmp_path / "session.jsonl"
    first = _session_with_compaction(session_path, prompts)
    _append_messages(first, _large_messages("first"))

    first_status = first.compact()
    assert first_status.compressed is True
    assert first.messages[0].role == "compactionSummary"

    reloaded = _session_with_compaction(session_path, prompts)
    assert reloaded.messages[0].role == "compactionSummary"
    _append_messages(reloaded, _large_messages("second"))

    second_status = reloaded.compact()

    assert second_status.compressed is True
    assert len(prompts) == 2
    assert "summary-1" in prompts[1]


def test_automatic_preflight_compaction_receives_persisted_summary(tmp_path: Path) -> None:
    prompts: list[str] = []
    session_path = tmp_path / "auto-session.jsonl"

    def summarize(prompt: str) -> str:
        prompts.append(prompt)
        return f"auto-summary-{len(prompts)}"

    first = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        context_length=40,
        summarizer=summarize,
        enable_tui=False,
        session_path=str(session_path),
    )
    _append_messages(first.session, _large_messages("first-auto"))
    first._transform_context(first.session.messages)
    assert first.session.messages[0].role == "compactionSummary"

    reloaded = CodingApp(
        cwd=str(tmp_path),
        model=faux_model(),
        context_length=40,
        summarizer=summarize,
        enable_tui=False,
        session_path=str(session_path),
    )
    _append_messages(reloaded.session, _large_messages("second-auto"))
    reloaded._transform_context(reloaded.session.messages)

    assert len(prompts) == 2
    assert "auto-summary-1" in prompts[1]


def test_manual_compaction_aborts_and_waits_for_active_turn(tmp_path: Path) -> None:
    session = _session_with_compaction(tmp_path / "active.jsonl", [])
    _append_messages(session, _large_messages("seed"))
    stream_started = threading.Event()
    release_stream = threading.Event()

    def blocking_stream(model, context, options):
        stream = create_assistant_message_event_stream()
        events = text_response_events(model, "done")
        stream.push(type(events[0])(partial=events[0].partial))
        stream_started.set()

        def finish() -> None:
            release_stream.wait(timeout=2)
            for event in events[1:]:
                stream.push(event)

        threading.Thread(target=finish, daemon=True).start()
        return stream

    turn = threading.Thread(target=lambda: session.prompt("active", stream_fn=blocking_stream))
    turn.start()
    assert stream_started.wait(timeout=2)

    compact_error: list[BaseException] = []

    def compact() -> None:
        try:
            session.compact()
        except BaseException as error:  # noqa: BLE001
            compact_error.append(error)

    compaction = threading.Thread(target=compact)
    compaction.start()
    compaction.join(timeout=2)
    release_stream.set()
    turn.join(timeout=2)

    assert session.agent.signal.aborted is True
    assert not compaction.is_alive()
    assert not turn.is_alive()
    assert compact_error == []
