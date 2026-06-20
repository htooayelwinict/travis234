# Phase 016: Hermes Historical Media Stripping

## Scope

Port the Hermes compaction behavior that strips image payloads from older surviving messages before the newest image-bearing user turn.

## Reference

- `hermes-agent/agent/context_compressor.py`
  - `_content_has_images`
  - `_strip_images_from_content`
  - `_strip_historical_media`

## Appv22 Changes

- `appV2.2/appv22/compaction/compressor.py`
  - Added `ImageContent` detection for appv22 dataclass message content.
  - Replaces historical `ImageContent` blocks with `TextContent("[Attached image - stripped after compression]")`.
  - Applies historical media stripping after summary assembly and tool-pair sanitization, matching Hermes ordering.

- `appV2.2/tests/test_compaction.py`
  - Added regression coverage for stripping an old protected-head image before a newer image-bearing user message.
  - Asserts the newest image-bearing user message keeps its image payload.

## Verification

- Focused red test before implementation:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py::test_compress_strips_historical_images_before_newest_image_user -q`
  - Result before port: `1 failed`

- Focused green test after implementation:
  - Same command
  - Result: `1 passed`

- Compaction suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_compaction.py tests/test_compaction_timing.py -q`
  - Result: `17 passed`

- Full suite:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests -q`
  - Result: `101 passed`

## Remaining Gaps

- Summary role selection/merge behavior and explicit summary end markers are not ported.
- Hermes protected-head system-message handling and bounded tail soft-ceiling details are still simplified.
- Secret redaction instructions, temporal anchoring, focused compression, summary-model fallback, and failure bookkeeping remain simplified.
