# Phase 051: Runtime Host Import

Status: complete

## Goal

Port Pi runtime-host JSONL import semantics into appv22 without importing reference modules: missing-file error, cancelable resume switch, copying external JSONL files into the active session directory, replacement lifecycle events, and restored imported session context.

## Reference Files

- `pi/packages/coding-agent/src/core/agent-session-runtime.ts`
- `pi/packages/coding-agent/src/core/session-manager.ts`

## Changes

- Added a regression for `AgentSessionRuntime.import_from_jsonl()` covering missing files, cancellation, copy destination, replacement lifecycle events, and restored messages.
- Added `SessionImportFileNotFoundError` with `file_path` metadata, matching Pi's public error surface.
- Implemented `AgentSessionRuntime.import_from_jsonl()` / `importFromJsonl()`.
- Import now resolves the input path, errors before lifecycle hooks when the source file is missing, copies the JSONL into the current session directory, emits `session_before_switch` with reason `resume`, emits `session_shutdown` with reason `resume`, creates the replacement session with `session_start` reason `resume`, and reuses the runtime factory.
- Exported `SessionImportFileNotFoundError` from `appv22.coding_agent`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "runtime_import"
```

Result: `1 passed, 51 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `52 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `173 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check
```

Result: passed.

```bash
rg -n "[ \t]+$" appV2.2/appv22/coding_agent/agent_session_runtime.py appV2.2/appv22/coding_agent/__init__.py appV2.2/tests/test_coding_agent.py
```

Result: no matches.

## Remaining Count

After this follow-up, the known open audit gaps are tree navigation runtime flow, package-manager-backed resource discovery, full skill/prompt-template/theme loading, labels/custom entries, and branch summary generation. The full plan checklist still has 0 unchecked items.
