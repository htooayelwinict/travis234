# Provider Ownership, Session Index, and Bash Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give provider state one injected owner, make session listing independent of history bytes, and replace the binary bash mutation heuristic with an explicitly advisory three-state classifier.

**Architecture:** Extend `ModelRegistry` with public mutation/snapshot operations and construct a fresh provider registry per control plane. Add a derived SQLite session index updated after JSONL persistence and backfilled with bounded header/tail reads. Extract conservative shell classification into a pure module whose result can influence progress bookkeeping but never authorization.

**Tech Stack:** Python 3.13, SQLite, dataclasses, enums, pytest, AST architecture assertions.

## Global Constraints

- Use only `travis` imports and `TRAVIS234_*` configuration.
- Provider state for two sessions/control planes must not share mutable registries, models, credentials, or extension stacks.
- JSONL remains the authoritative session history; SQLite is derived and rebuildable.
- Warm session listing decodes zero history records and reads zero JSONL bytes.
- Cold backfill reads at most 8 KiB header + 64 KiB tail and decodes at most 257 records per changed/unindexed file.
- Bash classification has exactly `read_only`, `mutating`, and `unknown`; it never grants or denies execution.
- Agent-loop and compaction behavior are unchanged.

---

### Task 1: Encode public provider ownership

**Files:**
- Modify: `tests/test_provider_control_plane.py`
- Create: `tests/architecture/test_provider_ownership.py`
- Modify later: `travis/coding_agent/model_registry.py`
- Modify later: `travis/coding_agent/provider_control_plane.py`

**Interfaces:**
- Produces tests for `ModelRegistry.snapshot/register_model/replace_model/remove_model/remove_provider_models/replace_all/resolve_fallback_api_key/unregister_provider`.
- Produces an AST/source boundary that forbids private collection access outside `model_registry.py`.

- [ ] **Step 1: Write failing isolation and public-operation tests**

```python
def test_default_control_planes_do_not_share_models_or_api_providers(tmp_path) -> None:
    left = ProviderControlPlane.create_default({"auth": tmp_path / "left-auth.json", "models": tmp_path / "left-models.json"})
    right = ProviderControlPlane.create_default({"auth": tmp_path / "right-auth.json", "models": tmp_path / "right-models.json"})
    model = Model(id="private", name="Private", api="openai-completions", provider="isolated", base_url="https://example.invalid", reasoning=False, input=["text"], cost=zero_cost(), context_window=1_000, max_tokens=100)

    left.ensure_model(model)

    assert left.models.find("isolated", "private") is model
    assert right.models.find("isolated", "private") is None
    assert left.api_providers is not right.api_providers


def test_discovered_model_replacement_is_local_to_plane() -> None:
    left = ProviderControlPlane.in_memory()
    right = ProviderControlPlane.in_memory()
    original = make_model("provider", "model", name="Original")
    replacement = replace(original, name="Replacement")
    left.ensure_model(original)
    right.ensure_model(original)
    left.merge_discovered_models([replacement])
    assert left.models.find("provider", "model").name == "Replacement"
    assert right.models.find("provider", "model").name == "Original"
```

- [ ] **Step 2: Write the failing architecture test**

```python
from pathlib import Path


def test_provider_consumers_do_not_access_registry_privates() -> None:
    root = Path(__file__).parents[2] / "travis"
    forbidden = ("._models", "._registered_providers", "._fallback_api_key", "_DEFAULT_API_PROVIDER_REGISTRY")
    failures = []
    for path in root.rglob("*.py"):
        if path.name in {"model_registry.py", "stream.py"}:
            continue
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            if token in text:
                failures.append(f"{path.relative_to(root)}: {token}")
    assert failures == []
```

- [ ] **Step 3: Run provider tests to verify red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_provider_control_plane.py tests/architecture/test_provider_ownership.py -k 'default_control_planes or replacement_is_local or registry_privates' -q`

Expected: FAIL because `create_default()` shares `_DEFAULT_API_PROVIDER_REGISTRY` and the control plane mutates private model collections.

- [ ] **Step 4: Commit the red provider contract**

```bash
git add tests/test_provider_control_plane.py tests/architecture/test_provider_ownership.py
git commit -m "test: define provider ownership boundary"
```

### Task 2: Implement public `ModelRegistry` operations

**Files:**
- Modify: `travis/coding_agent/model_registry.py`
- Modify: `tests/test_model_registry.py` or create it if registry tests remain embedded.

**Interfaces:**
- Produces the exact public operations consumed by Task 3.

- [ ] **Step 1: Add focused failing registry-operation tests**

```python
def test_registry_public_model_operations(registry) -> None:
    first = make_model("p", "m", name="First")
    second = replace(first, name="Second")
    assert registry.register_model(first) is True
    assert registry.register_model(first) is False
    assert registry.snapshot() == (first,)
    assert registry.replace_model(second) is first
    assert registry.snapshot() == (second,)
    assert registry.remove_model("p", "m") is second
    assert registry.snapshot() == ()


def test_replace_all_copies_input_and_preserves_order(registry) -> None:
    models = [make_model("p", "a"), make_model("p", "b")]
    registry.replace_all(models)
    models.clear()
    assert [model.id for model in registry.snapshot()] == ["a", "b"]
```

- [ ] **Step 2: Run to verify red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_model_registry.py -q`

Expected: FAIL because the public operations do not exist.

- [ ] **Step 3: Add public operations with lock-protected ownership**

```python
def snapshot(self) -> tuple[Model, ...]:
    with self._lock:
        return tuple(self._models)


def register_model(self, model: Model) -> bool:
    with self._lock:
        if self.find(model.provider, model.id) is not None:
            return False
        self._models.append(model)
        return True


def replace_model(self, model: Model) -> Model | None:
    with self._lock:
        for index, existing in enumerate(self._models):
            if (existing.provider, existing.id) == (model.provider, model.id):
                self._models[index] = model
                return existing
        self._models.append(model)
        return None


def remove_model(self, provider: str, model_id: str) -> Model | None:
    with self._lock:
        for index, existing in enumerate(self._models):
            if (existing.provider, existing.id) == (provider, model_id):
                return self._models.pop(index)
    return None


def remove_provider_models(self, provider: str) -> tuple[Model, ...]:
    with self._lock:
        removed = tuple(model for model in self._models if model.provider == provider)
        self._models[:] = [model for model in self._models if model.provider != provider]
        return removed


def replace_all(self, models: Iterable[Model]) -> None:
    with self._lock:
        self._models[:] = list(models)


def resolve_fallback_api_key(self, provider: str) -> str | None:
    with self._lock:
        return self._fallback_api_key(provider)


def unregister_provider(self, provider: str) -> bool:
    with self._lock:
        if provider not in self._registered_providers:
            return False
        self._registered_providers.pop(provider)
    self.api_providers.unregister_source(f"provider:{provider}")
    self.refresh()
    return True
```

Add `self._lock = threading.RLock()` during construction and test present and
absent removals. `replace_models()` becomes `replace_all()`; compatibility aliases
are not retained.

- [ ] **Step 4: Run registry tests green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_model_registry.py -q`

Expected: PASS.

- [ ] **Step 5: Commit registry API**

```bash
git add travis/coding_agent/model_registry.py tests/test_model_registry.py
git commit -m "refactor: expose owned model registry operations"
```

### Task 3: Remove global provider state from the control plane

**Files:**
- Modify: `travis/coding_agent/provider_control_plane.py`
- Modify: `travis/ai/register_builtins.py`
- Modify: `travis/ai/stream.py`
- Modify: `travis/ai/models.py`
- Modify: `travis/cli.py`
- Modify: `travis/coding_agent/agent_session_services.py`
- Modify: `tests/test_provider_control_plane.py`
- Modify: `tests/test_ai_stream.py`

**Interfaces:**
- `ProviderControlPlane.create_default(paths: Mapping[str, str | Path] | None = None) -> ProviderControlPlane`; no discarded environment parameter.
- `register_builtin_providers(registry: ApiProviderRegistry, *, prefix: str = "TRAVIS234_WORKER_LLM", dotenv_path: Path | None = None) -> tuple[ProviderRegistration, ...]`.

- [ ] **Step 1: Write failing builtin-registry injection test**

```python
def test_builtin_registration_targets_only_injected_registry() -> None:
    left = ApiProviderRegistry()
    right = ApiProviderRegistry()
    registrations = register_builtin_providers(left)
    try:
        assert left.get("openai-completions") is not None
        assert right.get("openai-completions") is None
    finally:
        for registration in registrations:
            registration.close()
```

- [ ] **Step 2: Run injection/isolation tests red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_ai_stream.py tests/test_provider_control_plane.py -k 'injected_registry or default_control_planes or replacement_is_local' -q`

Expected: FAIL on global registry sharing.

- [ ] **Step 3: Construct one isolated plane at the composition root**

`create_default()` creates a fresh `ApiProviderRegistry`, constructs
`ModelRegistry(auth, models_path, api_providers)`, registers builtins into that
registry, and stores the returned registrations for `close()`. Remove the unused
`environment` parameter.

Replace private calls exactly:

```python
def _fallback_resolver(self, provider: str) -> str | None:
    self._fallback_counts[provider] = self._fallback_counts.get(provider, 0) + 1
    return self.models.resolve_fallback_api_key(provider)


def ensure_model(self, model: Model) -> None:
    self.models.register_model(model)


def merge_discovered_models(self, models: Iterable[Model]) -> None:
    for model in models:
        self.models.replace_model(model)
```

Extension registration uses public provider-present/unregister methods. CLI
creates the plane before model hydration and passes that same object to startup,
session services, model listing, and extensions. Delete mutable global compatibility
facades from `ai/models.py` after all callers use the plane.

- [ ] **Step 4: Run provider boundary green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_provider_control_plane.py tests/test_ai_stream.py tests/test_cli.py tests/architecture/test_provider_ownership.py -q`

Expected: PASS.

- [ ] **Step 5: Commit provider ownership**

```bash
git add travis/coding_agent/provider_control_plane.py travis/coding_agent/model_registry.py travis/ai/register_builtins.py travis/ai/stream.py travis/ai/models.py travis/cli.py travis/coding_agent/agent_session_services.py tests/test_provider_control_plane.py tests/test_ai_stream.py tests/test_cli.py tests/architecture/test_provider_ownership.py
git commit -m "fix: isolate provider state per control plane"
```

### Task 4: Define the derived session index and performance counters

**Files:**
- Create: `travis/coding_agent/session_index.py`
- Create: `tests/test_session_index.py`
- Create: `tests/test_session_catalog_performance.py`

**Interfaces:**
- Produces `SessionIndexRecord`, `SessionScanStats`, and `SessionIndex` as declared below.

- [ ] **Step 1: Write failing index round-trip tests**

```python
def test_session_index_round_trips_summary(tmp_path: Path) -> None:
    index = SessionIndex(tmp_path / "catalog.sqlite3")
    record = SessionIndexRecord(path=tmp_path / "one.jsonl", session_id="one", cwd=tmp_path, created_at=timestamp(), modified_ns=10, size_bytes=20, device=1, inode=2, name="Demo", preview="hello", model="provider/model")
    index.upsert(record)
    assert index.query() == (record,)
    index.close()


def test_warm_reconcile_reads_no_history_bytes(tmp_path: Path) -> None:
    session = write_session(tmp_path / "large.jsonl", payload_bytes=100 * 1024 * 1024)
    index = SessionIndex(tmp_path / "catalog.sqlite3")
    cold = index.reconcile([session])
    warm = index.reconcile([session])
    assert cold.files_backfilled == 1
    assert cold.bytes_read <= 73_728
    assert cold.records_decoded <= 257
    assert warm == SessionScanStats(files_statted=1, cache_hits=1)
```

- [ ] **Step 2: Run index tests to verify red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_session_index.py tests/test_session_catalog_performance.py -q`

Expected: FAIL because `session_index` does not exist.

- [ ] **Step 3: Implement the exact data contracts**

```python
@dataclass(frozen=True)
class SessionIndexRecord:
    path: Path
    session_id: str
    cwd: Path
    created_at: datetime
    modified_ns: int
    size_bytes: int
    device: int
    inode: int
    name: str | None
    preview: str
    model: str | None


@dataclass(frozen=True)
class SessionScanStats:
    files_statted: int = 0
    files_backfilled: int = 0
    bytes_read: int = 0
    records_decoded: int = 0
    cache_hits: int = 0
```

`SessionIndex` owns a WAL-mode SQLite connection protected by an `RLock`, schema
version table, unique normalized path, and indexes on cwd, session ID, and
modified time. Its public methods are `upsert(record)`, `record_header(path,
header, stat)`, `record_append(path, entry, stat)`, `query(cwd=None)`,
`reconcile(paths)`, `remove_missing(paths)`, and `close()`.

Use this exact upsert statement inside the connection transaction:

```python
self._connection.execute(
    """INSERT INTO sessions
       (path, session_id, cwd, created_at, modified_ns, size_bytes, device, inode, name, preview, model)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(path) DO UPDATE SET
         session_id=excluded.session_id, cwd=excluded.cwd,
         created_at=excluded.created_at, modified_ns=excluded.modified_ns,
         size_bytes=excluded.size_bytes, device=excluded.device,
         inode=excluded.inode, name=excluded.name,
         preview=excluded.preview, model=excluded.model""",
    (
        str(record.path), record.session_id, str(record.cwd), record.created_at.isoformat(),
        record.modified_ns, record.size_bytes, record.device, record.inode,
        record.name, record.preview, record.model,
    ),
)
```

`query()` selects those eleven columns, optionally adds `WHERE cwd = ?`, orders
by `modified_ns DESC, path DESC`, and maps rows to `SessionIndexRecord`.
`remove_missing()` uses one transaction and parameterized `DELETE` statements;
an empty path collection deletes all rows. `close()` is idempotent and closes the
connection under the lock. `reconcile()` compares
`(st_dev, st_ino, st_size, st_mtime_ns)`. A changed/unindexed file reads no more
than 8192 bytes from the head and 65536 bytes from the tail, with at most 257
decoded records. Corrupt files yield per-path diagnostics and do not invalidate
other rows.

- [ ] **Step 4: Run index tests green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_session_index.py tests/test_session_catalog_performance.py -q`

Expected: PASS with exact byte/record counters.

- [ ] **Step 5: Commit the derived index**

```bash
git add travis/coding_agent/session_index.py tests/test_session_index.py tests/test_session_catalog_performance.py
git commit -m "feat: add bounded persistent session index"
```

### Task 5: Integrate session writes and catalog queries with the index

**Files:**
- Modify: `travis/coding_agent/session_store.py`
- Modify: `travis/coding_agent/session_catalog.py`
- Modify: `travis/cli.py`
- Modify: `tests/test_session_catalog.py`
- Modify: `tests/test_session_store_recovery.py`
- Modify: `tests/test_session_store_performance.py`

**Interfaces:**
- `SessionStore(path, cwd=..., index=None)` updates derived state after durable JSONL writes.
- `SessionCatalog(agent_dir, index=None)` queries index rows after reconciliation.

- [ ] **Step 1: Write failing warm-listing and recovery tests**

```python
def test_catalog_warm_listing_is_independent_of_history_size(tmp_path: Path) -> None:
    index = SessionIndex(tmp_path / "catalog.sqlite3")
    small = create_history(tmp_path / "sessions/small.jsonl", payload_bytes=1_024)
    large = create_history(tmp_path / "sessions/large.jsonl", payload_bytes=100 * 1024 * 1024)
    catalog = SessionCatalog(str(tmp_path), index=index)
    catalog.list_all()
    first = catalog.scan_stats
    listed = catalog.list_all()
    assert {info.path for info in listed} == {small.resolve(), large.resolve()}
    assert catalog.scan_stats.bytes_read == 0
    assert catalog.scan_stats.records_decoded == 0
    assert catalog.scan_stats.files_backfilled == 0


def test_store_append_updates_preview_without_catalog_history_read(tmp_path: Path) -> None:
    index = SessionIndex(tmp_path / "catalog.sqlite3")
    path = tmp_path / "sessions/demo.jsonl"
    store = SessionStore(str(path), cwd=str(tmp_path), index=index)
    store.append_message(UserMessage(content="new preview", timestamp=now_ms()))
    catalog = SessionCatalog(str(tmp_path), index=index)
    assert catalog.list_all()[0].preview == "new preview"
    assert catalog.scan_stats.bytes_read == 0
```

- [ ] **Step 2: Run integration tests red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_session_catalog.py tests/test_session_catalog_performance.py -k 'warm_listing or append_updates_preview' -q`

Expected: FAIL because catalog rereads every JSONL entry and store does not update an index.

- [ ] **Step 3: Update index after authoritative writes**

`SessionStore._write_header()` writes/flushes the JSONL header first, stats the
file, then calls `index.record_header()`. `_write_record()` appends and syncs the
record first, stats the file, then calls `index.record_append()`. If index update
fails, JSONL remains valid and a diagnostic is recorded; next reconcile rebuilds
the stale row. Message/session-info/model-change entries update preview/name/model.

- [ ] **Step 4: Query indexed summaries from the catalog**

`SessionCatalog.list_all()` and `list_for_cwd()`:

1. enumerate JSONL paths;
2. call `index.reconcile(paths)` and store `scan_stats`;
3. call `index.remove_missing(paths)`;
4. convert `index.query()` rows directly to `SessionInfo`;
5. sort by modified ns without `_read_jsonl()`.

`resolve()` may directly reconcile one explicit path. Remove full-history
`_read_jsonl()` from discovery; history loading remains owned by `SessionStore`.
CLI creates one index at `<agent_dir>/sessions/catalog.sqlite3` and injects it
into catalog/store/session services.

- [ ] **Step 5: Run session suites green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_session_index.py tests/test_session_catalog.py tests/test_session_catalog_performance.py tests/test_session_store_recovery.py tests/test_session_store_performance.py -q`

Expected: PASS; warm counters remain zero for both small and 100 MiB histories.

- [ ] **Step 6: Commit session indexing**

```bash
git add travis/coding_agent/session_store.py travis/coding_agent/session_catalog.py travis/cli.py tests/test_session_catalog.py tests/test_session_catalog_performance.py tests/test_session_store_recovery.py tests/test_session_store_performance.py
git commit -m "fix: index session discovery metadata"
```

### Task 6: Replace binary shell mutation guessing with advisory classification

**Files:**
- Create: `travis/coding_agent/policies/bash_classification.py`
- Create: `tests/policies/test_bash_classification.py`
- Modify: `travis/coding_agent/policies/tool_guardrails.py`
- Modify: `tests/test_coding_policy.py`
- Modify: `tests/test_agent_runtime_hardening.py`

**Interfaces:**
- Produces `BashMutationClass`, `BashMutationHint`, `classify_bash_mutation(command: str) -> BashMutationHint`.

- [ ] **Step 1: Write the failing classification matrix**

```python
@pytest.mark.parametrize("command", [
    "echo hi>file",
    "sed -i s/a/b/ file",
    "python -c \"from pathlib import Path; Path('x').write_text('y')\"",
    "git checkout -- file",
    "git restore file",
])
def test_detects_mutating_forms(command: str) -> None:
    assert classify_bash_mutation(command).classification is BashMutationClass.MUTATING


@pytest.mark.parametrize("command", ["printf '%s\\n' hi | sed -n 1p", "git status --short", "cat < input.txt"])
def test_accepts_fully_known_read_only_forms(command: str) -> None:
    assert classify_bash_mutation(command).classification is BashMutationClass.READ_ONLY


@pytest.mark.parametrize("command", ["make test", "python script.py", "$(dynamic_command)", "unterminated '"])
def test_unprovable_forms_are_unknown(command: str) -> None:
    assert classify_bash_mutation(command).classification is BashMutationClass.UNKNOWN
```

- [ ] **Step 2: Write the advisory-only integration test**

```python
def test_unknown_classification_never_creates_authorization_decision() -> None:
    hint = classify_bash_mutation("python script.py")
    decision = policy_decision_for_bash("python script.py")
    assert hint.classification is BashMutationClass.UNKNOWN
    assert decision is None or decision.allows_execution
    assert getattr(decision, "reason", "") != hint.reason
```

- [ ] **Step 3: Run tests to verify red**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/policies/test_bash_classification.py -q`

Expected: FAIL because the module does not exist; baseline helper also misses every required mutating example.

- [ ] **Step 4: Implement conservative pure classification**

```python
class BashMutationClass(str, Enum):
    READ_ONLY = "read_only"
    MUTATING = "mutating"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BashMutationHint:
    classification: BashMutationClass
    reason: str


def classify_bash_mutation(command: str) -> BashMutationHint:
    if not command.strip():
        return BashMutationHint(BashMutationClass.READ_ONLY, "empty command")
    if _contains_output_or_in_place_write(command):
        return BashMutationHint(BashMutationClass.MUTATING, "output or in-place write")
    try:
        segments = _parse_static_segments(command)
    except ValueError as error:
        return BashMutationHint(BashMutationClass.UNKNOWN, f"shell parse failed: {error}")
    classifications = tuple(_classify_segment(segment) for segment in segments)
    if BashMutationClass.MUTATING in classifications:
        return BashMutationHint(BashMutationClass.MUTATING, "known mutator")
    if all(value is BashMutationClass.READ_ONLY for value in classifications):
        return BashMutationHint(BashMutationClass.READ_ONLY, "fully allowlisted read-only command")
    return BashMutationHint(BashMutationClass.UNKNOWN, "command behavior is not statically known")
```

Detect attached redirects lexically before `shlex`, in-place flags for sed/perl,
known package/file/VCS mutators, and recognized `Path.write_*`/write-mode `open`
interpreter snippets. Arbitrary interpreters, scripts, build tools, substitutions,
and unrecognized executables are `UNKNOWN`.

- [ ] **Step 5: Integrate as progress bookkeeping only**

`tool_guardrails.py` generates semantic read keys only for `READ_ONLY`.
`MUTATING` and `UNKNOWN` conservatively reset mutation-sensitive progress/failure
memory. Remove `_bash_command_may_change_state()`. No `Allow`, `Block`, consent,
capability, or sandbox decision consumes `BashMutationHint`.

- [ ] **Step 6: Run policy/agent cross-check green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/policies/test_bash_classification.py tests/test_coding_policy.py tests/test_agent_runtime_hardening.py -q`

Expected: PASS.

- [ ] **Step 7: Commit advisory classifier**

```bash
git add travis/coding_agent/policies/bash_classification.py travis/coding_agent/policies/tool_guardrails.py tests/policies/test_bash_classification.py tests/test_coding_policy.py tests/test_agent_runtime_hardening.py
git commit -m "fix: classify shell mutation hints conservatively"
```
