# Red-Zone-Free Pi Reliability Parity Design

**Status:** Approved through the user's standing instruction to proceed with the recommended design

**Upstream reference:** Pi `a597371bda2af70372d1323d550483b5f4a0ae36`

**Goal:** Evaluate and port up to five Pi reliability corrections into Travis234 when each is locally reproducible, without changing the core agent runtime or compaction red zones.

## Source and decision policy

Travis234 remains authoritative for product identity, state, architecture, and preserved invariants. The updated local Pi checkout is a behavioral and regression oracle, not a runtime dependency.

Every candidate correction must begin with a focused test that fails against the current Travis234 implementation for the same underlying reason as the Pi regression. If a failure cannot be reproduced locally, that candidate is documented and deferred rather than ported speculatively.

The five candidates are:

1. Ignore directories whose names match context-file names.
2. Cancel every concurrent direct user-bash execution.
3. Classify DNS resolution failures as retryable session errors.
4. Recover once from an OpenAI Codex WebSocket `previous_response_not_found` continuation error.
5. Make Codex provider retry delays abortable.

Provider authentication, model catalogs, constrained sampling, compaction retries, summary usage accounting, RPC additions, and unrelated Pi changes are separate future projects.

## Immutable red zones

Production changes under these paths are forbidden:

- `travis/agent/**`
- `travis/compaction/**`

The implementation must also preserve:

- agent-turn and lifecycle event ordering;
- iteration budgeting;
- ordered tool-result persistence;
- bounded parallel tool execution;
- tool preparation and validation;
- context construction and estimation;
- compaction policy, algorithms, timing, summaries, and transactions;
- session JSONL v3 format and state paths.

The final implementation range must have an empty Git diff under both red-zone path prefixes. Tests may exercise those packages but must not modify their implementation or fixtures that redefine their behavior.

## Delivery architecture

The work is divided into two independently revertible stages. Each correction has its own regression boundary and smallest-owner implementation.

### Stage A: coding-agent edge reliability

Stage A contains the context discovery, direct bash cancellation, and retry-classification corrections. It does not change provider request construction.

#### Context-file candidate validation

`load_context_file_from_dir()` remains the owner of context-file discovery. A candidate is readable context only when it is a regular file. A directory, socket, or other non-file entry with a recognized name is skipped, and discovery continues to the next supported filename.

This preserves:

- global-before-project context ordering;
- ancestor traversal boundaries;
- recognized filename precedence;
- current handling of unreadable files;
- the trust distinction between plain context and behavior-changing resources.

No new diagnostic is required. A directory named `AGENTS.md` is an absent candidate, not a malformed context file.

Expected production owner:

- `travis/coding_agent/resource_loader.py`

Expected regression owner:

- `tests/test_coding_resources_and_services.py`

#### Concurrent direct-bash cancellation

Direct user bash currently stores one active `AbortSignal`, so a second concurrent call can overwrite the first signal and completion of either call can incorrectly report that no bash remains active.

The session will hold a lock-protected set of active direct-bash signals:

1. `execute_bash()` creates one signal for one invocation.
2. The signal is registered before invoking the configured operations.
3. The exact signal is passed to that invocation.
4. `finally` removes only that signal.
5. `abort_bash()` snapshots the active set under the lock and aborts every signal outside the lock.
6. `is_bash_running` reports whether the set is non-empty.

The lock protects compound set operations and snapshots. Calling `abort_bash()` repeatedly is safe because `AbortSignal.abort()` is idempotent. No command waits while holding the registry lock, and callbacks cannot deadlock registry cleanup.

This behavior applies only to direct user commands invoked through `AgentSession.execute_bash()`. Model tool scheduling and managed process ownership remain unchanged.

Expected production owners:

- `travis/coding_agent/agent_session.py` for state initialization only
- `travis/coding_agent/session_bash.py` for registration, cleanup, and cancellation
- `travis/coding_agent/session_models.py` for the existing status projection

Expected regression owner:

- `tests/test_coding_persistence_and_compaction.py`

Despite the historical test filename, no compaction production code or compaction behavior is in scope.

#### DNS retry classification

The session retry classifier will recognize Pi's verified DNS failure fragments:

- `getaddrinfo`
- `ENOTFOUND`
- `EAI_AGAIN`

Matching remains case-insensitive because the existing implementation lowercases provider errors. The added constants therefore use lowercase normalized forms internally.

The existing non-retryable provider-limit classification remains authoritative and runs first. A quota or balance error containing a DNS-looking fragment must remain non-retryable.

This change affects only whether the existing session retry controller uses its existing bounded retry policy. It does not add attempts, alter backoff calculation, or change the agent loop.

Expected production owner:

- `travis/coding_agent/session_types.py`

Expected regression owner:

- `tests/test_coding_persistence_and_compaction.py`

### Stage B: Codex provider transport recovery

Stage B is contained in the OpenAI Codex provider runtime and its provider-contract tests.

#### Missing cached continuation recovery

A cached WebSocket request may include a previous response identifier that the backend no longer recognizes. When the backend returns the exact error code `previous_response_not_found`, Travis234 will make one recovery attempt without the stale continuation.

Required behavior:

1. Detect the structured provider error code rather than matching general prose.
2. Mark the cached continuation unavailable for the retry.
3. Rebuild and resend the request without `previous_response_id`, using the full request input required for a valid standalone request.
4. Permit at most one missing-continuation recovery for one provider call.
5. Do not emit duplicate start events or persist a partial failed response.
6. If the retry succeeds, retain its returned response ID as the next cache continuation.
7. If the same error repeats, use the existing terminal provider-error path.
8. Preserve existing connection-limit recovery and session-scoped SSE fallback behavior for unrelated WebSocket failures.

The implementation must not convert arbitrary provider errors into missing-continuation recovery. It must also avoid clearing cached state belonging to unrelated sessions.

Expected production owner:

- `travis/ai/providers/codex_runtime.py`

Expected regression owner:

- `tests/test_reference_runtime_contract.py`

#### Abortable Codex retry delays

The Codex SSE retry path currently checks the abort signal before and after a blocking `time.sleep()`. A long provider-directed delay therefore remains unresponsive until the sleep finishes.

A provider-local wait helper will:

1. Return immediately for non-positive delays.
2. Wait for either the requested delay or the existing signal's abort callback.
3. Unsubscribe the temporary callback in `finally`.
4. Raise the existing `Request was aborted` error when cancellation wins.
5. Preserve current retry counts, exponential delay calculations, `Retry-After` handling, and maximum-delay clamping.

The helper will use only the signal's public `aborted` and `add_callback()` behavior. It will not add methods to the stable agent `AbortSignal` type.

Expected production owner:

- `travis/ai/providers/codex_runtime.py`

Expected regression owner:

- `tests/test_reference_runtime_contract.py`

## Data flows

### Concurrent direct bash

1. Caller enters `execute_bash()`.
2. Session creates and registers an invocation-owned signal.
3. Bash operations execute with that signal.
4. Output is spooled and persisted through the unchanged result path.
5. Completion removes only the invocation-owned signal.
6. A concurrent abort snapshots all registered signals and aborts each one.

### Session retry classification

1. Provider returns an assistant error through the unchanged provider/session boundary.
2. Existing non-retryable limit checks run.
3. Existing retryable markers, including the three DNS additions, are evaluated.
4. Existing bounded session retry policy decides attempts and delay.
5. Agent continuation remains owned by the existing session-turn controller.

### Codex missing continuation

1. A cached WebSocket request is assembled for one session.
2. The provider rejects its prior response identifier.
3. Provider runtime recognizes the structured error code.
4. Provider runtime rebuilds one standalone retry without stale continuation state.
5. The successful response becomes the new continuation point, or the repeated failure terminates normally.

### Abortable provider delay

1. A retryable Codex SSE attempt calculates its existing delay.
2. Provider-local wait subscribes to the existing abort signal.
3. Timeout permits the next attempt; cancellation raises immediately.
4. The temporary subscription is always removed.

## Error handling and concurrency guarantees

- Context discovery skips non-file candidates without masking a later valid filename.
- Bash registration and cleanup occur in `try`/`finally`.
- Bash cancellation never holds the registry lock while invoking signal callbacks.
- An invocation finishing concurrently with abort may be present in the snapshot; abort remains harmless and idempotent.
- DNS markers do not override known non-retryable quota and billing failures.
- Missing-continuation recovery is bounded to one retry and keyed to the structured error code.
- Existing Codex connection-limit and SSE fallback behavior remains intact.
- Abort during provider backoff produces the existing aborted-request contract and does not start another HTTP attempt.
- No new persistence entries, public event types, state locations, or environment variables are introduced.

## Verification strategy

Every correction follows red-green-refactor discipline:

1. Add one focused regression that fails for the expected reason.
2. Run it and record the failure.
3. Implement the smallest owner-local correction.
4. Run the focused and neighboring suites.
5. Refactor only after the regression is green.

Required focused coverage:

### Context discovery

- A directory named `AGENTS.md` is ignored.
- Discovery continues to a later valid supported context filename.
- Existing ancestor and global context ordering remains unchanged.

### Concurrent bash

- Two direct bash calls can remain active simultaneously.
- `is_bash_running` stays true when either invocation remains active.
- One invocation completing does not clear the other's signal.
- `abort_bash()` cancels both active calls.
- Signals are removed after normal completion, cancellation, and raised execution errors.
- Existing streamed-output sanitization and persistence remain unchanged.

### DNS classification

- `getaddrinfo`, `ENOTFOUND`, and `EAI_AGAIN` each trigger the existing bounded retry.
- Matching is case-insensitive.
- A non-retryable quota/balance error still does not retry even if it contains one of the new fragments.
- Retry attempt count and event ordering remain unchanged.

### Codex missing continuation

- First request establishes cached continuation.
- Second request receives `previous_response_not_found`.
- Exactly one retry omits the stale previous-response identifier and sends sufficient standalone input.
- Successful recovery installs the new response ID.
- A repeated missing-continuation error terminates without a loop.
- Start and terminal stream events are emitted once.
- Existing connection-limit and SSE-fallback tests remain green.

### Abortable delay

- Abort during a positive retry delay returns promptly.
- No subsequent HTTP attempt starts after abort.
- Temporary abort callbacks are unsubscribed.
- Zero-delay and successful retry behavior remain unchanged.
- Provider delay caps remain unchanged.

Required focused commands will be selected from:

```text
.venv/bin/pytest -q tests/test_coding_resources_and_services.py
.venv/bin/pytest -q tests/test_coding_persistence_and_compaction.py
.venv/bin/pytest -q tests/test_reference_runtime_contract.py
```

Final repository qualification:

```text
.venv/bin/pytest -q
npm run test:launcher
npm run pack:launcher
.venv/bin/python -m build
```

Relevant release-container smoke checks must also run according to repository guidance.

The implementation plan must define a baseline commit and prove red-zone integrity with:

```text
git diff --exit-code <baseline>..HEAD -- travis/agent travis/compaction
```

The exact baseline is selected immediately before implementation. A non-empty result blocks completion.

## Commit and rollback boundaries

The implementation plan should use one correction per commit-sized task:

1. Context candidate regular-file guard.
2. Concurrent direct-bash cancellation registry.
3. DNS retry classification.
4. Codex missing-continuation recovery.
5. Abortable Codex retry wait.
6. Final qualification evidence, only if documentation or verification records are required.

Each correction must remain independently revertible. Stage A must be fully green before Stage B begins. A Stage B failure must not block shipping independently validated Stage A corrections.

## Deferred upstream work

This design intentionally does not cover:

- compaction and branch-summary provider retries;
- compaction retry lifecycle events;
- persisted tool, compaction, or branch-summary usage;
- model catalog regeneration or ETag refresh;
- Claude Opus 5 or Qwen Token Plan catalog additions;
- OpenRouter, Kimi Code, or Anthropic bearer-token authentication;
- constrained tool sampling;
- RPC bash update events or thinking-level queries;
- external editor support;
- pending streaming stop reasons;
- SQLite session storage;
- Pi Agent Harness restructuring.

Those changes require their own focused designs and must not expand this implementation.

## Acceptance criteria

The project is complete only when:

- every shipped correction began as a locally failing regression;
- non-reproducible candidates were deferred without production changes;
- every behavior that is shipped matches its bounded contract;
- no production file under either red-zone path changed;
- agent ordering, iteration budgets, bounded tool execution, context construction, compaction, and JSONL persistence remain unchanged;
- focused and full Python tests pass;
- npm launcher tests and dry-run packaging pass;
- Python sdist and wheel builds pass;
- relevant release-container smoke checks pass;
- no credentials or alternate Travis234 state paths are introduced.
