# Coding System-Prompt Deduplication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan inline after the process-quality plan reaches GREEN. Do not dispatch subagents unless the user separately and explicitly authorizes them.

**Goal:** Reduce semantic duplication in Travis234's default coding system prompt without weakening engineering discipline, active-tool routing, bounded subagent behavior, documentation discovery, project instructions, or lazy skills.

**Architecture:** Keep `build_system_prompt()` and tool-owned prompt metadata as the existing assembly boundary. Make the senior-engineering paragraph the single source of high-level subagent policy, leave only two operation-specific rules on `spawn_subagent`, and compact the always-present Travis234 documentation index while retaining exact installed-file discovery. Use deterministic prompt-delta budgets and existing behavior tests instead of changing session/context machinery.

**Tech Stack:** Python 3.13, pytest, Travis234 `BuildSystemPromptOptions`, tool metadata, resource discovery, deterministic context estimation, `uv`, npm, Twine, and Docker.

## Global Constraints

- Execute this plan only after `2026-08-10-process-tool-call-quality-and-context-hygiene.md` passes its focused automated gates.
- Work only in `/Users/htooayelwin/lewis/travis234` on `main`, preserving all pre-existing dirty changes.
- Never edit, stage, remove, or overwrite `docs/superpowers/plans/2026-07-27-red-zone-free-pi-reliability-parity.md` or `docs/travis234-future-agent-framework-brainstorm.md`.
- Add a failing regression before each prompt behavior change.
- Do not modify tool activation, `_prompt_requests_subagent_tools()`, `_prompt_rejects_subagent_tools()`, bounded parallel execution, child ownership, result collection, or subagent limits.
- Explicit user requests not to use subagents must continue to hide every subagent tool for that turn.
- Project instructions that restrict delegation must take precedence over generic automatic-delegation guidance.
- Keep evidence verification, exact test-count verification, changed-file verification, and non-fabrication requirements verbatim in meaning.
- Keep installed documentation discovery exact: never advertise a missing file and never invent a topic file.
- Keep skill bodies lazy. Do not remove skill name, description, or location metadata and do not alter skill precedence.
- Do not make documentation injection depend on the current user message; that would change the system prompt across turns and complicate envelope accounting.
- Do not modify the generic loop, AgentHarness, session persistence, compaction, context estimation, provider transport, tool schemas, or process lifecycle.
- No commit, push, tag, release, publication, or account change is authorized.

---

## File Responsibility Map

- `travis/coding_agent/system_prompt.py`: canonical engineering/subagent policy, execution routing, documentation index formatting, project context, and skill catalog placement.
- `travis/coding_agent/session_subagents.py`: operation-specific `spawn_subagent` prompt metadata; no session activation or execution changes.
- `tests/test_coding_tools_and_subagents.py`: subagent prompt delta, explicit opt-out, active-tool behavior, and canonical policy assertions.
- `tests/test_coding_resources_and_services.py`: documentation discovery, missing-file safety, prompt line budget, and built-in prompt metadata.
- `tests/test_reference_runtime_contract.py`: absence of broad behavioral recovery policy.
- `tests/test_context_estimate.py`: unchanged additive envelope accounting.
- `docs/verification/main-coding-system-prompt-compactness.md`: before/after prompt measurements and non-secret automated evidence.

## Measured Baseline

- Default coding prompt with managed process tools: approximately 6,974 characters and 1,744 Travis234-estimated tokens.
- Current-repository prompt with root `AGENTS.md` and two packaged lazy skills: approximately 9,155 characters and 2,289 estimated tokens.
- Subagent policy adds approximately 1,606 characters and 402 estimated tokens to the core prompt.
- The always-present installed Travis234 documentation section adds approximately 1,001 characters and 251 estimated tokens in this worktree.
- The prompt contains 31 unique guideline strings. Exact-string deduplication exists, but semantically equivalent subagent instructions occur in the main policy paragraph and `spawn_subagent` guidelines.
- The process-quality plan owns process-schema and process-guidance reduction. This plan must not reopen that work.

## Combined Execution Order

For the complete approved repair, follow the cross-plan order recorded in `2026-08-10-process-tool-call-quality-and-context-hygiene.md`: process Tasks 1-5, focused process checkpoint, this plan's Tasks 1-4, one combined broad qualification, then one twelve-prompt Minimax M3 installed-wheel TUI run. If this plan is executed independently at a later time, run all of Task 5 before reporting completion.

---

### Task 1: Add Deterministic Subagent and Documentation Prompt Budgets

**Files:**
- Modify: `tests/test_coding_tools_and_subagents.py`
- Modify: `tests/test_coding_resources_and_services.py`

**Interfaces:**
- Consumes: `AgentSession.system_prompt`, `set_active_tools_by_name()`, `build_system_prompt()`, and monkeypatchable `get_packaged_context_paths()`.
- Produces: regression budgets that fail on semantic duplication rather than absolute installation-path length.

- [ ] **Step 1: Add a failing subagent prompt-delta test**

```python
def test_default_subagent_policy_has_one_compact_authority(tmp_path: Path) -> None:
    session = AgentSession(
        cwd=str(tmp_path),
        model=faux_model(),
        agent_dir=str(tmp_path / "agent"),
    )
    try:
        with_subagents = session.system_prompt
        session.set_active_tools_by_name(["read", "bash", "tmux", "edit", "write"])
        without_subagents = session.system_prompt
    finally:
        session.shutdown()

    delta = len(with_subagents) - len(without_subagents)
    assert delta <= 1_150
    assert with_subagents.count("two or more independent, bounded") == 1
    assert "project instructions restrict delegation" in with_subagents
    assert with_subagents.count("Honor an explicit user request not to use subagents") == 1
```

The budget compares prompts built in the same process with identical cwd, documentation, project context, skills, and date, so path length cancels out.

- [ ] **Step 2: Add a failing controlled documentation-section budget test**

```python
def test_default_documentation_index_is_compact_and_exact(tmp_path: Path, monkeypatch) -> None:
    resources = tmp_path / "resources"
    readme = resources / "README.md"
    docs = resources / "docs"
    examples = resources / "examples"
    docs.mkdir(parents=True)
    examples.mkdir()
    readme.write_text("# Travis234\n", encoding="utf-8")
    first = docs / "README.md"
    second = docs / "extensions.md"
    first.write_text("# Docs\n", encoding="utf-8")
    second.write_text("# Extensions\n", encoding="utf-8")
    monkeypatch.setattr(
        system_prompt_module,
        "get_packaged_context_paths",
        lambda: (str(readme), str(docs), str(examples)),
    )

    prompt = build_system_prompt(BuildSystemPromptOptions(cwd=str(tmp_path)))
    section = prompt[prompt.index("Travis234 documentation") : prompt.index("Current date:")]

    assert len(section.splitlines()) <= 9
    assert str(readme) in section
    assert str(first) in section
    assert str(second) in section
    assert str(examples) in section
    assert "never assume an unlisted topic file exists" in section
```

- [ ] **Step 3: Run the two tests and verify RED**

```bash
uv run --no-sync python -m pytest -q \
  tests/test_coding_tools_and_subagents.py::test_default_subagent_policy_has_one_compact_authority \
  tests/test_coding_resources_and_services.py::test_default_documentation_index_is_compact_and_exact
```

Expected: the subagent delta exceeds 1,150 characters or repeats policy, and the documentation section exceeds nine lines.

- [ ] **Step 4: Checkpoint tests without changing production code**

```bash
git diff --check
git diff -- tests/test_coding_tools_and_subagents.py tests/test_coding_resources_and_services.py
```

---

### Task 2: Establish One Canonical Subagent Policy and Remove Tool-Metadata Repetition

**Files:**
- Modify: `travis/coding_agent/system_prompt.py`
- Modify: `travis/coding_agent/session_subagents.py`
- Modify: `tests/test_coding_tools_and_subagents.py`
- Modify: `tests/test_coding_resources_and_services.py`

**Interfaces:**
- Consumes: `_SUBAGENT_ORCHESTRATION_GUIDANCE`, `ToolDefinition.prompt_guidelines`, and the existing active-tool filter.
- Produces: one high-level subagent policy plus two operation-specific spawn rules.
- Preserves: the schemas, execution callbacks, role handling, timeouts, child result APIs, and per-turn opt-out tool filtering.

- [ ] **Step 1: Rewrite the canonical high-level policy in `system_prompt.py`**

Use one paragraph with these exact semantics:

```python
_SUBAGENT_ORCHESTRATION_GUIDANCE = (
    "Use subagents when the user explicitly requests delegation. Otherwise, use them for two or more independent, "
    "bounded engineering workstreams only when project instructions do not restrict delegation. Give each child "
    "exact scope, constraints, expected evidence, and verification; do not delegate trivial, sequential, tightly "
    "coupled, shared-architecture, overlapping-edit, integration, or final-validation work. Start independent "
    "children concurrently with `spawn_subagent` and `wait=false`, continue useful parent work, collect every child "
    "with `wait_subagent`, and independently verify material claims before synthesizing the outcome. Honor an "
    "explicit user request not to use subagents."
)
```

Keep `_ENGINEERING_GUIDANCE` unchanged, including child summaries as leads, exact count/file verification, and non-fabrication.

- [ ] **Step 2: Reduce `spawn_subagent` prompt guidelines to operation-specific rules**

Replace its seven guidelines with:

```python
prompt_guidelines=[
    "Pass the user's exact delegated path or name directly to the child; do not inspect or resolve that target first with parent tools.",
    "Spawn independent children together with wait=false; collect every result and verify child evidence before finalizing.",
],
```

Do not change the `spawn_subagent` description, schema, callback, or other subagent tool definitions in this task.

- [ ] **Step 3: Update obsolete exact prompt assertions**

Update `test_agent_session_exposes_only_core_subagent_workflow_by_default` to assert:

```python
assert "Use subagents when the user explicitly requests delegation" in session.system_prompt
assert "only when project instructions do not restrict delegation" in session.system_prompt
assert "Start independent children concurrently" in session.system_prompt
assert "collect every child with `wait_subagent`" in session.system_prompt
assert "Treat child summaries as leads rather than proof" in session.system_prompt
assert "Never invent files, tests, command results, or verification" in session.system_prompt
```

Retain every activation/opt-out test. Do not rewrite `_prompt_requests_subagent_tools()` or `_prompt_rejects_subagent_tools()` to make the prompt budget pass.

- [ ] **Step 4: Run the focused subagent prompt and behavior tests**

```bash
uv run --no-sync python -m pytest -q \
  tests/test_coding_tools_and_subagents.py -k \
  'subagent_policy or core_subagent_workflow or parallel_delegation or opt_out or natural_coding_request'
```

Expected: prompt budget passes, explicitly requested/parallel language still exposes the core tools, and explicit opt-out still removes every subagent tool for the provider turn.

- [ ] **Step 5: Run the full subagent and resource modules**

```bash
uv run --no-sync python -m pytest -q \
  tests/test_subagents.py \
  tests/test_coding_tools_and_subagents.py \
  tests/test_coding_resources_and_services.py
```

- [ ] **Step 6: Checkpoint the task without committing**

```bash
git diff --check
git status --short
```

---

### Task 3: Compact the Installed Documentation Index Without Making It Dynamic

**Files:**
- Modify: `travis/coding_agent/system_prompt.py`
- Modify: `tests/test_coding_resources_and_services.py`

**Interfaces:**
- Consumes: `get_packaged_context_paths()` and filesystem existence checks.
- Produces: a maximum nine-line documentation section for the current README/docs/examples shape.
- Preserves: exact installed Markdown enumeration, missing-path suppression, docs/examples root resolution, and full-read/cross-reference requirements.

- [ ] **Step 1: Rewrite `_documentation_section()` output assembly**

Retain the existing discovery code and conditional existence checks. Replace only the prose/list assembly with:

```python
lines = [
    "",
    "",
    "Travis234 documentation (consult only for Travis234 itself, its SDK, extensions, themes, skills, or TUI):",
]
if readme_exists:
    lines.append(f"- Main documentation: {readme_path}")
if docs_exists:
    lines.append(f"- Additional docs root: {docs_path}")
    lines.extend(f"- Installed documentation file: {path}" for path in installed_docs)
if examples_exists:
    lines.append(f"- Examples root: {examples_path}")
if docs_exists and examples_exists:
    lines.append(
        "- Resolve docs/... under the docs root and examples/... under the examples root, not cwd."
    )
elif docs_exists:
    lines.append("- Resolve docs/... under the docs root, not cwd.")
elif examples_exists:
    lines.append("- Resolve examples/... under the examples root, not cwd.")
if docs_exists:
    lines.append(
        "- For Travis234 work, read the listed Markdown completely and follow its links; use only listed files "
        "and never assume an unlisted topic file exists."
    )
elif examples_exists:
    lines.append("- For Travis234 SDK or extension work, consult the installed examples root.")
return "\n".join(lines)
```

The branches must preserve these conditions:

- Omit the README line when missing.
- Omit the docs root, installed files, and docs-specific wording when the docs directory is missing.
- Omit the examples line and examples mapping when the examples directory is missing.
- Keep one exact installed-file line per actual Markdown file so the model never invents filenames.

- [ ] **Step 2: Update existing documentation assertions**

Update tests to expect `Additional docs root:` and `Examples root:`. Retain:

- `test_default_system_prompt_only_advertises_installed_documentation`.
- `test_default_system_prompt_never_names_missing_documentation_files`.
- Absence of missing docs-root resolution guidance.
- Presence of every actual installed Markdown file.
- The complete-read and cross-reference requirement.

- [ ] **Step 3: Run controlled documentation tests and verify GREEN**

```bash
uv run --no-sync python -m pytest -q tests/test_coding_resources_and_services.py -k \
  'documentation or installed_documentation or missing_documentation'
```

- [ ] **Step 4: Verify custom prompts, project instructions, and lazy skills remain unchanged**

```bash
uv run --no-sync python -m pytest -q tests/test_coding_resources_and_services.py -k \
  'custom_prompt or resource_loader_discovers_context or packaged_builtin_skills or no_skills or user_skill_overrides'
```

Expected: custom system prompts still receive project context, lazy skill metadata only when read is active, and no skill body enters the default prompt.

- [ ] **Step 5: Checkpoint the task without committing**

```bash
git diff --check
git diff -- travis/coding_agent/system_prompt.py tests/test_coding_resources_and_services.py
```

---

### Task 4: Record Before/After Prompt Quality and Guard Context-Envelope Semantics

**Files:**
- Create during execution: `docs/verification/main-coding-system-prompt-compactness.md`
- Test: `tests/test_context_estimate.py`
- Test: `tests/test_reference_runtime_contract.py`

**Interfaces:**
- Consumes: the final prompt builder and active tool definitions after the process-quality and prompt-deduplication plans.
- Produces: reproducible character/token measurements and evidence that only static text/schema sizes changed.

- [ ] **Step 1: Measure four deterministic variants in memory**

Using `build_system_prompt()` and `estimate_text_tokens()`, record:

1. Core default with read, Bash, process, tmux, edit, write, spawn, and wait.
2. The same prompt without process.
3. The same prompt without subagent tools.
4. The root repository prompt with root `AGENTS.md` and only the two packaged skills.

For tool contracts, serialize only `name`, `description`, and `parameters` with compact sorted JSON. Do not serialize callbacks or user configuration.

- [ ] **Step 2: Write the verification record**

`docs/verification/main-coding-system-prompt-compactness.md` must contain:

- Date and worktree commit identity.
- The pre-change measurements already recorded in this plan.
- Final characters, estimated tokens, and lines for all four variants.
- Final process schema and complete default tool-contract estimates.
- Process and subagent deltas.
- A semantic checklist showing preserved engineering, evidence, active-tool, project-context, documentation, and lazy-skill behavior.
- Explicit statement that no loop, session, compaction, persistence, provider, or lifecycle module changed.

- [ ] **Step 3: Run envelope and reference contracts**

```bash
uv run --no-sync python -m pytest -q \
  tests/test_context_estimate.py \
  tests/test_reference_runtime_contract.py
```

Expected: static system/tool estimates are lower, while component addition, provider-real usage, estimated-trailing replay, and no-broad-recovery-policy assertions pass unchanged.

- [ ] **Step 4: Review the actual assembled prompt once**

Print the clean core prompt with line numbers, never the root `.env` or user global prompt files. Confirm:

- One engineering/evidence authority.
- One high-level subagent authority.
- No placeholder process JSON in the system prompt.
- No contradictory PTY/process/tmux routing.
- Exact installed documentation paths only.
- Project context remains after the generic policy.
- Lazy skill metadata remains after project context and before date/cwd.

If any semantic conflict remains, add a failing string/behavior regression before changing it.

---

### Task 5: Run Independent Automated and Packaging Qualification

**Files:**
- No planned production changes.
- Any failure-induced fix starts a new focused regression-first cycle.

**Interfaces:**
- Consumes: Tasks 1-4 and the completed process-quality implementation.
- Produces: fresh full-suite, package, parity, and container evidence without release mutation.

- [ ] **Step 1: Audit file scope**

```bash
git status --short
git diff --check
git diff --name-only
```

For this plan, expected production changes are limited to `system_prompt.py` and `session_subagents.py`. The process plan has its own separately attributable `process.py` and `bash.py` changes. No generic-loop, AgentHarness, session, compaction, provider, or persistence file may be added to this plan's diff.

- [ ] **Step 2: Run the focused prompt/subagent/resource suite**

```bash
uv run --no-sync python -m pytest -q \
  tests/test_coding_tools_and_subagents.py \
  tests/test_coding_resources_and_services.py \
  tests/test_process_tools.py \
  tests/test_reference_runtime_contract.py \
  tests/test_context_estimate.py
```

- [ ] **Step 3: Run the complete Python and adapter suites**

```bash
uv run --no-sync python -m pytest -q
uv run --project packages/travis234-mcp-adapter pytest \
  packages/travis234-mcp-adapter/tests -q
```

- [ ] **Step 4: Run npm, parity, and package gates**

```bash
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
uv run --no-sync python scripts/verify_acceptance.py --parity-json
```

Build root and adapter wheel/sdist artifacts into one explicit `mktemp -d` directory, run Twine against all four artifacts, and install the exact wheels into clean Python 3.13 before importing both packages and checking CLI help.

- [ ] **Step 5: Run the unprivileged release-container smoke**

```bash
docker build --no-cache -f Dockerfile.release -t travis234:prompt-compactness .
python3 evals/container_smoke.py --image travis234:prompt-compactness
```

- [ ] **Step 6: Use the process plan's installed Minimax M3 TUI as the live combined gate**

If both plans are executed in one approved worktree, run Task 7 of the process-quality plan only after this prompt-deduplication plan is GREEN. That twelve-prompt run then qualifies the exact combined installed wheel. Do not run a second redundant live session unless the first reveals a subagent/documentation-specific regression.

- [ ] **Step 7: Final audit without GitOps**

```bash
git diff --check
git status --short
git diff --stat
```

Report exact counts, measurements, TUI results, and remaining dirty/protected files. Do not commit, push, publish, or release.

---

## Completion Criteria

- Subagent policy adds at most 1,150 characters relative to the same prompt without subagent tools.
- The phrase `two or more independent, bounded` appears only once in the assembled default prompt.
- The prompt explicitly says project delegation restrictions constrain generic automatic delegation.
- Explicit subagent opt-out still removes every subagent tool for the provider turn.
- `spawn_subagent` retains only operation-specific prompt guidance; schemas and execution behavior remain unchanged.
- The current installed documentation section is no more than nine lines under the controlled fixture and still lists every actual Markdown file.
- Missing docs/files remain absent from the prompt; no topic filename is invented.
- Project instructions, custom prompts, active-tool filtering, lazy skill metadata, and skill precedence remain unchanged.
- The senior-engineering evidence/non-fabrication contract remains intact.
- No generic loop, AgentHarness, session, compaction, context-estimation, provider, tool schema, or lifecycle behavior changes.
- Focused/full Python, adapter, npm, parity, builds, Twine, exact-wheel, container, and the combined twelve-prompt Minimax M3 gates pass with fresh evidence.

## Execution Gate

This is a separately reviewable follow-up, not part of the microscopic process-schema bug fix. Execute it inline only after the process plan is GREEN and only after the user approves implementation of this broader prompt cleanup.
