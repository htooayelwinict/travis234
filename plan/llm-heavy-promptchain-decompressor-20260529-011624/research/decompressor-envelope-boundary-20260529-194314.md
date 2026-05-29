# Research: DecompressorRuntime Envelope Boundary Refactor

## Question

How should the existing Python/LangGraph decompressor be refactored so `DecompressorRuntime` emits a descriptive-only `Envelope` while the planner retains ownership of strategy, steps, worker selection, execution flow, and budgets?

Boundary law to preserve:

```text
Decompressor describes the problem.
Planner designs the execution.
Kernel controls execution.
Workers perform bounded tasks.
```

## Summary

The current implementation leaks planner/kernel concepts from the decompressor through `Envelope.execution_hints`, `Envelope.budget_hint`, and the `observe_first` intent. These fields currently influence planner selection directly, especially in `app/planner/selector.py`, so the refactor is more than a schema rename: planner routing must be adjusted to consume descriptive fields such as `input_type`, `ambiguity`, `context_needed`, `constraints`, `risks`, `complexity_hint`, `confidence`, `domains`, and precise user intents.

The safest path is to first update the shared schema and decompressor stage contracts, then update deterministic and LLM prompt-chain assembly, then adjust planner selector/tests to infer strategy internally from the descriptive envelope. The LLM prompt chain should keep the existing heavy staged shape, but its stages must become descriptive: `normalize_request`, `extract_artifacts`, `classify_request`, `infer_context_and_risk`, `assemble_envelope`, and `validate_envelope`. There should be no `recommend_planner` stage.

## Repository findings

### Active plan location

This research belongs under the existing decompressor plan:

```text
plan/llm-heavy-promptchain-decompressor-20260529-011624/
```

The plan currently describes an optional LLM prompt-chain decompressor and still references old planner-leaking fields (`execution_hints`, `budget_hint`) in acceptance criteria and existing patterns.

### Current schema leaks

Source: `app/schemas.py`

`Envelope` currently contains:

```python
execution_hints: list[str] = Field(default_factory=list)
budget_hint: str = "medium"
```

These should be removed from the decompressor boundary and replaced with:

```python
constraints: list[str] = Field(default_factory=list)
complexity_hint: str = "medium"
```

Recommended final `Envelope` fields:

```text
request_id
raw_input
normalized_input
user_goal
input_type
intents
risks
artifacts
context_needed
constraints
ambiguity
assumptions
complexity_hint
confidence
metadata
```

### Deterministic decompressor leaks

Source: `app/decompressor/runtime.py`

Current deterministic assembly sets `execution_hints` and `budget_hint` in `_run_deterministic`.

Leak points:

- `_classify_request(...)` returns `budget_hint`.
- `_infer_context_and_risk(...)` returns `execution_hints`.
- `_intents(...)` adds `observe_first` for ambiguous requests.
- `_budget_hint(...)` estimates low/medium/high budget from input type/domain.
- `_validate_envelope(...)` currently only round-trips Pydantic validation and does not reject planner-owned fields.

Refactor direction:

- Rename `_budget_hint` to a descriptive `_complexity_hint`.
- Rename `execution_hints` production to `constraints` production.
- Remove `observe_first` from intent generation.
- Keep ambiguous requests descriptive with `input_type == "ambiguous_request"`, `risks` such as `ambiguous_scope`, `context_needed` such as `scope_clarification`, and constraints such as `target_scope_must_be_identified_before_mutation` and `mutation_requires_verification`.

### LLM prompt-chain leaks

Sources:

- `app/decompressor/prompt_chain.py`
- `app/decompressor/contracts.py`
- `app/decompressor/labels.py`

Current stage contracts require planner-leaking fields:

- `RequestClassification.budget_hint`
- `RiskContextInference.execution_hints`
- `labels.BUDGET_HINTS`
- `labels.EXECUTION_HINTS`
- allowed intent `observe_first`

Current `_STAGES` only includes:

```text
normalize_request
extract_artifacts
classify_request
infer_context_and_risk
```

Refactor direction:

- `RequestClassification` should use `complexity_hint`, not `budget_hint`.
- `RiskContextInference` should use `constraints`, not `execution_hints`.
- Allowed labels should include richer descriptive intents and context labels needed by the Lighthouse SDK case.
- Prompt allowed-label payload should expose `constraints` and `complexity_hints`, not `execution_hints` and `budget_hints`.
- Metadata stage list should include the full allowed chain:
  - `normalize_request`
  - `extract_artifacts`
  - `classify_request`
  - `infer_context_and_risk`
  - `assemble_envelope`
  - `validate_envelope`
- `assemble_envelope` and `validate_envelope` can be internal runtime stages if not separate model calls. They still need to appear in diagnostics when completed.
- Do not add or retain a planner recommendation stage.

### Planner coupling to leaks

Source: `app/planner/selector.py`

Current selector contains:

```python
if "observe_first" in envelope.intents or "observe_first_required" in envelope.execution_hints:
    return self._fallback
```

After removing leaks, the planner should infer fallback/observe-first strategy from descriptive fields it owns interpreting, for example:

- `envelope.input_type == "ambiguous_request"`
- `"ambiguous_scope" in envelope.risks`
- `"scope_clarification" in envelope.context_needed`
- `"target_scope_must_be_identified_before_mutation" in envelope.constraints`
- low confidence

The fallback planner may keep `strategy="observe_first"` because `strategy` is a `Plan` field, not an `Envelope` field.

### Test surface to update

Sources:

- `tests/test_decompressor.py`
- `tests/test_planner.py`
- `tests/test_graph.py`

Existing tests assert old fields directly:

- `envelope.budget_hint == ...`
- `envelope.execution_hints == ...`
- `"observe_first" in envelope.intents`
- fake LLM responses with `budget_hint` and `execution_hints`

Required new tests should assert:

1. No planner leaks for `fix network_sniffer.py`:
   - no `planner_hint`, `execution_hints`, `budget_hint`, `steps`, `worker_type`, or `strategy` in `envelope.model_dump()`.
2. Lighthouse SDK example:
   - `input_type == "mutation_request"`
   - intents include `sdk.integration`, `async.migration`, `performance.fix`
   - context includes `dependency_manifest`, `transaction_api_locations`
   - constraints includes `do_not_invent_lighthouse_sdk_api`
   - `complexity_hint == "medium"`
   - assumptions do not claim lag is definitely caused by synchronous transaction API calls.
3. Observe-first is not an intent for `fix the app`.
4. All decompressor outputs use `complexity_hint`, not `budget_hint`.

## Required label taxonomy updates

### Intents

Current intent labels are too narrow and include strategy:

```text
code.fix
observe_first
research.lookup
infra.debug
question.answer
```

Recommended descriptive intent labels for this refactor:

```text
code.fix
code.refactor
sdk.integration
async.migration
performance.fix
research.lookup
infra.debug
question.answer
```

`observe_first` should be removed from allowed decompressor intents.

### Risks

Current risks are close but need richer ambiguity/performance labels:

```text
mutation_requested
file_mutation
needs_verification
ambiguous_scope
ambiguous_mutation
observation_context_needed
```

Recommended additions/replacements:

```text
ambiguous_sdk_identity
performance_cause_unknown
```

`observation_context_needed` is borderline strategy-like. Prefer descriptive alternatives such as `ambiguous_scope`, `ambiguous_mutation`, and richer `context_needed` entries.

### Context needed

Current context labels are insufficient:

```text
repo_tree
target_file
scope_clarification
```

Recommended additions for SDK/performance/refactor requests:

```text
dependency_manifest
sdk_import_or_package_reference
transaction_api_locations
current_transaction_flow
performance_or_lag_evidence
existing_async_patterns
tests_or_verification_entrypoints
```

These are descriptive context requirements, not a plan.

### Constraints

`constraints` should replace `execution_hints`. Good constraints describe invariants the planner must respect without prescribing steps or workers.

Recommended initial constraints:

```text
sdk_availability_must_be_confirmed_before_refactor
do_not_invent_lighthouse_sdk_api
transaction_api_locations_must_be_identified_before_mutation
target_locations_must_be_identified_before_mutation
target_scope_must_be_identified_before_mutation
performance_claims_require_evidence
mutation_requires_verification
do_not_invent_missing_api
```

Avoid constraints that are disguised step ordering, such as `observe_first_required`, `inspect_target_file_before_patch`, and `verify_after_patch`.

## Lighthouse SDK classification notes

For input:

```text
do we have lighthouse sdk if we do, use it as async function to connect all transation apis and fix lagging issues.
```

The decompressor should normalize the typo-tolerant meaning without claiming implementation facts:

```text
Check whether a Lighthouse SDK exists in the project. If it exists, refactor the transaction APIs to use it asynchronously to address lagging issues.
```

Descriptive classification:

- `input_type`: `mutation_request`
- `intents`: `code.refactor`, `sdk.integration`, `async.migration`, `performance.fix`
- `domains`: `code`
- `risks`: `mutation_requested`, `file_mutation`, `ambiguous_scope`, `ambiguous_sdk_identity`, `performance_cause_unknown`, `needs_verification`
- artifacts:
  - dependency hint: `Lighthouse SDK`
  - code component hint: `transaction APIs`
- richer context as listed above
- constraints as listed in the expected case
- safe assumptions only:
  - request refers to current workspace
  - SDK may be discoverable from dependencies/imports/docs/existing code
  - transaction APIs are implemented somewhere in repository
- ambiguity should include unknown SDK identity, unknown transaction API locations, unknown lag cause, broad scope.

Do not assume synchronous transaction calls are the cause of lag; that belongs in ambiguity/evidence requirements.

## Validation and repair rules

Add schema validation or a decompressor boundary sanitizer that rejects/removes planner leaks from final `Envelope` dumps. Required forbidden keys:

```text
planner_hint
planner_confidence
planner_alternatives
execution_hints
budget_hint
worker_type
steps
strategy
max_tool_calls
max_model_calls
```

Recommended behavior:

- If `budget_hint` appears from an LLM stage, map valid `low|medium|high` values to `complexity_hint`, then remove `budget_hint`.
- If `execution_hints` appears, do not pass it through. Convert only clearly safe invariants into `constraints`; otherwise drop.
- If `observe_first` appears in intents, remove it and represent ambiguity through `input_type`, `risks`, `context_needed`, `constraints`, and `ambiguity`.
- If planner/kernel keys appear at the final envelope boundary, fail validation or repair before returning.

Use `pydantic` `extra="forbid"` on internal stage contracts where practical so injected LLM responses cannot silently carry planner keys.

## Second-opinion synthesis retained

Open Bridge was used for a focused second-pass synthesis. Useful retained recommendations:

- The highest risk is selector regression because planner selection currently reads decompressor strategy leaks directly.
- Add a planner-side translation/interpretation layer by updating selector logic to infer strategy from descriptive envelope fields, not from prescriptive hints.
- Refactor stage contracts before changing routing so tests expose all dependency points.

The Open Bridge output was advisory and checked against local repository files before inclusion.

## Recommended minimal implementation sequence

1. Update `app/schemas.py` `Envelope` to remove `execution_hints`/`budget_hint` and add `constraints`/`complexity_hint`. Consider `extra="forbid"` to reject unexpected planner-owned fields.
2. Update `app/decompressor/contracts.py` stage models to use `complexity_hint` and `constraints`; forbid extras if possible.
3. Update `app/decompressor/labels.py` with descriptive intent/context/risk/constraint labels and remove `observe_first`, `EXECUTION_HINTS`, and `BUDGET_HINTS` naming.
4. Update `app/decompressor/runtime.py` deterministic logic:
   - no `observe_first` intent
   - richer SDK/performance/refactor classification
   - `complexity_hint` instead of budget
   - constraints instead of execution hints
   - safe Lighthouse assumptions/ambiguity
   - final leak validation/repair
5. Update `app/decompressor/prompt_chain.py` prompts, sanitizers, stage diagnostics, and final validation:
   - allowed stages include `assemble_envelope` and `validate_envelope`
   - no planner recommendation stage
   - prompt examples use constraints/complexity
6. Update `app/planner/selector.py` to stop reading removed fields and make observe-first/fallback decisions from descriptive ambiguity/scope signals.
7. Update decompressor and planner tests, including the four required test cases.
8. Run focused tests first, then full suite.

## Known limitations / open questions

- Existing plan documentation still describes the old schema and should be updated during implementation or documentation phase.
- The user asked not to code yet, so this note does not verify the final behavior through tests.
- Exact Pydantic extra-field strategy should be chosen during implementation after checking current Pydantic v2 config style in the project.
- There is no external official documentation needed for this refactor; repository source and user-provided architecture rules are the primary sources.

## Source pointers

- `app/schemas.py` — shared `Envelope`, `Plan`, `Task`, `Result` schemas.
- `app/decompressor/runtime.py` — deterministic decompressor and final validation hook.
- `app/decompressor/prompt_chain.py` — optional LLM prompt-chain stages, prompts, sanitization, metadata.
- `app/decompressor/contracts.py` — LLM stage output contracts.
- `app/decompressor/labels.py` — allowed label taxonomy.
- `app/planner/selector.py` — planner routing currently coupled to decompressor leaks.
- `tests/test_decompressor.py` — most tests needing schema/field updates.
- `tests/test_planner.py` — selector tests needing route assertions from descriptive fields.

## Saved path

`plan/llm-heavy-promptchain-decompressor-20260529-011624/research/decompressor-envelope-boundary-20260529-194314.md`
