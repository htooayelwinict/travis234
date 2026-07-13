# appv231 Provider Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give SDK, CLI, AgentSession, and TUI one authoritative model/provider/authentication service with correct provider wire contracts, cancellation, and complete tool-schema validation.

**Architecture:** `CodingApp` owns an injected `ProviderControlPlane` composed from auth, model, API-provider, and capability registries. Registrations are source-owned and disposable. Provider profiles declare authentication and transport capability; only implemented contracts are selectable and advertised.

**Tech Stack:** Python 3.13, httpx, JSON Schema Draft 2020-12, threading locks, atomic file replacement, pytest fake-HTTP transports.

## Global Constraints

- Complete Plans 1-3 first.
- Do not edit compaction files or perform mutating git operations; read-only status and diff checks are permitted.
- Do not print API keys, OAuth tokens, request authorization headers, or `.env` contents.
- Remove duplicated authorities rather than synchronizing multiple mutable copies.
- Catalog entries without implemented transports are not selectable.
- Provider settings must affect runtime behavior or be removed.

---

### Task 1: Standards-Compliant Tool Schema Validation

**Files:**
- Modify: `appV2.3.1/pyproject.toml`
- Modify: `pyproject.toml`
- Replace internals: `appV2.3.1/appv231/ai/validation.py`
- Modify: `appV2.3.1/appv231/agent/types.py:53-63`
- Extend: `appV2.3.1/tests/test_ai_validation.py`
- Modify: `appV2.3.1/tests/test_pyproject_dependencies.py`

**Interfaces:**
- Produces: `compile_tool_schema(schema: Mapping[str, object]) -> CompiledToolSchema`
- Preserves: `validate_tool_arguments(tool, tool_call) -> dict[str, object]`
- Adds: tool registration rejects invalid schemas before a model turn

- [ ] **Step 1: Write missing-keyword regressions**

```python
@pytest.mark.parametrize(
    ("schema", "arguments"),
    [
        ({"type": "string", "enum": ["staging"]}, "production"),
        ({"type": "integer", "minimum": 1}, -5),
        ({"type": "string", "pattern": "^[a-z]+$"}, "NOT-LOWER"),
        ({"const": "safe"}, "unsafe"),
        ({"type": "object", "additionalProperties": {"type": "integer"}}, {"x": "bad"}),
    ],
)
def test_complete_json_schema_constraints_are_enforced(schema, arguments):
    tool = tool_with_single_value_schema(schema)
    with pytest.raises(ToolValidationError):
        validate_tool_arguments(tool, call_with_value(arguments))
```

Add a schema-registration test using an invalid regex or invalid `type` declaration and assert a startup-time error names the tool.

- [ ] **Step 2: Verify current subset accepts invalid arguments**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_ai_validation.py -k complete_json_schema
```

Expected before repair: at least `enum` and `minimum` cases do not raise.

- [ ] **Step 3: Add the maintained dependency**

Add `jsonschema>=4.23,<5` to both runtime dependency lists used by local tests and the app package. Update the dependency test to require the same lower and upper bounds.

- [ ] **Step 4: Compile and validate schemas**

```python
from jsonschema.validators import validator_for

def compile_tool_schema(schema: Mapping[str, object]) -> CompiledToolSchema:
    validator_class = validator_for(schema)
    validator_class.check_schema(schema)
    return CompiledToolSchema(validator_class(dict(schema)))
```

Keep the documented coercion phase, then call the compiled validator and sort errors by absolute path. Bound the formatted error to the existing provider error limit.

- [ ] **Step 5: Cache validators on tool definitions**

Compile once when an `AgentTool` is registered or wrapped. Do not compile on every call. Preserve schema data sent to providers.

- [ ] **Step 6: Run validation and provider-replay tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_ai_validation.py appV2.3.1/tests/test_ai_appv2_env_provider.py -k "validation or malformed"
```

Expected: pass.

### Task 2: Transactional Auth Storage and OAuth Refresh

**Files:**
- Modify: `appV2.3.1/appv231/coding_agent/auth_storage.py`
- Create: `appV2.3.1/tests/test_auth_storage_hardening.py`
- Modify: `appV2.3.1/tests/test_coding_agent.py` OAuth expectations

**Interfaces:**
- Produces: `AuthStorageError`
- Produces: `OAuthProvider(get_api_key, refresh_token)` protocol
- Changes: `AuthStorage.set/remove` update memory only after durable storage success
- Changes: `AuthStorage.get_api_key` refreshes expired OAuth credentials once under a per-provider lock

- [ ] **Step 1: Write malformed-storage and refresh regressions**

```python
def test_set_fails_closed_when_auth_file_is_malformed(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text("{malformed", encoding="utf-8")
    storage = AuthStorage.create(path)
    with pytest.raises(AuthStorageError, match="malformed"):
        storage.set("openrouter", {"type": "api_key", "key": "secret"})
    assert storage.get("openrouter") is None
    assert path.read_text(encoding="utf-8") == "{malformed"

def test_expired_oauth_refreshes_once_and_persists(tmp_path):
    provider = CountingOAuthProvider(access="fresh", expires=future_ms())
    storage = auth_storage_with_expired_credential(tmp_path, provider)
    assert storage.get_api_key("example") == "fresh"
    assert storage.get_api_key("example") == "fresh"
    assert provider.refresh_calls == 1
    assert read_auth_json(tmp_path)["example"]["access"] == "fresh"
```

- [ ] **Step 2: Verify current memory-only behavior**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_auth_storage_hardening.py
```

Expected before repair: malformed-file mutation appears in memory or expired OAuth returns the stale token.

- [ ] **Step 3: Make storage transactions authoritative**

Use a sibling lock file for `flock`, parse under the lock, write JSON to a mode-`0600` temporary file, flush/fsync it, `os.replace()` it over the auth file, and fsync the parent directory. Return the committed merged mapping from the transaction and only then assign `self._data`.

- [ ] **Step 4: Add per-provider OAuth refresh locking**

```python
with self._oauth_locks.setdefault(provider, threading.Lock()):
    credential = self.get(provider)
    if not oauth_is_expired(credential):
        return provider_impl.get_api_key(credential)
    refreshed = settle(provider_impl.refresh_token(credential))
    self.set(provider, {"type": "oauth", **refreshed})
    return provider_impl.get_api_key(self.get(provider))
```

The canonical awaitable control-plane API added in Task 3 must await async refresh callbacks; the sync method uses its sync facade.

- [ ] **Step 5: Normalize status semantics**

`get_auth_status()` returns `configured=True` for stored, runtime, environment, and explicit provider-config credentials, with a separate `source` field. Ensure `has_auth()` and status cannot contradict each other.

- [ ] **Step 6: Run auth tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_auth_storage_hardening.py appV2.3.1/tests/test_coding_agent.py -k "auth or oauth or provider_status"
```

Expected: pass without logging credentials.

### Task 3: One Injected Provider Control Plane

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/provider_control_plane.py`
- Modify: `appV2.3.1/appv231/ai/stream.py`
- Modify: `appV2.3.1/appv231/coding_agent/model_registry.py`
- Modify: `appV2.3.1/appv231/app.py:75-142`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py:390-450,990-1185`
- Modify: `appV2.3.1/appv231/cli.py:31-205,400-510`
- Create: `appV2.3.1/tests/test_provider_control_plane.py`

**Interfaces:**
- Produces: `ApiProviderRegistry.register(provider, source_id) -> ProviderRegistration`
- Produces: `ProviderControlPlane(auth, models, api_providers, capabilities)`
- Produces: `ProviderControlPlane.create_default(paths, environment) -> ProviderControlPlane`
- Consumed by: `CodingApp`, `AgentSession`, CLI, and later TUI

- [ ] **Step 1: Write identity and isolation tests**

```python
def test_app_session_cli_services_share_one_control_plane(tmp_path):
    control = ProviderControlPlane.in_memory()
    app = CodingApp(cwd=str(tmp_path), model=model(), provider_control_plane=control)
    assert app.provider_control_plane is control
    assert app.session.provider_control_plane is control
    assert app.session.model_registry is control.models
    assert app.session.auth_storage is control.auth

def test_two_in_memory_control_planes_do_not_leak_registrations():
    left = ProviderControlPlane.in_memory()
    right = ProviderControlPlane.in_memory()
    left.api_providers.register(fake_provider("private"), source_id="test")
    assert left.api_providers.get("private") is not None
    assert right.api_providers.get("private") is None

def test_repeated_refresh_does_not_chain_fallback_resolvers():
    control = ProviderControlPlane.in_memory()
    for _ in range(20):
        control.refresh()
    control.auth.get_api_key("custom")
    assert control.fallback_resolution_count("custom") == 1
```

- [ ] **Step 2: Verify globals currently leak between sessions**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_provider_control_plane.py -k control_plane
```

Expected before implementation: missing injection API or global registration visible in both sessions.

- [ ] **Step 3: Introduce instance registries**

Move storage currently held by module globals in `ai.stream` behind `ApiProviderRegistry`. Keep top-level convenience functions only as a temporary adapter to a default control plane during migration; remove internal callers before this plan's gate.

- [ ] **Step 4: Inject through construction roots**

Add `provider_control_plane` parameters to `CodingApp` and `AgentSession`. CLI creates exactly one default instance after dotenv/environment setup and passes it through model resolution and app construction. Delete `_CliModelRegistry` and `_SessionModelRegistry` after callers migrate.

- [ ] **Step 5: Run app, CLI, and isolation tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_provider_control_plane.py appV2.3.1/tests/test_cli.py appV2.3.1/tests/test_app_integration.py
```

Expected: pass.

### Task 4: Source-Owned Provider Registrations

**Files:**
- Modify: `appV2.3.1/appv231/coding_agent/provider_control_plane.py`
- Modify: `appV2.3.1/appv231/coding_agent/model_registry.py:350-410`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py:3100-3205`
- Extend: `appV2.3.1/tests/test_provider_control_plane.py`
- Modify extension-provider tests in: `appV2.3.1/tests/test_coding_agent.py`

**Interfaces:**
- Produces: `ProviderRegistration.close() -> None`, idempotent
- Produces: `ProviderControlPlane.register_extension(source_id, provider_config) -> ProviderRegistration`
- Guarantees: exact source ownership for API transport, models, auth config, and request headers

- [ ] **Step 1: Write unload and restoration regressions**

```python
def test_extension_close_removes_every_owned_surface_and_restores_previous_provider():
    control = ProviderControlPlane.in_memory()
    original = control.register_extension("base", provider_config(api="same", model="base"))
    override = control.register_extension("plugin", provider_config(api="plug-api", model="plugin"))
    assert control.api_providers.get("plug-api") is not None
    override.close()
    assert control.api_providers.get("plug-api") is None
    assert control.models.find("same", "base") is not None
    override.close()
    original.close()
```

- [ ] **Step 2: Verify leaked API provider**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_provider_control_plane.py -k extension_close
```

Expected before repair: the extension API provider remains registered.

- [ ] **Step 3: Implement ownership stacks**

Each registry stores entries by logical key and source ID. Closing a source removes its entries and reveals the previous source's entry where one existed. Do not capture previous resolvers in nested lambdas.

- [ ] **Step 4: Route AgentSession extension APIs through one handle**

Replace manual `_extension_provider_original_*` maps with stored registration handles. Session teardown closes all handles.

- [ ] **Step 5: Run extension lifecycle tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_provider_control_plane.py appV2.3.1/tests/test_coding_agent.py -k "extension_provider or unregister_provider"
```

Expected: pass.

### Task 5: Unified Model Eligibility and Selection

**Files:**
- Modify: `appV2.3.1/appv231/coding_agent/model_registry.py:220-272`
- Modify: `appV2.3.1/appv231/ai/model_resolver.py:179-353`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py:3090-3155`
- Modify: `appV2.3.1/appv231/cli.py`
- Modify: `appV2.3.1/appv231/tui/interactive_mode.py:596-606`
- Extend: `appV2.3.1/tests/test_provider_control_plane.py`
- Extend: `appV2.3.1/tests/test_ai_model_resolver.py`
- Modify: `appV2.3.1/tests/test_coding_agent.py` cycling tests

**Interfaces:**
- Produces: `ModelRegistry.is_selectable(model: Model) -> bool`
- Produces: `ModelRegistry.get_selectable(active: Model | None = None) -> list[Model]`
- Guarantees: startup, saved default, scope, cycling, and picker use the same eligibility rule

- [ ] **Step 1: Write saved-default and cycling regressions**

```python
def test_saved_default_without_auth_falls_back_to_selectable_model():
    registry = registry_with(
        model("saved", authenticated=False),
        model("ready", authenticated=True),
    )
    result = find_initial_model(
        scoped_models=[],
        is_continuing=False,
        model_registry=registry,
        default_provider="test",
        default_model_id="saved",
    )
    assert result.model.id == "ready"

def test_cycle_never_selects_unauthenticated_model(session):
    session.set_model(model("ready", authenticated=True))
    assert session.cycle_model().model.id != "no-auth"
```

- [ ] **Step 2: Verify current unauthenticated selection**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_ai_model_resolver.py appV2.3.1/tests/test_provider_control_plane.py -k "saved_default or cycle_never"
```

Expected before repair: saved or cycled model is unauthenticated.

- [ ] **Step 3: Centralize eligibility**

`is_selectable` requires an implemented provider capability, a valid model entry, and satisfaction of the profile's authentication requirement. API-key/OAuth profiles consult `AuthStorage.has_auth(provider)`; local or explicitly auth-free profiles are selectable without credentials. `get_selectable(active)` may include an unavailable active model only as a flagged diagnostic row, never as a cycle target.

Add a regression proving an implemented auth-free local provider remains selectable.

- [ ] **Step 4: Remove resolver fallbacks that bypass eligibility**

Use `get_selectable()` in `find_initial_model`, CLI resolution, scoped model resolution, and `AgentSession.cycle_model`. Return a bounded fallback message when a configured default is skipped.

- [ ] **Step 5: Run model tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_ai_model_resolver.py appV2.3.1/tests/test_provider_control_plane.py appV2.3.1/tests/test_cli.py -k model
```

Expected: pass.

### Task 6: Provider Wire Contracts, Capability Filtering, and Cancellation

**Files:**
- Modify: `appV2.3.1/appv231/ai/providers/base.py`
- Modify: `appV2.3.1/appv231/ai/providers/catalog.py`
- Modify: `appV2.3.1/appv231/ai/providers/transports.py`
- Modify: `appV2.3.1/appv231/ai/providers/appv2_env.py:1962-2082`
- Modify: `appV2.3.1/appv231/ai/env_config.py`
- Modify: `appV2.3.1/appv231/agent/types.py:28-41`
- Extend: `appV2.3.1/tests/test_ai_provider_catalog.py`
- Extend: `appV2.3.1/tests/test_ai_appv2_env_provider.py`
- Extend: `appV2.3.1/tests/test_ai_env_config.py`
- Extend: `appV2.3.1/tests/test_provider_control_plane.py`

**Interfaces:**
- Produces: `ProviderProfile.auth_headers(credential: ResolvedCredential) -> dict[str, str]`
- Produces: `ProviderProfile.transport_available: bool`
- Produces: `AbortSignal.add_callback(callback) -> unsubscribe`
- Guarantees: provider worker exits within a bounded interval after abort

- [ ] **Step 1: Add fake-HTTP contract tests**

```python
def test_direct_anthropic_uses_required_headers(fake_http):
    run_provider_request(provider="anthropic", api_key="test-key", fake_http=fake_http)
    request = fake_http.single_request
    assert request.url == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == "test-key"
    assert request.headers["anthropic-version"]
    assert "Authorization" not in request.headers

def test_abort_closes_http_stream_and_exits_worker(blocking_http):
    signal = AbortSignal()
    stream = start_request(signal=signal, transport=blocking_http)
    signal.abort()
    assert blocking_http.closed.wait(timeout=1)
    with pytest.raises(RequestAborted):
        stream.result_sync()
```

Add tests proving Bedrock is absent from selectable catalog output while its transport is `UnsupportedTransport`, and Anthropic env aliases resolve from its profile.

Add capability tests for `tool_choice`, parallel-tool settings, and provider-specific reasoning parameters. A supported parameter must appear in the request payload; an unsupported parameter must produce a structured warning or validation error, never disappear silently.

- [ ] **Step 2: Verify current header and cancellation failures**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_ai_appv2_env_provider.py appV2.3.1/tests/test_ai_provider_catalog.py -k "direct_anthropic or abort_closes or bedrock"
```

Expected before repair: generic Bearer header, live HTTP worker after abort, or unsupported Bedrock advertised.

- [ ] **Step 3: Make authentication provider-specific**

Resolve a credential with kind (`api_key`, `oauth`, or external). Anthropic API keys produce `x-api-key` plus `anthropic-version`; Anthropic OAuth uses its declared Bearer contract. Other profiles explicitly declare Bearer, custom header, no-header, or external SDK auth.

- [ ] **Step 4: Add abort callbacks**

Extend `AbortSignal` with thread-safe callback registration. The HTTP request registers `response.close`/client cancellation and unregisters it in `finally`. The SSE loop also checks `signal.aborted` between chunks and returns `RequestAborted` instead of waiting for a normal terminal event.

- [ ] **Step 5: Filter capabilities and unify env aliases**

`provider_catalog()` excludes profiles whose API mode resolves to `UnsupportedTransport`. `env_config` queries the profile's `env_vars`; remove separately maintained alias lists where they duplicate profile data.

- [ ] **Step 6: Resolve dead settings**

Wire request timeout to `httpx.Timeout`, transport mode to profile resolution, and retry settings to the single existing retry owner. Remove any exposed field that remains unsupported rather than silently accepting it.

- [ ] **Step 7: Run all provider contract tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_ai_appv2_env_provider.py \
  appV2.3.1/tests/test_ai_provider_catalog.py \
  appV2.3.1/tests/test_ai_env_config.py \
  appV2.3.1/tests/test_provider_control_plane.py
```

Expected: pass without network access.

### Task 7: Provider Control-Plane Gate

**Files:**
- Modify: none

**Interfaces:**
- Produces the single provider authority consumed by the TUI plan

- [ ] **Step 1: Prove duplicate service classes are gone**

```bash
rg -n "class _CliModelRegistry|class _SessionModelRegistry|_models_with_active_fallback" appV2.3.1/appv231
```

Expected: no output. Startup, session cycling, and the TUI's local candidate list all delegate to `ModelRegistry.get_selectable()` by this gate; Plan 5 only moves remote loading off the UI thread.

- [ ] **Step 2: Run provider, CLI, app, and schema tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_provider_control_plane.py \
  appV2.3.1/tests/test_auth_storage_hardening.py \
  appV2.3.1/tests/test_ai_model_resolver.py \
  appV2.3.1/tests/test_ai_validation.py \
  appV2.3.1/tests/test_ai_appv2_env_provider.py \
  appV2.3.1/tests/test_cli.py \
  appV2.3.1/tests/test_app_integration.py
```

Expected: pass.

- [ ] **Step 3: Run full suite and redzone check**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests
git diff --exit-code -- appV2.3.1/appv231/compaction
```

Expected: zero failures and no redzone diff.
