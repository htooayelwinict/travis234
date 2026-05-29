# Phase 3 — Runtime Wiring

## Goal

Wire LLM prompt-chain mode into `DecompressorRuntime` without no-argument deterministic behavior.

## Status

Completed. `DecompressorRuntime(model_client=...)` or `DecompressorRuntime(prompt_chain=...)` is required; no-argument construction fails fast because the decompressor is now LLM-only.

2026-05-29 update: Removed deterministic/static Envelope assembly. The runtime owns request IDs and prompt-chain wiring only; the LLM prompt-chain emits the descriptive Envelope, and boundary cleanup validates/sanitizes it without injecting scenario semantics.

2026-05-29 coalesced update: Runtime wiring now routes through the one-call `LLMPromptChainDecompressor`; fake clients respond to `decompress_request` and optional `repair_decompressed_envelope` stages.

## Files

- Update `app/decompressor/runtime.py`
- Update `tests/test_decompressor.py`
- Update `tests/test_planner.py` if planner integration coverage is added here

## Tasks

1. Add explicit constructor parameters such as `model_client=None` or `prompt_chain=None`.
2. Reject no-argument construction.
3. Route all runtime calls through the prompt chain.
4. Ensure stage failures raise after repair instead of falling back to static output.
5. Add tests proving fake LLM clients drive all decompressor behavior.

## Risks

- Recursion bugs if prompt-chain fallback calls `run(...)` on the same LLM-configured runtime.
- Request ID sequencing changes that break assumptions.

## Rollback

Revert constructor and routing changes in `runtime.py`.

## Verification

```bash
uv run pytest tests/test_decompressor.py tests/test_planner.py -q
```
