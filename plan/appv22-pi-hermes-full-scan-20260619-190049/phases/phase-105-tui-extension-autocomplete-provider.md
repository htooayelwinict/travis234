# Phase 105 - TUI Extension Autocomplete Provider Hook

## Goal

Port the next Pi extension UI hook into appv22's TUI without importing Pi modules: extension shortcut contexts must expose `ui.addAutocompleteProvider(factory)`, the provider factory must wrap the current provider, trigger characters must be retained, command argument completions must survive `registerCommand()`, and the editor must actually apply completions.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts` defines `AutocompleteProviderFactory` and `ExtensionUIContext.addAutocompleteProvider(factory)`.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` stores autocomplete provider wrappers, rebuilds the base provider, aggregates `triggerCharacters`, and installs the provider on the active editor.
- `pi/packages/tui/src/autocomplete.ts` defines provider methods `getSuggestions()`, `applyCompletion()`, optional `shouldTriggerFileCompletion()`, and command `getArgumentCompletions()`.
- `pi/packages/coding-agent/docs/extensions.md` documents custom autocomplete providers layered on top of built-in slash/path completion.

## Regression

Added `test_interactive_mode_extension_shortcut_can_add_autocomplete_provider`.

The test first failed because appv22's TUI had no autocomplete provider surface:

```text
AttributeError: 'InteractiveMode' object has no attribute 'get_autocomplete_suggestions'
```

After the provider stack was added, the expanded command-argument assertion failed because registered commands dropped `getArgumentCompletions`:

```text
AssertionError: assert None == {'items': [{'label': 'staging', 'value': 'staging'}], 'prefix': 'st'}
```

## Implementation

- Added `SimpleAutocompleteProvider`, a small Python equivalent for Pi's combined slash-command provider.
- Added `Input.setAutocompleteProvider()` / `set_autocomplete_provider()`.
- Added Tab handling in `Input.handle_input()` that calls provider `getSuggestions()` and `applyCompletion()` and applies the first suggestion.
- Added `InteractiveMode.create_base_autocomplete_provider()`, `setup_autocomplete_provider()`, `add_autocomplete_provider()`, and `get_autocomplete_suggestions()`.
- Added `_ExtensionShortcutUI.addAutocompleteProvider()` / `add_autocomplete_provider()`.
- Preserved `triggerCharacters` from provider wrappers and reinstalled the provider on the active editor.
- Preserved command-level `getArgumentCompletions` / `get_argument_completions` callbacks in `RegisteredCommand` so slash-command argument suggestions can delegate through the base provider.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_extension_shortcut_can_add_autocomplete_provider -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -k "extension_shortcut or autocomplete or input" -q
```

Result: `18 passed, 29 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -k "register_command or command or extension" -q
```

Result: `36 passed, 59 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `242 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially remaining extension UI hooks (custom components), provider auth/model validation, runtime-host session switching details, and final full-audit proof before the overall goal can be marked complete.
