# appv231 Session and Context Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make persistent JSONL appends scale linearly and ensure resumed/provider/compacted context never treats stale process handles as live or drops relevant process state.

**Architecture:** `SessionStore` retains its append-only format and cross-process file lock but synchronizes only unseen bytes. A coding-only `ProcessContextResolver` scans opaque handles, resolves them against live and durable process state, injects one transient provider overlay, and merges the same bounded ledger into compaction details through the existing coding adapter.

**Tech Stack:** Python 3.13, JSONL, `os.stat` device/inode identity, existing `SessionFileLock`, coding-agent context transform, custom messages, existing compaction adapter, pytest.

## Global Constraints

- Complete `2026-07-12-appv231-01-process-runtime-v2.md` first.
- Do not modify any file under `appV2.3.1/appv231/agent/`.
- Do not modify any file under `appV2.3.1/appv231/compaction/`.
- Keep the existing JSONL version and load all existing sessions without migration.
- Preserve branch parent selection, fork, resume, export, corruption detection, truncated-tail quarantine, and durable checkpoint behavior.
- Provider overlay messages are transient and must not append another JSONL line.
- Process context contains no command text, environment, PID, or process output.
- Compaction process details are bounded to sixteen records.
- Use red-green TDD and scoped commits only.

---

### Task 1: Incremental SessionStore Tail Synchronization

**Files:**
- Modify: `appV2.3.1/appv231/coding_agent/session_store.py`
- Extend: `appV2.3.1/tests/test_session_store_recovery.py`
- Create: `appV2.3.1/tests/test_session_store_performance.py`

**Interfaces:**
- Produces: `_disk_offset`, `_disk_identity`, `_read_range`, and `_sync_from_disk`.
- Preserves: public SessionStore API and append parent semantics.
- Guarantees: normal append parses only unseen records; file replacement/shrink falls back to one full reload.

- [ ] **Step 1: Write failing suffix-read and behavior tests**

```python
def test_single_writer_appends_parse_only_unseen_suffix(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "session.jsonl"
    store = SessionStore(str(path), cwd=str(tmp_path))
    parsed_bytes = 0
    full_loads = 0
    original = store._read_range
    original_load = store._load

    def measured(start: int) -> bytes:
        nonlocal parsed_bytes
        payload = original(start)
        parsed_bytes += len(payload)
        return payload

    def measured_load() -> None:
        nonlocal full_loads
        full_loads += 1
        original_load()

    monkeypatch.setattr(store, "_read_range", measured)
    monkeypatch.setattr(store, "_load", measured_load)
    for index in range(2_000):
        store.append_message(UserMessage(content=f"message-{index}", timestamp=now_ms()))

    final_size = path.stat().st_size
    assert full_loads == 0
    assert parsed_bytes <= final_size * 3
    assert len(store.file_entries) == 2_001
```

Extend recovery tests with two long-lived store instances alternating 100
appends, explicit branch-parent selection after an external append, inode
replacement, file shrink, and incomplete-tail recovery.

- [ ] **Step 2: Run performance and recovery tests to witness quadratic reads**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_session_store_performance.py \
  appV2.3.1/tests/test_session_store_recovery.py
```

Expected: missing `_read_range` or parsed bytes grow near the sum of every file
prefix rather than the final file size.

- [ ] **Step 3: Track disk identity and offset on full load**

```python
def _disk_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return (stat.st_dev, stat.st_ino)


def _load(self) -> None:
    raw = self._read_range(0)
    self._rebuild_from_bytes(raw)
    # Truncated-tail recovery can atomically replace the file.
    self._disk_offset = self.path.stat().st_size
    self._disk_identity = _disk_signature(self.path)
```

Move the current line parsing/recovery behavior into `_rebuild_from_bytes`
without changing its corruption rules. Initialize `_disk_offset = 0` and
`_disk_identity = None` before the constructor's first load. After
`_write_header` atomically creates a new session, set both fields from the
written payload and resulting file identity.

- [ ] **Step 4: Implement unseen-suffix synchronization**

```python
def _read_range(self, start: int) -> bytes:
    with self.path.open("rb") as handle:
        handle.seek(start)
        return handle.read()


def _sync_from_disk(self) -> None:
    stat = self.path.stat()
    identity = (stat.st_dev, stat.st_ino)
    if self._disk_identity != identity or stat.st_size < self._disk_offset:
        self._load()
        return
    if stat.st_size == self._disk_offset:
        return
    suffix = self._read_range(self._disk_offset)
    if not suffix.endswith((b"\n", b"\r")):
        self._load()
        return
    for line_number, raw_line in enumerate(suffix.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            entry = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SessionCorruptionError(self.path, len(self.file_entries) + line_number, str(error)) from error
        self._apply_loaded_entry(entry)
    self._disk_offset = stat.st_size
    self._disk_identity = identity
```

`_apply_loaded_entry` updates `file_entries`, `by_id`, `leaf_id`, and
`_disk_leaf_id` without assigning a new ID.

- [ ] **Step 5: Preserve branch-parent selection during append**

```python
def _append_entry(self, entry: dict[str, Any], durable: bool = False) -> str:
    with self._thread_lock, SessionFileLock(self.path):
        selected_parent = self.leaf_id
        follows_disk_leaf = selected_parent == self._disk_leaf_id
        self._sync_from_disk()
        parent_id = self.leaf_id if follows_disk_leaf else selected_parent
        committed = {
            **entry,
            "id": self._generate_id(),
            "parentId": parent_id,
            "timestamp": _timestamp(),
        }
        self._write_record(committed, durable=durable)
        self._apply_committed_entry(committed)
        self._disk_offset += len(_record_payload(committed))
        self._disk_identity = _disk_signature(self.path)
        return committed["id"]
```

Serialize once and pass the same bytes to `_write_record` and offset accounting
so Unicode length cannot drift.

- [ ] **Step 6: Run persistence behavior and parse-budget tests**

```bash
for run in 1 2 3; do
  TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
    appV2.3.1/tests/test_session_store_performance.py \
    appV2.3.1/tests/test_session_store_recovery.py \
    appV2.3.1/tests/test_session_catalog.py || exit 1
done
```

Expected: three passes, no lost/torn entries, and parse budget at most three
times final size.

- [ ] **Step 7: Commit incremental persistence**

```bash
git add appV2.3.1/appv231/coding_agent/session_store.py appV2.3.1/tests/test_session_store_recovery.py appV2.3.1/tests/test_session_store_performance.py
git commit -m "perf(appv231): sync only new session records"
```

### Task 2: Process Reference Scanner and Resolver

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/process_context.py`
- Modify: `appV2.3.1/appv231/coding_agent/processes/service.py`
- Modify: `appV2.3.1/appv231/coding_agent/processes/types.py`
- Create: `appV2.3.1/tests/test_process_context.py`

**Interfaces:**
- Produces: `ProcessContextRecord`, `referenced_process_ids`, `ProcessContextResolver.resolve`, and service `inspect`/`inspect_many`.
- Consumes: agent messages, live service, durable completion store, owner.
- Guarantees: bounded metadata-only resolution and explicit `unavailable` state for stale running handles.

- [ ] **Step 1: Write failing scanner and reconciliation tests**

```python
def test_resolver_marks_old_running_handle_unavailable_after_restart(tmp_path: Path) -> None:
    process_id = "proc_" + "b" * 32
    messages = [
        ToolResultMessage(
            tool_call_id="call-1",
            tool_name="bash",
            content=[TextContent(text="running")],
            details={"status": "running", "sessionId": process_id, "nextCursor": 10, "outputSize": 10},
            is_error=False,
            timestamp=now_ms(),
        )
    ]
    resolver = ProcessContextResolver(empty_service(), empty_completion_store(), owner_factory(tmp_path))

    records = resolver.resolve(messages)
    assert records == (
        ProcessContextRecord(
            session_id=process_id,
            status="unavailable",
            cursor=10,
            output_size=10,
            exit_code=None,
            durable_output=False,
            reason="application-restarted",
        ),
    )
```

Also cover live running, durable exited, terminal observed in transcript,
compaction details, malformed IDs, duplicates, foreign workspace, and the
sixteen-record priority cap. Add 10,000 historical structured handles and assert
at most 64 candidates, one durable batch query, and sixteen rendered records.

- [ ] **Step 2: Run tests and witness missing resolver**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_process_context.py
```

Expected: collection fails because `process_context` does not exist.

- [ ] **Step 3: Add metadata-only service inspection**

```python
def inspect(self, owner: ProcessOwner, session_id: str) -> ProcessSnapshot | None:
    try:
        with self._record_call(owner, session_id) as record:
            return self._metadata_snapshot(record)
    except ProcessNotFoundError:
        if self._completion_store is None:
            return None
        return self._completion_store.inspect(owner, session_id)
```

`_metadata_snapshot` sets `output=""` and `cursor=next_cursor=output_size`; it
does not read or expose command/output. Add `inspect_many(owner, session_ids)`:
snapshot live records under the service lock, then resolve missing IDs with one
`ProcessCompletionStore.inspect_many` parameterized SQLite query. Accept at
most 64 valid unique IDs and preserve input order.

- [ ] **Step 4: Implement strict handle extraction**

```python
_PROCESS_ID = re.compile(r"^proc_[0-9a-f]{32}$")


def referenced_process_ids(messages: Sequence[AgentMessage]) -> tuple[ProcessReference, ...]:
    ordered: dict[str, ProcessReference] = {}
    for message in messages:
        details = getattr(message, "details", None)
        if isinstance(details, Mapping):
            _collect_detail_reference(details, ordered)
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolCall) and block.name == "process":
                    _collect_process_argument(block.arguments, ordered)
        if getattr(message, "role", None) == "compactionSummary":
            for item in (getattr(message, "details", None) or {}).get("managedProcesses", []):
                _collect_detail_reference(item, ordered)
    return _prioritized_candidates(ordered.values(), limit=64)
```

Never regex arbitrary assistant/user prose for process IDs; only structured
Track the latest structured state and message position while scanning. Candidate
order is historical nonterminal first, then most-recent terminal/unavailable;
resolution later applies the final live-state priority and sixteen-record cap.

- [ ] **Step 5: Resolve and prioritize records**

Resolve the bounded candidates with one `service.inspect_many` call. If absent
and the last historical state was nonterminal, produce `unavailable` with reason
`application-restarted`. Preserve terminal historical state when it is newer
than a missing live record. Sort running/stopping/draining first, then durable
terminal, then unavailable, and truncate to sixteen.

- [ ] **Step 6: Run context and service tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_process_context.py \
  appV2.3.1/tests/test_process_service.py \
  appV2.3.1/tests/test_process_completions.py \
  -k "inspect or context or completion"
```

Expected: pass without reading output bodies during context resolution.

- [ ] **Step 7: Commit process context resolution**

```bash
git add appV2.3.1/appv231/coding_agent/process_context.py appV2.3.1/appv231/coding_agent/processes/service.py appV2.3.1/appv231/coding_agent/processes/types.py appV2.3.1/tests/test_process_context.py
git commit -m "feat(appv231): reconcile persisted process state"
```

### Task 3: Transient Provider Process Overlay

**Files:**
- Modify: `appV2.3.1/appv231/coding_agent/process_context.py`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py`
- Extend: `appV2.3.1/tests/test_process_context.py`
- Extend: `appV2.3.1/tests/test_coding_agent.py`
- Extend: `appV2.3.1/tests/test_app_integration.py`

**Interfaces:**
- Consumes: Task 2 resolver and existing coding `transform_context` adapter.
- Produces: one transient non-displayed `CustomMessage(custom_type="managed_process_state")`.
- Guarantees: provider context sees current status; stored session messages and JSONL entry count do not change.

- [ ] **Step 1: Add failing transient-overlay tests**

```python
def test_provider_receives_reconciled_process_overlay_without_jsonl_append(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    seen: list[list[AgentMessage]] = []
    session = session_with_stale_running_process(session_path, capture_context=seen.append)
    before_lines = session_path.read_text(encoding="utf-8").count("\n")

    session.prompt("what is the build status?")

    assert any(
        getattr(message, "customType", None) == "managed_process_state"
        and "status=unavailable" in str(getattr(message, "content", ""))
        for message in seen[-1]
    )
    assert session_path.read_text(encoding="utf-8").count("\n") == before_lines + persisted_turn_lines(session)
    assert not any(getattr(message, "customType", None) == "managed_process_state" for message in session.messages)
```

Also prove one overlay only, no command/output text, no overlay with zero
references, and live-to-terminal status refresh between provider calls.

- [ ] **Step 2: Run tests and confirm stale transcript reaches provider**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_process_context.py appV2.3.1/tests/test_coding_agent.py -k process_overlay
```

Expected: no overlay is present and stale `running` remains uncorrected.

- [ ] **Step 3: Render a bounded machine-readable custom message**

```python
def process_context_message(records: Sequence[ProcessContextRecord]) -> CustomMessage | None:
    if not records:
        return None
    lines = ["<managed-process-state>"]
    for record in records[:16]:
        fields = [
            record.session_id,
            f"status={record.status}",
            f"cursor={record.cursor}",
            f"outputSize={record.output_size}",
        ]
        if record.exit_code is not None:
            fields.append(f"exitCode={record.exit_code}")
        if record.durable_output:
            fields.append("durableOutput=true")
        if record.reason:
            fields.append(f"reason={record.reason}")
        lines.append(" ".join(fields))
    lines.append("</managed-process-state>")
    return CustomMessage(
        custom_type="managed_process_state",
        content="\n".join(lines),
        display=False,
        details=None,
        timestamp=now_ms(),
    )
```

All enum/reason values come from fixed code constants.

- [ ] **Step 4: Append the overlay after caller compaction transform**

```python
def _transform_context(self, messages: list[AgentMessage], signal=None) -> list[AgentMessage]:
    transformed = (
        self._caller_transform_context(messages, signal)
        if self._caller_transform_context is not None
        else list(messages)
    )
    overlay = self._process_context.overlay(transformed) if self._process_context else None
    if overlay is not None:
        transformed = [*transformed, overlay]
    if self._extension_runner.has_handlers("context"):
        return self._extension_runner.emit_context(transformed)
    return transformed
```

Do not mutate `messages` or `agent.state.messages`. Give extensions the
reconciled context, preserving existing extension ordering after compaction.

- [ ] **Step 5: Run provider, compaction-transform, and persistence tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_process_context.py \
  appV2.3.1/tests/test_coding_agent.py \
  appV2.3.1/tests/test_app_integration.py \
  -k "process_overlay or transform_context or preflight_compaction or persistence"
```

Expected: pass and JSONL contains no `managed_process_state` custom entry.

- [ ] **Step 6: Commit transient reconciliation**

```bash
git add appV2.3.1/appv231/coding_agent/process_context.py appV2.3.1/appv231/coding_agent/agent_session.py appV2.3.1/tests/test_process_context.py appV2.3.1/tests/test_coding_agent.py appV2.3.1/tests/test_app_integration.py
git commit -m "fix(appv231): overlay live process state"
```

### Task 4: Compaction Process Ledger Through Coding Adapter

**Files:**
- Modify: `appV2.3.1/appv231/coding_agent/process_context.py`
- Modify: `appV2.3.1/appv231/coding_agent/compaction_adapter.py`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py`
- Extend: `appV2.3.1/tests/test_coding_agent.py`
- Extend: `appV2.3.1/tests/test_app_integration.py`
- Extend: `appV2.3.1/tests/test_process_context.py`

**Interfaces:**
- Produces: compaction detail key `managedProcesses` and `<managed-processes>` rendering.
- Consumes: compressor-provided details and Task 2 context records.
- Guarantees: manual, preflight, post-response, and overflow compaction retain process state without editing compressor code.

- [ ] **Step 1: Add failing active-process compaction regression**

```python
def test_compaction_persists_active_process_ledger_without_core_change(tmp_path: Path) -> None:
    app, process_id = app_with_running_process_and_compaction(tmp_path)
    app.session.compact(summarizer=fake_summarizer)

    entries = [json.loads(line) for line in Path(app.session.session_path).read_text().splitlines()]
    compacted = next(entry for entry in reversed(entries) if entry["type"] == "compaction")
    assert compacted["details"]["managedProcesses"] == [
        {
            "sessionId": process_id,
            "status": "running",
            "cursor": 0,
            "outputSize": 0,
            "exitCode": None,
            "durableOutput": False,
        }
    ]
    assert process_id in default_convert_to_llm(app.session.messages)[0].content[0].text
```

Add terminal-from-store, unavailable-after-restart, sixteen-record cap,
duplicate summary tags, and preservation of `readFiles`/`modifiedFiles`.

- [ ] **Step 2: Run compaction tests and confirm process ID is dropped**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_coding_agent.py appV2.3.1/tests/test_app_integration.py -k process_ledger
```

Expected: compaction details contain only file inventory or are absent.

- [ ] **Step 3: Merge bounded records into existing details**

```python
def merge_process_compaction_details(
    details: object, records: Sequence[ProcessContextRecord]
) -> dict[str, object] | None:
    merged = dict(details) if isinstance(details, Mapping) else {}
    serialized = [record.as_compaction_details() for record in records[:16]]
    if serialized:
        merged["managedProcesses"] = serialized
    return merged or None
```

Before both `append_compaction` call sites in AgentSession, resolve against the
source messages and merge. Do not pass process details into the compressor; add
them only at the coding-session persistence boundary.

- [ ] **Step 4: Render process details from CompactionSummaryMessage**

```python
_MANAGED_PROCESSES_TAG = "managed-processes"


def _process_detail_section(value: object) -> str:
    if not isinstance(value, list):
        return ""
    lines: list[str] = []
    for item in value[:16]:
        if not isinstance(item, Mapping):
            continue
        session_id = item.get("sessionId")
        status = item.get("status")
        if not _valid_process_id(session_id) or status not in _CONTEXT_STATUSES:
            continue
        fields = [session_id, f"status={status}"]
        for key in ("cursor", "outputSize", "exitCode", "durableOutput"):
            if key in item:
                fields.append(f"{key}={_safe_scalar(item[key])}")
        lines.append(" ".join(fields))
    return "" if not lines else "<managed-processes>\n" + "\n".join(lines) + "\n</managed-processes>"
```

Append this section only if the summary does not already contain the tag.

- [ ] **Step 5: Run every coding-app compaction path**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_process_context.py \
  appV2.3.1/tests/test_coding_agent.py \
  appV2.3.1/tests/test_app_integration.py \
  -k "compact or context_overflow or process_ledger"
```

Expected: pass for manual, preflight, post-response, and overflow recovery.

- [ ] **Step 6: Prove compaction redzone has no diff**

```bash
test -z "$(git diff --name-only | rg '^appV2\.3\.1/appv231/compaction/' || true)"
```

Expected: exit zero.

- [ ] **Step 7: Commit coding-adapter compaction state**

```bash
git add appV2.3.1/appv231/coding_agent/process_context.py appV2.3.1/appv231/coding_agent/compaction_adapter.py appV2.3.1/appv231/coding_agent/agent_session.py appV2.3.1/tests/test_process_context.py appV2.3.1/tests/test_coding_agent.py appV2.3.1/tests/test_app_integration.py
git commit -m "fix(appv231): preserve process state in compaction"
```

### Task 5: Session Branch, Resume, and Export Compatibility

**Files:**
- Extend: `appV2.3.1/tests/test_session_store_recovery.py`
- Extend: `appV2.3.1/tests/test_session_catalog.py`
- Extend: `appV2.3.1/tests/test_session_commands.py`
- Extend: `appV2.3.1/tests/test_coding_agent.py`
- Extend: `appV2.3.1/tests/test_app_integration.py`

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: compatibility proof; production changes only if a regression exposes a coding-layer bug.

- [ ] **Step 1: Add an end-to-end persistent-session regression**

```python
def test_resume_compact_fork_and_export_preserve_reconciled_process_state(tmp_path: Path) -> None:
    first = build_app(tmp_path)
    process_id = launch_and_finish_large_process(first)
    first.session.compact(summarizer=fake_summarizer)
    session_path = first.session.session_path
    first.close()

    second = build_app(tmp_path, session_path=session_path)
    assert resolved_process(second, process_id).status == "exited"
    forked = second.session.fork(second.session.session_entries[-1]["id"])
    exported = second.session.export_to_jsonl(str(tmp_path / "export.jsonl"))

    assert Path(forked.session_path).exists()
    assert Path(exported).exists()
    assert process_id in Path(exported).read_text(encoding="utf-8")
```

- [ ] **Step 2: Run session workflow tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_session_store_recovery.py \
  appV2.3.1/tests/test_session_store_performance.py \
  appV2.3.1/tests/test_session_catalog.py \
  appV2.3.1/tests/test_session_commands.py \
  appV2.3.1/tests/test_coding_agent.py \
  appV2.3.1/tests/test_app_integration.py \
  -k "session or resume or fork or export or process"
```

Expected: pass with unchanged JSONL version and no migration command.

- [ ] **Step 3: Verify a production-sized JSONL fixture read-only**

Generate a representative 2,241-entry file through the public append API, then
open a second read-only SessionStore over it. This keeps the acceptance command
portable and never reads or mutates an operator's real session:

```bash
tmpdir="$(mktemp -d)"
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python - "$tmpdir/session.jsonl" <<'PY'
import sys, time
from appv231.ai.types import UserMessage
from appv231.coding_agent.session_store import SessionStore

path = sys.argv[1]
writer = SessionStore(path, cwd="/workspace")
for index in range(2_240):
    writer.append_message(UserMessage(content=f"message-{index}"))
started = time.monotonic()
store = SessionStore(path, cwd="/workspace")
print({"entries": len(store.file_entries), "loadSeconds": time.monotonic() - started})
PY
rm -rf "$tmpdir"
```

Expected: 2,241 entries load successfully and no operator session is accessed.

- [ ] **Step 4: Leave compatibility verification commit-free**

This task is verification-only. If it exposes a production defect, stop this
task and add the fix plus its regression to the owning earlier task before
rerunning Steps 1-3. Do not create an empty or catch-all compatibility commit.

### Task 6: Session and Context Acceptance Gate

**Files:**
- Verify only; no production edits expected.

**Interfaces:**
- Consumes: all tasks in this plan.
- Produces: evidence for final integrated verification.

- [ ] **Step 1: Run all persistence/context tests three times**

```bash
for run in 1 2 3; do
  TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
    appV2.3.1/tests/test_session_store_recovery.py \
    appV2.3.1/tests/test_session_store_performance.py \
    appV2.3.1/tests/test_session_catalog.py \
    appV2.3.1/tests/test_process_context.py \
    appV2.3.1/tests/test_app_integration.py || exit 1
done
```

Expected: three passes and parse-budget assertions remain below threshold.

- [ ] **Step 2: Run compaction compatibility suites**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_compaction.py \
  appV2.3.1/tests/test_compaction_integration.py \
  appV2.3.1/tests/test_compaction_timing.py \
  appV2.3.1/tests/test_coding_agent.py \
  appV2.3.1/tests/test_app_integration.py \
  -k "compact or process_ledger or session_store"
```

Expected: pass with the compressor unchanged.

- [ ] **Step 3: Prove both redzones are untouched**

```bash
if git diff --name-only 96b38b9..HEAD | rg '^appV2\.3\.1/appv231/(agent|compaction)/'; then
  echo 'redzone modified' >&2
  exit 1
fi
```

Expected: no output and exit zero.
