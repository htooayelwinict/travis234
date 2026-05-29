# Phase 1 — Internal Contracts, Labels, Redaction, and Fake-Client Scaffolding

## Goal

Create safe internal boundaries for LLM-only structured decompression.

## Status

Completed. Added the internal Pydantic structured-output contract, provider-agnostic model-client protocol, redaction helpers, and fake-client test scaffolding.

2026-05-29 update: Refactored contracts and labels to the descriptive Envelope boundary from `research/decompressor-envelope-boundary-20260529-194314.md`: `complexity_hint` replaces `budget_hint`, `constraints` replaces `execution_hints`, `observe_first` is no longer an allowed decompressor intent, and stage contracts forbid extras.

2026-05-29 follow-up: Simplified label taxonomy for the LLM-only prompt-chain design. Scenario-shaped labels are now compatibility aliases mapped to generic vocabulary such as `dependency.integration`, `code.migration`, `target_locations`, and `dependency_reference`. Specific entities belong in `artifacts` or free-text ambiguity/assumptions, not in global allowed labels.

2026-05-29 coalesced update: The static label taxonomy was removed from runtime semantics. The active contract is `DecompressedEnvelope`, a coalesced structured-output model; `RequestClassification` remains only as a compatibility alias/class for older tests/imports.

## Files

- Add `app/decompressor/contracts.py`
- Add `app/decompressor/canonicalize.py`
- Add `app/decompressor/redaction.py`
- Update `tests/test_decompressor.py`

## Tasks

1. Define the internal Pydantic model:
   - `DecompressedEnvelope`
2. Define `PromptChainModelClient` protocol.
3. Add boundary canonicalization for forbidden-field stripping and safe cleanup.
4. Preserve open-ended LLM semantic strings instead of static label clamping.
5. Add redaction helper for common secret-like patterns.
6. Add fake-client scaffolding in tests or test helpers.

## Risks

- Over-exporting internal types as public API.
- Divergence between runtime labels and new label constants.

## Rollback

Remove the new modules and test additions; deterministic runtime remains untouched.

## Verification

```bash
uv run python -c "from app.decompressor.contracts import RequestClassification; print(RequestClassification.model_json_schema()['title'])"
uv run pytest tests/test_decompressor.py -q
```
