# appv231 Process Tool Contract Design

## Goal

Prevent models from wasting agent-loop iterations by mixing `bash`, `process.poll`,
and `process.wait` arguments while preserving the existing process runtime.

## Constraints

- Do not modify `appV2.3.1/appv231/agent/`.
- Do not modify `appV2.3.1/appv231/compaction/`.
- Keep one adaptable `process` tool; do not add model-specific tools.
- Preserve process service, completion, containment, output, session, and guardrail semantics.
- Do not silently invent a missing cursor.

## Design

`PROCESS_SCHEMA` becomes a discriminated `oneOf` contract. Each action declares
only its accepted fields, exact required fields, useful descriptions, and
`additionalProperties: false`. The existing runtime validator remains as
defense in depth.

A process argument preparer normalizes only unambiguous compatibility cases:

- camel-case metadata keys become their snake-case tool keys;
- `wait` with only `yield_time_ms` treats that value as `wait_time_ms` when valid;
- `poll` carrying `wait_time_ms` expresses terminal-wait intent, becomes `wait`,
  and drops an irrelevant `yield_time_ms`.

Missing `session_id` or `cursor` remains an error. Tool guidance and bash handoff
text include canonical wait and poll argument shapes. TUI rendering exposes
action, process ID, cursor, and wait duration, but never process input. Existing
guardrails remain a last-resort circuit breaker; this change does not tighten
warnings, thresholds, or blockers.

## Verification

Focused tests prove schema/runtime parity, normalization, actionable recovery,
safe rendering, and prompt contracts. The full Python suite, redzone diff gate,
and an actual Mimo TUI long-command scenario prove integration behavior.
