# Phase 073: Extension Flags

## Goal

Port Pi coding-agent extension flag registration into appv22 so extensions can register boolean/string flags, defaults are retained, duplicate names keep the first registration, and runtime flag values can be read and overridden.

## Reference

- `pi/packages/coding-agent/src/core/extensions/types.ts`
- `pi/packages/coding-agent/src/core/extensions/loader.ts`
- `pi/packages/coding-agent/src/core/extensions/runner.ts`
- `pi/packages/coding-agent/test/extensions-runner.test.ts`

Pi exposes `registerFlag()`, `getFlag()`, `getFlags()`, `setFlagValue()`, and `getFlagValues()`. Defaults are installed into shared runtime values, and the first extension to register a duplicate flag name wins.

## Changes

- Added `ExtensionFlag`.
- Added `ExtensionRunner.register_flag()` / `registerFlag()`.
- Added `get_flags()` / `getFlags()`, `get_flag()` / `getFlag()`, `set_flag_value()` / `setFlagValue()`, and `get_flag_values()` / `getFlagValues()`.
- Exported `ExtensionFlag` from `appv22.coding_agent`.
- Added a regression for default values, duplicate registration, explicit value override, and missing flag reads.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_extension_runner_flag_registration_defaults_and_values -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "flag_registration or lifecycle_handlers or extension_command or provider_extension_hooks or tool_call_extension or tool_result_extension or context_extension or before_agent_start"
```

Result: `12 passed, 69 deselected`.
