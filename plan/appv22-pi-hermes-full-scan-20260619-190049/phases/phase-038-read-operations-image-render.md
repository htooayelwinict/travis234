# Phase 038: Read Operations, Image, Abort, and Compact Render Parity

## Goal

Port the remaining Pi read tool behavior into appv22 without importing reference modules: operation injection, abort checkpoints, image resize omission/vision notices, Pi dimension notes, no-details text reads when untruncated, and compact read rendering for skills/resources/docs.

## Reference Files

- `pi/packages/coding-agent/src/core/tools/read.ts`
- `pi/packages/coding-agent/src/utils/image-resize.ts`
- `pi/packages/coding-agent/test/image-resize-callers.test.ts`
- `pi/packages/coding-agent/test/tool-execution-component.test.ts`

## Changes

- Added regressions for `ReadOperations` call ordering and virtual files.
- Added abort coverage after access and before image/text reads continue.
- Ported `ReadOperations`, `ReadImageResizeResult`, `auto_resize_images`, and injected `image_resizer`.
- Added resize failure behavior that returns text-only image omission output.
- Added non-vision model image notes while preserving image attachments when resize succeeds.
- Matched Pi's resized-image dimension note wording and coordinate scale.
- Matched Pi's `details=None` behavior for untruncated text reads.
- Added compact render classification for `SKILL.md`, `AGENTS.md`/`CLAUDE.md`, `README.md`, `docs/`, and `examples/`.
- Added compact line-range formatting and collapsed read-result hiding for non-error reads.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "read_tool_uses_operations or read_tool_checks_abort or read_tool_omits_unresizable or read_tool_resized_images or read_tool_compact_render"
```

Result: `5 passed, 31 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `36 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `146 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check
```

Result: passed.

## Remaining Count

After this phase, 5 plan checklist items remain open.
