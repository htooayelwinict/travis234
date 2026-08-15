# Contract-parity architecture

Travis234 adopts reference-runtime capabilities through narrow owners rather than widening the behavior-sensitive agent loop. Durable artifacts are the first completed contract-parity slice.

## Durable artifact ownership

- `artifact_store.py` owns immutable SHA-256 objects, physical quotas, verification, permissions, and the cross-process maintenance lock.
- `artifact_manifest.py` owns append-only per-session authorization, strict recovery rules, reference limits, and filtered fork copies.
- `artifacts.py` remains the session-facing adapter for ephemeral and durable references.
- `resource_refs.py` resolves only opaque IDs authorized by the active manifest and returns bounded byte ranges.
- `artifact_gc.py` owns explicit fail-closed collection and shares the promotion maintenance lock.
- session composition opens a durable registry only after both the session path and agent directory are known. In-memory sessions retain the existing ephemeral registry.

Tool code may request promotion only after output is complete. Promotion failure is result metadata, not a tool-effect failure. Subagents may declare workspace-relative regular files at terminal status; public results replace those paths with retained artifact IDs while preserving changed-file metadata separately.

## Invariants

- No object digest or host object path is model-visible.
- A manifest authorizes exact `artifact-<32 lowercase hex>` IDs for one session.
- Promotion plus manifest append and garbage collection serialize on `agent/artifacts/.lock`.
- Collection deletes nothing if any manifest or object entry is unreadable or structurally unsafe.
- Forking copies authorization records, never physical objects, and retains only branch-reachable or explicitly retained records.
- Generic agent-loop ordering, iteration budgets, cancellation, steering, follow-ups, and bounded parallel execution are unchanged.
