# AppV2.2 TUI Long Conversation Matrix

Date: 2026-06-18

Scope: live TUI-only agent sessions, Pi-style skill/tool routing, Hermes-style session compaction, and no CLI surface.

## Scenario Results

| Scenario | Workspace | Live TUI user turns | Session result | Compactions | Issues observed | Recovery / fix | Verification |
| --- | --- | ---: | --- | ---: | --- | --- | --- |
| 1. Text metrics helper workflow | `plan/appv22-tui-scenario-1-text-metrics` | 12+ | completed | 2 | Conditional README edit exhausted turn budget twice; duplicate `tests/test_text_metrics-1.py` was created during incremental test edits. | Reopened same TUI session with higher turn budget, completed README edit, then deleted duplicate through TUI. Product fix changed overwrite-denial guidance to preserve existing files instead of steering to sibling files. | `PYTHONPATH=plan/appv22-tui-scenario-1-text-metrics .venv/bin/pytest plan/appv22-tui-scenario-1-text-metrics/tests` -> 6 passed. |
| 2. Cart discount bugfix workflow | `plan/appv22-tui-scenario-2-cart` | 11 | completed | 1 | Initial "fix the bug" request selected inspection but not mutation tools; boundary test was first written to `tests/test_cart-1.py`. | Product fix added `fix` / `bugfix` mutation intent to file-management skill selection and runtime action gating. Duplicate test file was moved/deleted through TUI. | `PYTHONPATH=plan/appv22-tui-scenario-2-cart .venv/bin/pytest plan/appv22-tui-scenario-2-cart/tests` -> 5 passed. |
| 3. Release-notes parser workflow | `plan/appv22-tui-scenario-3-release-notes` | 12 | completed | 2 | First write hit overwrite denial and recovered; docstring turn completed without a visible assistant message; repeated-heading test was added but implementation still reset repeated sections, failing pytest. | TUI recovery turn used pytest failure feedback and fixed `summarize_changes` to aggregate repeated headings. Product overwrite-denial guidance now supports retrying the same path with `overwrite:true` while preserving content. | `PYTHONPATH=plan/appv22-tui-scenario-3-release-notes .venv/bin/pytest plan/appv22-tui-scenario-3-release-notes/tests` -> 6 passed. |

## Product Fixes

- Removed AppV2.2 standalone CLI entrypoints so only TUI/Textual surfaces remain: `appv22_ui/cli.py`, `scripts/appv22_cli.py`.
- Added TUI frame dedupe to prevent background redraw flooding during live sessions.
- Added runtime action-intent gating so no-write / analysis-only requests can finalize after observation even if prior context selected action-capable tools.
- Added `fix` and `bugfix` as file mutation triggers and runtime action markers, matching natural coding prompts.
- Changed file-management overwrite-denial guidance to preserve/update the intended existing file for add/update/edit/fix/patch requests, using sibling paths only when the latest request asks for a separate new file.

## Verification

- `PYTHONPATH=appV2.2 .venv/bin/pytest appV2.2/tests` -> 108 passed.
- Scenario 1 pytest -> 6 passed; only `tests/test_text_metrics.py` remains.
- Scenario 2 pytest -> 5 passed; only `tests/test_cart.py` remains.
- Scenario 3 pytest -> 6 passed; only `tests/test_release_notes.py` remains.
