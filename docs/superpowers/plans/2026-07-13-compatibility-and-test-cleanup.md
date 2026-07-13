# Compatibility, Dependency, Duplication, and Test Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove unused runtime dependencies, camelCase compatibility surfaces, compatibility-only modules, exact helper duplicates, and oversized test owners without reducing behavior coverage.

**Architecture:** Encode measurable repository hygiene in one reusable checker, remove compatibility at callers before deleting aliases, consolidate duplicates by domain owner, and split tests mechanically by subsystem while preserving collection count.

**Tech Stack:** Python 3.13, AST analysis, pytest, Setuptools metadata.

## Global Constraints

- Direct runtime dependencies are only `httpx`, `jsonschema`, and `psutil` unless a new direct runtime import is proven by the checker.
- `playwright` remains optional and documented for browser development only.
- Runtime Python contains zero lowercase-starting camelCase function names or alias assignments.
- Serialized protocol dictionary keys may remain camelCase only at serialization/deserialization boundaries.
- Runtime Python contains zero normalized duplicate-function groups with at least three statements or four physical source lines.
- Every `tests/**/*.py` file is at most 2,000 physical lines.
- Full collected test count may increase but may not decrease after splitting.
- Compatibility removals are hard cutover; no deprecation aliases are retained.

---

### Task 1: Add repository hygiene checker and failing gates

**Files:**
- Create: `scripts/check_repository_hygiene.py`
- Create: `tests/architecture/test_repository_hygiene.py`

**Interfaces:**
- Produces `HygieneReport(unused_dependencies, camel_symbols, duplicate_groups, oversized_tests, forbidden_compatibility)`.
- CLI exits zero only when every report field is empty.

- [ ] **Step 1: Implement report data and AST discovery**

```python
@dataclass(frozen=True)
class HygieneReport:
    unused_dependencies: tuple[str, ...]
    camel_symbols: tuple[str, ...]
    duplicate_groups: tuple[tuple[str, ...], ...]
    oversized_tests: tuple[str, ...]
    forbidden_compatibility: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not any(dataclasses.astuple(self))
```

The checker:

1. reads `[project].dependencies` from `pyproject.toml`;
2. maps distribution names to import roots (`jsonschema`, `httpx`, `psutil`);
3. walks imports under `travis/`;
4. finds function/assignment names matching `^[a-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*$`;
5. normalizes function AST bodies after removing names/location metadata and groups duplicates with at least three statements or four source lines;
6. counts physical lines under `tests/`;
7. searches for forbidden argument aliases and compatibility module names.

- [ ] **Step 2: Add failing assertions**

```python
def test_repository_hygiene_is_clean() -> None:
    report = inspect_repository(ROOT)
    assert report.unused_dependencies == ()
    assert report.camel_symbols == ()
    assert report.duplicate_groups == ()
    assert report.oversized_tests == ()
    assert report.forbidden_compatibility == ()
```

- [ ] **Step 3: Run the gate to verify red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/architecture/test_repository_hygiene.py -q`

Expected: FAIL reporting unused `langgraph`, `openrouter`, `pydantic`; hundreds of
camel symbols; the known duplicate groups; and the three oversized baseline test owners.

- [ ] **Step 4: Commit the red hygiene gate**

```bash
git add scripts/check_repository_hygiene.py tests/architecture/test_repository_hygiene.py
git commit -m "test: define repository hygiene gates"
```

### Task 2: Remove unused direct dependencies

**Files:**
- Modify: `pyproject.toml`
- Modify: `tests/test_pyproject_dependencies.py`
- Modify: `README.md`

**Interfaces:**
- Produces exact direct runtime dependency set `{httpx, jsonschema, psutil}` and optional browser extra `{playwright}`.

- [ ] **Step 1: Add exact dependency contract**

```python
def test_direct_runtime_dependencies_match_imported_owners() -> None:
    metadata = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    names = {Requirement(item).name for item in metadata["project"]["dependencies"]}
    assert names == {"httpx", "jsonschema", "psutil"}
    browser = {Requirement(item).name for item in metadata["project"]["optional-dependencies"]["browser"]}
    assert browser == {"playwright"}
```

- [ ] **Step 2: Run dependency tests red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_pyproject_dependencies.py -q`

Expected: FAIL with the three unused runtime dependencies.

- [ ] **Step 3: Remove the unused declarations**

Set runtime dependencies to:

```toml
dependencies = [
  "httpx>=0.27",
  "jsonschema>=4.23,<5",
  "psutil>=6.1",
]
```

Retain `playwright>=1.59` only in `[project.optional-dependencies].browser` and
document that core runtime/tests do not require it.

- [ ] **Step 4: Run dependency and packaging tests green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_pyproject_dependencies.py tests/test_installed_metadata.py -q`

Expected: PASS.

- [ ] **Step 5: Commit dependency cleanup**

```bash
git add pyproject.toml tests/test_pyproject_dependencies.py README.md
git commit -m "chore: remove unused runtime dependencies"
```

### Task 3: Remove camelCase and compatibility-only APIs

**Files:**
- Modify: all `travis/**/*.py` callers/definitions reported by the hygiene checker.
- Modify: affected `tests/**/*.py`.
- Delete: compatibility-only modules proven to have no canonical consumers.
- Modify: `travis/coding_agent/tools/process.py`
- Modify: `travis/coding_agent/session_subagents.py`

**Interfaces:**
- Canonical Python APIs and process arguments use snake_case only.
- Canonical subagent shell tool is `bash`; no `run` alias.

- [ ] **Step 1: Add failing canonical-argument tests**

```python
@pytest.mark.parametrize("alias", ["sessionId", "nextCursor", "yieldTimeMs", "waitTimeMs", "maxBytes"])
def test_process_tool_rejects_compatibility_arguments(alias: str, process_tool) -> None:
    result = process_tool.execute("call", {"action": "poll", alias: "legacy"})
    assert result.is_error is True
    assert "unknown argument" in result.content


def test_internal_subagent_exposes_only_canonical_bash_tool(subagent_tools) -> None:
    names = {tool.name for tool in subagent_tools}
    assert "bash" in names
    assert "run" not in names
```

- [ ] **Step 2: Run canonical tests and hygiene gate red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_process_tools.py tests/test_subagents.py tests/architecture/test_repository_hygiene.py -q`

Expected: FAIL because aliases are accepted/exported and camel symbols remain.

- [ ] **Step 3: Convert callers before deleting definitions**

For each checker-reported camel symbol:

1. locate every internal/test caller with `rg`;
2. replace it with the existing or newly named snake_case API;
3. run the owning test node;
4. delete the camel definition/assignment/export;
5. rerun the owning file.

Do not rename serialized JSON keys in the same step; serializers explicitly map
snake_case fields to protocol keys.

- [ ] **Step 4: Remove exact compatibility surfaces**

Delete `AgentSession._install_subagent_tool_aliases()` and its `run` alias.
Delete `_PROCESS_ARGUMENT_ALIASES` and reject its five former keys. Remove config,
AI helper, key/util, component, model-registry, settings, and tool-factory camel
exports plus tests that existed only to require them. Delete a compatibility-only
module only after `rg` proves zero canonical imports and its behavior owner has
direct coverage.

- [ ] **Step 5: Run owner suites after alias removal**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_process_tools.py tests/test_subagents.py tests/test_coding_agent.py tests/test_tui.py tests/test_ai_models.py tests/test_model_registry.py -q`

Expected: PASS.

- [ ] **Step 6: Run camel/compatibility gate green**

Run: `PYTHONPATH=. .venv/bin/python scripts/check_repository_hygiene.py --only camel_symbols,forbidden_compatibility`

Expected: exit 0 with both fields empty.

- [ ] **Step 7: Commit compatibility hard cutover**

```bash
git add -A travis tests
git commit -m "refactor: remove compatibility-only APIs"
```

### Task 4: Consolidate exact helper duplicates by owner

**Files:**
- Create: `travis/coding_agent/message_utils.py`
- Create: `travis/coding_agent/auth_utils.py`
- Create: `travis/coding_agent/tools/shared.py`
- Modify: duplicate owners reported below.
- Modify: related tests.

**Interfaces:**
- `message_utils.last_assistant_message`, `bash_execution_to_text`, `settings_value`.
- `auth_utils.oauth_is_expired`.
- `tools.shared.context_value`, `check_aborted`, `to_posix_path`, `file_content_metadata`, `line_count`.

- [ ] **Step 1: Write focused shared-helper tests**

```python
def test_last_assistant_message_returns_last_matching_role() -> None:
    messages = [user("one"), assistant("first"), user("two"), assistant("last")]
    assert last_assistant_message(messages).content[0].text == "last"


def test_file_content_metadata_is_identical_for_bytes_and_text() -> None:
    assert file_content_metadata("a\nb\n") == file_content_metadata(b"a\nb\n")


def test_check_aborted_raises_canonical_error() -> None:
    signal = FakeSignal(aborted=True)
    with pytest.raises(RuntimeError, match="Operation aborted"):
        check_aborted(signal)
```

- [ ] **Step 2: Run helper tests red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/coding_agent/test_message_utils.py tests/coding_agent/test_auth_utils.py tests/tools/test_shared.py -q`

Expected: FAIL because shared owners do not exist.

- [ ] **Step 3: Move exact implementations once**

Consolidate these baseline duplicates:

- `bash_execution_to_text`: session/branch summarization;
- `file_content_metadata`: write/edit;
- `settings_value`: session/resource loader;
- `oauth_is_expired`: auth storage/model auth;
- `last_assistant_message`: app/session;
- `context_value`: write/edit/ls/read;
- `line_count`: output spool/process completions/process output;
- `check_aborted`: grep/ls/find/read;
- `to_posix_path`: grep/find/read/path utils.

All former owners import the one domain helper. Do not create forwarding wrappers.

- [ ] **Step 4: Run affected owner suites green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/coding_agent tests/tools tests/test_auth_storage_hardening.py tests/test_process_output.py tests/test_process_completions.py tests/test_app_integration.py -q`

Expected: PASS.

- [ ] **Step 5: Run duplicate gate green**

Run: `PYTHONPATH=. .venv/bin/python scripts/check_repository_hygiene.py --only duplicate_groups`

Expected: exit 0 with no qualifying normalized duplicate group.

- [ ] **Step 6: Commit duplicate consolidation**

```bash
git add travis/coding_agent tests
git commit -m "refactor: consolidate duplicated runtime helpers"
```

### Task 5: Split oversized tests by subsystem

**Files:**
- Split/delete: `tests/test_coding_agent.py`
- Split/delete: `tests/test_tui.py`
- Split/delete: `tests/test_ai_travis_env_provider.py`
- Create: focused owner files under `tests/coding_agent/`, `tests/tui/`, and `tests/ai/providers/`.

**Interfaces:**
- Produces test files at most 2,000 lines and collection count at least the count recorded before splitting.

- [ ] **Step 1: Record pre-split collection inventory**

Run: `PYTHONPATH=. .venv/bin/python -m pytest --collect-only -q > artifacts/pre-split-collection.txt`

Expected: command exits 0. Record the final collected count in
`artifacts/pre-split-count.txt`; artifacts remain ignored.

- [ ] **Step 2: Move coding-agent tests by owner**

Create focused modules for config/resources, auth/models, extensions, tools,
session persistence/tree/export, compaction adapter, subagents, policy/guardrails,
and runtime events. Move fixtures to `tests/coding_agent/conftest.py` only when at
least two owner files consume them. Preserve every test function body and name.

- [ ] **Step 3: Move TUI tests by owner**

Create focused modules for editor/components, terminal/rendering, interactive
dispatch, process commands, session commands, model/auth dialogs, extensions,
SIGINT/shutdown, and footer/status. Preserve parameter IDs and golden values.

- [ ] **Step 4: Move provider tests by owner**

Create focused modules for message translation, request construction, chat
stream, Responses stream, Anthropic stream, auth/runtime selection, partial JSON,
and provider errors. Preserve byte fixtures and exact event/error assertions.

- [ ] **Step 5: Verify collection identity**

Run: `PYTHONPATH=. .venv/bin/python -m pytest --collect-only -q > artifacts/post-split-collection.txt`

Expected: collected count is not lower than `artifacts/pre-split-count.txt`, and
every pre-split node name appears once after applying the documented owner-path
mapping.

- [ ] **Step 6: Run split owner suites and line gate**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/coding_agent tests/tui tests/ai/providers -q`

Expected: PASS.

Run: `PYTHONPATH=. .venv/bin/python scripts/check_repository_hygiene.py --only oversized_tests`

Expected: exit 0; every test file is at most 2,000 lines.

- [ ] **Step 7: Commit test ownership split**

```bash
git add -A tests
git commit -m "test: split suites by subsystem ownership"
```

### Task 6: Prove complete cleanup

**Files:**
- Create: `docs/verification/repository-hygiene.md`

**Interfaces:**
- Produces current dependency/camel/duplicate/test-size/full-suite evidence.

- [ ] **Step 1: Run all hygiene gates**

Run: `PYTHONPATH=. .venv/bin/python scripts/check_repository_hygiene.py`

Expected: exit 0 with an empty report.

- [ ] **Step 2: Run complete collection and test suites**

Run: `PYTHONPATH=. .venv/bin/python -m pytest --collect-only -q`

Expected: count is at least the pre-cleanup baseline.

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests -q`

Expected: PASS.

- [ ] **Step 3: Record evidence and commit**

Record exact dependency list, zero-count hygiene fields, maximum test-file size,
collected count, passed count, and commands in `docs/verification/repository-hygiene.md`.

```bash
git add docs/verification/repository-hygiene.md
git commit -m "docs: record repository hygiene verification"
```
