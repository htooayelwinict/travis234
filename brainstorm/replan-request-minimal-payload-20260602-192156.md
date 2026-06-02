# Replan Request Minimal Payload

## Problem

For planner-level replans, the current runtime sends:

- `envelope`
- `current_plan`
- `replan_request`

The `ReplanRequest` currently includes:

- `request_id`
- `plan_id`
- `run_id`
- `failed_step_id`
- `reason`
- `worker_result`
- `completed_artifacts`
- `remaining_budget`
- `recommended_action`

This captures the failing step context and the artifact store, but it does not explicitly tell the planner which prior steps already completed successfully.

## Current Strength

The current payload is already good at:

- explaining why replanning was requested
- showing the planner what artifacts exist
- giving the planner the failing worker status snapshot
- keeping the planner on the original objective via `envelope` + `current_plan`

## Gap

The planner cannot know with certainty which earlier steps truly succeeded when:

- a successful step produced no artifact
- a step had side effects
- multiple steps could plausibly map to similar artifacts
- future execution becomes more parallel or branchy

That means the planner may need to guess whether to reuse, skip, or regenerate parts of the old plan.

## Smallest Useful Addition

Add one runtime payload field to the replan request:

- `completed_step_ids: list[str]`

Why this is the best minimal addition:

1. It tells the planner exactly which steps are done.
2. It prevents accidental re-running of successful non-artifact steps.
3. It keeps the planner stateless; no extra runtime lookups are needed.
4. It works even if a completed step emitted no artifact.

## Recommendation

For planner-level replans only, I would keep the current payload shape and add just:

- `completed_step_ids`

I would **not** add full successful worker-result history unless a later design truly needs it. That would make the runtime heavier without improving the planner much in the common case.

## Practical Read

So the answer is:

- current payload: close, but not fully enough for robust replanning
- missing piece: explicit successful step identity
- best minimal fix direction: `completed_step_ids`

Saved as a durable brainstorm note for future worker-runtime design.
