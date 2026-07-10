# appv231 Coding Policy and Execution Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make coding-agent guardrails explicit, composable, and honest about enforcement while keeping all domain policy outside the generic core.

**Architecture:** Replace monolithic/session-coupled decisions with an immutable turn context and typed policy pipeline. File tools use canonical workspace and artifact capabilities. Bash runs through a declared trusted or container-sandboxed backend; shell parsing remains advisory classification rather than a security claim.

**Tech Stack:** Python 3.13, pathlib, dataclasses, existing coding tools, Docker sandbox launched by the npm wrapper, pytest, Node.js test runner.

## Global Constraints

- Complete Plans 1 and 2 first.
- Do not edit compaction files or perform mutating git operations; read-only status and diff checks are permitted.
- Do not add coding policy back into `appv231.agent`.
- Do not authorize package mutation through raw substring matching.
- Do not describe trusted local bash execution as filesystem-contained.
- Preserve current tool names and result shapes unless a task explicitly adds structured metadata.

---

### Task 1: Typed Coding Policy Pipeline

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/policies/types.py`
- Create: `appV2.3.1/appv231/coding_agent/policies/pipeline.py`
- Modify: `appV2.3.1/appv231/coding_agent/policies/tool_guardrails.py`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py:767-930,3500-3900`
- Create: `appV2.3.1/tests/test_coding_policy.py`

**Interfaces:**
- Produces: `CodingTurnContext(cwd, latest_user_message, capabilities, tool_catalog, run_id, turn_id)`
- Produces: `Allow`, `Block(code, reason, metadata)`, `RequireConsent(capability, reason)`
- Produces: `ToolPolicy.evaluate(call: ToolCallView, context: CodingTurnContext) -> PolicyDecision`
- Produces: `PolicyPipeline.evaluate(...) -> PolicyDecision`

- [ ] **Step 1: Write pipeline ordering and short-circuit tests**

```python
def test_policy_pipeline_returns_first_non_allow_decision():
    visited: list[str] = []
    policies = [
        StubPolicy("first", Allow(), visited),
        StubPolicy("consent", RequireConsent("package_mutation", "approval required"), visited),
        StubPolicy("never", Block("late", "must not run"), visited),
    ]
    decision = PolicyPipeline(policies).evaluate(tool_call("bash"), turn_context())
    assert decision == RequireConsent("package_mutation", "approval required")
    assert visited == ["first", "consent"]
```

Add tests proving context is immutable and block metadata is not encoded as JSON in the tool-result text.

- [ ] **Step 2: Run and verify missing policy types**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_coding_policy.py -k pipeline
```

Expected before implementation: import failure.

- [ ] **Step 3: Implement decision types and pipeline**

```python
@dataclass(frozen=True)
class Allow:
    pass

@dataclass(frozen=True)
class Block:
    code: str
    reason: str
    metadata: Mapping[str, object] = field(default_factory=dict)

@dataclass(frozen=True)
class RequireConsent:
    capability: str
    reason: str
```

The pipeline stops at the first non-`Allow` result. It does not know about Agent hooks or TUI rendering.

- [ ] **Step 4: Adapt existing guardrail controller**

Wrap current loop-progress and duplicate-observation behavior as policies without changing thresholds. `AgentSession.before_tool_call` converts `Block` or `RequireConsent` into the core `BeforeToolCallResult` and emits a typed coding-agent policy event separately.

Replace the ambiguous `hard_stop_enabled` switch with two explicit settings: `guidance_enabled` and `blocking_enabled`. A migration adapter maps the legacy field once at the coding-profile configuration boundary. Add tests proving `blocking_enabled=False` disables every built-in blocking threshold while guidance may remain enabled.

- [ ] **Step 5: Run policy and existing guardrail tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_coding_policy.py appV2.3.1/tests/test_coding_agent.py -k "guardrail or no_progress or repeated"
```

Expected: pass.

### Task 2: Structured Package-Mutation Consent

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/policies/package_consent.py`
- Modify: `appV2.3.1/appv231/coding_agent/policies/tool_guardrails.py:115-153,331-384,780-818`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py`
- Modify: `appV2.3.1/appv231/cli.py:350-390`
- Extend: `appV2.3.1/tests/test_coding_policy.py`
- Extend: `appV2.3.1/tests/test_cli.py`

**Interfaces:**
- Produces: `TurnCapabilities.grant(name: str, uses: int = 1)`
- Produces: `TurnCapabilities.consume(name: str) -> bool`
- Produces: `AgentSession.grant_capability(name: str, uses: int = 1) -> None`
- Produces CLI flag: `--allow-package-install` grants one turn-scoped package mutation capability
- Produces: `PackageMutationPolicy`

- [ ] **Step 1: Write classifier and capability tests**

```python
@pytest.mark.parametrize("command", [
    "npm install left-pad",
    "/usr/bin/npm install left-pad",
    "env npm install left-pad",
    "python -m pip install requests",
])
def test_package_mutation_requires_structured_capability(command):
    policy = PackageMutationPolicy()
    context = turn_context(capabilities=TurnCapabilities())
    assert isinstance(policy.evaluate(bash_call(command), context), RequireConsent)

def test_package_mutation_consumes_exactly_one_grant():
    capabilities = TurnCapabilities()
    capabilities.grant("package_mutation", uses=1)
    policy = PackageMutationPolicy()
    assert isinstance(policy.evaluate(bash_call("npm install x"), turn_context(capabilities)), Allow)
    assert isinstance(policy.evaluate(bash_call("npm install y"), turn_context(capabilities)), RequireConsent)
```

Add a regression proving a prompt containing incidental text such as documentation about `npm install` does not grant consent.

- [ ] **Step 2: Verify absolute executable bypass**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_coding_policy.py -k package_mutation
```

Expected before repair: `/usr/bin/npm install` is allowed without consent.

- [ ] **Step 3: Normalize executable basenames**

Tokenize with the existing shell tokenizer, skip supported environment wrappers, and compare `Path(token).name` against the package-manager set. Keep command recognition separate from consent authority.

- [ ] **Step 4: Wire explicit grants**

`AgentSession` snapshots capabilities into `CodingTurnContext`; successful authorization consumes a use. `--allow-package-install` grants the capability before the initial prompt. Interactive `/allow` integration is completed in Plan 5.

- [ ] **Step 5: Delete natural-language authorization markers**

Remove `_PACKAGE_MANAGER_ALLOW_MARKERS` and `_user_message_allows_package_manager_mutation`. Deny wording may remain a policy hint but cannot override an explicit absence of capability.

- [ ] **Step 6: Run policy and CLI tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_coding_policy.py appV2.3.1/tests/test_cli.py -k "package or allow"
```

Expected: pass.

### Task 3: Canonical Workspace Capability for File Tools

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/capabilities.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/path_utils.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/read.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/write.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/edit.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/find.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/grep.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/ls.py`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py:3560-3675,4770-4855`
- Extend: `appV2.3.1/tests/test_coding_policy.py`
- Extend: `appV2.3.1/tests/test_coding_agent.py`

**Interfaces:**
- Produces: `WorkspaceCapability(root: Path, extra_read_roots: tuple[Path, ...] = ())`
- Produces: `resolve(path: str, access: Literal["read", "write", "execute"]) -> Path`
- Raises: `CapabilityViolation(code, requested_path, resolved_path)`
- Consumed by all built-in file-tool factories

- [ ] **Step 1: Write relative, prefix, and symlink escape tests**

```python
@pytest.mark.parametrize("requested", ["../outside.txt", "sub/../../outside.txt"])
def test_workspace_capability_rejects_relative_escape(tmp_path, requested):
    workspace = tmp_path / "work"
    workspace.mkdir()
    with pytest.raises(CapabilityViolation, match="outside_workspace"):
        WorkspaceCapability(workspace).resolve(requested, access="read")

def test_workspace_capability_uses_path_ancestry_not_substring(tmp_path):
    root = tmp_path / "a"
    sibling = tmp_path / "abc" / "secret.txt"
    sibling.parent.mkdir()
    sibling.write_text("secret")
    with pytest.raises(CapabilityViolation):
        WorkspaceCapability(root).resolve(str(sibling), access="read")
```

Add a symlink inside the workspace pointing outside and assert read/write rejection after resolution.

- [ ] **Step 2: Verify current relative bash/file scope assumptions fail**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_coding_policy.py -k workspace_capability
```

Expected before repair: missing capability or an escape succeeds.

- [ ] **Step 3: Implement exact ancestry checks**

```python
resolved = (self.root / requested).resolve() if not Path(requested).is_absolute() else Path(requested).resolve()
if resolved != self.root and self.root not in resolved.parents:
    raise CapabilityViolation("outside_workspace", requested, resolved)
```

For non-existent write targets, resolve the nearest existing parent and append the remaining components without following a future symlink.

- [ ] **Step 4: Inject one capability into all file tools**

Tool factories accept `workspace: WorkspaceCapability` instead of reimplementing checks from raw `cwd`. Preserve path display relative to the workspace where possible.

- [ ] **Step 5: Remove substring authorization**

Delete `_user_authorized_absolute_path()` and replace any caller-authorized external root with an exact `extra_read_roots` capability constructed by the application boundary.

- [ ] **Step 6: Run all file-tool tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_coding_policy.py appV2.3.1/tests/test_coding_agent.py -k "read or write or edit or find or grep or workspace"
```

Expected: pass.

### Task 4: Session Artifact Capabilities

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/artifacts.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/output_spool.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/read.py`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py`
- Extend: `appV2.3.1/tests/test_output_spool.py`
- Extend: `appV2.3.1/tests/test_coding_policy.py`

**Interfaces:**
- Produces: `ArtifactRegistry.register(path: Path, kind: str, access: Literal["read"]) -> ArtifactRef`
- Produces: `ArtifactRegistry.resolve_read(path_or_id: str) -> Path | None`
- Produces: `ArtifactRegistry.close(remove_files: bool = True) -> None`
- Consumed by: output spool and read-tool capability resolution

- [ ] **Step 1: Write exact-artifact access tests**

```python
def test_registered_output_artifact_is_readable_without_temp_directory_bypass(tmp_path):
    registry = ArtifactRegistry()
    artifact = tmp_path / "tool-output"
    artifact.write_text("complete")
    ref = registry.register(artifact, kind="bash-output", access="read")
    assert registry.resolve_read(ref.id) == artifact.resolve()
    assert registry.resolve_read(str(artifact)) == artifact.resolve()
    assert registry.resolve_read(str(tmp_path / "other")) is None
```

Add lifecycle tests for cleanup and a no-cleanup mode used when a session intentionally preserves artifacts.

- [ ] **Step 2: Implement opaque IDs and exact resolved paths**

```python
@dataclass(frozen=True)
class ArtifactRef:
    id: str
    path: Path
    kind: str
    access: Literal["read"] = "read"
```

Registry lookup compares exact resolved paths. It never authorizes a parent directory.

- [ ] **Step 3: Register spool artifacts**

Pass the session registry into `OutputSpool`. Include `artifactId` and the exact path in tool-result details when truncation occurs.

- [ ] **Step 4: Integrate read resolution**

The read tool first asks `WorkspaceCapability`; on an outside-workspace read failure, it may resolve only an exact read artifact through `ArtifactRegistry`.

- [ ] **Step 5: Run artifact and output tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_output_spool.py appV2.3.1/tests/test_coding_policy.py -k artifact
```

Expected: pass.

### Task 5: Honest Bash Execution Backends

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/execution_backend.py`
- Modify: `appV2.3.1/appv231/coding_agent/bash_executor.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/bash.py`
- Modify: `appV2.3.1/appv231/coding_agent/agent_session.py`
- Modify: `packages/appv231-cli/bin/appv231.js`
- Extend: `packages/appv231-cli/test/appv231-cli.test.js`
- Extend: `appV2.3.1/tests/test_coding_policy.py`

**Interfaces:**
- Produces: `ExecutionBackend.mode: Literal["trusted", "sandboxed"]`
- Produces: `ExecutionBackend.spawn(command, cwd, env, options) -> ProcessHandle`
- Produces: `TrustedLocalBackend`
- Produces: `ContainerSandboxBackend(workspace_root, agent_home)`
- Preserves: existing bash timeout, cancellation, and process-tree behavior

- [ ] **Step 1: Write backend declaration tests**

```python
def test_local_backend_never_claims_filesystem_containment():
    backend = TrustedLocalBackend()
    assert backend.mode == "trusted"
    assert backend.filesystem_contained is False

def test_container_backend_requires_sandbox_marker(monkeypatch, tmp_path):
    monkeypatch.delenv("APPV231_SANDBOX", raising=False)
    with pytest.raises(RuntimeError, match="sandbox marker"):
        ContainerSandboxBackend(tmp_path / "workspace", tmp_path / "agent-home")
```

- [ ] **Step 2: Verify npm launcher mount contract**

Extend the Node test to assert Docker receives only the selected workspace and agent-home host mounts, sets `APPV231_SANDBOX=1`, uses the non-root image user, and never mounts the host root or Docker socket.

- [ ] **Step 3: Implement backend selection**

`AgentSession` selects `ContainerSandboxBackend` only when the launcher marker and expected canonical roots match. Direct local Python execution selects `TrustedLocalBackend`. The policy/UI can report this mode without claiming that command-token scanning enforces containment.

- [ ] **Step 4: Reclassify bash path scanning**

Keep command/path scanning for warning, loop detection, and consent decisions. Remove block messages that claim it is a complete sandbox. A caller requiring `sandboxed` mode fails closed when only trusted mode is available.

- [ ] **Step 5: Run Python and Node backend tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_coding_policy.py appV2.3.1/tests/test_sandbox_launcher.py
node --test packages/appv231-cli/test/appv231-cli.test.js
```

Expected: pass.

### Task 6: Atomic Write and Edit Replacement

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/tools/atomic_file.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/write.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/edit.py`
- Modify: `appV2.3.1/appv231/coding_agent/tools/file_mutation_queue.py`
- Extend: `appV2.3.1/tests/test_coding_policy.py`
- Extend: `appV2.3.1/tests/test_coding_agent.py`

**Interfaces:**
- Produces: `atomic_replace_text(path: Path, content: str, *, encoding: str = "utf-8") -> None`
- Guarantees: failure before `os.replace()` leaves the previous file byte-for-byte intact
- Preserves: per-file mutation queue ordering and existing mode bits

- [ ] **Step 1: Write interrupted-write regressions**

```python
def test_atomic_write_failure_preserves_original(tmp_path, monkeypatch):
    target = tmp_path / "file.txt"
    target.write_text("original", encoding="utf-8")
    monkeypatch.setattr(os, "replace", lambda *_args: raise_(OSError("interrupted")))
    with pytest.raises(OSError, match="interrupted"):
        atomic_replace_text(target, "replacement")
    assert target.read_text(encoding="utf-8") == "original"
```

Add a successful replacement test preserving executable mode and an edit-tool test proving queued edits still serialize.

- [ ] **Step 2: Implement sibling temporary replacement**

Create the temporary file in the target directory, write/flush/fsync it, copy the target mode when it exists, and call `os.replace()`. Delete the temporary file in `finally` when replacement did not occur.

- [ ] **Step 3: Route write/edit final content through the helper**

Keep validation, diff generation, and mutation-queue ownership unchanged. Only the final filesystem replacement changes.

- [ ] **Step 4: Run mutation tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_coding_policy.py appV2.3.1/tests/test_coding_agent.py -k "atomic or write or edit or mutation_queue"
```

Expected: pass.

### Task 7: Coding Policy Gate

**Files:**
- Modify: none

**Interfaces:**
- Produces the coding profile consumed by provider, TUI, and evaluation plans

- [ ] **Step 1: Run focused policy/tool tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider \
  appV2.3.1/tests/test_coding_policy.py \
  appV2.3.1/tests/test_output_spool.py \
  appV2.3.1/tests/test_coding_agent.py \
  appV2.3.1/tests/test_sandbox_launcher.py \
  appV2.3.1/tests/test_agent_core_boundary.py
```

Expected: pass.

- [ ] **Step 2: Prove old policy ownership is gone**

```bash
test ! -e appV2.3.1/appv231/agent/tool_dispatch.py
test ! -e appV2.3.1/appv231/agent/tool_guardrails.py
rg -n "appv231\.agent\.tool_guardrails|_user_message_allows_package_manager_mutation|_user_authorized_absolute_path" appV2.3.1/appv231
```

Expected: both `test` commands succeed and `rg` has no output.

- [ ] **Step 3: Run the complete suite and redzone check**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests
git diff --exit-code -- appV2.3.1/appv231/compaction
```

Expected: full suite passes; redzone diff is empty.
