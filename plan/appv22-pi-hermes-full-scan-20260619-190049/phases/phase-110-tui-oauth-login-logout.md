# Phase 110 - TUI OAuth Login Logout Commands

## Goal

Port the next Pi interactive auth flow into appv22 without importing Pi modules: `/login` and `/logout` must be local TUI commands that drive OAuth provider auth instead of falling through into the model.

## Reference

- `pi/packages/coding-agent/src/core/slash-commands.ts` registers `/login` and `/logout`.
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts` intercepts exact `/login` and `/logout`, shows auth selectors, invokes OAuth login callbacks, and removes only stored credentials on logout.
- `pi/packages/coding-agent/src/modes/interactive/components/login-dialog.ts` and `oauth-selector.ts` define the rendered OAuth selector/dialog surfaces.

## Regression

Added `test_interactive_mode_login_logout_oauth_are_local_tui_commands`.

The test first failed because appv22 routed `/login`, selection input, `/logout`, and selection input through the model:

```text
AssertionError: assert ['model', 'model', 'model', 'model'] == ['login']
```

## Implementation

- Added `/login` and `/logout` to the base TUI autocomplete command list.
- Added local auth command parsing before normal provider dispatch.
- Implemented line-oriented OAuth provider selection over registered OAuth providers.
- Implemented `/login` handling that calls `login_oauth_provider()` with callbacks for auth URL, device-code, prompt, progress, manual code input, select prompt, and signal.
- Implemented `/logout` handling that selects from stored OAuth credentials and calls `logout_provider()`, leaving env/model-config auth untouched.
- Rendered local status/error rows for login/logout instead of sending those commands to the model.
- Updated TUI test setup to reset the model registry between tests so dynamic provider auth state cannot leak across TUI tests.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py::test_interactive_mode_login_logout_oauth_are_local_tui_commands -q
```

Result: `1 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -k "login or logout or compact_alias or manual_compress or autocomplete" -q
```

Result: `4 passed, 45 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -k "provider or auth or oauth or model or command or extension" -q
```

Result: `42 passed, 56 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_ai_models.py tests/test_ai_stream.py -q
```

Result: `9 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `247 passed`.

## Remaining Count

The full goal is still active. Remaining work should continue with final appv22-vs-Pi/Hermes audit and any newly identified gaps, especially API-key login UI, OAuth callback-server/manual redirect UX, richer extension/provider hooks, runtime-host session switching details, and final live compaction/TUI confidence checks.
