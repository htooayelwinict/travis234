# Phase 058: User And Skill Rendering

Status: complete

## Goal

Port Pi interactive rendering for user messages and skill invocation blocks so appv22 no longer displays user turns as raw `> text` lines.

## Reference Files

- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/src/modes/interactive/interactive-mode.ts`
- `pi/packages/coding-agent/src/modes/interactive/components/user-message.ts`
- `pi/packages/coding-agent/src/modes/interactive/components/skill-invocation-message.ts`

## Changes

- Added Pi-compatible `parse_skill_block()` and `ParsedSkillBlock`.
- Added `UserMessageComponent` with OSC 133 prompt-zone markers.
- Added collapsed/expanded `SkillInvocationMessageComponent`.
- Updated `message_to_component()` to split skill blocks from trailing user text.
- Updated the interactive prompt loop to render submitted prompts through the user-message component instead of raw `> prompt` text.
- Updated TUI width/strip helpers to treat OSC escape sequences as zero-width.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q -k "user_and_skill or splits_skill_block or renders_real_prompt_loop or keeps_agent_output"
```

Result: `4 passed, 21 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_tui.py -q
```

Result: `25 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `184 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check -- appV2.2/appv22 appV2.2/tests plan/appv22-pi-hermes-full-scan-20260619-190049
```

Result: passed before documentation update.

## Reality Check

This closes another concrete Pi TUI mismatch found after the prior audit. The current compact appv22 plan has no known unchecked implementation items, but strict full-source parity is still broader than the compact runtime and should continue through targeted regressions for newly identified Pi/Hermes surfaces.
