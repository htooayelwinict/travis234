# Deep Generational Compaction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Repository instructions prohibit subagents, and the user has prohibited all Git operations. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace only the manual `/compact deep` behavior with an atomic, bounded generational checkpoint that retains no pre-compaction raw suffix, while leaving normal `/compact`, automatic compaction, overflow recovery, and every shared compaction/session layer unchanged.

**Architecture:** A new command-local strategy module inspects the completed transcript, serializes it with bounded tool evidence, generates one replacement handoff, and permits one repair call when validation fails. `CompactionTransactionCoordinator.manual()` invokes it only when `deep=True`, then reuses the existing explicit-compaction persistence APIs with `firstKeptEntryId=""`; the old multi-pass manager remains intact as the rollback implementation and is not edited.

**Tech Stack:** Python 3.11+, dataclasses, existing Travis234 message and compaction types, pytest, JSONL session persistence, existing model summarizer callbacks.

## Global Constraints

- The product and CLI remain `Travis234` and `travis234`; the import package remains `travis`.
- Work only in the repository root. Do not create a Git worktree.
- Do not invoke Git commands, create commits, merge, push, reset, or clean.
- Do not modify `travis/compaction/compressor.py`, `travis/compaction/timing.py`, `travis/coding_agent/compaction_adapter.py`, `travis/coding_agent/session_store.py`, the agent loop, or provider code.
- Do not change normal `/compact`, automatic threshold compaction, overflow recovery, extension compaction, session schema, or session resume behavior.
- The new implementation is reachable only through the guarded manual `deep=True` command path used by `/compact deep` and its existing `/compress deep` alias.
- A deep checkpoint must write exactly one ordinary compaction entry with `firstKeptEntryId=""`; earlier JSONL records remain append-only and untouched.
- Any unsafe boundary, capacity error, summarizer error, failed repair, validation error, or insufficient token reduction must preserve the exact original messages and append no compaction entry.
- The preferred handoff body is 2,048 estimated tokens; the absolute ceiling is 4,096 estimated tokens.
- Permit at most one repair summarizer call. Never mechanically truncate a generated handoff.
- Add a failing regression test before each behavior change, then run focused and repository-level verification.
- Preserve credentials: tests and diagnostics must never print prompts, summaries, `.env` values, auth records, or provider keys.

## File Structure

- Create `travis/coding_agent/deep_compaction_command.py`: owns deep-only boundary inspection, bounded serialization, prompt construction, anchor extraction, summary generation, one repair attempt, validation, and the result type.
- Modify `travis/coding_agent/compaction_coordinator.py`: add one guarded `deep=True` route inside manual compaction, reuse existing adapter/manager APIs, and construct command status without changing any non-deep branch.
- Modify `travis/tui/interactive_session_commands.py`: update only the `/compact deep` help line from “multi-pass” to “generational checkpoint.”
- Create `tests/test_deep_compaction_command.py`: pure unit tests for boundary, serialization, budgeting, repair, redaction, file anchors, and no-op behavior.
- Modify `tests/test_compaction_integration.py`: persistence, rollback, resume, greeting, background-process details, and normal/automatic isolation regressions.
- Modify `tests/test_tui_commands_and_extensions.py`: help-copy and `/compress deep` alias coverage.

---

### Task 1: Define the deep-command result and safe-boundary contract

**Files:**
- Create: `travis/coding_agent/deep_compaction_command.py`
- Create: `tests/test_deep_compaction_command.py`

**Interfaces:**
- Consumes: `Sequence[AgentMessage]` from the manual command coordinator.
- Produces: `DeepCheckpointResult` and `inspect_deep_boundary(messages) -> str | None`.

- [ ] **Step 0: Record protected shared-layer checksums outside the repository**

Run:

```bash
shasum -a 256 \
  travis/compaction/compressor.py \
  travis/compaction/timing.py \
  travis/coding_agent/compaction_adapter.py \
  travis/coding_agent/session_store.py \
  > /tmp/travis234-deep-protected.sha256
```

Expected: `/tmp/travis234-deep-protected.sha256` contains four hashes and no repository file changes.

- [ ] **Step 1: Write failing safe-boundary tests**

Add tests that construct ordinary Travis messages and assert exact refusal reasons:

```python
from travis.ai.types import AssistantMessage, TextContent, ToolCall, ToolResultMessage, UserMessage, empty_usage
from travis.coding_agent.deep_compaction_command import inspect_deep_boundary


def _assistant(text: str = "done", *, stop_reason: str = "stop", calls=()):
    return AssistantMessage(
        content=[TextContent(text=text), *calls],
        api="faux",
        provider="faux",
        model="faux-model",
        usage=empty_usage(),
        stop_reason=stop_reason,
    )


def test_deep_boundary_accepts_a_completed_turn() -> None:
    messages = [UserMessage(content="inspect"), _assistant("inspection complete")]
    assert inspect_deep_boundary(messages) is None


def test_deep_boundary_refuses_an_unanswered_user() -> None:
    assert inspect_deep_boundary([UserMessage(content="unfinished")]) == "unanswered_user"


def test_deep_boundary_refuses_an_aborted_final_assistant() -> None:
    messages = [UserMessage(content="run"), _assistant(stop_reason="aborted")]
    assert inspect_deep_boundary(messages) == "aborted_assistant"


def test_deep_boundary_refuses_an_errored_final_assistant() -> None:
    messages = [UserMessage(content="run"), _assistant(stop_reason="error")]
    assert inspect_deep_boundary(messages) == "errored_assistant"


def test_deep_boundary_refuses_an_unmatched_tool_call() -> None:
    call = ToolCall(id="call-1", name="read", arguments={"path": "large.log"})
    messages = [UserMessage(content="read it"), _assistant(stop_reason="toolUse", calls=(call,))]
    assert inspect_deep_boundary(messages) == "unmatched_tool_call"


def test_deep_boundary_refuses_a_tool_result_without_final_assistant() -> None:
    call = ToolCall(id="call-1", name="read", arguments={"path": "large.log"})
    result = ToolResultMessage(
        tool_call_id="call-1",
        tool_name="read",
        content=[TextContent(text="result")],
        is_error=False,
    )
    messages = [UserMessage(content="read it"), _assistant(stop_reason="toolUse", calls=(call,)), result]
    assert inspect_deep_boundary(messages) == "unfinished_tool_turn"
```

- [ ] **Step 2: Run the boundary tests and verify failure**

Run:

```bash
uv run pytest -q tests/test_deep_compaction_command.py -k deep_boundary
```

Expected: collection fails because `travis.coding_agent.deep_compaction_command` does not exist.

- [ ] **Step 3: Add the result type and boundary inspector**

Create the module with these public definitions and exact refusal ordering:

```python
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from travis.agent.types import AgentMessage
from travis.ai.types import AssistantMessage, ToolCall

DEEP_BODY_TARGET_TOKENS = 2_048
DEEP_BODY_MAX_TOKENS = 4_096
DEEP_MIN_SAVINGS_TOKENS = 256
DEEP_MIN_SAVINGS_RATIO = 0.05
DEEP_STRATEGY = "generational-v1"


@dataclass(frozen=True)
class DeepCheckpointResult:
    compressed: bool
    summary: str | None
    details: dict[str, object] | None
    tokens_before: int
    handoff_tokens: int
    repair_count: int
    target_tokens: int
    reason: str | None = None
    error: str | None = None


def inspect_deep_boundary(messages: Sequence[AgentMessage]) -> str | None:
    visible = [
        message
        for message in messages
        if getattr(message, "role", None) in {"user", "assistant", "toolResult"}
    ]
    if not visible:
        return None

    final = visible[-1]
    final_role = getattr(final, "role", None)
    if final_role == "user":
        return "unanswered_user"
    if final_role == "toolResult":
        return "unfinished_tool_turn"
    if final_role != "assistant":
        return "unfinished_turn"

    stop_reason = getattr(final, "stop_reason", "stop")
    if stop_reason == "aborted":
        return "aborted_assistant"
    if stop_reason == "error":
        return "errored_assistant"
    if stop_reason != "stop":
        return "unmatched_tool_call" if stop_reason == "toolUse" else "unfinished_turn"

    call_ids = {
        block.id
        for message in visible
        if getattr(message, "role", None) == "assistant"
        for block in getattr(message, "content", ())
        if isinstance(block, ToolCall)
    }
    result_ids = {
        str(getattr(message, "tool_call_id", ""))
        for message in visible
        if getattr(message, "role", None) == "toolResult"
    }
    if call_ids - result_ids:
        return "unmatched_tool_call"
    return None
```

- [ ] **Step 4: Run the boundary tests and verify pass**

Run:

```bash
uv run pytest -q tests/test_deep_compaction_command.py -k deep_boundary
```

Expected: all boundary tests pass.

- [ ] **Step 5: Review checkpoint without Git operations**

Run `git diff` is prohibited. Review only the two named files using `sed` and confirm no other file changed.

---

### Task 2: Add bounded serialization and recent file anchors

**Files:**
- Modify: `travis/coding_agent/deep_compaction_command.py`
- Modify: `tests/test_deep_compaction_command.py`

**Interfaces:**
- Consumes: completed compressor-context messages.
- Produces: `serialize_deep_source(messages) -> str` and `recent_file_operations(messages) -> tuple[list[str], list[str]]`.

- [ ] **Step 1: Write failing serialization tests**

Add tests proving large tool payloads and reasoning cannot dominate the summarizer request:

```python
from travis.ai.types import ThinkingContent
from travis.coding_agent.deep_compaction_command import recent_file_operations, serialize_deep_source


def test_deep_serialization_bounds_tool_output_and_excludes_reasoning() -> None:
    call = ToolCall(id="read-1", name="read", arguments={"path": "logs/huge.log"})
    assistant = AssistantMessage(
        content=[ThinkingContent(thinking="PRIVATE-REASONING" * 500), call],
        api="faux",
        provider="faux",
        model="faux-model",
        usage=empty_usage(),
        stop_reason="toolUse",
    )
    result = ToolResultMessage(
        tool_call_id="read-1",
        tool_name="read",
        content=[TextContent(text="HEAD\n" + "x" * 20_000 + "\nTAIL")],
        is_error=False,
    )

    serialized = serialize_deep_source([UserMessage(content="inspect"), assistant, result, _assistant()])

    assert "PRIVATE-REASONING" not in serialized
    assert "HEAD" in serialized and "TAIL" in serialized
    assert "tool output compacted" in serialized
    assert len(serialized) < 8_000


def test_recent_file_operations_are_bounded_and_modified_wins() -> None:
    messages = []
    for index in range(40):
        messages.append(_assistant(stop_reason="toolUse", calls=(
            ToolCall(id=f"read-{index}", name="read", arguments={"path": f"src/read_{index}.py"}),
        )))
        messages.append(_assistant(stop_reason="toolUse", calls=(
            ToolCall(id=f"edit-{index}", name="edit", arguments={"path": f"src/edit_{index}.py"}),
        )))

    read_files, modified_files = recent_file_operations(messages)

    assert len(read_files) == 16
    assert len(modified_files) == 32
    assert modified_files[-1] == "src/edit_39.py"
```

- [ ] **Step 2: Run the serialization tests and verify failure**

Run:

```bash
uv run pytest -q tests/test_deep_compaction_command.py -k 'serialization or recent_file'
```

Expected: import errors for the missing functions.

- [ ] **Step 3: Implement bounded serialization**

Add constants and helpers with the following behavior:

```python
import json

from travis.ai.types import TextContent, ToolResultMessage, UserMessage
from travis.compaction.compressor import _bash_file_mutation_paths, _tool_path

DEEP_USER_MAX_CHARS = 4_000
DEEP_ASSISTANT_MAX_CHARS = 4_000
DEEP_TOOL_RESULT_MAX_CHARS = 2_000
DEEP_TOOL_RESULT_HEAD_CHARS = 1_400
DEEP_TOOL_RESULT_TAIL_CHARS = 500
DEEP_TOOL_ARGUMENT_MAX_CHARS = 1_000
DEEP_READ_FILE_LIMIT = 16
DEEP_MODIFIED_FILE_LIMIT = 32


def _bounded_text(text: str, *, limit: int, head: int, tail: int, marker: str) -> str:
    if len(text) <= limit:
        return text
    removed = len(text) - head - tail
    return text[:head] + f"\n...[{marker}: {removed} chars omitted]...\n" + text[-tail:]


def serialize_deep_source(messages: Sequence[AgentMessage]) -> str:
    records: list[str] = []
    for message in messages:
        role = getattr(message, "role", "unknown")
        if role == "compactionSummary":
            records.append(f"[PREVIOUS CHECKPOINT]\n{getattr(message, 'summary', '')}")
            continue
        if role == "user":
            content = getattr(message, "content", "")
            text = content if isinstance(content, str) else "".join(
                block.text for block in content if isinstance(block, TextContent)
            )
            records.append("[USER]\n" + _bounded_text(
                text,
                limit=DEEP_USER_MAX_CHARS,
                head=3_000,
                tail=800,
                marker="user content compacted",
            ))
            continue
        if role == "assistant":
            text = "".join(
                block.text for block in getattr(message, "content", ())
                if isinstance(block, TextContent)
            )
            calls = []
            for block in getattr(message, "content", ()):
                if not isinstance(block, ToolCall):
                    continue
                encoded = json.dumps(block.arguments, ensure_ascii=False, sort_keys=True)
                encoded = _bounded_text(
                    encoded,
                    limit=DEEP_TOOL_ARGUMENT_MAX_CHARS,
                    head=700,
                    tail=200,
                    marker="tool arguments compacted",
                )
                calls.append(f"{block.name}({encoded})")
            body = _bounded_text(
                text,
                limit=DEEP_ASSISTANT_MAX_CHARS,
                head=3_000,
                tail=800,
                marker="assistant content compacted",
            )
            if calls:
                body += "\n[TOOL CALLS]\n" + "\n".join(calls)
            records.append("[ASSISTANT]\n" + body)
            continue
        if role == "toolResult":
            text = "".join(
                block.text for block in getattr(message, "content", ())
                if isinstance(block, TextContent)
            )
            records.append(
                f"[TOOL RESULT {getattr(message, 'tool_name', '')} "
                f"{getattr(message, 'tool_call_id', '')}]\n"
                + _bounded_text(
                    text,
                    limit=DEEP_TOOL_RESULT_MAX_CHARS,
                    head=DEEP_TOOL_RESULT_HEAD_CHARS,
                    tail=DEEP_TOOL_RESULT_TAIL_CHARS,
                    marker="tool output compacted",
                )
            )
            continue
        if role == "bashExecution":
            command = str(getattr(message, "command", "") or "")
            output = str(getattr(message, "output", "") or "")
            records.append(
                "[USER SHELL EXECUTION]\n"
                + _bounded_text(
                    f"$ {command}\n{output}",
                    limit=DEEP_TOOL_RESULT_MAX_CHARS,
                    head=DEEP_TOOL_RESULT_HEAD_CHARS,
                    tail=DEEP_TOOL_RESULT_TAIL_CHARS,
                    marker="shell output compacted",
                )
            )
            continue
        if role == "branchSummary":
            records.append(f"[BRANCH CHECKPOINT]\n{getattr(message, 'summary', '')}")
            continue
        if role == "custom":
            content = getattr(message, "content", "")
            text = content if isinstance(content, str) else "".join(
                block.text for block in content if isinstance(block, TextContent)
            )
            records.append("[CUSTOM CONTEXT]\n" + _bounded_text(
                text,
                limit=DEEP_USER_MAX_CHARS,
                head=3_000,
                tail=800,
                marker="custom context compacted",
            ))
    return "\n\n".join(records)
```

Implement recent-first collection without sorting away recency:

```python
def recent_file_operations(messages: Sequence[AgentMessage]) -> tuple[list[str], list[str]]:
    reads: list[str] = []
    modified: list[str] = []

    def add_recent(target: list[str], path: str, limit: int) -> None:
        if path and path not in target:
            target.append(path)
            if len(target) > limit:
                del target[0]

    for message in messages:
        if getattr(message, "role", None) != "assistant":
            continue
        for block in getattr(message, "content", ()):
            if not isinstance(block, ToolCall):
                continue
            if block.name == "bash":
                for path in sorted(_bash_file_mutation_paths(block.arguments)):
                    add_recent(modified, path, DEEP_MODIFIED_FILE_LIMIT)
                continue
            path = _tool_path(block.arguments)
            if block.name == "read":
                add_recent(reads, path, DEEP_READ_FILE_LIMIT)
            elif block.name in {"write", "edit"}:
                add_recent(modified, path, DEEP_MODIFIED_FILE_LIMIT)

    modified_set = set(modified)
    return [path for path in reads if path not in modified_set], modified
```

- [ ] **Step 4: Run the serialization tests and verify pass**

Run:

```bash
uv run pytest -q tests/test_deep_compaction_command.py -k 'serialization or recent_file'
```

Expected: all selected tests pass.

- [ ] **Step 5: Review checkpoint without Git operations**

Confirm the serializer never includes `ThinkingContent` and caps each source message independently.

---

### Task 3: Generate and validate one bounded generational handoff

**Files:**
- Modify: `travis/coding_agent/deep_compaction_command.py`
- Modify: `tests/test_deep_compaction_command.py`

**Interfaces:**
- Consumes: messages, existing `ContextCompressor`, optional summarizer override, optional focus.
- Produces: `generate_deep_checkpoint(messages, compressor, summarizer=None, focus=None) -> DeepCheckpointResult`.

- [ ] **Step 1: Write failing generation and repair tests**

Add tests using a real `ContextCompressor` with deterministic summarizers:

```python
from travis.compaction.compressor import ContextCompressor, SUMMARY_END_MARKER, SUMMARY_PREFIX
from travis.coding_agent.deep_compaction_command import generate_deep_checkpoint

VALID_SUMMARY = """## Historical Task Snapshot
None.
## Goal
Preserve the completed checkpoint.
## Constraints & Preferences
(none)
## Completed Actions
1. Verified the requested state.
## Active State at Compaction Cut
Idle.
## Historical In-Progress State
None.
## Blocked
(none)
## Key Decisions
- Use an atomic checkpoint.
## Resolved Questions
None.
## Historical Pending User Asks
None.
## Relevant Files
(none)
## Historical Remaining Work
None.
## Critical Context
Checkpoint complete.
"""


def test_deep_generation_accepts_a_bounded_valid_summary() -> None:
    compressor = ContextCompressor(context_length=1_048_576)
    messages = [UserMessage(content="finish"), _assistant("finished")]

    result = generate_deep_checkpoint(messages, compressor, summarizer=lambda _prompt: VALID_SUMMARY)

    assert result.compressed is True
    assert result.summary is not None
    assert result.handoff_tokens <= 4_096
    assert result.repair_count == 0
    assert result.details["deepStrategy"] == "generational-v1"


def test_deep_generation_repairs_an_oversized_summary_once() -> None:
    calls: list[str] = []

    def summarize(prompt: str) -> str:
        calls.append(prompt)
        return (VALID_SUMMARY + "x" * 20_000) if len(calls) == 1 else VALID_SUMMARY

    compressor = ContextCompressor(context_length=1_048_576)
    result = generate_deep_checkpoint(
        [UserMessage(content="finish"), _assistant("finished")],
        compressor,
        summarizer=summarize,
    )

    assert result.compressed is True
    assert result.repair_count == 1
    assert len(calls) == 2


def test_deep_generation_rolls_back_after_failed_repair() -> None:
    calls: list[str] = []

    def summarize(prompt: str) -> str:
        calls.append(prompt)
        return VALID_SUMMARY + "x" * 20_000

    compressor = ContextCompressor(context_length=1_048_576)
    result = generate_deep_checkpoint(
        [UserMessage(content="finish"), _assistant("finished")],
        compressor,
        summarizer=summarize,
    )

    assert result.compressed is False
    assert result.reason == "validation_failed"
    assert result.repair_count == 1
    assert len(calls) == 2


def test_deep_generation_refuses_secret_shaped_output() -> None:
    compressor = ContextCompressor(context_length=1_048_576)
    leaked = VALID_SUMMARY + "\nOPENAI_API_KEY=sk-secret-value"
    result = generate_deep_checkpoint(
        [UserMessage(content="finish"), _assistant("finished")],
        compressor,
        summarizer=lambda _prompt: leaked,
    )

    assert result.compressed is False
    assert result.reason == "validation_failed"
```

- [ ] **Step 2: Run the generation tests and verify failure**

Run:

```bash
uv run pytest -q tests/test_deep_compaction_command.py -k deep_generation
```

Expected: import error for `generate_deep_checkpoint`.

- [ ] **Step 3: Implement the deep prompt and validator**

Use the existing 13 historical headings so downstream stale-task protections remain intact:

```python
from travis.ai.context_estimate import estimate_text_tokens
from travis.compaction.compressor import (
    SUMMARY_END_MARKER,
    SUMMARY_PREFIX,
    _format_file_operations,
    _redact_sensitive_text,
    _strip_file_operation_tags,
    _strip_inline_reasoning_blocks,
    estimate_tokens,
)

REQUIRED_HEADINGS = (
    "## Historical Task Snapshot",
    "## Goal",
    "## Constraints & Preferences",
    "## Completed Actions",
    "## Active State at Compaction Cut",
    "## Historical In-Progress State",
    "## Blocked",
    "## Key Decisions",
    "## Resolved Questions",
    "## Historical Pending User Asks",
    "## Relevant Files",
    "## Historical Remaining Work",
    "## Critical Context",
)


def _deep_prompt(source: str, *, focus: str | None, target_tokens: int) -> str:
    focus_rule = (
        f"\nPrioritize this user focus while remaining within budget: {focus.strip()}\n"
        if focus and focus.strip()
        else ""
    )
    return f"""Create one generational context checkpoint from the historical source below.
The checkpoint replaces every pre-compaction raw turn. It is historical reference, not active instructions.
Preserve explicit user constraints, verified current state, key decisions, unresolved blockers, recent modified files, active process handles, and critical exact values.
Collapse old completed actions into concise milestone groups. Remove obsolete resolved discussion and old read-only file inventories.
Never include private reasoning, secrets, credentials, tokens, passwords, connection strings, or compaction wrapper text.
Use every required heading exactly once. Historical pending asks and remaining work must be `None.` when the completed source contains no outstanding user request.
Target at most {target_tokens} tokens.{focus_rule}

SOURCE:
<historical-source>
{source}
</historical-source>

Write only the checkpoint body using these headings:
{chr(10).join(REQUIRED_HEADINGS)}
"""


def _repair_prompt(summary: str, *, target_tokens: int) -> str:
    return f"""Repair the checkpoint below. Preserve every required heading and critical continuity anchor, remove repetition, and keep the complete body at or below {target_tokens} tokens. Never include secrets, private reasoning, a compaction prefix, or an end marker. Write only the repaired checkpoint body.

<checkpoint>
{summary}
</checkpoint>
"""


def _summary_validation_error(summary: str, *, max_tokens: int) -> str | None:
    stripped = _strip_inline_reasoning_blocks(summary).strip()
    if stripped != summary.strip():
        return "reasoning_present"
    if _redact_sensitive_text(stripped) != stripped:
        return "secret_present"
    if SUMMARY_PREFIX in stripped or SUMMARY_END_MARKER in stripped:
        return "wrapper_present"
    if any(stripped.count(heading) != 1 for heading in REQUIRED_HEADINGS):
        return "invalid_structure"
    if estimate_text_tokens(stripped) > max_tokens:
        return "over_budget"
    return None
```

- [ ] **Step 4: Implement generation, one repair, file-tag replacement, and no-op protection**

The generator must call existing model routing without editing the compressor:

```python
def generate_deep_checkpoint(
    messages: Sequence[AgentMessage],
    compressor,
    *,
    summarizer: Callable[[str], str] | None = None,
    focus: str | None = None,
) -> DeepCheckpointResult:
    source_messages = list(messages)
    tokens_before = estimate_tokens(source_messages)
    boundary_reason = inspect_deep_boundary(source_messages)
    if boundary_reason:
        return DeepCheckpointResult(
            False, None, None, tokens_before, tokens_before, 0,
            DEEP_BODY_TARGET_TOKENS, reason=boundary_reason,
        )

    source = serialize_deep_source(source_messages)
    prompt = _deep_prompt(source, focus=focus, target_tokens=DEEP_BODY_TARGET_TOKENS)
    summarizer_window = int(getattr(compressor, "summarizer_context_window", 0) or 0)
    if summarizer_window:
        prompt_tokens = estimate_text_tokens(prompt)
        reserve = int(getattr(compressor, "summarizer_max_tokens", 0) or DEEP_BODY_MAX_TOKENS) + 4_096
        if prompt_tokens + reserve >= summarizer_window:
            return DeepCheckpointResult(
                False, None, None, tokens_before, tokens_before, 0,
                DEEP_BODY_TARGET_TOKENS, reason="summarizer_capacity",
            )

    try:
        summary = compressor._run_summary_summarizer(prompt, summarizer)  # noqa: SLF001
    except Exception as error:  # noqa: BLE001
        return DeepCheckpointResult(
            False, None, None, tokens_before, tokens_before, 0,
            DEEP_BODY_TARGET_TOKENS,
            reason="summary_failed",
            error=compressor._compact_error_text(error),  # noqa: SLF001
        )
    if summary is None:
        return DeepCheckpointResult(
            False, None, None, tokens_before, tokens_before, 0,
            DEEP_BODY_TARGET_TOKENS, reason="summary_unavailable",
        )

    repair_count = 0
    error = _summary_validation_error(summary, max_tokens=DEEP_BODY_TARGET_TOKENS)
    if error:
        repair_count = 1
        try:
            repaired = compressor._run_summary_summarizer(  # noqa: SLF001
                _repair_prompt(summary, target_tokens=DEEP_BODY_TARGET_TOKENS),
                summarizer,
            )
        except Exception as repair_error:  # noqa: BLE001
            return DeepCheckpointResult(
                False, None, None, tokens_before, tokens_before, repair_count,
                DEEP_BODY_TARGET_TOKENS,
                reason="repair_failed",
                error=compressor._compact_error_text(repair_error),  # noqa: SLF001
            )
        summary = repaired or ""
        error = _summary_validation_error(summary, max_tokens=DEEP_BODY_MAX_TOKENS)
    if error:
        return DeepCheckpointResult(
            False, None, None, tokens_before, tokens_before, repair_count,
            DEEP_BODY_TARGET_TOKENS, reason="validation_failed", error=error,
        )

    read_files, modified_files = recent_file_operations(source_messages)
    summary = _strip_file_operation_tags(summary).rstrip()
    file_section = _format_file_operations(read_files, modified_files)
    if file_section:
        summary += file_section
    if estimate_text_tokens(summary) > DEEP_BODY_MAX_TOKENS:
        return DeepCheckpointResult(
            False, None, None, tokens_before, tokens_before, repair_count,
            DEEP_BODY_TARGET_TOKENS,
            reason="validation_failed",
            error="file_anchors_over_budget",
        )
    handoff_tokens = estimate_text_tokens(
        f"{SUMMARY_PREFIX}\n{summary}\n\n{SUMMARY_END_MARKER}"
    )
    minimum_savings = max(DEEP_MIN_SAVINGS_TOKENS, int(tokens_before * DEEP_MIN_SAVINGS_RATIO))
    if tokens_before - handoff_tokens < minimum_savings:
        return DeepCheckpointResult(
            False, None, None, tokens_before, handoff_tokens, repair_count,
            DEEP_BODY_TARGET_TOKENS, reason="insufficient_reduction",
        )

    details: dict[str, object] = {
        "readFiles": read_files,
        "modifiedFiles": modified_files,
        "deepStrategy": DEEP_STRATEGY,
        "handoffTokens": handoff_tokens,
        "repairCount": repair_count,
        "targetTokens": DEEP_BODY_TARGET_TOKENS,
    }
    return DeepCheckpointResult(
        True, summary, details, tokens_before, handoff_tokens,
        repair_count, DEEP_BODY_TARGET_TOKENS,
    )
```

- [ ] **Step 5: Run the complete pure deep-command test file**

Run:

```bash
uv run pytest -q tests/test_deep_compaction_command.py
```

Expected: all tests pass.

- [ ] **Step 6: Run secret and compaction estimator regressions**

Run:

```bash
uv run pytest -q tests/test_compaction.py -k 'redact or reasoning or estimator or summary_budget'
```

Expected: all selected existing tests pass.

---

### Task 4: Route only manual deep compaction through the new strategy

**Files:**
- Modify: `travis/coding_agent/compaction_coordinator.py`
- Modify: `tests/test_compaction_integration.py`

**Interfaces:**
- Consumes: `DeepCheckpointResult` from Task 3.
- Produces: existing `ManualCompressionStatus`; successful persistence has `firstKeptEntryId=""`.

- [ ] **Step 1: Write the failing persistence and greeting regression**

Add an integration test that appends a large completed turn, compacts deeply, and resumes:

```python
VALID_DEEP_SUMMARY = """## Historical Task Snapshot
None.
## Goal
Preserve the completed checkpoint.
## Constraints & Preferences
(none)
## Completed Actions
1. Verified the requested state.
## Active State at Compaction Cut
Idle.
## Historical In-Progress State
None.
## Blocked
(none)
## Key Decisions
- Use an atomic checkpoint.
## Resolved Questions
None.
## Historical Pending User Asks
None.
## Relevant Files
(none)
## Historical Remaining Work
None.
## Critical Context
Checkpoint complete.
"""


def _completed_assistant(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)],
        api="faux",
        provider="faux",
        model="faux-model",
        usage=empty_usage(),
        stop_reason="stop",
    )


def test_manual_deep_generational_checkpoint_keeps_no_raw_suffix_and_survives_resume(tmp_path: Path) -> None:
    session_path = tmp_path / "deep-generational.jsonl"
    prompts: list[str] = []
    session = _session_with_compaction(session_path, prompts)
    source = []
    for index in range(24):
        source.extend([
            UserMessage(content=f"deep-history user {index} " + ("x" * 80)),
            AssistantMessage(
                content=[TextContent(text=f"deep-history assistant {index} " + ("y" * 80))],
                api="faux",
                provider="faux",
                model="faux-model",
                usage=empty_usage(),
                stop_reason="stop",
            ),
        ])
    _append_messages(session, source)

    status = session.compact(deep=True, summarizer=lambda _prompt: VALID_DEEP_SUMMARY)

    assert status.compressed is True
    entry = next(entry for entry in reversed(session.session_entries) if entry["type"] == "compaction")
    assert entry["firstKeptEntryId"] == ""
    assert entry["details"]["deepStrategy"] == "generational-v1"
    assert len(session.messages) == 1
    assert session.messages[0].role == "compactionSummary"

    resumed = _session_with_compaction(session_path, prompts)
    assert len(resumed.messages) == 1
    assert resumed.messages[0].role == "compactionSummary"
```

Add the provider-facing greeting regression with a marker that the deterministic summary does not contain:

```python
def test_manual_deep_next_greeting_does_not_replay_the_large_raw_tail(tmp_path: Path) -> None:
    session = _session_with_compaction(tmp_path / "deep-greeting.jsonl", [])
    marker = "RAW-LARGE-TAIL-MARKER"
    _append_messages(session, [
        UserMessage(content="complete the large task"),
        AssistantMessage(
            content=[TextContent(text=marker + ("x" * 40_000))],
            api="faux",
            provider="faux",
            model="faux-model",
            usage=empty_usage(),
            stop_reason="stop",
        ),
    ])

    status = session.compact(deep=True, summarizer=lambda _prompt: VALID_DEEP_SUMMARY)
    assert status.compressed is True
    session.agent.state.messages.append(UserMessage(content="hello"))
    rendered = repr(to_compressor_messages(session.messages))

    assert marker not in rendered
    assert "Checkpoint complete" in rendered
    assert "hello" in rendered
```

- [ ] **Step 2: Write the failing atomic-refusal regression**

Capture the session file bytes and message identities before deep compaction with an unfinished tool turn:

```python
def test_manual_deep_refusal_does_not_append_or_replace_context(tmp_path: Path) -> None:
    session_path = tmp_path / "deep-refusal.jsonl"
    session = _session_with_compaction(session_path, [])
    call = ToolCall(id="pending", name="read", arguments={"path": "pending.log"})
    _append_messages(session, [
        UserMessage(content="inspect pending"),
        AssistantMessage(
            content=[call],
            api="faux",
            provider="faux",
            model="faux-model",
            usage=empty_usage(),
            stop_reason="toolUse",
        ),
    ])
    before_bytes = session_path.read_bytes()
    before_messages = list(session.messages)

    status = session.compact(deep=True, summarizer=lambda _prompt: VALID_DEEP_SUMMARY)

    assert status.compressed is False
    assert status.deep_stop_reason == "unmatched_tool_call"
    assert session_path.read_bytes() == before_bytes
    assert session.messages == before_messages
```

- [ ] **Step 3: Write normal and automatic isolation regressions**

Characterize exact current boundaries before editing the coordinator:

```python
def test_normal_manual_compaction_still_uses_manager_boundary(tmp_path: Path) -> None:
    session = _session_with_compaction(tmp_path / "normal.jsonl", [])
    _append_messages(session, _large_messages("normal", count=24))
    status = session.compact(deep=False)
    assert status.compressed is True
    assert status.first_kept_entry_id
    assert len(session.messages) > 1


def test_automatic_compaction_still_keeps_a_raw_suffix(tmp_path: Path) -> None:
    session = _session_with_compaction(tmp_path / "automatic.jsonl", [])
    _append_messages(session, _large_messages("automatic", count=80))
    outcome = session.compaction_transactions.preflight(session.messages)
    assert outcome.compressed is True
    entry = next(entry for entry in reversed(session.session_entries) if entry["type"] == "compaction")
    assert entry["firstKeptEntryId"]
```

- [ ] **Step 4: Run the new integration tests and verify the deep tests fail**

Run:

```bash
uv run pytest -q tests/test_compaction_integration.py -k 'deep_generational or deep_refusal or normal_manual_compaction_still or automatic_compaction_still'
```

Expected: deep generational tests fail because the coordinator still calls the multi-pass manager; normal and automatic characterization tests pass.

- [ ] **Step 5: Add one guarded deep route inside `manual()`**

Import the strategy and add the branch only after existing extension interception has had its chance:

```python
from travis.coding_agent.deep_compaction_command import generate_deep_checkpoint
```

Inside `operation()`, immediately after the existing extension-compaction block:

```python
            if deep:
                deep_result = generate_deep_checkpoint(
                    source,
                    self.manager.compressor,
                    summarizer=summarizer or self.manager._summarizer,  # noqa: SLF001
                    focus=focus,
                )
                if not deep_result.compressed:
                    status = ManualCompressionStatus(
                        messages=source,
                        compressed=False,
                        noop=True,
                        headline="Deep checkpoint made no changes",
                        token_line=f"Approx request size: ~{deep_result.tokens_before:,} tokens (unchanged)",
                        note=_deep_refusal_note(deep_result.reason),
                        warning=(
                            f"Deep checkpoint failed: {deep_result.error}"
                            if deep_result.error
                            else None
                        ),
                        focus=focus,
                        tokens_before=deep_result.tokens_before,
                        deep=True,
                        compression_passes=1 + deep_result.repair_count,
                        deep_stop_reason=deep_result.reason,
                        target_tokens=deep_result.target_tokens,
                    )
                    return CompactionOutcome(messages=source, compressed=False, result=status)

                compaction = {
                    "summary": deep_result.summary,
                    "firstKeptEntryId": "",
                    "tokensBefore": deep_result.tokens_before,
                    "details": deep_result.details,
                }
                output, entry = self._adapter.apply_extension_compaction(
                    compaction,
                    source_messages=source,
                )
                record = self.manager.record_extension_compaction(
                    output,
                    summary=deep_result.summary or "",
                    tokens_before=deep_result.tokens_before,
                    details=deep_result.details,
                    trigger="manual",
                )
                self._emit_session_compact(
                    entry,
                    from_extension=False,
                    reason="manual",
                    will_retry=False,
                )
                status = ManualCompressionStatus(
                    messages=output,
                    compressed=True,
                    noop=False,
                    headline=f"Deep checkpoint: {len(source)} → {len(output)} messages",
                    token_line=(
                        f"Approx request size: ~{deep_result.tokens_before:,} → "
                        f"~{deep_result.handoff_tokens:,} tokens"
                    ),
                    note=(
                        "Created one bounded generational handoff with no retained raw suffix."
                        + (" One repair pass was used." if deep_result.repair_count else "")
                    ),
                    focus=focus,
                    summary=deep_result.summary,
                    details=deep_result.details,
                    tokens_before=deep_result.tokens_before,
                    first_kept_entry_id="",
                    summary_model_requested=record.summary_model_requested,
                    summary_model_used=record.summary_model_used,
                    summary_model_fallback=record.summary_model_fallback,
                    summary_model_error=record.summary_model_error,
                    summary_model_dedicated=record.summary_model_dedicated,
                    deep=True,
                    compression_passes=1 + deep_result.repair_count,
                    deep_stop_reason="target_reached",
                    target_tokens=deep_result.target_tokens,
                )
                return CompactionOutcome(messages=output, compressed=True, result=status)
```

Add a command-local refusal formatter in the same coordinator file:

```python
def _deep_refusal_note(reason: str | None) -> str:
    notes = {
        "unanswered_user": "Deep checkpoint refused because the latest user message has no completed answer.",
        "aborted_assistant": "Deep checkpoint refused because the latest assistant response was aborted.",
        "errored_assistant": "Deep checkpoint refused because the latest assistant response failed.",
        "unmatched_tool_call": "Deep checkpoint refused because a tool call is unfinished.",
        "unfinished_tool_turn": "Deep checkpoint refused because the latest tool turn has no final assistant response.",
        "summarizer_capacity": "Deep checkpoint refused because the summarizer cannot fit the checkpoint source. Run normal /compact first.",
        "summary_failed": "Deep checkpoint summary generation failed; the original context was preserved.",
        "summary_unavailable": "Deep checkpoint has no configured summarizer; the original context was preserved.",
        "repair_failed": "Deep checkpoint repair failed; the original context was preserved.",
        "validation_failed": "Deep checkpoint validation failed; the original context was preserved.",
        "insufficient_reduction": "Deep checkpoint was skipped because it would not materially reduce context.",
    }
    return notes.get(reason, "Deep checkpoint refused; the original context was preserved.")
```

Do not edit the existing non-deep call to `self.manager.compress_manual_with_status(...)`.

- [ ] **Step 6: Run the focused integration tests and verify pass**

Run:

```bash
uv run pytest -q tests/test_compaction_integration.py -k 'deep_generational or deep_refusal or normal_manual_compaction_still or automatic_compaction_still'
```

Expected: all selected tests pass.

- [ ] **Step 7: Run all compaction coordinator and timing tests**

Run:

```bash
uv run pytest -q tests/test_compaction_integration.py tests/test_compaction_timing.py tests/test_compaction.py
```

Expected: all tests pass, including the unchanged direct-manager multi-pass deep tests.

---

### Task 5: Preserve process details and prove atomic failure behavior

**Files:**
- Modify: `tests/test_compaction_integration.py`
- Modify: `tests/test_deep_compaction_command.py`
- Modify: `travis/coding_agent/deep_compaction_command.py`
- Modify: `travis/coding_agent/compaction_coordinator.py`

**Interfaces:**
- Consumes: existing adapter process-detail merge and deep command result.
- Produces: regression proof that no shared persistence edits are required.

- [ ] **Step 1: Add active background-process detail coverage**

Use the existing `_session_with_compaction()` helper’s process-context injection pattern to return one `ProcessContextRecord`, then assert the adapter merges it without deep-strategy process code:

```python
from travis.coding_agent.process_context import ProcessContextRecord


def test_manual_deep_preserves_active_process_details_via_existing_adapter(tmp_path: Path) -> None:
    session = _session_with_compaction(tmp_path / "deep-process.jsonl", [])
    session._process_context.resolve = lambda _messages: (
        ProcessContextRecord(
            session_id="proc_1234567890abcdef1234567890abcdef",
            status="running",
            cursor=12,
            output_size=48,
            exit_code=None,
            durable_output=True,
        ),
    )
    _append_messages(session, [
        UserMessage(content="start service"),
        _completed_assistant("service running " + ("x" * 8_000)),
    ])

    status = session.compact(deep=True, summarizer=lambda _prompt: VALID_DEEP_SUMMARY)

    assert status.compressed is True
    entry = next(entry for entry in reversed(session.session_entries) if entry["type"] == "compaction")
    assert entry["details"]["deepStrategy"] == "generational-v1"
    assert entry["details"]["managedProcesses"][0]["sessionId"] == "proc_1234567890abcdef1234567890abcdef"
    assert entry["details"]["managedProcesses"][0]["status"] == "running"
```

Do not add process logic to the new strategy; `SessionCompactionAdapter.apply_extension_compaction()` remains the owner.

- [ ] **Step 2: Add summarizer exception and invalid-structure rollback coverage**

For each failure, snapshot session bytes and messages, invoke deep compaction, and assert:

```python
assert status.compressed is False
assert session_path.read_bytes() == before_bytes
assert session.messages == before_messages
assert not any(
    entry["type"] == "compaction" and entry["timestamp"] > before_timestamp
    for entry in session.session_entries
)
```

- [ ] **Step 3: Add repeated-deep no-op coverage**

After one successful deep checkpoint, invoke `/compact deep` again without new messages. Assert `insufficient_reduction`, keep the original compaction entry as the leaf, and prove the summarizer call count does not change.

Add this fast path immediately after `tokens_before` is calculated in `generate_deep_checkpoint()`:

```python
    if (
        len(source_messages) == 1
        and getattr(source_messages[0], "role", None) == "compactionSummary"
        and estimate_text_tokens(str(getattr(source_messages[0], "summary", "") or ""))
        <= DEEP_BODY_TARGET_TOKENS
    ):
        return DeepCheckpointResult(
            False, None, None, tokens_before, tokens_before, 0,
            DEEP_BODY_TARGET_TOKENS, reason="insufficient_reduction",
        )
```

- [ ] **Step 4: Run focused process and rollback tests**

Run:

```bash
uv run pytest -q tests/test_deep_compaction_command.py tests/test_compaction_integration.py -k 'deep and (process or rollback or repeated or repair or invalid)'
```

Expected: all selected tests pass.

- [ ] **Step 5: Verify shared files are unchanged by checksum**

Run:

```bash
shasum -a 256 -c /tmp/travis234-deep-protected.sha256
```

Expected: all four protected files report `OK`.

---

### Task 6: Update only deep-command help and alias coverage

**Files:**
- Modify: `travis/tui/interactive_session_commands.py:419`
- Modify: `tests/test_tui_commands_and_extensions.py`

**Interfaces:**
- Consumes: existing `_manual_compression_options()` parsing.
- Produces: accurate help text; `/compress deep` remains an alias for manual deep compaction.

- [ ] **Step 1: Write the failing help-copy test**

Assert help contains:

```text
/compact deep [focus] - Create an aggressive bounded generational checkpoint.
```

and no longer describes deep compaction as multi-pass.

- [ ] **Step 2: Write alias parsing coverage**

Add:

```python
def test_compress_deep_remains_a_manual_deep_alias() -> None:
    assert _manual_compression_options("/compress deep context envelope") == (
        "context envelope",
        True,
    )
```

- [ ] **Step 3: Run the command tests and verify the help test fails**

Run:

```bash
uv run pytest -q tests/test_tui_commands_and_extensions.py -k 'compact and (help or deep or compress)'
```

Expected: the help-copy assertion fails; alias characterization passes.

- [ ] **Step 4: Change only the help line**

Replace the current line with:

```python
"/compact deep [focus] - Create an aggressive bounded generational checkpoint.",
```

- [ ] **Step 5: Run the command tests and verify pass**

Run:

```bash
uv run pytest -q tests/test_tui_commands_and_extensions.py -k 'compact and (help or deep or compress)'
```

Expected: all selected tests pass.

---

### Task 7: Run proportional and repository-level verification

**Files:**
- No product-file edits unless a failing regression identifies a defect inside the approved deep-only scope.
- Do not update verification records or README in this task.

**Interfaces:**
- Consumes: completed deep-command implementation.
- Produces: evidence required by repository guidance before reporting completion.

- [ ] **Step 1: Run the complete deep and compaction test group**

Run:

```bash
uv run pytest -q \
  tests/test_deep_compaction_command.py \
  tests/test_compaction.py \
  tests/test_compaction_policy.py \
  tests/test_compaction_timing.py \
  tests/test_compaction_integration.py \
  tests/test_coding_persistence_and_compaction.py \
  tests/test_tui_runtime_compaction_and_models.py \
  tests/test_tui_commands_and_extensions.py
```

Expected: all selected tests pass.

- [ ] **Step 2: Run architecture boundary tests**

Run:

```bash
uv run pytest -q \
  tests/test_compaction_boundary_architecture.py \
  tests/test_hermes_compaction_parity.py \
  tests/test_coding_exports_and_boundaries.py
```

Expected: all tests pass and no shared compaction ownership boundary changes.

- [ ] **Step 3: Run the full Python suite**

Run:

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests -q
```

Expected: all repository Python tests pass.

- [ ] **Step 4: Run npm launcher tests and package dry-run**

Run:

```bash
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Expected: launcher tests pass and the package contains only declared release files.

- [ ] **Step 5: Build Python distributions**

Run:

```bash
python -m build
```

Expected: wheel and source distribution build successfully.

- [ ] **Step 6: Run acceptance/parity verification**

Run:

```bash
.venv/bin/python scripts/verify_acceptance.py --parity-json
```

Expected: acceptance and parity checks pass.

- [ ] **Step 7: Build and smoke the release container**

Run when Docker is available:

```bash
docker build --no-cache -f Dockerfile.release -t travis234:hardening-smoke .
python evals/container_smoke.py --image travis234:hardening-smoke
```

Expected: image build succeeds and the unprivileged production smoke passes.

- [ ] **Step 8: Run the credential-safe follow-up scenario regressions**

Run:

```bash
uv run pytest -q \
  tests/test_compaction_integration.py::test_manual_deep_generational_checkpoint_keeps_no_raw_suffix_and_survives_resume \
  tests/test_compaction_integration.py::test_manual_deep_next_greeting_does_not_replay_the_large_raw_tail
```

Expected: both scenarios pass without credentials or paid-provider calls.

- [ ] **Step 9: Final scope inspection without Git operations**

Use `rg --files`, `sed`, and the protected checksum from Task 1 to confirm the only product changes are:

```text
travis/coding_agent/deep_compaction_command.py
travis/coding_agent/compaction_coordinator.py
travis/tui/interactive_session_commands.py
```

The only test changes must be the three test files listed in this plan. Do not invoke Git.

---

## Self-Review Checklist

- Spec coverage: deep-only routing, zero raw suffix, safe boundary, bounded 2K/4K handoff, one repair, atomic rollback, process details, session resume, greeting quality, no-op repetition, and normal/automatic isolation each have explicit tasks and tests.
- Placeholder scan: the plan contains no deferred implementation markers; every code-changing step includes exact code or an exact assertion contract.
- Type consistency: `DeepCheckpointResult`, `inspect_deep_boundary()`, `serialize_deep_source()`, `recent_file_operations()`, and `generate_deep_checkpoint()` use the same names and return types across all tasks.
- Rollback boundary: the old `CompactionManager` deep implementation remains unedited and available.
- Git constraint: there are no commit, add, reset, merge, push, clean, checkout, or worktree steps.
