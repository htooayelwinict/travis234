# Phase 112 - Provider Request Auth Headers

## Goal

Port Pi `ModelRegistry.getApiKeyAndHeaders()` request-time auth/header resolution into appv22 without importing Pi modules.

## Reference

- `pi/packages/coding-agent/src/core/model-registry.ts` keeps provider request config and model request headers separately.
- `getApiKeyAndHeaders(model)` resolves stored/provider API keys, provider headers, model-specific headers, and optional `Authorization: Bearer ...` on every request.
- `pi/packages/coding-agent/src/core/resolve-config-value.ts` treats `$ENV` and `${ENV}` as env references, `$$`/`$!` as escapes, `!command` as a shell command, and uppercase strings as literals.
- Pi SDK/session code throws when request auth resolution returns `{ ok: false }` instead of silently calling the provider.

## Regression

Added:

- `test_get_api_key_and_headers_merges_provider_model_and_auth_header`
- `test_get_api_key_and_headers_reports_missing_auth_header_key`
- `test_get_api_key_and_headers_uses_separate_model_request_headers`
- `test_get_api_key_and_headers_resolves_env_templates_and_uppercase_literals`
- `test_get_api_key_and_headers_resolves_command_api_key_on_each_request`
- `test_stream_simple_passes_registry_headers_and_auth_header`

The focused tests first failed because `get_api_key_and_headers` and `register_model_request_headers` did not exist, and `stream_simple()` only propagated an env `api_key`.

## Implementation

- Added `_MODEL_REQUEST_HEADERS` and `register_model_request_headers()` to mirror Pi's separate model request-header map.
- Added `get_api_key_and_headers(model)` returning Pi-shaped `{ ok, apiKey, headers }` / `{ ok: false, error }` results.
- Upgraded appv22 config value resolution to support Pi-style env templates, escapes, shell command config values, missing-env diagnostics, and uncached request-time resolution for throwing paths.
- Changed `stream()` / `stream_simple()` to resolve model auth every request, merge auth headers with explicit options, preserve explicit per-call `api_key`, and raise on auth resolution errors.
- Exported the new registry APIs from `appv22.ai`.
- Changed extension provider model registration to store model-level headers in the request-header map instead of collapsing provider/model headers into `Model.headers`.
- Stopped applying provider headers directly onto current models; provider headers now remain request config like Pi.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_ai_models.py::test_get_api_key_and_headers_merges_provider_model_and_auth_header tests/test_ai_models.py::test_get_api_key_and_headers_reports_missing_auth_header_key tests/test_ai_models.py::test_get_api_key_and_headers_uses_separate_model_request_headers tests/test_ai_models.py::test_get_api_key_and_headers_resolves_env_templates_and_uppercase_literals tests/test_ai_models.py::test_get_api_key_and_headers_resolves_command_api_key_on_each_request tests/test_ai_stream.py::test_stream_simple_passes_registry_headers_and_auth_header
```

Result: `6 passed`.

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_ai_models.py tests/test_ai_stream.py
```

Result: `15 passed`.

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_coding_agent.py
```

Result: `96 passed, 2 failed` due to this macOS environment resolving `python` to the Xcode command-line-tools stub / missing `python` executable. The failures were `test_bash_tool_truncates_tail_and_persists_full_output` and `test_agent_session_extension_command_context_exec_runs_without_session_message`, both unrelated to provider auth/header behavior.

```bash
cd appV2.2 && PYTHONPATH=. pytest tests/test_coding_agent.py -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message'
```

Result: `96 passed, 2 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. python3 -m compileall -q appv22 tests
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. pytest -k 'not bash_tool_truncates_tail_and_persists_full_output and not agent_session_extension_command_context_exec_runs_without_session_message'
```

Result: `252 passed, 2 deselected`.

## Remaining Count

The full goal remains active. Continue with a fresh final audit against Pi/Hermes references before declaring completion. Likely remaining candidates include OAuth callback-server/manual redirect UX details, built-in provider display/default-model behavior, richer provider/extension hooks, runtime-host session switching details, and live compaction/TUI confidence checks.
