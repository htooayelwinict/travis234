# Phase 101 - TUI Extension Footer

## Goal

Port the next Pi extension UI hook into appv22's TUI without importing Pi modules: extension shortcut contexts must expose `ui.setFooter(factory?)`, replace the built-in footer with an extension component, pass a footer data provider, dispose prior custom footers, and restore the built-in footer when cleared.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` defines `ExtensionUIContext.setFooter(factory?)`.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` removes the current footer, disposes a previous custom footer, creates the custom footer with `(tui, theme, footerDataProvider)`, and restores the built-in footer when cleared.
- `pi/packages/coding-agent/src/core/footer-data-provider.ts` exposes the readonly footer provider methods `getGitBranch`, `getExtensionStatuses`, `getAvailableProviderCount`, and `onBranchChange`.
- `pi/packages/coding-agent/docs/tui.md` documents custom footers reading `footerData` and returning disposable components.

## Regression

Added `test_interactive_mode_extension_shortcut_can_replace_and_restore_footer`.

The test first failed because appv22's extension shortcut UI did not expose `setFooter()` and the built-in footer stayed rendered:

```text
AssertionError: assert 'custom footer: plan=ready' in rendered
```

## Implementation

- Added a replaceable `footer_container` around the built-in `FooterComponent`.
- Added `InteractiveMode.set_extension_footer(factory=None)`.
- Added `_ExtensionShortcutUI.setFooter()` / `set_footer()`.
- Added `_ExtensionFooterDataProvider`, exposing Pi-shaped `getExtensionStatuses()`, `getGitBranch()`, `getAvailableProviderCount()`, and `onBranchChange()` plus snake_case aliases.
- Custom footer factories receive `(tui, theme, footerData)` and can return normal appv22 components or component-like objects with `render()`.
- Existing custom footers are disposed before replacement or restoration.
- Clearing with `None` restores the built-in footer while preserving extension statuses for the default footer.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_extension_shortcut_can_replace_and_restore_footer -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -k "extension_shortcut" -q
```

Result: `13 passed, 30 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -k "footer or real_prompt_loop or keeps_agent_output_above_status_footer" -q
```

Result: `7 passed, 36 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `238 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially remaining extension UI hooks (`setHeader`, editor/custom component hooks), provider auth/model validation, and final full-audit proof before the overall goal can be marked complete.
