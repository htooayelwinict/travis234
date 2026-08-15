# Contract-parity architecture

Travis234 adopts reference-runtime capabilities through narrow owners rather than widening the behavior-sensitive agent loop. Durable artifacts and uniform coding-tool policy are completed contract-parity slices.

## Durable artifact ownership

- `artifact_store.py` owns immutable SHA-256 objects, physical quotas, verification, permissions, and the cross-process maintenance lock.
- `artifact_manifest.py` owns append-only per-session authorization, strict recovery rules, reference limits, and filtered fork copies.
- `artifacts.py` remains the session-facing adapter for ephemeral and durable references.
- `resource_refs.py` resolves only opaque IDs authorized by the active manifest and returns bounded byte ranges.
- `artifact_gc.py` owns explicit fail-closed collection and shares the promotion maintenance lock.
- session composition opens a durable registry only after both the session path and agent directory are known. In-memory sessions retain the existing ephemeral registry.

Tool code may request promotion only after output is complete. Promotion failure is result metadata, not a tool-effect failure. Subagents may declare workspace-relative regular files at terminal status; public results replace those paths with retained artifact IDs while preserving changed-file metadata separately.

## Uniform tool-policy ownership

- `coding_agent/policy/types.py` owns the immutable effect, mode, setting, and
  decision vocabulary.
- `coding_agent/policy/engine.py` owns deterministic evaluation, sanitized
  approval requests, exact session grants, and fail-closed broker behavior.
- `session_policy_controller.py` applies policy after extension `tool_call`
  hooks have finalized the tool name and arguments and before execution begins.
- `interactive_tool_approval.py` is the optional native-TUI broker. The policy
  package imports neither the generic agent loop nor the TUI.
- `ToolDefinition.execution_mode` remains a scheduling declaration. Security
  effects are separate metadata and never enter model-facing tool schemas.

Audit mode preserves existing execution while exposing incomplete legacy
extension metadata. Enforce mode denies undeclared tools, auto-allows only a
complete effect-set match, and asks an injected broker for other tools. Machine
modes deliberately have no broker and therefore deny approval-required work.
Project trust remains the earlier resource-loading boundary; a trusted project
may tighten policy but cannot widen its global auto-allow set or lower its mode.

The generic MCP adapter remains one `mcp` proxy and declares all four effects
because the remote method's semantics are not locally provable. Its approval
context contains only server and normalized operation. Internal subagents share
the app-owned prompt queue for presentation but receive their own policy engine
and session grant set.

## Invariants

- No object digest or host object path is model-visible.
- A manifest authorizes exact `artifact-<32 lowercase hex>` IDs for one session.
- Promotion plus manifest append and garbage collection serialize on `agent/artifacts/.lock`.
- Collection deletes nothing if any manifest or object entry is unreadable or structurally unsafe.
- Forking copies authorization records, never physical objects, and retains only branch-reachable or explicitly retained records.
- Tool-policy grants are in-memory session state and never enter JSONL,
  artifacts, acceptance output, resume, fork, clone, or restart.
- Approval diagnostics contain tool name, declared effects, stable reason code,
  and centrally sanitized bounded context; raw arguments and credentials are
  excluded.
- Generic agent-loop ordering, iteration budgets, cancellation, steering, follow-ups, and bounded parallel execution are unchanged.
