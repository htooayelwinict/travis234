# Phase 048: TUI Compact Alias Routing

Status: complete

## Goal

Fix the live TUI regression where `/compact` was treated as a normal user prompt and sent through the model/tool loop instead of invoking local Hermes manual compaction.

## Reference Files

- `appV2.2/appv22/tui/interactive_mode.py`
- `appV2.2/appv22/compaction/timing.py`
- `pi/packages/coding-agent/src/core/agent-session.ts`

## Changes

- Added a focused TUI regression proving `/compact <focus>` is local and does not call the model provider.
- Added explicit manual compaction command detection for `/compress`, `/compress <focus>`, `/compact`, and `/compact <focus>`.
- Reused the same Hermes manual compaction rendering path for both commands.
- Avoided broad prefix matching so unrelated slash commands like `/compression-status` do not accidentally trigger compaction.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q -k "manual_compress or compact_alias"
```

Result: `2 passed, 19 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q
```

Result: `21 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `170 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check
```

Result: passed.

```bash
rg -n "[ \t]+$" appV2.2/appv22/tui/interactive_mode.py appV2.2/tests/test_tui.py
```

Result: no matches.

## Remaining Count

After this follow-up, the known open audit gaps remain runtime-host session switching, package-manager-backed resource discovery, full skill/prompt-template/theme loading, labels/custom entries, and branch summary generation. The full plan checklist still has 0 unchecked items.
