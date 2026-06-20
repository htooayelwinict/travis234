# Phase 063: Interactive User Bash Extension Hook

Status: complete

## Goal

Port Pi's `user_bash` extension interception for interactive `!` and `!!` commands.

## Reference Files

- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts`
- `pi/packages/coding-agent/src/core/extensions/runner.ts`
- `pi/packages/coding-agent/src/core/extensions/types.ts`

## Changes

- Added `ExtensionRunner.emit_user_bash()` / `emitUserBash()`.
- Updated `InteractiveMode._run_bash_command()` to emit a Pi-shaped `user_bash` event with `command`, `excludeFromContext`, and `cwd`.
- Added support for extension-returned full `BashResult` objects: render output, mark command complete, and record the result without local shell execution.
- Added support for extension-returned `BashOperations`: pass them into normal `AgentSession.execute_bash()`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_bang_uses_user_bash_extension_result -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_bang_uses_user_bash_extension_result tests/test_tui.py::test_interactive_mode_bang_uses_user_bash_extension_operations -q
```

Result: `2 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q -k "bang_runs_bash or user_bash"
```

Result: `3 passed, 26 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "extension or execute_bash or abort_bash or pending_bash"
```

Result: `6 passed, 58 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q
```

Result: `29 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `193 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check -- appV2.2/appv22 appV2.2/tests plan/appv22-pi-hermes-full-scan-20260619-190049
```

Result: passed before documentation update.

```bash
rg -n "from pi|import pi|from hermes|import hermes|hermes-agent|appV2\.1|appv21|sys\.path.*pi|PYTHONPATH.*pi|importlib.*pi" appV2.2/appv22 appV2.2/tests appV2.2/scripts -g '*.py'
```

Result: only expected docstring/self-test references.

## Reality Check

This closes Pi's interactive `user_bash` interception surface for full-result and custom-operations handlers. The broad appv22 Pi/Hermes parity goal remains active because a strict source-wide completion audit has not proven every in-scope surface.
