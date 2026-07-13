# Travis234 Growth Rules

Travis234 should grow as a composable agent runtime, not as one ever-expanding coding-agent object.

## Stable kernel

Treat these modules as behavior-sensitive:

- `travis/agent/agent.py`
- `travis/agent/agent_loop.py`
- `travis/agent/types.py`
- `travis/ai/types.py`
- `travis/ai/stream.py`
- `travis/ai/validation.py`
- `travis/compaction/compressor.py`
- `travis/compaction/timing.py`

Preserve iteration budgeting, ordered tool results, bounded parallel execution, abort semantics, and compaction behavior. Any behavioral change needs a focused failing regression test and a boundary-level verification test.

## Extension boundaries

- Provider selection and credentials belong in the provider control plane.
- Coding policy belongs under `travis/coding_agent/policies/`.
- Long-running process ownership belongs under `travis/coding_agent/processes/`.
- Terminal rendering and input routing belong under `travis/tui/`.
- Session discovery and persistence must expose bounded, indexed APIs rather than loading all history through callers.
- Product capabilities should be composed through small services and facades instead of adding responsibilities to major session or TUI objects.

## Change discipline

1. Define the public contract with a failing test.
2. Make the smallest implementation change that satisfies it.
3. Refactor only after the focused tests are green.
4. Run neighboring tests and the complete suite.
5. Verify package, launcher, and container behavior when distribution boundaries change.

Never add alternate product names, CLI aliases, state paths, or environment prefixes. Travis234 uses the identity and state contract documented in `README.md`.
