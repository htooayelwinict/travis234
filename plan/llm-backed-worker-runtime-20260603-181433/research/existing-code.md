# Existing Code

- `WorkerKernelRuntime` already owns validation, budget, retries, replan, and artifact stores.
- `TaskCompiler` already turns plan steps into tasks and validates required input artifacts.
- `WorkerRegistry` already resolves one `worker_type` to one runnable group.
- Existing workers are stubs and can remain as test doubles.
