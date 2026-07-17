# OpenAI Codex Generation Capability Fix Design

**Status:** Draft for user review

**Date:** 2026-07-17

**Scope:** Correct only the `openai_codex_responses` generation-parameter contract and wire payload

## Goal

Prevent Travis234 from sending sampling fields that the ChatGPT OAuth Codex Responses endpoint does not accept, while retaining the user's configured values as session/startup state, reporting them as dropped for the active Codex model, and leaving every other provider, Agent session behavior, compaction path, and context-envelope calculation unchanged.

## Evidence and root cause

Context7 resolved the high-reputation official Codex source as `/openai/codex`. The official `ResponsesApiRequest` declaration in [`codex-rs/codex-api/src/common.rs`](https://github.com/openai/codex/blob/main/codex-rs/codex-api/src/common.rs) serializes these request controls:

- `model`
- `instructions`
- `input`
- `tools`
- `tool_choice`
- `parallel_tool_calls`
- `reasoning`
- `store`
- `stream`
- `stream_options`
- `include`
- `service_tier`
- `prompt_cache_key`
- `text`
- `client_metadata`

It does not define `temperature`, `top_p`, or `max_output_tokens`.

Travis currently violates that contract at two boundaries:

1. `travis/ai/providers/capabilities.py` groups `openai_codex_responses` with generic OpenAI and Azure Responses, returns `temperature` and `max_tokens` as supported, and copies `top_p` into request overrides.
2. `CodexResponsesTransport.build_kwargs()` accepts the resulting values and can serialize `temperature` directly or sampling fields through `request_overrides`.

The incorrect capability assumption originated in commit `9ab17b9` and was encoded by a test expecting Codex temperature support. The new `/params` session feature exposed the pre-existing provider defect by carrying an inherited CLI value through a model switch; it did not create the provider defect.

## Selected design

Use a Codex-specific allowlist at capability preparation and a final Codex wire-schema guard. This is intentionally narrower than modifying the generic Responses policy, retrying provider failures, or changing `/params` reset semantics.

### Capability boundary

Insert an `api_mode == "openai_codex_responses"` branch before the generic Responses branch.

Codex-supported generation controls:

- `parallel_tool_calls`
- `tool_choice`

Codex operational controls that remain handled outside `GenerationPayload`:

- `thinking` through the existing session reasoning path
- `timeout_seconds` through provider request timeout preparation
- model metadata such as context window and native output ceiling

Codex-unsupported user generation fields:

- `temperature`
- `top_p`
- `max_tokens`
- `stop`
- `frequency_penalty`
- `presence_penalty`
- `seed`
- `provider_sort`

When one of these fields is configured, the capability layer preserves the immutable source `GenerationParams` object but returns no wire value for that field and emits a `ProviderParamWarning(param=<name>, action="dropped", ...)`.

The generic `openai_responses` and `azure_openai_responses` branch remains byte-for-byte behaviorally unchanged.

### Wire boundary

`CodexResponsesTransport.build_kwargs()` must never return a request body containing:

- `temperature`
- `top_p`
- `max_output_tokens`

The transport removes those keys after applying `request_overrides`. This is defense in depth for direct transport callers and future capability regressions. `OpenAIResponsesTransport` and `AzureOpenAIResponsesTransport` override `build_kwargs()` and therefore retain their existing request behavior.

No reactive retry is added. A known invalid field must be prevented before the request instead of consuming a failed provider call and retrying mid-turn.

## Exact behavior matrix

| Field | OpenAI Codex behavior | Stored session/startup value | Other providers |
|---|---|---|---|
| `temperature` | Dropped with warning; absent on wire | Preserved | Unchanged |
| `top_p` | Dropped with warning; absent on wire | Preserved | Unchanged |
| `max_tokens` override | Dropped with warning; absent on Codex wire | Preserved | Unchanged |
| `stop` | Existing dropped warning retained | Preserved | Unchanged |
| penalties and `seed` | Existing dropped warnings retained | Preserved | Unchanged |
| `provider_sort` | Dropped with warning | Preserved | Unchanged |
| `parallel_tool_calls` | Forwarded when tools are enabled | Preserved | Unchanged |
| `tool_choice` | Forwarded | Preserved | Unchanged |
| `thinking` | Existing reasoning mapping | Existing session owner | Unchanged |
| `timeout_seconds` | Existing HTTP timeout path | Preserved | Unchanged |

## `/params` semantics

`/params` remains an editor of configured session state, not a destructive provider-normalization command.

For a Codex model with inherited CLI temperature, the expected display is equivalent to:

```text
openai-codex/gpt-5.3-codex-spark: thinking=high, temperature=0.2 (cli); warnings: temperature dropped
```

`/params reset` continues to clear only durable session overrides. If `temperature=0.2` came from CLI or dotenv configuration, it remains visible and remains marked dropped while Codex is selected. Switching back to a compatible provider activates it again without rewriting the session.

## Data flow after correction

```text
provider/dotenv/CLI settings
            +
durable session overrides
            |
            v
effective immutable GenerationParams
            |
            v
Codex-specific capability allowlist
   | supported             | unsupported
   v                       v
request fields        dropped warnings
   |
   v
Codex wire-schema guard
   |
   v
chatgpt.com/backend-api/codex/responses
```

No step modifies messages, session JSONL, prompt construction, tool ordering, iteration budgets, compaction state, or token estimates.

## File boundary

Production modifications are limited to:

- `travis/ai/providers/capabilities.py`
- `travis/ai/providers/transports.py`

Regression modifications are limited to:

- `tests/test_ai_provider_capabilities.py`
- `tests/test_reference_runtime_contract.py`
- `tests/test_tui_runtime_compaction_and_models.py`

No changes are permitted in:

- `travis/agent/`
- `travis/coding_agent/`
- `travis/compaction/`
- session-store or session-generation-parameter ownership
- model context windows or output ceilings
- provider authentication
- OpenRouter, OpenAI API, Azure, Anthropic, Google, Bedrock, Mistral, or other transport policy

## Failure handling

- Unsupported Codex fields produce informational `dropped` warnings; they do not invalidate or erase configured values.
- Supported Codex controls continue through existing validation and provider preparation.
- No automatic mutation, fallback provider, or retry is introduced.
- Provider errors unrelated to these fields retain existing behavior.

## Verification strategy

### TDD regression

1. Replace the incorrect Codex capability expectation with a failing test asserting unsupported fields are dropped and supported tool controls survive.
2. Add a failing wire-shape test that injects unsupported fields directly into `CodexResponsesTransport` and proves they must be absent.
3. Add a failing TUI/session-isolation test matching the reported model-switch/reset scenario and proving messages, token estimate, thinking, and saved parameter sources are unchanged.

### Non-Codex invariants

- Explicitly assert generic OpenAI and Azure Responses retain their current temperature, `top_p`, and output-token behavior.
- Run the complete provider and TUI suites.
- Run the complete repository Python suite, npm launcher tests, Python/npm builds, clean-wheel acceptance, parity verification, and release-container smoke required by repository guidance.

### Live acceptance

From a freshly built wheel:

1. Start with `temperature=0.2` from CLI or dotenv.
2. Select `openai-codex/gpt-5.3-codex-spark`.
3. Confirm `/params` preserves `temperature=0.2` and reports `temperature dropped`.
4. Send a prompt and confirm no unsupported-temperature error.
5. Run `/params reset`, confirm inherited CLI temperature remains dropped, and send another successful prompt.
6. Switch to a compatible non-Codex provider and confirm the same configured temperature is no longer marked dropped and remains effective.

## Risks and containment

| Risk | Containment |
|---|---|
| Accidentally disable temperature for generic Responses | Codex branch is exact-match and generic regression tests assert unchanged payloads |
| Hide a bad field without informing the user | Capability warning remains visible in `/params` |
| Erase startup/session configuration | Capability preparation operates on immutable values and never publishes session state |
| Alter context envelope | No message, model-window, compaction, or token-estimation code is touched; isolation test compares before/after estimates |
| Reintroduce through direct transport calls | Final Codex wire guard removes unsupported keys after overrides |
| Mask unrelated provider failures | No reactive retry or broad exception matching is added |

## Non-goals

- Changing `/params reset` precedence
- Removing unsupported values from the user's session or startup configuration
- Adding provider-error retry logic
- Modifying generic OpenAI or Azure Responses schemas
- Changing reasoning effort, tool execution, authentication, compaction, or context limits
- Refactoring the transport hierarchy
- Committing or pushing without a separate user instruction
