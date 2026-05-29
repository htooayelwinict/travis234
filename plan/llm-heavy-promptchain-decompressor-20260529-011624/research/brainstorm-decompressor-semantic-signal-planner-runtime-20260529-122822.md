# Brainstorm: Decompressor Semantic Signal Without Planner Recommendation

## Problem statement

The current boundary is mixed. `DecompressorRuntime` still emits `planner_hint`, `planner_confidence`, and `planner_alternatives` in the `Envelope` (`app/schemas.py`), and `PlannerSelector` gives those fields override power when confidence is at least `0.70` (`app/planner/selector.py`). That means planner choice is partly decided upstream by the decompressor instead of being owned by `PlannerRuntime`.

The new requirement is tighter:

- Decompressor should **not recommend a planner**.
- Decompressor should still emit **enough semantic signal** for `PlannerRuntime` / `PlannerSelector` to make a good planner choice.

This is especially relevant because `DecompressorRuntime` is already rich enough to emit intent, domain, risk, ambiguity, context, and execution-hint signals (`app/decompressor/runtime.py`). Those are closer to understanding the request, while planner selection is an execution-policy decision.

## Constraints

- Preserve the top-level runtime split: decompressor understands input, planner selects strategy.
- Keep migration risk low because current tests and plan artifacts assume `planner_hint` exists.
- Maintain planner selection quality for current cases such as:
  - direct question → direct planner
  - concrete file fix → code planner
  - vague mutation / observe-first → fallback planner
  - infra-only request → infra planner
  - research request → research planner
- Avoid creating a new hidden coupling where decompressor simply renames `planner_hint` into another planner-shaped field.

## Options considered

### Option 1: Remove planner recommendation entirely and let selector infer from existing envelope fields

Use the current semantic fields only: `input_type`, `intents`, `domains`, `risks`, `artifacts`, `context_needed`, `execution_hints`, `ambiguity`, `confidence`, and `budget_hint`.

**Pros**

- Cleanest boundary: decompressor describes the request; planner chooses the planner.
- Lowest conceptual complexity.
- Mostly matches the selector's existing fallback logic already present in `app/planner/selector.py`.

**Cons**

- May expose gaps where current semantic fields are not expressive enough for edge cases.
- Planner quality could dip temporarily if selector logic currently relies on high-confidence hints for some mixed cases.

**Best fit when**

- Existing labels already capture most routing intent.
- The team wants the strongest ownership boundary quickly.

### Option 2: Replace planner recommendation with planner-neutral routing signals

Keep decompressor rich, but make it emit explicitly planner-neutral semantics such as:

- mutability: read-only vs mutate
- specificity: concrete target vs vague scope
- operational surface: code vs infra vs research vs mixed
- action shape: answer, inspect, patch, verify, compare
- certainty: how reliable the classification is

This can live in existing fields when possible, with minimal additional planner-neutral fields only if a real gap remains.

**Pros**

- Preserves strong boundary while making planner routing more explicit.
- Easier to reason about than raw planner hints.
- Lets `PlannerSelector` act as a policy mapper from semantic signals to planners.

**Cons**

- Risks schema churn if too many new routing fields are introduced.
- Can become planner hints in disguise if labels are overly tailored to current planners.

**Best fit when**

- The team wants better-than-minimal routing quality without keeping planner-specific advice.

### Option 3: Keep planner fields temporarily, but demote them to migration-only metadata ignored by selector

Decompressor may still populate legacy planner fields for compatibility or observability, but `PlannerSelector` stops honoring them and relies only on semantic fields.

**Pros**

- Safest migration path.
- Allows side-by-side comparison between semantic selection and old recommendation behavior.
- Reduces immediate breakage risk in tests, logs, and diagnostics.

**Cons**

- Boundary remains muddy until cleanup is finished.
- Future contributors may keep using the legacy fields by accident.
- Can prolong a half-migrated architecture.

**Best fit when**

- The repo needs a staged transition rather than a clean cut.

### Option 4: Introduce a capability / constraint profile for planner selection

Decompressor emits an abstract routing profile such as:

- needs_observation_first
- allows_direct_answer
- requires_repo_context
- likely_file_mutation
- likely_infra_surface
- research_heavy
- mixed_domain

Planner selection becomes a rule match against those capabilities/constraints.

**Pros**

- Strong architectural separation.
- Easier to extend when more planners appear later.
- Makes planner policy more declarative.

**Cons**

- Higher design overhead than current needs likely justify.
- Can duplicate information already present in intents, risks, and execution hints.
- Over-engineering risk for a small planner set.

**Best fit when**

- The planner registry is expected to grow and routing policy will become materially more complex.

## Recommended path

Recommend **Option 2 with Option 3 as a short migration aid**.

Practical meaning:

1. Treat decompressor output as a **semantic understanding layer**, not a planner recommender.
2. Make `PlannerSelector` choose using planner-neutral signals, primarily:
   - `input_type`
   - `intents`
   - `domains`
   - `risks`
   - `artifacts`
   - `execution_hints`
   - `ambiguity`
   - `confidence`
3. Only introduce new fields if a real routing gap cannot be expressed through those existing signals.
4. If migration safety matters, keep legacy planner fields briefly for diagnostics/comparison, but stop letting selector trust them.

Why this path fits this repo:

- The selector already contains semantic routing rules independent of planner hints (`observe_first`, question detection, code/research/infra checks).
- The decompressor already emits most of the needed signal.
- The requirement is not just “don’t trust decompressor hints”; it is “decompressor should not recommend the planner.” Option 2 preserves rich semantics without collapsing back into planner-shaped advice.

## Semantic signals PlannerRuntime should rely on

The strongest current planner-neutral signals appear to be:

- **Direct-answer signal**: `input_type == "question"` and no artifacts.
- **Observe-first signal**: `observe_first` intent, `observe_first_required` execution hint, or high ambiguity.
- **Code-mutation signal**: `code.fix` intent, `code` domain, file artifacts, `file_mutation` risk.
- **Infra signal**: `infra.*` intent or `infra` domain without stronger code-mutation evidence.
- **Research signal**: `research.*` intent or `research` domain.
- **Confidence / ambiguity signal**: low confidence or ambiguous scope should bias toward fallback behavior.

In other words, the planner should choose from semantics about **what the request is and how safe execution must be**, not from decompressor opinions about **which planner class to instantiate**.

## Risks and mitigations

### Risk 1: Semantic fields are insufficient for mixed or edge cases

**Mitigation:** compare current planner-hint-driven cases against pure semantic routing before removing legacy fields completely; add planner-neutral signals only where an actual gap is observed.

### Risk 2: New routing fields become planner hints in disguise

**Mitigation:** name fields around request properties (`requires_repo_context`, `likely_mutation`, `specificity`) rather than planner names or planner-family concepts.

### Risk 3: Migration leaves dead schema and confusing tests

**Mitigation:** if legacy planner fields remain temporarily, mark them migration-only and define a clear removal checkpoint in the active plan artifacts.

### Risk 4: Selector logic becomes more complex and scattered

**Mitigation:** keep routing policy centralized in `PlannerSelector`; decompressor should emit facts, not policy.

### Risk 5: LLM decompressor mode may drift in labels and reduce planner quality

**Mitigation:** validate planner-neutral labels more tightly and test routing outcomes from semantic envelopes rather than testing planner-hint honoring.

## Open Bridge comparison note

An Open Bridge second-opinion pass also favored moving away from planner-specific hints and toward planner-neutral routing inputs. Its most relevant insight for this repo was that a capability/constraint model is architecturally clean, but likely heavier than needed right now compared with using the existing semantic envelope fields more directly.

## Next steps

1. Audit current planner decisions that still depend on `planner_hint`.
2. Define the minimum planner-neutral signal set needed for selector accuracy.
3. Decide whether migration should be immediate clean-cut or temporary dual-track.
4. Update tests to assert planner selection from semantic envelopes rather than decompressor recommendation fields.

## Saved path

`plan/llm-heavy-promptchain-decompressor-20260529-011624/research/brainstorm-decompressor-semantic-signal-planner-runtime-20260529-122822.md`
