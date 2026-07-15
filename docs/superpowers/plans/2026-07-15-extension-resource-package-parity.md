# Python-Native Extension, Resource, and Package Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Do not use subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Match pinned Pi's extension lifecycle and resource workflows in a Python-native Travis runtime.

**Architecture:** Keep `ExtensionRunner` as the extension API owner, split resource parsing from orchestration, and add a trust-aware Python package manager. Pi defines behavior and event order; JavaScript execution is deliberately excluded.

**Status:** Complete on 2026-07-15. The full Phase 3 gate passes 156 tests.

**Tech Stack:** Python 3.13, pytest, asyncio, PyYAML 6, pathspec-style ignore matching implemented with existing filesystem utilities or one bounded dependency, subprocess Git/pip with credential stripping.

## Global Constraints

- Project extension/resource/package loading requires resolved trust.
- Global and explicit operator resources remain available before project trust.
- Python is the only native extension execution language.
- Event payloads match Pi semantically while using Python naming conventions internally.
- Package subprocesses never receive provider credentials.
- Do not perform state-changing Git operations in the Travis repository; tests may create temporary standalone Git repositories.

---

### Task 1: Complete extension event parity and shared event bus

**Files:**
- Modify: `travis/coding_agent/extensions.py`
- Modify: `travis/coding_agent/event_bus.py`
- Modify: `travis/coding_agent/resource_loader.py`
- Modify: `travis/coding_agent/session_events.py`
- Modify: `travis/coding_agent/session_extensions.py`
- Modify: `travis/coding_agent/session_models.py`
- Modify: `travis/coding_agent/session_persistence.py`
- Create: `tests/test_extension_event_parity.py`

**Interfaces:**
- Produces: `PINNED_PI_EXTENSION_EVENTS: tuple[str, ...]`
- Produces: missing emissions `project_trust`, `session_info_changed`, `model_select`, `thinking_level_select`
- Preserves: existing before-event cancellation and result-merging behavior

- [ ] **Step 1: Add a manifest parity test**

```python
PINNED_PI_EXTENSION_EVENTS = (
    "project_trust", "resources_discover", "session_start", "session_info_changed",
    "session_before_switch", "session_before_fork", "session_before_compact", "session_compact",
    "session_shutdown", "session_before_tree", "session_tree", "context",
    "before_provider_request", "before_provider_headers", "after_provider_response",
    "before_agent_start", "agent_start", "agent_end", "agent_settled", "turn_start", "turn_end",
    "message_start", "message_update", "message_end", "tool_execution_start",
    "tool_execution_update", "tool_execution_end", "model_select", "thinking_level_select",
    "tool_call", "tool_result", "user_bash", "input",
)


def test_extension_runner_declares_all_pinned_pi_events() -> None:
    assert set(ExtensionRunner.supported_event_types()) == set(PINNED_PI_EXTENSION_EVENTS)
```

- [ ] **Step 2: Emit the four missing events at their state owners**

Emit `session_info_changed` after durable session-name changes, `model_select` after a successful model bind, and `thinking_level_select` after a successful level change. `project_trust` remains bootstrap-only in the trust resolver. Include previous/new values and source where Pi does.

- [ ] **Step 3: Pass one event bus into every runner**

Change runner construction to:

```python
runtime = ExtensionRunner(cwd=self.cwd, event_bus=self.event_bus)
```

Ensure reload detaches old subscriptions before discarding the runner. Add two extensions in a test: one publishes on the shared bus and the other receives it before and after reload.

- [ ] **Step 4: Implement stable duplicate command names**

Keep registration identity separate from invocation name. Resolve collisions as `name`, `name:1`, `name:2` in source order. `get_registered_command()` accepts the resolved invocation name and never silently overwrites an earlier command.

- [ ] **Step 5: Run event and lifecycle tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_extension_event_parity.py \
  tests/test_extension_loading_and_reload.py \
  tests/test_coding_policy_and_extensions.py -k event \
  tests/test_coding_resources_and_services.py -k 'event_bus or command'
```

Expected: all selected tests pass and the manifest contains all 33 events.

### Task 2: Safe YAML frontmatter and ignored resource discovery

**Files:**
- Modify: `pyproject.toml`
- Create: `travis/coding_agent/skills.py`
- Create: `travis/coding_agent/prompt_templates.py`
- Modify: `travis/coding_agent/resource_loader.py`
- Create: `tests/test_resource_runtime_parity.py`

**Interfaces:**
- Produces: `parse_frontmatter(raw: str) -> tuple[dict[str, object], str]`
- Produces: `load_skills(paths, *, cwd, metadata_by_path) -> dict`
- Produces: `load_prompt_templates(paths, *, cwd, metadata_by_path) -> dict`

- [ ] **Step 1: Add PyYAML and write failing frontmatter tests**

Add runtime dependency:

```toml
"PyYAML>=6,<7",
```

Test quoted values, arrays, multiline descriptions, nested mappings, booleans, and malformed YAML diagnostics. Use `yaml.safe_load`; reject non-mapping frontmatter.

- [ ] **Step 2: Enforce skill validation**

```python
def validate_skill_metadata(name: str, description: str) -> tuple[str, ...]:
    errors: list[str] = []
    if not SKILL_NAME_PATTERN.fullmatch(name):
        errors.append("name must match the Pi skill-name contract")
    if len(description) > 1_024:
        errors.append("description must be at most 1024 characters")
    return tuple(errors)
```

Preserve existing valid-name behavior and return diagnostics rather than executing malformed resources.

- [ ] **Step 3: Implement ignore-file traversal**

For each discovery root, merge `.gitignore`, `.ignore`, and `.fdignore` patterns in that order. Apply directory patterns before descending. Always ignore `.git/`, caches, and package build outputs. Explicitly passed individual files remain explicit operator choices.

- [ ] **Step 4: Move parsing out of `resource_loader.py`**

Leave compatibility imports in `resource_loader.py` only until all internal callers use the new modules. The loader orchestrates and caches; skill/template modules own parsing and diagnostics.

- [ ] **Step 5: Run resource parsing tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_resource_runtime_parity.py -k 'yaml or ignore or validation' \
  tests/test_coding_resources_and_services.py -k 'skill or prompt'
```

Expected: all selected tests pass.

### Task 3: Prompt, skill-command, and theme runtime behavior

**Files:**
- Create: `travis/coding_agent/themes.py`
- Modify: `travis/coding_agent/prompt_templates.py`
- Modify: `travis/coding_agent/skills.py`
- Modify: `travis/coding_agent/session_turns.py`
- Modify: `travis/coding_agent/session_extensions.py`
- Modify: `travis/tui/interactive_extensions.py`
- Modify: `travis/tui/interactive_view.py`
- Test: `tests/test_resource_runtime_parity.py`
- Test: `tests/test_tui_commands_and_extensions.py`

**Interfaces:**
- Produces: `expand_prompt_template(text: str, templates: Sequence[PromptTemplate]) -> str`
- Produces: `skill_commands(skills: Sequence[Skill]) -> tuple[RegisteredCommand, ...]`
- Produces: `ThemeRegistry.register_many(themes: Sequence[Theme]) -> None`

- [ ] **Step 1: Write failing runtime-effect tests**

Test that `/review src/app.py` expands a loaded template with arguments before provider submission, `/skill:lint` injects the selected skill only when enabled, and `/reload` replaces template/skill/theme behavior in the next turn.

- [ ] **Step 2: Implement prompt expansion**

Only a command occupying the start of user input expands. Preserve unmatched input literally. Split arguments with shell-like quoting and expose `$ARGUMENTS` plus positional `$1` through `$9`. Internal prompts pass `expand_prompt_templates=False`.

- [ ] **Step 3: Generate skill commands**

When `SettingsManager.get_enable_skill_commands()` is true, register `/skill:<name>` commands with source metadata and argument hints. Collisions follow the extension command resolver rather than overwriting.

- [ ] **Step 4: Register themes with the active TUI**

Create a `ThemeRegistry` owned by interactive mode. Load built-in and discovered themes, preserve the active theme across reload when it still exists, and fall back with a diagnostic when removed. Connect extension `setTheme` to the registry.

- [ ] **Step 5: Correct `/reload` status**

Report separate extension, skill, prompt, and theme diagnostic counts. Only claim a resource reloaded after the active registries are updated.

- [ ] **Step 6: Run runtime resource tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_resource_runtime_parity.py \
  tests/test_tui_commands_and_extensions.py -k 'reload or theme or skill or prompt'
```

Expected: all selected tests pass and provider payloads show expansion effects.

### Task 4: Trust-aware Python package manager

**Files:**
- Create: `travis/coding_agent/package_manager.py`
- Modify: `travis/coding_agent/resource_loader.py`
- Modify: `travis/coding_agent/settings_manager.py`
- Create: `tests/test_package_manager.py`

**Interfaces:**
- Produces: `PackageSource`, `InstalledPackage`, `ResolvedPaths`
- Produces: `DefaultPackageManager.resolve() -> ResolvedPaths`
- Produces: `install`, `remove`, `update`, and `list_installed`

- [ ] **Step 1: Write failing source-parser tests**

```python
@pytest.mark.parametrize(
    ("source", "kind"),
    [("./local-extension", "local"), ("git+https://example.test/repo.git@v1", "git"), ("travis-demo==1.2.0", "python")],
)
def test_package_source_kinds(source: str, kind: str, tmp_path: Path) -> None:
    assert parse_package_source(source, cwd=tmp_path).kind == kind
```

- [ ] **Step 2: Define package records**

```python
PackageScope = Literal["global", "project", "temporary"]
PackageKind = Literal["local", "git", "python"]


@dataclass(frozen=True)
class PackageSource:
    raw: str
    kind: PackageKind
    location: str
    revision: str | None = None


@dataclass(frozen=True)
class InstalledPackage:
    source: PackageSource
    scope: PackageScope
    install_path: str
    version: str | None
```

- [ ] **Step 3: Implement transactional install roots**

Use `<agent_dir>/packages` for global packages and `<cwd>/.travis234/packages` for trusted project packages. Install into a sibling temporary directory, validate resource manifests and Python extension entry points, then atomically replace the target.

- [ ] **Step 4: Implement source operations**

- Local: validate and reference an explicit path without copying when temporary; copy atomically when persisted.
- Git: clone with argument arrays, optional exact revision, shallow fetch where valid, and no shell interpolation.
- Python: run `python -m pip install --target <temp> <normalized-spec>` with a sanitized environment.

Remove provider keys, OAuth tokens, dotenv secrets, and Travis worker/compression credentials from every package subprocess. Preserve only standard proxy/certificate variables and explicit package-index credentials supplied for the operation.

- [ ] **Step 5: Enforce trust on project scope**

Every project install/remove/update and project settings write calls one `assert_project_trusted_for_scope()` method. Global and temporary explicit operations remain operator-authorized.

- [ ] **Step 6: Implement configured-source resolution**

Read global and trusted project package sources from settings, resolve enabled resources, detect missing installs, and return diagnostics. Update checks compare pinned revision or installed distribution version; never auto-update during ordinary startup.

- [ ] **Step 7: Run package tests**

```bash
.venv/bin/python -m pytest -q tests/test_package_manager.py tests/test_coding_resources_and_services.py -k package
```

Expected: local/Git/Python fixtures pass, project operations fail when untrusted, and sanitized environments contain no provider credential names.

### Task 5: Package and resource CLI

**Files:**
- Create: `travis/coding_agent/package_cli.py`
- Modify: `travis/cli.py`
- Modify: `travis/tui/interactive_extensions.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_package_manager.py`

**Interfaces:**
- Consumes: `DefaultPackageManager`
- Produces: `install`, `remove`, `update`, `list`, and `config` command families

- [ ] **Step 1: Write CLI parsing and trust tests**

Test global and `--local` scopes, `--approve`/`--no-approve`, exact source preservation, update without implicit trust prompting, and actionable usage errors.

- [ ] **Step 2: Add package subcommands before agent startup**

Dispatch package commands without constructing `CodingApp`. Reuse the same trust resolver; `update` consults saved/default trust but never prompts. Project mutations require `--approve` or an applicable saved decision.

- [ ] **Step 3: Add TUI package commands**

Expose list/install/remove/update status with bounded background execution. Require confirmation before mutation and refresh active resources only after a successful operation.

- [ ] **Step 4: Run CLI and package tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_cli.py -k 'install or remove or update or package' \
  tests/test_package_manager.py \
  tests/test_tui_commands_and_extensions.py -k package
```

Expected: all selected tests pass.

### Task 6: Full Phase 3 verification

**Files:**
- Modify: `README.md`
- Modify: `travis/resources/docs/extensions.md`
- Modify: `docs/verification/acceptance-matrix.md`
- Test: `tests/test_pyproject_dependencies.py`

**Interfaces:**
- Consumes: complete Phase 3 behavior
- Produces: extension/resource/package acceptance evidence

- [ ] **Step 1: Document the Python-native compatibility boundary**

State that Pi behavior is targeted while JavaScript extension execution is not. Document trust, events, YAML, ignore files, templates, skill commands, themes, and package sources.

- [ ] **Step 2: Run the Phase 3 gate**

```bash
.venv/bin/python -m pytest -q \
  tests/test_extension_event_parity.py \
  tests/test_extension_loading_and_reload.py \
  tests/test_resource_runtime_parity.py \
  tests/test_package_manager.py \
  tests/test_coding_resources_and_services.py \
  tests/test_tui_commands_and_extensions.py \
  tests/test_pyproject_dependencies.py
```

Expected: all commands pass and the event manifest reports all 33 Pi events.

- [ ] **Step 3: Review checkpoint without Git operations**

Run `git diff --check`, inspect `git status --short`, and record evidence. Do not stage or commit.
