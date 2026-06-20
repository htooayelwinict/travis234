# Phase 107 - Extension Provider Config Validation

## Goal

Port the next Pi provider/model validation behavior into appv22 without importing Pi modules: dynamic extension provider registration must reject invalid provider configs before mutating model/provider state.

## Reference

- `pi/packages/coding-agent/src/core/model-registry.ts` implements `ModelRegistry.registerProvider()` through `validateProviderConfig()` before applying provider changes.
- Pi rejects `streamSimple` without `api`.
- Pi rejects provider `models[]` without `baseUrl`.
- Pi rejects provider `models[]` without either `apiKey` or `oauth`.
- Pi rejects dynamic models without an `api` at provider or model level.
- `pi/packages/coding-agent/docs/extensions.md` and `docs/custom-provider.md` document `baseUrl`, `apiKey`/`oauth`, `api`, and `streamSimple` requirements.

## Regression

Added `test_agent_session_extension_register_provider_validates_model_auth_config`.

The test first failed because appv22 accepted a dynamic model provider without `baseUrl`:

```text
AssertionError: expected missing baseUrl to be rejected
```

## Implementation

- Added `_validate_extension_provider_config(provider_name, config)` in `AgentSession`.
- Called validation before registering stream handlers, model lists, or active-session provider overrides.
- Rejected `streamSimple` / `stream_simple` unless `api` is supplied.
- Rejected provider `models[]` unless `baseUrl` / `base_url` is supplied.
- Rejected provider `models[]` unless `apiKey` / `api_key` or `oauth` is supplied.
- Rejected model definitions without model-level `api` when the provider has no `api`.
- Updated successful dynamic provider model tests to include `apiKey`, matching Pi's dynamic provider contract.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_agent_session_extension_register_provider_validates_model_auth_config -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -k "provider or set_model or command or extension" -q
```

Result: `39 passed, 57 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `244 passed`.

## Remaining Count

The tracked checklist remains closed, but the full goal is still active. Remaining work should continue with targeted Pi/Hermes parity scans, especially richer extension/provider hooks, provider auth status/OAuth storage surfaces, runtime-host session switching details, and final full-audit proof before the overall goal can be marked complete.
