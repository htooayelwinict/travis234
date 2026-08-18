# Phase 3 — Provider Ownership and Wire Isolation

> **Required skills:** `superpowers:executing-plans`,
> `superpowers:test-driven-development`, `superpowers:systematic-debugging`, and
> `superpowers:verification-before-completion`.

**Goal:** Remove provider import cycles, split the monolithic transport owner into
bounded API-family modules, preserve exact provider wire behavior, and bound catalog
network/resource usage.

**Architecture:** Leaf contracts and supported-mode facts have no dependency on concrete
transports. `transports.py` remains a compatibility export/registry surface while
implementations move to `transport_families/`. Every family is pinned by sanitized
request/response/stream fixtures before moving.

**Do not modify:** `travis/agent/agent_loop.py`, session/TUI composition, provider IDs,
`api_mode` values or aliases, credential storage, retry ownership, event ordering.

---

## Task 3.1: Isolate provider contracts and supported-mode facts

**Files:**

- Create: `travis/ai/providers/provider_contracts.py`
- Create: `travis/ai/providers/provider_modes.py`
- Create: `travis/ai/providers/provider_profiles.py`
- Modify: `travis/ai/providers/base.py`
- Modify: `travis/ai/providers/__init__.py`
- Create: `tests/ai/providers/test_provider_contracts.py`
- Modify: `tests/ai/providers/test_provider_owners.py`
- Modify: `tests/test_provider_ownership_architecture.py`
- Modify: `pyrightconfig.json`, `ruff.toml`

- [ ] **Step 1: Add failing leaf-ownership tests**

Require:

- normalized response/tool/usage dataclasses and `ProviderTransport` protocol live in
  `provider_contracts.py`;
- `ProviderProfile` lives in `provider_profiles.py`;
- canonical modes and aliases live in `provider_modes.py`;
- those modules do not import `transports`, `transport_registry`, concrete families,
  runtime HTTP code, catalog, or application/session code;
- `base.py` and `providers.__init__` continue importing the existing public names;
- `ProviderProfile.transport_available` returns the same result for every known and
  unknown mode without importing concrete transports.

- [ ] **Step 2: Confirm RED**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/ai/providers/test_provider_contracts.py \
  tests/ai/providers/test_provider_owners.py \
  tests/test_provider_ownership_architecture.py
```

- [ ] **Step 3: Move declarations with compatibility re-exports**

Move, do not duplicate, the canonical dataclasses/protocol. Define:

```python
CANONICAL_API_MODES: frozenset[str]
API_MODE_ALIASES: Mapping[str, str]


def normalize_api_mode(value: str) -> str:
    return API_MODE_ALIASES.get(value, value)


def transport_mode_is_supported(value: str) -> bool:
    return normalize_api_mode(value) in CANONICAL_API_MODES
```

`ProviderProfile.transport_available` delegates to the pure supported-mode function.
`base.py` contains explicit imports and `__all__` only for compatibility plus any
remaining base-owned behavior.

- [ ] **Step 4: Run public import and type gates**

```bash
uv run --locked --all-extras --dev python -c \
  'from travis.ai.providers.base import ProviderProfile, ProviderTransport, NormalizedResponse; print("ok")'
uv run --locked --all-extras --dev pytest -q \
  tests/ai/providers/test_provider_contracts.py \
  tests/test_ai_provider_capabilities.py \
  tests/test_provider_ownership_architecture.py
uv run --locked --all-extras --dev pyright \
  travis/ai/providers/provider_contracts.py \
  travis/ai/providers/provider_modes.py \
  travis/ai/providers/provider_profiles.py
```

- [ ] **Step 5: Commit**

```bash
git add travis/ai/providers/provider_contracts.py \
  travis/ai/providers/provider_modes.py \
  travis/ai/providers/provider_profiles.py \
  travis/ai/providers/base.py travis/ai/providers/__init__.py \
  tests/ai/providers/test_provider_contracts.py \
  tests/ai/providers/test_provider_owners.py \
  tests/test_provider_ownership_architecture.py \
  pyrightconfig.json ruff.toml
git commit -m "refactor(providers): isolate provider contracts"
```

---

## Task 3.2: Capture sanitized golden wire fixtures

**Files:**

- Create: `tests/ai/providers/wire_fixtures.py`
- Create: `tests/fixtures/provider_wire/chat_completions.json`
- Create: `tests/fixtures/provider_wire/mistral.json`
- Create: `tests/fixtures/provider_wire/google.json`
- Create: `tests/fixtures/provider_wire/bedrock.json`
- Create: `tests/fixtures/provider_wire/anthropic.json`
- Create: `tests/fixtures/provider_wire/codex_responses.json`
- Create: `tests/fixtures/provider_wire/openai_responses.json`
- Create: `tests/fixtures/provider_wire/azure_responses.json`
- Expand: `tests/ai/providers/test_provider_characterization.py`
- Expand: `tests/test_subscription_provider_wire_compatibility.py`

- [ ] **Step 1: Define a deterministic fixture schema**

Each JSON file records:

```json
{
  "schemaVersion": 1,
  "apiMode": "canonical-mode",
  "endpointPath": "/expected",
  "requestCases": [],
  "responseCases": [],
  "streamCases": []
}
```

Case fields include input model facts, messages/tools/options, expected request body,
expected normalized response or event type/value list, and sanitized expected headers.
Use literal placeholders such as `<API_KEY>`, `<ACCOUNT_ID>`, `<SESSION_ID>`, and
`<REQUEST_ID>`. Reject fixture keys or values matching credential/token patterns.

- [ ] **Step 2: Add fixture validation tests**

Tests reject unknown schema versions, missing cases, unstable map ordering, absolute
private paths, authorization values other than placeholders, JWT-like text, and fixture
update modes controlled by ambient environment variables.

- [ ] **Step 3: Populate fixtures from explicit current expectations**

Use the current implementation to inspect behavior, but hand-author reviewed expected
JSON. Cover at minimum:

- system/developer prompts;
- text, image, thinking, tool call, and tool result messages;
- omitted versus explicit sampling fields;
- reasoning budgets/effort;
- prompt cache/session keys;
- Mistral tool IDs;
- Anthropic tool choice and thinking constraints;
- Codex/OpenAI/Azure response input and instruction differences;
- usage and finish-reason normalization;
- malformed/partial streaming errors and final done event.

- [ ] **Step 4: Run fixtures against the old monolith**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/ai/providers/test_provider_characterization.py \
  tests/test_subscription_provider_wire_compatibility.py \
  tests/test_ai_provider_capabilities.py
```

Expected: GREEN before any transport class moves.

- [ ] **Step 5: Commit**

```bash
git add tests/ai/providers/wire_fixtures.py \
  tests/fixtures/provider_wire \
  tests/ai/providers/test_provider_characterization.py \
  tests/test_subscription_provider_wire_compatibility.py
git commit -m "test(providers): pin sanitized wire contracts"
```

---

## Task 3.3: Introduce the transport registry and compatibility surface

**Files:**

- Create: `travis/ai/providers/transport_registry.py`
- Create: `travis/ai/providers/transport_families/__init__.py`
- Create: `travis/ai/providers/transport_families/unsupported.py`
- Modify: `travis/ai/providers/transports.py`
- Modify: `travis/ai/providers/__init__.py`
- Create: `tests/ai/providers/test_transport_registry.py`
- Modify: `tests/test_provider_ownership_architecture.py`

- [ ] **Step 1: Add failing registry tests**

Assert every canonical mode and alias returns the same concrete class and API/endpoint
facts as the baseline. Unknown modes return `UnsupportedTransport` preserving the
normalized unknown string. Registry order is deterministic and duplicate registration
fails at import/test construction time.

- [ ] **Step 2: Implement an immutable default registry**

Define a mapping from canonical modes to singleton transports plus a pure
`get_transport(api_mode)` function. Do not add global mutation or runtime extension
registration. Move `UnsupportedTransport` to its family module.

Keep `travis.ai.providers.transports.get_transport` and every currently imported class
available through explicit compatibility imports. At this point the large concrete
classes may still remain in `transports.py`; the registry boundary must be green before
they move.

- [ ] **Step 3: Verify**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/ai/providers/test_transport_registry.py \
  tests/test_ai_provider_capabilities.py \
  tests/test_provider_ownership_architecture.py
```

- [ ] **Step 4: Commit**

```bash
git add travis/ai/providers/transport_registry.py \
  travis/ai/providers/transport_families \
  travis/ai/providers/transports.py travis/ai/providers/__init__.py \
  tests/ai/providers/test_transport_registry.py \
  tests/test_provider_ownership_architecture.py
git commit -m "refactor(providers): add explicit transport registry"
```

---

## Task 3.4: Extract chat-completions and Mistral families

**Files:**

- Create: `travis/ai/providers/transport_families/chat_completions.py`
- Create: `travis/ai/providers/transport_families/mistral.py`
- Modify: `travis/ai/providers/transports.py`
- Modify: `travis/ai/providers/transport_registry.py`
- Modify: provider characterization/parity/replay tests

- [ ] Characterize any uncovered helper before moving it.
- [ ] Move `ChatCompletionsTransport` and only its genuinely shared pure helpers to the
  chat family module.
- [ ] Move `MistralConversationsTransport` and Mistral-only normalization helpers to the
  Mistral module; import shared chat behavior explicitly.
- [ ] Preserve compatibility imports from `transports.py`.
- [ ] Run chat/Mistral golden fixtures, replay-neutrality, provider parity, images,
  tools, usage, and error tests.
- [ ] Run scoped Ruff/Pyright for the new modules.
- [ ] Commit as `refactor(providers): isolate chat and mistral transports`.

---

## Task 3.5: Extract Google and Bedrock families

**Files:**

- Create: `travis/ai/providers/transport_families/google.py`
- Create: `travis/ai/providers/transport_families/bedrock.py`
- Modify: `travis/ai/providers/transports.py`
- Modify: `travis/ai/providers/transport_registry.py`
- Modify focused characterization tests

- [ ] Move `GoogleGenerativeAITransport` and `GoogleVertexTransport` with Google-only
  message/tool/request/normalization helpers.
- [ ] Move `BedrockConverseStreamTransport` with Bedrock-only helpers.
- [ ] Preserve Vertex authorization and Bedrock streaming ownership outside these pure
  request/normalization classes where it currently lives.
- [ ] Run golden, Vertex auth, Bedrock stream, image, tool, and usage tests.
- [ ] Run scoped Ruff/Pyright.
- [ ] Commit as `refactor(providers): isolate google and bedrock transports`.

---

## Task 3.6: Extract Anthropic messages

**Files:**

- Create: `travis/ai/providers/transport_families/anthropic.py`
- Modify: `travis/ai/providers/transports.py`
- Modify: `travis/ai/providers/transport_registry.py`
- Modify Anthropic/capability/subscription characterization tests

- [ ] Pin all sampling, thinking, cache, tool-reference, tool-choice, Claude Code
  identity, image, and usage cases before the move.
- [ ] Move `AnthropicMessagesTransport` and Anthropic-only pure helpers.
- [ ] Decompose the high-complexity `build_kwargs` into pure helpers for sampling,
  thinking, tools, caching, and system blocks, with direct unit tests for each.
- [ ] Do not change OAuth, request execution, SSE parsing, retry, or error formatting.
- [ ] Run all Anthropic golden and subscription wire tests, then scoped Ruff/Pyright.
- [ ] Commit as `refactor(providers): isolate anthropic transport`.

---

## Task 3.7: Extract Codex, OpenAI, and Azure Responses families

**Files:**

- Create: `travis/ai/providers/transport_families/responses.py`
- Create: `travis/ai/providers/transport_families/azure_responses.py`
- Modify: `travis/ai/providers/transports.py`
- Modify: `travis/ai/providers/transport_registry.py`
- Modify responses/subscription/Codex characterization tests

- [ ] Pin developer/system instruction selection, response input conversion, deferred
  tools, reasoning items, prompt cache keys, service tier, sampling, deployment mapping,
  and normalized tool-call metadata.
- [ ] Move `CodexResponsesTransport` and `OpenAIResponsesTransport` to `responses.py`.
- [ ] Move `AzureOpenAIResponsesTransport` and Azure-only URL/deployment behavior to
  `azure_responses.py`.
- [ ] Break high-complexity body construction into tested pure helpers without changing
  field omission or order-insensitive JSON values.
- [ ] Preserve current public class imports from `transports.py`.
- [ ] Run all golden, Codex reliability, subscription wire, provider capability,
  translation, and stream tests.
- [ ] Commit as `refactor(providers): isolate responses transports`.

---

## Task 3.8: Finish monolith reduction and remove provider cycles

**Files:**

- Modify: `travis/ai/providers/transports.py`
- Modify: `travis/ai/providers/base.py`
- Modify: `travis/ai/providers/transport_registry.py`
- Modify: `travis/ai/providers/transport_families/__init__.py`
- Modify: architecture tests, Ruff, and Pyright configs

- [ ] **Step 1: Make `transports.py` a bounded compatibility module**

It contains explicit imports/re-exports and `get_transport`, not concrete transport
implementations. Target below 300 lines.

- [ ] **Step 2: Add cycle detection**

Architecture tests build the provider-module import graph and fail any strongly
connected component larger than one. Specifically prove contracts/profiles/modes do not
depend on concrete families and families do not import the compatibility module.

- [ ] **Step 3: Run all provider suites and static analysis**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/ai/providers \
  tests/test_ai_provider_capabilities.py \
  tests/test_provider_ownership_architecture.py \
  tests/test_provider_replay_neutrality.py \
  tests/test_subscription_provider_wire_compatibility.py \
  tests/test_codex_reliability_contract.py \
  tests/test_ai_images.py
uv run --locked --all-extras --dev ruff check travis/ai/providers
uv run --locked --all-extras --dev pyright
```

- [ ] **Step 4: Commit**

```bash
git add travis/ai/providers tests/ai/providers \
  tests/test_provider_ownership_architecture.py ruff.toml pyrightconfig.json
git diff --cached --name-only
git commit -m "refactor(providers): complete bounded transport ownership"
```

Inspect and narrow staging to provider-owned paths.

---

## Task 3.9: Bound remote model catalogs and refresh concurrency

**Files:**

- Create: `travis/ai/providers/model_catalog_fetch.py`
- Create: `tests/ai/providers/test_model_catalog_fetch.py`
- Modify: `travis/ai/providers/provider_profiles.py`
- Modify: `travis/ai/providers/base.py`
- Modify: `travis/ai/models.py`
- Modify: `tests/test_models_runtime.py`

- [ ] **Step 1: Write failing URL/size/concurrency tests**

Require:

- HTTPS remote model endpoints are accepted;
- loopback HTTP (`localhost`, `127.0.0.1`, `::1`) is accepted for development;
- remote plain HTTP, file, data, FTP, and scheme-relative URLs are rejected before I/O;
- bodies larger than `2 * 1024 * 1024` bytes abort without parsing;
- malformed JSON and non-list/data shapes return a sanitized diagnostic/`None` under
  the current compatibility contract;
- refresh-all never runs more than four provider refreshes concurrently;
- output model/provider order remains provider-registration order, not completion order;
- one provider failure does not poison refresh-all.

- [ ] **Step 2: Confirm RED**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/ai/providers/test_model_catalog_fetch.py \
  tests/test_models_runtime.py -k refresh
```

- [ ] **Step 3: Implement bounded fetch and worker pool**

Read in chunks up to a named `MAX_MODEL_CATALOG_BYTES` and reject the next byte. Validate
initial and redirected effective URLs under the same policy. Preserve `ProviderProfile`
method compatibility by delegating to the leaf fetcher.

Replace one-thread-per-provider refresh with `ThreadPoolExecutor(max_workers=min(4,
provider_count))`, submit in registration order, settle all futures, and preserve
best-effort failure isolation. Do not introduce this executor on model streaming.

- [ ] **Step 4: Run focused and security tests**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/ai/providers/test_model_catalog_fetch.py \
  tests/test_models_runtime.py \
  tests/test_catalog_generation.py \
  tests/test_ai_validation.py \
  tests/ai/providers/test_provider_error_redaction.py
```

- [ ] **Step 5: Commit**

```bash
git add travis/ai/providers/model_catalog_fetch.py \
  travis/ai/providers/provider_profiles.py travis/ai/providers/base.py \
  travis/ai/models.py tests/ai/providers/test_model_catalog_fetch.py \
  tests/test_models_runtime.py
git commit -m "fix(providers): bound model catalog discovery"
```

---

## Task 3.10: Phase 3 qualification

- [ ] Run the master checkpoint, full root suite, coverage, adapter suite, npm tests,
  root/adapter builds, Twine checks, and clean-wheel import smoke.
- [ ] Run every provider golden fixture twice to expose accidental mutable singleton
  state.
- [ ] Install the exact root wheel and run TUI scenarios for provider/model discovery,
  offline model selection, failed login redaction, faux streaming with tools/reasoning,
  model refresh, and normal shutdown.
- [ ] If the user later explicitly supplies live credentials for qualification, run a
  separate selected-provider smoke without logging keys; live credentials are not
  required for this phase to pass.
- [ ] Record each scenario PASS/FAIL, provider family fixture counts, response-size and
  concurrency results, and the protected hash.
- [ ] Commit as `docs: record phase 3 provider qualification`.
- [ ] Report and stop for review before Phase 4.
