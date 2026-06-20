# Phase 011: Bash Status Semantics

Status: complete

## Reference

- `pi/packages/coding-agent/src/core/tools/bash.ts`

## Appv22 Files

- `appV2.2/appv22/coding_agent/tools/bash.py`
- `appV2.2/tests/test_coding_agent.py`

## Result

Removed appv22's success `[exit code 0]` footer from bash output and changed nonzero exits to raise tool errors with the command output plus `Command exited with code N`, matching Pi's direct tool execution behavior.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: passed, `15 passed`.
