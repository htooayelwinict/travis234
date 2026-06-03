# Requirements

User-provided requirements:

1. Add planner validation inside worker kernel runtime.
2. Fix missing input artifacts in task compilation. Missing required artifacts must produce `needs_replan`, `blocked`, or `failed`, with `missing_artifacts` in metadata.
3. Kernel runtime controls everything after receiving envelope and plan: validation, worker spawning, budget ceilings, instance replacement, replan requests, and finalization.
4. Treat `worker_type` as a worker group, not a single instance. Example: `web_research_worker` may run source discovery, source extraction, and citation formatting instances.
5. Classify worker failures into instance-level failures and plan-level failures.
6. Instance-level failures should be retried/replaced inside kernel and logged for future debugging.
7. Plan-level failures should trigger the existing internal replan path.
8. Replan must remain internal runtime behavior, not graph state routing.
9. Tighten schemas for `PlanStep`, `Task`, permissions, result status, artifacts, issues, and replan signals.
10. Fix current retry duct tape. Kernel decides budget and retry behavior.

Corrections and additions:

- Keep `worker_type` stable in planner schema. Use registry-side group definitions for instance orchestration instead of forcing planner to describe every internal worker instance.
- Split completed artifacts from partial/failed-step artifacts. A replan request should not accidentally treat failed-step output as completed truth.
- Add a kernel run state object separate from LangGraph `RuntimeState`.
- Add event logs and attempt IDs. Future debugging needs instance attempt history, not just final summaries.
- Add status taxonomy before real tools: otherwise workers can invent statuses and bypass kernel logic.
- Add timeout/cancellation handling for long-running instances.
- Add artifact provenance and trust level before mutation workers rely on discovered paths.
- Keep backward compatibility for current tests and historical plan logs during the transition.
