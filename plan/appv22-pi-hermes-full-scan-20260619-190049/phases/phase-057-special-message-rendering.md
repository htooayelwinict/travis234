# Phase 057: Special Message Rendering

Status: complete

## Goal

Port Pi interactive rendering for branch summaries, compaction summaries, and extension custom messages, and align session reload so compaction entries rebuild into message history.

## Reference Files

- `pi/packages/coding-agent/src/core/messages.ts`
- `pi/packages/coding-agent/src/core/session-manager.ts`
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts`
- `pi/packages/coding-agent/src/modes/interactive/components/branch-summary-message.ts`
- `pi/packages/coding-agent/src/modes/interactive/components/compaction-summary-message.ts`
- `pi/packages/coding-agent/src/modes/interactive/components/custom-message.ts`

## Changes

- Added collapsed/expanded `BranchSummaryMessageComponent`.
- Added collapsed/expanded `CompactionSummaryMessageComponent` with formatted `tokensBefore` output.
- Added `CustomMessageComponent` with default markdown rendering and optional custom renderer fallback.
- Added `message_to_component()` to map existing Pi-style coding-agent messages into TUI components.
- Updated `InteractiveMode` to render existing session messages during initial TUI setup.
- Added `CompactionSummaryMessage` to the session store and rebuilt compaction entries before kept messages, matching Pi's session-manager ordering.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py tests/test_tui.py -q -k "compaction_summary_message or special_message_components or existing_special_messages"
```

Result: `3 passed, 79 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py tests/test_coding_agent.py -q
```

Result: `82 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `182 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check -- appV2.2/appv22 appV2.2/tests plan/appv22-pi-hermes-full-scan-20260619-190049
```

Result: passed.

## Remaining Count

Known tracked implementation gaps from this plan remain closed. Strict full-source parity is still broader than the compact appv22 runtime; future work should continue with targeted regressions for newly identified provider, auth, export, RPC, CLI, platform, or richer UI surfaces.
