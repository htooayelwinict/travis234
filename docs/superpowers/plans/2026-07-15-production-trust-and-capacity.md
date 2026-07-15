# Production Trust and Route Capacity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Do not use subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent unapproved project code execution and make every selected model use the actual provider-route context capacity.

**Architecture:** Add a fail-closed trust control plane and make resource loading a two-pass operation. Keep model metadata generation in the provider control plane, but use Pi's route-specific OpenRouter precedence.

**Tech Stack:** Python 3.13, pytest, JSON trust store, existing file locks/settings manager, argparse, pinned Pi characterization fixtures.

## Global Constraints

- State remains under `~/.travis234`; the trust store is `<agent_dir>/trust.json`.
- Global/user resources remain trusted; project behavior-changing resources require a resolved decision.
- Context files remain readable before trust unless context loading is explicitly disabled.
- Non-interactive execution never prompts and defaults to untrusted.
- Existing explicit `project_trusted=True|False` SDK tests remain supported as process overrides.
- Do not perform state-changing Git operations.

---

### Task 1: Trust store and resource detection

**Files:**
- Create: `travis/coding_agent/project_trust.py`
- Modify: `travis/coding_agent/__init__.py`
- Test: `tests/test_project_trust.py`

**Interfaces:**
- Produces: `ProjectTrustStore(agent_dir: str | Path)`
- Produces: `ProjectTrustStore.get(cwd: str | Path) -> bool | None`
- Produces: `ProjectTrustStore.set(cwd: str | Path, decision: bool | None) -> None`
- Produces: `ProjectTrustStore.set_many(updates: Sequence[ProjectTrustUpdate]) -> None`
- Produces: `has_trust_requiring_project_resources(cwd: str | Path) -> bool`
- Produces: `get_project_trust_options(cwd: str | Path, include_session_only: bool) -> tuple[ProjectTrustOption, ...]`

- [ ] **Step 1: Write failing path and decision tests**

```python
def test_trust_store_uses_nearest_parent_and_child_override(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    parent = tmp_path / "work"
    child = parent / "repo"
    child.mkdir(parents=True)
    store = ProjectTrustStore(agent_dir)

    store.set(parent, True)
    assert store.get(child) is True

    store.set(child, False)
    assert store.get(child) is False

    store.set(child, None)
    assert store.get(child) is True


def test_malformed_trust_store_fails_closed(tmp_path: Path) -> None:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "trust.json").write_text('["invalid"]', encoding="utf-8")
    store = ProjectTrustStore(agent_dir)

    with pytest.raises(ProjectTrustError, match="expected an object"):
        store.get(tmp_path / "repo")
```

- [ ] **Step 2: Run tests to verify the module is missing**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_project_trust.py
```

Expected: collection fails because `travis.coding_agent.project_trust` does not exist.

- [ ] **Step 3: Implement normalized locked storage**

Implement these exact public types and signatures:

```python
DefaultProjectTrust = Literal["ask", "always", "never"]
ProjectTrustDecision = bool | None


@dataclass(frozen=True)
class ProjectTrustUpdate:
    path: str
    decision: ProjectTrustDecision


@dataclass(frozen=True)
class ProjectTrustOption:
    label: str
    trusted: bool
    updates: tuple[ProjectTrustUpdate, ...]
    saved_path: str | None = None


class ProjectTrustError(RuntimeError):
    pass


class ProjectTrustStore:
    def __init__(self, agent_dir: str | Path) -> None:
        self.path = Path(agent_dir).expanduser().resolve() / "trust.json"

    def get(self, cwd: str | Path) -> ProjectTrustDecision:
        entry = self.get_entry(cwd)
        return entry.decision if entry is not None else None

    def get_entry(self, cwd: str | Path) -> ProjectTrustUpdate | None:
        with SessionFileLock(self.path):
            data = self._read_unlocked()
        current = Path(cwd).expanduser().resolve()
        while True:
            value = data.get(str(current))
            if value is True or value is False:
                return ProjectTrustUpdate(str(current), value)
            if current.parent == current:
                return None
            current = current.parent

    def set(self, cwd: str | Path, decision: ProjectTrustDecision) -> None:
        self.set_many((ProjectTrustUpdate(str(cwd), decision),))

    def set_many(self, updates: Sequence[ProjectTrustUpdate]) -> None:
        with SessionFileLock(self.path):
            data = self._read_unlocked()
            for update in updates:
                key = str(Path(update.path).expanduser().resolve())
                if update.decision is None:
                    data.pop(key, None)
                else:
                    data[key] = update.decision
            ordered = {key: data[key] for key in sorted(data)}
            atomic_replace_text(self.path, json.dumps(ordered, indent=2) + "\n")

    def _read_unlocked(self) -> dict[str, bool | None]:
        if not self.path.exists():
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ProjectTrustError(f"Failed to read trust store {self.path}: {error}") from error
        if not isinstance(value, dict):
            raise ProjectTrustError(f"Invalid trust store {self.path}: expected an object")
        for key, decision in value.items():
            if not isinstance(key, str) or decision not in (True, False, None):
                raise ProjectTrustError(
                    f"Invalid trust store {self.path}: decisions must be true, false, or null"
                )
        return value
```

Use `SessionFileLock` and `atomic_replace_text` from their existing Travis owners. Validate every JSON value as `true`, `false`, or `null`; write sorted keys with a trailing newline; use resolved canonical paths.

- [ ] **Step 4: Add failing resource-detection tests**

```python
@pytest.mark.parametrize(
    "relative",
    [
        ".travis234/settings.json",
        ".travis234/extensions/example.py",
        ".travis234/skills/demo/SKILL.md",
        ".travis234/prompts/review.md",
        ".travis234/themes/night.json",
        ".travis234/SYSTEM.md",
        ".travis234/APPEND_SYSTEM.md",
        ".agents/skills/demo/SKILL.md",
    ],
)
def test_project_resource_requires_trust(tmp_path: Path, relative: str) -> None:
    candidate = tmp_path / relative
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text("resource", encoding="utf-8")
    assert has_trust_requiring_project_resources(tmp_path) is True
```

- [ ] **Step 5: Implement resource detection and trust choices**

Use the pinned Pi resource list, replacing `.pi` with `.travis234`. Ignore `~/.agents/skills` as a user-global resource. Return project, parent, session-only, and decline choices in stable order.

- [ ] **Step 6: Run focused tests and review the persisted file**

```bash
.venv/bin/python -m pytest -q tests/test_project_trust.py
```

Expected: all tests pass; tests prove sorted writes, nearest-parent lookup, removal with `None`, malformed-file rejection, and resource detection.

### Task 2: Fail-closed resolution and two-pass loading

**Files:**
- Modify: `travis/coding_agent/project_trust.py`
- Modify: `travis/coding_agent/resource_loader.py`
- Modify: `travis/coding_agent/settings_manager.py`
- Modify: `travis/coding_agent/extensions.py`
- Modify: `travis/coding_agent/session_extensions.py`
- Modify: `travis/coding_agent/agent_session_services.py`
- Test: `tests/test_project_trust.py`
- Test: `tests/test_extension_loading_and_reload.py`

**Interfaces:**
- Consumes: `ProjectTrustStore`, resource detection, `SettingsManager.get_default_project_trust()`
- Produces: `ProjectTrustContext(has_ui: bool, select: Callable | None)`
- Produces: `async resolve_project_trust(...) -> bool`
- Produces: `ExtensionRunner.async_emit_project_trust(event, context) -> dict[str, object] | None`
- Produces: `DefaultResourceLoader.reload(options: Mapping[str, object] | None = None) -> None`

- [ ] **Step 1: Write failing resolution-precedence tests**

```python
@pytest.mark.asyncio
async def test_no_ui_unknown_project_fails_closed(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    extension = project / ".travis234" / "extensions" / "unsafe.py"
    extension.parent.mkdir(parents=True)
    extension.write_text("raise RuntimeError('executed')", encoding="utf-8")

    trusted = await resolve_project_trust(
        cwd=project,
        trust_store=ProjectTrustStore(tmp_path / "agent"),
        context=ProjectTrustContext(has_ui=False, select=None),
        default_project_trust="ask",
    )

    assert trusted is False


@pytest.mark.asyncio
async def test_override_precedes_saved_decision(tmp_path: Path) -> None:
    store = ProjectTrustStore(tmp_path / "agent")
    store.set(tmp_path / "repo", False)
    assert await resolve_project_trust(
        cwd=tmp_path / "repo",
        trust_store=store,
        context=ProjectTrustContext(has_ui=False, select=None),
        trust_override=True,
    ) is True
```

- [ ] **Step 2: Implement deterministic resolution**

Implement:

```python
@dataclass(frozen=True)
class ProjectTrustContext:
    has_ui: bool
    select: Callable[[str, Sequence[str]], str | None] | None


async def resolve_project_trust(
    *,
    cwd: str | Path,
    trust_store: ProjectTrustStore,
    context: ProjectTrustContext,
    trust_override: bool | None = None,
    default_project_trust: DefaultProjectTrust = "ask",
    extension_runner: ExtensionRunner | None = None,
    on_extension_error: Callable[[str], None] | None = None,
) -> bool:
    resolved_cwd = str(Path(cwd).expanduser().resolve())
    if trust_override is not None:
        return trust_override
    if not has_trust_requiring_project_resources(resolved_cwd):
        return True
    if extension_runner is not None:
        try:
            result = await extension_runner.async_emit_project_trust(
                {"type": "project_trust", "cwd": resolved_cwd}, context
            )
        except Exception as error:
            if on_extension_error is not None:
                on_extension_error(str(error))
        else:
            if result is not None and result.get("trusted") in {"yes", "no"}:
                trusted = result["trusted"] == "yes"
                if result.get("remember") is True:
                    trust_store.set(resolved_cwd, trusted)
                return trusted
    saved = trust_store.get(resolved_cwd)
    if saved is not None:
        return saved
    if default_project_trust == "always":
        return True
    if default_project_trust == "never" or not context.has_ui or context.select is None:
        return False
    choices = get_project_trust_options(resolved_cwd, include_session_only=True)
    selected = context.select(
        f"Trust project folder?\n{resolved_cwd}", [choice.label for choice in choices]
    )
    choice = next((item for item in choices if item.label == selected), None)
    if choice is None:
        return False
    if choice.updates:
        trust_store.set_many(choice.updates)
    return choice.trusted
```

`async_emit_project_trust()` iterates handlers in extension load order; the first `yes` or `no` result wins and `undecided` continues. Handler failures are accumulated as diagnostics and do not trust the project.

- [ ] **Step 3: Write the execution-sentinel regression**

```python
def test_default_loader_never_executes_unknown_project_extension(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    sentinel = tmp_path / "executed"
    extension = project / ".travis234" / "extensions" / "unsafe.py"
    extension.parent.mkdir(parents=True)
    extension.write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('executed', encoding='utf-8')\n"
        "def extension(travis):\n"
        "    return None\n",
        encoding="utf-8",
    )

    loader = DefaultResourceLoader(cwd=str(project), agent_dir=str(tmp_path / "agent"))
    loader.reload({"projectTrustContext": ProjectTrustContext(False, None)})

    assert sentinel.exists() is False
    assert loader.project_trusted is False
```

- [ ] **Step 4: Make settings and package defaults fail closed**

Change the default in `SettingsManager.create()`/`from_storage()` from trusted to untrusted only when trust has not been resolved. Preserve explicit `projectTrusted=True` as an override. Represent unresolved separately from false during startup; do not silently coerce `None` to true.

- [ ] **Step 5: Implement the two-pass resource reload**

Add `load_project_trust_extensions()` that sets project trust false, reloads global settings, and loads global/user plus explicit CLI extensions. Then let `reload()` resolve trust before loading packages and all resources. Construct every `ExtensionRunner` with the loader's shared event bus.

- [ ] **Step 6: Bind extension context trust dynamically**

Replace the constant action with:

```python
"isProjectTrusted": lambda: bool(self._settings_manager.is_project_trusted()),
```

Use the actual owning settings manager field for the session. Add a test that flips trust and observes the updated result without recreating the context object.

- [ ] **Step 7: Run trust and loader tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_project_trust.py \
  tests/test_extension_loading_and_reload.py \
  tests/test_coding_resources_and_services.py -k 'trust or resource_loader or event_bus'
```

Expected: all selected tests pass and no untrusted sentinel exists.

### Task 3: CLI and interactive trust controls

**Files:**
- Modify: `travis/cli.py`
- Modify: `travis/app.py`
- Modify: `travis/tui/interactive_extensions.py`
- Modify: `travis/tui/interactive_session_commands.py`
- Modify: `travis/tui/interactive_view.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_tui_commands_and_extensions.py`

**Interfaces:**
- Consumes: trust resolver and `ProjectTrustStore`
- Produces: `args.project_trust_override: bool | None`
- Produces: `/trust` interactive command

- [ ] **Step 1: Write failing CLI argument tests**

```python
def test_cli_trust_flags_are_mutually_exclusive() -> None:
    with pytest.raises(SystemExit):
        main(["--approve", "--no-approve", "--no-session", "prompt"])


def test_noninteractive_unknown_project_does_not_execute_extension(tmp_path: Path, monkeypatch) -> None:
    sentinel = tmp_path / "executed"
    write_project_extension(tmp_path, sentinel)
    monkeypatch.setattr("travis.cli.CodingApp", FauxCodingApp)
    assert main(["--cwd", str(tmp_path), "--no-session", "prompt"]) == 0
    assert sentinel.exists() is False
```

- [ ] **Step 2: Add trust flags and persistent settings construction**

Add one mutually exclusive argparse group:

```python
trust_group = parser.add_mutually_exclusive_group()
trust_group.add_argument("-a", "--approve", dest="project_trust_override", action="store_const", const=True)
trust_group.add_argument("-na", "--no-approve", dest="project_trust_override", action="store_const", const=False)
```

Create a file-backed `SettingsManager` in CLI startup, pass it into `CodingApp`, and pass the override plus mode-aware trust context through resource-loader options.

- [ ] **Step 3: Add an interactive startup selector**

For the initial safety phase, use a mode-neutral callback with the exact Pi choice labels. Cancellation returns untrusted. Later TUI parity may replace its presentation without changing `resolve_project_trust()`.

- [ ] **Step 4: Implement `/trust`**

The command displays the current inherited or exact decision and allows project, parent, session-only, and decline choices. Persist selected updates through `ProjectTrustStore`. Match Pi behavior: tell the user to reload/restart before newly trusted project code runs; never execute it as a side effect of merely displaying status.

- [ ] **Step 5: Emit `project_trust` only from bootstrap extensions**

Test that a global extension can decide trust and a project extension cannot participate before trust. A remembered extension decision writes `trust.json`.

- [ ] **Step 6: Run CLI/TUI trust tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_cli.py -k trust \
  tests/test_tui_commands_and_extensions.py -k trust \
  tests/test_project_trust.py
```

Expected: all selected tests pass; non-interactive unknown projects remain untrusted.

### Task 4: OpenRouter route-capacity precedence

**Files:**
- Modify: `travis/ai/catalog_generation.py`
- Modify: `scripts/sync_builtin_model_catalog.py`
- Modify: `travis/ai/builtin_models.json`
- Test: `tests/test_catalog_generation.py`
- Test: `tests/test_reference_runtime_contract.py`

**Interfaces:**
- Produces: `apply_openrouter_capabilities(catalog, payload) -> tuple[catalog, changed_count]`
- Preserves: explicit runtime/environment model overrides after catalog loading

- [ ] **Step 1: Replace the locked-in divergence with a failing route test**

```python
def test_openrouter_uses_top_provider_context_and_output_limits() -> None:
    catalog = {
        "openrouter": {
            "xiaomi/mimo-v2.5": {"contextWindow": 1_048_576, "maxTokens": 131_072}
        }
    }
    payload = {
        "data": [{
            "id": "xiaomi/mimo-v2.5",
            "context_length": 1_048_576,
            "top_provider": {"context_length": 32_000, "max_completion_tokens": 4_096},
        }]
    }

    refreshed, changed = apply_openrouter_capabilities(catalog, payload)

    assert changed == 1
    assert refreshed["openrouter"]["xiaomi/mimo-v2.5"]["contextWindow"] == 32_000
    assert refreshed["openrouter"]["xiaomi/mimo-v2.5"]["maxTokens"] == 4_096
```

- [ ] **Step 2: Run the regression and confirm the wrong 1M value**

```bash
.venv/bin/python -m pytest -q tests/test_catalog_generation.py::test_openrouter_uses_top_provider_context_and_output_limits
```

Expected: FAIL because the current implementation uses model-level context length.

- [ ] **Step 3: Implement route-first precedence**

Use:

```python
top_provider = item.get("top_provider") if isinstance(item.get("top_provider"), dict) else {}
context_window = _positive_int(
    top_provider.get("context_length", top_provider.get("contextLength"))
)
if context_window is None:
    context_window = _positive_int(item.get("context_length", item.get("contextLength")))
max_tokens = _positive_int(
    top_provider.get("max_completion_tokens", top_provider.get("maxCompletionTokens"))
)
```

Reject a generated maximum output greater than or equal to the route window by retaining the last valid maximum output and emitting a generator diagnostic.

- [ ] **Step 4: Generate a pinned Pi parity fixture**

Compare Travis's OpenRouter records with `pi/packages/ai/src/providers/openrouter.models.ts` at the pinned commit. Store only IDs and normalized `contextWindow`/`maxTokens` in a test fixture; do not make the reference clone a runtime dependency.

- [ ] **Step 5: Refresh the generated catalog**

Run the repository's sync script against the same captured OpenRouter payload used to generate the parity fixture. Inspect the 40 previously divergent records; every remaining difference must be documented as an explicit Travis override.

- [ ] **Step 6: Run catalog and resolver tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_catalog_generation.py \
  tests/test_reference_runtime_contract.py -k 'openrouter or context_window or max_tokens' \
  tests/test_ai_model_resolver.py \
  tests/test_model_registry.py
```

Expected: all selected tests pass and MiMo v2.5 resolves to the route-specific capacity.

### Task 5: Production-safety acceptance gate

**Files:**
- Modify: `README.md`
- Modify: `docs/verification/acceptance-matrix.md`
- Modify: `scripts/verify_acceptance.py`
- Create: `evals/untrusted_repository_smoke.py`
- Test: `tests/test_eval_harness.py`
- Test: `tests/architecture/test_acceptance_matrix.py`

**Interfaces:**
- Consumes: completed trust and capacity behavior
- Produces: deterministic untrusted-repository smoke evidence

- [ ] **Step 1: Add a failing smoke-harness test**

```python
def test_untrusted_repository_smoke_never_creates_sentinel(tmp_path: Path) -> None:
    result = run_untrusted_repository_smoke(tmp_path)
    assert result.exit_code == 0
    assert result.project_trusted is False
    assert result.extension_executed is False
```

- [ ] **Step 2: Implement the offline smoke**

Create a temporary repository with project settings, extension, skill, prompt, theme, and system-prompt files. Launch a faux-provider non-interactive turn without trust flags. Assert global resources load, project Python does not execute, and the session completes.

- [ ] **Step 3: Add acceptance rows**

Add exact proving commands for project trust and route-capacity parity. Update README security documentation so opening an arbitrary repository is accurately described.

- [ ] **Step 4: Run the full Phase 1 gate**

```bash
.venv/bin/python -m pytest -q \
  tests/test_project_trust.py \
  tests/test_extension_loading_and_reload.py \
  tests/test_catalog_generation.py \
  tests/test_reference_runtime_contract.py \
  tests/test_eval_harness.py \
  tests/architecture/test_acceptance_matrix.py
.venv/bin/python -m evals.untrusted_repository_smoke
```

Expected: all tests pass and the smoke reports `extension_executed=false`.

- [ ] **Step 5: Review checkpoint without Git operations**

Inspect `git diff --check` and `git status --short` read-only. Record changed files and test output. Do not stage or commit.
