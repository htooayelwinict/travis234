from __future__ import annotations

import base64
import json
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import httpx

from travis.ai.builtin_models import load_builtin_models
from travis.ai.env_config import ModelConfig
from tests._provider_runtime import register_model, reset_models
from travis.cli import _env_model_from_config
from travis.ai.types import (
    AssistantMessage,
    Context,
    ImageContent,
    Model,
    SimpleStreamOptions,
    TextContent,
    ThinkingContent,
    Tool,
    ToolCall,
    ToolResultMessage,
    UserMessage,
    empty_usage,
)
from travis.ai.providers.message_translation import convert_messages
from travis.ai.providers.base import ProviderProfile
from travis.ai.providers.catalog import get_provider_profile
from travis.ai.providers.faux import create_faux_provider, faux_model, text_response_events
from travis.ai.providers.provider_request import PreparedProviderRequest, prepare_provider_request
from travis.ai.providers import travis_env as travis_env_module
from travis.ai.providers import codex_runtime as codex_runtime_module
from travis.ai.providers.travis_env import TravisProvider, _authorize_google_vertex_request
from travis.ai.providers.chat_stream import parse_sse_chunks
from travis.ai.providers.bedrock_stream import _parse_bedrock_events
from travis.ai.providers.responses_stream import decode_responses_stream
from travis.ai.providers.provider_errors import _format_provider_exception
from travis.ai.providers.transports import (
    AzureOpenAIResponsesTransport,
    BedrockConverseStreamTransport,
    AnthropicMessagesTransport,
    ChatCompletionsTransport,
    CodexResponsesTransport,
    GoogleGenerativeAITransport,
    GoogleVertexTransport,
    MistralConversationsTransport,
    OpenAIResponsesTransport,
    UnsupportedTransport,
    get_transport,
)
from travis.ai.context_estimate import (
    estimate_context_tokens,
    estimate_message_tokens,
    estimate_text_tokens,
)
from travis.compaction.compressor import ContextCompressor
from travis.coding_agent.extensions import ExtensionRunner
from travis.coding_agent.agent_session import AgentSession
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.model_registry import _merge_compat
from travis.coding_agent.system_prompt import BuildSystemPromptOptions, build_system_prompt


def _openrouter_qwen_model() -> Model:
    return Model(
        id="qwen/qwen3-coder-next",
        name="Qwen: Qwen3 Coder Next",
        api="openai-completions",
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        reasoning=False,
        input=["text"],
        context_window=262_144,
        max_tokens=262_144,
        compat={
            "supportsDeveloperRole": False,
            "thinkingFormat": "openrouter",
        },
    )


def test_openrouter_qwen_replay_uses_canonical_chat_completion_shape() -> None:
    model = _openrouter_qwen_model()
    assistant = AssistantMessage(
        content=[ToolCall(id="call_1", name="read", arguments={"path": "README.md"})],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="toolUse",
    )
    result = ToolResultMessage(
        tool_call_id="call_1",
        tool_name="read",
        content=[TextContent(text="contents")],
        is_error=False,
    )

    messages, tools = convert_messages(
        Context(
            system_prompt="system",
            messages=[UserMessage(content="read it"), assistant, result],
            tools=[
                Tool(
                    name="read",
                    description="Read a file",
                    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
                )
            ],
        ),
        model,
    )

    assert messages[0] == {"role": "system", "content": "system"}
    assert messages[2]["content"] is None
    assert messages[3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "contents",
    }
    assert tools is not None
    assert tools[0]["function"]["strict"] is False


def test_failed_provider_responses_are_omitted_without_mutating_session_history() -> None:
    model = _openrouter_qwen_model()
    failed = AssistantMessage(
        content=[TextContent(text="partial response that must not be replayed")],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="error",
        error_message="private TLS failure details",
    )
    aborted = replace(
        failed,
        content=[TextContent(text="partial interrupted response")],
        stop_reason="aborted",
        error_message="Operation aborted",
    )

    messages, _ = convert_messages(
        Context(
            messages=[
                UserMessage(content="old failed task"),
                failed,
                UserMessage(content="old interrupted task"),
                aborted,
                UserMessage(content="new active task"),
            ]
        ),
        model,
    )

    assert messages == [{"role": "user", "content": "new active task"}]
    assert failed.stop_reason == "error"
    assert failed.error_message == "private TLS failure details"
    assert failed.content[0].text == "partial response that must not be replayed"


def test_failed_provider_response_keeps_completed_tool_work_in_replay() -> None:
    model = _openrouter_qwen_model()
    tool_call = AssistantMessage(
        content=[ToolCall(id="call_1", name="edit", arguments={"path": "invoice.py"})],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="toolUse",
    )
    tool_result = ToolResultMessage(
        tool_call_id="call_1",
        tool_name="edit",
        content=[TextContent(text="edited invoice.py")],
        is_error=False,
    )
    aborted = AssistantMessage(
        content=[TextContent(text="partial interrupted response")],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="aborted",
        error_message="Operation aborted",
    )

    messages, _ = convert_messages(
        Context(
            messages=[
                UserMessage(content="edit the invoice validator"),
                tool_call,
                tool_result,
                aborted,
                UserMessage(content="new active task"),
            ]
        ),
        model,
    )

    assert [message["role"] for message in messages] == ["user", "assistant", "tool", "user"]
    assert messages[0]["content"] == "edit the invoice validator"
    assert messages[2]["content"] == "edited invoice.py"
    assert messages[3]["content"] == "new active task"


def test_sub_512k_context_compaction_uses_hermes_threshold_band() -> None:
    compressor = ContextCompressor(context_length=262_144, threshold_percent=0.50)

    assert compressor.threshold_percent == 0.75
    assert compressor.threshold_tokens == 196_608
    assert compressor.should_compress(60_000) is False


def test_32k_context_uses_reachable_fallback_after_output_reservation() -> None:
    compressor = ContextCompressor(
        context_length=32_000,
        threshold_percent=0.50,
        max_tokens=4_096,
    )

    assert compressor.threshold_tokens == int((32_000 - 4_096) * 0.85)
    assert compressor.should_compress(compressor.threshold_tokens - 1) is False
    assert compressor.should_compress(compressor.threshold_tokens) is True


def test_64k_context_uses_reachable_fallback_when_floor_reaches_window() -> None:
    compressor = ContextCompressor(context_length=64_000, threshold_percent=0.50)

    assert compressor.threshold_tokens == int(64_000 * 0.85)
    assert compressor.should_compress(compressor.threshold_tokens - 1) is False
    assert compressor.should_compress(compressor.threshold_tokens) is True


def test_context_estimate_counts_tools_loaded_after_last_provider_usage() -> None:
    usage = empty_usage()
    usage.input = 100
    usage.total_tokens = 100
    assistant = AssistantMessage(
        content=[TextContent(text="done")],
        api="openai-responses",
        provider="openai",
        model="gpt-5.4",
        usage=usage,
        stop_reason="toolUse",
        timestamp=100,
    )
    loaded_tool = Tool(
        name="loaded",
        description="loaded after tool search",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
    )
    original_tool = Tool(name="tool_search", description="search", parameters={"type": "object"})
    result = ToolResultMessage(
        tool_call_id="call-1",
        tool_name="tool_search",
        content=[TextContent(text="loaded")],
        is_error=False,
        added_tool_names=["loaded"],
        timestamp=200,
    )
    context = Context(messages=[assistant, result], tools=[original_tool, loaded_tool])

    estimate = estimate_context_tokens(context)

    added_tool_tokens = estimate_text_tokens(
        json.dumps([loaded_tool], default=lambda item: item.__dict__, separators=(",", ":"))
    )
    assert estimate.tokens == 100 + estimate_message_tokens(result) + added_tool_tokens
    assert estimate.trailing_tokens == estimate_message_tokens(result) + added_tool_tokens


def test_runtime_has_no_model_steering_policy_modules() -> None:
    policy_dir = Path(__file__).parents[1] / "travis" / "coding_agent" / "policies"

    assert not (policy_dir / "tool_guardrails.py").exists()
    assert not (policy_dir / "bash_classification.py").exists()
    assert not (policy_dir / "package_consent.py").exists()


def test_reference_oracles_are_present_but_outside_the_runtime_tree() -> None:
    root = Path(__file__).parents[1]

    assert (root / "pi").is_dir()
    assert (root / "hermes-agent").is_dir()
    assert (root / "appv231").is_dir()
    assert (root / "PI_HERMES_TRAVIS_CROSS_CHECK_REPORT.md").is_file()
    assert all(not path.is_relative_to(root / "travis") for path in (
        root / "pi",
        root / "hermes-agent",
        root / "appv231",
    ))


def test_travis_runtime_has_no_artificial_iteration_halt() -> None:
    root = Path(__file__).parents[1]
    loop_source = (root / "travis" / "agent" / "agent_loop.py").read_text(encoding="utf-8")
    agent_source = (root / "travis" / "agent" / "agent.py").read_text(encoding="utf-8")
    cli_source = (root / "travis" / "cli.py").read_text(encoding="utf-8")

    assert not (root / "travis" / "agent" / "iteration_budget.py").exists()
    assert not (root / "travis" / "coding_agent" / "policies" / "iteration_limit.py").exists()
    assert "max_iterations" not in loop_source + agent_source
    assert "on_iteration_limit" not in loop_source + agent_source
    assert "--max-iterations" not in cli_source


def test_app_has_no_provider_specific_prompt_guardrail_rewrite() -> None:
    source = (Path(__file__).parents[1] / "travis" / "app.py").read_text(encoding="utf-8")

    assert "_PROMPT_GUARDRAIL_ERROR_PATTERNS" not in source
    assert "_elide_failed_turn_tool_results" not in source


def test_default_system_prompt_does_not_embed_behavioral_recovery_policy() -> None:
    prompt = build_system_prompt(
        BuildSystemPromptOptions(
            cwd="/workspace",
            selected_tools=["read", "bash", "edit", "write"],
            tool_snippets={name: name for name in ("read", "bash", "edit", "write")},
        )
    )

    assert "Bounded autonomous workflow" not in prompt
    assert "Current request priority" not in prompt
    assert "Do not use bash heredocs" not in prompt


def test_env_model_selection_preserves_generated_catalog_contract() -> None:
    reset_models()
    qwen = next(model for model in load_builtin_models() if model.provider == "openrouter" and model.id == "qwen/qwen3-coder-next")
    register_model(qwen)
    config = ModelConfig(
        enabled=True,
        api_key=None,
        model=qwen.id,
        base_url=qwen.base_url,
        timeout_seconds=60,
        temperature=0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        provider="openrouter",
    )

    selected = _env_model_from_config(config)

    assert selected.api == "openai-completions"
    assert selected.context_window == 262_144
    assert selected.max_tokens == 262_144
    assert selected.compat == {"supportsDeveloperRole": False, "thinkingFormat": "openrouter"}


def test_openrouter_mimo_v25_uses_route_specific_context_capacity() -> None:
    mimo = next(
        model
        for model in load_builtin_models()
        if model.provider == "openrouter" and model.id == "xiaomi/mimo-v2.5"
    )

    assert mimo.context_window == 32_000
    assert mimo.max_tokens == 4_096

    compressor = ContextCompressor(
        context_length=mimo.context_window,
        threshold_percent=0.5,
        max_tokens=mimo.max_tokens,
    )
    assert compressor.threshold_tokens == int((32_000 - 4_096) * 0.85)


def test_compression_model_resolves_luna_pro_from_generated_catalog() -> None:
    reset_models()
    luna = next(
        model
        for model in load_builtin_models()
        if model.provider == "openrouter" and model.id == "openai/gpt-5.6-luna-pro"
    )
    register_model(luna)
    selected = _env_model_from_config(
        ModelConfig(
            enabled=True,
            api_key=None,
            model="openai/gpt-5.6-luna-pro",
            base_url="https://openrouter.ai/api/v1",
            timeout_seconds=60,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            provider="openrouter",
        )
    )

    assert selected.id == "openai/gpt-5.6-luna-pro"
    assert selected.context_window == 1_050_000
    assert selected.max_tokens == 128_000
    assert selected.reasoning is True


def test_provider_and_model_compat_are_deep_merged() -> None:
    merged = _merge_compat(
        {
            "supportsDeveloperRole": False,
            "openRouterRouting": {"only": ["A"], "sort": "latency"},
            "chatTemplateKwargs": {"enable_thinking": True},
        },
        {
            "supportsDeveloperRole": True,
            "openRouterRouting": {"sort": "price"},
            "chatTemplateKwargs": {"preserve_thinking": True},
        },
    )

    assert merged == {
        "supportsDeveloperRole": True,
        "openRouterRouting": {"only": ["A"], "sort": "price"},
        "chatTemplateKwargs": {"enable_thinking": True, "preserve_thinking": True},
    }


def test_chat_replay_preserves_tool_reasoning_details_and_empty_output_placeholder() -> None:
    model = _openrouter_qwen_model()
    assistant = AssistantMessage(
        content=[
            ToolCall(
                id="call_1",
                name="read",
                arguments={"path": "README.md"},
                thought_signature='{"type":"reasoning.trace","token":"opaque"}',
            )
        ],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="toolUse",
    )
    result = ToolResultMessage(
        tool_call_id="call_1",
        tool_name="read",
        content=[],
        is_error=False,
    )

    messages, _tools = convert_messages(Context(messages=[assistant, result]), model)

    assert messages[0]["reasoning_details"] == [{"type": "reasoning.trace", "token": "opaque"}]
    assert messages[1]["content"] == "(no tool output)"


def test_chat_cache_markers_cover_instruction_last_tool_and_last_conversation_message() -> None:
    model = replace(
        _openrouter_qwen_model(),
        id="anthropic/claude-test",
        compat={"cacheControlFormat": "anthropic", "thinkingFormat": "openrouter"},
    )
    context = Context(
        system_prompt="system",
        messages=[UserMessage(content="hello")],
        tools=[Tool(name="read", description="Read", parameters={"type": "object"})],
    )
    messages, tools = convert_messages(context, model)

    body = ChatCompletionsTransport().build_kwargs(
        model=model.id,
        messages=messages,
        tools=tools,
        profile=ProviderProfile(name="openrouter", base_url=model.base_url),
        stream=True,
        temperature=0,
        max_tokens=1_024,
        model_compat=model.compat,
        model_reasoning=model.reasoning,
        model_thinking_level_map=model.thinking_level_map,
        cache_retention="short",
    )

    assert body["messages"][0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert body["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"}


def test_model_compat_owns_reasoning_payload_shape() -> None:
    body = ChatCompletionsTransport().build_kwargs(
        model="reasoner",
        messages=[{"role": "user", "content": "work"}],
        tools=None,
        profile=ProviderProfile(name="openrouter", base_url="https://openrouter.ai/api/v1"),
        stream=True,
        temperature=0,
        max_tokens=2_048,
        reasoning_config={"enabled": True, "effort": "medium"},
        model_compat={"thinkingFormat": "openrouter"},
        model_reasoning=True,
        model_thinking_level_map={"medium": "high"},
        cache_retention="none",
    )

    assert body["reasoning"] == {"effort": "high"}


def test_openrouter_disabled_reasoning_defaults_to_none_for_sparse_thinking_map() -> None:
    body = ChatCompletionsTransport().build_kwargs(
        model="openai/gpt-5.6-luna-pro",
        messages=[{"role": "user", "content": "summarize"}],
        tools=None,
        profile=ProviderProfile(name="openrouter", base_url="https://openrouter.ai/api/v1"),
        stream=True,
        temperature=0,
        max_tokens=None,
        reasoning_config=None,
        model_compat={"thinkingFormat": "openrouter"},
        model_reasoning=True,
        model_thinking_level_map={"xhigh": "xhigh", "max": "max"},
        cache_retention="none",
    )

    assert body["reasoning"] == {"effort": "none"}


def test_tool_call_extension_failure_escapes_to_the_tool_runtime() -> None:
    runner = ExtensionRunner()

    def fail(_event):
        raise RuntimeError("extension failure")

    runner.on("tool_call", fail)

    with pytest.raises(RuntimeError, match="extension failure"):
        runner.emit_tool_call({"type": "tool_call", "toolName": "read", "input": {}})


def test_final_provider_headers_are_mutable_and_null_deletes_a_header() -> None:
    model = _openrouter_qwen_model()
    observed: dict[str, object] = {}

    def on_headers(headers, _model):
        observed.update(headers)
        headers["X-Trace"] = "trace-id"
        headers["X-Remove"] = None

    config = ModelConfig(
        enabled=True,
        api_key="test-key",
        model=model.id,
        base_url=model.base_url,
        timeout_seconds=60,
        temperature=0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        provider="openrouter",
    )
    request = prepare_provider_request(
        model,
        Context(messages=[UserMessage(content="hello")]),
        SimpleNamespace(headers={"X-Remove": "old"}, on_headers=on_headers),
        config,
        ProviderProfile(name="openrouter", base_url=model.base_url),
    )

    assert "Authorization" in observed
    assert request.headers["X-Trace"] == "trace-id"
    assert "X-Remove" not in request.headers


def test_runtime_timeout_option_controls_provider_request_deadline() -> None:
    model = _openrouter_qwen_model()
    config = ModelConfig(
        enabled=True,
        api_key="test-key",
        model=model.id,
        base_url=model.base_url,
        timeout_seconds=60,
        temperature=0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        provider="openrouter",
    )

    request = prepare_provider_request(
        model,
        Context(messages=[UserMessage(content="hello")]),
        SimpleNamespace(timeout_ms=250),
        config,
        ProviderProfile(name="openrouter", base_url=model.base_url),
    )

    assert request.timeout_seconds == 0.25


def test_summary_request_can_use_provider_native_output_ceiling_without_wire_cap() -> None:
    model = _openrouter_qwen_model()
    config = ModelConfig(
        enabled=True,
        api_key="test-key",
        model=model.id,
        base_url=model.base_url,
        timeout_seconds=60,
        temperature=0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        provider="openrouter",
    )

    request = prepare_provider_request(
        model,
        Context(messages=[UserMessage(content="summarize this context")]),
        SimpleStreamOptions(max_tokens=None, omit_max_tokens=True),
        config,
        ProviderProfile(name="openrouter", base_url=model.base_url),
    )

    assert "max_tokens" not in request.body
    assert "max_completion_tokens" not in request.body


@pytest.mark.parametrize(
    ("transport", "api", "provider", "container", "field"),
    [
        (ChatCompletionsTransport(), "openai-completions", "openrouter", None, "max_tokens"),
        (MistralConversationsTransport(), "mistral-conversations", "mistral", None, "max_tokens"),
        (GoogleGenerativeAITransport(), "google-generative-ai", "google", "generationConfig", "maxOutputTokens"),
        (GoogleVertexTransport(), "google-vertex", "google-vertex", "generationConfig", "maxOutputTokens"),
        (BedrockConverseStreamTransport(), "bedrock-converse-stream", "amazon-bedrock", "inferenceConfig", "maxTokens"),
        (CodexResponsesTransport(), "openai-codex-responses", "openai-codex", None, "max_output_tokens"),
        (OpenAIResponsesTransport(), "openai-responses", "openai", None, "max_output_tokens"),
        (AzureOpenAIResponsesTransport(), "azure-openai-responses", "azure-openai-responses", None, "max_output_tokens"),
    ],
)
def test_summary_wire_cap_is_omitted_across_optional_cap_transports(
    transport,
    api: str,
    provider: str,
    container: str | None,
    field: str,
) -> None:
    model = Model(
        id="summary-model",
        name="Summary",
        api=api,
        provider=provider,
        base_url="https://provider.invalid/v1",
        context_window=128_000,
        max_tokens=32_000,
    )
    context = Context(messages=[UserMessage(content="summarize")], system_prompt="summary policy")
    body = transport.build_kwargs(
        model=model.id,
        messages=[{"role": "system", "content": "summary policy"}, {"role": "user", "content": "summarize"}],
        tools=[],
        profile=ProviderProfile(name=provider, base_url=model.base_url, default_max_tokens=8_192),
        stream=True,
        temperature=None,
        max_tokens=None,
        omit_max_tokens=True,
        context=context,
        target_model=model,
        options=SimpleNamespace(),
    )

    target = body.get(container, {}) if container else body
    assert field not in target


def test_direct_session_emits_agent_settled_after_the_provider_run(tmp_path: Path) -> None:
    model = faux_model()
    events: list[str] = []
    runner = ExtensionRunner()
    runner.on("agent_settled", lambda event: events.append(event["type"]))
    provider = create_faux_provider(lambda active_model, _context: text_response_events(active_model, "done"))
    session = AgentSession(
        cwd=str(tmp_path),
        model=model,
        tools=[],
        extension_runner=runner,
    )

    session.prompt("work", stream_fn=provider.stream_simple)

    assert events == ["agent_settled"]


def test_model_is_selectable_through_its_owning_provider() -> None:
    registry = ModelRegistry.in_memory(AuthStorage.in_memory())
    model = _openrouter_qwen_model()
    registry.auth_storage.set_runtime_api_key(model.provider, "test-key")
    registry.register_model(model)

    assert registry.is_selectable(model) is True


def test_chat_prompt_cache_contract_uses_bounded_session_key() -> None:
    session_id = "s" * 80
    body = ChatCompletionsTransport().build_kwargs(
        model="reasoner",
        messages=[{"role": "user", "content": "work"}],
        tools=None,
        profile=ProviderProfile(name="openai", base_url="https://api.openai.com/v1"),
        stream=True,
        temperature=0,
        max_tokens=2_048,
        session_id=session_id,
        base_url="https://api.openai.com/v1",
        cache_retention="long",
    )

    assert body["prompt_cache_key"] == session_id[:64]
    assert body["prompt_cache_retention"] == "24h"


def test_provider_profile_cannot_inject_hidden_model_payload_fields() -> None:
    body = ChatCompletionsTransport().build_kwargs(
        model="qwen3-coder-next",
        messages=[{"role": "user", "content": "work"}],
        tools=None,
        profile=get_provider_profile("openrouter"),
        stream=True,
        temperature=0,
        max_tokens=2_048,
    )

    assert "vl_high_resolution_images" not in body


def test_explicit_openrouter_preferences_remain_request_data() -> None:
    body = ChatCompletionsTransport().build_kwargs(
        model="qwen/qwen3-coder-next",
        messages=[{"role": "user", "content": "work"}],
        tools=None,
        profile=get_provider_profile("openrouter"),
        stream=True,
        temperature=0,
        max_tokens=2_048,
        provider_preferences={"only": ["Together"]},
    )

    assert body["provider"] == {"only": ["Together"]}


def test_model_api_dispatches_to_mistral_transport() -> None:
    assert isinstance(get_transport("mistral-conversations"), MistralConversationsTransport)


def test_mistral_request_contract_includes_cache_affinity() -> None:
    body = MistralConversationsTransport().build_kwargs(
        model="codestral-latest",
        messages=[{"role": "user", "content": "work"}],
        tools=None,
        profile=ProviderProfile(name="mistral", base_url="https://api.mistral.ai/v1"),
        stream=True,
        temperature=0.2,
        max_tokens=4_096,
        session_id="session-1",
        cache_retention="short",
        reasoning_config={"enabled": True, "effort": "high"},
    )

    assert body == {
        "model": "codestral-latest",
        "stream": True,
        "messages": [{"role": "user", "content": "work"}],
        "temperature": 0.2,
        "max_tokens": 4_096,
        "reasoning_effort": "high",
        "prompt_cache_key": "session-1",
        "extra_headers": {"x-affinity": "session-1"},
    }


def test_mistral_native_contract_normalizes_tool_ids_and_uses_prompt_mode() -> None:
    model = Model(
        id="magistral-medium-latest",
        name="Magistral Medium",
        api="mistral-conversations",
        provider="mistral",
        base_url="https://api.mistral.ai/v1",
        reasoning=True,
        input=["text"],
        context_window=128_000,
        max_tokens=32_000,
    )
    original_id = "foreign|tool-call:id-that-is-too-long"
    assistant = AssistantMessage(
        content=[
            ThinkingContent(thinking="reason"),
            ToolCall(id=original_id, name="read", arguments={"path": "a.py"}),
        ],
        api="openai-responses",
        provider="openai",
        model="gpt-5",
        usage=empty_usage(),
        stop_reason="toolUse",
    )
    result = ToolResultMessage(
        tool_call_id=original_id,
        tool_name="read",
        content=[TextContent(text="ok")],
        is_error=False,
    )
    context = Context(messages=[UserMessage(content="work"), assistant, result])

    body = MistralConversationsTransport().build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=ProviderProfile(name="mistral"),
        stream=True,
        temperature=None,
        max_tokens=4096,
        reasoning_config={"enabled": True, "effort": "high"},
        context=context,
        target_model=model,
    )

    normalized_id = body["messages"][1]["tool_calls"][0]["id"]
    assert len(normalized_id) == 9
    assert normalized_id.isalnum()
    assert body["messages"][2]["tool_call_id"] == normalized_id
    assert body["messages"][1]["content"] == [{"type": "text", "text": "reason"}]
    assert body["prompt_mode"] == "reasoning"
    assert "reasoning_effort" not in body


def test_mistral_stream_decodes_typed_thinking_chunks() -> None:
    model = Model(
        id="magistral-medium-latest",
        name="Magistral Medium",
        api="mistral-conversations",
        provider="mistral",
        base_url="https://api.mistral.ai/v1",
        reasoning=True,
        input=["text"],
        context_window=128_000,
        max_tokens=32_000,
    )
    lines = [
        'data: {"id":"resp-1","choices":[{"delta":{"content":['
        '{"type":"thinking","thinking":[{"type":"text","text":"reason"}]},'
        '{"type":"text","text":"answer"}]},"finish_reason":"stop"}],'
        '"usage":{"prompt_tokens":10,"completion_tokens":4,"total_tokens":14,'
        '"num_cached_tokens":2}}',
    ]

    events = list(parse_sse_chunks(lines, model, api_mode="mistral_conversations"))
    message = events[-1].message

    assert [event.type for event in events] == [
        "start",
        "thinking_start",
        "thinking_delta",
        "thinking_end",
        "text_start",
        "text_delta",
        "text_end",
        "done",
    ]
    assert message.content[0] == ThinkingContent(thinking="reason")
    assert message.content[1] == TextContent(text="answer")
    assert message.usage.input == 8
    assert message.usage.cache_read == 2


def test_prepared_request_uses_model_api_not_provider_profile_mode() -> None:
    model = Model(
        id="codestral-latest",
        name="Codestral",
        api="mistral-conversations",
        provider="mistral",
        base_url="https://api.mistral.ai/v1",
        reasoning=False,
        input=["text"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=32_768,
        max_tokens=4_096,
    )
    config = ModelConfig(
        enabled=True,
        api_key="test-key",
        model=model.id,
        base_url=model.base_url,
        timeout_seconds=60,
        temperature=0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        provider="mistral",
    )

    request = prepare_provider_request(
        model,
        Context(messages=[UserMessage(content="hello")]),
        None,
        config,
        ProviderProfile(name="mistral", base_url=model.base_url),
    )

    assert request.api_mode == "mistral_conversations"
    assert request.url == "https://api.mistral.ai/v1/chat/completions"
    assert "stream_options" not in request.body


@pytest.mark.parametrize(
    ("api", "transport_type"),
    [
        ("openai-responses", OpenAIResponsesTransport),
        ("azure-openai-responses", AzureOpenAIResponsesTransport),
        ("openai-codex-responses", CodexResponsesTransport),
    ],
)
def test_responses_apis_have_distinct_transports(api, transport_type) -> None:
    assert isinstance(get_transport(api), transport_type)


def test_responses_request_shapes_are_not_conflated() -> None:
    common = dict(
        model="gpt-5",
        messages=[{"role": "system", "content": "policy"}, {"role": "user", "content": "work"}],
        tools=None,
        profile=ProviderProfile(name="openai", base_url="https://api.openai.com/v1"),
        stream=True,
        temperature=0,
        max_tokens=8,
        session_id="s" * 80,
        cache_retention="long",
    )

    openai = OpenAIResponsesTransport().build_kwargs(**common)
    azure = AzureOpenAIResponsesTransport().build_kwargs(**common)
    codex = CodexResponsesTransport().build_kwargs(**common)

    assert openai["max_output_tokens"] == 16
    assert openai["prompt_cache_key"] == "s" * 64
    assert openai["prompt_cache_retention"] == "24h"
    assert openai["input"][0] == {"role": "system", "content": "policy"}
    assert "prompt_cache_retention" not in azure
    assert azure["prompt_cache_key"] == "s" * 64
    assert codex["instructions"] == "policy"
    assert "max_output_tokens" not in codex
    assert "prompt_cache_retention" not in codex


def test_codex_prepared_request_uses_provider_endpoint_and_oauth_headers() -> None:
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "account-123",
                }
            }
        ).encode()
    ).decode().rstrip("=")
    token = f"header.{payload}.signature"
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == "openai-codex" and model.id == "gpt-5.4"
    )
    config = ModelConfig(
        enabled=True,
        api_key=token,
        model=model.id,
        base_url=model.base_url,
        timeout_seconds=60,
        temperature=0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        provider=model.provider,
    )

    request = prepare_provider_request(
        model,
        Context(messages=[UserMessage(content="hello")]),
        SimpleNamespace(session_id="session-123"),
        config,
        get_provider_profile("openai-codex"),
    )

    assert request.url == "https://chatgpt.com/backend-api/codex/responses"
    assert request.headers["Authorization"] == f"Bearer {token}"
    assert request.headers["chatgpt-account-id"] == "account-123"
    assert request.headers["originator"] == "travis234"
    assert request.headers["OpenAI-Beta"] == "responses=experimental"
    assert request.headers["accept"] == "text/event-stream"
    assert request.headers["session-id"] == "session-123"
    assert request.headers["x-client-request-id"] == "session-123"
    assert request.headers["User-Agent"].startswith("travis234 (")


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://chatgpt.com/backend-api", "https://chatgpt.com/backend-api/codex/responses"),
        ("https://chatgpt.com/backend-api/codex", "https://chatgpt.com/backend-api/codex/responses"),
        ("https://chatgpt.com/backend-api/codex/responses", "https://chatgpt.com/backend-api/codex/responses"),
    ],
)
def test_codex_url_resolution_matches_provider_contract(base_url, expected) -> None:
    assert CodexResponsesTransport.build_url(base_url, "gpt-5.4", None, None) == expected


def test_openai_responses_url_and_context_affinity_match_provider_contract() -> None:
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == "openai" and model.id == "gpt-4"
    )
    config = ModelConfig(
        enabled=True,
        api_key="test-key",
        model=model.id,
        base_url=model.base_url,
        timeout_seconds=60,
        temperature=0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        provider=model.provider,
    )

    request = prepare_provider_request(
        model,
        Context(messages=[UserMessage(content="hello")]),
        SimpleNamespace(session_id="session-123"),
        config,
        get_provider_profile("openai"),
    )

    assert request.url == "https://api.openai.com/v1/responses"
    assert request.headers["session_id"] == "session-123"
    assert request.headers["x-client-request-id"] == "session-123"


def test_openai_responses_openrouter_affinity_uses_x_session_id() -> None:
    model = replace(_openrouter_qwen_model(), api="openai-responses")
    body = OpenAIResponsesTransport().build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=ProviderProfile(name="openrouter", base_url=model.base_url),
        stream=True,
        temperature=None,
        max_tokens=1024,
        session_id="session-123",
        context=Context(messages=[UserMessage(content="hello")]),
        target_model=model,
        model_compat=model.compat,
    )

    assert body["extra_headers"] == {"x-session-id": "session-123"}


@pytest.mark.parametrize(
    ("api", "model_id"),
    [
        ("openai-completions", "gpt-4.1"),
        ("anthropic-messages", "claude-haiku-4.5"),
        ("openai-responses", "gpt-5-mini"),
    ],
)
def test_github_copilot_requests_include_dynamic_user_and_vision_headers(api, model_id) -> None:
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == "github-copilot" and model.api == api and model.id == model_id
    )
    config = ModelConfig(
        enabled=True,
        api_key="copilot-session-token",
        model=model.id,
        base_url=model.base_url,
        timeout_seconds=60,
        temperature=0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        provider=model.provider,
    )
    context = Context(
        messages=[
            UserMessage(
                content=[
                    TextContent(text="inspect this"),
                    ImageContent(data="aW1hZ2U=", mime_type="image/png"),
                ]
            )
        ]
    )

    request = prepare_provider_request(
        model,
        context,
        None,
        config,
        get_provider_profile("github-copilot"),
    )

    assert request.headers["X-Initiator"] == "user"
    assert request.headers["Openai-Intent"] == "conversation-edits"
    assert request.headers["Copilot-Vision-Request"] == "true"


def test_cloudflare_gateway_auth_resolves_scoped_url_and_header() -> None:
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == "cloudflare-ai-gateway" and model.api == "openai-completions"
    )
    config = ModelConfig(
        enabled=True,
        api_key=None,
        model=model.id,
        base_url=model.base_url,
        timeout_seconds=60,
        temperature=0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        provider=model.provider,
    )

    request = prepare_provider_request(
        model,
        Context(messages=[UserMessage(content="hello")]),
        SimpleNamespace(
            api_key="cloudflare-key",
            env={
                "CLOUDFLARE_ACCOUNT_ID": "account-1",
                "CLOUDFLARE_GATEWAY_ID": "gateway-1",
            },
        ),
        config,
        get_provider_profile(model.provider),
    )

    assert "account-1/gateway-1" in request.url
    assert request.headers["cf-aig-authorization"] == "Bearer cloudflare-key"
    assert "Authorization" not in request.headers
    assert "x-api-key" not in request.headers


def test_cloudflare_workers_auth_requires_account_id() -> None:
    model = next(
        model for model in load_builtin_models() if model.provider == "cloudflare-workers-ai"
    )
    config = ModelConfig(
        enabled=True,
        api_key=None,
        model=model.id,
        base_url=model.base_url,
        timeout_seconds=60,
        temperature=0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        provider=model.provider,
    )

    with pytest.raises(RuntimeError, match="CLOUDFLARE_ACCOUNT_ID"):
        prepare_provider_request(
            model,
            Context(messages=[UserMessage(content="hello")]),
            SimpleNamespace(api_key="cloudflare-key", env={}),
            config,
            get_provider_profile(model.provider),
        )


def test_github_copilot_agent_initiator_is_derived_from_the_last_native_message() -> None:
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == "github-copilot" and model.id == "gpt-5-mini"
    )
    config = ModelConfig(
        enabled=True,
        api_key="copilot-session-token",
        model=model.id,
        base_url=model.base_url,
        timeout_seconds=60,
        temperature=0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        provider=model.provider,
    )
    context = Context(
        messages=[
            UserMessage(content="read it"),
            ToolResultMessage(
                tool_call_id="call-1",
                tool_name="read",
                content=[TextContent(text="contents")],
                is_error=False,
            ),
        ]
    )

    request = prepare_provider_request(
        model,
        context,
        SimpleNamespace(headers={"X-Initiator": "explicit"}),
        config,
        get_provider_profile("github-copilot"),
    )

    assert request.headers["X-Initiator"] == "explicit"
    assert request.headers["Openai-Intent"] == "conversation-edits"
    assert "Copilot-Vision-Request" not in request.headers


def test_anthropic_affinity_header_is_generated_only_when_model_requests_it() -> None:
    body = AnthropicMessagesTransport().build_kwargs(
        model="claude-compatible",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        profile=ProviderProfile(name="custom-anthropic"),
        stream=True,
        temperature=None,
        max_tokens=1024,
        session_id="session-123",
        model_compat={"sendSessionAffinityHeaders": True},
        api_key="test-key",
    )

    assert body["extra_headers"]["x-session-affinity"] == "session-123"


def test_provider_specific_options_reach_each_wire_payload() -> None:
    codex = CodexResponsesTransport().build_kwargs(
        model="gpt-5.4",
        messages=[{"role": "system", "content": "policy"}],
        tools=[],
        profile=ProviderProfile(name="openai-codex"),
        stream=True,
        temperature=None,
        max_tokens=1024,
        reasoning_config={"enabled": True, "effort": "high"},
        reasoning_summary="detailed",
        service_tier="flex",
        text_verbosity="medium",
        tool_choice="required",
    )
    assert codex["reasoning"] == {"effort": "high", "summary": "detailed"}
    assert codex["service_tier"] == "flex"
    assert codex["text"] == {"verbosity": "medium"}
    assert codex["tool_choice"] == "required"

    anthropic = AnthropicMessagesTransport().build_kwargs(
        model="claude-compatible",
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        profile=ProviderProfile(name="custom-anthropic"),
        stream=True,
        temperature=None,
        max_tokens=1024,
        tool_choice={"type": "tool", "name": "read"},
        metadata={"user_id": "employee-123", "ignored": "value"},
    )
    assert anthropic["tool_choice"] == {"type": "tool", "name": "read"}
    assert anthropic["metadata"] == {"user_id": "employee-123"}

    gemini_model = next(
        model
        for model in load_builtin_models()
        if model.provider == "google" and model.id == "gemini-2.5-flash"
    )
    gemini_context = Context(
        messages=[UserMessage(content="hello")],
        tools=[Tool(name="read", description="Read", parameters={"type": "object"})],
    )
    google = GoogleGenerativeAITransport().build_kwargs(
        model=gemini_model.id,
        messages=[],
        tools=[],
        profile=ProviderProfile(name="google"),
        stream=True,
        temperature=None,
        max_tokens=1024,
        tool_choice="any",
        context=gemini_context,
        target_model=gemini_model,
    )
    assert google["toolConfig"] == {"functionCallingConfig": {"mode": "ANY"}}

    mistral = MistralConversationsTransport().build_kwargs(
        model="codestral-latest",
        messages=[{"role": "user", "content": "hello"}],
        tools=[{"type": "function", "function": {"name": "read", "parameters": {"type": "object"}}}],
        profile=ProviderProfile(name="mistral"),
        stream=True,
        temperature=None,
        max_tokens=1024,
        tool_choice="required",
    )
    assert mistral["tool_choice"] == "required"


def test_azure_responses_uses_provider_base_deployment_and_api_key_contract(monkeypatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT_NAME_MAP", "gpt-5.4=corp-gpt-54")
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == "azure-openai-responses" and model.id == "gpt-5.4"
    )
    configured_model = replace(
        model,
        base_url="https://corp-resource.cognitiveservices.azure.com/openai/v1/responses",
    )
    config = ModelConfig(
        enabled=True,
        api_key="azure-key",
        model=configured_model.id,
        base_url=configured_model.base_url,
        timeout_seconds=60,
        temperature=0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        provider=configured_model.provider,
    )

    request = prepare_provider_request(
        configured_model,
        Context(messages=[UserMessage(content="hello")]),
        SimpleNamespace(azure_api_version="2025-04-01-preview"),
        config,
        ProviderProfile(name="azure-openai-responses"),
    )

    assert request.url == (
        "https://corp-resource.cognitiveservices.azure.com/openai/v1/responses"
        "?api-version=2025-04-01-preview"
    )
    assert request.body["model"] == "corp-gpt-54"
    assert request.headers["api-key"] == "azure-key"
    assert not any(key.lower() == "authorization" for key in request.headers)


def test_google_native_request_uses_google_wire_contract() -> None:
    model = Model(
        id="gemini-2.5-flash",
        name="Gemini 2.5 Flash",
        api="google-generative-ai",
        provider="google",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        reasoning=True,
        input=["text", "image"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=1_048_576,
        max_tokens=65_536,
    )
    config = ModelConfig(
        enabled=True,
        api_key="google-key",
        model=model.id,
        base_url=model.base_url,
        timeout_seconds=60,
        temperature=0,
        top_p=None,
        frequency_penalty=None,
        presence_penalty=None,
        seed=None,
        provider="google",
    )
    request = prepare_provider_request(
        model,
        Context(messages=[UserMessage(content="hello")], system_prompt="policy"),
        SimpleNamespace(reasoning="medium"),
        config,
        ProviderProfile(name="google", base_url=model.base_url),
    )

    assert isinstance(get_transport(model.api), GoogleGenerativeAITransport)
    assert request.api_mode == "google_generative_ai"
    assert request.url == (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-2.5-flash:streamGenerateContent?alt=sse"
    )
    assert request.headers["x-goog-api-key"] == "google-key"
    assert request.body == {
        "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
        "generationConfig": {
            "maxOutputTokens": 65_536,
            "thinkingConfig": {"includeThoughts": True, "thinkingBudget": 8192},
        },
        "systemInstruction": {"parts": [{"text": "policy"}]},
    }

    summary_request = prepare_provider_request(
        model,
        Context(messages=[UserMessage(content="summarize")], system_prompt="policy"),
        SimpleStreamOptions(reasoning="off", omit_max_tokens=True),
        config,
        ProviderProfile(name="google", base_url=model.base_url),
    )
    assert "maxOutputTokens" not in summary_request.body.get("generationConfig", {})


def test_google_stream_contract_preserves_thoughts_tools_and_usage() -> None:
    model = Model(
        id="gemini-2.5-flash",
        name="Gemini 2.5 Flash",
        api="google-generative-ai",
        provider="google",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        reasoning=True,
        input=["text"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=1_048_576,
        max_tokens=65_536,
    )
    lines = [
        'data: {"responseId":"r1","candidates":[{"content":{"parts":['
        '{"text":"consider","thought":true,"thoughtSignature":"c2ln"},'
        '{"functionCall":{"name":"read","args":{"path":"a.py"}},"thoughtSignature":"dG9vbA=="}'
        ']},"finishReason":"STOP"}],"usageMetadata":{"promptTokenCount":12,'
        '"cachedContentTokenCount":2,"candidatesTokenCount":3,"thoughtsTokenCount":4,'
        '"totalTokenCount":19}}',
    ]

    events = list(
        parse_sse_chunks(lines, model, api_mode="google_generative_ai", include_reasoning=True)
    )

    assert [event.type for event in events] == [
        "start",
        "thinking_start",
        "thinking_delta",
        "thinking_end",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_end",
        "done",
    ]
    message = events[-1].message
    assert message.stop_reason == "toolUse"
    assert message.response_id == "r1"
    assert message.usage.input == 10
    assert message.usage.output == 7
    assert message.usage.cache_read == 2
    assert message.usage.reasoning == 4


def test_google_native_replay_validates_signatures_and_places_legacy_tool_images() -> None:
    model = Model(
        id="gemini-2.5-flash",
        name="Gemini 2.5 Flash",
        api="google-generative-ai",
        provider="google",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        reasoning=True,
        input=["text", "image"],
        context_window=1_048_576,
        max_tokens=65_536,
    )
    assistant = AssistantMessage(
        content=[
            TextContent(text="answer", text_signature="not base64!"),
            ThinkingContent(thinking="thought", thinking_signature="c2ln"),
            ToolCall(
                id="call-1",
                name="look",
                arguments={},
                thought_signature="dG9vbA==",
            ),
        ],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="toolUse",
    )
    result = ToolResultMessage(
        tool_call_id="call-1",
        tool_name="look",
        content=[ImageContent(data="aW1hZ2U=", mime_type="image/png")],
        is_error=False,
    )
    context = Context(messages=[UserMessage(content="go"), assistant, result])

    body = GoogleGenerativeAITransport().build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=ProviderProfile(name="google"),
        stream=True,
        temperature=None,
        max_tokens=1024,
        context=context,
        target_model=model,
    )

    assistant_parts = body["contents"][1]["parts"]
    assert assistant_parts[0] == {"text": "answer"}
    assert assistant_parts[1] == {"text": "thought", "thought": True, "thoughtSignature": "c2ln"}
    assert assistant_parts[2] == {
        "functionCall": {"name": "look", "args": {}},
        "thoughtSignature": "dG9vbA==",
    }
    assert body["contents"][2] == {
        "role": "user",
        "parts": [
            {
                "functionResponse": {
                    "name": "look",
                    "response": {"output": "(see attached image)"},
                }
            }
        ],
    }
    assert body["contents"][3] == {
        "role": "user",
        "parts": [
            {"text": "Tool result image:"},
            {"inlineData": {"mimeType": "image/png", "data": "aW1hZ2U="}},
        ],
    }


def test_vertex_request_uses_project_location_resource_path(monkeypatch) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "corp-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    transport = get_transport("google-vertex")

    assert isinstance(transport, GoogleVertexTransport)
    assert transport.build_url(
        "https://{location}-aiplatform.googleapis.com",
        "gemini-2.5-flash",
    ) == (
        "https://us-central1-aiplatform.googleapis.com/v1/projects/corp-project/"
        "locations/us-central1/publishers/google/models/gemini-2.5-flash:streamGenerateContent?alt=sse"
    )


def test_bedrock_converse_request_uses_native_blocks() -> None:
    model = Model(
        id="anthropic.claude-sonnet-4-6",
        name="Claude Sonnet 4.6",
        api="bedrock-converse-stream",
        provider="amazon-bedrock",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        reasoning=True,
        input=["text"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=200_000,
        max_tokens=64_000,
    )
    context = Context(
        messages=[UserMessage(content="hello")],
        system_prompt="policy",
        tools=[Tool(name="read", description="Read a file", parameters={"type": "object"})],
    )
    transport = get_transport(model.api)
    body = transport.build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=ProviderProfile(name="bedrock"),
        stream=True,
        temperature=0,
        max_tokens=4096,
        cache_retention="long",
        reasoning_config={"enabled": True, "effort": "medium"},
        context=context,
        target_model=model,
    )

    assert isinstance(transport, BedrockConverseStreamTransport)
    assert transport.build_url(model.base_url, model.id) == (
        "https://bedrock-runtime.us-east-1.amazonaws.com/model/"
        "anthropic.claude-sonnet-4-6/converse-stream"
    )
    assert body["messages"] == [
        {
            "role": "user",
            "content": [
                {"text": "hello"},
                {"cachePoint": {"type": "default", "ttl": "1h"}},
            ],
        }
    ]
    assert body["system"] == [
        {"text": "policy"},
        {"cachePoint": {"type": "default", "ttl": "1h"}},
    ]
    assert body["inferenceConfig"] == {"maxTokens": 4096, "temperature": 0}
    assert body["toolConfig"]["tools"][0]["toolSpec"]["name"] == "read"
    assert body["additionalModelRequestFields"] == {
        "thinking": {"type": "adaptive", "display": "summarized"},
        "output_config": {"effort": "medium"},
    }


def test_bedrock_eventstream_contract_preserves_tools_and_usage() -> None:
    model = Model(
        id="anthropic.claude-sonnet-4-6",
        name="Claude Sonnet 4.6",
        api="bedrock-converse-stream",
        provider="amazon-bedrock",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        reasoning=True,
        input=["text"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=200_000,
        max_tokens=64_000,
    )
    events = list(
        _parse_bedrock_events(
            [
                {"messageStart": {"role": "assistant"}},
                {
                    "contentBlockStart": {
                        "contentBlockIndex": 0,
                        "start": {"toolUse": {"toolUseId": "call-1", "name": "read"}},
                    }
                },
                {
                    "contentBlockDelta": {
                        "contentBlockIndex": 0,
                        "delta": {"toolUse": {"input": '{"path":"a.py"}'}},
                    }
                },
                {"contentBlockStop": {"contentBlockIndex": 0}},
                {"messageStop": {"stopReason": "tool_use"}},
                {
                    "metadata": {
                        "usage": {
                            "inputTokens": 12,
                            "outputTokens": 3,
                            "cacheReadInputTokens": 2,
                            "cacheWriteInputTokens": 1,
                            "totalTokens": 15,
                        }
                    }
                },
            ],
            model,
        )
    )

    assert [event.type for event in events] == [
        "start",
        "toolcall_start",
        "toolcall_delta",
        "toolcall_end",
        "done",
    ]
    message = events[-1].message
    assert message.content[0].arguments == {"path": "a.py"}
    assert message.usage.input == 9
    assert message.usage.cache_read == 2
    assert message.usage.cache_write == 1


def test_bedrock_request_decodes_image_bytes_and_falls_back_from_unsigned_claude_thinking() -> None:
    model = Model(
        id="anthropic.claude-sonnet-4-6",
        name="Claude Sonnet 4.6",
        api="bedrock-converse-stream",
        provider="amazon-bedrock",
        base_url="https://bedrock-runtime.us-east-1.amazonaws.com",
        reasoning=True,
        input=["text", "image"],
        context_window=1_000_000,
        max_tokens=128_000,
    )
    assistant = AssistantMessage(
        content=[ThinkingContent(thinking="unfinished", thinking_signature=None)],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="stop",
    )
    context = Context(
        messages=[
            UserMessage(content=[ImageContent(data="aW1hZ2U=", mime_type="image/png")]),
            assistant,
        ]
    )

    body = BedrockConverseStreamTransport().build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=ProviderProfile(name="amazon-bedrock"),
        stream=True,
        temperature=None,
        max_tokens=1024,
        context=context,
        target_model=model,
    )

    assert body["messages"][0]["content"] == [
        {"image": {"format": "png", "source": {"bytes": b"image"}}}
    ]
    assert body["messages"][1]["content"] == [{"text": "unfinished"}]


def test_every_static_model_api_has_a_concrete_transport() -> None:
    apis = {model.api for model in load_builtin_models()}

    assert apis == {
        "anthropic-messages",
        "azure-openai-responses",
        "bedrock-converse-stream",
        "google-generative-ai",
        "google-vertex",
        "mistral-conversations",
        "openai-codex-responses",
        "openai-completions",
        "openai-responses",
    }
    assert all(not isinstance(get_transport(api), UnsupportedTransport) for api in apis)


def test_anthropic_native_contract_applies_cache_and_adaptive_thinking() -> None:
    model = Model(
        id="claude-sonnet-4-6",
        name="Claude Sonnet 4.6",
        api="anthropic-messages",
        provider="anthropic",
        base_url="https://api.anthropic.com",
        reasoning=True,
        input=["text"],
        cost={"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        context_window=1_000_000,
        max_tokens=128_000,
        compat={"forceAdaptiveThinking": True},
    )
    context = Context(
        messages=[UserMessage(content="hello")],
        system_prompt="policy",
        tools=[Tool(name="read", description="Read", parameters={"type": "object"})],
    )
    body = AnthropicMessagesTransport().build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=ProviderProfile(name="anthropic"),
        stream=True,
        temperature=0.7,
        max_tokens=4096,
        cache_retention="long",
        reasoning_config={"enabled": True, "effort": "medium"},
        context=context,
        target_model=model,
        model_compat=model.compat,
    )

    marker = {"type": "ephemeral", "ttl": "1h"}
    assert body["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "hello", "cache_control": marker}]}
    ]
    assert body["system"] == [{"type": "text", "text": "policy", "cache_control": marker}]
    assert body["tools"] == [
        {
                "name": "read",
                "description": "Read",
                "input_schema": {"type": "object", "properties": {}, "required": []},
                "eager_input_streaming": True,
                "cache_control": marker,
        }
    ]
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": "medium"}
    assert "temperature" not in body

    summary_body = AnthropicMessagesTransport().build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=ProviderProfile(name="anthropic"),
        stream=True,
        temperature=None,
        max_tokens=None,
        omit_max_tokens=True,
        context=Context(messages=[UserMessage(content="summarize")]),
        target_model=model,
        model_compat=model.compat,
    )
    assert summary_body["max_tokens"] == model.max_tokens


def test_anthropic_native_contract_uses_deferred_tool_references() -> None:
    model = Model(
        id="claude-sonnet-4-6",
        name="Claude Sonnet 4.6",
        api="anthropic-messages",
        provider="anthropic",
        base_url="https://api.anthropic.com",
        reasoning=True,
        input=["text"],
        context_window=1_000_000,
        max_tokens=128_000,
    )
    assistant = AssistantMessage(
        content=[ToolCall(id="load|invalid", name="load_tools", arguments={"names": ["write"]})],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="toolUse",
    )
    result = ToolResultMessage(
        tool_call_id="load|invalid",
        tool_name="load_tools",
        content=[TextContent(text="loaded")],
        added_tool_names=["write"],
        is_error=False,
    )
    context = Context(
        messages=[UserMessage(content="work"), assistant, result],
        tools=[
            Tool(name="load_tools", description="Load", parameters={"type": "object"}),
            Tool(name="write", description="Write", parameters={"type": "object"}),
        ],
    )

    body = AnthropicMessagesTransport().build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=ProviderProfile(name="anthropic"),
        stream=True,
        temperature=None,
        max_tokens=4096,
        context=context,
        target_model=model,
        model_compat=model.compat,
    )

    assert body["tools"] == [
        {
            "name": "load_tools",
            "description": "Load",
                "eager_input_streaming": True,
                "input_schema": {"type": "object", "properties": {}, "required": []},
                "cache_control": {"type": "ephemeral"},
        },
        {
            "name": "write",
            "description": "Write",
            "eager_input_streaming": True,
            "input_schema": {"type": "object", "properties": {}, "required": []},
            "defer_loading": True,
        },
    ]
    assert body["messages"][-1] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "load|invalid",
                "content": [{"type": "tool_reference", "tool_name": "write"}],
                "is_error": False,
            },
            {"type": "text", "text": "loaded", "cache_control": {"type": "ephemeral"}},
        ],
    }
    assert "extra_headers" not in body


def test_anthropic_oauth_stream_restores_registered_tool_casing() -> None:
    model = Model(
        id="claude-sonnet-4-6",
        name="Claude Sonnet 4.6",
        api="anthropic-messages",
        provider="anthropic",
        base_url="https://api.anthropic.com",
        input=["text"],
    )
    tools = [Tool(name="read", description="Read", parameters={"type": "object"})]
    lines = [
        'data: {"type":"message_start","message":{"id":"msg_1","usage":{}}}',
        'data: {"type":"content_block_start","index":0,"content_block":'
        '{"type":"tool_use","id":"call_1","name":"Read","input":{}}}',
        'data: {"type":"content_block_delta","index":0,"delta":'
        '{"type":"input_json_delta","partial_json":"{\\"path\\":\\"a.py\\"}"}}',
        'data: {"type":"content_block_stop","index":0}',
        'data: {"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{}}',
        'data: {"type":"message_stop"}',
    ]

    events = list(
        parse_sse_chunks(
            lines,
            model,
            api_mode="anthropic_messages",
            tools=tools,
            anthropic_oauth=True,
        )
    )

    assert events[-1].message.content[0].name == "read"


def test_chat_stream_does_not_reclassify_model_tool_errors_as_context_length() -> None:
    model = _openrouter_qwen_model()
    lines = [
        'data: {"id":"r1","choices":[{"delta":{"tool_calls":[{"index":0,"id":"call-1",'
        '"function":{"name":"write","arguments":"{\\"path\\":\\"a.py\\",\\"content\\":"}}]},'
        '"finish_reason":null}]}',
        'data: {"id":"r1","choices":[{"delta":{},"finish_reason":"tool_calls"}]}',
    ]

    events = list(parse_sse_chunks(lines, model))

    assert events[-1].type == "done"
    assert events[-1].reason == "toolUse"
    assert events[-1].message.diagnostics is None


def test_provider_error_boundary_does_not_infer_guardrail_recovery_policy() -> None:
    model = _openrouter_qwen_model()
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(
        403,
        request=request,
        json={"error": {"message": "prompt injection patterns detected"}},
    )
    error = httpx.HTTPStatusError("forbidden", request=request, response=response)

    formatted = _format_provider_exception(error, model)

    assert formatted == '403: {"error":{"message":"prompt injection patterns detected"}}'
    assert "compact" not in formatted.lower()
    assert "retry" not in formatted.lower()


def test_streaming_http_error_body_is_read_before_the_response_closes(monkeypatch) -> None:
    class ErrorBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"error":{"message":"provider says exact failure"}}'

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            stream=ErrorBody(),
            headers={"content-type": "application/json"},
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        travis_env_module.httpx,
        "Client",
        lambda timeout: real_client(timeout=timeout, transport=httpx.MockTransport(handler)),
    )
    model = Model(
        id="model",
        name="Model",
        api="openai-completions",
        provider="custom",
        base_url="https://provider.example/v1",
    )
    provider = TravisProvider(
        ModelConfig(
            enabled=True,
            api_key="test-key",
            model=model.id,
            base_url=model.base_url,
            timeout_seconds=5,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            provider=model.provider,
        )
    )

    message = provider.stream(
        model,
        Context(messages=[UserMessage(content="hello")]),
    ).result_sync()

    assert message.stop_reason == "error"
    assert message.error_message == '403: {"error":{"message":"provider says exact failure"}}'


def test_codex_retries_a_transient_response_before_streaming_begins(monkeypatch) -> None:
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "account-123"}}
        ).encode()
    ).decode().rstrip("=")
    token = f"header.{payload}.signature"
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                429,
                json={"error": {"message": "temporarily rate limited"}},
                headers={"retry-after-ms": "0"},
            )
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.completed","response":{"id":"response-1",'
                '"status":"completed","output":[],"usage":{"input_tokens":1,'
                '"output_tokens":0,"total_tokens":1}}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        travis_env_module.httpx,
        "Client",
        lambda timeout: real_client(timeout=timeout, transport=httpx.MockTransport(handler)),
    )
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == "openai-codex" and model.id == "gpt-5.4"
    )
    provider = TravisProvider(
        ModelConfig(
            enabled=True,
            api_key=token,
            model=model.id,
            base_url=model.base_url,
            timeout_seconds=5,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            provider=model.provider,
        )
    )

    message = provider.stream(
        model,
        Context(messages=[UserMessage(content="hello")]),
        SimpleNamespace(api_key=token, max_retries=1, max_retry_delay_ms=0, transport="sse"),
    ).result_sync()

    assert calls == 2
    assert message.stop_reason == "stop"


def test_codex_auto_websocket_reuses_session_and_sends_only_context_delta(monkeypatch) -> None:
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "account-123"}}
        ).encode()
    ).decode().rstrip("=")
    token = f"header.{payload}.signature"

    class OpenState:
        name = "OPEN"

    class FakeConnection:
        state = OpenState()

        def __init__(self) -> None:
            self.sent: list[dict[str, object]] = []
            self.responses = [
                {
                    "type": "response.completed",
                    "response": {
                        "id": "response-1",
                        "status": "completed",
                        "output": [],
                        "usage": {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
                    },
                },
                {
                    "type": "response.completed",
                    "response": {
                        "id": "response-2",
                        "status": "completed",
                        "output": [],
                        "usage": {"input_tokens": 1, "output_tokens": 0, "total_tokens": 1},
                    },
                },
            ]
            self.closed = False

        def send(self, data: str) -> None:
            self.sent.append(json.loads(data))

        def recv(self, timeout=None):
            del timeout
            return json.dumps(self.responses.pop(0))

        def close(self, code=1000, reason="done") -> None:
            del code, reason
            self.closed = True

    connection = FakeConnection()
    handshakes: list[tuple[str, dict[str, str], float]] = []

    def connect(url, headers, timeout):
        handshakes.append((url, dict(headers), timeout))
        return connection

    codex_runtime_module.close_codex_websocket_sessions()
    monkeypatch.setattr(codex_runtime_module, "_connect_websocket", connect)
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == "openai-codex" and model.id == "gpt-5.4"
    )
    provider = TravisProvider(
        ModelConfig(
            enabled=True,
            api_key=token,
            model=model.id,
            base_url=model.base_url,
            timeout_seconds=5,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            provider=model.provider,
        )
    )
    options = SimpleNamespace(
        api_key=token,
        transport="auto",
        session_id="session-123",
        websocket_connect_timeout_ms=250,
    )

    first = provider.stream(
        model,
        Context(messages=[UserMessage(content="first")]),
        options,
    ).result_sync()
    second = provider.stream(
        model,
        Context(messages=[UserMessage(content="first"), first, UserMessage(content="second")]),
        options,
    ).result_sync()

    assert first.response_id == "response-1"
    assert second.response_id == "response-2"
    assert len(handshakes) == 1
    assert handshakes[0][0] == "wss://chatgpt.com/backend-api/codex/responses"
    assert handshakes[0][1]["originator"] == "travis234"
    assert handshakes[0][1]["OpenAI-Beta"] == "responses_websockets=2026-02-06"
    assert handshakes[0][2] == 0.25
    assert "previous_response_id" not in connection.sent[0]
    assert connection.sent[1]["previous_response_id"] == "response-1"
    assert len(connection.sent[1]["input"]) == 1
    codex_runtime_module.close_codex_websocket_sessions()


def test_codex_auto_websocket_failure_falls_back_to_sse_for_the_session(monkeypatch) -> None:
    payload = base64.urlsafe_b64encode(
        json.dumps(
            {"https://api.openai.com/auth": {"chatgpt_account_id": "account-123"}}
        ).encode()
    ).decode().rstrip("=")
    token = f"header.{payload}.signature"
    websocket_calls = 0
    http_calls = 0

    def fail_websocket(_url, _headers, _timeout):
        nonlocal websocket_calls
        websocket_calls += 1
        raise OSError("websocket unavailable")

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal http_calls
        http_calls += 1
        return httpx.Response(
            200,
            text=(
                'data: {"type":"response.completed","response":{"id":"response-sse",'
                '"status":"completed","output":[],"usage":{"input_tokens":1,'
                '"output_tokens":0,"total_tokens":1}}}\n\n'
            ),
            headers={"content-type": "text/event-stream"},
        )

    codex_runtime_module.close_codex_websocket_sessions()
    monkeypatch.setattr(codex_runtime_module, "_connect_websocket", fail_websocket)
    real_client = httpx.Client
    monkeypatch.setattr(
        codex_runtime_module.httpx,
        "Client",
        lambda timeout: real_client(timeout=timeout, transport=httpx.MockTransport(handler)),
    )
    model = next(
        model
        for model in load_builtin_models()
        if model.provider == "openai-codex" and model.id == "gpt-5.4"
    )
    provider = TravisProvider(
        ModelConfig(
            enabled=True,
            api_key=token,
            model=model.id,
            base_url=model.base_url,
            timeout_seconds=5,
            temperature=0,
            top_p=None,
            frequency_penalty=None,
            presence_penalty=None,
            seed=None,
            provider=model.provider,
        )
    )
    options = SimpleNamespace(api_key=token, transport="auto", session_id="session-fallback")

    first = provider.stream(model, Context(messages=[UserMessage(content="first")]), options).result_sync()
    second = provider.stream(model, Context(messages=[UserMessage(content="second")]), options).result_sync()

    assert first.stop_reason == "stop"
    assert second.stop_reason == "stop"
    assert websocket_calls == 1
    assert http_calls == 2
    codex_runtime_module.close_codex_websocket_sessions()


def test_chat_usage_separates_cache_and_reasoning_tokens() -> None:
    model = _openrouter_qwen_model()
    lines = [
        'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":100,"completion_tokens":30,'
        '"prompt_tokens_details":{"cached_tokens":20,"cache_write_tokens":5},'
        '"completion_tokens_details":{"reasoning_tokens":10}}}',
        "data: [DONE]",
    ]

    events = list(parse_sse_chunks(lines, model, wait_for_usage_after_finish=True))
    usage = events[-1].message.usage

    assert usage.input == 75
    assert usage.output == 30
    assert usage.cache_read == 20
    assert usage.cache_write == 5
    assert usage.reasoning == 10
    assert usage.total_tokens == 130


def test_responses_transport_preserves_native_replay_and_deferred_tool_contract() -> None:
    model = Model(
        id="gpt-5.4",
        name="GPT-5.4",
        api="openai-responses",
        provider="openai",
        base_url="https://api.openai.com/v1",
        reasoning=True,
        input=["text", "image"],
        context_window=1_000_000,
        max_tokens=128_000,
        compat={"supportsToolSearch": True},
    )
    reasoning_item = {
        "type": "reasoning",
        "id": "rs_1",
        "summary": [],
        "encrypted_content": "opaque",
    }
    assistant = AssistantMessage(
        content=[
            ThinkingContent(thinking="", thinking_signature=json.dumps(reasoning_item)),
            TextContent(
                text="working",
                text_signature=json.dumps({"v": 1, "id": "msg_1", "phase": "commentary"}),
            ),
            ToolCall(id="call_1|fc_1", name="load_tools", arguments={"names": ["write"]}),
        ],
        api=model.api,
        provider=model.provider,
        model=model.id,
        usage=empty_usage(),
        stop_reason="toolUse",
    )
    result = ToolResultMessage(
        tool_call_id="call_1|fc_1",
        tool_name="load_tools",
        content=[TextContent(text="loaded")],
        added_tool_names=["write"],
        is_error=False,
    )
    context = Context(
        system_prompt="policy",
        messages=[UserMessage(content="work"), assistant, result],
        tools=[
            Tool(name="load_tools", description="Load", parameters={"type": "object"}),
            Tool(name="write", description="Write", parameters={"type": "object"}),
        ],
    )

    body = OpenAIResponsesTransport().build_kwargs(
        model=model.id,
        messages=[],
        tools=[],
        profile=ProviderProfile(name="openai"),
        stream=True,
        temperature=None,
        max_tokens=32,
        context=context,
        target_model=model,
        model_compat=model.compat,
    )

    assert body["input"][0] == {"role": "developer", "content": "policy"}
    assert reasoning_item in body["input"]
    assert {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "output_text", "text": "working", "annotations": []}],
        "status": "completed",
        "id": "msg_1",
        "phase": "commentary",
    } in body["input"]
    assert body["tools"] == [
        {
            "type": "function",
            "name": "load_tools",
            "description": "Load",
            "parameters": {"type": "object"},
            "strict": False,
        }
    ]
    assert any(item.get("type") == "tool_search_output" for item in body["input"])


def test_responses_stream_preserves_text_signature_and_exact_usage_split() -> None:
    model = Model(
        id="gpt-5.4",
        name="GPT-5.4",
        api="openai-responses",
        provider="openai",
        base_url="https://api.openai.com/v1",
        context_window=1_000_000,
        max_tokens=128_000,
    )
    lines = [
        'data: {"type":"response.output_item.added","output_index":0,"item":'
        '{"type":"message","id":"msg_1","role":"assistant","status":"in_progress","content":[]}}',
        'data: {"type":"response.output_item.done","output_index":0,"item":'
        '{"type":"message","id":"msg_1","phase":"final_answer","role":"assistant",'
        '"status":"completed","content":[{"type":"output_text","text":"done","annotations":[]}]}}',
        'data: {"type":"response.completed","response":{"id":"resp_1","status":"completed",'
        '"output":[],"usage":{"input_tokens":100,"output_tokens":30,"total_tokens":130,'
        '"input_tokens_details":{"cached_tokens":20,"cache_write_tokens":5},'
        '"output_tokens_details":{"reasoning_tokens":10}}}}',
    ]

    events = list(decode_responses_stream(lines, model))
    message = events[-1].message

    assert isinstance(message.content[0], TextContent)
    assert json.loads(message.content[0].text_signature) == {
        "v": 1,
        "id": "msg_1",
        "phase": "final_answer",
    }
    assert message.usage.input == 75
    assert message.usage.cache_read == 20
    assert message.usage.cache_write == 5
    assert message.usage.output == 30
    assert message.usage.reasoning == 10
    assert message.usage.total_tokens == 130


def test_vertex_url_requires_project_location_for_adc_and_uses_express_for_key(monkeypatch) -> None:
    transport = GoogleVertexTransport()
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)

    assert transport.build_url(
        "https://aiplatform.googleapis.com",
        "gemini-3-flash-preview",
        None,
        "vertex-api-key",
    ) == (
        "https://aiplatform.googleapis.com/v1/publishers/google/models/"
        "gemini-3-flash-preview:streamGenerateContent?alt=sse"
    )
    with pytest.raises(ValueError, match="project ID"):
        transport.build_url(
            "https://aiplatform.googleapis.com",
            "gemini-3-flash-preview",
            None,
            None,
        )

    options = SimpleNamespace(project="my-project", location="us-central1")
    assert transport.build_url(
        "https://aiplatform.googleapis.com",
        "gemini-3-flash-preview",
        options,
        None,
    ) == (
        "https://us-central1-aiplatform.googleapis.com/v1/projects/my-project/locations/us-central1/"
        "publishers/google/models/gemini-3-flash-preview:streamGenerateContent?alt=sse"
    )


def test_vertex_adc_adds_oauth_bearer_header(monkeypatch) -> None:
    credentials = SimpleNamespace(token=None)

    def refresh(_request) -> None:
        credentials.token = "adc-token"

    credentials.refresh = refresh
    google_module = ModuleType("google")
    auth_module = ModuleType("google.auth")
    transport_module = ModuleType("google.auth.transport")
    requests_module = ModuleType("google.auth.transport.requests")
    auth_module.default = lambda **_kwargs: (credentials, "project")
    requests_module.Request = object
    google_module.auth = auth_module
    auth_module.transport = transport_module
    transport_module.requests = requests_module
    monkeypatch.setitem(sys.modules, "google", google_module)
    monkeypatch.setitem(sys.modules, "google.auth", auth_module)
    monkeypatch.setitem(sys.modules, "google.auth.transport", transport_module)
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", requests_module)
    request = PreparedProviderRequest(
        url="https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1",
        headers={"Content-Type": "application/json"},
        body={},
        timeout_seconds=60,
        api_mode="google_vertex",
        decoder=lambda _lines: iter(()),
    )

    authorized = _authorize_google_vertex_request(request)

    assert authorized.headers["Authorization"] == "Bearer adc-token"
