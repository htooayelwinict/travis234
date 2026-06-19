# Sub-project 1: ai-parity Design

Date: 2026-06-19
Status: Design (awaiting user review)
Parent: `2026-06-19-appv22-pi-hermes-parity-decomposition.md`
Reference: `pi/packages/ai/src`

## Goal

Port the public surface of pi's `ai` package into a new `appv22/ai/` Python
package: a provider/model abstraction, the unified message types, the streaming
`AssistantMessageEvent` protocol, and the tool-call format. Provide one concrete
provider — a **fresh, self-contained** OpenAI/OpenRouter-compatible client with
**real SSE streaming**.

**Remove the appv21 dependency entirely.** appv22 currently reaches into a sibling
`appV2.1/` package via dynamic `import_module("appv21...")` + `sys.path`
discovery (all confined to `appv22/providers/appv2_env.py`). This sub-project
ports the small pieces appv22 actually used from appv21 (the `.env` loader and
env→model-config resolution) as fresh appv22 code, and deletes the appv21
import/discovery path. No runtime import of `appv21` remains.

This sub-project builds the `ai` layer but does **not** yet rewire the agent loop
(that is sub-project 2). The existing `appv22/providers/appv2_env.py` `decide()`
path stays as a transitional shim (now backed by the new appv22 transport instead
of appv21) until sub-project 2 deletes it.

## Scope

Port only what appv22 needs (not all 14k LOC / 12 providers of pi `ai`):

### New package layout (`appv22/ai/`, mirrors `pi/packages/ai/src`)

| appv22 file | pi source | Contents |
|---|---|---|
| `ai/types.py` | `types.ts` | `KnownApi`/`Api`, `Provider`, `ThinkingLevel`, `StopReason`, content blocks `TextContent`/`ThinkingContent`/`ImageContent`/`ToolCall`, `Usage`, `UserMessage`/`AssistantMessage`/`ToolResultMessage`/`Message`, `Tool`, `Context`, `Model`, `StreamOptions`/`SimpleStreamOptions`, `ProviderResponse`, the `AssistantMessageEvent` union, `StreamFunction` type alias. |
| `ai/event_stream.py` | `utils/event-stream.ts` | `EventStream` (push/end/`__aiter__`/`result()`) and `AssistantMessageEventStream` (completes on `done`/`error`), plus `create_assistant_message_event_stream`. |
| `ai/stream.py` | `stream.ts` + `api-registry.ts` | `stream`, `complete`, `stream_simple`, `complete_simple`; `register_api_provider`, `get_api_provider`, `reset_api_providers`, `ApiProvider`. |
| `ai/models.py` | `models.ts` | minimal `Model` registry accessors (`get_model`, `get_models`, `get_providers`, `register_model`) + `calculate_cost`. |
| `ai/overflow.py` | `utils/overflow.ts` + appv22 `provider_errors.py` | `is_context_overflow`. |
| `ai/env_config.py` | (fresh; replaces appv21 `env_config.py`) | `.env` loader + env→`ModelConfig` resolution (`load_dotenv_values`, `load_model_config`), snake_case, no appv21. |
| `ai/providers/appv2_env.py` | provider impl pattern | `AppV2EnvProvider` implementing the streaming `ApiProvider` contract over a fresh httpx SSE client, configured from `ai/env_config.py`. |
| `ai/providers/register_builtins.py` | `providers/register-builtins.ts` | lazy registration of the appv2-env provider. |
| `ai/providers/faux.py` | `providers/faux.ts` | scripted faux provider for tests (emits a programmed `AssistantMessageEvent` sequence). |
| `ai/__init__.py` | `index.ts` | barrel re-exporting the public surface. |

### Data type mapping (TS → Python)

- TS `interface`/discriminated unions → Python `@dataclass`(frozen where pi is
  readonly) + `Literal` discriminators. `type` string literals are kept **identical**
  to pi (`"text"`, `"thinking"`, `"toolCall"`, `"start"`, `"text_delta"`,
  `"toolcall_end"`, `"done"`, `"error"`, …).
- `AssistantMessageEvent` is a union of small frozen dataclasses (one per `type`),
  each carrying `partial: AssistantMessage` (and `contentIndex`/`delta`/`content`/
  `toolCall`/`message`/`error`/`reason` as in pi). Field names use snake_case
  except the literal `type` values.
- `Usage` mirrors pi exactly: `input/output/cache_read/cache_write/total_tokens`
  + nested `cost{input,output,cache_read,cache_write,total}`.
- `Model` mirrors pi's fields (`id,name,api,provider,base_url,reasoning,
  thinking_level_map?,input,cost,context_window,max_tokens,headers?`). `compat`
  omitted (provider-specific; not needed for appv2-env).

### Streaming event protocol (must match pi exactly)

Order contract: `start` → per content block
`text_start|text_delta|text_end` / `thinking_start|thinking_delta|thinking_end`
/ `toolcall_start|toolcall_delta|toolcall_end` (each carries `content_index` and
a `partial: AssistantMessage` snapshot) → terminating `done` (reason
`stop|length|toolUse`, carries `message`) or `error` (reason `aborted|error`,
carries `error: AssistantMessage`). **Failures are encoded as a `done`/`error`
event, never raised** out of a returned stream.

Tool calls are `ToolCall` content blocks inside the `AssistantMessage`; arguments
accumulate via `toolcall_delta` (partial JSON) and finalize in `toolcall_end`.
Tool *results* are separate `ToolResultMessage`s produced by the agent layer
(sub-project 2), not by `ai`.

## Provider: appv2-env with real SSE (fresh, appv21-free)

`AppV2EnvProvider.stream(model, context, options)` returns an
`AssistantMessageEventStream` immediately and runs the request in a background
worker (thread), so the contract "never throw after returning" holds.

Transport: a **fresh** OpenAI/OpenRouter-compatible client built on **`httpx`**
(streaming `POST /chat/completions` with SSE), configured by the ported
`ai/env_config.py` which resolves `{enabled, api_key, model, base_url, timeout,
temperature, top_p, ...}` from `.env` + `os.environ` for the `APPV2_WORKER_LLM`
prefix (with `OPENROUTER_*`/`OPENAI_*` fallbacks), mirroring appv21's resolution
rules but as new appv22 code. The non-streaming `openrouter` SDK is **not** used.
Parse SSE chunks:

- `choices[].delta.content` → `text_start`/`text_delta`/`text_end`.
- `choices[].delta.reasoning` (when present) → `thinking_*`.
- `choices[].delta.tool_calls[]` → `toolcall_start`/`toolcall_delta`
  (argument fragments) / `toolcall_end`.
- final `finish_reason` → `done` reason mapping
  (`stop`→`stop`, `length`→`length`, `tool_calls`→`toolUse`).
- usage chunk → `AssistantMessage.usage` (fresh usage normalization ported from
  appv21's `_normalize_usage`).
- on exception / abort → `error` event with stopReason `error`/`aborted`.

When the config is disabled or missing an api key, the provider registration
yields a `NullProvider` (fresh port of appv21 `null_model`) whose `stream` emits a
single `error` event ("model transport not configured") rather than raising.

`context.tools` (pi `Tool[]`) is serialized to OpenRouter `tools` (function
schema). `context.system_prompt` → leading system message. `Message[]` →
OpenRouter chat messages (assistant tool calls and `toolResult` messages mapped
to OpenRouter `tool` role).

A `convert_messages` helper (mirrors pi providers' `transform-messages.ts` at the
needed subset) does Message[] → OpenRouter payload, and the SSE parser does
chunks → `AssistantMessageEvent`.

## appv21 removal checklist (this sub-project)

- Delete `_ensure_local_appv21_import_path` / `_discover_local_appv21_root` and the
  `import_module("appv21...")` calls from `appv22/providers/appv2_env.py`.
- Port `.env` loading + env→config resolution into `appv22/ai/env_config.py`
  (fresh; no `from appv21...`).
- Port the null/disabled behavior into `appv22/ai/providers` (fresh).
- Repoint the transitional `decide()` shim provider's transport to the new appv22
  client so the runtime keeps working without appv21.
- Grep gate: zero `appv21` / `appV2.1` references remain anywhere under `appV2.2/`.

## Transitional shim & deletion checkpoint

- Keep `appv22/providers/appv2_env.py` (`decide()` + decision schema) in this
  sub-project, but backed by the new appv22 transport (not appv21); the runtime
  still uses it.
- Deletion happens in sub-project 2 when `agent_loop` switches to `ai.stream_simple`.
  Recorded here so the checkpoint is explicit.

## Testing (TDD where practical)

New tests under `appV2.2/tests/test_ai_*.py`:

1. `EventStream`/`AssistantMessageEventStream`: push/iterate/`result()` resolves on
   `done`; `error` event resolves `result()` to the error message; no throw.
2. Event ordering: a faux provider emits `start → text_delta* → done`; assert the
   exact event sequence and final assembled `AssistantMessage`.
3. Tool-call assembly: `toolcall_start → toolcall_delta(json fragments) →
   toolcall_end` produces a `ToolCall` block with parsed `arguments`.
4. `stream_simple`/`complete_simple` resolve a provider via the api-registry and
   return the final message.
5. SSE parser unit test: feed canned OpenRouter SSE lines → expected event list
   (no network).
6. `is_context_overflow`: ported cases from `provider_errors.py` regressions.
7. Faux provider (`ai/providers/faux.py`, port of pi `faux.ts`) for use by later
   sub-projects' tests.

No real network in tests (faux provider + canned SSE).

## Verification

- `python -m pytest appV2.2/tests/test_ai_*.py` green.
- Mapping table (pi symbol → appv22 symbol) included in the implementation plan.
- Grep gate: `appv22/ai/` has no `import pi`/`import hermes`.

## Risks

- **Streaming transport correctness.** OpenRouter SSE tool-call delta framing
  varies; mitigate with the canned-SSE parser test and a synthesized-from-final
  fallback path if a chunk stream lacks deltas.
- **Async vs thread model.** appv22 today is synchronous. The `EventStream` is
  async-iterable; the agent loop (sub-project 2) will drive it. For this
  sub-project the provider runs the request in a worker thread and pushes events;
  `result()` is awaitable and also exposed via a sync `result_sync()` helper to
  keep current sync call sites usable until sub-project 2.
- **Scope creep.** Only the appv2-env provider is ported; resist adding others.

## Non-goals

- Rewiring the agent loop (sub-project 2).
- Model catalog generation, image APIs, OAuth, non-appv2-env providers.
