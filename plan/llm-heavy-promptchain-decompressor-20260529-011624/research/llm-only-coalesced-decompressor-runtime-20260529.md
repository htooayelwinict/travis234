# Research: LLM-Only Coalesced Decompressor Runtime

## Question

How should the decompressor runtime be refactored so it remains fully LLM-powered, avoids static semantic labels, and reduces latency from the current flattened multi-call prompt chain?

## Summary

The current decompressor makes four sequential model calls: normalize, extract artifacts, classify, and infer context/risk. That design is slow because every stage pays network latency and model scheduling overhead. It also overuses static label whitelists, which makes the decompressor feel deterministic and brittle instead of LLM-owned.

The recommended replacement is a single structured LLM decomposition call that emits the complete `Envelope` shape, followed by local Pydantic validation, generic boundary cleanup, and one repair call only if validation fails. This preserves the Envelope boundary while letting the model produce rich semantic strings, artifacts, ambiguity, assumptions, risks, constraints, and context without a static taxonomy driving meaning.

## Key Findings

- Context7 LangChain docs emphasize structured output with Pydantic/JSON Schema as the primary reliable path for validated LLM JSON output.
- Context7 Pydantic docs confirm `model_validate_json` and `extra="forbid"` are the right local enforcement tools, and validation errors can be fed into repair prompts.
- Open Bridge recommended replacing four sequential stages with a coalesced dynamic structured inference call, then a lightweight repair loop.
- The existing `PromptChainModelClient.complete_json(stage, prompt, schema)` already supports passing a JSON schema to the provider, so the refactor does not need provider SDK dependencies.
- Static allowed-label sets should not be the source of semantic meaning. They can be removed from the main prompt and replaced with open-ended string/list fields plus forbidden planner/kernel boundary validation.

## Recommendation

Refactor to a `CoalescedDecompression` prompt:

1. One LLM call named `decompress_request` emits the full descriptive Envelope payload without `request_id`, `raw_input`, or `metadata`.
2. The runtime injects `request_id`, original `raw_input`, and sanitized metadata.
3. Pydantic validates the full structure.
4. Boundary cleanup removes planner/kernel leaks and deduplicates text, but does not inject semantic labels.
5. If validation fails, make one repair call with validation errors and the previous response.
6. If repair fails, raise `PromptChainError`; do not synthesize a deterministic Envelope.

## Expected Benefits

- Reduces normal path from four model requests to one.
- Keeps repair path bounded at two total model requests.
- Gives the LLM ownership of semantics instead of static label tables.
- Keeps repo-local enforcement focused on schema validity, redaction, and planner-boundary safety.

## Risks

- A single large prompt may miss a secondary field compared with smaller staged prompts.
- Open-ended semantics may vary more across model/provider changes.
- Planner selection may need to tolerate richer free-form intent/constraint strings.
- Repair prompt quality becomes more important because there is no deterministic fallback.

## Source Pointers

- `app/decompressor/prompt_chain.py` currently performs four sequential model calls.
- `app/decompressor/contracts.py` contains stage-specific models that should become one coalesced model.
- `app/decompressor/labels.py` currently constrains semantic meaning and should shrink or be removed from prompt logic.
- `app/decompressor/canonicalize.py` should remain boundary-focused: forbidden key removal, deduplication, unsafe assumption removal, underspecified input guard.
- Context7: LangChain structured output docs and Pydantic validation docs.
- Open Bridge: recommended single-turn coalesced structured inference with one repair pass.

## Saved Path

`plan/llm-heavy-promptchain-decompressor-20260529-011624/research/llm-only-coalesced-decompressor-runtime-20260529.md`
