# Phase 054: Default Branch Summary Generation

Status: complete

## Goal

Port Pi's default model-generated branch summary path into appv22 without importing reference modules, while keeping it separate from Hermes timing compaction.

## Reference Files

- `pi/packages/coding-agent/src/core/compaction/branch-summarization.ts`
- `pi/packages/coding-agent/src/core/compaction/utils.ts`
- `pi/packages/coding-agent/src/core/agent-session.ts`
- `pi/packages/coding-agent/src/core/messages.ts`

## Changes

- Added a regression for `AgentSession.navigate_tree(..., {"summarize": True})` when no extension supplies a summary.
- Added `appv22.coding_agent.branch_summarization` with Pi-style branch preparation, conversation serialization, summary prompt, summarization system prompt, file-operation tracking, preamble insertion, and read/modified file details.
- Wired `AgentSession.navigate_tree()` to call the default branch summarizer only for tree navigation summary requests.
- Kept the implementation isolated from Hermes `CompactionManager`, compressor cooldowns, session rotation, and real-usage tracking. The only shared piece is token estimation for prompt budgeting.

## Verification

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "default_branch_summary"
```

Result: `1 passed, 56 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q -k "navigate_tree or custom_entries or custom_message_next_turn"
```

Result: `5 passed, 52 deselected`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_coding_agent.py -q
```

Result: `57 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run pytest tests -q
```

Result: `178 passed`.

```bash
cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')
```

Result: passed.

```bash
git diff --check
```

Result: passed.

```bash
rg -n "[ \t]+$" appV2.2/appv22/coding_agent/agent_session.py appV2.2/appv22/coding_agent/session_store.py appV2.2/appv22/coding_agent/branch_summarization.py appV2.2/tests/test_coding_agent.py
```

Result: no matches.

## Remaining Count

After this follow-up, the known open audit gaps are package-manager-backed resource discovery and full skill/prompt-template/theme loading. The full plan checklist still has 0 unchecked items.
