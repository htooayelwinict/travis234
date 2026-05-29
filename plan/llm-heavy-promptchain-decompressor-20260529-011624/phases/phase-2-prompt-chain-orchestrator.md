# Phase 2 — Prompt-Chain Orchestrator

## Goal

Implement the internal LLM prompt-chain orchestrator with coalesced validation, envelope assembly, and one bounded repair attempt.

## Status

Completed. Added the prompt-chain orchestrator with a coalesced `complete_json(...)` call, Pydantic validation, sanitized diagnostics, prompt redaction, and one repair attempt before raising a prompt-chain error.

2026-05-29 update: Prompt-chain prompts, sanitizers, envelope assembly, and diagnostics now use descriptive `complexity_hint`/`constraints`. The stage diagnostics include internal `assemble_envelope` and `validate_envelope`; legacy LLM `budget_hint`, `execution_hints`, and `observe_first` outputs are repaired/dropped before strict validation so planner-owned concepts do not cross the envelope boundary.

2026-05-29 follow-up: Removed scenario-specific Lighthouse/SDK runtime repair. The chain now relies on prompt outputs for rich semantic content and uses only generic validation, label sanitization, forbidden-field removal, and deduplication at the boundary. Verification: `uv run pytest -q` passes.

2026-05-29 coalesced update: Replaced the staged prompt chain with one `decompress_request` structured-output call. Validation failure triggers one `repair_decompressed_envelope` call, then raises `PromptChainError` if repair is invalid. Boundary cleanup preserves LLM semantic strings and no longer clamps intents/domains/risks/context/constraints to static label sets. Verification: `uv run pytest tests/test_decompressor.py tests/test_planner.py tests/test_graph.py -q` and `uv run pytest -q` pass.

## Files

- Add `app/decompressor/prompt_chain.py`
- Update `tests/test_decompressor.py`

## Tasks

1. Implement a chain component that accepts a `PromptChainModelClient`.
2. Build a coalesced prompt using redacted raw input.
3. Call `complete_json(...)` with `decompress_request`, prompt text, and `model_json_schema()`.
4. Validate responses with `model_validate_json(...)` and call `repair_decompressed_envelope` once after validation failure.
5. Canonicalize the Envelope boundary without static semantic label clamping.
6. Assemble an `Envelope` with runtime-owned `request_id` and original raw input.
7. Store only sanitized diagnostics in `Envelope.metadata`.
8. Raise a prompt-chain error when model calls or validation fail after repair.

## Risks

- Accidentally storing raw model responses or prompts in metadata.
- Making fallback too complex before basic whole-chain fallback works.

## Rollback

Remove `prompt_chain.py` and LLM-mode tests.

## Verification

```bash
uv run pytest tests/test_decompressor.py -q
```
