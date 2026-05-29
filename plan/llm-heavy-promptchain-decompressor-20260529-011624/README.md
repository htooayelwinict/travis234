# Implementation Plan: LLM Heavy Prompt-Chain Decompressor

## Goal

Implement an injectable LLM-only decompressor that produces the existing `Envelope` contract through a coalesced structured-output model call while preserving the current LangGraph topology.

## Success criteria

- `DecompressorRuntime()` fails fast without an injected model client or prompt chain.
- An injected LLM prompt-chain path can run one coalesced model-backed decompression call and return a valid `Envelope`.
- The graph stays `decompressor_node -> planner_node -> worker_kernel_node -> END`; prompt-chain stages do not become LangGraph nodes.
- Model output is Pydantic-validated and boundary-cleaned before reaching planner/runtime code.
- Unit tests use fake/canned model responses only; no live model calls occur in tests.
- Prompt input redacts common secret-like strings before external model calls.

## Implementation status

Completed. The runtime now requires explicit model-client or prompt-chain injection and has no deterministic/static Envelope generator.

2026-05-29 boundary refactor completed from `research/decompressor-envelope-boundary-20260529-194314.md`: `Envelope` is descriptive-only (`constraints`/`complexity_hint`), decompressor outputs no longer expose `execution_hints`, `budget_hint`, or `observe_first` intents, and planner fallback behavior is inferred from descriptive ambiguity/context signals.

2026-05-29 coalesced LLM refactor completed from `research/llm-only-coalesced-decompressor-runtime-20260529.md`: normal decompression is one `decompress_request` structured-output call, with one `repair_decompressed_envelope` retry only after validation failure. Boundary cleanup no longer clamps semantic strings through a static label taxonomy; it strips planner/kernel leaks, deduplicates text, removes unsafe assumptions, clamps confidence/complexity, and guards pronoun-only requests.

## Plan source

This plan implements the recommendation from:

- `plan/research-llm-heavy-promptchain-decompressor-20260529-011000/README.md`

## Artifacts

- Main implementation plan: `plan.md`
- Requirements research: `research/requirements.md`
- Existing code research: `research/existing-code.md`
- Reference notes: `research/references.md`
- Phase execution files: `phases/`

## Recommended first implementation step

Start with Phase 1: add internal decompressor contracts, allowed-label constants, redaction helpers, and fake-test-client scaffolding without changing runtime behavior.
