# Travis234 Session Generation Parameters Design

**Status:** Approved

**Date:** 2026-07-17

**Scope:** Direct `/params` mutation, durable session overrides, and provider-call wiring

## Goal

Turn the existing read-only `/params` command into a direct, typed editor for the active session's model-generation settings. Changes must affect the next provider turn, survive session resume and branch operations, remain isolated from other sessions, and preserve the existing Agent loop, provider payload builders, compaction behavior, context accounting, and startup configuration.

## Product contract

The command remains readable and scriptable. It does not open a picker.

```text
/params
/params temperature
/params temperature 0.2
/params thinking high
/params stop END,STOP
/params reset temperature
/params reset
```

The supported names are:

- `thinking`
- `temperature`
- `top_p`
- `max_tokens`
- `timeout_seconds`
- `frequency_penalty`
- `presence_penalty`
- `seed`
- `parallel_tool_calls`
- `tool_choice`
- `stop`
- `provider_sort`

### Read operations

- `/params` displays the active provider/model, current thinking level, effective generation parameters, their sources, and current capability warnings.
- `/params <name>` preserves the existing filtered-display behavior.
- A filter that matches neither a parameter nor a warning reports `no generation parameter matching <name>`.

### Write operations

- `/params <name> <value>` validates and writes one setting.
- The complete remainder after `<name>` is treated as the value so stop sequences and textual tool choices are not truncated at the first space.
- `/params thinking <level>` delegates to the existing model-aware `AgentSession.set_thinking_level()` method. Thinking never enters the generation-parameter override map.
- `/params reset <name>` removes one saved generation override and reveals the startup/provider default for that field.
- `/params reset` removes every saved generation override. It does not alter thinking because thinking is an independent durable session setting.
- `/params reset thinking` reports that thinking must be set explicitly and makes no change.
- `none`, `null`, and an empty value are not implicit reset operations. The command reports the explicit `reset` syntax instead of silently changing state.

Successful writes report the new effective value and that it applies to the next turn. Reset operations report either the restored effective value or that the field now uses its provider/model default.

## Precedence and effective values

The provider request observes this precedence from lowest to highest:

1. provider and dotenv configuration
2. startup CLI generation parameters
3. durable active-session overrides
4. existing runtime recovery and context/output safety clamps

Thinking remains owned by the session's existing thinking-level state and provider reasoning mapping.

Session overrides contain only explicitly changed fields. Resetting a field removes it from the override map, allowing the current startup/provider value to become effective. A resumed session's saved overrides win over startup flags for the fields it owns; untouched fields continue to inherit the current startup/provider configuration.

`/params` reports configured effective values and capability warnings. Context-dependent output-token clamping remains a request-time safety decision and may lower `max_tokens`; the command must not claim that a requested value bypasses that clamp.

## Durable session representation

Add one append-only JSONL entry type:

```json
{
  "type": "generation_params_change",
  "params": {
    "temperature": 0.2,
    "max_tokens": 8192,
    "stop": ["END", "STOP"]
  }
}
```

The entry stores the complete normalized **override map**, not a patch and not the merged startup defaults. An empty map represents a full reset. Each successful mutation appends one complete snapshot after validation. This makes replay deterministic and makes reset semantics independent of earlier events.

Allowed JSON values are limited to the existing typed `GenerationParams` fields. Provider preferences, credentials, headers, prompt content, and arbitrary keys are forbidden. Source labels are reconstructed as `session` and are not accepted from JSONL input.

This additive entry remains compatible with session format version 3; it does not require a version bump. Older Travis234 builds ignore the non-message entry, while the new reader validates it before use.

`SessionStore.build_context()` scans the complete active branch and returns the latest normalized override map alongside messages, thinking level, model, and session name. The new entry is non-message state: it never becomes model context and is not included in compaction summaries.

Because the entry participates in the existing parent-linked branch, these operations inherit the correct value without special copying:

- resume
- fork
- clone
- import/export
- session-tree navigation

Navigating before a parameter entry restores the earlier branch value. Navigating after it restores the newer value.

## Runtime ownership

### Session owner

`AgentSession` owns the normalized generation override map and exposes narrow operations:

- read a copy of current overrides
- set one validated override
- reset one override
- reset all overrides

Mutation is atomic from the caller's perspective: construct and validate the candidate map, append its complete snapshot, then publish it in memory. A persistence failure leaves the previously effective map unchanged.

The existing `GenerationParams` parser remains the authority for ranges and types. Session code does not duplicate numeric or boolean parsing rules.

### TUI owner

`InteractiveMode` retains the startup `GenerationParams` passed by the CLI. It combines those values with the active session's restored overrides for display and provider calls. On session rebound it discards the previous session's override view, loads the newly active session's map, recomputes warnings, refreshes the footer thinking value, and requests one render.

The `/params` command handler owns grammar, active-turn rejection, user-facing results, and help/autocomplete text. It delegates persistence to `AgentSession`.

### Provider-call adapter

A narrow TUI stream adapter copies the effective `GenerationParams` into the existing `SimpleStreamOptions` for every model call in the turn, including tool continuations and automatic provider retries. It then calls the session's existing model registry stream method.

The adapter does not alter prompts, messages, tools, iteration budgets, event ordering, parallel execution, or response handling. Existing request preparation continues to merge provider configuration, apply transport capability policy, and enforce request-time safety clamps.

`max_tokens` is passed through the existing runtime option and request clamp path. The request uses the lower of the configured effective value and the current model/runtime cap, so a session value can lower output allocation but cannot raise it beyond model, context, or recovery limits.

## Model capability behavior

After a write, reset, model switch, or session rebound, Travis recomputes warnings with the existing generation-payload capability policy for the active provider and API mode.

- A valid but unsupported setting remains durably saved.
- `/params` marks it as dropped with the existing reason.
- The actual provider request follows the same capability policy.
- Switching to a compatible model may activate the saved setting without rewriting the session.

Thinking levels use the active model's existing supported-level list and clamping behavior. Non-reasoning models therefore remain `off` even when a different level is requested.

## Concurrency and error handling

- Parameter mutations are rejected while an Agent turn is active. Read-only `/params` operations remain available.
- Unknown names report the supported-name list.
- Missing values report exact usage.
- Validation errors report the existing field-specific message and append nothing.
- Persistence errors report failure and preserve the previous in-memory and durable value.
- Resetting a missing field or an already empty override map succeeds idempotently without appending redundant state.
- Provider warnings are informational; they do not invalidate otherwise typed session state.
- No command value is logged as a credential, interpreted as a path, or executed as code.

## Validation rules

The current `GenerationParams` rules remain unchanged:

- `temperature`: finite number from 0 through 2
- `top_p`: finite number from 0 through 1
- `max_tokens`: positive integer
- `timeout_seconds`: positive finite number
- `frequency_penalty` and `presence_penalty`: finite number from -2 through 2
- `seed`: integer
- `parallel_tool_calls`: strict accepted boolean spellings
- `tool_choice` and `provider_sort`: non-empty text
- `stop`: JSON string array or comma-separated string sequence

## Tests

Regression tests are written before production changes.

### Command and parsing

- preserve `/params` and `/params <filter>` classification
- parse direct set and reset forms without stealing ordinary prompts
- preserve the full value remainder
- update help and autocomplete descriptions
- reject unknown names, missing values, implicit null/reset values, and active-turn writes

### Session persistence

- append normalized full override snapshots
- restore the latest active-branch snapshot
- empty snapshot resets all overrides
- an invalid JSONL parameter entry is ignored, preserving the latest prior valid snapshot or an empty map when none exists
- persistence failure leaves memory unchanged
- resume, fork, clone, import/export, and tree navigation preserve branch-local values
- compaction leaves parameter entries as non-context session state

### Provider behavior

- startup values remain effective before any override
- session overrides win on the next provider request
- tool continuation and retry calls receive the same effective values
- resetting reveals startup/provider values
- separate sessions cannot leak overrides
- model switch and rebound recompute capability warnings
- request-time `max_tokens` safety clamps remain authoritative
- thinking changes reach provider reasoning through the existing session path

### Isolation

- mutations do not change messages, estimated context, compaction count, Agent-loop ordering, tool ordering, or persisted prompt content
- no provider payload builder needs a structural rewrite
- no secret-shaped values enter the new session event

### Repository gates

Before completion, run focused parameter/session/provider/TUI tests, the complete Python suite, npm launcher tests, Python and npm package builds, clean-wheel acceptance, Pi/Hermes parity verification, a real installed-entry PTY scenario, and the release-container smoke test required by repository guidance.

## Documentation

Update README command documentation with direct examples, persistence/reset semantics, supported fields, next-turn behavior, active-turn rejection, and provider-warning behavior.

## Non-goals

- A visual settings dashboard or parameter picker
- Global or project-default mutation
- Editing provider credentials, URLs, headers, or arbitrary request JSON
- Mid-turn parameter replacement
- Parameter changes for compaction or auxiliary summarizer models
- Changing provider capability policy
- Changing context limits, compaction thresholds, Agent-loop budgets, or parallel tool execution
- Treating generation settings as prompt or transcript content
