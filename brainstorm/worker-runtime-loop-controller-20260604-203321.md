# Worker Runtime Loop Controller Brainstorm

## Problem

The current worker runtime works, but its control behavior is spread across several nested loops:
plan steps, kernel attempt retries, worker group templates, worker model turns, tool calls, mutation denial repair, and planner replan. This makes the system observable but harder to reason about as one agentic runtime.

## Sources

- OpenClaw agent loop docs describe one authoritative serialized agent loop per session, with queueing, prompt assembly, tool events, compaction, retries, and lifecycle streams handled by the runtime.
- Claude Code agent loop docs describe a repeated cycle: model evaluates, calls tools, receives results, repeats until no tool calls remain. It also exposes permissions, budgets, hooks, and observability around that loop.
- OpenAI Agents SDK docs distinguish LLM-led orchestration from code-led orchestration, recommend manager-style central control when one owner must enforce guardrails, and explicitly call out loops where error messages are fed back for improvement.
- Anthropic's agent engineering guidance recommends simple composable patterns, clear tool interfaces, easy-to-use tool formats, and investing heavily in agent-computer interfaces.
- LangGraph persistence docs show the production value of checkpoints, replay, fault tolerance, and durable per-step state rather than a purely in-memory loop.

## Key Findings

Current design is closer to a structured workflow plus inner agent loops than a single autonomous agent loop. That is not wrong for a production worker kernel. It is safer than a pure free-running loop because the kernel owns permissions, budgets, artifacts, retries, and replan boundaries.

The weak part is that loop policy is implicit. Retry decisions, repair decisions, budget expansion, verification retry hints, and replan decisions live in separate methods and metadata strings. This creates correctness drift and makes it harder to answer "why did the runtime do this?"

Do not collapse everything into one raw OpenClaw-style agent loop. Instead, create one explicit control-plane object that owns loop policy and decisions while preserving the existing specialized execution loops.

## Recommendation

Add a `WorkerRunController` or `WorkerLoopController` later. It should not execute tools or call workers directly at first. It should centralize decisions:

- classify result/issue as instance failure, kernel failure, plan failure, or completed
- decide continue, retry same step, retry with repair instruction, replan, block, or finalize
- produce a typed `LoopDecision`
- track per-step attempts, denial repair attempts, model/tool budgets, and reason codes
- generate retry instructions for the next worker instance
- optionally run one cheap LLM diagnostic call before respawn only for ambiguous failures

Keep the actual agent loop inside `AgenticWorkerGroupRunner`. Keep write operation denial inside tools. Keep planner replan inside `WorkerKernelRuntime`. The new controller should make those decisions explicit and testable.

## LLM Diagnostic Before Respawn

Use a diagnostic LLM call selectively, not always.

Good cases:
- malformed structured output repeated once
- empty worker decision after useful observations
- output contract miss where artifacts are partially present
- verification failed before command execution
- tool choice confusion between similar tools

Bad cases:
- budget ceiling itself
- tool provider missing
- permission denied by strict policy
- obvious schema parse error with deterministic repair instruction
- planner-level missing artifacts

The diagnostic call should output a compact `RetryInstruction` schema, not a new plan:

- failure_summary
- likely_cause
- next_instance_instruction
- required_first_action
- prohibited_actions
- budget_adjustments
- expected_artifact_reminder

## Best Shape

The production-grade target is not "one loop everywhere"; it is "one loop policy owner." The runtime can still have nested loops internally, but there should be one authoritative decision object at every boundary.

```text
kernel
  -> compile task
  -> agent group loop
       -> instance loop
            -> tool loop
            -> mutation denial repair observation
  -> LoopController decides next action
       -> continue next step
       -> retry same step with instruction
       -> replan
       -> block/fail/finalize
```

## Risks

- A controller object can become a god object if it starts executing workers or tools.
- An LLM diagnostic call before every retry will add latency and cost.
- Too many policy branches can make agent behavior feel mechanical.

## Next Steps

1. Define a small `LoopDecision` schema.
2. Move failure classification and retry/replan choice into a controller.
3. Keep current loops but route their exit decisions through the controller.
4. Add optional diagnostic LLM call only for ambiguous retryable failures.
5. Test against existing probe results: Xiaomi schema errors, mutation denial repair, verification retry, missing artifact replan.
