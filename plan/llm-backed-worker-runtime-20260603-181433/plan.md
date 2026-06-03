# LLM-Backed Worker Runtime Implementation Plan

## Summary

Add an LLM-backed worker execution path without changing the planner contract. The kernel compiles scoped tasks, worker groups run one or more internal LLM instances, tools are exposed only through permission gates, and each group returns one `Result`.

## Implementation

- Add worker `.env` config and `WorkerKernelRuntime.from_env(...)`.
- Add an agentic worker group runner with structured JSON model decisions.
- Add permission-gated repo, write, command, and web tool adapters.
- Pass scoped plan/envelope context through `TaskCompiler`.
- Register LLM-backed groups only when worker LLM mode is enabled.

## Verification

- Unit test env config fallback behavior.
- Unit test permission gates.
- Unit test LLM worker fanout, artifact handoff, and replan failures.
- Run worker-kernel and graph regressions.
