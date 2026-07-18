# Travis234 extensions

Travis234 extensions are trusted Python modules that add commands, tools, event handlers, shortcuts, typed CLI flags, providers, custom-message renderers, resource paths, and TUI presentation hooks. They execute inside the Travis234 process with the user's permissions. Load only code you trust.

This file is the installed, authoritative extension reference for the Python runtime. Travis234 advertises its absolute installed path to the agent whenever the user asks about Travis234 extensions. **No extension-authoring skill is required**: the agent should read this file completely, create or repair the extension, syntax-check it, reload it, run it, inspect the result, and continue until the user confirms that it works.

## Create an extension with the agent

For a project-only extension, ask the agent to:

1. Read this installed guide completely.
2. Create .travis234/extensions/<name>.py inside the workspace.
3. Keep the first version small and independently testable.
4. Run python -m py_compile .travis234/extensions/<name>.py.
5. Resolve project trust with /trust if the workspace is not already trusted.
6. Run /reload.
7. Invoke the new command, shortcut, or tool through the real TUI.
8. Read any source-attributed diagnostic, repair the file, repeat the syntax check and /reload, then rerun the same behavior.
9. Stop changing the extension only after the requested behavior works and the user confirms it.

For an extension available in every workspace, use:

~~~text
~/.travis234/agent/extensions/<name>.py
~~~

Do not put generated extensions inside the installed Python package. Wheel or tool upgrades replace package files. User-owned extensions belong in the global or project locations above.

## Discovery and trust

Travis234 discovers extensions from:

~~~text
~/.travis234/agent/extensions/   # global user extensions
.travis234/extensions/          # project extensions, after trust resolves
~~~

An operator may also pass repeatable --extension PATH options or configure extension resource paths. An explicit path is operator-selected. Project-owned automatic discovery remains disabled until trust is resolved.

Unknown projects fail closed: Travis234 does not import project extensions merely to ask whether the project should be trusted. Use:

- --approve or --no-approve for a process-only decision;
- /trust for a saved project or parent decision;
- /reload after a saved trust decision to load the newly authorized resources.

Global extensions remain available while project trust is unresolved. Extension packages follow the same global-versus-trusted-project boundary described in Packages.

Discovery accepts Python files. A discovered module must export a callable named extension. JavaScript and TypeScript extensions are not executed by this runtime.

## Extension module anatomy

Minimal extension:

~~~python
def extension(travis):
    async def handle_check(args, ctx):
        target = args.strip() or "workspace"
        ctx.ui.notify(f"Checking {target}")
        return ctx.send_message(
            {
                "customType": "live-check",
                "content": f"CHECK_OK:{target}",
                "display": True,
            }
        )

    travis.register_command(
        "live-check",
        {
            "description": "Run the live check",
            "handler": handle_check,
        },
    )
~~~

Rules:

- extension(travis) may be synchronous or asynchronous.
- The factory runs while resources are loading. Registration methods are available then.
- Session actions such as sending messages or changing tools are unavailable until the session is bound. Put those actions inside commands, event handlers, shortcuts, or tool execution.
- The travis object is source-scoped. Registrations and diagnostics retain the extension file path.
- Sync and async handlers are both supported. Awaitables are resolved exactly once at the host boundary.
- Event and command handlers may accept the context as a second positional argument. Legacy one-argument handlers remain supported.

The factory API exposes these registration surfaces:

~~~text
travis.register_command(name, options)
travis.unregister_command(name)
travis.register_flag(name, options)
travis.register_shortcut(key, options)
travis.register_tool(tool_definition)
travis.unregister_tool(name)
travis.register_provider(name, config)
travis.unregister_provider(name)
travis.register_message_renderer(custom_type, renderer)
travis.on(event_name, handler)
travis.events.on(custom_event_name, handler)
travis.events.emit(custom_event_name, value)
~~~

The shared travis.events bus is for extension-to-extension application events. Use travis.on(...) for the pinned Travis234 lifecycle events.

## Source lifetime, reload, and session replacement

An extension factory receives a source-scoped API proxy. Event, tool, and command handlers receive generation-guarded contexts. Do not retain either object across /reload or session replacement.

These operations replace or invalidate an extension generation:

- /reload;
- /new;
- resume or switch;
- fork;
- clone;
- import or another active-session replacement;
- application shutdown.

Calls through an old API or context raise a stale-context RuntimeError intentionally. The correct recovery is to let the new extension factory run and reacquire a fresh handler context, not to suppress or retry through the captured object.

Reload is refused while a provider turn or compaction is active. It invalidates the old runtime; clears old registrations and extension UI state; rediscovers resources; creates a fresh runtime; and emits startup once for the active session.

Session replacement binds the new host before its deferred session_start. Every created session receives one start event. Replaced and closed sessions retain their established shutdown event. Extensions do not own replacement ordering or session locks.

## Commands, flags, and shortcuts

### Slash commands

~~~python
def extension(travis):
    def handle_status(args, ctx):
        result = ctx.exec(
            "git",
            ["status", "--short"],
            {"cwd": ctx.cwd, "timeout": 5_000},
        )
        ctx.ui.set_status("git", "clean" if not result["stdout"] else "dirty")
        return ctx.send_message(
            {
                "customType": "git-status",
                "content": result["stdout"] or "Working tree clean",
                "display": True,
                "details": {"exitCode": result["code"]},
            }
        )

    travis.register_command(
        "repo-status",
        {"description": "Show repository status", "handler": handle_status},
    )
~~~

A command receives the text after its name as one args string and a fresh command context. It runs without creating a provider turn, may be sync or async, executes exactly once on the serialized extension-command lane, and is never inserted into steering or follow-up queues.

Optional getArgumentCompletions or get_argument_completions may provide argument completion. Duplicate command names remain addressable in source order as name, name:1, name:2, and so on.

### Typed CLI flags

Only boolean and string flags are supported:

~~~python
def extension(travis):
    travis.register_flag(
        "review-mode",
        {
            "type": "string",
            "description": "Select the review profile",
            "default": "focused",
        },
    )
    travis.register_flag(
        "review-verbose",
        {"type": "boolean", "description": "Show detailed diagnostics"},
    )

    def show_flags(_args, ctx):
        ctx.ui.notify(
            f"mode={travis.get_flag('review-mode')} "
            f"verbose={travis.get_flag('review-verbose')}"
        )

    travis.register_command("review-flags", {"handler": show_flags})
~~~

Flag names must be valid long-option names and must not collide with built-in options or another extension. Boolean flags consume no value. String flags require one value; repeating a string flag uses the last value.

Flag values are process-local. They are reapplied when the app replaces its active session and are not written to settings or session JSONL. Project extension flag schemas are unavailable before project trust resolves.

### Raw TUI shortcuts

~~~python
def extension(travis):
    async def mark_ready(ctx):
        ctx.ui.set_status("review", "ready", {"state": "working"})

    travis.register_shortcut(
        "ctrl+r",
        {"description": "Mark review ready", "handler": mark_ready},
    )
~~~

Shortcuts are matched against raw terminal input before editor submission and can run without Enter. They may be synchronous or asynchronous. Built-in and user-configured editor keybindings are protected; a conflict is skipped with a source-attributed diagnostic. Raw shortcuts are unavailable in line-input fallback because that host only receives submitted lines.

## Tools and providers

### Typed tools

Use the canonical ToolDefinition bridge:

~~~python
from travis.agent.types import AgentToolResult
from travis.ai.types import TextContent
from travis.coding_agent.tools.types import ToolDefinition


def extension(travis):
    def execute(tool_call_id, args, signal=None, on_update=None, ctx=None):
        value = str(args.get("value", ""))
        return AgentToolResult(
            content=[TextContent(text=f"validated:{value}")],
            details={"toolCallId": tool_call_id, "cwd": ctx.cwd},
        )

    travis.register_tool(
        ToolDefinition(
            name="validate_value",
            label="validate value",
            description="Validate one text value",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            execute=execute,
            prompt_guidelines=[
                "Use validate_value only when the user asks to validate a value."
            ],
        )
    )
~~~

The execute signature is:

~~~text
execute(tool_call_id, args, signal=None, on_update=None, ctx=None)
~~~

The tool context is the active generation-guarded extension context. Tool registration after session binding refreshes the live registry. Registration alone does not guarantee activation: selected-tool policy and allowlists still determine which schemas enter the next provider request.

Built-in wrapping, argument preparation, abort signaling, bounded execution, result ordering, and session persistence remain owned by Travis234.

### Providers

register_provider(name, config) delegates to Travis234's Python model registry. Registration during factory loading is queued until the session binds. One invalid queued provider produces a source-attributed diagnostic without preventing unrelated extensions from loading.

~~~python
travis.register_provider(
    "example",
    {
        "baseUrl": "https://provider.example/v1",
        "api": "openai-responses",
        "apiKey": "$EXAMPLE_API_KEY",
        "models": [
            {
                "id": "example-model",
                "name": "Example Model",
                "reasoning": True,
                "input": ["text"],
                "cost": {
                    "input": 0,
                    "output": 0,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                },
                "contextWindow": 128_000,
                "maxTokens": 8_192,
            }
        ],
    },
)
~~~

When defining models, baseUrl, an api, and either apiKey or oauth are required. Environment references are resolved by the existing authentication owner. Never embed secrets in an extension file.

unregister_provider(name) closes the extension-owned registration and restores any pre-existing provider models that it replaced. Extensions cannot bypass provider authentication, request translation, capability filtering, or request ownership.

### Custom messages and renderers

register_message_renderer(custom_type, renderer) customizes TUI rendering for matching custom messages. A renderer receives (message, options=None, theme=None) and may return a TUI component.

Use ctx.send_message(...) to create a custom message:

~~~python
ctx.send_message(
    {
        "customType": "review-result",
        "content": "No blocking findings",
        "display": True,
        "details": {"blocking": 0},
    },
    {"transient": False},
)
~~~

Supported delivery options include transient, deliverAs (nextTurn, steer, or followUp where applicable), and triggerTurn. Transient messages are rendered but not persisted.

## Events

Register lifecycle handlers with:

~~~python
unsubscribe = travis.on("tool_call", handler)
~~~

Handlers receive (event, ctx); one-argument handlers remain supported. Sync and async handlers are awaited in registration order.

The 33 pinned event names are:

~~~text
project_trust
resources_discover
session_start
session_info_changed
session_before_switch
session_before_fork
session_before_compact
session_compact
session_shutdown
session_before_tree
session_tree
context
before_provider_request
before_provider_headers
after_provider_response
before_agent_start
agent_start
agent_end
agent_settled
turn_start
turn_end
message_start
message_update
message_end
tool_execution_start
tool_execution_update
tool_execution_end
model_select
thinking_level_select
tool_call
tool_result
user_bash
input
~~~

Important contracts:

- project_trust uses the first decisive {"trusted": "yes"} or {"trusted": "no"} result; "undecided" continues.
- resources_discover merges returned skillPaths, promptPaths, and themePaths and retains the source extension.
- session_before_switch, session_before_fork, session_before_compact, and session_before_tree accept truthy results; {"cancel": True} stops the chain.
- input chains {"action": "transform", "text": ..., "images": ...}; {"action": "handled"} ends processing; unchanged input returns continue.
- before_agent_start may return message and/or systemPrompt. Later handlers see the latest prompt through the event and ctx.get_system_prompt().
- That temporary system-prompt view is scoped only to before_agent_start; other event contexts see the ordinary session prompt.
- context handlers chain {"messages": [...]} replacements.
- before_provider_request chains each non-None returned payload.
- before_provider_headers mutates the shared headers mapping; assigning None requests deletion. Return values do not replace the mapping.
- message_end may replace message, but its role must remain unchanged.
- tool_result may replace content, details, and isError.
- tool_call may return {"block": True, "reason": "..."}. Unlike ordinary observer failures, a tool_call exception propagates to the tool boundary and blocks unsafe execution.
- user_bash uses the first truthy result.
- model_select.source is set, cycle, or restore.
- thinking_level_select exposes level and previousLevel plus thinkingLevel and previousThinkingLevel.

Ordinary observer failures are isolated, attributed to their extension path, and reported without stopping unrelated handlers.

## User messages and input boundaries

ctx.send_user_message(content, options=None) accepts a string or text/image content blocks. It marks input source as extension, preserves images, supports deliverAs values steer and followUp, and deliberately skips slash-command and prompt-template expansion.

Therefore an extension-sent string such as /reload is model input, not a host command. Call command-context ctx.reload() when a command intentionally needs to reload.

Ordinary prompt templates expand only at the start of an ordinary user turn. They support shell quoting, $ARGUMENTS, $@, positional values, defaults, and slices.

## Context API

Event and tool contexts expose:

| Surface | Meaning |
| --- | --- |
| ctx.cwd | Active session working directory |
| ctx.mode | tui, print, json, or rpc |
| ctx.has_ui | Whether interactive UI is available |
| ctx.ui | Interactive UI or safe no-op UI |
| ctx.model | Active model |
| ctx.model_registry | Active Python model registry |
| ctx.session_manager | Bound session manager, when available |
| ctx.signal | Current abort signal |
| ctx.is_idle() | Whether the agent is idle |
| ctx.is_project_trusted() | Current project-trust state |
| ctx.abort() | Abort the active command or turn |
| ctx.has_pending_messages() | Whether queued work is pending |
| ctx.shutdown() | Request host shutdown |
| ctx.get_context_usage() | Current context telemetry |
| ctx.get_system_prompt() | Current applicable system prompt |
| ctx.compact(options) | Request normal extension compaction |
| ctx.spawn_subagent(role, goal, options) | Spawn and wait for one supervised task |
| ctx.list_subagents() | List supervised tasks |
| ctx.get_subagent_result(task_id) | Read one result |
| ctx.cancel_subagent(task_id, reason) | Cancel one task |

Command contexts additionally expose:

~~~text
ctx.get_system_prompt_options()
ctx.send_message(message, options=None)
ctx.send_user_message(content, options=None)
ctx.append_entry(custom_type, data=None)
ctx.set_session_name(name)
ctx.get_session_name()
ctx.set_label(entry_id, label)
ctx.get_active_tools()
ctx.get_all_tools()
ctx.set_active_tools(names)
ctx.get_commands()
ctx.get_thinking_level()
ctx.set_thinking_level(level)
ctx.set_model(model)
ctx.exec(command, args, options=None)
ctx.wait_for_idle()
ctx.new_session(options=None)
ctx.fork(entry_id, options=None)
ctx.navigate_tree(target_id, options=None)
ctx.switch_session(session_path, options=None)
ctx.reload()
~~~

ctx.exec() executes one argv vector without a shell, with stdin closed, captured stdout/stderr, optional cwd, and an optional millisecond timeout. It returns:

~~~python
{"stdout": str, "stderr": str, "code": int, "killed": bool}
~~~

It is not the managed-process API: it returns no process handle and cannot later poll, write, or resize. Use registered tools or ordinary agent process tools for long-running or interactive work.

Session-changing actions invalidate the old context. Return from the handler after new_session, fork, switch_session, or reload.

## UI API

The same runtime is bound in every host:

| Host | ctx.mode | ctx.has_ui | UI behavior |
| --- | --- | --- | --- |
| Interactive TUI | `tui` | True | Interactive extension UI |
| Print | `print` | False | Safe no-op UI |
| JSON | `json` | False | Safe no-op UI; stdout stays JSON-only |
| RPC | `rpc` | False | Safe no-op UI; stdout stays RPC JSON-only |

Interactive TUI methods:

~~~text
ctx.ui.notify(message)
ctx.ui.input(title, placeholder=None, options=None)
ctx.ui.select(title, options, dialog_options=None)
ctx.ui.confirm(title, message, options=None)
ctx.ui.editor(title, prefill=None)
ctx.ui.custom(factory, options=None)
ctx.ui.on_terminal_input(handler)
ctx.ui.set_status(key, text, options=None)
ctx.ui.set_working_message(message=None)
ctx.ui.set_working_visible(visible)
ctx.ui.set_working_indicator(options=None)
ctx.ui.set_hidden_thinking_label(label=None)
ctx.ui.set_title(title)
ctx.ui.set_widget(key, content=None, options=None)
ctx.ui.set_footer(factory=None)
ctx.ui.set_header(factory=None)
ctx.ui.set_editor_text(text)
ctx.ui.get_editor_text()
ctx.ui.paste_to_editor(text)
ctx.ui.add_autocomplete_provider(factory)
ctx.ui.set_theme(name)
ctx.ui.setTheme(name)
~~~

Use `ctx.has_ui` before requiring interaction. In non-interactive modes, selection/input/editor/custom UI return no value, confirmation returns False, editor text is empty, presentation mutations are no-ops, set_theme reports UI not available, and diagnostics go to stderr.

set_status(key, text, {"state": "working"}) may participate in Travis234's shared restrained working signal. Extensions cannot define animation loops or frame rates. UI updates never enter model context or session JSONL.

## Diagnose and repair

Syntax-check first:

~~~bash
python -m py_compile .travis234/extensions/example.py
~~~

Then run /reload and invoke the exact feature. TUI diagnostics appear in history; print/JSON/RPC diagnostics go to stderr; reload also reports skill, prompt, and theme diagnostics.

| Symptom | Cause | Repair |
| --- | --- | --- |
| Project extension does not load | Trust unresolved or denied | Use /trust, then /reload |
| Missing callable extension(travis) | Invalid entry point | Define def extension(travis): ... |
| stale-context RuntimeError | Captured old API/context | Reacquire it in the new factory/handler |
| Command produces no model response | Commands do not create model turns | Call send_user_message or send_message intentionally |
| Shortcut is ignored | Key conflict or line-input fallback | Choose another key and reload |
| Tool is absent from requests | Inactive or disallowed | Review active tools and allowlist |
| JSON output is invalid | Extension wrote to stdout | Use context/UI APIs; diagnostics use stderr |
| Provider registration fails | Missing route/API/auth/model fields | Correct config without hard-coded secrets |

When the user is not technical, the agent should perform this loop itself. It should not stop at "please edit the file" or ask the user to interpret a traceback the agent can read. Preserve the requested behavior, make the smallest repair, rerun the same reproduction, and ask only for final user-visible confirmation.

## Packages

Packages may be local directories, git+https URLs with an optional exact revision, or Python requirements.

~~~bash
travis234 install SOURCE
travis234 remove SOURCE
travis234 update [SOURCE]
travis234 list [--json]
travis234 config [--add SOURCE | --remove SOURCE]
~~~

Add --local --approve --cwd /path/to/project for trusted project scope. TUI equivalents are /install, /remove, /update, and /packages.

JavaScript manifest:

~~~json
{
  "name": "review-extension",
  "version": "1.0.0",
  "travis": {
    "extensions": ["extensions/review.py"],
    "skills": ["skills/review/SKILL.md"],
    "prompts": ["prompts/review.md"],
    "themes": ["themes/review.json"]
  }
}
~~~

Python manifest:

~~~toml
[project]
name = "review-extension"
version = "1.0.0"

[tool.travis234]
extensions = ["extensions/review.py"]
skills = ["skills/review/SKILL.md"]
prompts = ["prompts/review.md"]
themes = ["themes/review.json"]
~~~

Without manifest entries, Travis234 discovers conventional extensions, skills, prompts, and themes directories inside the package.

Installs are transactional. Startup diagnoses missing configured packages but never auto-installs or auto-updates. Network package subprocesses receive a credential-sanitized environment. --offline permits local operations and blocks network acquisition. Package resources cannot escape the package root.

## Context cost and ownership

Loading an extension and registering ordinary lifecycle handlers adds no model-context tokens by itself.

Context grows only when an extension activates context-bearing material: active tool schemas or guidelines, messages, system-prompt replacements, context-event replacements, or discovered and invoked skills/templates.

UI statuses, widgets, headers, footers, terminal titles, shortcuts, and diagnostics stay outside model context.

Extensions do not own agent-loop ordering, session JSONL or locks, context-envelope construction/accounting, compaction policy, provider translation/capability filtering, credential storage, iteration budgets, or bounded parallel tool execution.

ctx.compact() requests the existing normal compaction owner; it does not install a separate algorithm. Registered tools still use the canonical wrappers and policy boundaries.

## Intentional Pi divergences

Travis234 targets Pi's observable extension behavior through a Python-native boundary. The temporary system-prompt view is scoped only to `before_agent_start`. This runtime does not execute JavaScript/TypeScript extensions.

- it does not execute JavaScript or TypeScript extensions;
- it does not expose Pi's per-extension JavaScript container objects;
- it does not expose interactive RPC UI;
- it does not promise Pi-native provider or editor internals;
- JavaScript extensions require a reviewed Python adapter;
- Python snake_case names are canonical, with only documented aliases;
- providers remain inside Travis234's Python auth/model registry;
- tool execution remains bounded and session-owned.

The parity manifest maps the 33 lifecycle events and supported extension/resource/package/CLI/session behavior to executable tests. Treat this installed guide and those contracts as authoritative; do not infer unsupported API solely from a Pi TypeScript example.
