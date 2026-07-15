# Production-Safe Extension CLI Flags Design

**Date:** 2026-07-15
**Status:** Approved
**Scope:** Python-native extension-defined CLI flags for `travis234`

## Summary

Travis already supports extension flag registration, defaults, runtime lookup, SDK-supplied values, and value preservation across `/reload`. The installed `travis234` CLI does not expose that capability because its strict `argparse` pass rejects extension-defined options before extensions load.

This design adapts Pi's deferred extension-flag strategy without copying Pi's greedy unknown-token parsing. Travis will load authorized extension schemas once, add typed long options to the normal parser, and then strictly parse the original argument vector. String flags support both `--name value` and `--name=value`; boolean flags never consume the following prompt.

Flag values are process-local. They are applied before session lifecycle events and reapplied whenever `CodingApp` constructs a replacement runtime. They do not change settings, session JSONL, the system prompt, or the base context envelope.

## Current State

The relevant capability is split across two startup paths:

- `ExtensionRunner.register_flag()` records `ExtensionFlag` definitions and defaults.
- `ExtensionRunner.get_flag()` exposes the effective value to an extension.
- `create_agent_session_services()` accepts `extensionFlagValues` and applies them after resource loading.
- `SessionExtensionController.reload()` copies existing values to the replacement runner.
- `travis.cli.main()` constructs `CodingApp` directly and uses one strict `parser.parse_args()` call before any extensions load.
- `CodingApp._create_session()` creates a new `DefaultResourceLoader` for every replacement runtime but has no extension flag input.

Consequently, SDK-level flag tests pass while a CLI invocation such as `travis234 --profile security` fails with argparse exit code 2 and `unrecognized arguments: --profile`.

The production-safe parity design already promises extension-defined flags, but the detailed CLI implementation plan and Pi parity manifest do not contain a corresponding executable contract.

## Goals

1. Expose Python extension flags through the installed `travis234` CLI.
2. Preserve normal typed argparse behavior, including prompt boundaries and deterministic errors.
3. Load each extension factory at most once per runtime construction.
4. Preserve project-trust and explicit-resource authorization boundaries.
5. Apply CLI values before any session lifecycle event or provider request.
6. Reapply the same values across new, forked, cloned, switched, and imported sessions.
7. Keep existing SDK `extensionFlagValues` compatibility.
8. Add explicit Pi parity evidence for extension flags.

## Non-Goals

- Short aliases for extension flags
- Automatic `--no-<flag>` inverse options
- Persisting flag values in settings or session JSONL
- Executing JavaScript or native Pi extensions
- Changing package subcommand parsing
- Making CLI values available while an extension factory is still registering itself
- Changing the core agent loop, compaction, iteration budgets, or bounded parallel execution
- Broad SDK, TUI, resource, or package-manager redesign

## Observable CLI Contract

### Supported forms

Given:

```python
def extension(travis):
    travis.register_flag(
        "verbose",
        {"type": "boolean", "description": "Enable verbose extension output"},
    )
    travis.register_flag(
        "profile",
        {"type": "string", "description": "Select an extension profile"},
    )
```

the CLI accepts:

```text
travis234 --verbose "inspect this repository"
travis234 --profile security "inspect this repository"
travis234 --profile=security "inspect this repository"
```

Rules:

- Extension flags use long names only.
- A boolean option has arity zero and sets its value to `True`.
- A string option has arity one.
- Repeated options use the last supplied value, matching Pi's map behavior.
- A boolean never consumes the following positional prompt.
- Tokens after `--` remain positional prompt text.
- Omitted flags retain the default established by `register_flag()`.
- Unknown options and missing values use normal argparse diagnostics and exit code 2.
- A supplied boolean value such as `--verbose=false` is rejected rather than silently interpreted as true.
- Extension options cannot shadow built-in CLI options.

### Registration constraints

The existing default type of `boolean` remains compatible. For CLI exposure:

- The effective type must be `boolean` or `string`.
- The name must be non-empty and form a valid long option without leading dashes.
- A name that collides with a built-in option is a fatal CLI schema error.
- Duplicate names keep first-registration precedence inside `ExtensionRunner` for deterministic introspection, but CLI startup reports the conflicting owners and fails because the option schema is ambiguous.

SDK-only construction remains able to inspect the deterministic first registration. The stricter collision failure applies when building a CLI schema.

## Architecture

```text
original argv
    |
    v
bootstrap core parser (no positional prompt)
    |
    v
resolve launch cwd, explicit paths, session cwd, and trust inputs
    |
    v
load global and explicit operator extensions once
    |
    v
provisional typed parse for trust-mode classification
    |
    v
resolve project trust and finish resource loading
    |
    v
collect all authorized ExtensionFlag schemas
    |
    v
augment the full argparse parser
    |
    v
strictly parse the original argv
    |
    v
extension_flag_values
    |
    v
CodingApp -> every created AgentSession
```

The initial resource loader is passed into `CodingApp` and consumed once. Replacement sessions create new cwd-bound loaders and apply the same process-local CLI values before constructing `AgentSession`.

## Component Design

### 1. Reusable CLI parser construction

`travis/cli.py` will extract its inline parser definition into a builder with an interface equivalent to:

```python
_build_parser(
    *,
    include_prompt: bool,
    extension_flags: Mapping[str, ExtensionFlag] | None = None,
) -> argparse.ArgumentParser
```

The builder preserves all current core arguments and mutual-exclusion rules. Automatic argparse help is replaced by a stored core `--help` option so authorized extension schemas can be loaded before help is rendered.

A dedicated argparse action writes dynamic values into an exact-name `extension_flag_values` mapping on the namespace. It does not derive ordinary namespace attribute names from flag text, preventing collisions such as `foo-bar` versus `foo_bar`.

The parser's existing abbreviation behavior is left unchanged. Tightening abbreviation globally would alter unrelated core CLI invocations and is outside this feature.

### 2. Three parsing stages

#### Bootstrap parse

The bootstrap parser contains core options but no positional prompt. It uses `parse_known_args()` only to obtain inputs needed before extension discovery, including:

- cwd and dotenv selection
- session selection
- trust override
- mode and plain/TUI controls
- explicit extension, skill, prompt-template, theme, and image paths
- offline mode
- early metadata actions

Unknown tokens are retained in their original order. No unknown token is assigned extension semantics during this phase.

Package subcommands and existing one-shot actions that do not consume extension flags keep their current early-dispatch behavior. Dynamic help is the intentional exception because it must discover authorized schemas before rendering.

#### Provisional typed parse

After global and explicit operator extensions load, their schemas are added to a parser. A provisional `parse_known_args()` determines whether the invocation can safely use an interactive trust prompt.

If unresolved option tokens remain, trust classification is conservative: an unknown project is treated as noninteractive for that launch. This prevents an as-yet-untrusted project extension from influencing the decision to execute itself. A project-only flag supplied in this ambiguous state therefore requires saved trust or `--approve`.

Help is noninteractive and never prompts for trust.

#### Final strict parse

After project trust is resolved and all authorized extensions load, the full parser includes every available extension schema and the positional prompt. It parses the original argv with `parse_args()`.

This final pass owns prompt construction, required-value errors, unknown-option errors, repeated-value precedence, and mode validation. No provider or agent turn begins before it succeeds.

### 3. Staged resource loading without duplicate execution

`DefaultResourceLoader` already has the required internal split:

- `load_project_trust_extensions()` loads resources allowed to participate in trust resolution.
- `_reload_all_resources(pretrust_extensions=...)` reuses the preloaded runtime while completing authorized resource loading.

The loader will expose a narrow completion operation that accepts the preloaded result, trust context, and trust store. Normal `reload()` remains a convenience wrapper around the same two phases.

The CLI uses the phases directly:

1. Create one loader for the effective startup cwd.
2. Load global and explicit operator extensions.
3. Perform provisional parsing and construct the trust context.
4. Resolve trust and complete the loader without re-running preloaded factories.
5. Reuse this completed loader in the initial `CodingApp` session.

Project extensions load once after approval. Global and explicit extensions load once before approval and remain in the shared pretrust runtime.

### 4. Shared flag value application

The private `_apply_extension_flag_values()` logic in `agent_session_services.py` becomes a shared extension-layer helper. The helper accepts an `ExtensionRunner` and a mapping of exact flag names to boolean or string values.

It:

- checks each supplied name against the current runner schema;
- applies `True` for supplied boolean flags;
- applies string values for string flags;
- leaves omitted defaults untouched;
- returns structured diagnostics for unknown names or type mismatches.

Both `create_agent_session_services()` and `CodingApp` call this helper. Camel-case and snake-case SDK inputs remain supported at the service boundary.

CLI values are applied only after all factories finish registering. Extensions therefore see defaults during factory execution and effective CLI values during session events, commands, tools, provider hooks, and prompt processing. This matches Pi's lifecycle boundary.

### 5. CodingApp ownership and replacement sessions

`CodingApp` gains inputs equivalent to:

```python
initial_resource_loader: DefaultResourceLoader | None
extension_flag_values: Mapping[str, bool | str] | None
```

It stores an immutable copy of the process-local values. `_create_session()`:

1. uses the initial preloaded loader once when its cwd matches;
2. otherwise constructs and reloads a normal cwd-bound loader;
3. applies and validates the stored flag values;
4. constructs `AgentSession` only after validation succeeds.

This path covers startup, new session, fork, clone, switch, and import. Existing `/reload` behavior continues copying current flag values from the old runner to the replacement runner.

If a replacement cwd does not register a flag supplied at process startup, replacement construction fails before `AgentSessionRuntime` tears down the active session. The existing session remains bound and usable.

### 6. Extension-aware help

Help is rendered after authorized schema discovery but before model and session construction.

Expected behavior:

- `travis234 --help` includes authorized global extension flags.
- `travis234 --extension ./extension.py --help` includes that explicit extension's flags.
- Trusted project flags appear when trust is saved or `--approve` is supplied.
- Unknown project code is not executed merely to produce help.
- Each entry shows `--name`, a `<value>` marker for strings, and its description.
- When a description is absent, help identifies the owning extension.
- Help does not create a session file, initialize a provider, emit session lifecycle events, or make a model request.

## Trust Matrix

| Extension source | Flag schema available | Requirement |
|---|---:|---|
| Global authorized extension | Yes | Existing global configuration |
| Explicit `--extension` path | Yes | Direct operator selection; path is resolved before session cwd changes |
| Project extension with saved trust | Yes | Saved positive trust decision |
| Project extension with `--approve` | Yes | Explicit process-local approval |
| Unknown project, ordinary interactive startup | After approval | Existing trust selector may run when argv is unambiguous |
| Unknown project with a project-only flag already in argv | No | Fail closed; use saved trust or `--approve` |
| Unknown project in print, JSON, RPC, plain, or help mode | No | Existing noninteractive fail-closed policy |
| Explicitly denied project | No | `--no-approve` or saved denial |

The presence of an option-shaped token never authorizes executable project code.

## Error Handling

| Condition | Initial CLI behavior | Replacement-session behavior |
|---|---|---|
| Unknown option | argparse error, exit 2 | Not applicable to already parsed argv |
| Missing string value | argparse error, exit 2 | Not applicable to already parsed argv |
| Invalid extension flag schema | stderr diagnostic, no session created | Replacement rejected; current session retained |
| Built-in option collision | stderr diagnostic, no session created | Replacement rejected; current session retained |
| Duplicate extension flag owners | stderr diagnostic naming both owners, no session created | Replacement rejected; current session retained |
| Supplied flag absent in target cwd | Startup error | Replacement rejected; current session retained |
| Extension load failure | Preserve existing structured diagnostic and fatal/non-fatal policy | Preserve existing policy; a supplied flag missing from the resulting schema still rejects replacement before teardown |
| Untrusted project-only flag | Unknown-option error after fail-closed loading | Not loaded without an explicit/saved trust decision |

JSON and RPC stdout remain reserved for machine frames. All startup diagnostics go to stderr.

## State and Context-Envelope Effects

Extension flag definitions and values are runtime control data. They are not written to:

- global or project settings;
- session JSONL;
- session SQLite indexes;
- system prompts;
- message history;
- provider payloads by the core runtime;
- tool schemas merely because a flag exists.

The base context-envelope effect is therefore zero. An extension can intentionally change the envelope after reading a flag, for example by enabling a tool, adding a discovered resource, or modifying a prompt. That downstream cost belongs to the extension behavior and is visible through the normal canonical envelope estimator.

No migration is required for existing sessions or `~/.travis234` data.

## Test Strategy

### Parser and help tests

- boolean flag before a prompt preserves the prompt;
- string flags support separated and equals forms;
- repeated strings use the last value;
- boolean values supplied with `=` are rejected;
- missing string values fail with exit code 2;
- unknown long and short options fail with exit code 2;
- `--` terminates option parsing;
- built-in and duplicate collisions identify owners;
- dynamic help includes type markers and descriptions;
- help does not initialize `CodingApp`, a model provider, or a session file.

### Trust tests

- explicit operator extension flags work without project trust;
- global extension flags work in an otherwise untrusted project;
- project-only flags load with saved trust;
- project-only flags load with `--approve`;
- an unknown project-only flag does not execute project code;
- `--no-approve` prevents project flag discovery;
- help never executes unknown project code.

### Runtime lifecycle tests

- an extension reads the CLI value during the first runtime hook;
- values survive `/reload`;
- values survive new, fork, clone, switch, and import paths;
- replacement into a cwd without the supplied schema fails transactionally;
- the original session remains active after replacement failure;
- SDK `extensionFlagValues` behavior remains compatible.

### Transport and regression tests

- print, JSON, RPC, and interactive paths share the same applied value;
- JSON and RPC stdout contain no startup diagnostics;
- documented invocations without extension flags preserve current behavior;
- package subcommands remain unchanged;
- core agent-loop and compaction tests remain unchanged.

### Parity evidence

Add a `pi.cli.extension_flags` entry to `scripts/parity_contracts.py` backed by an end-to-end CLI test. The contract should cover registration, typed parsing, runtime visibility, and prompt preservation rather than only `ExtensionRunner` unit behavior.

## Verification Scope

Implementation verification must include:

1. focused parser, extension, resource-loader, trust, and session-replacement tests;
2. the complete Python test suite;
3. npm launcher tests;
4. Python and npm package builds;
5. installed CLI help and extension-flag smoke tests;
6. relevant no-cache container build and CLI smoke checks;
7. parity-contract validation.

No live model call is required because this feature completes before provider invocation.

## Alternatives Considered

### Exact Pi unknown-token capture

Pi captures every unknown long option before extension discovery and heuristically consumes the following non-option token as its value. This is compact, but it allows a boolean option such as `--verbose hello` to swallow `hello` instead of preserving it as the prompt. Travis will not copy this ambiguity.

### Equals-only deferred string flags

Requiring `--profile=security` removes arity ambiguity without staged typed parsing. It is safe but unnecessarily breaks Pi-compatible `--profile security` usage and produces weaker help integration.

### Load extensions twice

A schema-only preflight loader followed by ordinary `CodingApp` loading would minimize constructor changes, but extension factories can have registration or initialization side effects. Double execution is not acceptable.

### Migrate the CLI onto the SDK service factory

The SDK service factory already applies extension values, but replacing `CodingApp` composition with that path would broaden the change into model, process, compaction, TUI, and session ownership. The focused preloaded-loader input preserves the existing application architecture.

## Acceptance Criteria

The design is satisfied when all of the following are true:

1. A Python extension can register boolean and string flags that the installed CLI accepts.
2. `--verbose hello` leaves `hello` as the prompt.
3. Authorized extension factories execute once per runtime construction.
4. Untrusted project code cannot load because an extension-looking option appears in argv.
5. Values are visible before the first runtime hook and across replacement sessions.
6. Replacement schema failure leaves the current session active.
7. Dynamic help is accurate and creates no session or provider runtime.
8. Existing SDK callers and invocations without extension flags remain compatible.
9. No settings, session, loop, compaction, or base context-envelope format changes occur.
10. The Pi parity manifest includes executable extension-flag evidence.
