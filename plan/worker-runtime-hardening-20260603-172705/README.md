# Worker Runtime Hardening Plan

Goal: turn the current worker kernel from a sequential mock executor into a strict, kernel-controlled runtime that can validate plans, compile scoped tasks, spawn worker-group instances, classify failures, retry/replace instances, and request internal replans without routing through graph state.

This plan keeps the existing decompressor and planner architecture intact. The worker kernel becomes the enforcement boundary between planner output and real tool-capable worker execution.

Primary recommendation: implement contract hardening first, then kernel failure classification, then worker-group orchestration. Do not start by replacing all workers with real tools; doing so before strict schemas and artifact validation would make shallow or unsafe outputs look successful.

Key acceptance criteria:

- Worker kernel validates `Envelope + Plan` before dispatch.
- Missing input artifacts never disappear silently.
- Worker-facing schemas use strict typed contracts.
- `worker_type` is treated as a worker group, with instance attempts controlled by kernel policy.
- Instance failures are retried/replaced by the kernel.
- Plan-level failures produce internal `ReplanRequest` and call `PlannerRuntime.replan(...)`.
- Replan remains internal to worker runtime, not a LangGraph reroute.
- Budget and retry ceilings are kernel-owned and consistently accounted.
- Existing planner/decompressor behavior remains compatible.

Main plan file: `plan.md`

Implementation status:

- Completed: contracts, kernel preflight validation, missing artifact handling, structured issues, retry accounting, worker-group wrapper support, and replan payload cleanup.
- Deferred: replacing placeholder workers with real tool-capable workers and running a live LLM end-to-end workflow after real workers exist.
