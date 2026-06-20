# Phase 140: Pi Export Sidebar Resize and Keyboard Controls

## Goal

Bring appv22's exported HTML browser shell closer to Pi's export UI by porting the resizable sidebar controller, persisted sidebar width, mobile sidebar close behavior, and header keyboard shortcuts.

This phase preserves the existing Hermes-style compaction layers. No compaction runtime code was changed.

## Reference

- `pi/packages/coding-agent/src/core/export-html/template.css`
  - `--sidebar-width`
  - `--sidebar-min-width`
  - `--sidebar-max-width`
  - `--sidebar-resizer-width`
  - `body.sidebar-resizing`
  - responsive sidebar/resizer behavior
- `pi/packages/coding-agent/src/core/export-html/template.js`
  - `SIDEBAR_WIDTH_STORAGE_KEY`
  - `isMobileLayout()`
  - `getSidebarBounds()`
  - `clampSidebarWidth(...)`
  - `applySidebarWidth(...)`
  - `loadSidebarWidth()`
  - `saveSidebarWidth(...)`
  - `setupSidebarResize()`
  - sidebar open/close handlers
  - `T` / `O` / Escape keyboard shortcuts

## Red Test

Added `test_agent_session_export_to_html_wires_sidebar_resize_and_keyboard_shortcuts`.

Initial run:

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_wires_sidebar_resize_and_keyboard_shortcuts -q
```

Result: failed as expected because the exported HTML did not include `--sidebar-width: 400px;`.

## Implementation

- Added Pi-shaped sidebar CSS variables and resize-state styling in the exported HTML shell.
- Added browser-side sidebar controller values and helpers:
  - `sidebar`
  - `overlay`
  - `hamburger`
  - `sidebarResizer`
  - `SIDEBAR_WIDTH_STORAGE_KEY`
  - `MIN_CONTENT_WIDTH`
  - `isMobileLayout()`
  - `getSidebarBounds()`
  - `clampSidebarWidth(width)`
  - `applySidebarWidth(width)`
  - `loadSidebarWidth()`
  - `saveSidebarWidth(width)`
  - `setupSidebarResize()`
- Wired pointer-based sidebar resize with capture, cleanup, persisted width, reset-on-double-click, and viewport resize clamping.
- Wired mobile/sidebar open and close behavior through hamburger, overlay, and sidebar close button.
- Added exported keyboard shortcuts:
  - Escape clears tree search and navigates to the active leaf bottom.
  - `T` toggles thinking blocks.
  - `O` toggles expandable tool/compaction/skill blocks.
  - Editable targets are ignored for `T`/`O`.

## Verification

- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_agent_session_export_to_html_wires_sidebar_resize_and_keyboard_shortcuts -q` -> `1 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html or export_html_from_file or export_to_jsonl' -q` -> `11 passed, 117 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests` -> passed
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_cli.py -q` -> `8 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_tui.py -q` -> `50 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `126 passed, 2 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `299 passed, 2 deselected`
- `git diff --check -- appV2.2/appv22/coding_agent/export_html.py appV2.2/tests/test_coding_agent.py plan/appv22-pi-hermes-full-scan-20260619-190049/plan.md` -> passed
- trailing-whitespace scan over touched tracked/untracked files -> passed

## Compaction Note

No compaction runtime code was changed. The protected Hermes-style compaction suites remained green at `36 passed`.

If a future broad compaction fix is needed, it must stay within Pi/Hermes scope and be backed by dedicated compaction verification.

## Remaining Gaps

- Full Pi export template parity remains incomplete.
- Richer tool rendering and full exported navigation caching still need dedicated parity slices.
- Live TUI ergonomics still need dedicated parity slices.
- The final out-of-scope removal audit is still pending.
- The full appv22 Pi/Hermes objective remains active and unproven.
