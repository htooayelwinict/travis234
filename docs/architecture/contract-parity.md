# Contract-parity architecture

Travis234 adopts reference-runtime capabilities through narrow owners rather
than widening the behavior-sensitive agent loop. Durable artifacts, uniform
coding-tool policy, and bounded language services are completed contract-parity
slices.

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

## Bounded language-service ownership

- `language_services/config.py` owns strict, trust-aware server definitions and
  deterministic workspace-root selection. Travis234 does not install or own the
  configured executable.
- `jsonrpc.py` owns framed stdio JSON-RPC, minimal child environments, request
  cancellation, stderr bounds, and graceful process shutdown.
- `documents.py` owns workspace containment, versions, hashes, and conversion
  between the tool's zero-based UTF-16 coordinates and a server's negotiated
  position encoding.
- `manager.py` owns lazy server startup, document synchronization, three-server
  LRU capacity, restart budgets, configuration generations, and session close.
- `workspace_edit.py` owns expiring action/preview tokens, normalized edit
  previews, content-hash preconditions, deterministic cross-file locking, and
  explicit best-effort restoration reports.
- `tool.py` is the one model-facing `lsp` boundary. Reads are bounded and can
  promote completed oversized output to artifacts. Mutation requires preview
  followed by the exact token; apply never requests the server.
- `interactive_lsp.py` reads manager status only. It cannot start a server and
  never exposes executable paths or initialization options.

Language services are session-owned. Session replacement and app shutdown
close them before the app tears down its managed-process owner. Server retries
are recovery within a live session, not durable scheduling, and exhausted
restart budgets remain visible until that manager is replaced or reloaded.
Preview/apply reduces accidental edits but cannot provide filesystem-wide
isolation from unrelated host processes; failed restoration is therefore
reported honestly instead of claimed as guaranteed rollback.

## Typed coordination ownership

- `agent_roles.py` owns strict immutable role resources and their projection
  from the shared capability registry. Global roles load from
  `~/.travis234/agent/roles/*.json`; project roles load only from a trusted
  `.travis234/roles/*.json`; packages may contribute the same resource kind.
- `subagent_roles.py` freezes a role at spawn by intersecting its tools and
  effects with the parent's already-active tool definitions. Missing ceilings
  inherit, explicit empty ceilings grant nothing, undeclared typed tools are
  excluded, and a requested timeout can only lower the role timeout.
- `subagent_result_types.py` retains the public task-result vocabulary, while
  `subagent_results.py` validates the bounded `summary`/`output`/`artifacts`
  envelope and converts schema mismatch into a failed result rather than an
  exception from `wait()`.
- `subagent_supervision.py` owns immutable revisioned snapshots,
  subscriptions, and shaped steering/cancellation results around the existing
  futures scheduler. It never owns child execution or call a subscriber while
  holding the supervisor lock.
- `interactive_subagents.py` and `components/subagent_roster.py` are the TUI
  projection. `/agents status`, `inspect`, `steer`, and `cancel` consume local
  supervisor state; they do not add goals, role context, or raw traces to the
  roster.

The established executor remains capped at three concurrent children and one
child level. `canSpawn` and `maxDepth` are validated role contract fields, but
they cannot widen that authoritative depth-one ceiling; typed children do not
receive delegation tools today. Role model selection uses only the existing
`worker` or `reviewer` route. Legacy subagents remain valid when no role
definition matches, and `/subagents` continues to select the existing
delegation skill rather than becoming a supervisor command.

## Explicit memory ownership

- `coding_agent/memory/types.py` owns immutable limits and scope/provenance
  vocabulary; global settings alone enable memory or allow global scope.
- `coding_agent/memory/store.py` owns the bounded private SQLite schema,
  normalized idempotent facts, project hashing, expiry visibility, and exact-ID
  deletion under the existing agent state root.
- `coding_agent/memory/tool.py` owns exactly one explicit model tool. It never
  retains or recalls automatically, declares conservative `read` plus `write`
  effects, and exposes only action/scope to approval context.
- `coding_agent/memory/safety.py` rejects credential-shaped retention and
  repeats an untrusted-data envelope around each recalled fact. Complete output
  too large for the inline limit uses the existing durable artifact boundary.
- `interactive_memory.py` reads metadata from the active session's existing
  store connection. `/memory status` cannot open storage or display fact/query,
  project-path/hash, session, or credential material.

Memory is a private opt-in notebook, not hidden context. Project facts are
isolated by a canonical-path hash; global facts exist only when the user
explicitly allows that scope. Corruption and capacity errors become bounded
tool/status diagnostics without changing conversation history. Memory is not
part of the internal subagent coding allowlist, so enabling it does not widen
child capabilities or alter expanded result packs.

## Optional integration conformance ownership

- `evals/optional_tool_conformance.py` exercises optional extension entry
  points through the normal trust-aware resource loader and existing tool
  policy engine. It does not import or adapt a browser/desktop runtime.
- `evals/optional_integration_fixture.py` registers synthetic browser and
  computer tools for trust, effects, approval, cancellation, bounded-output,
  artifact, sanitized-audit, and shutdown checks. It performs no real browser,
  accessibility, or desktop action.
- Project integration code must remain absent before trust. Trusted tools must
  declare effects, propagate cancellation, bound model-visible output, keep
  arguments and credentials out of policy records, and clean owned resources
  during session shutdown.
- Optional packages and MCP servers remain outside the root dependency graph.
  Direct imports of the generic agent loop or TUI from an integration are a
  conformance failure because those layers are not extension APIs.

The conformance gate proves Travis contract compatibility, not the correctness
of a third-party browser driver, OS accessibility behavior, or a vendor's
security model. A real integration still requires separate platform tests and
permission review; passing this gate grants no trust or policy bypass.

## Observe-only operation-journal ownership

- `coding_agent/operations/store.py` owns the versioned SQLite schema,
  transactions, bounds, restrictive permissions, and explicit settled-row
  pruning at the existing agent state root.
- `coordinator.py` owns session operation order and fail-open degradation.
  Provider/tool intents are durable before execution and settlements are
  durable afterward without changing generic agent-loop ordering.
- `recovery.py` compares runtime PID plus process creation time, with a bounded
  heartbeat fallback only when liveness cannot be determined. Dead-runtime
  intents become uncertain atomically; recovery exports no replay executor.
- `session_operations.py` binds one operation to a real prompt/continue turn.
  Provider retries and tool continuations remain separate effects, and usage is
  idempotent by bounded source identity.
- `interactive_operations.py` projects read-only metadata for the active
  session fingerprint. It never renders registers, fingerprints, provider
  payloads, tool arguments/results, or content from JSONL.

SQLite and JSONL intentionally have different authority. JSONL is durable
conversation history and remains independently resumable. The operation
journal observes effect boundaries but is not a transactional ledger and does
not synthesize messages. A crash between effect execution and settlement is
therefore reported as uncertain with replay policy `never`; exactly-once is not
claimed. Corruption, capacity exhaustion, and journal I/O errors degrade
observation without failing the coding turn. Retention is explicit and cannot
be triggered by TUI inspection.

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
- Language-server commands and initialization options never enter status,
  acceptance output, model-facing schemas, or session history.
- Role reload affects new children only; a running task retains its frozen
  tools, effects, model role, context, timeout, schema, and artifact policy.
- Acceptance output contains role names and sanitized provenance plus
  supervisor limits only. It excludes role context, goals, result bodies,
  model credentials, and host paths.
- Operation-journal acceptance output contains mode, schema version, and
  contract counts only. It excludes operation IDs, session fingerprints,
  effect names, usage identities, provider/model names, and all content.
- Memory acceptance output contains disabled-by-default settings, bounds,
  null counts for unopened storage, and explicit false automatic-retention and
  automatic-injection flags only. It excludes facts, queries, tags, project
  identity, session identity, and credentials.
