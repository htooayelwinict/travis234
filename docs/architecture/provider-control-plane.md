# appv231 Provider Control Plane

appv231 provider behavior is controlled outside the agent loop.

## Layers

```text
CLI/env/profile input
  -> GenerationParams
  -> ProviderCapabilities
  -> transport payload
  -> provider response normalization
```

## Red-Zone Rule

Provider ergonomics must not require changes to:

- `appV2.3.1/appv231/agent/agent_loop.py`
- `appV2.3.1/appv231/ai/types.py`
- `appV2.3.1/appv231/ai/stream.py`
- `appV2.3.1/appv231/compaction/`
- `appV2.3.1/appv231/coding_agent/session_store.py`

If a provider feature needs those files, it is a kernel change and needs explicit approval.

## Parameter Policy

Direct providers use explicit capability policy. Unsupported user parameters are dropped only with warnings.

Routing aggregators such as OpenRouter can receive provider-routing preferences through the provider payload object.

## Merge Order

```text
provider defaults < .env < process env < profile defaults < CLI flags < TUI session override
```

The current implementation supports:

```text
provider defaults < .env < process env < CLI flags
```

## Testing Rule

Default tests use fake providers, payload snapshots, and monkeypatched HTTP clients. Live provider calls are manual verification only.
