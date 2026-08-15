# Travis234 Durable Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Repository guidance prohibits subagents unless the user explicitly requests them.

**Goal:** Make promoted, sanitized tool and subagent outputs readable after session close, resume, and fork while leaving ordinary small output and non-persistent sessions ephemeral.

**Architecture:** Add a content-addressed `DurableArtifactStore`, an append-only manifest beside each durable session JSONL file, and a narrow `ResourceRefResolver`. Keep `ArtifactRegistry` as the session-facing adapter so existing read/bash/process callers do not learn object paths or manifest internals.

**Tech Stack:** Python 3.13, immutable SHA-256 objects, JSONL manifests, atomic sibling-temp writes, `fsync`, existing `SessionFileLock`, pytest fault injection, and existing byte-paginated read tools.

## Global Constraints

- Create `codex/durable-artifacts` from the planning HEAD of `codex/model-role-router`, whose implementation tree is Phase 1B code commit `ec53c69`; first verify the delta from `ec53c69` contains only the seven new planning documents.
- Use only the existing agent directory below `~/.travis234`; objects live at `agent/artifacts/objects/<digest[:2]>/<digest>`.
- A manifest path is exactly `<session-jsonl-path>.artifacts.jsonl`.
- Durable sessions preserve existing opaque `artifact-<32 lowercase hex>` IDs. Object digests and host paths are never model-visible.
- Default limits are 64 MiB per object, 512 MiB/10,000 references per session, and 2 GiB/100,000 physical objects per installation.
- Trusted project settings may lower effective limits but cannot raise global limits. A global user setting is the only way to raise a default.
- Promotion happens only for truncated output, explicit retention, or declared subagent artifacts. Small output remains in JSONL only.
- In-memory sessions do not create a session file, manifest, object root, or hidden session.
- Artifact failure returns the original bounded sanitized tool result plus `artifactUnavailable`; it does not turn a successful tool effect into failure.
- Garbage collection deletes only objects proven unreferenced after every manifest is scanned successfully. Any unreadable manifest fails collection closed.
- Promotion/manifest append and garbage collection share one cross-process maintenance lock so collection cannot delete an object between publication and authorization.
- Do not change generic agent-loop ordering, tool-result ordering, or `~/.travis234` path ownership.

---

### Task 1: Durable object contracts and atomic promotion

**Files:**
- Create: `travis/coding_agent/artifact_store.py`
- Create: `tests/test_durable_artifact_store.py`
- Modify: `travis/coding_agent/__init__.py`

**Interfaces:**
- Consumes: an already-sanitized source `Path` and installation agent directory.
- Produces: `ArtifactLimits`, `StoredArtifactObject`, `ArtifactPromotionError`, process-reentrant `ArtifactMaintenanceLock`, and `DurableArtifactStore.promote(path, limits) -> StoredArtifactObject`.

- [ ] **Step 0: Verify the implementation base contains planning changes only**

```bash
git diff --name-only ec53c69...HEAD
```

Expected: exactly the roadmap plus the six Phase 1C–5 plan documents under `docs/superpowers/plans/`; no source, test, package, or runtime file.

- [ ] **Step 1: Write failing atomic-promotion, deduplication, permissions, integrity, symlink, and limit tests**

```python
def test_identical_concurrent_promotions_publish_one_verified_object(tmp_path):
    source = tmp_path / "source.log"
    source.write_bytes(b"same sanitized bytes")
    store = DurableArtifactStore(tmp_path / "agent")

    with ThreadPoolExecutor(max_workers=8) as pool:
        promoted = list(pool.map(lambda _: store.promote(source), range(16)))

    assert len({item.digest for item in promoted}) == 1
    assert len(list((tmp_path / "agent/artifacts/objects").rglob("[0-9a-f]" * 64))) == 1
    assert store.verify(promoted[0].digest).read_bytes() == source.read_bytes()
    assert stat.S_IMODE(store.verify(promoted[0].digest).stat().st_mode) == 0o600


def test_promotion_rejects_object_and_physical_limits_without_partial_file(tmp_path):
    source = tmp_path / "large.log"
    source.write_bytes(b"x" * 9)
    store = DurableArtifactStore(tmp_path / "agent")

    with pytest.raises(ArtifactPromotionError, match="object_limit"):
        store.promote(source, ArtifactLimits(max_object_bytes=8))

    assert list((tmp_path / "agent/artifacts").rglob("*.tmp")) == []
```

- [ ] **Step 2: Run the new store tests and verify the red state**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_durable_artifact_store.py -q
```

Expected: collection fails because `artifact_store` does not exist.

- [ ] **Step 3: Implement immutable store types and atomic promotion**

Use these public contracts:

```python
@dataclass(frozen=True)
class ArtifactLimits:
    max_object_bytes: int = 64 * 1024 * 1024
    max_session_logical_bytes: int = 512 * 1024 * 1024
    max_session_objects: int = 10_000
    max_physical_bytes: int = 2 * 1024 * 1024 * 1024
    max_physical_objects: int = 100_000
    min_free_bytes: int = 128 * 1024 * 1024


@dataclass(frozen=True)
class StoredArtifactObject:
    digest: str
    byte_size: int


class ArtifactPromotionError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class DurableArtifactStore:
    def __init__(self, agent_dir: str | Path) -> None: ...
    def promote(self, source: Path, limits: ArtifactLimits | None = None) -> StoredArtifactObject: ...
    def verify(self, digest: str) -> Path: ...
    def physical_bytes(self) -> int: ...
    def maintenance_lock(self) -> ArtifactMaintenanceLock: ...
```

Promotion must stream the source through SHA-256, reject changes between hashing and copy, write a `0600` sibling temp, `fsync` the file, atomically link it only when the digest path is absent, `fsync` the parent directory, and verify an existing deduplicated object before returning it. Reject symlink/non-regular source, object, and temp paths using `lstat`/no-follow opens. Object directories are `0700`. Use `agent/artifacts/.lock` around the physical byte/count quota check and publication so concurrent distinct promotions cannot exceed the installation limit. The lock combines a process-local `RLock`/owner depth with the filesystem lock so `ArtifactRegistry.promote()` can hold it across the nested store call and manifest append without deadlock; test nested entry and two-process exclusion.

- [ ] **Step 4: Run store tests and existing atomic-file tests**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_durable_artifact_store.py \
  tests/test_coding_tools_and_subagents.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit the object store**

```bash
git add travis/coding_agent/artifact_store.py travis/coding_agent/__init__.py \
  tests/test_durable_artifact_store.py
git commit -m "feat(artifacts): add immutable durable object store"
```

### Task 2: Append-only session artifact manifests

**Files:**
- Create: `travis/coding_agent/artifact_manifest.py`
- Create: `tests/test_artifact_manifest.py`
- Modify: `travis/coding_agent/session_lock.py`

**Interfaces:**
- Consumes: one durable session JSONL path and `StoredArtifactObject` metadata.
- Produces: `ArtifactProducer`, `ArtifactManifestEntry`, and `ArtifactManifest` with atomic append/load/fork operations.

- [ ] **Step 1: Write failing manifest recovery and fork-filter tests**

```python
def test_manifest_append_reloads_exact_id_and_recovers_only_torn_tail(tmp_path):
    session = tmp_path / "session.jsonl"
    session.write_text('{"type":"session"}\n', encoding="utf-8")
    manifest = ArtifactManifest.for_session(session)
    entry = _entry("artifact-" + "a" * 32, tool_call_id="call-1")
    manifest.append(entry)
    with manifest.path.open("ab") as handle:
        handle.write(b'{"type":"artifact"')

    reopened = ArtifactManifest.for_session(session)

    assert reopened.get(entry.id) == entry
    assert reopened.recovered_tail_path is not None


def test_fork_copies_only_references_reachable_from_target_branch(tmp_path):
    source = ArtifactManifest.for_session(tmp_path / "source.jsonl")
    kept = _entry("artifact-" + "b" * 32, session_entry_id="entry-kept")
    dropped = _entry("artifact-" + "c" * 32, session_entry_id="entry-other")
    source.append(kept)
    source.append(dropped)

    forked = source.fork_to(
        tmp_path / "fork.jsonl",
        allowed_entry_ids={"entry-kept"},
        allowed_tool_call_ids=set(),
    )

    assert forked.entries == (kept,)
```

Add a retained entry without a producer and prove it is copied to every descendant fork. A normal entry is copied only when its producer entry/tool-call is reachable.

- [ ] **Step 2: Run manifest tests and verify the red state**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_artifact_manifest.py -q
```

Expected: import failure for `artifact_manifest`.

- [ ] **Step 3: Implement manifest records and durable append**

```python
@dataclass(frozen=True)
class ArtifactProducer:
    session_entry_id: str | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True)
class ArtifactManifestEntry:
    id: str
    digest: str
    kind: str
    byte_size: int
    created_at_ms: int
    producer: ArtifactProducer
    retained: bool = False


class ArtifactManifest:
    @classmethod
    def for_session(cls, session_path: str | Path) -> "ArtifactManifest": ...
    def append(self, entry: ArtifactManifestEntry) -> None: ...
    def get(self, artifact_id: str) -> ArtifactManifestEntry | None: ...
    def fork_to(
        self,
        target_session_path: str | Path,
        *,
        allowed_entry_ids: set[str],
        allowed_tool_call_ids: set[str],
    ) -> "ArtifactManifest": ...
```

The path is `Path(str(session_path) + ".artifacts.jsonl")`. Validate lowercase SHA-256 and artifact-ID formats, enforce session logical-byte/reference counts before append, reject duplicate IDs with different metadata, tolerate only a torn final record, and leave earlier corruption untouched. Use `SessionFileLock` and append one newline-terminated record before mutating memory.

- [ ] **Step 4: Run manifest and session recovery tests**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_artifact_manifest.py \
  tests/test_session_store_recovery.py -q
```

Expected: all pass and existing JSONL recovery behavior is unchanged.

- [ ] **Step 5: Commit manifest ownership**

```bash
git add travis/coding_agent/artifact_manifest.py travis/coding_agent/session_lock.py \
  tests/test_artifact_manifest.py
git commit -m "feat(artifacts): persist append-only session manifests"
```

### Task 3: Adapt `ArtifactRegistry` and add bounded resource resolution

**Files:**
- Modify: `travis/coding_agent/artifacts.py`
- Create: `travis/coding_agent/resource_refs.py`
- Create: `tests/test_resource_ref_resolver.py`
- Modify: `tests/test_output_spool.py`
- Modify: `tests/test_coding_tools_and_subagents.py`

**Interfaces:**
- Consumes: optional `DurableArtifactStore`, optional `ArtifactManifest`, and ephemeral registered paths.
- Produces: backward-compatible `register()` plus new `promote()` and `ResourceRefResolver.resolve_read()`.

- [ ] **Step 1: Write failing durable/ephemeral authorization tests**

```python
def test_registry_promotes_for_durable_session_and_survives_close(tmp_path):
    source = tmp_path / "complete.log"
    source.write_text("complete", encoding="utf-8")
    manifest = ArtifactManifest.for_session(tmp_path / "session.jsonl")
    registry = ArtifactRegistry(
        durable_store=DurableArtifactStore(tmp_path / "agent"),
        manifest=manifest,
    )

    ref = registry.promote(source, "command-output", tool_call_id="call-1")
    registry.close(remove_files=True)

    reopened = ArtifactRegistry(
        durable_store=DurableArtifactStore(tmp_path / "agent"),
        manifest=ArtifactManifest.for_session(tmp_path / "session.jsonl"),
    )
    assert reopened.resolve_read(ref.id).read_text(encoding="utf-8") == "complete"


def test_foreign_manifest_cannot_resolve_object_digest_or_host_path(tmp_path):
    resolver = _resolver_with_one_artifact(tmp_path)
    assert resolver.resolve_read("f" * 64, byte_offset=0, byte_limit=10).available is False
    assert resolver.resolve_read(str(tmp_path / "agent/artifacts/objects"), byte_offset=0, byte_limit=10).available is False
```

- [ ] **Step 2: Run registry/resolver tests and verify the red state**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_resource_ref_resolver.py \
  tests/test_output_spool.py -q
```

Expected: missing `promote` and resolver contracts.

- [ ] **Step 3: Implement the adapter and resolver**

Add:

```python
class ArtifactRegistry:
    def promote(
        self,
        path: Path,
        kind: str,
        *,
        session_entry_id: str | None = None,
        tool_call_id: str | None = None,
        retained: bool = False,
    ) -> ArtifactRef: ...


@dataclass(frozen=True)
class ResourceReadResolution:
    available: bool
    artifact_id: str
    content: bytes = b""
    next_offset: int | None = None
    total_bytes: int | None = None
    error_code: str | None = None


class ResourceRefResolver:
    def resolve_read(
        self,
        identifier: str,
        *,
        byte_offset: int,
        byte_limit: int,
    ) -> ResourceReadResolution: ...
```

`ArtifactRegistry.resolve_read()` remains for existing internal callers but delegates artifact IDs through the resolver. Hold the store maintenance lock across object promotion and manifest append; an append failure may leave a safe orphan for later collection but never an authorized partial record. Enforce `1 <= byte_limit <= 50 * 1024`, validate UTF-8 cursor boundaries in the read tool, and verify the digest on first object access. Cache only successful inode/size/mtime metadata and reverify whenever any stat field changes; open no symlink. A missing or mismatched object returns a stable unavailable code.

Durable promoted refs always have `remove_on_close=False`; closing a registry removes only ephemeral spool sources after successful promotion and never unlinks a content-addressed object. Cover repeated close and mixed ephemeral/durable refs.

- [ ] **Step 4: Run artifact/read compatibility tests**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_resource_ref_resolver.py \
  tests/test_output_spool.py \
  tests/test_coding_tools_and_subagents.py -q
```

Expected: all pass; model-visible output contains only artifact IDs.

- [ ] **Step 5: Commit the registry adapter**

```bash
git add travis/coding_agent/artifacts.py travis/coding_agent/resource_refs.py \
  tests/test_resource_ref_resolver.py tests/test_output_spool.py \
  tests/test_coding_tools_and_subagents.py
git commit -m "feat(artifacts): resolve durable session references"
```

### Task 4: Promote only completed truncated tool output

**Files:**
- Modify: `travis/coding_agent/tools/output_spool.py`
- Modify: `travis/coding_agent/tools/bash.py`
- Modify: `travis/coding_agent/tools/process.py`
- Modify: `travis/coding_agent/session_bash.py`
- Modify: `tests/test_output_spool.py`
- Modify: `tests/test_process_tools.py`
- Modify: `tests/test_coding_tools_and_subagents.py`

**Interfaces:**
- Consumes: completed sanitized spool path and optional `ArtifactRegistry`.
- Produces: one final `artifactId` or an `artifactUnavailable` diagnostic without changing command success.

- [ ] **Step 1: Add failing completion and failure-shaping regressions**

```python
def test_spool_does_not_promote_incomplete_bytes(tmp_path):
    registry = _durable_registry(tmp_path)
    spool = OutputSpool(max_bytes=4, directory=tmp_path, artifact_registry=registry)
    spool.append(b"first")
    interim = spool.snapshot(persist_if_truncated=True)
    spool.append(b"-last")
    spool.finish()
    final = spool.snapshot(persist_if_truncated=True)

    assert interim.artifact_id is None
    assert registry.resolve_read(final.artifact_id).read_bytes() == b"first-last"


def test_successful_bash_keeps_success_when_artifact_promotion_fails(tmp_path):
    result = _run_truncated_bash_with_failing_store(tmp_path)
    assert result.details["exitCode"] == 0
    assert result.details["artifactId"] is None
    assert result.details["artifactUnavailable"]["code"] == "physical_limit"
```

- [ ] **Step 2: Run focused tool tests and verify the red state**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_output_spool.py \
  tests/test_process_tools.py::test_managed_bash_preserves_tail_truncation_metadata \
  tests/test_coding_tools_and_subagents.py::test_synchronous_bash_exposes_truncated_artifact_id_to_the_model -q
```

Expected: incomplete promotion or missing diagnostic assertions fail.

- [ ] **Step 3: Move promotion to the final snapshot boundary**

`OutputSpool.snapshot(persist_if_truncated=True)` may set intent before finish but must call `ArtifactRegistry.promote()` only after `finish()`. Preserve temporary spools until promotion has succeeded or the final bounded result has captured the diagnostic. Delete ephemeral source files on close after successful durable copy. Add `artifactUnavailable` as a bounded `{code, message}` object with no host path.

- [ ] **Step 4: Run bash, process, spool, and process-regression suites**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_output_spool.py \
  tests/test_process_tools.py \
  tests/test_process_regressions.py \
  tests/test_coding_tools_and_subagents.py -q
```

Expected: all pass and small output creates no durable object.

- [ ] **Step 5: Commit tool promotion behavior**

```bash
git add travis/coding_agent/tools/output_spool.py travis/coding_agent/tools/bash.py \
  travis/coding_agent/tools/process.py travis/coding_agent/session_bash.py \
  tests/test_output_spool.py tests/test_process_tools.py \
  tests/test_coding_tools_and_subagents.py
git commit -m "feat(tools): promote completed truncated output"
```

### Task 5: Bind manifests to durable session lifecycle

**Files:**
- Modify: `travis/coding_agent/agent_session.py`
- Modify: `travis/coding_agent/agent_session_services.py`
- Modify: `travis/coding_agent/agent_session_runtime.py`
- Modify: `travis/coding_agent/session_persistence.py`
- Modify: `travis/coding_agent/session_catalog.py`
- Create: `tests/test_durable_artifact_session_lifecycle.py`
- Modify: `tests/test_session_parity.py`

**Interfaces:**
- Consumes: `session_path`, `agent_dir`, branch entry IDs, and tool-call IDs.
- Produces: resumed registry ownership and filtered manifest copies for fork/clone/import/move.

- [ ] **Step 1: Add failing restart, resume, fork, and ephemeral-session tests**

```python
def test_truncated_artifact_survives_shutdown_and_resume(tmp_path):
    session_path = tmp_path / "session.jsonl"
    artifact_id = _create_durable_session_and_truncated_output(tmp_path, session_path)

    resumed = _resume_session(tmp_path, session_path)

    assert resumed._artifacts.resolve_read(artifact_id).read_text() == _FULL_OUTPUT


def test_fork_copies_reference_without_copying_object(tmp_path):
    parent, artifact_id = _session_with_artifact(tmp_path)
    object_paths_before = _object_paths(tmp_path)
    fork_path = parent.create_branched_session(parent.session_store.leaf_id)
    forked = _resume_session(tmp_path, Path(fork_path))

    assert forked._artifacts.resolve_read(artifact_id) is not None
    assert _object_paths(tmp_path) == object_paths_before


def test_in_memory_session_never_creates_artifact_root(tmp_path):
    session = AgentSession(cwd=str(tmp_path), model=_model(), agent_dir=str(tmp_path / "agent"))
    _run_small_output(session)
    session.shutdown()
    assert not (tmp_path / "agent/artifacts").exists()
```

- [ ] **Step 2: Run lifecycle tests and verify the red state**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_durable_artifact_session_lifecycle.py -q
```

Expected: artifact IDs disappear after close or manifest copies are absent.

- [ ] **Step 3: Compose durable artifacts after `SessionStore` ownership is known**

Create a narrow factory in `agent_session_services.py`:

```python
def create_session_artifact_registry(
    *,
    session_path: str | None,
    agent_dir: str,
    settings_manager: SettingsManager,
) -> ArtifactRegistry: ...
```

Return a plain ephemeral registry when `session_path` is `None`. For durable sessions, open the store and manifest and reload exact IDs. During fork/clone, derive reachable entry IDs and tool-call IDs from the target branch before copying the manifest. Move/import copy the sidecar atomically; a missing sidecar is valid for historical sessions. Sidecar failure aborts the session move/fork before switching the active runtime, leaving the source untouched.

- [ ] **Step 4: Run lifecycle and complete session matrices**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_durable_artifact_session_lifecycle.py \
  tests/test_session_parity.py \
  tests/test_coding_persistence_and_compaction.py \
  tests/test_app_integration.py -q
```

Expected: all pass; historical JSONL without sidecars resumes unchanged.

- [ ] **Step 5: Commit session lifecycle integration**

```bash
git add travis/coding_agent/agent_session.py travis/coding_agent/agent_session_services.py \
  travis/coding_agent/agent_session_runtime.py travis/coding_agent/session_persistence.py \
  travis/coding_agent/session_catalog.py tests/test_durable_artifact_session_lifecycle.py \
  tests/test_session_parity.py
git commit -m "feat(sessions): retain artifact references across resume and fork"
```

### Task 6: Promote declared subagent artifacts and add conservative collection

**Files:**
- Modify: `travis/coding_agent/session_subagents.py`
- Modify: `travis/coding_agent/subagent_trace.py`
- Create: `travis/coding_agent/artifact_gc.py`
- Create: `tests/test_artifact_gc.py`
- Modify: `tests/test_subagents.py`
- Modify: `tests/test_coding_tools_and_subagents.py`

**Interfaces:**
- Consumes: child-declared workspace-relative artifact paths and every manifest below the session catalog.
- Produces: promoted artifact IDs in public child results and `ArtifactGarbageCollector.collect() -> ArtifactGcReport`.

- [ ] **Step 1: Add failing child authorization and GC tests**

```python
def test_declared_subagent_artifact_is_promoted_but_host_path_is_hidden(tmp_path):
    result = _run_child_declaring_artifact(tmp_path, "reports/review.md")
    assert result.artifacts[0].startswith("artifact-")
    assert str(tmp_path) not in result.summary
    assert str(tmp_path) not in json.dumps(result.to_dict())


def test_gc_fails_closed_when_any_manifest_is_unreadable(tmp_path):
    collector = _collector_with_referenced_and_orphan_objects(tmp_path)
    (tmp_path / "sessions/broken.jsonl.artifacts.jsonl").write_text("not-json\n")

    report = collector.collect()

    assert report.completed is False
    assert report.deleted == ()
    assert _all_objects_still_exist(tmp_path)
```

- [ ] **Step 2: Run child/GC tests and verify the red state**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_artifact_gc.py \
  tests/test_subagents.py -q
```

Expected: missing collector and raw child paths remain.

- [ ] **Step 3: Implement declared promotion and fail-closed GC**

Resolve child-declared files through the existing workspace capability, reject directories/symlink escapes/out-of-workspace paths, and promote only after the child reaches a terminal status. Replace public artifact path strings with artifact IDs while retaining files-changed paths separately.

```python
@dataclass(frozen=True)
class ArtifactGcReport:
    completed: bool
    scanned_manifests: int
    referenced_digests: int
    deleted: tuple[str, ...]
    retained: tuple[str, ...]
    errors: tuple[str, ...]


class ArtifactGarbageCollector:
    def collect(self, *, dry_run: bool = False) -> ArtifactGcReport: ...
```

Hold the same cross-process maintenance lock used by promotion while scanning every session path enumerated by the existing session catalog and unlinking objects. Retained entries remain ordinary manifest references; no second hold-file format is introduced. Never run collection automatically on ordinary shutdown. Provide only an internal maintenance API in Phase 1C; no new CLI deletion command is needed.

- [ ] **Step 4: Run subagent, GC, and artifact matrices**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_artifact_gc.py \
  tests/test_subagents.py \
  tests/test_coding_tools_and_subagents.py \
  tests/test_durable_artifact_session_lifecycle.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit child artifacts and collection**

```bash
git add travis/coding_agent/session_subagents.py travis/coding_agent/subagent_trace.py \
  travis/coding_agent/artifact_gc.py tests/test_artifact_gc.py \
  tests/test_subagents.py tests/test_coding_tools_and_subagents.py
git commit -m "feat(artifacts): retain declared child outputs safely"
```

### Task 7: Settings, diagnostics, documentation, and architecture limits

**Files:**
- Modify: `travis/coding_agent/settings_manager.py`
- Modify: `travis/coding_agent/eval_trace.py`
- Modify: `README.md`
- Create: `docs/architecture/contract-parity.md`
- Create: `docs/settings.md`
- Create: `tests/test_artifact_settings.py`
- Modify: `tests/test_eval_trace.py`
- Modify: `tests/architecture/test_facade_boundaries.py`

**Interfaces:**
- Consumes: global/project `artifacts` settings and artifact lifecycle events.
- Produces: `SettingsManager.get_artifact_limits()` and sanitized promotion/unavailable trace events.

- [ ] **Step 1: Add failing settings precedence and no-secret trace tests**

```python
def test_project_artifact_limits_can_lower_but_not_raise_global(tmp_path):
    settings = _settings(
        global_value={"maxObjectBytes": 1024},
        project_value={"maxObjectBytes": 2048, "maxSessionLogicalBytes": 512},
        trusted=True,
    )
    limits = settings.get_artifact_limits()
    assert limits.max_object_bytes == 1024
    assert limits.max_session_logical_bytes == 512


def test_artifact_trace_contains_no_path_content_or_environment(tmp_path):
    event = _promotion_event_with_secret_sentinels(tmp_path)
    serialized = json.dumps(event)
    assert "artifact-promoted" in serialized
    assert "secret-sentinel" not in serialized
    assert str(tmp_path) not in serialized
```

- [ ] **Step 2: Run settings/trace tests and verify the red state**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_artifact_settings.py tests/test_eval_trace.py -q
```

Expected: accessors and allowlisted event fields are missing.

- [ ] **Step 3: Implement strict settings and sanitized events**

Support only positive integer fields `maxObjectBytes`, `maxSessionLogicalBytes`, `maxSessionObjects`, `maxPhysicalBytes`, `maxPhysicalObjects`, and `minFreeBytes`. Invalid hand-edited fields are ignored per scope so a malformed project value cannot hide a valid global value. Emit only artifact ID, kind, byte size, source (`ephemeral|durable`), outcome, and stable error code.

- [ ] **Step 4: Document operator behavior and enforce owner limits**

Document promotion triggers, limits, resume/fork behavior, byte-pagination, storage location, explicit maintenance, and failure semantics. Add new artifact modules to the bounded collaborator architecture list and keep each at or below 750 lines.

- [ ] **Step 5: Run focused Phase 1C matrix and commit**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_durable_artifact_store.py tests/test_artifact_manifest.py \
  tests/test_resource_ref_resolver.py tests/test_artifact_gc.py \
  tests/test_artifact_settings.py tests/test_output_spool.py \
  tests/test_process_tools.py tests/test_subagents.py \
  tests/test_durable_artifact_session_lifecycle.py \
  tests/architecture/test_facade_boundaries.py -q
git diff --check
```

```bash
git add README.md docs/architecture/contract-parity.md docs/settings.md \
  travis/coding_agent/settings_manager.py travis/coding_agent/eval_trace.py \
  tests/test_artifact_settings.py \
  tests/test_eval_trace.py tests/architecture/test_facade_boundaries.py
git commit -m "docs: qualify durable artifact lifecycle"
```

### Task 8: Phase 1C repository and installed-wheel qualification

**Files:**
- No source changes expected.

**Interfaces:**
- Consumes: complete Phase 1C branch.
- Produces: fresh non-container verification evidence and the exact base commit for Phase 1D.

- [ ] **Step 1: Run the complete Python and npm suites**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest tests -q
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Expected: zero failures and the npm inventory remains five declared files.

- [ ] **Step 2: Build root wheel/sdist outside the worktree**

```bash
phase1c_dist=$(mktemp -d /tmp/travis234-phase1c.XXXXXX)
uv build --clear --out-dir "$phase1c_dist" .
shasum -a 256 "$phase1c_dist"/*
```

Audit the wheel for all artifact modules and absence of `.env`, research clones, worktrees, and planning documents.

- [ ] **Step 3: Run installed-wheel native-TUI resume/fork scenario**

Install the exact wheel into an isolated Python 3.13 environment. Through the real `travis234` PTY and established `--dotenv` boundary, create one truncated command artifact, read page zero, exit, resume, read the same artifact ID, fork, and read it again. Independently assert one object digest, two authorized manifests after fork, clean TUI shutdown, and no remaining process. Do not print dotenv values or host object paths.

- [ ] **Step 4: Run acceptance and Git-scope audits**

```bash
/Users/htooayelwin/orca/travis234/.venv/bin/python scripts/verify_acceptance.py --parity-json
git diff --check
git diff --exit-code ec53c69...HEAD -- travis/agent travis/ai/providers \
  packages/travis234-cli packages/travis234-mcp-adapter
git status --short --branch
```

Expected: no generic-loop/provider/npm/MCP changes and a clean worktree.

- [ ] **Step 5: Record the verified commit and stop at the phase gate**

Do not build or smoke a container. Record Python/npm/build/TUI evidence in the execution handoff, preserve the branch/worktree, and use the verified `HEAD` as the only Phase 1D base.
