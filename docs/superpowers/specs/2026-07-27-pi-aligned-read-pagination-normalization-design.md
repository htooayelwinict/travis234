# Pi-Aligned Read Pagination Normalization Design

**Status:** Approved for implementation planning  
**Date:** 2026-07-27

## Problem

The `openrouter/xiaomi/mimo-v2.5-pro` route repeatedly generated `read` calls containing both supported pagination families:

```json
{
  "path": ".../subagent-delegation/SKILL.md",
  "offset": 1,
  "limit": 2000,
  "byte_offset": 0,
  "byte_limit": 50000
}
```

Travis234 correctly rejects mixed line and byte pagination. The model nevertheless repeated the same structured arguments even while its reasoning stated that it would omit one family, preventing it from loading the required skill.

## Pi cross-check

Pi's current coding-agent and Agent Harness read tools expose only:

- `path`
- `offset`
- `limit`

Pi does not expose byte pagination. Travis234's `byte_offset` and `byte_limit` fields are a local extension used for virtual artifact reads and oversized single-line content.

The correction must therefore preserve Pi's line-oriented behavior for ordinary filesystem reads while retaining Travis234's artifact capability.

## Considered approaches

### Target-aware argument preparation

Normalize a mixed call before schema validation and execution:

- ordinary filesystem target: remove `byte_offset` and `byte_limit`;
- registered virtual artifact: remove `offset` and `limit`.

This is the selected approach. It is provider-neutral, deterministic from the target owner, and preserves both Pi behavior and Travis234's artifact extension.

### JSON Schema union

Express the two modes through `oneOf` or nested exclusions. This accurately documents the contract but does not guarantee that OpenRouter or MiMo will honor the union while generating tool arguments. Provider support for complex tool schemas also varies.

This approach is rejected for this correction.

### Provider-specific repair

Rewrite MiMo or OpenRouter tool calls in the provider layer. This would couple generic file-tool behavior to one route and leave other models vulnerable to the same malformed combination.

This approach is rejected.

## Architecture and data flow

`travis/coding_agent/tools/read.py` remains the sole production owner.

1. `create_read_tool_definition()` installs a private argument-preparation callback.
2. The callback returns the original mapping unchanged unless both pagination families are present.
3. For a mixed call, the callback asks the existing `ArtifactRegistry` whether `path` names a registered virtual artifact.
4. For an artifact, it copies the arguments and removes line pagination.
5. For an ordinary path, it copies the arguments and removes byte pagination.
6. The existing agent-loop preparation and validation pipeline receives the normalized mapping.
7. `_execute_read()` retains its current mixed-mode rejection as defense-in-depth for direct execution and extension misuse.

No file is opened during preparation. Missing paths proceed through the existing execution error path.

## Scope boundaries

Production changes are limited to:

- `travis/coding_agent/tools/read.py`

Regression changes are limited to:

- `tests/test_coding_tools_and_subagents.py`

The correction must not change:

- `travis/agent/**`;
- `travis/compaction/**`;
- provider adapters;
- iteration budgeting or tool ordering;
- read truncation limits;
- ordinary line pagination;
- virtual artifact byte pagination;
- image reads;
- persistence formats.

## Error handling

- Non-mapping or otherwise invalid arguments remain subject to existing validation.
- Calls using only one pagination family remain byte-for-byte unchanged.
- Mixed ordinary-file calls use Pi-compatible line pagination.
- Mixed artifact calls use Travis234 byte pagination.
- Direct execution with unresolved mixed pagination continues to raise the current error.

## Regression strategy

Add a locally failing regression using MiMo's exact four-field payload for an ordinary `SKILL.md` file. The prepared arguments must contain only `path`, `offset`, and `limit`, and execution must return the requested content.

Add a second regression using the same mixed shape for a registered virtual artifact. The prepared arguments must contain only `path`, `byte_offset`, and `byte_limit`, and execution must return the requested byte range.

Retain the existing regression proving that direct mixed-mode execution is rejected.

Focused verification:

```text
.venv/bin/pytest -q tests/test_coding_tools_and_subagents.py -k "read and pagination"
```

Repository qualification must repeat the Python suite, launcher tests, package builds, hygiene checks, red-zone diff, and release-container smoke required by repository guidance.

## Acceptance criteria

- MiMo's observed mixed payload no longer loops on an ordinary skill file.
- Ordinary reads retain Pi's line-pagination semantics.
- Virtual artifacts retain Travis234's byte-pagination semantics.
- Single-mode calls are not rewritten.
- The direct executor still rejects unresolved mixed pagination.
- No production file in either red zone changes.
- Focused and repository-level qualification pass.
