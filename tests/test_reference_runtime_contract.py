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
from travis.ai.providers.params import GenerationParams
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


def test_reference_oracles_are_optional_and_outside_runtime_and_release_trees() -> None:
    root = Path(__file__).parents[1]
    oracle_names = ("pi", "hermes-agent", "appv231")
    docker_ignored = set((root / ".dockerignore").read_text(encoding="utf-8").splitlines())

    assert (root / "PI_HERMES_TRAVIS_CROSS_CHECK_REPORT.md").is_file()
    assert set(oracle_names) <= docker_ignored
    assert all(not (root / "travis" / name).exists() for name in oracle_names)
    for oracle in (root / name for name in oracle_names):
        if oracle.exists():
            assert oracle.is_dir()
            assert not oracle.is_relative_to(root / "travis")


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


@pytest.mark.parametrize(
    ("timeout_ms", "expected_seconds"),
    ((250, 0.25), (0, None)),
)
def test_runtime_timeout_option_controls_provider_request_deadline(
    timeout_ms: int,
    expected_seconds: float | None,
) -> None:
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
        SimpleNamespace(timeout_ms=timeout_ms),
        config,
        ProviderProfile(name="openrouter", base_url=model.base_url),
    )

    assert request.timeout_seconds == expected_seconds


def test_negative_runtime_timeout_is_rejected_before_request_construction() -> None:
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

    with pytest.raises(ValueError, match="Invalid timeout_ms: -1"):
        prepare_provider_request(
            model,
            Context(messages=[UserMessage(content="hello")]),
            SimpleNamespace(timeout_ms=-1),
            config,
            ProviderProfile(name="openrouter", base_url=model.base_url),
        )


def test_generation_warning_precedes_payload_callback_and_replacement() -> None:
    model = replace(
        _openrouter_qwen_model(),
        id="glm-5",
        provider="zai",
        base_url="https://api.z.ai/api/paas/v4",
        compat=None,
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
    callback_order: list[str] = []
    warning_fields: list[tuple[str, str]] = []

    def on_generation_warning(warning) -> None:
        callback_order.append("warning")
        warning_fields.append((warning.param, warning.action))

    def on_payload(_body, _model) -> dict[str, object]:
        callback_order.append("payload")
        return {"replacement": True}

    request = prepare_provider_request(
        model,
        Context(messages=[UserMessage(content="hello")]),
        SimpleNamespace(
            generation_params=GenerationParams(provider_sort="price"),
            on_generation_warning=on_generation_warning,
            on_payload=on_payload,
        ),
        config,
        ProviderProfile(name=model.provider, base_url=model.base_url),
    )

    assert callback_order == ["warning", "payload"]
    assert warning_fields == [("provider_sort", "dropped")]
    assert request.body == {"replacement": True}


def test_non_mapping_payload_callback_result_retains_constructed_body() -> None:
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
        provider=model.provider,
    )
    observed_body: dict[str, object] = {}

    def on_payload(body, _model) -> str:
        observed_body.update(body)
        return "ignored"

    request = prepare_provider_request(
        model,
        Context(messages=[UserMessage(content="hello")]),
        SimpleNamespace(on_payload=on_payload),
        config,
        ProviderProfile(name=model.provider, base_url=model.base_url),
    )

    assert observed_body
    assert request.body == observed_body


@pytest.mark.parametrize(
    "placeholder_key",
    ("gcp-vertex-credentials", "<vertex-managed-credentials>"),
)
def test_vertex_placeholder_api_keys_are_suppressed(
    placeholder_key: str,
) -> None:
    model = Model(
        id="gemini-3-flash-preview",
        name="Gemini 3 Flash Preview",
        api="google-vertex",
        provider="google-vertex",
        base_url="https://aiplatform.googleapis.com",
        context_window=1_048_576,
        max_tokens=65_536,
    )
    config = ModelConfig(
        enabled=True,
        api_key=placeholder_key,
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
        SimpleNamespace(project="test-project", location="us-central1"),
        config,
        ProviderProfile(name=model.provider, base_url=model.base_url),
    )

    assert "key=" not in request.url
    assert "x-goog-api-key" not in {key.lower() for key in request.headers}


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
    codex = CodexResponsesTransport().build_kwargs(
        **common,
        request_overrides={"top_p": 0.9, "max_output_tokens": 123},
    )

    assert openai["temperature"] == 0
    assert openai["max_output_tokens"] == 16
    assert openai["prompt_cache_key"] == "s" * 64
    assert openai["prompt_cache_retention"] == "24h"
    assert openai["input"][0] == {"role": "system", "content": "policy"}
    assert "prompt_cache_retention" not in azure
    assert azure["temperature"] == 0
    assert azure["prompt_cache_key"] == "s" * 64
    assert codex["instructions"] == "policy"
    assert "temperature" not in codex
    assert "top_p" not in codex
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
    assert "temperature" not in request.body


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
