# Phase 111 - TUI API Key Login Command

## Goal

Port the next Pi interactive auth flow into appv22 without importing Pi modules: `/login` must expose Pi's auth-type selector and support API-key login as a local TUI command.

## Reference

- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` handles exact `/login` by showing `Use a subscription` / `Use an API key`, then selects the provider for the chosen auth type.
- `showApiKeyLoginDialog()` prompts for the API key, stores `{ type: "api_key", key }`, refreshes model availability, and renders `Saved API key for ...`.
- `showOAuthSelector("logout")` lists stored credentials of either auth type and removes only auth.json credentials, leaving environment variables and model config intact.

## Regression

Added `test_interactive_mode_login_api_key_is_local_tui_command`.

The test first failed because `/login` only knew about OAuth providers, so selecting the API-key flow and entering the API key fell through into model turns:

```text
AssertionError: assert [('model', Context(...)), ...] == []
```

## Implementation

- Added model-registry helpers `get_auth_credential()` and `list_auth_providers()` to expose Pi AuthStorage-like stored credential enumeration without exposing secret values through status APIs.
- Exported the new helpers from `appv22.ai`.
- Changed `/login` handling to show a Pi-style auth-type selector before provider selection.
- Kept subscription login on the OAuth provider path from Phase 110.
- Added API-key provider selection from registered model providers and prompt-based API-key storage through `set_auth_credential(provider, {"type": "api_key", "key": value})`.
- Changed `/logout` selection to list all stored OAuth/API-key credentials and render Pi-style API-key removal text.
- Restricted local auth command parsing to exact `/login` and `/logout`, matching Pi's interactive command interception.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_login_api_key_is_local_tui_command -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_login_logout_oauth_are_local_tui_commands -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -k "login or logout or compact_alias or manual_compress or autocomplete" -q
```

Result: `5 passed, 45 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -k "provider or auth or oauth or model or command or extension" -q
```

Result: `42 passed, 56 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_ai_models.py tests/test_ai_stream.py -q
```

Result: `9 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `248 passed`.

## Remaining Count

The full goal remains active. Continue with a final appv22-vs-Pi/Hermes audit and any newly discovered gaps, especially OAuth callback-server/manual redirect UX details, built-in provider display/default-model behavior, richer extension/provider hooks, runtime-host session switching details, and live compaction/TUI confidence checks.
