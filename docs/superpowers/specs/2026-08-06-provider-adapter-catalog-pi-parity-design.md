# Provider Adapter and Catalog Pi-Parity Design

Date: 2026-08-06
Status: approved for implementation planning; no runtime implementation in this commit

## Decision summary

Travis234 will preserve its existing provider architecture: static provider metadata,
a checked-in model catalog, and provider-specific compatibility resolution at the
request boundary.

The first change set will be deliberately microscopic:

1. Correct the output-token field for the two direct Z.AI routes.
2. Preserve the documented reason for incomplete OpenAI Responses events and map
   only output-token exhaustion to Travis's `length` stop reason.
3. Remove retired Claude Opus 4.1 identifiers only from the direct Anthropic
   catalog.
4. Add a deterministic, read-only Pi catalog comparison and explicit promotion
   gate for future pre-release catalog maintenance.

Travis234 will not fetch model catalogs during normal startup. Pi is the parity
baseline, but official provider documentation and verified endpoint behavior are
authoritative when Pi, aggregate catalogs, and Travis234 differ.

## Why this work is needed

A read-only audit of Travis234 at commit
`69d0e6e272672e0bc6e3ee180413bf782e8f6cb0` and Pi at commit
`bde81c84405514c8b0f57c34405c152fb129c0ce` found that the core provider
architecture remains current, but several narrow facts have drifted.

The generated catalogs contained:

- 35 Travis234 providers and 1,070 models;
- 38 Pi providers and 1,212 models;
- 220 provider/model pairs present only in Pi;
- 78 provider/model pairs present only in Travis234; and
- 339 common records with at least one metadata difference.

Those totals do not justify copying Pi's catalog wholesale. Some differences are
new provider families, some are gateway-specific aliases, some require adapter
capabilities Travis234 does not yet implement, and some are legitimate
Travis234 safety overrides. Numerical equality is therefore not the correctness
criterion.

The audit did confirm three immediate defects:

- `resolve_openai_compat()` classifies Z.AI as nonstandard but does not include it
  in the existing `max_tokens` branch, so direct Z.AI requests use
  `max_completion_tokens`.
- `_map_responses_status()` maps every incomplete Responses result to `length`
  without examining `incomplete_details.reason`.
- The direct Anthropic catalog still advertises `claude-opus-4-1` and
  `claude-opus-4-1-20250805` after the dated model's retirement.

## Evidence and authority

### Source precedence

When sources disagree, implementation decisions use this order:

1. Current official provider documentation.
2. Reproducible behavior from the provider's supported endpoint.
3. Current Pi request/stream implementation.
4. Pi's generated metadata and its upstream sources, including models.dev and
   provider catalog endpoints.
5. The existing Travis234 catalog.

Pi is a design and parity oracle, not an unquestioned source of truth. A Pi record
must not be promoted if Travis234 lacks the required transport, compatibility
field, authentication behavior, or safe context/output accounting.

### Official documentation

OpenAI documents `response.incomplete` as a terminal event containing
`incomplete_details.reason`. Incomplete responses can result from output-token
limits or safety filtering, so the client must not treat every incomplete result
as ordinary truncation. The current official Python SDK types the token-limit
reason as `max_output_tokens`, while the current REST streaming-event example
still shows `max_tokens`. Travis234 will recognize both spellings as
output-token exhaustion and remain strict for every other reason.

References:

- https://developers.openai.com/api/reference/resources/responses/streaming-events
- https://developers.openai.com/api/reference/resources/responses
- https://github.com/openai/openai-python/blob/main/src/openai/types/responses/response.py

The Z.AI chat-completions request schema documents `max_tokens` as the output
limit for the direct compatible endpoint.

Reference:

- https://docs.bigmodel.cn/api-reference/%E6%A8%A1%E5%9E%8B-api/%E5%AF%B9%E8%AF%9D%E8%A1%A5%E5%85%A8

Anthropic marks `claude-opus-4-1-20250805` retired on the direct Claude API as of
2026-08-05 and recommends `claude-opus-4-8`. Anthropic also states that
partner-operated platforms can use different retirement schedules.

Reference:

- https://platform.claude.com/docs/en/about-claude/model-deprecations

xAI currently documents both Responses and Chat Completions for `grok-4.5`.
Travis234's Chat Completions route is consequently valid even though current Pi
prefers Responses.

Reference:

- https://docs.x.ai/developers/grok-4-5

### Current Pi behavior

The inspected Pi checkout:

- sends Z.AI output limits through `max_tokens`;
- passes `incomplete_details.reason` into Responses stop-reason mapping;
- maps only `max_output_tokens` to `length` and treats other incomplete reasons
  as errors;
- omits direct Anthropic Opus 4.1 while retaining the Bedrock and intermediary
  provider variants; and
- generates its catalog before release from multiple sources, then applies
  narrow documented corrections.

Travis234 should copy these ownership boundaries, not port the complete Pi
generator or its entire output.

## Existing architecture to preserve

The provider control plane remains outside the generic agent loop.

- `travis/ai/provider_metadata.py` owns provider identity, base URLs,
  authentication kind, and aliases.
- `travis/ai/builtin_models.json` is the shipped model catalog.
- `travis/ai/builtin_models.py` converts catalog records to runtime `Model`
  values.
- `travis/ai/providers/openai_compat.py` resolves conservative OpenAI-compatible
  request behavior from provider identity and explicit model metadata.
- Provider stream decoders own provider terminal-event semantics.
- `travis/ai/catalog_generation.py` contains pure catalog transformation helpers.
- `scripts/sync_builtin_model_catalog.py` performs pre-release catalog
  maintenance.

The implementation must not move provider facts into the agent loop, session
state, compaction, TUI, or persistence layers.

## Options considered

### Option A: one-off adapter and JSON edits

Change the two adapters and manually remove the two Anthropic records.

Advantages:

- Smallest immediate diff.
- Very limited runtime regression surface.

Rejected as the complete design because it does not make future Pi drift visible.
The same class of catalog issue would recur silently.

### Option B: promotion-gated Pi parity (selected)

Make the confirmed microscopic fixes and extend the existing pre-release tooling
with deterministic Pi comparison plus explicit promotion scope.

Advantages:

- Preserves the current static runtime.
- Keeps every catalog mutation reviewable.
- Separates actual incompatibility from harmless catalog drift.
- Allows adapter capability to land before models that require it.
- Supports incremental Pi parity without a large catalog rewrite.

Cost:

- Full numerical parity is intentionally not automatic.
- Maintainers must approve each promotion or retirement group.

### Option C: port Pi's complete catalog generator

Copy Pi's multi-source generator and replace the Travis234 catalog wholesale.

Advantages:

- Maximum catalog similarity to Pi.
- New upstream models would appear with less manual work.

Rejected because it would introduce new provider families, unsupported
compatibility fields, hundreds of record changes, and a much larger release
surface. It is incompatible with a surgical correction.

## Selected runtime design

### Z.AI output-token field

The correction belongs in `_detect_openai_compat()` in
`travis/ai/providers/openai_compat.py`.

The existing `is_zai` predicate already recognizes both provider IDs and both
official host patterns. The implementation will add `is_zai` to the existing
`use_max_tokens` decision. No model-ID list and no new transport branch are
needed.

Required result for both `zai` and `zai-coding-cn`:

```text
requested output limit
  -> final body max_tokens
  -> no final body max_completion_tokens
```

Explicit catalog compatibility metadata must continue to override detected
defaults field by field.

### OpenAI Responses incomplete events

The correction belongs in `travis/ai/providers/responses_stream.py`.

`_map_responses_status()` will accept the provider's incomplete reason in addition
to status. When a terminal `response.completed` or `response.incomplete` event is
decoded, the caller will extract:

```text
response.incomplete_details.reason
```

The mapping will be:

| Provider status | Incomplete reason | Travis stop reason | Error message |
|---|---|---|---|
| `completed` | any | `stop` | none |
| `incomplete` | `max_output_tokens` or documented legacy/example spelling `max_tokens` | `length` | none |
| `incomplete` | another string | `error` | includes that reason |
| `incomplete` | absent | `error` | states that no provider reason was supplied |
| `failed` or `cancelled` | any | `error` | existing provider-status message |

Queued and in-progress handling remains unchanged because this patch concerns
terminal events only. Tool-call promotion from `stop` to `toolUse` remains
unchanged. No new public message field or raw-stop-reason field is introduced.

### Direct Anthropic retirement

Only these records will be removed from the `anthropic` object in
`travis/ai/builtin_models.json`:

- `claude-opus-4-1`
- `claude-opus-4-1-20250805`

The implementation will not remove:

- Amazon Bedrock inference-profile IDs;
- `cloudflare-ai-gateway/claude-opus-4-1`;
- `opencode/claude-opus-4-1`; or
- unrelated aliases on partner-operated services.

That containment matches both Anthropic's platform-specific lifecycle warning and
current Pi output.

## Pre-release catalog parity design

### No runtime discovery

`builtin_providers()` will continue loading the packaged catalog without a
`refresh_models` callback. No startup network request, background refresh, cache
under `~/.travis234`, fallback catalog, or alternate user-state path will be
introduced.

### Deterministic comparison

Pure helpers in `travis/ai/catalog_generation.py` will compare the Travis234
catalog with an explicitly supplied Pi-generated `models.json`.

The comparison result will classify differences as:

- provider missing from Travis234;
- provider missing from Pi;
- model missing from Travis234;
- model missing from Pi;
- common model with field differences; and
- record requiring an API or compatibility capability not recognized by
  Travis234.

The report will use stable provider, model, and field ordering. It must not
contain credentials, environment contents, or arbitrary request headers.

### Explicit promotion gate

The maintenance script may apply Pi-derived data only to an explicitly selected
provider/model scope. Unselected records remain byte-for-byte equivalent after
normalization.

Each promotion group must identify:

- provider and model IDs;
- whether the action is add, update, or retire;
- the Pi commit used for comparison;
- an official evidence URL when the change affects routing, compatibility,
  lifecycle, context, or output limits; and
- any deliberate Travis234 override.

The first implementation need not promote the 220 Pi-only records. Its catalog
mutation is limited to the approved direct Anthropic retirement. The comparison
report establishes the safe mechanism for later focused promotions.

### Existing OpenRouter behavior

The existing OpenRouter capacity merge and safe-output handling remain intact.
The Pi comparison must report, not overwrite, known Travis234 safety overrides
where an advertised output limit consumes the entire context window.

### CLI behavior

The catalog maintenance script will expose separate check and apply behavior.

- Check mode is read-only and can emit a structured report.
- Apply mode requires explicit promotion scope.
- A missing, malformed, or incompatible Pi input fails before writing.
- Writes use stable compact JSON matching the existing packaged artifact.
- The default Python test suite uses fixtures and performs no network access.

Exact flag names are an implementation-plan detail, but check mode must never
write and apply mode must never broaden its requested scope.

## Error handling and safety

- Z.AI request construction remains local and deterministic.
- An incomplete Responses event with an unknown reason fails visibly rather than
  masquerading as harmless token truncation.
- Catalog comparison rejects malformed top-level provider or model mappings.
- Catalog apply validates the complete prospective artifact before replacement.
- A failed catalog operation leaves `builtin_models.json` unchanged.
- No credential-bearing provider response is stored as a fixture or printed.
- The two user-owned untracked documents remain outside all staging and edits.

## Regression design

Repository policy requires a failing regression before every bug fix.

### Z.AI regressions

Add focused final-body tests for representative models on:

- `zai`;
- `zai-coding-cn`; and
- a normal OpenAI-compatible provider that must continue using
  `max_completion_tokens`.

The tests will assert presence and absence of the exact competing fields.

### Responses regressions

Add stream-decoder tests for:

- `incomplete_details.reason == "max_output_tokens"` producing `length`;
- `incomplete_details.reason == "max_tokens"` also producing `length` for the
  current REST-reference spelling;
- a content-filter reason producing `error` and preserving the reason in the
  message;
- an unknown reason producing `error` and preserving the reason;
- a missing reason producing a precise local error; and
- a completed response remaining `stop`.

### Anthropic catalog regressions

Add catalog tests proving:

- both direct Anthropic Opus 4.1 IDs are absent;
- current replacement models remain present; and
- the existing Bedrock, Cloudflare gateway, and OpenCode records are retained.

### Catalog tooling regressions

Use small checked-in fixtures to prove:

- comparison output is deterministic;
- check mode does not write;
- malformed Pi input fails before writing;
- an approved promotion changes only selected records;
- an unapproved record cannot be changed;
- known OpenRouter safety overrides remain unchanged; and
- structured reports classify unsupported providers and compatibility fields
  without promoting them.

## Verification strategy

Focused verification will cover provider request bodies, Responses stream
decoding, catalog loading, and catalog-generation helpers first.

Before completion, repository guidance requires:

1. the full Python suite;
2. npm launcher tests;
3. npm and Python package builds and package checks; and
4. relevant installed-package and container smoke checks.

No live provider call is required for deterministic unit coverage. If maintainers
choose to run credentialed smoke tests, their bodies and credentials must not be
captured in tracked files or command output.

## Explicit non-goals

This design does not include:

- switching `grok-4.5` from Chat Completions to Responses;
- adding Baseten, Qwen token-plan, or other provider families;
- bulk-importing missing OpenRouter, Copilot, or gateway models;
- adding runtime catalog fetches or persistent catalog caches;
- Google transient retry changes or signed-empty-block handling;
- arbitrary sampling parameters or `thinking_token_budget` support;
- Bedrock diagnostic enrichment or a public raw-stop-reason field;
- stream policy changes for missing Chat Completions `finish_reason` values;
- Copilot policy restoration or Fireworks model-specific caching;
- any agent-loop, compaction, iteration-budget, tool-ordering, bounded-parallel,
  subagent, TUI, or persistence change; or
- any refactor of the shared provider control plane.

Each deferred adapter capability requires its own official evidence, failing
regression, bounded design, and verification before models depending on it are
promoted.

## Expected file boundary

Implementation planning should remain within:

- `travis/ai/providers/openai_compat.py`;
- `travis/ai/providers/responses_stream.py`;
- `travis/ai/catalog_generation.py`;
- `travis/ai/builtin_models.json`;
- `scripts/sync_builtin_model_catalog.py`;
- focused provider and catalog tests; and
- small non-secret catalog fixtures if needed.

If implementation discovers that a change is required in the agent loop,
compaction, session persistence, iteration budgeting, or bounded parallel
execution, work must stop and return to design review.
