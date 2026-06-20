# Phase 042: TUI Transcript Ordering and Compaction Visibility

## Goal

Fix issues found during a real app run after Phase 41: assistant/tool output rendered after the status/footer, long generic tool results flooded the terminal, and Hermes compaction had no visible threshold/count or manual `/compress` path in the TUI.

## Changes

- Added regressions for transcript ordering, collapsed long generic tool results, footer context metrics, and manual `/compress` feedback.
- Routed `InteractiveRenderer` output into the interactive history container when `InteractiveMode` is active, keeping assistant/tool output above status/footer.
- Collapsed long generic tool results to 10 lines by default, with existing `set_expanded(True)` support showing the full output.
- Added context token threshold and compaction count fields to `FooterComponent`, prioritizing those metrics before long `cwd` text so they remain visible in narrow terminals.
- Added `/compress [focus]` handling in `InteractiveMode`, wired to `CompactionManager.compress_manual_with_status()`, and rendered Hermes-style feedback into the transcript.
- Added automatic compaction notices after normal turns when compression count increases.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q -k "markdown_input_select or collapses_long_generic or keeps_agent_output or manual_compress"
```

Result: `4 passed, 13 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q
```

Result: `17 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_app_integration.py -q
```

Result: `4 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py tests/test_compaction_timing.py -q
```

Result: `36 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `160 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check
```

Result: passed.

## Remaining Count

After this follow-up, 0 plan checklist items remain open.
