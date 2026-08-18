# Travis234 Provider Control Plane

Provider selection and credentials are controlled outside the generic agent loop.

The control plane owns provider registration, model discovery, authentication storage, runtime model selection, and provider-specific transport configuration. The generic loop receives only the selected model, provider stream, messages, tools, and generation options.

Provider, model, base URL, credential, and context-window metadata form one runtime binding. Credential lookup is keyed by the selected model's provider on every request; a model switch never inherits the previous provider's key. Travis234 keeps this invariant inside its own registry and transport design.

Authentication is resolved at the final request boundary from both the normalized provider and the selected model's API mode. This matters for mixed-protocol gateways such as OpenCode: a single provider can require bearer auth for an OpenAI route, `x-api-key` for an Anthropic-compatible route, or `x-goog-api-key` for a Google route. Static provider defaults cannot safely decide that header. Custom providers retain the conservative bearer default unless they opt into a known provider contract.

Provider identity is a separate final-header policy. Direct Kimi Code traffic replaces any inherited Kimi CLI user agent with `Travis234/<installed-version>` after transport header finalization and before operator callbacks. Other providers' explicit identities are preserved.

Provider-controlled error bodies are untrusted diagnostic input. Before errors enter agent events, the TUI, or JSONL session history, Travis234 recursively redacts sensitive JSON fields and removes the active request credential, bearer-token values, and recognizable `sk-...` tokens. Redaction happens before output truncation so a reflected secret cannot survive at the truncation boundary.

`/login` is storage, not a billable health check. It confirms where the credential was saved and tells the user that verification occurs on the first provider request. The request boundary owns wire authentication and error redaction for TUI, SDK, print, JSON, and RPC modes alike.

This boundary keeps provider behavior out of these behavior-sensitive modules:

- `travis/agent/agent_loop.py`
- `travis/ai/types.py`
- `travis/ai/stream.py`
- `travis/compaction/`
- `travis/coding_agent/session_store.py`

Provider state must be changed through public registry operations. The control plane must not mutate registry-private collections or retain process-global ownership that bypasses the active application instance.
