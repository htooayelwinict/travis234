# Travis234 Production-Safe Extension Runtime Parity Design

**Status:** Approved; microscopic implementation authorized. No Git operations are allowed until all required verification passes.

**Goal:** Make Travis234's existing Python extension APIs behave reliably across TUI, print, JSON, RPC, reload, and session replacement while preserving the agent loop, session state, context envelope, compaction, provider behavior, and bounded tool execution.

## Source authority

1. The Travis234 repository root at commit `68b1831` is authoritative for product behavior and preserved safety invariants.
2. The local Pi checkout at commit `3da591ab` is the behavioral oracle for extension runtime semantics.
3. Pi remains a design and test oracle only; Travis234 does not import Pi at runtime.
4. Python remains Travis234's native extension language. Direct JavaScript or TypeScript extension execution is out of scope.

## Verified current state

The pre-change baseline is clean:

```text
uv run pytest -q
1759 passed in 115.53s
```

The existing focused extension, resource, flag, and TUI tests also pass:

```text
113 passed in 8.77s
```

Those green tests prove registration and isolated helper behavior, but several do not prove end-to-end runtime reachability. In particular, the parity manifest maps every Pi extension event to one declaration-only test.

## Verified drift

### Host binding and lifecycle

- Pi binds extensions in TUI, print, JSON, and RPC modes. Travis234 performs full host binding only during initial TUI startup.
- Print, JSON, and RPC sessions receive core actions during `AgentSession` construction, but they do not receive their actual host mode, safe UI, command actions, error sink, `session_start`, or `resources_discover` lifecycle through `bind_extensions()`.
- Replacement sessions are created with deferred startup. Travis234 installs the replacement and invokes application rebound listeners before calling `emit_deferred_session_start()`, but the TUI listener currently posts only a later redraw. It does not synchronously bind the replacement extension runtime.
- Consequently `/new`, resume, fork, clone, import, and extension-triggered replacements can emit startup with default print/no-UI semantics.

### Tools

- Travis234 already contains `wrap_registered_tool()` and `wrap_registered_tools()`, ported from Pi.
- The production tool registry does not use them. It wraps extension tools with a basic `ToolContext(cwd, model)`.
- Extension tools therefore do not receive the canonical extension context, and additive active-tool metadata is not produced by the live path.
- `register_tool()` and `unregister_tool()` mutate the runner registry without requesting a live tool refresh.

### Commands and shortcuts

- Pi awaits async extension command handlers and executes registered commands as host commands, including while streaming.
- Travis234 discards command awaitables. It also catches `TypeError` around the whole handler invocation and may invoke a handler twice when the handler itself raises `TypeError`.
- Only two built-in extension commands are dispatched directly by the TUI. Other registered commands are routed through the normal prompt/turn path when idle and rejected through steering while streaming.
- Pi matches extension shortcuts against raw editor input. Travis234 compares submitted prompt text such as `ctrl+w` after Enter.
- Shortcut conflicts with protected built-in keys are not resolved or diagnosed.

### Context, source attribution, and staleness

- Event and command context views carry generation guards, but extension factories receive the shared runner itself. A captured top-level API can outlive reload or session replacement.
- The flattened runner relies on a mutable `_loading_extension_path`; handler errors commonly report `<python-extension>` rather than the real source.
- Pre-bind action methods generally degrade to no-ops, hiding invalid extension lifecycle usage.
- A complete Pi-style per-extension container refactor would fix this structurally, but it exceeds the approved surgical scope.

### Event semantics

- `thinking_level_select` uses Travis-only field names rather than Pi's `level` and `previousLevel`.
- `model_select.source` is always `api` instead of `set`, `cycle`, or `restore`.
- `input` returns `transform` even when no handler transformed anything and hardcodes `interactive` at the prompt boundary.
- Chained `before_agent_start` handlers receive the latest system prompt in the event, but `ctx.get_system_prompt()` still reads the original session prompt.
- `send_user_message()` flattens image content, uses interactive input semantics, and allows slash-command/template expansion.

### Current Pi direction

Pi now dogfoods its extension runtime. Its bundled hidden llama.cpp extension registers a native provider, refreshes a dynamic model catalog, exposes an async command, and uses extension UI and model-registry context. This demonstrates why runtime reachability and context fidelity matter more than merely declaring API names.

Native provider objects, dynamic provider catalog refresh, hidden built-in extensions, entry renderers, advanced editor components, and interactive RPC UI are intentionally excluded from this design.

## Selected architecture

Introduce one narrow `ExtensionHostAdapter` at the existing application/session boundary.

The adapter owns only:

- host mode: `tui`, `print`, `json`, or `rpc`;
- the host's extension binding factory;
- initial binding;
- synchronous replacement binding through `CodingApp.subscribe_session_rebound()`;
- its own rebound subscription;
- safe callback settlement helpers used by host dispatch paths.

The adapter does not own:

- an agent or agent loop;
- session messages, persistence, branching, or JSONL state;
- context construction or context accounting;
- compaction policy or compaction transactions;
- provider requests, generation parameters, or credentials;
- tool scheduling or parallelism;
- resource trust policy or discovery rules.

### Public seams used

- `AgentSession.bind_extensions(bindings)`
- `CodingApp.subscribe_session_rebound(listener)`
- `ExtensionRunner.create_context()`
- `ExtensionRunner.create_command_context()`
- existing command-context actions
- existing TUI extension UI
- existing extension tool wrapper

No alternate session or agent lifecycle is introduced.

## Host lifecycle design

### TUI startup

1. Construct the TUI and extension UI object.
2. Start the terminal UI.
3. Start the host adapter as `mode="tui"`, `hasUI=true`.
4. Bind the active session.
5. Let the first bind emit `session_start` and resource discovery.

### TUI replacement

1. `AgentSessionRuntime` creates a replacement with deferred startup.
2. The old session emits `session_shutdown` and is disposed.
3. The replacement is installed in `CodingApp`.
4. `CodingApp` synchronously notifies rebound listeners.
5. The adapter binds the replacement with TUI mode and actions.
6. First binding consumes deferred startup and emits exactly one `session_start`.
7. The TUI posts its redraw and subscription refresh after binding.
8. The runtime's final `emit_deferred_session_start()` becomes a no-op because startup was consumed.

`AgentSessionRuntime` ordering is not modified.

### Print and JSON

`run_print_mode()` and `run_json_mode()` start a scoped adapter before the first prompt and dispose only the rebound subscription afterward. Bindings include:

- `mode="print"` or `mode="json"`;
- a safe no-op UI;
- `hasUI=false`;
- session-control command actions;
- a human-safe error sink that never writes plain text to JSON stdout.

### RPC

`RpcServer.run()` starts a scoped adapter before processing frames:

- `mode="rpc"`;
- safe no-op UI;
- `hasUI=false`;
- command actions and rebound binding;
- diagnostics outside the RPC stdout stream.

Travis234 does not claim Pi's interactive RPC UI parity in this change.

### Lifecycle guarantees

- Exactly one `session_start` per created session.
- Exactly one `session_shutdown` per replaced or closed session.
- Reload reuses current bindings and emits the existing reload lifecycle once.
- `resources_discover` runs after correct mode and UI state are installed.
- Adapter disposal removes only adapter-owned subscriptions.

## Safe non-interactive UI

Add a no-op UI object implementing Travis234's currently supported extension UI methods. Selection and input return no value, confirmation returns false, editor text is empty, theme changes fail with `UI not available`, and mutation methods do nothing.

The runner stores UI availability separately from UI object presence. Supplying the no-op object must not make `ctx.has_ui` true.

## Extension tool execution

Only extension-registered tools change wrappers:

- Use `wrap_registered_tool(registered, runner)` in the live registry.
- Keep all built-in tools on their existing `ToolContext` wrapper.
- Preserve allowlists, exclusions, source metadata, active-tool selection, scheduling, and bounded parallel execution.
- Request a registry refresh after post-bind `register_tool()` or `unregister_tool()`.
- Initial factory-time registration remains safe because the refresh action is a no-op until core binding.
- Preserve the existing `added_tool_names` behavior so newly activated tools can appear on the next model call.

## Extension command execution

All registered extension commands are host commands:

- They never enter the model prompt solely because their slash command was submitted.
- They never enter steering or follow-up queues solely because a turn is active.
- Sync and async handlers are executed exactly once.
- Signature inspection supports legacy one-argument handlers without catching a handler's internal `TypeError` as an arity mismatch.
- Commands execute on a dedicated serialized host command lane, not the TUI render thread.
- The command lane serializes extension commands with each other. It does not add a new session or agent lock.
- Session-mutating actions continue through existing public runtime actions and existing busy-state guards; the adapter does not bypass them.
- Failures are attributed to the command's extension source.

The command context is a compatibility proxy. It delegates Pi-style runtime properties to the canonical command context while preserving Travis234's existing convenience actions such as `send_message`, `send_user_message`, `exec`, model controls, and subagent controls.

## Extension shortcuts

- Add one optional raw-input extension callback to the main multiline editor.
- Resolve built-in and user-configured keybindings before extension shortcuts.
- Skip protected conflicts and produce a source-aware diagnostic.
- Match remaining shortcuts with the existing key parser.
- Settle sync or async handlers exactly once and isolate errors.
- Remove submitted prompt-text shortcut emulation.
- Keep line-input fallback behavior explicit: raw shortcuts are unavailable because raw terminal key sequences are unavailable.

## Source-scoped API proxy

Each extension factory receives a thin proxy around the shared runner. The proxy captures:

- runner generation;
- extension source path;
- shared runner reference.

Each delegated API call asserts the generation is active, applies the captured source scope for registration and diagnostics, and delegates to the shared runner. This preserves the flattened runner and avoids Pi's broader per-extension container refactor.

Registration and queued provider registration remain valid during loading. Actions that require a bound session fail clearly instead of silently doing nothing.

## Approved event corrections

Only verified mismatches are changed:

1. `thinking_level_select`
   - add `level` and `previousLevel`;
   - retain `thinkingLevel` and `previousThinkingLevel` aliases.
2. `model_select`
   - emit `source` as `set`, `cycle`, or `restore`.
3. `input`
   - return `continue` when unchanged;
   - retain `transform` only after a transformation;
   - propagate `interactive`, `rpc`, or `extension` source.
4. `before_agent_start`
   - chained `ctx.get_system_prompt()` reads the latest handler-produced prompt.

No other event payload changes without a focused failing regression.

## Extension-sent user messages

`send_user_message()` must:

- preserve text and image blocks;
- use `source="extension"`;
- disable prompt-template and slash-command expansion;
- preserve `steer` and `followUp` delivery choices.

This adds only optional input metadata at the session-turn boundary. It does not alter persistence or agent-loop semantics.

## Error isolation and diagnostics

- Handler registrations retain the registering extension path.
- Commands, shortcuts, tools, resource discovery, and queued provider errors report their real source.
- Async event-bus tasks receive a failure callback so exceptions are observed.
- Failures remain isolated on Pi's isolated event surfaces. Pi deliberately propagates a `tool_call` handler exception to the tool runtime, so that failure blocks execution rather than continuing to later tool-call handlers.
- TUI errors render in the existing history/status surface.
- Print diagnostics use stderr.
- JSON and RPC stdout framing remains unchanged; diagnostics use stderr.

Registration precedence is unchanged. This work does not redesign duplicate tools, message renderers, commands, shortcuts, or flags beyond protected shortcut conflict handling.

## Context-envelope guarantee

With no extensions installed, before and after implementation must be exactly equal for:

- system prompt;
- live message list;
- active tool names;
- tool definitions and schemas;
- provider request messages and tool schemas;
- context component counts.

The adapter adds no messages, system-prompt text, summaries, compaction records, or hidden tools. An extension may still change context only through an explicit extension action.

## Compatibility risks and mitigations

- Newly reachable lifecycle handlers may reveal extensions that assumed TUI-only execution. Correct mode and no-op UI prevent accidental crashes from missing UI objects.
- Immediate commands can overlap an active model turn. A serialized host command lane prevents command-command races and keeps callbacks off the render thread; existing session guards remain authoritative.
- Richer extension tool context can break extensions using `isinstance(ToolContext)`. The documented API is attribute-based, and `cwd` plus `model` remain present.
- Dynamic tool registration may grow the next request only when the extension explicitly registers or activates tools.
- Literal submitted text such as `ctrl+w` no longer triggers a shortcut; real raw keys do.
- Extension-sent slash text no longer expands accidentally.
- Stale captured APIs raise instead of mutating an obsolete runtime.
- More accurate isolation may surface previously swallowed errors.

## Explicit non-goals

- JavaScript or TypeScript extension execution.
- Pi's native provider object overload.
- Dynamic provider `refreshModels` parity.
- Bundled hidden provider extensions.
- Full per-extension runtime containers.
- `registerEntryRenderer`.
- Advanced editor-component and theme-introspection APIs.
- Interactive RPC extension UI.
- A read-only Pi-compatible `SessionManager` adapter.
- Registration precedence redesign.
- Agent, persistence, context, compaction, or provider refactors.

## Verification

Every behavior change starts with a failing regression test and follows red-green-refactor.

Required automated gates:

- focused extension host, event, tool, command, shortcut, automation, and RPC tests;
- full Python suite;
- npm launcher tests;
- npm pack dry run;
- Python wheel and source build;
- parity/acceptance verifier;
- relevant release-container smoke tests.

Required live gate:

- use the installed `travis234` console entry point in a real attached PTY;
- isolate state under `/tmp/travis234-extension-acceptance`;
- load `.env`;
- select `openrouter/xiaomi/mimo-v2.5-pro` through `/model mimo`;
- run the 21 scenarios in `evals/scenarios.json` one at a time in the same acceptance campaign;
- include extension creation, reload, commands, raw shortcuts, extension tools, mode/replacement continuity, compaction, and post-compaction continuation stress;
- classify weak answers as model quality rather than runtime defects;
- stop and diagnose provider failures, malformed tool behavior, lifecycle duplication, context changes, protocol corruption, or leaked processes.

No Git operation occurs until these gates pass.
