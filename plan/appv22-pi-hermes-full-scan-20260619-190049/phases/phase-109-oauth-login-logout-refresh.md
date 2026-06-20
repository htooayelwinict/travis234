# Phase 109 - OAuth Login Logout Refresh Lifecycle

## Goal

Port the next Pi AuthStorage/OAuth lifecycle slice into appv22 without importing Pi modules: registered dynamic OAuth providers must support login, logout, and expired-token refresh during API-key resolution.

## Reference

- `pi/packages/coding-agent/src/core/auth-storage.ts` implements `login(providerId, callbacks)`, `logout(provider)`, OAuth refresh under `getApiKey()`, and error recording for failed refresh.
- `pi/packages/ai/src/utils/oauth/index.ts` defines provider registry behavior and `getOAuthApiKey()` refresh semantics.
- `pi/packages/ai/src/utils/oauth/types.ts` defines the OAuth provider shape: `login(callbacks)`, `refreshToken(credentials)`, and `getApiKey(credentials)`.

## Regression

Added `test_agent_session_extension_provider_oauth_login_logout_and_refresh`.

The test first failed because appv22 had no login lifecycle API:

```text
AttributeError: module 'appv22.ai.models' has no attribute 'login_oauth_provider'. Did you mean: 'get_oauth_providers'?
```

## Implementation

- Added `login_oauth_provider(provider, callbacks)` to invoke a registered OAuth provider's login callback and store returned credentials as `{type: "oauth", ...credentials}`.
- Added `logout_provider(provider)` to remove stored credentials without unregistering the provider metadata.
- Added expired OAuth refresh in `get_api_key_for_provider()`: `refreshToken` / `refresh_token` is called when `expires` is in the past, refreshed credentials replace the stored credential, and `getApiKey` / `get_api_key` derives the returned key.
- Added awaitable-settling support for OAuth provider callbacks, matching appv22's existing extension callback pattern.
- Added `drain_auth_errors()` for Pi-style non-crashing auth error collection when OAuth refresh fails.
- Exported the new helpers from `appv22.ai`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_provider_oauth_login_logout_and_refresh -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_provider_auth_status_tracks_api_key_and_oauth tests/test_coding_agent.py::test_agent_session_extension_provider_oauth_login_logout_and_refresh -q
```

Result: `2 passed`.

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

Result: `246 passed`.

## Remaining Count

The tracked checklist remains largely closed, but the full goal is still active. Remaining work should continue with a final appv22-vs-Pi/Hermes audit and any newly discovered gaps, especially richer extension/provider hooks, OAuth login UI/callback details, runtime-host session switching details, and final live compaction/TUI confidence checks.
