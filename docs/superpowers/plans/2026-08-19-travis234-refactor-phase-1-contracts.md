# Phase 1 — Explicit Contracts and Composition Shell

> **Required skills:** `superpowers:executing-plans`,
> `superpowers:test-driven-development`, and
> `superpowers:verification-before-completion`.

**Goal:** Introduce typed construction and façade contracts, remove the session factory
cycle, and create composition shells without moving behavior from the existing mixin
runtimes.

**Architecture:** Existing public factories retain their accepted mappings and result
shapes. Internal code converts them to immutable typed records. Protocols break concrete
cycles and describe genuine substitutable ports. `RuntimeFacade` remains operational.

**Do not modify:** `travis/agent/agent_loop.py`; controller behavior; provider wire code;
TUI command behavior; persistence formats.

---

## Task 1.1: Inventory the supported façade surfaces

**Files:**

- Create: `travis/coding_agent/session_contracts.py`
- Create: `travis/tui/interactive_contracts.py`
- Create: `tests/architecture/test_refactor_contracts.py`
- Modify: `tests/test_runtime_facade_contract.py`
- Modify: `tests/coding_agent/test_session_owner_boundaries.py`
- Modify: `tests/tui/test_interactive_owner_boundaries.py`

- [ ] **Step 1: Derive the supported inventory from real callers**

Use `rg` across `travis/`, `tests/`, and bundled extensions to list non-private members
accessed on `AgentSession` and `InteractiveMode`. Classify each as:

- public supported method/property;
- lifecycle method;
- extension compatibility member;
- private test/internal member.

Put the supported names in immutable `frozenset` constants:

```python
AGENT_SESSION_PUBLIC_MEMBERS: frozenset[str]
INTERACTIVE_MODE_PUBLIC_MEMBERS: frozenset[str]
```

Do not include arbitrary private state merely because a test reaches into it. Preserve
private dynamic forwarding separately through the existing compatibility tests.

- [ ] **Step 2: Add failing inventory tests**

Tests assert:

- every supported name resolves on a normally constructed façade;
- every callable remains callable;
- `dispose` and `shutdown` are explicitly defined by `AgentSession`;
- no newly declared public method exists only through an unrecorded dynamic lookup;
- the inventories contain no underscore-prefixed names;
- contract modules import neither concrete façades nor application composition roots.

Confirm RED because the inventories do not exist.

- [ ] **Step 3: Add static façade protocols**

Define narrow protocols for callers that need substitution, including:

```python
class SessionLifecyclePort(Protocol):
    def dispose(self) -> None: ...
    def shutdown(self, *args: object, **kwargs: object) -> None: ...


class SessionFactory(Protocol):
    def __call__(self, **kwargs: object) -> SessionLifecyclePort: ...
```

Add TUI ports only for terminal rendering, app/session access, and owner-thread dispatch
that are used by more than one controller or fake. Do not mark protocols runtime
checkable.

- [ ] **Step 4: Run focused contracts and Pyright**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/test_runtime_facade_contract.py \
  tests/architecture/test_refactor_contracts.py \
  tests/coding_agent/test_session_owner_boundaries.py \
  tests/tui/test_interactive_owner_boundaries.py
uv run --locked --all-extras --dev pyright \
  travis/coding_agent/session_contracts.py \
  travis/tui/interactive_contracts.py
```

- [ ] **Step 5: Commit**

```bash
git add travis/coding_agent/session_contracts.py \
  travis/tui/interactive_contracts.py \
  tests/test_runtime_facade_contract.py \
  tests/architecture/test_refactor_contracts.py \
  tests/coding_agent/test_session_owner_boundaries.py \
  tests/tui/test_interactive_owner_boundaries.py
git commit -m "refactor(contracts): inventory session and tui facades"
```

---

## Task 1.2: Replace the service dictionary internally with a typed bundle

**Files:**

- Create: `travis/coding_agent/session_composition.py`
- Create: `tests/coding_agent/test_session_composition.py`
- Modify: `travis/coding_agent/agent_session_services.py`
- Modify: `tests/test_coding_resources_and_services.py`

- [ ] **Step 1: Write failing typed-bundle tests**

Define the expected immutable internal record:

```python
@dataclass(frozen=True, slots=True)
class SessionDependencies:
    cwd: str
    agent_dir: str
    settings_manager: SettingsManager
    resource_loader: DefaultResourceLoader
    auth_storage: AuthStorage
    model_registry: ModelRegistry
    session_catalog: SessionCatalog
    session_path: str
    session_id: str
    operation_runtime: object | None
    diagnostics: tuple[Mapping[str, object], ...]
```

Tests assert canonical absolute paths, shared `AuthStorage` identity, tuple diagnostics,
generated session path/id, injected-owner identity, and rejection of mismatched registry
authentication. Add `to_legacy_mapping()` and `from_legacy_mapping()` round-trip tests;
camelCase keys remain exactly the current factory keys.

- [ ] **Step 2: Confirm RED**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/coding_agent/test_session_composition.py
```

- [ ] **Step 3: Extract typed construction without changing the public factory**

Move the body of `create_agent_session_services` into a private
`build_session_dependencies(options) -> SessionDependencies`. Keep
`create_agent_session_services(options) -> dict[str, object]` as the supported
compatibility wrapper returning `to_legacy_mapping()`.

`create_agent_session_from_services` accepts either `SessionDependencies` or the legacy
mapping and normalizes once. No caller downstream indexes a free-form service dictionary
after normalization.

- [ ] **Step 4: Run focused construction and resource tests**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/coding_agent/test_session_composition.py \
  tests/test_coding_resources_and_services.py \
  tests/test_app_integration.py \
  tests/test_cli_runtime_controls.py
uv run --locked --all-extras --dev pyright \
  travis/coding_agent/session_composition.py
```

- [ ] **Step 5: Commit**

```bash
git add travis/coding_agent/session_composition.py \
  travis/coding_agent/agent_session_services.py \
  tests/coding_agent/test_session_composition.py \
  tests/test_coding_resources_and_services.py
git commit -m "refactor(session): type service composition"
```

---

## Task 1.3: Remove the concrete session factory import cycle

**Files:**

- Modify: `travis/coding_agent/agent_session_services.py`
- Modify: `travis/coding_agent/agent_session_runtime.py`
- Modify: `travis/coding_agent/session_contracts.py`
- Modify: `tests/architecture/test_refactor_contracts.py`
- Modify: `tests/test_app_integration.py`
- Modify: `tests/test_session_parity.py`

- [ ] **Step 1: Add failing import-cycle and injectable-factory tests**

Architecture tests parse imports and reject:

```text
agent_session_services -> agent_session
agent_session_runtime  -> agent_session
session_contracts      -> agent_session
```

Factory tests inject a callable returning a fake `SessionLifecyclePort` and prove
service construction and runtime replacement use it without importing the concrete
class. A default call still returns a real `AgentSession`.

- [ ] **Step 2: Confirm RED**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/architecture/test_refactor_contracts.py \
  tests/test_app_integration.py -k session_factory
```

- [ ] **Step 3: Break the cycle with a late default factory and protocols**

- Import `default_convert_to_llm` directly from `session_types`, not through
  `agent_session`.
- Type concrete `AgentSession` references under `TYPE_CHECKING` only.
- Resolve the default concrete factory inside a small leaf function after modules are
  initialized.
- Carry the injected `SessionFactory` through `SessionDependencies`/runtime construction.
- Keep `CreateAgentSessionResult.session` structurally typed while returning the same
  concrete runtime in normal use.

Do not add module-level service-locator state.

- [ ] **Step 4: Run clean-interpreter import checks**

```bash
uv run --locked --all-extras --dev python -c \
  'import travis.coding_agent.agent_session_services; import travis.coding_agent.agent_session; print("ok")'
uv run --locked --all-extras --dev python -c \
  'import travis.coding_agent.agent_session; import travis.coding_agent.agent_session_services; print("ok")'
uv run --locked --all-extras --dev pytest -q \
  tests/architecture/test_refactor_contracts.py \
  tests/test_app_integration.py \
  tests/test_session_parity.py \
  tests/test_session_commands.py
```

- [ ] **Step 5: Commit**

```bash
git add travis/coding_agent/agent_session_services.py \
  travis/coding_agent/agent_session_runtime.py \
  travis/coding_agent/session_contracts.py \
  tests/architecture/test_refactor_contracts.py \
  tests/test_app_integration.py tests/test_session_parity.py
git commit -m "refactor(session): decouple runtime factories"
```

---

## Task 1.4: Normalize bootstrap aliases once

**Files:**

- Create: `travis/coding_agent/session_options.py`
- Create: `tests/coding_agent/test_session_options.py`
- Modify: `travis/coding_agent/agent_session_services.py`
- Modify: `tests/test_cli_runtime_controls.py`

- [ ] **Step 1: Write table-driven alias tests**

Test every camel/snake pair currently accepted by `agent_session_services.py`, including
agent dir, session path/id/dir, settings, resource loader/options/reload options,
authentication, model registry, operation runtime, extension flags, model/thinking,
scoped models, provider/model IDs, retry settings, tools, and conversion hooks.

Rules:

- snake_case and camelCase produce equal normalized values;
- providing both with different values raises `ValueError` naming the pair;
- unknown keys remain in a read-only `extras` mapping for downstream compatibility;
- input mappings are never mutated;
- secrets are never included in `repr`.

- [ ] **Step 2: Confirm RED**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/coding_agent/test_session_options.py
```

- [ ] **Step 3: Implement immutable option normalization**

Use a frozen, slotted `SessionBootstrapOptions` plus a single alias table. Replace local
`options.get("camel", options.get("snake"))` chains only inside the service-construction
path. Do not change `AgentSession`'s direct Python constructor signature in this phase.

- [ ] **Step 4: Run focused factory/CLI tests and Pyright**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/coding_agent/test_session_options.py \
  tests/coding_agent/test_session_composition.py \
  tests/test_cli_runtime_controls.py \
  tests/test_coding_resources_and_services.py
uv run --locked --all-extras --dev pyright \
  travis/coding_agent/session_options.py \
  travis/coding_agent/session_composition.py
```

- [ ] **Step 5: Commit**

```bash
git add travis/coding_agent/session_options.py \
  travis/coding_agent/agent_session_services.py \
  tests/coding_agent/test_session_options.py \
  tests/test_cli_runtime_controls.py
git commit -m "refactor(session): normalize bootstrap options once"
```

---

## Task 1.5: Make public annotations resolvable

**Files:**

- Create: `tests/architecture/test_public_type_hints.py`
- Modify only the modules named by failing `get_type_hints` results
- Modify: `pyrightconfig.json`

- [ ] **Step 1: Add the failing clean-interpreter hint test**

Enumerate public functions/classes exported by:

- `travis`;
- `travis.coding_agent`;
- `travis.coding_agent.agent_session_services`;
- `travis.coding_agent.session_contracts`;
- `travis.tui.interactive_contracts`;
- `travis.ai.providers`.

Call `typing.get_type_hints` with each defining module's globals/locals and report
qualified names. Confirm the known unresolved forward references fail.

- [ ] **Step 2: Fix each smoking gun, not the test**

For every failure, import the runtime-visible type from its canonical leaf module or use
a quoted forward reference with a resolvable module namespace. Do not catch `NameError`,
inject names in the test, or replace annotations with `Any`.

- [ ] **Step 3: Run hint, import, and scoped type gates**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/architecture/test_public_type_hints.py \
  tests/architecture/test_refactor_contracts.py
uv run --locked --all-extras --dev pyright
```

- [ ] **Step 4: Expand Pyright's include scope**

Add every Phase 1 module to the checked execution environment. Run the quality
configuration test to prove the scope is monotonic.

- [ ] **Step 5: Commit**

Stage only the test, Pyright config, and modules actually fixed. Add each reported module
by its exact path after reviewing its diff; never stage the complete `travis/` tree:

```bash
git add pyrightconfig.json tests/architecture/test_public_type_hints.py \
  tests/architecture/test_quality_configuration.py
git diff --cached --name-only
```

Then add the exact corrected module paths, inspect the staged list, and commit
`fix(types): resolve public annotation contracts`.

---

## Task 1.6: Phase 1 qualification

- [ ] Run the master phase checkpoint.
- [ ] Run all Phase 1 contract, factory, session parity, app integration, CLI, and
  architecture tests.
- [ ] Build root and adapter artifacts and run Twine checks.
- [ ] Install the root wheel into a fresh environment.
- [ ] Exercise normal-user TUI scenarios for startup/help, one faux-provider prompt,
  `/login` discovery without credentials, new/resume/fork, `/coordination --plan`, and
  clean shutdown.
- [ ] Record each prompt PASS/FAIL and the protected SHA in
  `docs/verification/contract-first-refactor.md`.
- [ ] Commit the evidence as `docs: record phase 1 refactor qualification`.
- [ ] Report and stop for review before Phase 2.
