# Phase 113 - Provider Display Names

## Goal

Port Pi `ModelRegistry.getProviderDisplayName()` behavior into appv22 so registry consumers and TUI auth flows use the same provider labels.

## Reference

- `pi/packages/coding-agent/src/core/model-registry.ts` resolves provider display names with this precedence: registered provider `name`, registered provider `oauth.name`, OAuth provider metadata name, built-in provider display name, then raw provider id.
- `pi/packages/coding-agent/src/core/provider-display-names.ts` defines built-in labels such as `OpenAI`, `Google Gemini`, `OpenRouter`, and provider-family names.
- Pi tests also verify `github-copilot` displays as `GitHub Copilot` through built-in OAuth provider metadata.

## Regression

Added:

- `test_get_provider_display_name_resolves_registered_oauth_built_in_and_fallback`
- Updated `test_interactive_mode_login_api_key_is_local_tui_command` to register provider `name: "Proxy AI"` and expect API-key login/logout status messages to use that display name.

The focused test first failed because `get_provider_display_name` did not exist in appv22's model registry.

## Implementation

- Added a registry-level `get_provider_display_name(provider)` function.
- Added Pi's built-in provider display-name table to appv22.
- Added `github-copilot` built-in OAuth display metadata for Pi's observable display-name behavior.
- Preserved registered provider config metadata so provider `name` can take precedence over OAuth/built-in/fallback labels.
- Exported `get_provider_display_name` from `appv22.ai`.
- Replaced the TUI-local `_provider_display_name()` fallback with the registry resolver.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_ai_models.py::test_get_provider_display_name_resolves_registered_oauth_built_in_and_fallback tests/test_tui.py::test_interactive_mode_login_api_key_is_local_tui_command
```

Result: `2 passed`.

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_ai_models.py tests/test_tui.py -k 'provider_display_name or login_api_key or login_logout_oauth'
```

Result: `3 passed, 56 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_ai_models.py tests/test_tui.py
```

Result: `59 passed`.

```bash
cd appV2.2 && PYTHONPATH=. python3 -m compileall -q appv22 tests
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. pytest -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message'
```

Result: `253 passed, 2 deselected`.

## Remaining Count

The full goal remains active. Provider display-name parity is closed for this slice, but default-model resolver behavior, OAuth callback-server/manual redirect UX, richer provider/extension hooks, runtime-host session switching details, and live compaction/TUI confidence checks remain candidates for a fresh audit.
