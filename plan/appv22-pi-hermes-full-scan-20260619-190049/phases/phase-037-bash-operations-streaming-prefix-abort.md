# Phase 037: Bash Operations, Streaming, Prefix, and Abort Parity

## Goal

Port the remaining Pi bash tool behavior into appv22 without importing reference modules: operation injection, local streaming execution, command prefixing, spawn hooks, partial updates, output accumulation, timeout formatting, and abort process-tree handling.

## Reference Files

- `pi/packages/coding-agent/src/core/tools/bash.ts`
- `pi/packages/coding-agent/src/core/tools/output-accumulator.ts`
- `pi/packages/coding-agent/test/tools.test.ts`
- `pi/packages/coding-agent/test/tool-execution-component.test.ts`
- `pi/packages/coding-agent/test/suite/agent-session-bash-persistence.test.ts`

## Changes

- Added regressions for `BashOperations`, command prefixing, spawn hook rewriting, initial partial updates, streaming output updates, timeout/abort error output preservation, local operation env streaming, and local abort behavior.
- Replaced the blocking `subprocess.run()` implementation with Pi-style `BashOperations`.
- Added `BashExecOptions`, `BashSpawnContext`, and `create_local_bash_operations()`.
- Added a Python `OutputAccumulator` equivalent for streaming UTF-8 decoding, tail snapshots, and full-output temp-file persistence.
- Added command prefix and spawn hook resolution before execution.
- Added local shell streaming via stdout/stderr reader threads.
- Added process-group termination on abort/timeout for local operations.
- Switched bash truncation details to Pi-style `fullOutputPath`.
- Extended truncation metadata with byte counts and partial-tail-line fields needed by bash formatting.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k 'bash_tool_runs_command or bash_tool_raises_on_nonzero_exit or bash_tool_truncates_tail_and_persists_full_output or bash_tool_uses_operations_prefix_spawn_hook_and_updates or bash_tool_preserves_output_path_on_timeout_and_abort_errors or local_bash_operations_stream_env_and_abort'
```

Result: `6 passed, 25 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `31 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `141 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check
```

Result: passed.

## Remaining Count

After this phase, 6 plan checklist items remain open.
