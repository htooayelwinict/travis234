# Worker Tool Schema Repair Brainstorm

## Problem

The live Qwen worker test failed before executing tools because the model emitted tool-call objects using `name` instead of the runtime's required `tool_name`. The selected tools were reasonable, but the strict worker decision schema rejected the wire shape.

## Constraints

- Keep planner-facing `worker_type` unchanged.
- Keep permission-gated tools; do not expose raw shell execution.
- Preserve strict artifact/result validation after normalization.
- Workers must work across model/provider drift, not only one favored model.

## Options

1. Use the same model as planner for workers.
   - Fastest short-term improvement.
   - Reduces model drift because planner currently uses `openai/gpt-5.3-codex`.
   - Does not solve schema variance across future models.

2. Add tolerant normalization before strict validation.
   - Accept aliases such as `name -> tool_name`, single tool-call object -> `tool_calls`, and `args -> arguments`.
   - Keep the canonical internal schema strict after normalization.
   - Best first fix because Qwen was semantically correct but syntactically off.

3. Use native OpenRouter tool calling for worker tools.
   - Aligns with provider-standard function-call shape.
   - Better long-term tool semantics and provider reliability tracking.
   - Larger refactor because the model client currently only returns message JSON content.

4. Add a worker decision repair call.
   - Mirrors planner repair behavior.
   - Helpful for malformed JSON or deeper schema drift.
   - Adds latency and cost; should be fallback, not first line.

5. Simplify the schema and prompt.
   - Rename canonical field to `name` or allow both aliases.
   - Reduces friction with common tool-call conventions.
   - Needs compatibility tests to avoid confusing internal tool routing.

## Recommendation

Implement options 2 and 5 first: normalize common tool-call aliases before strict Pydantic validation, and make `name` an accepted alias for `tool_name`. Then retest with Qwen. If it still struggles, run the same live scenario with the planner model to compare model quality.

After that, plan option 3 as the production-grade path: native OpenRouter tool calling with explicit function schemas, `tool_choice`, and `parallel_tool_calls=false` for deterministic worker loops.

## Risks

- Over-tolerant parsing could hide real model drift. Mitigate by storing `normalized_decision_shape` metadata/warnings.
- Native tool calling may not be equally supported by every selected model/provider. Mitigate with provider `require_parameters` or model capability checks.
- Repair calls increase latency. Mitigate by only repairing after deterministic normalization fails.

## Next Steps

1. Add a normalization layer in `WorkerLLMController.decide`.
2. Add tests for `name`, `args`, root-level single call, and canonical schema.
3. Rerun the Qwen live mock repo test.
4. Run one comparison test with `WORKER_LLM_MODEL=openai/gpt-5.3-codex`.
