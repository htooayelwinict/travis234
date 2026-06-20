# Phase 036: Find/Grep/Ls Path and Truncation Parity

## Goal

Recheck appv22 `find`, `grep`, `ls`, path utilities, and truncation behavior against the Pi coding-agent tools, then port the missing local Python equivalents without importing the reference modules.

## Reference Files

- `pi/packages/coding-agent/src/core/tools/find.ts`
- `pi/packages/coding-agent/src/core/tools/grep.ts`
- `pi/packages/coding-agent/src/core/tools/ls.ts`
- `pi/packages/coding-agent/src/core/tools/path-utils.ts`
- `pi/packages/coding-agent/src/core/tools/truncate.ts`
- `pi/packages/coding-agent/test/suite/regressions/3302-find-path-glob.test.ts`
- `pi/packages/coding-agent/test/suite/regressions/3303-find-nested-gitignore.test.ts`
- `pi/packages/coding-agent/test/path-utils.test.ts`

## Changes

- Added regressions for Pi path input normalization, read-path normalization, path-based `find` globs, scoped nested `.gitignore` behavior for `find`/`grep`, `find` result-limit notices, `grep` glob/literal/limit/no-match behavior, and `ls` entry-limit notices.
- Ported `resolve_to_cwd()` input normalization for leading `@` and Unicode spaces, plus `resolve_read_path()` macOS/NFD/curly-quote fallbacks.
- Wired `read` through `resolve_read_path()`.
- Replaced appv22-only `find.max_results` schema with Pi-style `limit` while keeping fallback argument compatibility for older callers.
- Added `FindOperations`, `GrepOperations`, and `LsOperations` injection surfaces.
- Ported Pi-style result texts/details for no matches, empty directories, result limits, entry limits, match limits, and truncation notices.
- Added path-containing glob support for `find`, plus POSIX relative path formatting from the search root.
- Added hierarchical `.gitignore` filtering for local `find`/`grep` traversal, including sibling-safe scoped rules.
- Added `grep` support for `glob`, `literal`, `ignoreCase`, `context`, `limit`, long-line truncation, and Pi no-match text.
- Added `ls` case-insensitive sorting and limit notices.
- Added `GREP_MAX_LINE_LENGTH`, `truncate_line()`, and Pi one-decimal `format_size()` behavior.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k 'read_tool_uses_pi_path_input_normalization or path_utils_normalizes_pi_file_inputs or find_tool_matches_path_globs_and_limit_notice or find_and_grep_respect_scoped_gitignore_rules or grep_tool_supports_glob_literal_limit_and_no_match_text or ls_tool_applies_pi_limit_notice_and_sorting'
```

Result: `6 passed, 22 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `28 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `138 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check
```

Result: passed.

## Remaining Count

After this phase, 7 plan checklist items remain open.
