# appv22 Pi + Hermes Compliance Audit

## Scan Inputs

`appV2.2/appv22` contains 52 Python files and about 10,481 lines. The scanned Pi/Hermes reference trees contain 351 source files and about 146,490 lines. The current appv22 package is a compact Python port aligned to the planned AI, agent-loop, coding-agent, TUI, and Hermes compaction contracts, not a literal file-for-file clone of every provider, CLI, auth, export, RPC, and platform-specific surface in those reference trees.

Reference files scanned for this pass:

- `pi/packages/ai/src/stream.ts`
- `pi/packages/ai/src/utils/event-stream.ts`
- `pi/packages/agent/src/agent.ts`
- `pi/packages/agent/src/agent-loop.ts`
- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/src/core/agent-session-runtime.ts`
- `pi/packages/coding-agent/src/core/tools/edit.ts`
- `pi/packages/coding-agent/src/core/tools/file-mutation-queue.ts`
- `pi/packages/coding-agent/src/core/tools/read.ts`
- `pi/packages/coding-agent/src/core/tools/bash.ts`
- `pi/packages/coding-agent/src/core/tools/write.ts`
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts`
- `pi/packages/tui/src`
- `hermes-agent/agent/context_compressor.py`
- `hermes-agent/agent/conversation_loop.py`
- `hermes-agent/agent/turn_context.py`
- `hermes-agent/agent/conversation_compression.py`

## Package Map

| appv22 area | Local files | Reference target | Status |
| --- | --- | --- | --- |
| AI stream/types | `ai/*.py`, `ai/providers/*.py` | `pi/packages/ai/src` | Mostly aligned for the planned runtime slices. Core stream/event/model types, provider option forwarding, provider env API-key fallback, simple/regular stream entrypoints, and OpenAI-compatible env provider routing are ported. Remaining AI work should come from new targeted regressions for provider-specific surfaces outside the compact appv22 runtime. |
| Agent core | `agent/agent.py`, `agent/agent_loop.py`, `agent/types.py` | `pi/packages/agent/src/agent.ts`, `agent-loop.ts`, `types.ts` | Mostly aligned for the planned slices: active-run guard, per-run abort signal, queue modes, idle settlement, richer stream options, queued assistant-continue behavior, loop event settlement, termination, and prepare-turn snapshots are ported. |
| Coding session | `coding_agent/agent_session.py`, `system_prompt.py` | `pi/packages/coding-agent/src/core/agent-session*.ts`, `sdk.ts`, `system-prompt.ts` | Mostly aligned for the planned slices. Definition-first tools, streaming preflight/queue events, compaction/retry/model/thinking/session-info events, JSONL persistence/branching, compaction-summary reload, bash execution messages and direct bash execution, runtime new/switch/fork/import, extension lifecycle, tree navigation summaries, custom entries, and package resource loading are ported. |
| Built-in tools | `coding_agent/tools/*.py` | `pi/packages/coding-agent/src/core/tools/*.ts` | Mostly aligned for planned tool slices. Read/write/edit/bash/find/grep/ls now have Pi-style operations/results for the covered contracts; remaining tool-related gaps are mainly richer TUI render components and future regressions outside the current checklist. |
| Compaction | `compaction/compressor.py`, `compaction/timing.py` | Hermes compressor/timing/session rotation files and Pi compaction | Mostly aligned for Phase 5: dual-pass timing, boundary alignment, sanitization, tail anchoring, summary prompt safety, failure/cooldown/fallback behavior, lineage persistence, and manual feedback are ported. |
| TUI/rendering | `tui/*.py`, `cli.py` | `pi/packages/tui/src`, Pi interactive mode | Mostly aligned for the planned slices: real interactive entry, editor/input behavior, selection/list components, markdown rendering, status/footer surfaces, user/skill invocation rendering, special branch/compaction/custom message components, `!`/`!!` bash command routing and bash execution rendering, tool render hooks, compact/expanded read rendering, initial history rendering, and differential redraw tests are ported. |

## Fixed During This Pass

### Agent Active Run Guard

Reference: `pi/packages/agent/src/agent.ts` uses `activeRun` to reject a second `prompt()`/`continue()` while a run is in progress.

Appv22 before this pass set `state.is_streaming` but still allowed a second `prompt()` to enter the loop. Added `test_agent_rejects_prompt_while_streaming` and implemented a run-state lock in `appV2.2/appv22/agent/agent.py`.

### Agent Per-Run Abort Signal

Reference: `pi/packages/agent/src/agent.ts` creates a fresh `AbortController` inside `runWithLifecycle()` and `abort()` only targets `activeRun`.

Appv22 previously reused one lifetime `AbortSignal`, so aborting one run could leak into later tool-call preparation. Added `test_agent_abort_signal_is_fresh_for_next_prompt` and now reset `AbortSignal` at the start of each accepted run.

### Agent Stream Option Propagation

Reference: `pi/packages/agent/src/agent-loop.ts` passes provider options into `streamFunction()`, including the active `signal`, resolved API key, and reasoning/config values.

Appv22 previously called `stream_function(config.model, llm_context, None)`. Added `test_agent_stream_options_include_active_signal`, added `signal` and retry/timeout parity fields to `StreamOptions`, and now pass a `SimpleStreamOptions` object into the stream function.

### Agent Queued Continue

Reference: `pi/packages/agent/src/agent.ts` drains queued steering or follow-up messages when `continue()` is called after an assistant turn. Its default queue mode is `one-at-a-time`.

Appv22 previously delegated directly to `run_agent_loop_continue()`, which rejects assistant-ending transcripts before queues can be consumed. Added regressions for queued follow-up and one-at-a-time steering from an assistant tail, ported `PendingMessageQueue`, and updated `continue_()` to run queued messages before throwing.

### Agent Listener Signal and Idle Settlement

Reference: `pi/packages/agent/src/agent.ts` gives listeners the active abort signal and exposes `waitForIdle()`, which resolves after `agent_end` listeners settle.

Appv22 previously had one-argument listeners and no idle API. Added regressions for `wait_for_idle()` waiting on a blocking `agent_end` listener and for two-argument listeners receiving the active signal. Implemented `wait_for_idle()` with a per-run event and arity-aware listener dispatch.

### Agent Failure Lifecycle

Reference: `pi/packages/agent/src/agent.ts` catches run executor failures and emits a synthetic assistant failure message through `message_start`, `message_end`, `turn_end`, and `agent_end`.

Appv22 previously let provider/loop exceptions escape without appending an assistant error message. Added `test_prompt_failure_emits_assistant_error_lifecycle` and implemented `_handle_run_failure()` in `Agent`.

### Agent Provider and Session Stream Options

Reference: `pi/packages/agent/src/agent.ts` exposes `sessionId`, `thinkingBudgets`, `transport`, `maxRetryDelayMs`, `onPayload`, and `onResponse`, and `agent-loop.ts` forwards them into stream options.

Appv22 previously only forwarded signal, API key, temperature, max tokens, and reasoning. Added `test_agent_forwards_provider_runtime_stream_options` and ported the missing option fields through `Agent`, `AgentLoopConfig`, and `SimpleStreamOptions`.

### Agent Public Queue API

Reference: `pi/packages/agent/src/agent.ts` exposes steering/follow-up mode getters/setters, queue clearing methods, and `hasQueuedMessages()`.

Appv22 previously had internal queues only. Added `test_agent_queue_status_clear_and_modes` and ported `steering_mode`, `follow_up_mode`, `clear_steering_queue()`, `clear_follow_up_queue()`, `clear_all_queues()`, and `has_queued_messages()`.

### Agent Prepare-Turn Signal

Reference: `pi/packages/agent/src/agent.ts` wraps `prepareNextTurn` so the callback receives the active abort signal for the current run.

Appv22 previously had no `Agent(..., prepare_next_turn=...)` wrapper hook, so users could not observe the active run signal during turn setup. Added `test_agent_prepare_next_turn_receives_active_abort_signal` and ported the wrapper hook into `Agent._build_config()`.

### Agent Tool Update Drain

Reference: `pi/packages/agent/src/agent-loop.ts` stores update-event emissions during tool execution and awaits them before emitting `tool_execution_end`.

Appv22 previously emitted update events synchronously and could finalize a tool before an async-equivalent sink had settled. Added `test_tool_execution_update_emit_settles_before_tool_execution_end` and now waits for future-like or event-like emit results from `tool_execution_update` before returning `tool_execution_end`.

### Agent Tool Emit Settlement and Termination

Reference: `pi/packages/agent/src/agent-loop.ts` awaits `emit(...)` calls throughout sequential and parallel tool execution, emits tool result messages after each finalized tool end, and terminates the next assistant turn only when every finalized tool result has `terminate: true`.

Appv22 previously ignored return values from ordinary event sink calls, so a future-like `tool_execution_start` sink could still be unsettled while the tool had already started executing. Added `test_tool_execution_start_emit_settles_before_tool_runs` across sequential and parallel modes, added `test_all_terminating_parallel_tool_results_stop_without_next_assistant_turn`, and ported a Python `_emit_event()` settlement helper for normal loop events while preserving the Pi-style batch drain for tool update events.

### Agent Prepare-Turn Snapshot Isolation

Reference: `pi/packages/agent/src/agent-loop.ts` applies `prepareNextTurn` updates by rebinding loop-local `currentContext` and `config`, leaving the caller's `initialConfig` object unchanged. It also builds a fresh `shouldStopAfterTurn` context after any snapshot context has been applied.

Appv22 previously mutated `AgentLoopConfig.model` and `reasoning` in place, and then passed the pre-snapshot context into `should_stop_after_turn`. Added `test_prepare_next_turn_snapshot_updates_loop_without_mutating_config` and `test_should_stop_after_turn_receives_prepare_next_turn_context_snapshot`, ported local config rebinding with `dataclasses.replace()`, and now gives `should_stop_after_turn` a post-snapshot context.

### Coding-Agent Definition-First Tool Registry

Reference: `pi/packages/coding-agent/src/core/agent-session.ts` keeps a definition-first tool registry, exposes active/all tool APIs, filters tools through allowed/excluded lists, and rebuilds the base system prompt when active tools change. `sdk.ts` starts normal sessions with `read`, `bash`, `edit`, and `write`, while explicit allowlists become the initial active tool set and excluded names win afterward.

Appv22 previously activated tools by parsing prompt words, so greetings had no tools and repo-inspection prompts switched to a read-only heuristic set. Added regressions for default active coding tools on any prompt, prompt-text-independent repo inspection, Pi-style allowlist/registry APIs, and caller-provided `ToolDefinition` wrappers. Removed the heuristic selector, added `get_active_tool_names()`, `get_all_tools()`, `get_tool_definition()`, and `set_active_tools_by_name()`, synthesized definitions from bare `AgentTool` overrides, and wrapped caller-provided definitions into executable tools without importing Pi modules.

### Coding-Agent Queue Update Events

Reference: `pi/packages/coding-agent/src/core/agent-session.ts` exposes session-level `subscribe()`, tracks visible steering/follow-up queue text, emits `queue_update` when queues change, and removes a queued user message before forwarding that message's `message_start` event.

Appv22 previously exposed only the lower-level `Agent` subscription and queue APIs, so UI/session callers could not observe Pi-shaped queue state through `AgentSession`. Added `test_agent_session_emits_queue_update_events_before_delivered_user_message`, ported `AgentSession.subscribe()`, `steer()`, `follow_up()`, `continue_()`, `clear_queue()`, `pending_message_count`, `get_steering_messages()`, `get_follow_up_messages()`, and `QueueUpdateEvent`, and subscribed the session wrapper to core agent events to forward events with Pi queue-removal ordering.

### Coding-Agent Prompt Streaming Behavior

Reference: `pi/packages/coding-agent/src/core/agent-session.ts` checks `isStreaming` inside `AgentSession.prompt()`. While streaming, a prompt without `streamingBehavior` fails during preflight; with `streamingBehavior: "steer"` it queues a steering message, and with `"followUp"` it queues a follow-up message without entering the core agent `activeRun` guard.

Appv22 previously delegated `AgentSession.prompt()` directly to `Agent.prompt()`, so concurrent session prompts always hit the lower-level active-run rejection and could not be routed into steering/follow-up queues. Added `test_agent_session_prompt_queues_during_streaming_by_behavior`, added `AgentSession.is_streaming`, `streaming_behavior`, `preflight_result`, and image-aware prompt handling, and routed streaming prompts through the session queue APIs before the core agent sees them.

### Coding-Agent Session Events, Retry, and Compaction

Reference: `pi/packages/coding-agent/src/core/agent-session.ts` defines the session-facing event union for `compaction_start`, `compaction_end`, `auto_retry_start`, `auto_retry_end`, `thinking_level_changed`, and `session_info_changed`, augments `agent_end` with `willRetry`, and exposes model/thinking/session-name state APIs while keeping model selection as an extension event rather than a session-listener event.

Appv22 previously had only queue updates plus forwarded lower-level agent events. Added regressions for session name and thinking-level events, model state updates without inventing a non-Pi listener event, manual compaction start/end events, successful transient retry events, and retry exhaustion failure events. Ported Pi-shaped event dataclasses with camelCase aliases, session name and thinking-level setters, `set_model()` state updates, a sync manual `compact()` event wrapper around `CompactionManager`, retry attempt tracking, retryable error detection, bounded retry start/end events, and `agent_end.willRetry` decoration.

### Coding-Agent Session Persistence and Branching

Reference: `pi/packages/coding-agent/src/core/session-manager.ts` persists sessions as JSONL with a `session` header and typed entries (`message`, `thinking_level_change`, `model_change`, `compaction`, and `session_info`) linked by `id`/`parentId`; active context is rebuilt by walking the current leaf branch.

Appv22 previously kept `AgentSession` history in memory only. Added regressions for typed JSONL persistence/reload and branching from an earlier entry. Ported a local `SessionStore` with versioned headers, message serialization/deserialization, typed state entries, active branch rebuilding, `session_entries`, `session_path`, and `branch(entry_id)`. Wired message-end, thinking-level, model, session-info, and manual compaction hooks into the store without importing Pi code.

### Write Tool Operation and Abort Queue Parity

Reference: `pi/packages/coding-agent/src/core/tools/write.ts` exposes pluggable `WriteOperations`, calls `mkdir(dirname)` and `writeFile()` inside `withFileMutationQueue()`, checks `signal.aborted` after each filesystem await-equivalent step, and keeps the queue locked while an aborted write operation is still in flight.

Appv22 previously used direct local `os.makedirs()` / `open().write()` calls only, returned appv22-specific details, and could not prove the Pi abort queue invariant with custom operations. Added `test_write_tool_keeps_queue_locked_until_aborted_write_settles`, ported `WriteOperations`, kept abort checks before mkdir, after mkdir, and after write, switched the success text to Pi's `Successfully wrote ... bytes to ...` shape, and removed write result details.

### Find/Grep/Ls Path and Truncation Parity

References: `pi/packages/coding-agent/src/core/tools/find.ts`, `grep.ts`, `ls.ts`, `path-utils.ts`, `truncate.ts`, and Pi regressions for path-based find globs.

Appv22 previously matched `find` only against basenames with `max_results`, ignored Pi `grep` fields such as `glob`, `literal`, `ignoreCase`, `context`, and `limit`, returned appv22-specific no-match/details text, allowed `ls` to ignore entry limits, and did not apply scoped `.gitignore` rules during local traversal. Path helpers also skipped Pi input normalization for leading `@` and Unicode space variants. Added regressions for path-based `find` globs and limit notices, scoped nested `.gitignore` behavior for `find`/`grep`, `grep` glob/literal/limit/no-match behavior, `ls` entry limit notices, path normalization, and `read` using normalized paths. Ported Pi-style schemas, pluggable operation dataclasses for `find`/`grep`/`ls`, hierarchical `.gitignore` filtering, POSIX relative output formatting, limit warning/details keys, byte truncation notices, long grep-line truncation, and `resolve_read_path()` fallback wiring into `read`.

### Bash Operations, Streaming, Prefix, and Abort Parity

Reference: `pi/packages/coding-agent/src/core/tools/bash.ts` and `pi/packages/coding-agent/src/core/tools/output-accumulator.ts` expose `BashOperations`, `createLocalBashOperations()`, `commandPrefix`, `spawnHook`, streaming `onUpdate` snapshots, process-tree abort, timeout handling, and Pi `fullOutputPath` details.

Appv22 previously used one blocking `subprocess.run()` call, so callers could not inject command backends, prefix commands, rewrite spawn context, receive live partial output, or abort a running process tree. Added regressions for operation injection plus prefix/spawn hook resolution, initial and streaming partial updates, full-output paths on timeout/abort errors, local operation env streaming, and local abort behavior. Ported `BashOperations`, `BashExecOptions`, `BashSpawnContext`, `create_local_bash_operations()`, a Python `OutputAccumulator`, streaming stdout/stderr readers, Pi-style output formatting/details, command prefixing, spawn hooks, process-group kill on abort/timeout, and `fullOutputPath` detail keys.

### Read Operations, Image, Abort, and Compact Render Parity

Reference: `pi/packages/coding-agent/src/core/tools/read.ts`, `pi/packages/coding-agent/src/utils/image-resize.ts`, and `pi/packages/coding-agent/test/tool-execution-component.test.ts` expose pluggable `ReadOperations`, auto-resize image control flow, non-vision model notices, abort checkpoints around filesystem awaits, Pi-only truncation details, and compact render labels for `SKILL.md`, resource files, and package docs.

Appv22 previously read local files directly, attached truncation details even when no truncation occurred, had no injected read operations, no resize-omission path, no non-vision image note, no abort checkpoints after access/detection/read, and rendered all read calls as plain `read <path>`. Added regressions for operations ordering, abort after access, text reads with no details when not truncated, resize failure omission with non-vision text, resized-image dimension notes with image attachments, and compact/collapsed render classification. Ported `ReadOperations`, `ReadImageResizeResult`, `auto_resize_images`, injected `image_resizer`, Pi dimension-note wording, non-vision notices, direct image omission on failed resize, compact docs/resource/skill labels with line ranges, and collapsed read-result hiding.

### Hermes Tool-Pair Boundary and Sanitization

Reference: `hermes-agent/agent/context_compressor.py` aligns compression boundaries around assistant tool calls and tool results, then sanitizes the final compressed transcript so API context never contains orphan tool results or dangling tool calls.

Appv22 previously spliced `[head, summary, tail]` without boundary alignment or post-splice cleanup. Added regressions for a head boundary landing on a tool result and a tail preserving an orphaned result. Ported dataclass-shaped equivalents of `_align_boundary_forward`, `_align_boundary_backward`, and `_sanitize_tool_pairs` into `appV2.2/appv22/compaction/compressor.py`.

### Hermes Protected Tail Anchoring

Reference: `hermes-agent/agent/context_compressor.py` forces the most recent user message and the most recent user-visible assistant reply into the protected tail after token-budget boundary selection.

Appv22 previously could compress away the active user request when a large tool sequence followed it, or replace the last visible assistant reply with the compaction summary when only the newest user turn fit the tail budget. Added regressions for both cases and ported dataclass-shaped equivalents of `_find_last_user_message_idx`, `_find_last_assistant_message_idx`, `_ensure_last_user_message_in_tail`, and `_ensure_last_assistant_message_in_tail`.

### Hermes Historical Media Stripping

Reference: `hermes-agent/agent/context_compressor.py` strips image parts before the newest image-bearing user message so old screenshots and other large visual payloads are not resent forever after compaction.

Appv22 previously preserved old `ImageContent` payloads in protected head/tail messages. Added a regression for an old protected-head image before a newer image-bearing user turn and ported dataclass-shaped equivalents of `_content_has_images`, `_strip_images_from_content`, and `_strip_historical_media`.

### Hermes Summary Role, Merge, and End Marker

Reference: `hermes-agent/agent/context_compressor.py` avoids consecutive same-role summary insertion by choosing the summary role from neighboring head/tail roles, merges the summary into the first tail message when neither standalone role is safe, and appends `_SUMMARY_END_MARKER` so compacted historical asks are not treated as active input.

Appv22 previously always inserted the summary as a `UserMessage` with only `SUMMARY_PREFIX`. Added regressions for assistant-role standalone summaries, explicit end markers, and merge-into-tail behavior. Ported Python dataclass equivalents for role selection, assistant summary construction, and prepend-merge behavior.

### Hermes Persisted Summary Rehydration

Reference: `hermes-agent/agent/context_compressor.py` detects persisted context-summary messages inside the protected head/compression window, strips the current or legacy prefix and end marker, rehydrates `_previous_summary`, and excludes that message from the `NEW CONVERSATION` segment of the summarizer prompt.

Appv22 previously only used in-memory `_previous_summary`, so resumed sessions with an existing summary message serialized that summary as ordinary conversation. Added a regression for an existing assistant summary message and ported dataclass equivalents of `_strip_summary_prefix`, `_is_context_summary_content`, and `_find_latest_context_summary`.

### Hermes Protected System Head Sizing

Reference: `hermes-agent/agent/context_compressor.py` uses `_protect_head_size()` so a leading `role: system` message is always protected and `protect_first_n` counts additional non-system head messages.

Appv22 previously used `protect_first_n` as a raw list slice count, which meant a system-compatible compaction path would either compact the system prompt when `protect_first_n=0` or count the system message against the configured non-system head budget. Added direct and end-to-end regressions, ported `_protect_head_size()`, counted structural system messages in `_message_text()`, and wired `compress()` to use the Hermes head boundary before tool-pair alignment.

### Hermes Protected Tail Floor

Reference: `hermes-agent/agent/context_compressor.py` uses a bounded recent-message floor, 1.5x soft token ceiling, and raw-budget fallback in `_find_tail_cut_by_tokens()` so large recent tool output does not block compression and generous budgets still leave a meaningful middle window.

Appv22 previously used `protect_last_n` as an unbounded hard floor. With `protect_last_n=20` and a tiny budget, it could keep almost the whole transcript live. Added a regression for the Hermes tiny-budget bounded-floor case, ported `_MAX_TAIL_MESSAGE_FLOOR`, soft-ceiling walking, raw-budget fallback, and message overhead into `_find_tail_start()`, while keeping the existing tool-boundary and latest-user/latest-assistant tail anchoring.

### Hermes Summary Prompt Safety and Redaction

Reference: `hermes-agent/agent/context_compressor.py` builds summary prompts with a shared summarizer preamble, historical checkpoint headings, secret-redaction instructions, temporal anchoring, and explicit iterative-update labels. It also redacts serialized turns and the returned summary body.

Appv22 previously generated a short summary prompt with coarse section names and serialized raw content directly into the summarizer prompt. Added regressions for prompt safety and secret redaction, ported the historical headings, temporal anchor rule, Hermes-style `PREVIOUS SUMMARY` / `NEW TURNS TO INCORPORATE` labels, and a local credential redaction pass for summary input/output boundaries.

### Hermes Focused Manual Compression

Reference: `hermes-agent/agent/context_compressor.py` accepts `focus_topic` and appends `FOCUS TOPIC` guidance to the summarizer prompt; manual compression paths pass `/compress <focus>` through as that topic.

Appv22 previously accepted `focus` on `CompactionManager.compress_manual()` but ignored it. Added a regression proving the focus reaches the summarizer prompt, added `focus_topic` to `ContextCompressor.generate_summary()` and `compress()`, appended Hermes-style focus guidance, redacted the focus text before prompt insertion, and wired `CompactionManager.compress_manual(..., focus=...)` into the compressor.

### Hermes Summary Failure Bookkeeping

Reference: `hermes-agent/agent/context_compressor.py` records summary failure fields, inserts a deterministic fallback handoff by default, and supports `abort_on_summary_failure=True` to preserve messages unchanged while setting `_last_compress_aborted`.

Appv22 previously let summarizer exceptions escape to `CompactionManager`, which no-oped the compression and used only a manager-level cooldown. Added regressions for default deterministic fallback, failure-field reset after success, and abort-on-summary-failure mode. Ported `_last_summary_error`, `_last_summary_dropped_count`, `_last_summary_fallback_used`, `_last_compress_aborted`, and `abort_on_summary_failure` into `ContextCompressor`.

Focused verification:

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py::test_agent_rejects_prompt_while_streaming -q
```

Result: passed.

## High-Confidence Mismatches

### Agent Runtime

- Queues now use a `PendingMessageQueue` with drain mode internally and expose Pi-style public status/clear APIs.
- Remaining runtime gaps: full async listener semantics beyond the synchronous Python-compatible settlement now implemented.

### Agent Loop

- `_stream_assistant_response` now passes a stream options object with signal, reasoning, API key, provider hooks, transport, retry-delay, thinking-budget, and session-id fields.
- `Agent` now passes the active abort signal to wrapper-level `prepare_next_turn`, and tool update emissions now settle before `tool_execution_end`.
- Normal loop events now settle future-like or event-like sink returns before the loop advances, including `tool_execution_start`, `tool_execution_end`, and tool result message emission in sequential and parallel execution modes.
- Parallel and sequential tool termination now have direct coverage for the Pi rule that a batch terminates only when every finalized tool result has `terminate: true`.
- `prepare_next_turn` snapshots now update the loop-local config/context without mutating the caller's `AgentLoopConfig`, and `should_stop_after_turn` sees the post-snapshot context.
- Phase 2 agent-loop plan items are complete; future loop work should come from new regressions against `pi/packages/agent/src/agent-loop.ts` rather than open Phase 2 checkboxes.

### Coding Session

- `AgentSession` now uses a definition-first registry with default active `read`, `bash`, `edit`, and `write`, explicit active tool updates, allowed/excluded filtering, synthesized definitions for bare `AgentTool` overrides, and executable wrappers for caller-provided `ToolDefinition` objects.
- `AgentSession` now has session-level event subscription and queue update parity for visible steering/follow-up queues.
- `AgentSession.prompt()` now has Pi-style streaming preflight behavior: `streaming_behavior="steer"` queues steering, `streaming_behavior="followUp"` queues follow-up, and missing behavior reports preflight failure while streaming.
- `AgentSession` now emits Pi-shaped compaction, retry, thinking-level, and session-info events, augments `agent_end` with `willRetry`, exposes `set_session_name()`, `set_thinking_level()`, `set_model()`, `retry_attempt`, and a manual `compact()` wrapper over the Hermes `CompactionManager`.
- `AgentSession` now optionally persists Pi-shaped JSONL sessions with a header plus typed `message`, `thinking_level_change`, `model_change`, `session_info`, and `compaction` entries. Reload rebuilds the active branch, and `branch(entry_id)` repoints the leaf so later messages become children of the selected entry.
- `AgentSession.getAllTools()` / `get_all_tools()` now returns Pi-shaped `ToolInfo` dictionaries with `promptGuidelines` and `sourceInfo`, while preserving appv22 snake_case compatibility aliases. Builtin tools report synthetic `<builtin:name>` source metadata, SDK/custom tool definitions report `<sdk:name>`, and `SourceInfo`/`create_synthetic_source_info()` are exported from `appv22.coding_agent`.
- Appv22 now has a local `ExtensionRunner` / `RegisteredTool` subset for Pi extension-registered tools. `AgentSession.refreshTools()` / `refresh_tools()` merges extension tools into the definition-first registry, preserves extension `sourceInfo`, obeys allow/exclude filters, and can activate all extension tools after refresh.
- `ExtensionRunner` now supports Pi-style lifecycle handler registration, `hasHandlers()`, `emit()`, `onError()`, before-event cancellation merging for `session_before_switch`, `session_before_fork`, `session_before_compact`, and `session_before_tree`, plus `emit_session_shutdown_event()`. `AgentSession` exposes `extensionRunner` / `hasExtensionHandlers()` and emits configurable `session_start` metadata at construction.
- Appv22 now has a local `DefaultResourceLoader` subset with Pi file names and getter shapes: AGENTS/CLAUDE context discovery, `.pi/SYSTEM.md`, `.pi/APPEND_SYSTEM.md`, `reload()`, `extendResources()`, and AgentSession prompt rebuilding from loader state. `resources_discover` extension handlers can now feed extension resource paths into the loader cache.
- Appv22 now has a local `AgentSessionRuntime` subset with Pi-style before-switch cancellation, `session_shutdown` emission, `session_start` metadata for replacement sessions, rebind callbacks, before-invalidate callbacks, `newSession()` / `new_session()`, `switchSession()` / `switch_session()`, and `dispose()`.
- Runtime fork replacement is now ported: `AgentSessionRuntime.fork()` emits cancelable `session_before_fork`, supports `position: "before" | "at"`, returns selected user text for before-user forks, copies the root-to-leaf path into a new parent-linked JSONL session file, emits shutdown/start lifecycle events, and rebinds the active session.
- Runtime JSONL import is now ported: `AgentSessionRuntime.import_from_jsonl()` / `importFromJsonl()` raises `SessionImportFileNotFoundError` for missing files, emits cancelable resume `session_before_switch`, copies imported files into the active session directory, emits shutdown/start lifecycle events, and restores the imported session through the runtime factory.
- Tree navigation host flow is now ported in `AgentSession.navigate_tree()` / `navigateTree()`: it collects abandoned-branch entries, emits cancelable `session_before_tree`, supports extension-supplied summaries, writes `branch_summary` and `label` entries, converts branch summaries to provider-safe user messages, returns editor text for user/custom-message targets, rebuilds session state, and emits `session_tree`.
- Custom session entries are now ported: `SessionStore.appendCustomEntry()` / `append_custom_entry()` writes opaque extension state, `appendCustomMessageEntry()` / `append_custom_message_entry()` writes context-participating `custom_message` entries, reload reconstructs `role="custom"` messages, `AgentSession.sendCustomMessage()` / `send_custom_message()` supports direct append, trigger-turn, streaming steer/follow-up, and `deliverAs="nextTurn"`, and message-end persistence stores custom messages in Pi's JSONL shape.
- Default model-generated branch summaries are now ported through `appv22.coding_agent.branch_summarization`: `navigate_tree(..., {"summarize": True})` serializes abandoned branch messages with Pi's branch-summary prompt, calls the configured model without mutating agent state or Hermes compaction state, prepends Pi's branch-summary preamble, stores read/modified file details, and writes the resulting `branch_summary` entry.
- Package-backed resource loading is now ported at the runtime-loader level: `DefaultPackageManager` resolves local package roots with Pi `package.json.pi` manifests plus conventional `skills/`, `prompts/`, and `themes/` folders; `DefaultResourceLoader` loads skills, prompt templates, and themes into Pi-shaped result objects; `.pi` and `.agents` skill discovery are included; extension resource paths update loaded resources; and loaded skills are appended to `AgentSession` system prompts when `read` is active.
- Compaction entries now reload into a Pi-shaped `compactionSummary` message before kept messages, matching the session-manager compaction context rebuild order.
- Bash execution messages are now ported as `role="bashExecution"` session messages with Pi field aliases, LLM conversion text, `excludeFromContext` skipping, and JSONL round-trip support.
- `AgentSession.executeBash()` / `execute_bash()` and `recordBashResult()` / `record_bash_result()` now execute user bash through local `BashOperations`, stream chunks, record `BashExecutionMessage`, persist to JSONL sessions, and honor `excludeFromContext`.
- `AgentSession.recordBashResult()` now matches Pi's streaming-order protection: bash executions recorded while the agent is streaming are queued in `_pending_bash_messages`, exposed through `hasPendingBashMessages` / `has_pending_bash_messages`, omitted from state/session persistence during the active run, and flushed at agent prompt boundaries. `BashResult` is also exported from `appv22.coding_agent`, matching Pi's public core surface.
- `AgentSession.abortBash()` / `abort_bash()` and `isBashRunning` / `is_bash_running` are now ported with a per-user-bash `AbortSignal`, matching Pi's session-level cancellation API without reusing the model-run abort signal.
- `ExtensionRunner.emitUserBash()` / `emit_user_bash()` and `InteractiveMode` user-bash interception are now ported: `!` / `!!` commands emit `user_bash` before local execution, extension-provided full `BashResult` objects are rendered and recorded directly, and extension-provided `BashOperations` are passed through normal `execute_bash()`.
- `ExtensionRunner.emitInput()` / `emit_input()` and `AgentSession.prompt()` input interception are now ported: `input` handlers see Pi-shaped events, chained transforms update text/images before prompt dispatch, `handled` short-circuits without calling the model, and `streamingBehavior` is reported only while the agent is actively streaming.
- `ExtensionRunner.emitMessageEnd()` / `emit_message_end()` and `AgentSession` finalized-message replacement are now ported: `message_end` handlers can replace finalized messages in-place before persistence/public listeners, and role-changing replacements are rejected through the extension error channel.
- `ExtensionRunner.emitToolCall()` / `emit_tool_call()` and `AgentSession` pre-tool interception are now ported through the agent-loop `before_tool_call` hook: handlers can block execution with a reason, the real tool is not called, and the reason is delivered as an error tool result to the next provider turn.
- `ExtensionRunner.emitToolResult()` / `emit_tool_result()` and `AgentSession` tool-result mutation are now ported through the agent-loop `after_tool_call` hook: handlers can chain partial changes to content, details, and `isError` before the tool result message enters context or persistence.
- `ExtensionRunner.emitBeforeAgentStart()` / `emit_before_agent_start()` and `AgentSession` turn-start injection are now ported: handlers can inject custom messages into the next model context and can chain system prompt modifications for the current turn while later turns reset to the base system prompt unless modified again.
- `ExtensionRunner.emitContext()` / `emit_context()` and `AgentSession` provider-context transformation are now ported: context handlers receive a cloned message list, can chain returned `messages`, run after any caller-provided `transform_context`, and affect only the provider context without rewriting saved session history.
- `ExtensionRunner.emitBeforeProviderRequest()` / `emit_before_provider_request()` and provider response observation are now ported through Agent stream options: `before_provider_request` handlers can chain payload replacements through `on_payload`, and `after_provider_response` handlers receive Pi-shaped `status` and `headers` through `on_response`.
- `ExtensionRunner.registerCommand()` / `register_command()` and idle extension slash-command dispatch are now ported: registered commands run before input hooks and provider turns, receive a Pi-shaped command context with system prompt helpers, leave session messages untouched, and do not consume provider responses.
- `ExtensionCommandContext` now exposes Pi-style action methods for command handlers: `appendEntry()` / `append_entry()` persists extension state through custom session entries, and `sendMessage()` / `send_message()` injects custom messages through the existing session custom-message path.
- `ExtensionCommandContext.sendUserMessage()` / `send_user_message()` is now ported for command handlers: default delivery triggers a normal user turn, while `deliverAs="steer"` and `deliverAs="followUp"` route through the existing Pi-style queue APIs.
- `ExtensionCommandContext` now exposes Pi-style session/tool metadata methods for command handlers: session name get/set, active tool get/set, all-tool metadata, and registered slash command listings.
- `ExtensionCommandContext` now exposes Pi-style thinking-level methods for command handlers through `getThinkingLevel()` / `get_thinking_level()` and `setThinkingLevel()` / `set_thinking_level()`.
- `ExtensionCommandContext.setModel()` / `set_model()` is now ported for command handlers, returning a boolean and routing successful model changes through `AgentSession.set_model()` so session model state and persistence hooks remain centralized. Full Pi provider-auth/model-registry validation remains a separate provider-registry parity slice.
- `ExtensionRunner.registerProvider()` / `register_provider()` is now ported for the command-time provider override path. Registrations queue until `AgentSession` binds provider actions, then apply immediately; matching active-session provider overrides update the active `Model` without reload, and Pi-style model config dictionaries can replace appv22 provider model entries plus optional custom stream handlers. `ExtensionRunner.unregisterProvider()` / `unregister_provider()` now restores the active model from a session-local pre-extension snapshot, removes extension-created provider models, and restores pre-existing provider model registry entries through `set_provider_models()`. OAuth, auth storage, and `hasConfiguredAuth()` behavior remain separate parity slices.
- `ExtensionCommandContext.setLabel()` / `set_label()` is now ported for command handlers, writing Pi-shaped JSONL label entries through the session store.
- `ExtensionCommandContext.exec()` is now ported for command handlers as an argv-style utility returning stdout/stderr/code/killed without recording a `bashExecution` transcript message.
- `ExtensionCommandContext.waitForIdle()` / `wait_for_idle()` and `compact()` are now ported for command handlers. `waitForIdle()` settles through the core agent idle API, while `compact({ customInstructions, onComplete, onError })` reuses the session Hermes compaction path and adapts the callback payload to Pi's `CompactionResult` shape (`summary`, `firstKeptEntryId`, `tokensBefore`, `details`).
- `AgentSession.steer()` and `AgentSession.follow_up()` now reject registered extension slash commands with Pi's queueing error instead of treating them as ordinary queued user text.
- `ExtensionRunner.registerFlag()` / `register_flag()` and flag value APIs are now ported: flags keep the first duplicate registration, install default values, expose current values, and support explicit runtime overrides.
- `ExtensionRunner.registerMessageRenderer()` / `register_message_renderer()` and lookup APIs are now ported, giving appv22 a Pi-shaped registry for custom-message renderer handoff.
- `ExtensionRunner.registerShortcut()` / `register_shortcut()` and shortcut lookup are now ported at the registry layer: shortcut keys normalize to lowercase and later duplicate extension registrations override earlier ones for TUI handoff.
- `InteractiveMode` now passes the extension message-renderer registry into existing custom-message history rendering, so registered renderers are actually used by the TUI instead of falling back to generic `[custom_type]` rendering.
- `InteractiveMode` now dispatches registered extension shortcuts before normal prompt handling. In the Python line-oriented TUI this uses exact submitted shortcut keys, runs the handler with a Pi-shaped TUI extension context, avoids model dispatch, and avoids rendering the shortcut key as a user prompt.
- The previously tracked registry/runtime gaps are closed. A final full audit pass is still required before claiming the overall goal complete, because the plan has accumulated many ported slices and may still have residual Pi/Hermes mismatches outside the tracked gap list.

### Tools

- `write` and `edit` now use a per-file mutation queue and check abort across their mutation steps. `write` also has Pi-style `WriteOperations`, no result details, Pi success text, and coverage that an aborted in-flight write keeps the queue locked until the operation settles.
- `edit` now exposes Pi's `edits[]` public schema, supports legacy `oldText/newText` preparation, applies multiple replacements against original content, rejects overlaps/duplicates/no-op edits, preserves BOM and line endings, and returns diff/patch details. Remaining edit gap: richer TUI preview rendering parity.
- `bash` no longer appends an appv22-only success exit-code footer and now treats nonzero exits as tool errors. It keeps tail output, writes full truncated output to a temp file, exposes Pi-style `BashOperations`/`create_local_bash_operations()`, supports command prefixes and spawn hooks, emits streaming partial updates, preserves output paths on abort/timeout errors, and kills the local process group on abort/timeout.
- `read` now detects PNG/JPEG/GIF/WEBP files, returns text plus `ImageContent`, uses Pi-style normalized read-path fallback for `@` paths, Unicode spaces, macOS screenshot AM/PM spacing, NFD filenames, and curly quotes, exposes `ReadOperations`, checks abort across filesystem-equivalent steps, supports auto-resize control flow with injected resize results/omission, emits non-vision image notices, hides non-error collapsed results, and renders compact docs/resource/skill labels with line ranges.
- `find` now uses Pi's `limit` schema/result notices, returns paths relative to the search root, supports path-containing globs such as `src/**/*.spec.ts`, applies scoped `.gitignore` rules hierarchically, and exposes `FindOperations`.
- `grep` now supports Pi's `glob`, `literal`, `ignoreCase`, `context`, and `limit` arguments, applies scoped `.gitignore` rules hierarchically, returns `No matches found` with no details for empty results, emits match-limit/truncation/long-line details, and exposes `GrepOperations`.
- `ls` now supports Pi's `limit` argument, case-insensitive sorting, entry-limit notices/details, empty-directory no-details behavior, and exposes `LsOperations`.
- `truncate` now includes Pi's `GREP_MAX_LINE_LENGTH`, `truncate_line()`, and one-decimal `format_size()` behavior.

### Hermes Compaction

- Appv22 now has Hermes-style tool-call/result boundary alignment and pair sanitization after compression assembly.
- Appv22 now anchors the latest user message and latest visible assistant reply into the protected tail after token-budget boundary selection.
- Appv22 now strips historical image content before the newest image-bearing user message.
- Appv22 now avoids consecutive same-role summary insertion, merges summaries into the first tail message when both roles collide, and appends an explicit summary end marker.
- Appv22 now rehydrates iterative summaries from existing summary-prefixed messages instead of serializing them as new conversation.
- Appv22 now preserves a leading structural system message separately from the configured non-system protected head count.
- Appv22 now caps the protected tail message floor and uses Hermes-style soft-ceiling/raw-budget tail selection.
- Appv22 summary prompts now include Hermes-style redaction instructions, temporal anchoring, historical headings, and input/output secret redaction.
- Appv22 now carries manual compression focus topics into Hermes-style summarizer focus guidance.
- Appv22 now records compressor-level summary failure fields, inserts deterministic fallback summaries by default, and supports abort-on-summary-failure mode.
- Appv22 now has a Python equivalent for Hermes summary-model fallback: `summary_summarizer` represents the auxiliary compression model, `summarizer` remains the main-model fallback, and aux failures populate `_last_aux_model_failure_{model,error}` while clearing `summary_model`.
- Appv22 now has compressor-level real-usage tracking and rough-estimate deferral parity: `update_from_response()`, `should_defer_preflight_to_real_usage()`, post-compression rough-token state, and manager preflight deferral after a real provider prompt fits.
- Appv22 now owns summary-failure cooldown inside `ContextCompressor`: failed summarizers set `_summary_failure_cooldown_until`, normal retries skip the summarizer during cooldown and use deterministic fallback, later success clears the cooldown, and manager manual/overflow force passes `force=True` so manual compression can clear the compressor cooldown before retrying.
- Appv22 now has a SQLite-backed `SessionLineageStore` with Hermes-style `sessions(id, parent_session_id, ended_at, end_reason)` rows. `SessionLineage.rotate()` writes compression end reasons and child parent links, and `SessionLineage.load()` restores the parent chain after a restart.
- Appv22 now has manual compression feedback/status parity: `compress_manual_with_status()` returns Hermes-style headline/token/note fields plus warning/info fields for summary aborts, deterministic fallback handoff, and auxiliary compression model recovery. The existing `compress_manual()` list-returning API remains as a compatibility wrapper.
- Phase 5 Hermes compaction/timing plan items are complete; future compaction work should come from new regressions against the references rather than open Phase 5 checkboxes.

### TUI and Rendering

- `InteractiveMode` now uses appv22's ported component stack instead of raw prompt text: startup text, history, `Input`, `StatusLine`, and `FooterComponent` render through the differential TUI while preserving the existing testable synchronous input seam.
- Appv22 now includes a small terminal `Markdown` renderer, single-line editor input with cursor/key handling, keyboard-navigable `SelectList`, footer/status surfaces, assistant message rendering for markdown/thinking/error states, and tool execution rendering with definition-level render hooks.
- Tool execution components now support compact/expanded result rendering; live `CodingApp` wiring passes active `ToolDefinition` objects into the renderer so read calls use compact docs/resource/skill labels and collapsed result hiding outside isolated tests.
- Differential redraw behavior is covered for full vs changed-line rendering and narrow terminal width constraints.
- The TUI renderer now clips rendered frames to the terminal row viewport before diffing, preventing cursor moves past the bottom of the terminal from turning footer/status redraws into repeated scrollback lines.
- `InteractiveMode` now treats `/compact` as a local alias for `/compress`, including optional focus text, so manual Hermes compaction does not fall through into the model/tool loop.
- The TUI now has Pi-style collapsed/expanded `BranchSummaryMessageComponent`, `CompactionSummaryMessageComponent`, and `CustomMessageComponent` renderers, and `InteractiveMode` renders existing special messages when opening a session history.
- User turns now render through `UserMessageComponent` with OSC 133 prompt zones instead of raw `> text` lines, and Pi-style `<skill name="..." location="...">` blocks render through collapsed/expanded `SkillInvocationMessageComponent` with trailing user text split into its own user message.
- Bash execution history now renders through `BashExecutionComponent`, including command header, preview/expanded output, exit/cancel/truncation status, full-output path, and `[no context]` labeling for `!!`-style excluded commands.
- `InteractiveMode` now intercepts `! command` and `!! command` before model dispatch, executes them locally, renders live bash output, and keeps `!!` executions out of LLM context.
- Bash executions triggered during an active model run are now kept as pending TUI/session history until the run settles, preserving Pi's tool-use/tool-result ordering invariant instead of injecting `bashExecution` into the transcript mid-stream.
- Interactive bash now emits Pi-style `user_bash` extension events before execution, supports extension full-result replacement, and supports extension-provided bash operations for custom local/remote shells.
- TUI extension shortcut contexts now expose Pi-style `ui.setStatus()` / `set_status()` for footer/status-bar text. `InteractiveMode` stores extension status values by key, refreshes the footer immediately, and `FooterComponent` renders sorted extension statuses alongside model/thinking/context fields.
- TUI extension shortcut contexts now expose Pi-style `ui.setWorkingMessage()` / `set_working_message()` for the interactive working/status row. The Python TUI stores a default working message (`Idle`), updates the visible `StatusLine` immediately, and resets to the default when called without an argument.
- TUI extension shortcut contexts now expose Pi-style `ui.setWorkingVisible()` / `set_working_visible()` for hiding or restoring the built-in working/status row. `StatusLine` owns the visibility flag, so hidden rows do not reserve layout space while the footer remains visible.
- TUI extension shortcut contexts now expose Pi-style `ui.setWorkingIndicator()` / `set_working_indicator()` for configuring the built-in working/status indicator. The Python port renders the first configured frame as a static indicator prefix on the status row and supports empty frames for hiding the indicator.
- TUI extension shortcut contexts now expose Pi-style `ui.input(title, placeholder?, opts?)` for extension text prompts. The Python line-oriented port renders an input prompt into history, uses the configured `input_fn`, returns the submitted value, and honors already-aborted dialog signals by returning `None`.
- TUI extension shortcut contexts now expose Pi-style `ui.select(title, options, opts?)` for extension option prompts. The Python line-oriented port renders numbered choices, accepts 1-based numeric or exact-label input, returns the selected option string, and honors already-aborted dialog signals by returning `None`.
- TUI extension shortcut contexts now expose Pi-style `ui.confirm(title, message, opts?)` for extension confirmation prompts. The Python line-oriented port renders a confirm dialog as a Yes/No selector and returns `True` only when the user selects `Yes`.
- TUI extension shortcut contexts now expose Pi-style `ui.onTerminalInput(handler)` / `on_terminal_input(handler)`. The Python line-oriented port calls handlers with submitted input before normal prompt routing, supports returned `{"data": ...}` rewrites and `{"consume": True}` consumption, and returns an unsubscribe callback.
- TUI extension shortcut contexts now expose Pi-style `ui.setHiddenThinkingLabel(label?)` / `set_hidden_thinking_label(label)`. Assistant message components now support hidden-thinking rendering with a configurable label, and `InteractiveMode` applies label changes to existing history and future streamed assistant components.
- TUI extension shortcut contexts now expose Pi-style `ui.setTitle(title)` / `set_title(title)`. The Python terminal abstraction writes Pi's OSC `0;title` sequence through `Terminal.set_title()`, and the interactive shortcut UI forwards title changes directly to the terminal without adding chat history.
- TUI extension shortcut contexts now expose Pi-style `ui.setWidget(key, content, options?)` / `set_widget(...)`. The Python TUI now keeps keyed above/below editor widget maps, renders string-array widgets through dedicated containers around the line-oriented editor prompt, supports `placement: "belowEditor"`, replaces existing widgets across placements, clears widgets with `None`, and caps widget text at ten lines with a truncation notice.
- TUI extension shortcut contexts now expose Pi-style `ui.setFooter(factory?)` / `set_footer(factory=None)`. `InteractiveMode` now wraps the built-in footer in a replaceable footer container, disposes prior custom footer components, passes a Python `ReadonlyFooterDataProvider` equivalent with `getExtensionStatuses()`, `getGitBranch()`, `getAvailableProviderCount()`, and `onBranchChange()`, replaces the built-in footer with custom components, and restores the built-in footer when cleared.
- TUI extension shortcut contexts now expose Pi-style `ui.setHeader(factory?)` / `set_header(factory=None)`. `InteractiveMode` now wraps the startup text in a replaceable header container, disposes prior custom header components, creates custom headers with `(tui, theme)`, replaces the built-in startup header, and restores the built-in header when cleared.
- TUI extension shortcut contexts now expose Pi-style `ui.setEditorText(text)` / `set_editor_text(text)`, `ui.getEditorText()` / `get_editor_text()`, and `ui.pasteToEditor(text)` / `paste_to_editor(text)`. The Python line-oriented TUI now keeps a persistent editor buffer for the next prompt, preloads it into the active `Input`, submits prefilled text on Enter, clears the buffer after non-shortcut submission, and handles bracketed-paste sequences in `Input.handle_input()`.
- TUI extension shortcut contexts now expose Pi-style `ui.editor(title, prefill?)` / `editor(title, prefill=None)`. The Python line-oriented port renders an `editor:` status row, shows optional prefill text, collects submitted multi-line text through the configured `input_fn`, returns `None` on EOF/cancel-equivalent input loss, and records submitted text in history.
- TUI extension shortcut contexts now expose Pi-style `ui.addAutocompleteProvider(factory)` / `add_autocomplete_provider(factory)`. `InteractiveMode` now builds a base slash-command autocomplete provider, preserves extension command argument completion callbacks from `registerCommand()`, applies provider wrappers in order, aggregates `triggerCharacters`, pushes the active provider into the current `Input`, and `Input` applies the first completion on Tab through provider `getSuggestions()` / `applyCompletion()`.
- TUI extension shortcut contexts now expose Pi-style `ui.custom(factory, options?)`. The Python line-oriented port calls factories with `(tui, theme, keybindings, done)`, temporarily replaces the editor container with the returned component, routes terminal input into `handle_input()` until `done(value)` closes it, restores the previous editor surface, returns the close value to the shortcut handler, and disposes the custom component while ignoring dispose errors.
- Extension provider registration now validates Pi-style provider config invariants before applying dynamic provider changes: `streamSimple` requires `api`; provider `models[]` require `baseUrl`; model-bearing providers require either `apiKey` or `oauth`; and every dynamic model requires an `api` at provider or model level. Successful dynamic provider model tests now include `apiKey`, while `oauth` remains accepted as an auth source.
- The appv22 model registry now has a Pi-style in-memory auth/status layer for dynamic providers: provider request configs track `apiKey`, headers, and auth-header intent; `has_configured_auth(model)` reflects stored/runtime/env/provider-config auth; `get_provider_auth_status(provider)` distinguishes stored, runtime, provider-config environment, command, and literal key sources without exposing secrets; `get_api_key_for_provider(provider)` resolves stored API keys, OAuth credentials, known env keys, and provider-config env/literal values; dynamic OAuth provider metadata is registered from `oauth` configs and removed on `unregisterProvider()`.
- Dynamic OAuth provider lifecycle is now ported further: `login_oauth_provider(provider, callbacks)` invokes the registered provider `login` callback and stores returned OAuth credentials, `logout_provider(provider)` removes stored credentials while leaving provider metadata intact, expired OAuth credentials refresh through `refreshToken` / `refresh_token` during `get_api_key_for_provider()`, refreshed credentials replace the stored credential, and refresh failures are recorded through `drain_auth_errors()` while provider key resolution returns `None`.
- Provider request auth/header resolution is now ported further: `get_api_key_and_headers(model)` mirrors Pi's `{ ok, apiKey, headers }` request-time result, supports provider headers, separate model request headers, `authHeader` bearer injection, and Pi-style config value resolution for env templates/escapes/shell commands. `stream()` and `stream_simple()` now resolve this auth path before provider calls and raise on auth-resolution errors instead of silently omitting required auth. Extension provider model headers are stored in the request-header map rather than collapsed into `Model.headers`.
- Provider display-name resolution is now ported further: `get_provider_display_name(provider)` mirrors Pi's registered provider name, registered OAuth name, OAuth metadata name, built-in provider name, built-in OAuth-provider name, then raw-id fallback order. TUI auth provider selection and login/logout status text now use the registry resolver instead of a local raw-id fallback.
- Default model data is now ported further: `DEFAULT_MODEL_PER_PROVIDER` and `get_default_model_for_provider(provider)` mirror Pi's current `defaultModelPerProvider` map, and appv22's env-backed startup fallback now uses the Pi OpenRouter default `moonshotai/kimi-k2.6` instead of the previous appv22-only Xiaomi fallback. The broader Pi `findInitialModel()` priority algorithm remains a separate audit item.
- Model resolver core behavior is now ported further: `appv22.ai.model_resolver` provides Pi-style exact reference matching, colon-safe pattern parsing, thinking suffix handling, CLI provider inference/custom model fallback, and initial model priority across CLI, scoped, saved-default, and available-model defaults. Further audit is still needed for integrating this resolver into every appv22 CLI/model-scope workflow.
- `InteractiveMode` now treats `/login` and `/logout` as local Pi-style TUI auth commands instead of user prompts. In the Python line-oriented port, `/login` first selects `Use a subscription` vs `Use an API key`; subscription login selects from registered OAuth providers, invokes provider login with callbacks for auth URLs, device codes, prompts, progress, manual-code input, select prompts, and signal, records local status, and never dispatches to the model; API-key login selects from registered model providers, prompts for the key, stores `{type: "api_key", key}`, and updates provider auth status. `/logout` selects from stored OAuth/API-key credentials, removes only the stored credential, and leaves environment/model-config auth untouched.
- Phase 6 TUI/rendering plan items are complete; future TUI work should come from new regressions against the references rather than open Phase 6 checkboxes.

## Next Regression Candidate

The tracked checklist items are largely closed, but the overall goal should not be marked complete until a fresh final full audit re-scans appv22 against the Pi/Hermes references. Any further work should begin with a new targeted scan or regression against a newly identified Pi/Hermes behavior gap, especially richer extension/provider hooks, OAuth callback-server/manual redirect UX details, built-in provider display/default-model details, or runtime-host session switching details.
