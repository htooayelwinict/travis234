# Subscription Provider Wire Compatibility Design

Date: 2026-07-17
Status: approved for implementation planning; no runtime implementation in this commit

## Decision summary

Travis234 should fix the confirmed Codex, Claude Code, and Copilot-Claude request defects at the provider wire boundary only.

The selected design combines:

1. Pi-style model compatibility flags for static, model-specific restrictions.
2. Small final-body guards inside the existing Codex and Anthropic transports for state-dependent restrictions.
3. Existing generation-capability translation for provider vocabulary differences that are known before request construction.

This design deliberately does not modify the agent loop, `AgentSession`, context construction, context envelopes, compaction, iteration budgeting, tool scheduling, model switching, TUI state ownership, or persisted generation parameters.

## Why this work is needed

The current provider pipeline correctly builds Travis's system prompt and session generation parameters, but several subscription-provider transports produce invalid or incomplete wire requests.

A read-only reproduction against the current tree produced:

```text
anthropic claude-sonnet-5
  temperature=0.2, top_p=0.8, thinking={type: disabled}

anthropic claude-fable-5
  temperature=0.2, top_p=0.8, thinking={type: disabled}

anthropic claude-haiku-4-5 with thinking and max_tokens=1500
  top_p=0.8, thinking={type: enabled, budget_tokens: 476}, tool_choice={type: any}

github-copilot claude-sonnet-5
  temperature=0.2, top_p=0.8, thinking={type: disabled}

openai-codex gpt-5.4 with SYSTEM_SENTINEL
  instructions="You are a helpful assistant."
  SYSTEM_SENTINEL absent from input
```

These are request-construction defects, not session or context-envelope defects.

## Evidence cross-check

### Context7 and official Anthropic documentation

Context7 resolved the current Claude API documentation and confirmed:

- New Claude models reject non-default sampling controls.
- Fable 5 uses always-on adaptive thinking and does not accept `thinking: {"type": "disabled"}`.
- Manual thinking requires at least 1,024 `budget_tokens`.
- Thinking permits only `tool_choice` values `auto` or `none`; forced tool use is rejected.

The official Claude API reference is more precise:

- Models released after Claude Opus 4.6 reject non-default `temperature`; only the default-compatible value remains accepted.
- Those models reject non-default `top_p`; only default-compatible values remain accepted.
- With manual thinking, `top_p` must be between 0.95 and 1.
- Manual thinking requires a budget of at least 1,024 tokens and a budget lower than `max_tokens`.

References:

- https://platform.claude.com/docs/en/api/typescript/messages/create
- https://platform.claude.com/docs/en/build-with-claude/extended-thinking
- https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking
- https://platform.claude.com/docs/en/about-claude/models/whats-new-sonnet-5
- https://platform.claude.com/docs/en/release-notes/overview

### Official OpenAI documentation

The Responses API defines `instructions` as the system/developer instruction supplied to the current response. It is not inherited through `previous_response_id`, so a client using response continuation must provide the instructions on every request.

References:

- https://developers.openai.com/api/reference/resources/responses/methods/create
- https://developers.openai.com/api/docs/guides/text

The public Responses API supports a broader schema than the ChatGPT subscription Codex endpoint. The narrower Codex generation allowlist already committed in `e69b370` remains the endpoint-specific protection. This design does not broaden Codex sampling support merely because the public Responses schema contains similarly named fields.

### Official GitHub Copilot documentation

The public Copilot CLI/SDK surface documents model selection and reasoning effort. It does not publish a stable raw contract for forwarding `temperature`, `top_p`, penalties, or other provider-native sampling fields to Copilot's internal subscription endpoints.

References:

- https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference
- https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference

The GitHub Models inference REST API is a separate product and endpoint. Its generic sampling schema must not be treated as proof of Copilot subscription endpoint compatibility.

### Bundled Pi source

The local Pi checkout was current at commit `3da591ab74ab9ab407e72ed882600b2c851fae21` when inspected.

Relevant Pi behavior:

- `openai-codex-responses.ts` sets `instructions` directly from `context.systemPrompt`.
- `anthropic-messages.ts` suppresses temperature during thinking and when the model compatibility flag disables it.
- It emits `thinking: disabled` only when `thinkingLevelMap.off` is not `null`.
- It uses `forceAdaptiveThinking` to choose adaptive rather than manual budget-based thinking.

Pi is a design oracle, not an unquestioned source of truth. Pi still exposes Codex temperature and can create a too-small Anthropic manual thinking budget after output clamping. Travis should preserve Pi's good extension-flag strategy without copying those defects.

## Immutable boundaries

The implementation must not modify:

- `travis/coding_agent/agent_session.py` or any agent-loop owner
- session persistence or session generation-parameter snapshots
- system-prompt construction
- context estimation, context envelopes, token accounting, or compaction
- automatic or `/compact` behavior
- tool execution order, bounded parallelism, or iteration budgets
- TUI command state or `/params` persistence
- model-switch lifecycle behavior

The user's configured `/params` values remain session values. A provider transport may omit or translate a value that its target endpoint cannot accept, but it must not erase or rewrite the session snapshot.

## Options considered

### Option A — compatibility flags plus final wire guards (selected)

Static model restrictions live in the existing model `compat` metadata. State-dependent combinations are enforced after request overrides are merged into the wire body.

Advantages:

- Matches Pi's extensible model-flag strategy.
- Keeps model facts out of the agent/session layers.
- Prevents `request_overrides` from bypassing safety checks.
- Allows exact regression tests against final request bodies.
- Limits runtime changes to the two existing provider modules.

Cost:

- New model entries must carry accurate compatibility flags.
- Some transport-only omissions cannot use the existing early warning callback without broadening the provider-request interface.

### Option B — hard-coded model IDs inside transports

Each transport would recognize names such as `claude-sonnet-5` and `claude-fable-5` directly.

Advantages:

- Very few edited files.
- Easy to understand for the first patch.

Rejected because:

- Model aliases and Copilot punctuation already differ.
- Facts become duplicated across transport logic and the catalog.
- Future model additions silently regress until code is edited.

### Option C — refactor the generation-capability pipeline

Pass the entire model and thinking state into `build_generation_payload`, return a fully normalized request plus warnings, and make transports mostly mechanical.

Advantages:

- Centralized warnings and policy.
- Clean conceptual ownership for a future redesign.

Rejected for this fix because:

- It changes `provider_request.py`, a shared request path for every provider.
- It expands the regression surface far beyond the subscription providers.
- It is not microscopic and conflicts with the user's core-layer safety constraint.

## Selected architecture

### Layer 1: existing capability translation

`travis/ai/providers/capabilities.py` remains the place for facts known from provider/API mode alone.

The Anthropic branch will make only two narrow corrections:

1. Translate generic `tool_choice=required` to Anthropic `{"type": "any"}`.
2. For the direct Anthropic and GitHub Copilot providers, drop a temperature outside Anthropic's `0..1` range and emit the existing `ProviderParamWarning`.

Other valid Anthropic strings remain `auto`, `any`, and `none`. An unrecognized string is dropped with a warning rather than sent as an invalid Anthropic `type`.

No parser, `/params`, session, or persistence behavior changes.

### Layer 2: model compatibility metadata

The pinned model catalog will use existing Pi-style flags:

- `supportsTemperature: false`
- `supportsTopP: false`
- existing `forceAdaptiveThinking: true`
- existing `thinkingLevelMap.off: null`

The patch will update only the current subscription entries whose restrictions are documented:

| Provider | Route | Models | Metadata change |
|---|---|---|---|
| `anthropic` | `anthropic-messages` | Fable 5, Opus 4.7, Opus 4.8, Sonnet 5 | sampling flags as required |
| `github-copilot` | `anthropic-messages` | Opus 4.7, Opus 4.8, Sonnet 5 | sampling flags as required |

`github-copilot/claude-fable-5` currently uses `openai-completions`; it is intentionally excluded because GitHub does not publish the raw compatibility contract for that route.

No OpenRouter, Bedrock, Vercel, OpenCode, generic OpenAI-compatible, or other provider catalog entries are changed by this work.

### Layer 3: final Anthropic wire guard

`AnthropicMessagesTransport.build_kwargs` already owns the final Anthropic body. A small pure helper should run after `request_overrides` is merged so user overrides cannot reintroduce an invalid field.

The helper enforces these invariants:

1. Remove `temperature` when `supportsTemperature` is false.
2. Remove `top_p` when `supportsTopP` is false.
3. When manual thinking is active, remove `top_p` values below 0.95.
4. When any thinking mode is active, translate forced tool choices (`any` or named `tool`) to `auto`; preserve `auto` and `none`.
5. Do not emit `thinking: disabled` when the model explicitly maps `off` to `null`.
6. Before emitting manual thinking, reject a `max_tokens` value below 2,048 with a clear local `ValueError`. This preserves Travis's existing 1,024-token answer reserve while guaranteeing Anthropic's 1,024-token minimum thinking budget.

The local error is preferred to silently raising `max_tokens`, silently disabling thinking, or sending an invalid remote request.

The helper receives only the already-resolved body, compatibility mapping, thinking state, and target-model metadata. It does not receive or mutate a session object or context envelope.

### Layer 4: Codex instructions repair

`CodexResponsesTransport.build_kwargs` will resolve instructions in this order:

1. `context.system_prompt` when a native context is available.
2. The first non-empty `system` or `developer` message for compatibility callers without native context.
3. `"You are a helpful assistant."` only when neither source exists.

The input conversion continues using `include_system_prompt=False`, so the system prompt appears once in `instructions` and is not duplicated in `input`.

This exactly addresses the confirmed root cause: the transport currently scans only `system`, while Travis translates the prompt to a `developer` role before request construction. It also matches Pi's direct use of `context.systemPrompt`.

The same body builder feeds Codex SSE and WebSocket execution, so no runtime/session/WebSocket ownership changes are required.

### Existing Codex parameter guard

Commit `e69b370` already:

- drops unsupported Codex subscription fields in `build_generation_payload`
- warns for explicit unsupported `/params`
- applies a final transport backstop after request overrides
- preserves generic OpenAI Responses behavior

This design retains that implementation unchanged. The new Codex work is only the system-instructions repair and its regression tests.

## Copilot containment policy

Copilot changes are deliberately route-scoped:

- Claude models using `anthropic-messages` inherit only the documented Anthropic wire invariants through explicit catalog flags.
- Copilot GPT models using `openai-responses` are unchanged.
- Copilot models using `openai-completions`, including Fable 5, are unchanged until authenticated evidence proves a specific incompatibility.
- Dynamic `/models` metadata is not promoted into a new runtime capability engine.
- The GitHub Models REST schema is not used as a proxy for Copilot subscription behavior.

This preserves existing Copilot behavior wherever the contract is undocumented.

## Error and warning behavior

- Provider/API-mode facts known in `capabilities.py` use the existing warning callback.
- Model/thinking combination guards act only on the final wire body and do not rewrite `/params` state.
- Invalid manual-thinking output budgets fail locally with a provider-specific message before network I/O.
- No retry, fallback, compaction, or model-switch behavior is added.

## Regression design

Every implementation change starts with a failing test.

### Capability tests

Add focused tests for:

- Anthropic `required -> any` translation.
- Invalid Anthropic temperature range being dropped only for `anthropic` and `github-copilot` Anthropic routes.
- Unknown Anthropic tool-choice strings being dropped with a warning.
- Existing OpenRouter and generic provider behavior remaining unchanged.

### Anthropic wire tests

Add final-body tests for:

- Sonnet 5 and Opus 4.7/4.8 omitting both sampling fields.
- Fable 5 omitting sampling fields and not emitting `thinking: disabled` when off is requested.
- Older/manual-thinking models dropping `top_p < 0.95`.
- Manual thinking preserving `top_p` in the valid range when the model supports it.
- Thinking converting forced tool choice to `auto` while preserving `none`.
- Manual thinking below the valid output budget failing before network I/O.
- Claude Code OAuth still prepending its required identity block and retaining the Travis system prompt.

### Codex wire tests

Add tests proving:

- `context.system_prompt` is copied exactly to `instructions`.
- the prompt is absent from `input`, preventing duplication.
- a `developer` message is accepted by the context-free compatibility path.
- the default fallback is used only when no prompt exists.
- the same request body contract is valid for the existing SSE/WebSocket execution path.

### Copilot boundary tests

Add tests proving:

- Copilot Sonnet 5 on `anthropic-messages` applies the sampling guard.
- Copilot GPT Responses retains its current request fields.
- Copilot Fable 5 on `openai-completions` is unchanged.

### Catalog tests

Pin the exact compatibility flags for the touched direct Anthropic and Copilot-Claude entries. This catches future catalog refresh drift without introducing runtime model-name checks.

## Surgical file allowlist

Runtime implementation may modify only:

- `travis/ai/providers/capabilities.py`
- `travis/ai/providers/transports.py`
- `travis/ai/builtin_models.json`

Regression implementation may modify only:

- `tests/test_ai_provider_capabilities.py`
- `tests/test_reference_runtime_contract.py`
- `tests/test_catalog_generation.py` or one new provider-catalog test module if isolation is clearer

Documentation may modify this design, its implementation plan, and the final verification record.

Any need to modify `provider_request.py`, agent/session files, context/compaction files, model registry ownership, or TUI lifecycle files is a stop condition requiring a new design review.

## Verification gates

After implementation:

1. Run the focused provider and catalog tests.
2. Run the complete Python test suite.
3. Run npm launcher tests and pack dry-run.
4. Build wheel and sdist.
5. Run the acceptance verifier.
6. Build the release container without cache and run the production container smoke test.
7. If authenticated provider accounts are available, perform one minimal non-destructive TUI turn each for Codex, Claude Code, and Copilot without printing credentials or request headers.

Live provider checks supplement but do not replace deterministic wire-body tests.

## Rollback

The design is easy to reverse because it has no migrations and no persisted-state changes:

- revert the provider compatibility commit
- restore the touched catalog flags
- no session cleanup, context migration, or user-state conversion is required

The pre-design baseline is commit `e69b370`.

## Self-review

- No placeholder sections remain.
- Static restrictions are separated from state-dependent restrictions.
- Public OpenAI Responses capability is not conflated with the private Codex subscription endpoint.
- GitHub Models is not conflated with Copilot subscription endpoints.
- Pi parity is used only where Pi behavior is correct and documented.
- Copilot undocumented routes are explicitly unchanged.
- Agent loop, sessions, context envelopes, and compaction are outside the file allowlist.
- The design contains no implementation code changes.
