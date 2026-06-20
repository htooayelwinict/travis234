# Phase 047: Resource Loader Reload Context

Status: complete

## Goal

Port the next verified Pi coding-agent resource-loader gap: reloadable context files, `.pi/SYSTEM.md` / `APPEND_SYSTEM.md` discovery, AgentSession prompt rebuilding from loader state, and the extension `resources_discover` hook point.

## Reference Files

- `pi/packages/coding-agent/src/core/resource-loader.ts`
- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/src/core/sdk.ts`
- `pi/packages/coding-agent/src/core/extensions/types.ts`
- `pi/packages/coding-agent/src/core/extensions/runner.ts`

## Changes

- Added `appv22.coding_agent.resource_loader` with `DefaultResourceLoader`, `load_project_context_files()`, and Pi-compatible getter aliases.
- Ported AGENTS/CLAUDE context discovery order: global agent file first, then ancestor project files from root toward cwd.
- Ported `.pi/SYSTEM.md` and `.pi/APPEND_SYSTEM.md` discovery for the effective cwd, with global `agentDir` fallback.
- Added `DefaultResourceLoader.reload()` and `extend_resources()` / `extendResources()` cache behavior.
- `AgentSession` now consumes a `resource_loader`, creates and reloads a default loader when none is supplied, and rebuilds the system prompt from loader system prompt, append prompt, and context files.
- Added `AgentSession.reload_resources()` / `reloadResources()`.
- Added `ExtensionRunner.emit_resources_discover()` / `emitResourcesDiscover()` so `resources_discover` handlers can feed resource paths into the loader cache after `session_start`.
- Exported `DefaultResourceLoader` and `load_project_context_files` from `appv22.coding_agent`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "resource_loader"
```

Result: `2 passed, 47 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `49 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `169 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check
```

Result: passed.

```bash
rg -n "[ \t]+$" appV2.2/appv22/coding_agent/resource_loader.py appV2.2/appv22/coding_agent/extensions.py appV2.2/appv22/coding_agent/agent_session.py appV2.2/appv22/coding_agent/__init__.py appV2.2/tests/test_coding_agent.py
```

Result: no matches.

## Remaining Count

After this follow-up, the known open audit gaps are runtime-host session switching, package-manager-backed resource discovery, full skill/prompt-template/theme loading, labels/custom entries, and branch summary generation. The full plan checklist still has 0 unchecked items.
