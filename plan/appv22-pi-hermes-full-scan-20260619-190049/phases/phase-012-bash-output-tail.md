# Phase 012: Bash Tail Output and Full Output Persistence

Status: complete

## Reference

- `pi/packages/coding-agent/src/core/tools/bash.ts`
- `pi/packages/coding-agent/src/core/tools/output-accumulator.ts`
- `pi/packages/coding-agent/src/core/tools/truncate.ts`

## Appv22 Files

- `appV2.2/appv22/coding_agent/tools/bash.py`
- `appV2.2/appv22/coding_agent/tools/truncate.py`
- `appV2.2/tests/test_coding_agent.py`

## Result

Added tail truncation for bash output and temp-file persistence for truncated full output. The visible bash result now shows the tail of long output and includes a `Full output: ...` note; `details` carries the truncation object and `full_output_path`.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: passed, `16 passed`.
