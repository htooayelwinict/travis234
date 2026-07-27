# Pi-Aligned Read Pagination Normalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover MiMo-generated mixed-pagination `read` calls by selecting the target-owned pagination mode without weakening direct execution validation.

**Architecture:** Add one private argument-preparation function to the existing read-tool owner. Ordinary files retain Pi's line-pagination contract, registered virtual artifacts retain Travis234's byte-pagination extension, and the existing executor guard remains unchanged.

**Tech Stack:** Python 3.13, pytest, existing `AgentTool.prepare_arguments`, existing `ArtifactRegistry`

## Global Constraints

- Baseline commit for red-zone comparison: `f74beba`.
- Modify production code only in `travis/coding_agent/tools/read.py`.
- Modify regression coverage only in `tests/test_coding_tools_and_subagents.py`.
- Do not modify `travis/agent/**` or `travis/compaction/**`.
- Do not add provider-specific MiMo or OpenRouter behavior.
- Do not change read truncation limits, image handling, path resolution, persistence, tool ordering, or iteration budgeting.
- Preserve the existing direct-execution rejection for unresolved mixed line and byte pagination.
- Preserve argument object identity for calls that use zero or one pagination family.
- Do not use `appv231/` as implementation evidence or modify it.

---

## File structure

- `travis/coding_agent/tools/read.py`: owns read argument preparation, artifact classification, validation, and execution.
- `tests/test_coding_tools_and_subagents.py`: owns ordinary-file, artifact, and malformed-pagination read regressions.

No new production module is needed.

---

### Task 1: Normalize mixed read pagination by target ownership

**Files:**
- Modify: `travis/coding_agent/tools/read.py:120-155,370-430`
- Test: `tests/test_coding_tools_and_subagents.py:35-125`

**Interfaces:**
- Consumes: `ArtifactRegistry.resolve_read(path_or_id: str) -> Path | None`
- Produces: `_prepare_read_arguments(input_args: object, artifacts: ArtifactRegistry | None) -> object`
- Installs: `ToolDefinition.prepare_arguments`

- [ ] **Step 1: Add the failing ordinary-file MiMo regression**

Add after `test_read_tool_with_offset_and_truncation`:

```python
def test_read_tool_prepares_mixed_mimo_pagination_as_pi_lines(
    tmp_path: Path,
) -> None:
    from travis.coding_agent.tools.read import create_read_tool

    target = tmp_path / "SKILL.md"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")
    tool = create_read_tool(str(tmp_path))
    mixed = {
        "path": "SKILL.md",
        "offset": 1,
        "limit": 2000,
        "byte_offset": 0,
        "byte_limit": 50000,
    }

    assert tool.prepare_arguments is not None
    prepared = tool.prepare_arguments(mixed)

    assert prepared == {
        "path": "SKILL.md",
        "offset": 1,
        "limit": 2000,
    }
    assert mixed == {
        "path": "SKILL.md",
        "offset": 1,
        "limit": 2000,
        "byte_offset": 0,
        "byte_limit": 50000,
    }
    result = tool.execute("mimo-skill-read", prepared)
    assert result.content[0].text.splitlines() == ["line1", "line2", "line3"]
```

This exact payload is copied from the failing `openrouter/xiaomi/mimo-v2.5-pro` session.

- [ ] **Step 2: Add the failing virtual-artifact MiMo regression**

Add beside the existing artifact pagination tests:

```python
def test_read_tool_prepares_mixed_mimo_pagination_as_artifact_bytes(
    tmp_path: Path,
) -> None:
    from travis.coding_agent.artifacts import ArtifactRegistry
    from travis.coding_agent.tools.read import create_read_tool

    target = tmp_path / "single-line.log"
    target.write_bytes(b"BEGIN_SPOOL" + (b"x" * 80_000) + b"END_SPOOL")
    artifacts = ArtifactRegistry()
    artifact = artifacts.register(
        target,
        kind="command-output",
        remove_on_close=False,
    )
    tool = create_read_tool(str(tmp_path), artifacts=artifacts)
    mixed = {
        "path": artifact.id,
        "offset": 1,
        "limit": 2000,
        "byte_offset": 0,
        "byte_limit": 50000,
    }

    assert tool.prepare_arguments is not None
    prepared = tool.prepare_arguments(mixed)

    assert prepared == {
        "path": artifact.id,
        "byte_offset": 0,
        "byte_limit": 50000,
    }
    result = tool.execute("mimo-artifact-read", prepared)
    assert result.content[0].text.startswith("BEGIN_SPOOL")
    assert result.details["byteRange"] == {
        "start": 0,
        "endExclusive": 50000,
        "totalBytes": target.stat().st_size,
    }
```

- [ ] **Step 3: Prove both regressions fail for the expected reason**

Run:

```bash
/Users/htooayelwin/lewis/travis234/.venv/bin/python -m pytest -q \
  tests/test_coding_tools_and_subagents.py \
  -k "prepares_mixed_mimo_pagination"
```

Expected: both tests FAIL because `tool.prepare_arguments` is `None`.

If either test passes before implementation, stop and reassess the task rather than adding speculative code.

- [ ] **Step 4: Add the target-aware preparation function**

Add before `_execute_read()`:

```python
def _prepare_read_arguments(
    input_args: object,
    artifacts: ArtifactRegistry | None,
) -> object:
    if not isinstance(input_args, dict):
        return input_args
    line_mode = "offset" in input_args or "limit" in input_args
    byte_mode = "byte_offset" in input_args or "byte_limit" in input_args
    if not line_mode or not byte_mode:
        return input_args

    prepared = dict(input_args)
    path = prepared.get("path")
    artifact_path = (
        artifacts.resolve_read(path)
        if artifacts is not None and isinstance(path, str)
        else None
    )
    if artifact_path is not None:
        prepared.pop("offset", None)
        prepared.pop("limit", None)
    else:
        prepared.pop("byte_offset", None)
        prepared.pop("byte_limit", None)
    return prepared
```

This function performs no filesystem read and does not mutate the provider-owned argument mapping.

- [ ] **Step 5: Install preparation on the existing tool definition**

In `create_read_tool_definition()`, add:

```python
prepare_arguments=lambda args: _prepare_read_arguments(args, artifacts),
```

Place it beside `execute`. Do not remove or weaken the mixed-mode check in `_execute_read()`.

- [ ] **Step 6: Add identity coverage for valid single-mode calls**

Add to the ordinary-file regression after creating `tool`:

```python
line_only = {"path": "SKILL.md", "offset": 2, "limit": 1}
assert tool.prepare_arguments(line_only) is line_only
```

Add to the artifact regression after creating `tool`:

```python
byte_only = {
    "path": artifact.id,
    "byte_offset": 0,
    "byte_limit": 64,
}
assert tool.prepare_arguments(byte_only) is byte_only
```

These assertions protect the no-op path from unnecessary rewriting.

- [ ] **Step 7: Run focused read pagination tests**

Run:

```bash
/Users/htooayelwin/lewis/travis234/.venv/bin/python -m pytest -q \
  tests/test_coding_tools_and_subagents.py \
  -k "read and (pagination or artifact or mixed_mimo)"
```

Expected: PASS, including the existing direct mixed-pagination rejection.

- [ ] **Step 8: Run the complete tool/subagent test module**

Run:

```bash
/Users/htooayelwin/lewis/travis234/.venv/bin/python -m pytest -q \
  tests/test_coding_tools_and_subagents.py
```

Expected: PASS.

- [ ] **Step 9: Commit the correction**

```bash
git add \
  travis/coding_agent/tools/read.py \
  tests/test_coding_tools_and_subagents.py
git commit -m "fix: normalize mixed read pagination"
```

---

### Task 2: Requalify the release branch

**Files:**
- Verify only

**Interfaces:**
- Consumes: the committed read normalization
- Produces: fresh repository-level release evidence

- [ ] **Step 1: Inspect the implementation range and red zones**

Run:

```bash
git status --short
git log --oneline f74beba..HEAD
git diff --check f74beba..HEAD
git diff --exit-code f74beba..HEAD -- travis/agent travis/compaction
```

Expected: a clean worktree, no whitespace errors, and no red-zone output.

- [ ] **Step 2: Run the complete Python suite**

```bash
/Users/htooayelwin/lewis/travis234/.venv/bin/python -m pytest -q
```

Expected: PASS with zero failures.

- [ ] **Step 3: Run launcher tests and npm dry-run packaging**

```bash
npm run test:launcher
npm run pack:launcher
```

Expected: 21 launcher tests pass and npm reports the intended five-file tarball.

- [ ] **Step 4: Build Python distributions**

```bash
/Users/htooayelwin/lewis/travis234/.venv/bin/python -m build
```

Expected: the sdist and wheel build successfully.

- [ ] **Step 5: Run repository hygiene**

```bash
/Users/htooayelwin/lewis/travis234/.venv/bin/python \
  scripts/check_repository_hygiene.py
```

Expected: every reported hygiene category is zero.

- [ ] **Step 6: Rebuild and smoke-test the release image**

```bash
docker build \
  --no-cache \
  -f Dockerfile.release \
  -t travis234:red-zone-pi-reliability .
/Users/htooayelwin/lewis/travis234/.venv/bin/python \
  evals/container_smoke.py \
  --image travis234:red-zone-pi-reliability
```

Expected: image build succeeds and the installed-container smoke exits zero.

- [ ] **Step 7: Run final integrity checks**

```bash
git diff --check f74beba..HEAD
git diff --exit-code f74beba..HEAD -- travis/agent travis/compaction
git status --short
```

Expected: no whitespace errors, no red-zone changes, and a clean worktree.
