# Phase 056: AI Env Key Fallback And Bridge Removal

Status: complete

## Goal

Close the final audit gap found in Pi `packages/ai` stream entrypoints and remove the obsolete JSON bridge surface that is outside the Pi/Hermes TUI design scope.

## Reference Files

- `pi/packages/ai/src/stream.ts`
- `pi/packages/ai/src/env.ts`
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts`

## Changes

- Added regressions proving `stream_simple()` injects a provider API key from the environment when options omit one.
- Added a regression proving an explicit caller-provided API key wins over the environment.
- Ported Pi-style provider environment key lookup into `appv22.ai.env_config`.
- Updated `stream()` and `stream_simple()` to apply env-key fallback before routing to the registered provider.
- Removed the obsolete `scripts/appv22_tui_bridge.py` JSONL/plain compatibility script and its tests; the appv22 entrypoint is now the actual interactive TUI path through `scripts/appv22_tui.py` and `appv22.cli`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_ai_stream.py tests/test_ai_env_config.py tests/test_ai_register_builtins.py tests/test_ai_appv2_env_provider.py tests/test_ai_models.py -q
```

Result: `18 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result after removing the obsolete bridge tests: `179 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check -- appV2.2/appv22 appV2.2/tests appV2.2/scripts plan/appv22-pi-hermes-full-scan-20260619-190049
```

Result: passed.

```bash
rg -n "appv22_tui_bridge|JSONL bridge|JSON/RPC bridge|old bridge" appV2.2 appV2.2/tests appV2.2/scripts -g '*.*'
```

Result: no matches.

```bash
rg -n "from pi|import pi|from hermes|import hermes|hermes-agent|appV2\.1|appv21|sys\.path.*pi|PYTHONPATH.*pi|importlib.*pi" appV2.2/appv22 appV2.2/tests appV2.2/scripts -g '*.py'
```

Result: no forbidden runtime imports; remaining matches are the appv21 coupling regression itself and docstrings naming Hermes reference files.

## Remaining Count

Known tracked implementation gaps from this plan are closed. The honest remaining count for the current plan checklist is 0 unchecked items.

Strict reality check: this does not mean appv22 is a literal file-for-file clone of the entire Pi/Hermes source trees. The scanned references are much larger and include provider, auth, export, RPC, CLI, and platform-specific surfaces outside the compact appv22 runtime. Any future work should start with a new targeted parity regression for one of those explicit surfaces.
