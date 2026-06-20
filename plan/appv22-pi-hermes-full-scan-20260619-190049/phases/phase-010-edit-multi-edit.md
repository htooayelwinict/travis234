# Phase 010: Edit Multi-Edit Schema

Status: complete

## Reference

- `pi/packages/coding-agent/src/core/tools/edit.ts`
- `pi/packages/coding-agent/src/core/tools/edit-diff.ts`
- `pi/packages/coding-agent/test/edit-tool-legacy-input.test.ts`

## Appv22 Files

- `appV2.2/appv22/coding_agent/tools/edit.py`
- `appV2.2/appv22/coding_agent/tools/edit_diff.py`
- `appV2.2/appv22/coding_agent/tools/types.py`
- `appV2.2/tests/test_coding_agent.py`

## Result

Removed appv22's public `old_string/new_string` edit shape. The edit tool now exposes Pi-style `path` plus `edits[]`, supports Pi legacy `oldText/newText` preparation, applies multiple replacements against original file content, rejects missing/duplicate/empty/overlapping/no-op edits, preserves BOM and original line endings, and returns diff/patch details.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: passed, `14 passed`.
