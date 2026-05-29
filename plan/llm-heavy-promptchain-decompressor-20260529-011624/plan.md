# Implementation Plan: LLM Heavy Prompt-Chain Decompressor

## Goal

Implement an injectable LLM-only `DecompressorRuntime` that preserves the existing `DecompressorRuntime.run(user_input: str) -> Envelope` boundary and existing LangGraph topology without deterministic/static Envelope generation.

## Acceptance criteria

- `DecompressorRuntime(model_client=...)` or an equivalent explicit injection enables the LLM prompt-chain path without requiring provider SDK dependencies in the decompressor runtime.
- `DecompressorRuntime()` with no constructor arguments fails fast because the runtime is LLM-only.
- The prompt-chain path validates coalesced structured output through Pydantic before assembling the final `Envelope`.
- Open-ended semantic strings from the LLM are preserved; boundary cleanup only deduplicates, strips planner/kernel leaks, removes unsafe assumptions, clamps confidence and complexity, and guards underspecified pronoun-only input.
- If any model stage fails with invalid JSON, schema validation errors, exceptions, or timeout-like errors after one repair attempt, the runtime raises a prompt-chain error instead of creating a static fallback Envelope.
- The graph remains unchanged in topology: `decompressor_node -> planner_node -> worker_kernel_node -> END`.
- Tests use fake model clients with canned JSON and never call live model providers.
- Model prompts redact common API key, token, password, and secret patterns before calling the injected client.
- `Envelope.metadata` may contain sanitized chain diagnostics such as mode, stage names, and fallback names, but never raw prompts, full model responses, credentials, or large file contents.

2026-05-29 boundary update: The requested refactor in `research/decompressor-envelope-boundary-20260529-194314.md` has been implemented on top of this completed plan. The active envelope boundary is now descriptive-only and LLM-only: `constraints` replaces `execution_hints`, `complexity_hint` replaces `budget_hint`, planner/kernel fields are forbidden at the `Envelope` boundary, and runtime code no longer injects deterministic/static scenario semantics.

2026-05-29 coalesced update: The multi-stage prompt chain has been replaced by a single `decompress_request` structured-output call plus one optional `repair_decompressed_envelope` call after validation failure. Static label taxonomy code was removed from runtime semantics. Verification: `uv run pytest tests/test_decompressor.py tests/test_planner.py tests/test_graph.py -q` and `uv run pytest -q` pass.

## Existing patterns

- `app/decompressor/runtime.py` now owns only LLM prompt-chain wiring and request ID creation.
- `app/decompressor/prompt_chain.py` now performs one coalesced `decompress_request` model call and one bounded repair call only when validation fails.
- `app/schemas.py` defines an enriched descriptive `Envelope` with `user_goal`, `context_needed`, `constraints`, `complexity_hint`, `ambiguity`, `assumptions`, and `metadata`.
- `app/planner/selector.py` chooses planners from semantic envelope fields such as `input_type`, `intents`, `domains`, `risks`, `constraints`, and `confidence`.
- `app/graph.py` has thin top-level nodes and module-level runtime instances; it currently calls only `_decompressor_runtime.run(...)` and serializes the returned envelope.
- Tests in `tests/test_decompressor.py`, `tests/test_planner.py`, and `tests/test_graph.py` use fake LLM clients with coalesced `decompress_request` JSON instead of deterministic decompressor behavior.
- `pyproject.toml` already depends on `pydantic>=2.0`; use Pydantic v2 APIs such as `model_validate_json`, `model_validate`, and `model_json_schema`.

## Files to change

### Primary implementation files

- `app/decompressor/runtime.py` — LLM-only constructor injection and environment wiring.
- `app/decompressor/contracts.py` — coalesced `DecompressedEnvelope` structured-output model and the minimal model-client interface.
- `app/decompressor/canonicalize.py` — boundary cleanup for leak stripping, deduplication, unsafe-assumption removal, confidence/complexity clamping, and underspecified-input guards.
- `app/decompressor/prompt_chain.py` — coalesced prompt-chain orchestrator, prompt construction, JSON validation, assembly, bounded repair, and diagnostics.
- `app/decompressor/redaction.py` — new secret/token redaction helpers for prompt inputs.
- `app/decompressor/__init__.py` — export only stable objects if needed; avoid exposing internal implementation details unnecessarily.

### Tests

- `tests/test_decompressor.py` — use fake clients for all decompressor behavior tests.
- `tests/test_planner.py` — add or adjust selector-oriented tests so LLM envelopes route through semantic planner selection.
- `tests/test_graph.py` — add a guard that default graph invocation does not require or invoke a model client, while keeping node topology assertions.

### Files expected to remain topology-stable

- `app/graph.py` — topology remains unchanged; accepts optional test injection for the LLM runtime/client factory.
- `app/schemas.py` — no schema changes expected; only change if implementation uncovers a missing field that planners actually consume.
- `app/planner/selector.py` — no structural change expected; may add tests around low-confidence or invalid hints if not already covered.
- `pyproject.toml` — no provider SDK dependency expected for the minimal injectable protocol approach.

## Phase plan

### Phase 1 — Internal contracts, labels, redaction, and fake-client test scaffolding

Create the internal structured-output model, model-client protocol, redaction helper, and fake client patterns in tests.

Independent verification:

- `uv run python -c "from app.decompressor.contracts import RequestClassification; print(RequestClassification.model_json_schema()['title'])"`
- `uv run pytest tests/test_decompressor.py -q`

Rollback: remove the new internal decompressor modules and any new tests added for them.

### Phase 2 — Prompt-chain orchestrator with validation and bounded repair

Implement the internal LLM prompt-chain component that executes a coalesced `complete_json(...)` call, validates with Pydantic, assembles an `Envelope`, and records sanitized diagnostics. Use one repair call only after validation failure.

Independent verification:

- `uv run pytest tests/test_decompressor.py -q`

Rollback: remove `app/decompressor/prompt_chain.py` and restore tests to deterministic-only coverage.

### Phase 3 — Backward-compatible `DecompressorRuntime` wiring

Add constructor injection to `DecompressorRuntime` and require an explicit model client or chain. No no-argument deterministic mode remains.

Independent verification:

- `uv run pytest tests/test_decompressor.py tests/test_planner.py -q`

Rollback: revert `app/decompressor/runtime.py` constructor/routing changes while keeping contract modules if useful.

### Phase 4 — Integration and graph-boundary protection tests

Verify LLM envelopes flow safely into planner selection through semantic fields, and graph tests remain deterministic with no implicit model calls.

Independent verification:

- `uv run pytest tests/test_graph.py tests/test_planner.py -q`
- `uv run pytest -q`

Rollback: revert added integration tests and any optional graph injection changes; default graph should remain usable with deterministic decompressor.

### Phase 5 — Documentation and operational notes

Document the mode boundary, injection contract, no-live-model test rule, redaction policy, metadata policy, and fallback behavior in code docstrings or a small durable note if project docs exist.

Independent verification:

- `uv run pytest -q`

Rollback: revert documentation-only changes.

## Risks and unknowns

- **Provider semantics are unspecified:** Plan around a minimal protocol, not a concrete SDK. Provider configuration should live outside the decompressor.
- **Latency/cost of heavy chaining:** Multiple model calls can be expensive; keep the feature explicit/injected and consider future gating or shadow mode.
- **Prompt injection:** User input may attempt to override JSON/schema instructions. Pydantic validation and allowed-label clamps must be authoritative.
- **Secret leakage:** Redaction must happen before model calls, and metadata must never persist raw prompts or full responses.
- **Prompt-chain failures:** A stage failure after repair raises instead of fabricating an Envelope. Callers must surface the error cleanly.
- **Test brittleness:** Fake-client tests assert exact labels emitted by canned LLM responses.
- **Graph singleton runtime:** `app/graph.py` currently creates module-level runtimes. Avoid changing this unless dependency injection is needed for future integration tests.
- **Request IDs:** Preserve runtime-owned request ID creation; never trust model-generated IDs.

## Verification commands

Run from repository root:

```bash
uv run python -c "from app.decompressor.contracts import RequestClassification; print(RequestClassification.model_json_schema()['title'])"
uv run pytest tests/test_decompressor.py -q
uv run pytest tests/test_planner.py -q
uv run pytest tests/test_graph.py -q
uv run pytest -q
```

## Recommended first implementation step

Begin with Phase 1 by creating `app/decompressor/contracts.py`, `app/decompressor/labels.py`, and `app/decompressor/redaction.py`, plus narrowly scoped unit tests for Pydantic stage validation, allowed-label clamping, and secret redaction. This creates safe boundaries before adding any model-call orchestration.

## Detailed order of operations

1. Add an internal Pydantic structured-output model: `DecompressedEnvelope`.
2. Add `PromptChainModelClient` protocol with `complete_json(*, stage: str, prompt: str, schema: dict[str, Any]) -> str`.
3. Add boundary canonicalization for forbidden-field removal, deduplication, confidence/complexity clamping, unsafe assumption removal, and underspecified input guards.
4. Add redaction helper and tests for token/password/API-key-like strings.
5. Add `LLMPromptChainDecompressor` or equivalent internal component that accepts a model client.
6. Implement coalesced and repair calls using `Model.model_json_schema()` and `Model.model_validate_json(...)`.
7. Assemble final `Envelope` using runtime-owned `request_id` and original raw input, then validate with `Envelope.model_validate(...)`.
8. Wire `DecompressorRuntime.__init__` so injected model-client behavior is explicit and no-argument construction fails fast.
9. Add fake-client tests for valid coalesced responses, invalid JSON/repair failure, open-ended semantic preservation, vague mutation ambiguity, redaction, and prompt-injection resistance.
10. Run full verification and update documentation/comments.

## Plan folder path

`plan/llm-heavy-promptchain-decompressor-20260529-011624/`
