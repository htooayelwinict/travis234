# Travis234 Provider Control Plane

Provider selection and credentials are controlled outside the generic agent loop.

The control plane owns provider registration, model discovery, authentication storage, runtime model selection, and provider-specific transport configuration. The generic loop receives only the selected model, provider stream, messages, tools, and generation options.

Provider, model, base URL, credential, and context-window metadata form one runtime binding. Credential lookup is keyed by the selected model's provider on every request; a model switch never inherits the previous provider's key. Travis234 keeps this invariant inside its own registry and transport design.

This boundary keeps provider behavior out of these behavior-sensitive modules:

- `travis/agent/agent_loop.py`
- `travis/ai/types.py`
- `travis/ai/stream.py`
- `travis/compaction/`
- `travis/coding_agent/session_store.py`

Provider state must be changed through public registry operations. The control plane must not mutate registry-private collections or retain process-global ownership that bypasses the active application instance.
