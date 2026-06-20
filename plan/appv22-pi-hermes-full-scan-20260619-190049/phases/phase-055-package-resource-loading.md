# Phase 055: Package Resource Loading

Status: complete

## Goal

Port Pi package-backed resource discovery and full skill/prompt-template/theme loading into appv22 without importing reference modules.

## Reference Files

- `pi/packages/coding-agent/src/core/resource-loader.ts`
- `pi/packages/coding-agent/src/core/package-manager.ts`
- `pi/packages/coding-agent/src/core/skills.ts`
- `pi/packages/coding-agent/src/core/prompt-templates.ts`
- `pi/packages/coding-agent/src/modes/interactive/theme/theme.ts`
- `pi/packages/coding-agent/src/core/system-prompt.ts`

## Changes

- Added a regression using a local Pi resource package with `package.json.pi` manifest entries for skills, prompt templates, and themes.
- Added `DefaultPackageManager` as a local runtime-loader subset that resolves local package roots, Pi package manifests, conventional package folders, `.pi` resources, and `.agents/skills` ancestor directories.
- Added Pi-shaped resource dataclasses for `Skill`, `PromptTemplate`, `Theme`, `ResolvedResource`, `ResolvedPaths`, and `ResourceDiagnostic`.
- Added local frontmatter parsing for skill and prompt markdown files.
- Added skill loading with `SKILL.md` discovery, description validation, collision diagnostics, source metadata, and `disable-model-invocation` support.
- Added prompt-template loading from markdown files with `description`, `argument-hint`, content, file path, and source metadata.
- Added theme JSON loading with source metadata.
- Updated `DefaultResourceLoader.reload()` and `extendResources()` to load and refresh skills, prompts, and themes from resolved paths.
- Updated system-prompt construction so loaded skills are included in the prompt when the `read` tool is active.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "resource_loader_resolves_package"
```

Result: `1 passed, 57 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "resource_loader or resource"
```

Result: `3 passed, 55 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `58 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `179 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check
```

Result: passed.

```bash
rg -n "[ \t]+$" appV2.2/appv22/coding_agent/resource_loader.py appV2.2/appv22/coding_agent/system_prompt.py appV2.2/appv22/coding_agent/agent_session.py appV2.2/tests/test_coding_agent.py
```

Result: no matches.

## Remaining Count

After this follow-up, the tracked open audit gaps from Phase 051-054 are closed. Do not mark the whole goal complete until a final full appv22-vs-Pi/Hermes audit pass checks for residual mismatches outside the tracked list.
