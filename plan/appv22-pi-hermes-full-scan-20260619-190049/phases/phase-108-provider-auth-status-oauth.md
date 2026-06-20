# Phase 108 - Provider Auth Status and OAuth Metadata

## Goal

Port the next Pi provider auth surface into appv22 without importing Pi modules: dynamic provider registration must feed a model-registry auth/status layer so provider API keys, OAuth metadata, and stored credentials affect model auth availability and status reporting.

## Reference

- `pi/packages/coding-agent/src/core/auth-storage.ts` defines runtime keys, stored credentials, `hasAuth()`, `getAuthStatus()`, `getApiKey()`, and OAuth provider listing.
- `pi/packages/coding-agent/src/core/model-registry.ts` stores provider request configs, implements `hasConfiguredAuth(model)`, `getProviderAuthStatus(provider)`, `getApiKeyForProvider(provider)`, registers dynamic OAuth provider metadata, and removes provider request metadata on unregister.
- `pi/packages/coding-agent/docs/custom-provider.md` documents `apiKey`, env interpolation, `authHeader`, and OAuth provider registration.

## Regression

Added `test_agent_session_extension_provider_auth_status_tracks_api_key_and_oauth`.

The test first failed because appv22's model registry had no auth-status surface:

```text
AttributeError: module 'appv22.ai.models' has no attribute 'has_configured_auth'
```

## Implementation

- Added an in-memory auth/status layer to `appv22.ai.models`.
- Added `register_provider_auth_config()` and `unregister_provider_auth_config()` for dynamic provider request metadata.
- Added stored/runtime credential helpers: `set_auth_credential()`, `remove_auth_credential()`, `set_runtime_api_key()`, and `remove_runtime_api_key()`.
- Added `has_auth()`, `has_configured_auth(model)`, `get_provider_auth_status(provider)`, `get_api_key_for_provider(provider)`, and `get_oauth_providers()`.
- Implemented Pi-shaped provider-config API key handling for literal keys, `$ENV` / `${ENV}` references, and command-backed key status without executing commands.
- Wired `AgentSession._register_extension_provider()` and `_unregister_extension_provider()` to store/remove request auth config and OAuth provider metadata.
- Exported the new auth/status helpers from `appv22.ai`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_provider_auth_status_tracks_api_key_and_oauth -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -k "provider or auth or model or command or extension" -q
```

Result: `41 passed, 56 deselected`.

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

Result: `245 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially richer extension/provider hooks, deeper OAuth login/logout flows, runtime-host session switching details, and final full-audit proof before the overall goal can be marked complete.
