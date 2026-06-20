# Phase 035: Write Tool Operations and Abort Queue

## Scope

Port the remaining write-tool Phase 4 parity slice from Pi: operation injection plus abort checks that keep the per-file mutation queue locked until the in-flight filesystem operation settles.

## Reference

- `pi/packages/coding-agent/src/core/tools/write.ts`
- `pi/packages/coding-agent/test/file-mutation-queue.test.ts`
- `pi/packages/coding-agent/test/tools.test.ts`

## Changes

- Added `test_write_tool_keeps_queue_locked_until_aborted_write_settles`.
- Tightened `test_write_tool_creates_dirs` to assert Pi success text and no result details.
- Added local `WriteOperations` with `mkdir` and `write_file` callables.
- Routed write execution through `WriteOperations` inside `with_file_mutation_queue()`.
- Kept abort checks before mkdir, after mkdir, and after write.
- Changed write result text to `Successfully wrote <len> bytes to <path>` and removed appv22-only details.
- Updated write description, prompt snippet, and prompt guideline to match Pi's current wording.

## Red/Green Evidence

Red:

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_write_tool_creates_dirs tests/test_coding_agent.py::test_write_tool_keeps_queue_locked_until_aborted_write_settles -q
```

Result:

- Failed during collection because `WriteOperations` did not exist.

Green:

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py::test_write_tool_creates_dirs tests/test_coding_agent.py::test_write_tool_keeps_queue_locked_until_aborted_write_settles -q
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Results:

- focused write regressions: `2 passed`
- `tests/test_coding_agent.py`: `22 passed`

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
git diff --check
```

Results:

- `tests`: `132 passed`
- `py_compile`: exit 0
- `git diff --check`: exit 0

## Remaining Work

- Port remaining Phase 4 tool gaps: bash streaming/process-tree abort/prefix hooks, read auto-resize/vision/render classification, and direct `find`/`grep`/`ls` parity rechecks.
- Port remaining Phase 3 session events and session persistence/branching.
- Continue Phase 6 TUI/rendering parity.
