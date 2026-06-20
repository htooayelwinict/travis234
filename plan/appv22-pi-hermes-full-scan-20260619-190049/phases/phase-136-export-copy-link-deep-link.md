# Phase 136: Pi Export Copy Link and Deep Link

## Goal

Continue the Pi export-html browser UI port by adding copy-link and deep-link navigation behavior for exported session entries.

This phase also fixes an extension runner action-binding regression exposed by the broad suite after the export work.

## Reference

- `pi/packages/coding-agent/src/core/export-html/template.js`
  - `buildShareUrl(entryId)`
  - `copyToClipboard(text, button)`
  - `renderCopyLinkButton(entryId)`
  - `navigateTo(targetId, scrollMode, scrollToEntryId)`
  - initial `leafId`/`targetId` deep-link handling
- `pi/packages/coding-agent/src/core/extensions/runner.ts`
  - action surface binding for session-name, message, custom-entry, label, and wait actions

## Red Tests

Added `test_agent_session_export_to_html_wires_copy_links_and_deep_links` in `appV2.2/tests/test_coding_agent.py`.

Initial run:

```bash
PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html_wires_copy_links_and_deep_links' -q
```

Result: failed as expected because appv22 parsed `targetId` but did not provide:

- `buildShareUrl(entryId)`;
- clipboard copy/fallback behavior;
- `renderCopyLinkButton(entryId)`;
- stable `entry-*` DOM IDs;
- copy button event binding;
- target scroll/highlight behavior;
- initial `navigateTo(leafId, 'target', urlTargetId)` handling.

## Implementation

- Added copy-link button CSS and repaired the scoped export CSS message block.
- Added browser-side `buildShareUrl(entryId)` with Pi-style `leafId` and `targetId` parameters.
- Added `copyToClipboard(text, button)` with `navigator.clipboard` and `document.execCommand('copy')` fallback.
- Added `renderCopyLinkButton(entryId)` and rendered copy buttons on exported message/custom/compaction/branch-summary sections.
- Added stable `id="entry-..."` anchors for rendered entries.
- Added `navigateTo(targetId, scrollMode, scrollToEntryId)` and initial deep-link routing.
- Updated tree node clicks to navigate and scroll to the clicked entry.

## Extension Binding Fix

The broad coding-agent suite exposed `test_agent_session_binds_pi_extension_runner_action_surface` failing because `AgentSession._bind_extension_core()` only bound tool/model/thinking actions into `ExtensionRunner.bind_core()`. Session name, custom message, custom entry, label, and wait actions remained no-op defaults.

Fixed the root cause by binding:

- `sendMessage`;
- `sendUserMessage`;
- `appendEntry`;
- `setSessionName`;
- `getSessionName`;
- `setLabel`;
- `waitForIdle`.

## Verification

- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html_wires_copy_links_and_deep_links' -q` -> `1 passed, 124 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py::test_agent_session_binds_pi_extension_runner_action_surface -q` -> `1 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k '(extension_runner or binds_pi_extension_runner_action_surface or extension_command or bind_extensions) and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `25 passed, 100 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'export_to_html or export_html_from_file or export_to_jsonl' -q` -> `8 passed, 117 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_cli.py -q` -> `8 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_compaction.py appV2.2/tests/test_compaction_timing.py -q` -> `36 passed`
- `PYTHONPATH=appV2.2 python3 -m compileall -q appV2.2/appv22 appV2.2/tests` -> passed
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `123 passed, 2 deselected`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests/test_tui.py -q` -> `50 passed`
- `PYTHONPATH=appV2.2 pytest appV2.2/tests -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message' -q` -> `296 passed, 2 deselected`
- `git diff --check -- appV2.2/appv22/coding_agent/export_html.py appV2.2/appv22/coding_agent/agent_session.py appV2.2/tests/test_coding_agent.py appV2.2/appv22/coding_agent/export_html_assets/vendor/marked.min.js appV2.2/appv22/coding_agent/export_html_assets/vendor/highlight.min.js plan/appv22-pi-hermes-full-scan-20260619-190049/plan.md` -> passed

## Compaction Note

No compaction runtime code was changed in this phase. The protected Hermes-style compaction suites remained green (`36 passed`) after the export deep-link and extension binding changes.

## Remaining Gaps

- Full Pi `template.css` and `template.js` parity remains incomplete.
- Rich tool result rendering, image modal behavior, sidebar resizing, JSONL download, header statistics, and keyboard shortcuts need additional export UI parity slices.
- The full appv22 Pi/Hermes objective remains active and unproven.
