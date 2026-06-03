# References

No external documentation is required for this plan.

Internal references:

- `app/schemas.py`
- `app/planner/validator.py`
- `app/worker_kernel/runtime.py`
- `app/worker_kernel/compiler.py`
- `app/worker_kernel/budget.py`
- `app/worker_kernel/registry.py`
- `app/worker_kernel/workers/`
- `tests/test_worker_kernel.py`
- `tests/test_planner.py`
- `tests/test_graph.py`

Design decision:

Replan remains an internal worker-kernel to planner-runtime call. The LangGraph state remains linear for now:

```text
decompressor_node -> planner_node -> worker_kernel_node -> END
```

Worker kernel internally handles:

```text
worker_group instance failure -> retry/replace instance
worker_group plan failure     -> ReplanRequest -> PlannerRuntime.replan(...)
kernel runtime failure        -> structured terminal Result
```
