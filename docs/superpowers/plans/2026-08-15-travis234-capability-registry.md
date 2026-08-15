# Travis234 Capability Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a typed, explainable, atomically reloaded capability registry beneath `DefaultResourceLoader` while preserving all current resource, trust, extension, CLI, TUI, and session behavior.

**Architecture:** A generic `CapabilityRegistry` builds immutable snapshots from typed providers and swaps only complete candidates. Travis234's internal resource provider owns a `ResourceLoadCandidate`; `DefaultResourceLoader` remains the compatibility façade and atomically swaps one candidate reference, preserving existing getter names, result shapes, ordering, and identity within a generation.

**Tech Stack:** Python 3.13, standard-library dataclasses/enums/threading/mapping proxies, existing Travis234 loaders, pytest, npm launcher tests, setuptools build, Docker release smoke.

## Global Constraints

- Execution requires separate explicit user approval after plan review.
- Names remain `Travis234`, `travis234`, and Python package `travis`.
- User state remains under `~/.travis234`; no alternate state path or migration alias.
- JSONL session compatibility remains unchanged.
- Credentials never enter tracked files, diagnostics, model prompts, or child environments.
- Agent-loop ordering, budgets, cancellation, steering, follow-ups, continuation, and ordered tool-result persistence remain unchanged.
- Parallel execution remains bounded and project code remains trust-gated.
- Generic MCP and the separately packaged bounded adapter remain supported.
- Existing resource precedence remains exact: resolved packages, then configured/explicit paths, then bundled defaults where applicable.
- `DefaultResourceLoader` keeps its constructor and getter signatures.
- New owner modules stay at or below 750 lines and do not import façades.
- No changes under `travis/agent/`, `travis/compaction/`, `travis/ai/providers/`, `packages/travis234-cli/`, or `packages/travis234-mcp-adapter/`.
- Each behavioral correction begins with a failing focused regression.
- Never stage the unrelated `.gitignore` change or ignored `oh-my-pi/` tree.

---

## File map

Create:

- `travis/coding_agent/capabilities/__init__.py`
- `travis/coding_agent/capabilities/types.py`
- `travis/coding_agent/capabilities/registry.py`
- `travis/coding_agent/resource_extensions.py`
- `travis/coding_agent/resource_candidates.py`
- `tests/test_capability_registry.py`
- `tests/test_resource_extension_loader.py`

Modify:

- `travis/coding_agent/resource_loader.py`
- `tests/test_coding_resources_and_services.py`
- `tests/test_extension_loading_and_reload.py`
- `tests/test_resource_runtime_parity.py`
- `tests/test_cli_runtime_controls.py`
- `tests/architecture/test_facade_boundaries.py`

Explicitly unchanged: `travis/cli.py`, `travis/coding_agent/agent_session.py`, `travis/coding_agent/agent_session_services.py`, `travis/coding_agent/session_tooling.py`, `travis/coding_agent/package_manager.py`, `travis/coding_agent/extensions.py`, and `travis/tui/interactive_mode.py`. They are regression surfaces only. Stop and revise the plan if implementation requires changing one.

## Locked interfaces

```python
class CapabilityKind(StrEnum):
    CONTEXT_FILE = "context_file"
    SKILL = "skill"
    PROMPT_TEMPLATE = "prompt_template"
    THEME = "theme"
    EXTENSION = "extension"
    TOOL = "tool"
    AGENT_ROLE = "agent_role"


@dataclass(frozen=True)
class CapabilityLoadContext:
    cwd: str
    agent_dir: str
    project_trusted: bool
    offline: bool
    generation: int
    data: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class CapabilitySource:
    provider: str
    path: str | None = None
    source: str = "local"
    scope: str = "temporary"
    origin: str = "top-level"


@dataclass(frozen=True)
class CapabilityRecord:
    kind: CapabilityKind
    key: str
    value: object
    source: CapabilitySource
    priority: int = 0
    enabled: bool = True


@dataclass(frozen=True)
class CapabilityDiagnostic:
    severity: Literal["warning", "error", "collision"]
    provider: str
    code: str
    message: str
    source: CapabilitySource | None = None


@dataclass(frozen=True)
class CapabilityProviderResult:
    records: tuple[CapabilityRecord, ...] = ()
    diagnostics: tuple[CapabilityDiagnostic, ...] = ()
    state: object | None = None
    dispose: Callable[[], None] | None = None


class CapabilityProvider(Protocol):
    name: str
    priority: int

    def load(self, context: CapabilityLoadContext) -> CapabilityProviderResult:
        raise NotImplementedError
```

```python
@dataclass(frozen=True)
class CapabilityResolution:
    winner: CapabilityRecord | None
    candidates: tuple[CapabilityRecord, ...]


class CapabilityReloadError(RuntimeError):
    def __init__(self, message: str, diagnostic: CapabilityDiagnostic) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic


class CapabilitySnapshot:
    generation: int
    diagnostics: tuple[CapabilityDiagnostic, ...]

    def records(self, kind: CapabilityKind) -> tuple[CapabilityRecord, ...]:
        raise NotImplementedError

    def resolve(self, kind: CapabilityKind, key: str) -> CapabilityResolution:
        raise NotImplementedError

    def provider_state(self, provider_name: str) -> object | None:
        raise NotImplementedError


class CapabilityRegistry:
    def register(self, provider: CapabilityProvider) -> None:
        raise NotImplementedError

    def seed(self, provider_name: str, result: CapabilityProviderResult) -> None:
        raise NotImplementedError

    def set_enabled(self, provider_name: str, enabled: bool) -> None:
        raise NotImplementedError

    @property
    def snapshot(self) -> CapabilitySnapshot:
        raise NotImplementedError

    def reload(
        self,
        context: CapabilityLoadContext,
        *,
        on_commit: Callable[[CapabilitySnapshot], None] | None = None,
    ) -> CapabilitySnapshot:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError
```

```python
# travis/coding_agent/resource_extensions.py
@dataclass(frozen=True)
class ExtensionLoadRequest:
    cwd: str
    event_bus: EventBusController
    discovered_paths: tuple[str, ...]
    additional_paths: tuple[str, ...]
    factories: tuple[Callable[[ExtensionRunner], object], ...]
    no_extensions: bool
    generation: int
    apply_override: bool
    override: Callable[[dict[str, object]], dict[str, object]] | None


class ExtensionRuntimeLease:
    result: dict[str, object]
    runtime: ExtensionRunner
    module_names: tuple[str, ...]

    def retain(self) -> "ExtensionRuntimeLease":
        raise NotImplementedError

    def release(self) -> None:
        raise NotImplementedError


def create_empty_extension_runtime(
    cwd: str,
    event_bus: EventBusController,
) -> ExtensionRuntimeLease:
    raise NotImplementedError


def load_extension_runtime(
    request: ExtensionLoadRequest,
    *,
    preloaded: ExtensionRuntimeLease | None = None,
) -> ExtensionRuntimeLease:
    raise NotImplementedError
```

```python
# travis/coding_agent/resource_candidates.py
@dataclass(frozen=True)
class ResourceContentRequest:
    cwd: str
    agent_dir: str
    project_trusted: bool
    resolved_paths: ResolvedPaths
    additional_skill_paths: tuple[str, ...]
    additional_prompt_paths: tuple[str, ...]
    additional_theme_paths: tuple[str, ...]
    no_context_files: bool
    no_skills: bool
    no_prompt_templates: bool
    no_themes: bool
    system_prompt_source: str | None
    append_system_prompt_source: tuple[str, ...] | None
    agents_files_override: Callable[[dict[str, list[dict[str, str]]]], dict[str, list[dict[str, str]]]] | None
    skills_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None
    prompts_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None
    themes_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None
    system_prompt_override: Callable[[str | None], str | None] | None
    append_system_prompt_override: Callable[[list[str]], list[str]] | None


@dataclass(frozen=True)
class ResourceContentCandidate:
    skills_result: dict[str, list[object]]
    prompts_result: dict[str, list[object]]
    themes_result: dict[str, list[object]]
    agents_files: tuple[dict[str, str], ...]
    system_prompt: str | None
    append_system_prompt: tuple[str, ...]
    package_diagnostics: tuple[object, ...]
    skill_paths: tuple[str, ...]
    prompt_paths: tuple[str, ...]
    theme_paths: tuple[str, ...]
    metadata_by_path: Mapping[str, dict[str, object]]


@dataclass(frozen=True)
class ResourceLoadCandidate:
    extensions: ExtensionRuntimeLease
    content: ResourceContentCandidate
    records: tuple[CapabilityRecord, ...]
    diagnostics: tuple[CapabilityDiagnostic, ...]

    def close(self) -> None:
        self.extensions.release()

    @classmethod
    def empty(cls, extensions: ExtensionRuntimeLease) -> "ResourceLoadCandidate":
        raise NotImplementedError


@dataclass(frozen=True)
class ResourceLoadRequest:
    mode: Literal["full", "extend"]
    content_request: ResourceContentRequest | None
    extension_request: ExtensionLoadRequest | None
    preloaded_extensions: ExtensionRuntimeLease | None
    current: ResourceLoadCandidate | None
    cwd: str
    skill_paths: tuple[str, ...] = ()
    prompt_paths: tuple[str, ...] = ()
    theme_paths: tuple[str, ...] = ()
    skills_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None = None
    prompts_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None = None
    themes_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None = None

    @classmethod
    def full(
        cls,
        content_request: ResourceContentRequest,
        extension_request: ExtensionLoadRequest,
        *,
        preloaded_extensions: ExtensionRuntimeLease | None = None,
    ) -> "ResourceLoadRequest":
        raise NotImplementedError

    @classmethod
    def extend(
        cls,
        current: ResourceLoadCandidate,
        *,
        cwd: str,
        skill_paths: tuple[str, ...],
        prompt_paths: tuple[str, ...],
        theme_paths: tuple[str, ...],
        skills_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None,
        prompts_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None,
        themes_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None,
    ) -> "ResourceLoadRequest":
        raise NotImplementedError

    def build(self) -> ResourceLoadCandidate:
        raise NotImplementedError


def build_resource_content(
    request: ResourceContentRequest,
) -> ResourceContentCandidate:
    raise NotImplementedError


def extend_resource_content(
    current: ResourceContentCandidate,
    *,
    cwd: str,
    skill_paths: tuple[str, ...],
    prompt_paths: tuple[str, ...],
    theme_paths: tuple[str, ...],
    skills_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None,
    prompts_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None,
    themes_override: Callable[[dict[str, list[object]]], dict[str, list[object]]] | None,
) -> ResourceContentCandidate:
    raise NotImplementedError
```

`DefaultResourceLoader.get_capability_snapshot()` returns the current immutable snapshot. Existing getters continue returning the same legacy dictionaries/lists for one committed generation.

---

### Task 1: Capability value contracts

**Files:**
- Create: `travis/coding_agent/capabilities/__init__.py`
- Create: `travis/coding_agent/capabilities/types.py`
- Create: `tests/test_capability_registry.py`

**Interfaces:**
- Consumes: Python standard library only.
- Produces: all value contracts in the first locked-interface block.

- [ ] **Step 1: Write failing type-contract tests**

```python
from dataclasses import FrozenInstanceError
from types import MappingProxyType

import pytest

from travis.coding_agent.capabilities.types import (
    CapabilityDiagnostic, CapabilityKind, CapabilityLoadContext,
    CapabilityRecord, CapabilitySource,
)


def test_capability_kinds_cover_phase_one_and_reserved_followups() -> None:
    assert {kind.value for kind in CapabilityKind} == {
        "context_file", "skill", "prompt_template", "theme",
        "extension", "tool", "agent_role",
    }


def test_capability_records_and_context_are_immutable() -> None:
    source = CapabilitySource("test", "/tmp/a")
    record = CapabilityRecord(CapabilityKind.SKILL, "audit", object(), source)
    context = CapabilityLoadContext(
        "/tmp/repo", "/tmp/agent", False, True, 1,
        MappingProxyType({"reason": "test"}),
    )
    with pytest.raises(FrozenInstanceError):
        record.key = "changed"  # type: ignore[misc]
    with pytest.raises(TypeError):
        context.data["reason"] = "changed"  # type: ignore[index]


def test_diagnostic_attribution_is_stable() -> None:
    source = CapabilitySource("skills", "/repo/SKILL.md")
    item = CapabilityDiagnostic(
        "collision", "skills", "capability_collision",
        'skill "audit" was shadowed', source,
    )
    assert item.source is source
    assert item.code == "capability_collision"
```

- [ ] **Step 2: Verify missing-package failure**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_capability_registry.py -q
```

Expected: `ModuleNotFoundError` for `travis.coding_agent.capabilities`.

- [ ] **Step 3: Implement the exact contracts**

Use the locked declarations. Validate non-empty provider names/keys and integer priorities without normalizing case:

```python
def _require_name(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")


def __post_init__(self) -> None:
    _require_name(self.key, "capability key")
    if isinstance(self.priority, bool) or not isinstance(self.priority, int):
        raise TypeError("capability priority must be an integer")
```

Export all seven contracts through explicit `__all__` entries in `capabilities/__init__.py`.

- [ ] **Step 4: Run and commit Task 1**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_capability_registry.py -q
git add travis/coding_agent/capabilities tests/test_capability_registry.py
git commit -m "feat(resources): define capability contracts"
```

Expected: 3 tests pass before commit.

---

### Task 2: Explainable atomic registry

**Files:**
- Create: `travis/coding_agent/capabilities/registry.py`
- Modify: `travis/coding_agent/capabilities/__init__.py`
- Modify: `tests/test_capability_registry.py`

**Interfaces:**
- Consumes: Task 1 contracts.
- Produces: `CapabilityResolution`, `CapabilitySnapshot`, `CapabilityRegistry`, `CapabilityReloadError`.

- [ ] **Step 1: Add failing precedence and explanation tests**

```python
@dataclass
class StaticProvider:
    name: str
    priority: int
    result: CapabilityProviderResult

    def load(self, context: CapabilityLoadContext) -> CapabilityProviderResult:
        return self.result


def _record(
    provider: str,
    key: str,
    value: str,
    *,
    priority: int = 0,
    enabled: bool = True,
) -> CapabilityRecord:
    return CapabilityRecord(
        CapabilityKind.SKILL, key, value,
        CapabilitySource(provider, f"/{provider}/{key}"),
        priority=priority,
        enabled=enabled,
    )


def test_registry_explains_precedence_and_filters_before_dedupe() -> None:
    registry = CapabilityRegistry()
    registry.register(StaticProvider("low", 10, CapabilityProviderResult(
        records=(_record("low", "audit", "low"),))))
    registry.register(StaticProvider("high", 20, CapabilityProviderResult(records=(
        _record("high", "audit", "lower-priority", priority=1),
        _record("high", "audit", "winner", priority=2),
        _record("high", "disabled", "ignored", enabled=False),
    ))))
    snapshot = registry.reload(CapabilityLoadContext("/repo", "/agent", False, False, 1))
    resolution = snapshot.resolve(CapabilityKind.SKILL, "audit")

    assert resolution.winner is not None
    assert resolution.winner.value == "winner"
    assert [record.value for record in resolution.candidates] == [
        "winner", "lower-priority", "low",
    ]
    assert snapshot.resolve(CapabilityKind.SKILL, "disabled").winner is None
    assert [item.code for item in snapshot.diagnostics] == [
        "capability_collision", "capability_collision",
    ]


def test_disabled_provider_contributes_neither_records_nor_state() -> None:
    provider = StaticProvider(
        "optional", 5,
        CapabilityProviderResult(
            records=(_record("optional", "audit", "hidden"),),
            state={"connected": True},
        ),
    )
    registry = CapabilityRegistry()
    registry.register(provider)
    registry.set_enabled("optional", False)
    snapshot = registry.reload(CapabilityLoadContext("/repo", "/agent", False, False, 1))

    assert snapshot.records(CapabilityKind.SKILL) == ()
    assert snapshot.provider_state("optional") is None
```

- [ ] **Step 2: Add failing rollback and disposal tests**

```python
class MutableProvider:
    priority = 0

    def __init__(self, name: str) -> None:
        self.name = name
        self.result = CapabilityProviderResult()
        self.error: Exception | None = None

    def load(self, context: CapabilityLoadContext) -> CapabilityProviderResult:
        if self.error is not None:
            raise self.error
        return self.result


def test_failed_reload_keeps_snapshot_and_disposes_candidate() -> None:
    disposed: list[str] = []
    stable = MutableProvider("stable")
    failing = MutableProvider("failing")
    registry = CapabilityRegistry()
    registry.register(stable)
    registry.register(failing)
    stable.result = CapabilityProviderResult(records=(_record("stable", "audit", "v1"),))
    first = registry.reload(CapabilityLoadContext("/repo", "/agent", False, False, 1))
    stable.result = CapabilityProviderResult(
        records=(_record("stable", "audit", "v2"),),
        dispose=lambda: disposed.append("v2"),
    )
    failing.error = RuntimeError("candidate failed")

    with pytest.raises(CapabilityReloadError, match="failing") as caught:
        registry.reload(CapabilityLoadContext("/repo", "/agent", False, False, 2))

    assert registry.snapshot is first
    assert disposed == ["v2"]
    assert caught.value.diagnostic.code == "provider_load_failed"
```

`MutableProvider` stores `name`, `priority=0`, `result`, and optional `error`. `CapabilityReloadError.diagnostic` exposes the failure diagnostic. Add a callback-rollback test where `on_commit` raises and the prior snapshot remains active with diagnostic code `snapshot_commit_failed`.

- [ ] **Step 3: Add a concurrent-reader regression**

Use `threading.Event` to block generation 2 inside a provider. While blocked, assert `registry.snapshot is first`; after release and join, assert the generation-2 winner is visible. The worker must finish within two seconds.

- [ ] **Step 4: Verify missing-registry failure**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_capability_registry.py -q
```

Expected: import failure for `capabilities.registry`.

- [ ] **Step 5: Implement merge and explanation**

Precedence is provider priority descending, registration order ascending, record priority descending, then encounter order. Keys are exact `(kind, key)` tuples. Disabled records are removed before candidate lists.

```python
for order, provider, result in sorted(loaded, key=lambda item: (-item[1].priority, item[0])):
    diagnostics.extend(result.diagnostics)
    records = sorted(
        (record for record in result.records if record.enabled),
        key=lambda record: -record.priority,
    )
    for record in records:
        candidates.setdefault((record.kind, record.key), []).append(record)
```

Store immutable tuples and mapping proxies. Add `capability_collision` for every shadowed record and retain all enabled candidates in `resolve()`. A provider disabled through `set_enabled()` is skipped completely and contributes no state or records; enabling it affects the next reload.

- [ ] **Step 6: Implement atomic reload, seed, and close**

Serialize loads with a reload lock and swaps with a state lock. Never execute providers or disposers under the state lock. Provider/callback failure restores old results and disposes only the candidate. `CapabilityReloadError` carries one sanitized `CapabilityDiagnostic`: `provider_load_failed` for provider exceptions and `snapshot_commit_failed` for callback exceptions. Successful replacement disposes old results outside locks. `seed()` works once per registered provider before first reload. Duplicate names, unknown seed names, repeated seeds, invalid enable names, and registration after first reload raise `ValueError`.

```python
with self._state_lock:
    old_snapshot, old_results = self._snapshot, self._results
    self._snapshot, self._results = candidate, candidate_results
    try:
        if on_commit is not None:
            on_commit(candidate)
    except Exception as error:
        self._snapshot, self._results = old_snapshot, old_results
        commit_error = error
    else:
        commit_error = None
```

Dispose candidate results and raise `commit_error` after releasing the lock; otherwise dispose replaced old results and return the candidate.

- [ ] **Step 7: Run focused boundaries and commit Task 2**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_capability_registry.py tests/test_agent_core_boundary.py tests/test_provider_ownership_architecture.py -q
git add travis/coding_agent/capabilities tests/test_capability_registry.py
git commit -m "feat(resources): add atomic capability registry"
```

Expected: all selected tests pass.

---

### Task 3: Isolated extension-runtime candidates

**Files:**
- Create: `travis/coding_agent/resource_extensions.py`
- Create: `tests/test_resource_extension_loader.py`
- Modify: `travis/coding_agent/resource_loader.py:1-30,194-208,256-265,404-524`
- Modify: `tests/test_extension_loading_and_reload.py`

**Interfaces:**
- Consumes: existing `ExtensionRunner`, event bus, resource discovery, source scopes, and extension overrides.
- Produces: `ExtensionLoadRequest`, `ExtensionRuntimeLease`, `create_empty_extension_runtime()`, `load_extension_runtime()`.

- [ ] **Step 1: Write failing lease and module-cleanup tests**

```python
def test_extension_lease_disposes_only_after_last_release(tmp_path: Path) -> None:
    extension = tmp_path / "extensions" / "sample.py"
    extension.parent.mkdir()
    extension.write_text(
        "def extension(travis):\n"
        "    travis.register_command('sample', {'handler': lambda args, ctx: []})\n",
        encoding="utf-8",
    )
    lease = load_extension_runtime(extension_request(tmp_path, extension))
    retained = lease.retain()
    module_names = lease.module_names

    lease.release()
    assert retained.runtime.get_registered_command("sample") is not None
    assert all(name in sys.modules for name in module_names)
    retained.release()
    assert all(name not in sys.modules for name in module_names)
```

`extension_request()` returns a complete `ExtensionLoadRequest` using `create_event_bus()`, the supplied paths as `discovered_paths`, empty additional paths/factories, generation 1, extensions enabled, overrides enabled, and no override callback.

```python
def extension_request(tmp_path: Path, *paths: Path) -> ExtensionLoadRequest:
    return ExtensionLoadRequest(
        cwd=str(tmp_path),
        event_bus=create_event_bus(),
        discovered_paths=tuple(str(path) for path in paths),
        additional_paths=(),
        factories=(),
        no_extensions=False,
        generation=1,
        apply_override=True,
        override=None,
    )
```

- [ ] **Step 2: Write failing error-isolation and pre-trust tests**

```python
def test_bad_extension_is_diagnostic_while_valid_extension_loads(tmp_path: Path) -> None:
    root = tmp_path / "extensions"
    root.mkdir()
    (root / "bad.py").write_text("raise RuntimeError('broken extension')\n", encoding="utf-8")
    (root / "good.py").write_text(
        "def extension(travis):\n    travis.register_flag('safe', {'type': 'boolean'})\n",
        encoding="utf-8",
    )
    lease = load_extension_runtime(extension_request(tmp_path, root))
    try:
        assert lease.runtime.get_flag("safe") is False
        assert [Path(item["path"]).name for item in lease.result["errors"]] == ["bad.py"]
        assert [Path(item["path"]).name for item in lease.result["extensions"]] == ["good.py"]
    finally:
        lease.release()


def test_preloaded_runtime_does_not_reexecute_inline_factory(tmp_path: Path) -> None:
    calls: list[str] = []

    def factory(travis) -> None:
        calls.append("factory")
        travis.register_flag("profile", {"type": "string"})

    request = replace(extension_request(tmp_path), factories=(factory,), apply_override=False)
    preloaded = load_extension_runtime(request)
    adopted = load_extension_runtime(
        replace(request, generation=2, apply_override=True),
        preloaded=preloaded,
    )
    try:
        assert adopted is preloaded
        assert calls == ["factory"]
        assert "profile" in adopted.runtime.get_flags()
    finally:
        adopted.release()
```

- [ ] **Step 3: Verify missing-module failure**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_resource_extension_loader.py -q
```

Expected: import failure for `resource_extensions`.

- [ ] **Step 4: Implement the extension lease**

Use an `RLock`, one initial reference, `retain()` for shared candidates, and final release that disposes the runtime and removes generated modules. Double retain/release after final disposal raises `RuntimeError`.

```python
def release(self) -> None:
    with self._lock:
        if self._released:
            raise RuntimeError("extension runtime lease is released")
        self._references -= 1
        if self._references:
            return
        self._released = True
    self._runtime.dispose()
    for module_name in self._module_names:
        sys.modules.pop(module_name, None)
```

`create_empty_extension_runtime(cwd, event_bus)` returns the exact existing empty result: `extensions=[]`, `errors=[]`, and one `ExtensionRunner`.

- [ ] **Step 5: Move extension discovery exactly**

Move `_update_extensions()`, `_load_extension_module()`, and `_run_extension_factory()` behavior from `resource_loader.py`. Preserve path order, real-path deduplication, `collect_resource_files`, missing/raising diagnostics, inline-factory rules, provider-registration attribution, owner scope, override timing, and module names `_travis234_extension_<digest>_<generation>`.

```python
module = ModuleType(module_name)
module.__file__ = str(path)
module.__package__ = module_name
module.__path__ = [str(path.parent)]  # type: ignore[attr-defined]
sys.modules[module_name] = module
exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), module.__dict__)
```

Build a replacement before releasing the current lease. If an override returns a malformed runtime result, release the candidate and raise `TypeError`; the old runtime remains active.

- [ ] **Step 6: Delegate the loader's private extension methods**

Initialize `_extension_lease` with `create_empty_extension_runtime()`. Rewrite `_update_extensions()` to call `load_extension_runtime()`, swap `self._extension_lease` and `self.extensions_result`, then release the replaced lease. Remember the pre-trust lease so `preloaded_result` must match its exact `result` object. Remove the old compiler/factory helpers and unused imports.

- [ ] **Step 7: Verify focused lifecycle and hygiene**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_resource_extension_loader.py tests/test_extension_loading_and_reload.py tests/test_coding_resources_and_services.py::test_default_resource_loader_ports_travis234_inline_extension_factories tests/test_coding_resources_and_services.py::test_staged_resource_reload_reuses_pretrust_runtime_without_reexecuting_factories -q
PYTHONPATH=. .venv/bin/python -m pytest tests/architecture/test_repository_hygiene.py -q
```

Expected: all tests pass and hygiene finds no duplicate implementation.

- [ ] **Step 8: Commit Task 3**

```bash
git add travis/coding_agent/resource_extensions.py travis/coding_agent/resource_loader.py tests/test_resource_extension_loader.py tests/test_extension_loading_and_reload.py
git commit -m "refactor(resources): isolate extension runtime candidates"
```

---

### Task 4: Non-extension resource candidates

**Files:**
- Create: `travis/coding_agent/resource_candidates.py`
- Modify: `travis/coding_agent/resource_loader.py:35-101,351-402,526-713`
- Modify: `tests/test_resource_runtime_parity.py`
- Modify: `tests/test_coding_resources_and_services.py`

**Interfaces:**
- Consumes: `ResolvedPaths`, current loaders/overrides, and exact current path order.
- Produces: `ResourceContentRequest`, `ResourceContentCandidate`, `build_resource_content()`, `extend_resource_content()`, record/diagnostic adapters.

- [ ] **Step 1: Add a failing precedence regression**

```python
def test_candidate_preserves_package_before_bundled_skill_precedence(tmp_path: Path) -> None:
    project, agent_dir, package = make_resource_roots(tmp_path)
    package_skill = write_skill(package / "skills/web-search/SKILL.md", "web-search", "package winner")
    write_package_manifest(package, skills=["skills/web-search/SKILL.md"])
    loader = DefaultResourceLoader(
        cwd=str(project), agent_dir=str(agent_dir),
        project_trusted=False, package_paths=[str(package)],
    )
    loader.reload()

    skill = next(item for item in loader.get_skills()["skills"] if item.name == "web-search")
    assert skill.description == "package winner"
    assert skill.source_info.origin == "package"
    collisions = [item for item in loader.get_skills()["diagnostics"] if item.type == "collision"]
    assert any(item.collision and item.collision["winnerPath"] == str(package_skill) for item in collisions)
```

Implement `make_resource_roots()`, `write_skill()`, and `write_package_manifest()` completely in the test file. Retain the existing test proving an app-owned user skill beats the bundled default when no package wins first.

```python
def make_resource_roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    project = tmp_path / "repo"
    agent_dir = tmp_path / "agent"
    package = tmp_path / "package"
    project.mkdir()
    agent_dir.mkdir()
    package.mkdir()
    return project, agent_dir, package


def write_skill(path: Path, name: str, description: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody\n",
        encoding="utf-8",
    )
    return path


def write_package_manifest(package: Path, *, skills: list[str]) -> None:
    (package / "package.json").write_text(
        json.dumps({"name": "fixture", "travis": {"skills": skills}}),
        encoding="utf-8",
    )
```

- [ ] **Step 2: Add failing purity and extension-resource tests**

```python
def test_content_build_does_not_mutate_previous_candidate(tmp_path: Path) -> None:
    prompt = write_prompt(tmp_path / "prompts/review.md", "v1")
    request = content_request(tmp_path, prompt_paths=(str(prompt.parent),))
    first = build_resource_content(request)
    write_prompt(prompt, "v2")
    second = build_resource_content(request)

    assert first.prompts_result["prompts"][0].content == "Prompt v1"
    assert second.prompts_result["prompts"][0].content == "Prompt v2"


def test_extend_builds_skill_prompt_and_theme_as_one_candidate(tmp_path: Path) -> None:
    initial = build_resource_content(content_request(tmp_path))
    skill, prompt, theme = write_extension_resources(tmp_path)
    extended = extend_resource_content(
        initial, cwd=str(tmp_path),
        skill_paths=(str(skill),), prompt_paths=(str(prompt),), theme_paths=(str(theme),),
        skills_override=None, prompts_override=None, themes_override=None,
    )
    assert [item.name for item in extended.skills_result["skills"]] == ["extension-skill"]
    assert [item.name for item in extended.prompts_result["prompts"]] == ["extension-prompt"]
    assert [item.name for item in extended.themes_result["themes"]] == ["extension-theme"]
    assert initial.skills_result["skills"] == []
```

Test helpers create valid frontmatter/JSON and an empty `ResolvedPaths`; no host state is read.

```python
def write_prompt(path: Path, version: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ndescription: {version}\n---\nPrompt {version}\n",
        encoding="utf-8",
    )
    return path


def content_request(tmp_path: Path, *, prompt_paths: tuple[str, ...] = ()) -> ResourceContentRequest:
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir(exist_ok=True)
    return ResourceContentRequest(
        cwd=str(tmp_path),
        agent_dir=str(agent_dir),
        project_trusted=False,
        resolved_paths=ResolvedPaths(),
        additional_skill_paths=(),
        additional_prompt_paths=prompt_paths,
        additional_theme_paths=(),
        no_context_files=True,
        no_skills=True,
        no_prompt_templates=False,
        no_themes=False,
        system_prompt_source=None,
        append_system_prompt_source=None,
        agents_files_override=None,
        skills_override=None,
        prompts_override=None,
        themes_override=None,
        system_prompt_override=None,
        append_system_prompt_override=None,
    )


def write_extension_resources(tmp_path: Path) -> tuple[Path, Path, Path]:
    skill = write_skill(tmp_path / "skills/extension-skill/SKILL.md", "extension-skill", "skill")
    prompt = tmp_path / "prompts/extension-prompt.md"
    prompt.parent.mkdir()
    prompt.write_text("---\ndescription: prompt\n---\nPrompt\n", encoding="utf-8")
    theme = tmp_path / "themes/extension-theme.json"
    theme.parent.mkdir()
    theme.write_text(
        json.dumps({"name": "extension-theme", "colors": {}, "vars": {}}),
        encoding="utf-8",
    )
    return skill, prompt, theme
```

- [ ] **Step 3: Verify missing-module failure**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_resource_runtime_parity.py -q
```

Expected: import failure for `resource_candidates`.

- [ ] **Step 4: Extract pure non-extension loading**

Move these exact helper bodies into `resource_candidates.py` and re-export them from `resource_loader.py`: `load_context_file_from_dir`, `load_project_context_files`, and `load_themes`. Also move `_resolve_prompt_input`, `_resource_paths`, `_merge_paths`, `_resolve_path`, and `_source_info_for_path`.

`build_resource_content()` preserves current path construction:

```python
skill_paths = _merge_paths(
    request.cwd,
    [item.path for item in request.resolved_paths.skills if item.enabled],
    [*request.additional_skill_paths, get_packaged_skills_path()]
    if not request.no_skills else list(request.additional_skill_paths),
)
prompt_paths = _merge_paths(
    request.cwd,
    [item.path for item in request.resolved_paths.prompts if item.enabled],
    list(request.additional_prompt_paths),
)
theme_paths = _merge_paths(
    request.cwd,
    [item.path for item in request.resolved_paths.themes if item.enabled],
    list(request.additional_theme_paths),
)
```

Preserve `metadata_by_path`, override order, trusted project system prompts, global fallback, context order, diagnostic objects, and independent result dictionaries.

- [ ] **Step 5: Adapt loaded values to capability records**

Use exact keys: `Skill.name`, `PromptTemplate.name`, `Theme.name`, and canonical context path. Copy every `SourceInfo` field into `CapabilitySource` with provider `default-resources`.

```python
CapabilityRecord(
    CapabilityKind.SKILL,
    skill.name,
    skill,
    CapabilitySource(
        "default-resources", skill.source_info.path,
        skill.source_info.source, skill.source_info.scope, skill.source_info.origin,
    ),
)
```

Convert legacy parser diagnostics to capability diagnostics while retaining the original objects in legacy getters. Codes are `resource_collision` and `resource_warning`.

- [ ] **Step 6: Delegate the loader's content block**

Build one `ResourceContentRequest` after the single `package_manager.resolve()` call. Apply the successful candidate in one helper that assigns skills, prompts, themes, context, system prompts, package diagnostics, and last path lists. Rewrite `extend_resources()` to call `extend_resource_content()` once before applying any field.

- [ ] **Step 7: Verify resource parity and hygiene**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_resource_runtime_parity.py tests/test_coding_resources_and_services.py tests/test_extension_loading_and_reload.py -q
PYTHONPATH=. .venv/bin/python -m pytest tests/architecture/test_repository_hygiene.py -q
```

Expected: all tests pass with no duplicate helpers.

- [ ] **Step 8: Commit Task 4**

```bash
git add travis/coding_agent/resource_candidates.py travis/coding_agent/resource_loader.py tests/test_resource_runtime_parity.py tests/test_coding_resources_and_services.py
git commit -m "refactor(resources): build isolated resource candidates"
```

---

### Task 5: Atomic `DefaultResourceLoader` cutover

**Files:**
- Modify: `travis/coding_agent/resource_candidates.py`
- Modify: `travis/coding_agent/resource_loader.py:103-575`
- Modify: `tests/test_coding_resources_and_services.py`
- Modify: `tests/test_extension_loading_and_reload.py`

**Interfaces:**
- Consumes: Tasks 2–4.
- Produces: `ResourceLoadCandidate`, internal `DefaultResourceCapabilityProvider`, atomic reload/extend, `get_capability_snapshot()`. A failed candidate retains the complete previous snapshot.

Use these complete test helpers for Steps 1–3:

```python
def loaded_resource_loader(tmp_path: Path) -> DefaultResourceLoader:
    project = tmp_path / "repo"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    agent_dir.mkdir()
    loader = DefaultResourceLoader(
        cwd=str(project), agent_dir=str(agent_dir), project_trusted=False,
    )
    loader.reload()
    return loader


def write_prompt_directory(tmp_path: Path, name: str) -> Path:
    prompt_dir = tmp_path / "extension-prompts"
    prompt_dir.mkdir(exist_ok=True)
    (prompt_dir / f"{name}.md").write_text(
        f"---\ndescription: {name}\n---\n{name}\n",
        encoding="utf-8",
    )
    return prompt_dir
```

- [ ] **Step 1: Add a failing rollback regression**

```python
def test_failed_candidate_keeps_runtime_getters_and_snapshot(tmp_path: Path) -> None:
    loader = loaded_resource_loader(tmp_path)
    previous_extensions = loader.get_extensions()
    previous_skills = loader.get_skills()
    previous_snapshot = loader.get_capability_snapshot()
    loader.skills_override = lambda _value: (_ for _ in ()).throw(RuntimeError("candidate rejected"))

    with pytest.raises(CapabilityReloadError, match="candidate rejected"):
        loader.reload()

    assert loader.get_extensions() is previous_extensions
    assert loader.get_skills() is previous_skills
    assert loader.get_capability_snapshot() is previous_snapshot
```

- [ ] **Step 2: Add a failing reader-atomicity regression**

Block `prompts_override` with two events during generation 2. While blocked, assert the getter is the old dictionary containing `Prompt v1`; after release/join, assert it is a new dictionary containing `Prompt v2`. The worker must finish within two seconds.

- [ ] **Step 3: Add a failing dynamic-resource regression**

```python
def test_extend_resources_swaps_content_without_replacing_runtime(tmp_path: Path) -> None:
    loader = loaded_resource_loader(tmp_path)
    runtime = loader.get_extensions()["runtime"]
    old_snapshot = loader.get_capability_snapshot()
    prompt_dir = write_prompt_directory(tmp_path, "review")

    loader.extend_resources({"promptPaths": [{"path": str(prompt_dir)}]})

    assert loader.get_extensions()["runtime"] is runtime
    assert loader.get_capability_snapshot() is not old_snapshot
    assert [item.name for item in loader.get_prompts()["prompts"]] == ["review"]
```

Exercise the same path through the existing `resources_discover` extension hook in `test_agent_session_reload_emits_lifecycle_and_rediscover_resources`; direct `extend_resources()` and hook-driven discovery must commit identical candidate generations.

- [ ] **Step 4: Verify missing-integration failures**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_coding_resources_and_services.py tests/test_extension_loading_and_reload.py -q
```

Expected: new tests fail because snapshot access and candidate-wide rollback are absent.

- [ ] **Step 5: Implement the combined candidate/provider**

`ResourceLoadCandidate` owns one extension lease plus one content candidate, cached records, and diagnostics. `close()` releases the lease. The provider accepts a typed `ResourceLoadRequest` from `context.data["resource_request"]`:

```python
class DefaultResourceCapabilityProvider:
    name = "default-resources"
    priority = 0

    def load(self, context: CapabilityLoadContext) -> CapabilityProviderResult:
        request = context.data.get("resource_request")
        if not isinstance(request, ResourceLoadRequest):
            raise TypeError("resource_request must be a ResourceLoadRequest")
        candidate = request.build()
        return CapabilityProviderResult(
            candidate.records, candidate.diagnostics,
            candidate, candidate.close,
        )
```

Its empty constructor is explicit and contains no shared mutable list:

```python
@classmethod
def empty(cls, extensions: ExtensionRuntimeLease) -> "ResourceLoadCandidate":
    content = ResourceContentCandidate(
        skills_result={"skills": [], "diagnostics": []},
        prompts_result={"prompts": [], "diagnostics": []},
        themes_result={"themes": [], "diagnostics": []},
        agents_files=(),
        system_prompt=None,
        append_system_prompt=(),
        package_diagnostics=(),
        skill_paths=(),
        prompt_paths=(),
        theme_paths=(),
        metadata_by_path=MappingProxyType({}),
    )
    return cls(extensions=extensions, content=content, records=(), diagnostics=())
```

`ResourceLoadRequest.full()` builds a new extension lease and content. `ResourceLoadRequest.extend()` retains the current lease and builds replacement content. Any failed build releases the newly acquired lease before raising.

The combined candidate concatenates extension records before content records, matching current load order. Convert each extension error to `CapabilityDiagnostic("error", "default-resources", "extension_load_failed", ...)` and each `PackageDiagnostic` to `CapabilityDiagnostic` with code `package_resolution_warning`. These are nonfatal candidate diagnostics; unexpected provider/builder exceptions still reject the entire candidate.

- [ ] **Step 6: Store one live projection**

In `DefaultResourceLoader.__init__`, create the provider/registry, create an empty candidate, register the provider, seed its result, and store it as `_projection`. Add `_reload_lock` and `_state_lock`.

```python
def get_extensions(self) -> dict[str, object]:
    with self._state_lock:
        return self._projection.extensions.result


def get_capability_snapshot(self) -> CapabilitySnapshot:
    with self._state_lock:
        return self._capabilities.snapshot
```

All other getters read one local projection under the same lock. Never construct fresh legacy dictionaries inside getters; `get_extensions()` identity is an SDK contract.

- [ ] **Step 7: Commit registry snapshot and projection together**

Create a context whose data is `MappingProxyType({"resource_request": request})`. Pass `_commit_snapshot` to registry reload; it validates provider state and assigns `_projection` under `_state_lock`. The registry disposes the previous candidate only after this callback succeeds.

```python
def _commit_snapshot(self, snapshot: CapabilitySnapshot) -> None:
    candidate = snapshot.provider_state("default-resources")
    if not isinstance(candidate, ResourceLoadCandidate):
        raise TypeError("default resource provider returned invalid state")
    with self._state_lock:
        self._projection = candidate
```

Route `extend_resources()` through the same registry/commit path. Remove transitional mutable result fields after every private read uses `_projection`.

- [ ] **Step 8: Verify the integrated resource matrix**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_capability_registry.py tests/test_resource_extension_loader.py tests/test_extension_loading_and_reload.py tests/test_resource_runtime_parity.py tests/test_coding_resources_and_services.py -q
```

Expected: all tests pass, including failure retention, atomic readers, getter identity, and extension-resource discovery.

- [ ] **Step 9: Commit Task 5**

```bash
git add travis/coding_agent/capabilities travis/coding_agent/resource_candidates.py travis/coding_agent/resource_loader.py tests/test_coding_resources_and_services.py tests/test_extension_loading_and_reload.py
git commit -m "refactor(resources): atomically commit capability snapshots"
```

---

### Task 6: Trust bootstrap and extension lifecycle hardening

**Files:**
- Modify: `travis/coding_agent/resource_loader.py`
- Modify: `tests/test_coding_resources_and_services.py`
- Modify: `tests/test_extension_loading_and_reload.py`
- Modify: `tests/test_cli_runtime_controls.py`

**Interfaces:**
- Consumes: Task 5 atomic loader and current project-trust contracts.
- Produces: pre-trust lease transfer, active-trust rollback on load failure, unchanged extension-aware CLI bootstrap without modifying `travis/cli.py`.

- [ ] **Step 1: Add a failing trust-rollback test**

```python
def test_failed_trusted_candidate_restores_active_trust_and_runtime(tmp_path: Path) -> None:
    project = tmp_path / "repo"
    agent_dir = tmp_path / "agent"
    project_extension = project / ".travis234/extensions/project.py"
    project_extension.parent.mkdir(parents=True)
    agent_dir.mkdir()
    project_extension.write_text(
        "def extension(travis):\n    travis.register_flag('project-only', {'type': 'boolean'})\n",
        encoding="utf-8",
    )
    loader = DefaultResourceLoader(
        cwd=str(project), agent_dir=str(agent_dir), project_trusted=False,
    )
    loader.reload()
    previous_runtime = loader.get_extensions()["runtime"]
    loader.skills_override = lambda _value: (_ for _ in ()).throw(
        RuntimeError("reject trusted candidate"))

    with pytest.raises(CapabilityReloadError, match="reject trusted candidate"):
        loader.complete_reload({"projectTrustOverride": True})

    assert loader.project_trusted is False
    assert loader.package_manager.project_trusted is False
    assert loader.get_extensions()["runtime"] is previous_runtime
    assert previous_runtime.get_flag("project-only") is None
```

- [ ] **Step 2: Add a failing bootstrap-ownership test**

```python
def test_pretrust_runtime_transfers_once_then_disposes_on_replacement(tmp_path: Path) -> None:
    calls: list[str] = []

    def factory(travis) -> None:
        calls.append("factory")
        travis.register_flag("profile", {"type": "string"})

    loader = DefaultResourceLoader(
        cwd=str(tmp_path), agent_dir=str(tmp_path / "agent"),
        extension_factories=[factory],
    )
    pretrust = loader.load_project_trust_extensions()
    first_runtime = pretrust["runtime"]
    loader.complete_reload(
        {"projectTrustOverride": False}, pretrust_extensions=pretrust,
    )
    assert loader.get_extensions()["runtime"] is first_runtime
    assert calls == ["factory"]

    loader.reload({"projectTrustOverride": False})
    assert loader.get_extensions()["runtime"] is not first_runtime
    assert calls == ["factory", "factory"]
```

Register an event handler in the factory and assert only the replacement handler runs after reload; this proves the old event-bus registration was disposed.

- [ ] **Step 3: Add a CLI bootstrap isolation test**

Using the existing CLI faux-provider fixture, create a global extension in the injected agent directory with one typed boolean flag. Invoke `main()` with that flag and `--no-approve`; create a project extension whose only behavior is writing a marker. Assert:

```python
assert exit_code == 0
assert bootstrap_calls == ["factory"]
assert not project_marker.exists()
```

The fixture never touches the real home directory or loads credentials.

- [ ] **Step 4: Verify the new lifecycle tests fail**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_coding_resources_and_services.py tests/test_extension_loading_and_reload.py tests/test_cli_runtime_controls.py -q
```

Expected: trust rollback, lease transfer, or handler disposal fails before hardening.

- [ ] **Step 5: Implement bootstrap lease ownership**

`load_project_trust_extensions()` remembers one bootstrap lease but does not replace the committed registry snapshot. It returns `lease.result` for provisional flag parsing. `complete_reload()` verifies exact origin, retains the lease for the candidate, and clears/releases bootstrap ownership after success or failure.

```python
if self._pretrust_extensions is None:
    raise ValueError("no pre-trust extension candidate is active")
if pretrust_extensions is not self._pretrust_extensions.result:
    raise ValueError("pretrust_extensions did not originate from this loader")
```

Inline factories execute once during a staged load. Candidate failure releases its lease/modules. A successful snapshot owns exactly one reference.

- [ ] **Step 6: Restore active trust on candidate failure**

```python
previous_trust = self.project_trusted
self._set_project_trusted(bool(trusted))
try:
    self._reload_capabilities(resource_request)
except Exception:
    self._set_project_trusted(previous_trust)
    raise
```

Successful commits retain the resolved trust. Persisted user decisions remain owned by `resolve_project_trust`; this rollback only repairs the active loader/package-manager projection after a resource-load failure.

- [ ] **Step 7: Run bootstrap, extension, and CLI suites**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_cli_runtime_controls.py tests/test_extension_cli.py tests/test_extension_loading_and_reload.py tests/test_extension_event_parity.py tests/test_coding_policy_and_extensions.py tests/test_coding_resources_and_services.py -q
```

Expected: all selected tests pass, including typed flags, pre-trust execution count, project isolation, reload events, runtime rebinding, and handler cleanup.

- [ ] **Step 8: Commit Task 6**

```bash
git add travis/coding_agent/resource_loader.py tests/test_coding_resources_and_services.py tests/test_extension_loading_and_reload.py tests/test_cli_runtime_controls.py
git commit -m "fix(resources): harden atomic trust bootstrap"
```

---

### Task 7: Real-loader explanations and architecture gates

**Files:**
- Modify: `tests/test_resource_runtime_parity.py`
- Modify: `tests/architecture/test_facade_boundaries.py`

**Interfaces:**
- Consumes: the completed Phase 1A loader and snapshot API.
- Produces: end-to-end explanations and enforced owner boundaries.

- [ ] **Step 1: Add a failing all-kind explanation test**

```python
def test_loader_explains_every_phase_one_resource_source(tmp_path: Path) -> None:
    loader, paths = configured_loader_with_every_kind(tmp_path)
    loader.reload()
    snapshot = loader.get_capability_snapshot()
    cases = (
        (CapabilityKind.SKILL, "audit", paths["skill"]),
        (CapabilityKind.PROMPT_TEMPLATE, "review", paths["prompt"]),
        (CapabilityKind.THEME, "night", paths["theme"]),
        (CapabilityKind.CONTEXT_FILE, paths["context"], paths["context"]),
        (CapabilityKind.EXTENSION, paths["extension"], paths["extension"]),
    )
    for kind, key, expected_path in cases:
        resolution = snapshot.resolve(kind, key)
        assert resolution.winner is not None
        assert resolution.winner.source.path == expected_path
        assert resolution.winner.source.provider == "default-resources"
```

`configured_loader_with_every_kind()` creates one valid skill, prompt, theme, context file, and extension; uses their explicit paths on a trusted loader; and returns canonical resolved path strings.

```python
def configured_loader_with_every_kind(
    tmp_path: Path,
) -> tuple[DefaultResourceLoader, dict[str, str]]:
    project = tmp_path / "repo"
    agent_dir = tmp_path / "agent"
    project.mkdir()
    agent_dir.mkdir()
    context = project / "AGENTS.md"
    context.write_text("repository context\n", encoding="utf-8")
    skill = write_skill(project / "skills/audit/SKILL.md", "audit", "audit code")
    prompt = project / "prompts/review.md"
    prompt.parent.mkdir()
    prompt.write_text("---\ndescription: review\n---\nReview\n", encoding="utf-8")
    theme = project / "themes/night.json"
    theme.parent.mkdir()
    theme.write_text(
        json.dumps({"name": "night", "colors": {}, "vars": {}}),
        encoding="utf-8",
    )
    extension = project / "extensions/sample.py"
    extension.parent.mkdir()
    extension.write_text("def extension(travis):\n    return None\n", encoding="utf-8")
    loader = DefaultResourceLoader(
        cwd=str(project),
        agent_dir=str(agent_dir),
        project_trusted=True,
        additional_skill_paths=[str(skill)],
        additional_prompt_template_paths=[str(prompt)],
        additional_theme_paths=[str(theme)],
        additional_extension_paths=[str(extension)],
    )
    return loader, {
        "skill": str(skill.resolve()),
        "prompt": str(prompt.resolve()),
        "theme": str(theme.resolve()),
        "context": str(context.resolve()),
        "extension": str(extension.resolve()),
    }
```

- [ ] **Step 2: Add every new owner to architecture enforcement**

Extend `OWNER_GLOBS` without removing existing entries:

```python
"travis/coding_agent/capabilities/*.py",
"travis/coding_agent/resource_candidates.py",
"travis/coding_agent/resource_extensions.py",
"travis/coding_agent/resource_loader.py",
```

Do not raise the 750-line limit. Split a module by responsibility if it exceeds the bound. New owners may not import `travis.app`, `travis.coding_agent.agent_session`, or TUI façades.

- [ ] **Step 3: Run explanation and architecture tests**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_resource_runtime_parity.py tests/architecture/test_facade_boundaries.py tests/architecture/test_repository_hygiene.py tests/test_agent_core_boundary.py tests/test_provider_ownership_architecture.py tests/test_compaction_boundary_architecture.py -q
```

Expected: all selected tests pass.

- [ ] **Step 4: Run the complete focused resource matrix**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests/test_capability_registry.py tests/test_resource_extension_loader.py tests/test_resource_runtime_parity.py tests/test_extension_loading_and_reload.py tests/test_coding_resources_and_services.py tests/test_coding_policy_and_extensions.py tests/test_extension_event_parity.py tests/test_extension_cli.py tests/test_cli_runtime_controls.py tests/test_eval_harness.py -q
```

Expected: zero failures.

- [ ] **Step 5: Audit forbidden paths and whitespace**

```bash
git diff --exit-code HEAD -- travis/agent travis/compaction travis/ai/providers packages/travis234-cli packages/travis234-mcp-adapter
git diff --check
git status --short
```

Expected: forbidden-path and whitespace checks exit zero. Status contains Phase 1A files plus the pre-existing unstaged `.gitignore`; no OMP file appears.

- [ ] **Step 6: Commit Task 7**

```bash
git add tests/test_resource_runtime_parity.py tests/architecture/test_facade_boundaries.py
git commit -m "test(resources): enforce capability registry boundaries"
```

---

### Task 8: Repository and release verification

**Files:**
- No production or test changes expected.

**Interfaces:**
- Consumes: complete Phase 1A implementation.
- Produces: fresh Python, npm, distribution, acceptance, and container evidence.

- [ ] **Step 1: Run the complete Python suite**

```bash
PYTHONPATH=. .venv/bin/python -m pytest tests -q
```

Expected: zero failures; record exact pass count and elapsed time.

- [ ] **Step 2: Run npm launcher and package inventory**

```bash
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Expected: launcher tests pass; inventory contains only declared files and no research tree.

- [ ] **Step 3: Build Python distributions outside the worktree**

```bash
phase1a_dist_dir=$(mktemp -d)
.venv/bin/python -m build --outdir "$phase1a_dist_dir"
.venv/bin/python -m zipfile -l "$phase1a_dist_dir"/*.whl
```

Expected: wheel/sdist build. Wheel contains the new capability/resource modules and excludes `oh-my-pi/`, `.env`, and workspace research files.

- [ ] **Step 4: Run acceptance mapping**

```bash
.venv/bin/python scripts/verify_acceptance.py --parity-json
```

Expected: exit zero with every mapped contract satisfied.

- [ ] **Step 5: Build and smoke the release container without credentials**

```bash
docker build --no-cache -f Dockerfile.release -t travis234:capability-registry .
.venv/bin/python evals/container_smoke.py --image travis234:capability-registry
```

Expected: build and smoke exit zero as the unprivileged `travis` user. Supply no dotenv or host credential file.

- [ ] **Step 6: Verify final Git scope**

```bash
git diff --check
git diff --exit-code origin/main...HEAD -- travis/agent travis/compaction travis/ai/providers packages/travis234-cli packages/travis234-mcp-adapter
git log --oneline --decorate origin/main..HEAD
git status --short --branch
```

Expected: no forbidden diff; design, plan, and seven implementation commits are visible; the pre-existing `.gitignore` remains unstaged; no OMP file is tracked.

Do not push, publish, tag, modify permissions, or begin Phase 1B. Report evidence and wait for authorization.

---

## Execution stop condition

This plan is documentation only until the user explicitly approves execution. On approval, create an isolated worktree with `superpowers:using-git-worktrees`, then use exactly one execution mode:

1. `superpowers:subagent-driven-development` only if the user explicitly authorizes subagents; repository instructions otherwise prohibit them.
2. `superpowers:executing-plans` for inline execution with review checkpoints.

No Task 1 implementation command may run before that approval.
