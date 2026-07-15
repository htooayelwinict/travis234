# Production-Safe Extension CLI Flags Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Subagents are not authorized for this repository task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose Python extension-defined boolean and string flags through the installed `travis234` CLI with typed parsing, trust-safe discovery, single factory execution, and process-local values that survive session replacement.

**Architecture:** Split startup into bootstrap, provisional, and final parsing around the existing pretrust/final resource-loader phases. Reuse the completed initial loader in `CodingApp`, centralize value validation in the extension layer, and reapply the immutable CLI value map whenever the app constructs a cwd-bound replacement session.

**Tech Stack:** Python 3.13, `argparse`, existing `DefaultResourceLoader`, `ExtensionRunner`, `CodingApp`, pytest, npm launcher tests, Python build tooling, Docker release smoke.

## Global Constraints

- Do not perform any Git operation until the user explicitly asks. This includes status, diff, add, commit, push, branch, merge, and worktree commands.
- Modify only the active Travis234 tree. Do not edit or import the local Pi, Hermes, or `appv231/` reference checkouts.
- Preserve `~/.travis234` state paths and existing session JSONL formats; add no migration or persisted flag state.
- Preserve core agent-loop ordering, iteration budgeting, compaction behavior, and bounded parallel execution.
- Add a failing regression test before each behavioral change.
- Extension factories must execute at most once per runtime construction.
- Unknown projects remain fail-closed; option-shaped input never authorizes project code.
- Extension flags are long-only, boolean or string, and process-local.
- Preserve current package subcommand behavior and current documented core CLI behavior.
- Keep JSON and RPC stdout machine-only; startup diagnostics go to stderr.
- Before completion, run focused and full Python tests, npm launcher tests, package builds, parity validation, and the relevant no-cache release-container smoke.

---

## File Responsibility Map

- `travis/coding_agent/extensions.py`: owns flag definitions, owner conflicts, shared value application, and validation errors.
- `travis/coding_agent/agent_session_services.py`: adapts SDK options to the shared extension-layer value helper.
- `travis/coding_agent/resource_loader.py`: exposes completion of a preloaded trust-safe extension runtime without executing factories twice.
- `travis/coding_agent/extension_cli.py`: new focused argparse adapter for validated extension schemas and exact-name value collection.
- `travis/cli.py`: owns staged CLI parsing, trust-mode classification, dynamic help, and forwarding the completed loader/value map.
- `travis/app.py`: owns the initial preloaded loader and reapplies process-local values to every replacement session.
- `tests/test_extension_cli.py`: new parser-schema unit tests.
- `tests/test_coding_policy_and_extensions.py`: extension runner value/conflict tests.
- `tests/test_coding_resources_and_services.py`: staged loader and SDK compatibility tests.
- `tests/test_cli_extension_flags.py`: new end-to-end CLI parsing, help, trust, and single-load tests.
- `tests/test_cli_runtime_controls.py`: `CodingApp` lifecycle and transactional replacement tests.
- `scripts/parity_contracts.py`: adds executable `pi.cli.extension_flags` evidence.
- `README.md`: documents registration, invocation, trust, and process-local behavior.
- `docs/verification/acceptance-matrix.md`: updates the Pi contract count from 77 to 78 after the new evidence exists.
- `evals/container_smoke.py`: verifies extension-aware help in the installed release container.
- `tests/test_release_workflow.py`: tests the extension-help smoke fixture without requiring Docker.

---

### Task 1: Centralize extension flag ownership and value application

**Files:**
- Modify: `travis/coding_agent/extensions.py:85-91,333-370,1216-1247`
- Modify: `travis/coding_agent/agent_session_services.py:16,98-108,557-591`
- Modify: `tests/test_coding_policy_and_extensions.py:465-490`
- Modify: `tests/test_coding_resources_and_services.py:1655-1688`

**Interfaces:**
- Produces: `ExtensionFlagConflict(name, first_extension_path, conflicting_extension_path)`
- Produces: `ExtensionFlagValidationError(diagnostics)`
- Produces: `ExtensionRunner.get_flag_conflicts() -> list[ExtensionFlagConflict]`
- Produces: `apply_extension_flag_values(runtime: ExtensionRunner, raw_values: object) -> list[dict[str, object]]`
- Preserves: SDK `extensionFlagValues` and `extension_flag_values` behavior

- [ ] **Step 1: Write failing owner-conflict and shared-helper tests**

Add the following coverage:

```python
def test_extension_runner_records_cross_owner_flag_conflicts(tmp_path: Path) -> None:
    from travis.coding_agent import DefaultResourceLoader

    def first(runner: ExtensionRunner) -> None:
        runner.register_flag("profile", {"type": "string", "description": "first"})

    def second(runner: ExtensionRunner) -> None:
        runner.register_flag("profile", {"type": "boolean", "description": "second"})

    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        extension_factories=[first, second],
    )
    loader.reload({"projectTrustOverride": False})
    runtime = loader.get_extensions()["runtime"]

    assert runtime.get_flags()["profile"].description == "first"
    assert runtime.get_flag_conflicts() == [
        ExtensionFlagConflict(
            name="profile",
            first_extension_path="<inline:1>",
            conflicting_extension_path="<inline:2>",
        )
    ]


def test_shared_extension_flag_value_helper_preserves_sdk_semantics() -> None:
    runner = ExtensionRunner()
    runner.register_flag("verbose", {"type": "boolean", "default": False})
    runner.register_flag("profile", {"type": "string", "default": "safe"})

    diagnostics = apply_extension_flag_values(
        runner,
        {"verbose": False, "profile": "security", "missing": True},
    )

    assert runner.get_flag("verbose") is True
    assert runner.get_flag("profile") == "security"
    assert diagnostics == [{"type": "error", "message": "Unknown option: --missing"}]
```

- [ ] **Step 2: Run the tests and verify the red state**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_coding_policy_and_extensions.py::test_extension_runner_records_cross_owner_flag_conflicts \
  tests/test_coding_policy_and_extensions.py::test_shared_extension_flag_value_helper_preserves_sdk_semantics
```

Expected: collection or assertion failure because the conflict type, accessor, and public helper do not exist.

- [ ] **Step 3: Add conflict tracking and the shared helper**

Add these extension-layer types and behavior:

```python
@dataclass(frozen=True)
class ExtensionFlagConflict:
    name: str
    first_extension_path: str
    conflicting_extension_path: str


class ExtensionFlagValidationError(ValueError):
    def __init__(self, diagnostics: list[dict[str, object]]) -> None:
        self.diagnostics = [dict(item) for item in diagnostics]
        super().__init__("; ".join(str(item.get("message", "invalid extension flag")) for item in diagnostics))
```

Initialize `self._flag_conflicts: list[ExtensionFlagConflict] = []` in `ExtensionRunner.__init__()`. Change `register_flag()` so a second owner records a conflict while preserving the first definition and default:

```python
existing = self._registered_flags.get(name)
if existing is not None:
    conflicting_path = self._loading_extension_path or "<python-extension>"
    if existing.extension_path != conflicting_path:
        conflict = ExtensionFlagConflict(name, existing.extension_path, conflicting_path)
        if conflict not in self._flag_conflicts:
            self._flag_conflicts.append(conflict)
    return
```

Add:

```python
def get_flag_conflicts(self) -> list[ExtensionFlagConflict]:
    return list(self._flag_conflicts)
```

Move the existing `_apply_extension_flag_values()` body from `agent_session_services.py` into `extensions.py` as `apply_extension_flag_values()`. Preserve its accepted mapping/iterable inputs, boolean-presence semantics, string-value requirement, and exact diagnostic text.

- [ ] **Step 4: Point SDK services at the shared helper**

Import `apply_extension_flag_values` alongside `ExtensionRunner` and replace the private call:

```python
diagnostics.extend(
    apply_extension_flag_values(
        runtime,
        options.get("extensionFlagValues") or options.get("extension_flag_values"),
    )
)
```

Delete the private duplicate helper from `agent_session_services.py`.

- [ ] **Step 5: Run focused compatibility tests**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_coding_policy_and_extensions.py -k 'flag' \
  tests/test_coding_resources_and_services.py -k 'flag or provider_and_flag_diagnostics'
```

Expected: all selected tests pass, including the existing SDK assertion that a supplied boolean `False` means the flag was present and becomes `True`.

- [ ] **Step 6: Review checkpoint without Git**

Read the four touched sections directly. Confirm the first registration/default still wins, same-owner duplicate behavior remains compatible, and `agent_session_services.py` contains no second implementation of flag application.

---

### Task 2: Expose staged resource-loader completion

**Files:**
- Modify: `travis/coding_agent/resource_loader.py:254-309`
- Modify: `tests/test_coding_resources_and_services.py:1614-1688`

**Interfaces:**
- Consumes: existing `load_project_trust_extensions() -> dict[str, object]`
- Produces: `DefaultResourceLoader.complete_reload(options=None, *, pretrust_extensions=None) -> None`
- Preserves: `DefaultResourceLoader.reload(options=None) -> None`

- [ ] **Step 1: Write the failing single-execution test**

```python
def test_staged_resource_reload_reuses_pretrust_runtime_without_reexecuting_factories(
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def extension_factory(runner: ExtensionRunner) -> None:
        calls.append("factory")
        runner.register_flag("profile", {"type": "string"})

    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        extension_factories=[extension_factory],
    )

    pretrust = loader.load_project_trust_extensions()
    pretrust_runtime = pretrust["runtime"]
    loader.complete_reload(
        {"projectTrustOverride": False},
        pretrust_extensions=pretrust,
    )

    assert calls == ["factory"]
    assert loader.get_extensions()["runtime"] is pretrust_runtime
    assert "profile" in pretrust_runtime.get_flags()
```

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_coding_resources_and_services.py::test_staged_resource_reload_reuses_pretrust_runtime_without_reexecuting_factories
```

Expected: FAIL because `complete_reload()` is absent.

- [ ] **Step 3: Refactor reload into a public completion phase**

Keep `reload()` as the ordinary entry point:

```python
def reload(self, options: Mapping[str, object] | None = None) -> None:
    self.complete_reload(options)
```

Move the current trust-resolution body into:

```python
def complete_reload(
    self,
    options: Mapping[str, object] | None = None,
    *,
    pretrust_extensions: dict[str, object] | None = None,
) -> None:
    resolved_options = dict(options or {})
    trust_override = _first_mapping_value(
        resolved_options,
        "projectTrustOverride",
        "project_trust_override",
    )
    if trust_override is not None and not isinstance(trust_override, bool):
        raise TypeError("project trust override must be true, false, or null")
    if trust_override is None:
        trust_override = self._project_trust_override

    if trust_override is None:
        if pretrust_extensions is None:
            pretrust_extensions = self.load_project_trust_extensions()
        context = _first_mapping_value(
            resolved_options,
            "projectTrustContext",
            "project_trust_context",
        ) or ProjectTrustContext(has_ui=False, select=None)
        if not isinstance(context, ProjectTrustContext):
            raise TypeError("project trust context must be a ProjectTrustContext")
        trust_store = _first_mapping_value(resolved_options, "trustStore", "trust_store")
        if trust_store is None:
            trust_store = ProjectTrustStore(self.agent_dir)
        if not isinstance(trust_store, ProjectTrustStore):
            raise TypeError("trust store must be a ProjectTrustStore")
        get_default = getattr(self.settings_manager, "get_default_project_trust", None)
        default_project_trust = get_default() if callable(get_default) else "ask"
        trusted = run_sync(
            resolve_project_trust(
                cwd=self.cwd,
                trust_store=trust_store,
                context=context,
                default_project_trust=default_project_trust,
                extension_runner=pretrust_extensions.get("runtime"),
            )
        )
    else:
        trusted = trust_override

    self._set_project_trusted(bool(trusted))
    self._reload_all_resources(pretrust_extensions=pretrust_extensions)
```

Do not call `load_project_trust_extensions()` when a caller already supplied `pretrust_extensions`.

- [ ] **Step 4: Add ordinary-reload regression coverage**

Extend the test so a separate loader using only `reload({"projectTrustOverride": False})` also invokes its factory exactly once and returns a usable runtime. This proves the convenience path did not regress.

- [ ] **Step 5: Run resource and trust tests**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_coding_resources_and_services.py -k 'resource_loader or inline_extension or staged_resource' \
  tests/test_project_trust.py
```

Expected: all selected tests pass.

- [ ] **Step 6: Review checkpoint without Git**

Confirm `reload()` and staged completion share one trust implementation, pretrust runtime identity is preserved, and explicit/global factories are not invoked twice.

---

### Task 3: Add a typed argparse adapter for extension schemas

**Files:**
- Create: `travis/coding_agent/extension_cli.py`
- Create: `tests/test_extension_cli.py`

**Interfaces:**
- Consumes: `ExtensionRunner.get_flags()` and `ExtensionRunner.get_flag_conflicts()`
- Produces: `ExtensionFlagSchemaError`
- Produces: `add_extension_flags(parser: argparse.ArgumentParser, runtime: ExtensionRunner) -> None`
- Produces namespace field: `extension_flag_values: dict[str, bool | str]`

- [ ] **Step 1: Write failing typed-parser tests**

Create `tests/test_extension_cli.py` with:

```python
from __future__ import annotations

import argparse

import pytest

from travis.coding_agent.extension_cli import ExtensionFlagSchemaError, add_extension_flags
from travis.coding_agent.extensions import ExtensionRunner


def _parser(runtime: ExtensionRunner) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("prompt", nargs="*")
    add_extension_flags(parser, runtime)
    return parser


def _runtime() -> ExtensionRunner:
    runtime = ExtensionRunner()
    runtime.register_flag("verbose", {"type": "boolean", "description": "Verbose"})
    runtime.register_flag("profile", {"type": "string", "description": "Profile"})
    return runtime


def test_typed_extension_flags_preserve_prompt_and_last_string_value() -> None:
    args = _parser(_runtime()).parse_args(
        ["--profile", "safe", "--verbose", "--profile=security", "inspect"]
    )

    assert args.extension_flag_values == {"profile": "security", "verbose": True}
    assert args.prompt == ["inspect"]


def test_option_terminator_keeps_extension_shaped_prompt_text() -> None:
    args = _parser(_runtime()).parse_args(["--", "--verbose", "inspect"])

    assert args.extension_flag_values == {}
    assert args.prompt == ["--verbose", "inspect"]


@pytest.mark.parametrize("argv", [["--profile"], ["--verbose=false"]])
def test_invalid_extension_flag_arity_uses_argparse_error(argv: list[str]) -> None:
    with pytest.raises(SystemExit, match="2"):
        _parser(_runtime()).parse_args(argv)


def test_extension_flag_cannot_shadow_builtin_option() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model")
    runtime = ExtensionRunner()
    runtime.register_flag("model", {"type": "string"})

    with pytest.raises(ExtensionFlagSchemaError, match="--model.*built-in"):
        add_extension_flags(parser, runtime)
```

Add a duplicate-owner test using two inline factories and assert the error names `--profile`, `<inline:1>`, and `<inline:2>`.

- [ ] **Step 2: Run the new module and verify it fails**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_extension_cli.py
```

Expected: collection failure because `extension_cli.py` does not exist.

- [ ] **Step 3: Implement exact-name collection and schema validation**

Create the module with this structure:

```python
from __future__ import annotations

import argparse
import re

from travis.coding_agent.extensions import ExtensionRunner


_FLAG_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class ExtensionFlagSchemaError(ValueError):
    pass


class _StoreExtensionFlag(argparse.Action):
    def __init__(self, *args, flag_name: str, boolean: bool, **kwargs) -> None:
        self.flag_name = flag_name
        self.boolean = boolean
        kwargs["nargs"] = 0 if boolean else None
        super().__init__(*args, **kwargs)

    def __call__(self, parser, namespace, values, option_string=None) -> None:
        current = dict(getattr(namespace, self.dest, None) or {})
        current[self.flag_name] = True if self.boolean else str(values)
        setattr(namespace, self.dest, current)


def add_extension_flags(
    parser: argparse.ArgumentParser,
    runtime: ExtensionRunner,
) -> None:
    conflicts = runtime.get_flag_conflicts()
    if conflicts:
        details = "; ".join(
            f'Extension flag "--{item.name}" from {item.conflicting_extension_path} '
            f"conflicts with {item.first_extension_path}"
            for item in conflicts
        )
        raise ExtensionFlagSchemaError(details)

    builtin_options = set(parser._option_string_actions)  # noqa: SLF001
    parser.set_defaults(extension_flag_values={})
    for name, flag in runtime.get_flags().items():
        option = f"--{name}"
        if not _FLAG_NAME.fullmatch(name):
            raise ExtensionFlagSchemaError(
                f'Extension flag "{option}" from {flag.extension_path} has an invalid name'
            )
        if flag.type not in {"boolean", "string"}:
            raise ExtensionFlagSchemaError(
                f'Extension flag "{option}" from {flag.extension_path} has invalid type {flag.type!r}'
            )
        if option in builtin_options:
            raise ExtensionFlagSchemaError(
                f'Extension flag "{option}" from {flag.extension_path} conflicts with a built-in option'
            )
        parser.add_argument(
            option,
            action=_StoreExtensionFlag,
            dest="extension_flag_values",
            flag_name=name,
            boolean=flag.type == "boolean",
            metavar="VALUE" if flag.type == "string" else None,
            help=flag.description or f"Registered by {flag.extension_path}",
        )
```

The action copies the mapping on every assignment so repeated parser use never mutates a shared default.

- [ ] **Step 4: Run parser tests**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_extension_cli.py
```

Expected: all tests pass. Help output should show `--profile VALUE` and `--verbose` with their descriptions.

- [ ] **Step 5: Review checkpoint without Git**

Confirm the module imports no app, model, session, or provider code; it only translates registered schemas into argparse actions.

---

### Task 4: Reapply process-local values in CodingApp session construction

**Files:**
- Modify: `travis/app.py:16-40,89-145,225-291`
- Modify: `tests/test_cli_runtime_controls.py:1-180`

**Interfaces:**
- Consumes: `apply_extension_flag_values()` and `ExtensionFlagValidationError`
- Produces constructor inputs: `initial_resource_loader` and `extension_flag_values`
- Guarantees: validation occurs before `AgentSession` construction and before replacement teardown

- [ ] **Step 1: Write failing initial/replacement lifecycle tests**

Add a helper that writes an extension registering `profile` and `verbose`. Add:

```python
def test_coding_app_applies_extension_flags_to_initial_and_replacement_sessions(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    extension = tmp_path / "operator" / "flags.py"
    _write_extension_flags(extension)
    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(tmp_path / "agent"),
        additional_extension_paths=[str(extension)],
    )
    loader.reload({"projectTrustOverride": False})

    app = CodingApp(
        cwd=str(project),
        agent_dir=str(tmp_path / "agent"),
        model=faux_model(),
        enable_tui=False,
        project_trust_override=False,
        additional_extension_paths=[str(extension)],
        initial_resource_loader=loader,
        extension_flag_values={"profile": "security", "verbose": True},
    )
    try:
        assert app.session.extension_runner.get_flag("profile") == "security"
        assert app.session.extension_runner.get_flag("verbose") is True

        replacement = app._create_runtime_session({"cwd": str(project)})
        try:
            assert replacement.session.extension_runner.get_flag("profile") == "security"
            assert replacement.session.extension_runner.get_flag("verbose") is True
        finally:
            replacement.session.dispose()
    finally:
        app.close()
```

Add a transactional failure test using an initial loader with an inline-only flag factory and no persistent extension path:

```python
def test_replacement_missing_cli_flag_schema_keeps_current_session(tmp_path: Path) -> None:
    loader = DefaultResourceLoader(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        extension_factories=[lambda runner: runner.register_flag("profile", {"type": "string"})],
    )
    loader.reload({"projectTrustOverride": False})
    app = CodingApp(
        cwd=str(tmp_path),
        agent_dir=str(tmp_path / "agent"),
        model=faux_model(),
        enable_tui=False,
        project_trust_override=False,
        initial_resource_loader=loader,
        extension_flag_values={"profile": "security"},
    )
    current = app.session
    try:
        with pytest.raises(ExtensionFlagValidationError, match="Unknown option: --profile"):
            app.new_session()
        assert app.session is current
    finally:
        app.close()
```

- [ ] **Step 2: Run the lifecycle tests and verify they fail**

Run the two new tests. Expected: `CodingApp.__init__()` rejects the new keyword arguments.

- [ ] **Step 3: Add CodingApp ownership fields**

Extend the constructor:

```python
initial_resource_loader: DefaultResourceLoader | None = None,
extension_flag_values: Mapping[str, bool | str] | None = None,
```

Store:

```python
self._initial_resource_loader = initial_resource_loader
self._extension_flag_values = dict(extension_flag_values or {})
```

Reject a preloaded loader whose resolved `cwd` differs from the app's resolved cwd.

- [ ] **Step 4: Consume or build the loader, then validate values**

Replace the unconditional loader construction in `_create_session()` with:

```python
resource_loader = self._initial_resource_loader
if resource_loader is not None:
    self._initial_resource_loader = None
    if resource_loader.cwd != resolved_cwd:
        resource_loader.get_extensions()["runtime"].dispose()
        raise ValueError("initial resource loader cwd does not match CodingApp cwd")
else:
    resource_loader = DefaultResourceLoader(
        cwd=resolved_cwd,
        agent_dir=self._agent_dir,
        settings_manager=self._settings_manager,
        project_trusted=self._project_trust_override,
        additional_extension_paths=self._additional_extension_paths,
        additional_skill_paths=self._additional_skill_paths,
        additional_prompt_template_paths=self._additional_prompt_template_paths,
        additional_theme_paths=self._additional_theme_paths,
        offline=self._offline,
    )
    resource_loader.reload({"projectTrustContext": self._project_trust_context})

runtime = resource_loader.get_extensions().get("runtime")
diagnostics = (
    apply_extension_flag_values(runtime, self._extension_flag_values)
    if isinstance(runtime, ExtensionRunner)
    else []
)
if diagnostics:
    if isinstance(runtime, ExtensionRunner):
        runtime.dispose()
    raise ExtensionFlagValidationError(diagnostics)
```

Construct `AgentSession` only after this block succeeds.

- [ ] **Step 5: Run lifecycle and existing reload tests**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_cli_runtime_controls.py -k 'extension_flag or replacement_missing' \
  tests/test_coding_resources_and_services.py::test_agent_session_reload_emits_lifecycle_and_rediscover_resources \
  tests/test_session_parity.py
```

Expected: all selected tests pass. Existing `/reload` still preserves values through its current copy loop.

- [ ] **Step 6: Review checkpoint without Git**

Confirm `_create_runtime_session()` still delegates every new/fork/clone/switch/import replacement through `_create_session()`, and failures occur before `AgentSessionRuntime._activate_replacement()` tears down the active session.

---

### Task 5: Integrate staged parsing, trust, and dynamic help in the CLI

**Files:**
- Modify: `travis/cli.py:17-35,350-700`
- Create: `tests/test_cli_extension_flags.py`

**Interfaces:**
- Consumes: `add_extension_flags()`, `ExtensionFlagSchemaError`, `DefaultResourceLoader.complete_reload()`, `CodingApp(initial_resource_loader=..., extension_flag_values=...)`
- Produces: `_build_parser(include_prompt: bool, extension_runtime: ExtensionRunner | None = None)`
- Produces: installed CLI support for authorized extension flags

- [ ] **Step 1: Write failing end-to-end CLI tests**

Create an extension writer whose module-level counter proves load count and whose factory registers the two flags:

```python
def _write_flag_extension(path: Path, counter: Path | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    counter_code = ""
    if counter is not None:
        counter_code = (
            "from pathlib import Path\n"
            f"_counter = Path({str(counter)!r})\n"
            "_counter.write_text(str(int(_counter.read_text() or '0') + 1) if _counter.exists() "
            "else '1', encoding='utf-8')\n"
        )
    path.write_text(
        counter_code
        + "\n"
        + "def extension(travis):\n"
        + "    travis.register_flag('verbose', {'type': 'boolean', 'description': 'Verbose extension'})\n"
        + "    travis.register_flag('profile', {'type': 'string', 'description': 'Extension profile'})\n",
        encoding="utf-8",
    )
```

Add tests proving:

```python
def test_cli_parses_typed_extension_flags_once_and_preserves_prompt(...):
    # Fake CodingApp captures kwargs; fake print transport captures prompt.
    # Invoke with --extension PATH --profile safe --verbose --profile=security inspect.
    # Assert extension_flag_values == {"profile": "security", "verbose": True}.
    # Assert prompt == "inspect", initial_resource_loader is completed, and counter == "1".


def test_extension_help_loads_schema_without_model_or_session(...):
    # Monkeypatch CodingApp and load_model_config to raise if called.
    # Invoke --extension PATH --help.
    # Assert exit 0 and help contains --profile VALUE, --verbose, and descriptions.


def test_unknown_project_flag_fails_closed_without_executing_project_code(...):
    # Put flags.py under project/.travis234/extensions with a module-level marker write.
    # Invoke noninteractive --profile security inspect without --approve.
    # Assert argparse exit 2, marker absent, and CodingApp never constructed.


def test_approved_project_flag_loads_and_reaches_app(...):
    # Repeat with --approve.
    # Assert marker exists and captured extension_flag_values == {"profile": "security"}.
```

Also add cases for `--no-approve`, unknown short options, a missing string value, `--verbose=false`, JSON-mode stderr cleanliness, and `--` prompt termination.

- [ ] **Step 2: Run the new CLI module and verify the red state**

Run:

```bash
.venv/bin/python -m pytest -q tests/test_cli_extension_flags.py
```

Expected: extension flags are rejected by the current strict parse or dynamic help is absent.

- [ ] **Step 3: Extract reusable parser construction**

Move the current core argument declarations verbatim into:

```python
def _build_parser(
    *,
    include_prompt: bool,
    extension_runtime: ExtensionRunner | None = None,
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the Travis234 terminal coding agent",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="store_true", help="Show this help message and exit")
    if include_prompt:
        parser.add_argument("prompt", nargs="*", help="Prompt to run. If omitted, starts the interactive TUI.")
    if extension_runtime is not None:
        add_extension_flags(parser, extension_runtime)
    return parser
```

Between the prompt declaration and the extension adapter call, mechanically move the complete existing core `add_argument(...)` declarations and mutually-exclusive groups from `main()` into `_build_parser()` verbatim. This is an extraction only: do not change any core defaults, aliases, types, choices, or mutual exclusions.

- [ ] **Step 4: Preserve package and one-shot early dispatch**

Keep package dispatch before parser staging. Bootstrap with the no-prompt parser:

```python
bootstrap_parser = _build_parser(include_prompt=False)
bootstrap_args, bootstrap_unknown = bootstrap_parser.parse_known_args(resolved_argv)
```

For `--install-extension`, `--export`, `--list-models`, and `--list-providers`, run the existing early behavior through a strict core-only parser with `include_prompt=True`. Do not load extension schemas for those actions. `--help` is not early-dispatched because its output is dynamic.

- [ ] **Step 5: Resolve cwd, explicit paths, and startup session from core inputs**

Use `bootstrap_args` for current cwd/path validation. Resolve explicit resources before a selected session can change effective cwd, preserving current operator-path semantics.

For help, use the resolved launch cwd and do not allocate a new session path or construct `SessionCatalog`. For ordinary startup, set a temporary empty prompt on the bootstrap namespace and call the existing `_resolve_startup_session()`; repeat its resume/prompt validation after the final parse.

- [ ] **Step 6: Load pretrust schemas, classify trust conservatively, and complete once**

Construct one `DefaultResourceLoader` with the same resource/trust/offline inputs passed to `CodingApp`. Then:

```python
pretrust = resource_loader.load_project_trust_extensions()
pretrust_runtime = pretrust.get("runtime")
if not isinstance(pretrust_runtime, ExtensionRunner):
    raise RuntimeError("Pre-trust extension load did not produce an extension runtime")

provisional_parser = _build_parser(
    include_prompt=True,
    extension_runtime=pretrust_runtime,
)
provisional_args, unresolved = provisional_parser.parse_known_args(resolved_argv)
has_unresolved_option = any(
    token.startswith("-") and token != "-"
    for token in unresolved
)
provisional_mode = _resolved_cli_mode(provisional_args)
trust_has_ui = (
    not provisional_args.help
    and not has_unresolved_option
    and provisional_mode == "interactive"
    and not provisional_args.plain
)
project_trust_context = ProjectTrustContext(
    has_ui=trust_has_ui,
    select=_select_project_trust_option if trust_has_ui else None,
)
resource_loader.complete_reload(
    {
        "projectTrustOverride": bootstrap_args.project_trust_override,
        "projectTrustContext": project_trust_context,
    },
    pretrust_extensions=pretrust,
)
```

Catch `ExtensionFlagSchemaError` around provisional schema construction and report it through `bootstrap_parser.error()`.

- [ ] **Step 7: Strictly parse all authorized flags and render dynamic help**

```python
runtime = resource_loader.get_extensions().get("runtime")
if not isinstance(runtime, ExtensionRunner):
    raise RuntimeError("Resource load did not produce an extension runtime")
parser = _build_parser(include_prompt=True, extension_runtime=runtime)
args = parser.parse_args(resolved_argv)

if args.help:
    try:
        parser.print_help()
        return 0
    finally:
        runtime.dispose()
```

After final parsing, run existing mode/prompt, resume/prompt, thinking-level, generation-parameter, and resource checks using `args`.

- [ ] **Step 8: Forward the completed loader and exact value map**

Pass:

```python
initial_resource_loader=resource_loader,
extension_flag_values=args.extension_flag_values,
```

to `CodingApp`. Catch `ExtensionFlagValidationError` during initial construction and convert it to `parser.error(str(error))`, disposing the preloaded runtime if no app took ownership.

- [ ] **Step 9: Run CLI, trust, automation, and session regressions**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_cli_extension_flags.py \
  tests/test_cli.py \
  tests/test_cli_runtime_controls.py \
  tests/test_project_trust.py \
  tests/test_automation_modes.py \
  tests/test_rpc_mode.py \
  tests/test_session_parity.py
```

Expected: all tests pass; JSON/RPC stdout remains machine-only.

- [ ] **Step 10: Review checkpoint without Git**

Trace one explicit extension invocation, one saved-trust project invocation, one denied project invocation, and `--help` directly through the functions. Confirm each factory load count and every early return's runtime disposal/ownership.

---

### Task 6: Add parity evidence, documentation, and installed-container smoke

**Files:**
- Modify: `scripts/parity_contracts.py:217-258`
- Modify: `README.md:98-108,219-238`
- Modify: `docs/verification/acceptance-matrix.md:27`
- Modify: `evals/container_smoke.py:20-80`
- Modify: `tests/test_release_workflow.py:65-86`
- Modify: `tests/test_cli_extension_flags.py`

**Interfaces:**
- Produces contract: `pi.cli.extension_flags`
- Produces container helper: `prepare_extension_flag_smoke(workspace: Path) -> Path`
- Documents: registration syntax, CLI syntax, trust boundary, lifecycle, and zero base-envelope effect

- [ ] **Step 1: Add a failing parity-contract assertion**

Add to the CLI extension test module:

```python
def test_cli_extension_flag_contract_is_manifested() -> None:
    from scripts.parity_contracts import PI_CONTRACTS

    entry = next(item for item in PI_CONTRACTS if item.contract_id == "pi.cli.extension_flags")
    assert entry.status == "parity"
    assert entry.evidence.endswith(
        "tests/test_cli_extension_flags.py::test_cli_parses_typed_extension_flags_once_and_preserves_prompt"
    )
```

Run it and expect failure because the manifest entry is absent.

- [ ] **Step 2: Add executable parity evidence**

Add near the other CLI contracts:

```python
_pi(
    "extension_flags",
    "cli",
    "tests/test_cli_extension_flags.py::test_cli_parses_typed_extension_flags_once_and_preserves_prompt",
),
```

Run:

```bash
.venv/bin/python scripts/verify_acceptance.py --parity-json
```

Expected: zero invalid evidence; Pi total increases from 77 to 78.

- [ ] **Step 3: Document extension flag use**

Add a compact README example under Extensions:

```python
def extension(travis):
    travis.register_flag("verbose", {"type": "boolean", "description": "Verbose output"})
    travis.register_flag("profile", {"type": "string", "description": "Select a profile"})
```

Then show:

```bash
travis234 --extension ./trusted-extension.py --profile security --verbose "inspect this repository"
```

State that values are process-local, survive session replacement, require explicit/saved trust for project-only schemas, and add zero core context tokens unless the extension uses them to enable context-bearing behavior.

Update the acceptance-matrix expected Pi count to 78 without rewriting historical verification records.

- [ ] **Step 4: Write a failing container-smoke fixture test**

Add:

```python
def test_container_smoke_prepares_extension_flag_fixture(tmp_path: Path) -> None:
    from evals.container_smoke import prepare_extension_flag_smoke

    extension = prepare_extension_flag_smoke(tmp_path)

    source = extension.read_text(encoding="utf-8")
    assert "register_flag('profile'" in source
    assert "'type': 'string'" in source
```

Run it and expect import failure because the helper is absent.

- [ ] **Step 5: Add installed dynamic-help smoke**

Implement:

```python
def prepare_extension_flag_smoke(workspace: Path) -> Path:
    extension = workspace / "extension-flag-smoke.py"
    extension.write_text(
        "def extension(travis):\n"
        "    travis.register_flag('profile', "
        "{'type': 'string', 'description': 'Container extension profile'})\n",
        encoding="utf-8",
    )
    extension.chmod(0o644)
    return extension
```

In `run_container_smoke()`, create the fixture in the mounted workspace and run the installed entrypoint with:

```text
--extension /workspace/extension-flag-smoke.py --help
```

Fail the smoke unless output includes `--profile` and `Container extension profile`. Do not pass provider credentials or invoke a model.

- [ ] **Step 6: Run docs, parity, and smoke-helper tests**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_cli_extension_flags.py \
  tests/test_release_workflow.py \
  tests/architecture/test_acceptance_matrix.py \
  evals/fixtures.py::test_readme_examples_match_supported_flags
.venv/bin/python scripts/verify_acceptance.py --parity-json
```

Expected: all tests pass and parity reports Pi 78, Hermes 11, invalid 0.

- [ ] **Step 7: Review checkpoint without Git**

Read the README example as executable Python and shell syntax. Confirm the container smoke uses only local files and `--help`, and no credential name or value is introduced.

---

### Task 7: Full repository and distribution verification

**Files:**
- Verify only; fix failures in the task that introduced them

**Interfaces:**
- Consumes: all prior task outputs
- Produces: evidence that source, launcher, distributions, installed entry, parity contracts, and release container satisfy the approved design

- [ ] **Step 1: Run the complete focused feature gate**

Run:

```bash
.venv/bin/python -m pytest -q \
  tests/test_extension_cli.py \
  tests/test_cli_extension_flags.py \
  tests/test_coding_policy_and_extensions.py \
  tests/test_coding_resources_and_services.py \
  tests/test_cli_runtime_controls.py \
  tests/test_project_trust.py \
  tests/test_automation_modes.py \
  tests/test_rpc_mode.py \
  tests/test_session_parity.py \
  tests/test_release_workflow.py \
  tests/architecture/test_acceptance_matrix.py
```

Expected: all focused tests pass.

- [ ] **Step 2: Run the complete Python suite**

Run:

```bash
.venv/bin/python -m pytest -q
```

Expected: every test passes; no tests are skipped because of this feature.

- [ ] **Step 3: Run launcher tests and package inspection**

Run:

```bash
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Expected: launcher tests pass and the npm dry-run contains exactly the declared five files.

- [ ] **Step 4: Build Python distributions**

Run:

```bash
.venv/bin/python -m build
```

Expected: wheel and sdist build successfully. Inspect archive member names with Python's `zipfile` and `tarfile`; confirm no `pi`, `hermes-agent`, `appv231`, `.env`, or superpowers plan/spec file is packaged.

- [ ] **Step 5: Smoke a clean installed wheel outside the checkout**

Create a temporary Python 3.13 virtual environment, install the newly built wheel, run `pip check`, and invoke:

```text
travis234 --extension <temporary-extension.py> --help
```

The temporary extension registers `profile` as a string flag. Expected: help exits zero and displays `--profile` plus its description without a provider key.

- [ ] **Step 6: Validate parity and repository hygiene**

Run:

```bash
.venv/bin/python scripts/verify_acceptance.py --parity-json
.venv/bin/python scripts/check_repository_hygiene.py
.venv/bin/python -m compileall -q travis tests evals scripts
```

Expected: Pi 78 and Hermes 11 with zero invalid evidence; hygiene reports zero violations; compileall exits zero.

- [ ] **Step 7: Build and smoke the release image without cache**

Run:

```bash
docker build --no-cache -f Dockerfile.release -t travis234:extension-flags-local .
.venv/bin/python -m evals.container_smoke --image travis234:extension-flags-local
```

Expected: build and smoke exit zero as unprivileged user `travis`; dynamic extension help appears; no provider credentials are forwarded; existing print/JSON/RPC, trust, compaction, npm, and process cleanup smokes still pass.

- [ ] **Step 8: Final non-Git completion audit**

Without invoking Git, inspect the explicitly listed source/test/doc files. Report:

- focused and full Python pass counts;
- npm launcher and pack results;
- wheel/sdist and installed-help results;
- parity totals;
- hygiene result;
- no-cache container result;
- confirmation that no settings/session migration and no base context-envelope data were added;
- confirmation that no Git operation was performed.
