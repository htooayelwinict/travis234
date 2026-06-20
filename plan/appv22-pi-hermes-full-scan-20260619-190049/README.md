# appv22 Pi + Hermes Full Scan

Created: 2026-06-19 19:00:49 Asia/Yangon

## Goal

Bring `appV2.2/appv22` into spec compliance with the local Pi reference packages and Hermes context-compaction design by porting behavior into appv22 code directly, without importing runtime modules from `pi/` or `hermes-agent/`.

## Scope

- Appv22 package: `appV2.2/appv22`
- Pi references:
  - `pi/packages/ai/src`
  - `pi/packages/agent/src`
  - `pi/packages/coding-agent/src/core`
  - `pi/packages/coding-agent/src/modes/interactive`
  - `pi/packages/tui/src`
- Hermes references:
  - `hermes-agent/agent/context_compressor.py`
  - `hermes-agent/agent/conversation_loop.py`
  - `hermes-agent/agent/turn_context.py`
  - `hermes-agent/agent/conversation_compression.py`
  - `hermes-agent/agent/manual_compression_feedback.py`
  - `hermes-agent/agent/context_engine.py`
  - `hermes-agent/agent/memory_provider.py`
  - `hermes-agent/agent/memory_manager.py`
  - `hermes-agent/agent/turn_finalizer.py`

## Protected Behavior

- The current Hermes-style compaction layer in `appV2.2/appv22/compaction` and its session wiring is treated as protected. Do not refactor, simplify, or replace it during unrelated Pi/Hermes port slices.
- Only direct Hermes/Pi parity improvements are acceptable in compaction, and those must have focused regression coverage plus broad verification before being claimed safe.

## Artifacts

- `plan.md`: implementation queue and verification strategy.
- `research/appv22-pi-hermes-audit.md`: file-by-file compliance map and mismatches.
- `phases/`: phase status notes as individual ports are completed.
