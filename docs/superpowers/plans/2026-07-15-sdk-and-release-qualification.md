# SDK Parity and Production Qualification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Do not use subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the Python public SDK surfaces and prove the combined production-safe Pi-parity program through source, package, installed, and container evidence.

**Architecture:** Compose existing Travis owners behind a Python `AgentHarness`; do not replace them. Add async-first model discovery, a narrow stream proxy, and an independent optional image API. Finish with requirement-by-requirement acceptance gates.

**Tech Stack:** Python 3.13, asyncio, httpx, existing provider registry/event streams, pytest, build, npm launcher tests, Docker release image.

## Global Constraints

- SDK APIs are Pythonic behavioral equivalents, not TypeScript signature clones.
- Sync wrappers must fail clearly or use a safe worker boundary inside an active event loop; they never call `asyncio.run()` on the active loop thread.
- Image generation is independent from chat model selection and optional for users who do not invoke it.
- Public exports preserve existing names.
- Do not invoke Git at all, including read-only status or diff commands.

---

### Task 1: Async-safe model discovery

**Files:**
- Modify: `travis/ai/models.py`
- Modify: `travis/ai/__init__.py`
- Test: `tests/test_ai_models.py`
- Test: `tests/test_models_runtime.py`

**Interfaces:**
- Produces: `AsyncModels`
- Produces: `Models.async_api() -> AsyncModels`
- Preserves: existing synchronous `Models` behavior outside running loops

- [x] **Step 1: Write the active-loop regression**

```python
@pytest.mark.asyncio
async def test_async_models_operates_inside_running_loop() -> None:
    models = Models(provider_loader=async_provider_loader)
    result = await models.async_api().refresh("openrouter")
    assert result[0].provider == "openrouter"
```

Add a sync-call test inside the same loop and require an actionable `ModelsError` directing the caller to `async_api()` rather than a raw `asyncio.run()` exception.

- [x] **Step 2: Extract async-first operations**

```python
class AsyncModels:
    def __init__(self, owner: Models) -> None:
        self._owner = owner

    async def refresh(self, provider: str | None = None) -> list[Model]:
        return await self._owner._refresh_async(provider)

    async def find(self, provider: str, model_id: str) -> Model | None:
        await self._owner._ensure_loaded_async(provider)
        return self._owner.find_cached(provider, model_id)
```

Make sync methods settle async work only when no event loop is running on the current thread.

- [x] **Step 3: Run model API tests**

```bash
.venv/bin/python -m pytest -q tests/test_ai_models.py tests/test_models_runtime.py
```

Expected: all tests pass in sync and async contexts.

Result: `25 passed`.

### Task 2: Public `AgentHarness`

**Files:**
- Create: `travis/coding_agent/agent_harness.py`
- Modify: `travis/coding_agent/__init__.py`
- Modify: `travis/__init__.py`
- Create: `tests/test_agent_harness.py`

**Interfaces:**
- Produces: `AgentHarnessConfig`
- Produces: `AgentHarness.create(config) -> AgentHarness`
- Produces: async prompt/continue/compact/session/resource operations

- [x] **Step 1: Write a composition test**

```python
@pytest.mark.asyncio
async def test_harness_composes_existing_session_and_resource_owners(tmp_path: Path) -> None:
    harness = AgentHarness.create(
        AgentHarnessConfig(cwd=str(tmp_path), model=faux_model(), persist_session=False)
    )
    result = await harness.prompt("hello")
    assert result.stop_reason == "stop"
    assert harness.session.cwd == str(tmp_path.resolve())
    assert harness.resource_loader is harness.session.resource_loader
    await harness.close()
```

- [x] **Step 2: Define the config and owner boundary**

```python
@dataclass(frozen=True)
class AgentHarnessConfig:
    cwd: str
    model: Model
    agent_dir: str | None = None
    persist_session: bool = True
    session_path: str | None = None
    thinking_level: str = "off"
    trust_override: bool | None = None
    offline: bool = False
```

`AgentHarness` owns one `CodingApp` or the same lower-level services, exposes them read-only, and delegates mutations through `AgentSessionRuntime`, `CompactionCoordinator`, and resource owners.

- [x] **Step 3: Expose focused capabilities**

Provide async methods for prompt, continue, compact, abort, switch/fork/clone session, reload resources, list skills/templates/themes, and subscribe to normalized events. Do not duplicate session-tree or compaction logic.

- [x] **Step 4: Add lifecycle safety**

Support `async with AgentHarness.create(...) as harness`. Closing aborts active work, waits boundedly, closes process/session owners, and is idempotent.

- [x] **Step 5: Run harness tests**

```bash
.venv/bin/python -m pytest -q tests/test_agent_harness.py tests/test_agent_loop.py tests/test_session_commands.py
```

Expected: all selected tests pass.

Result: `57 passed`.

### Task 3: Stream proxy

**Files:**
- Create: `travis/ai/stream_proxy.py`
- Modify: `travis/ai/__init__.py`
- Create: `tests/test_stream_proxy.py`

**Interfaces:**
- Produces: `stream_proxy(source, *, transform=None, on_event=None) -> EventStream`

- [x] **Step 1: Write ordering, cancellation, and error tests**

Test that events preserve source order, transforms may replace or suppress events, cancellation closes the source, callbacks cannot orphan tasks, and source errors reach `result()` unchanged.

- [x] **Step 2: Implement the proxy over existing `EventStream`**

Use one bounded forwarding task. Await async callbacks. Ensure final result is settled exactly once and source cleanup runs in `finally`.

- [x] **Step 3: Run stream tests**

```bash
.venv/bin/python -m pytest -q tests/test_stream_proxy.py tests/test_ai_event_stream.py tests/test_abort_context.py
```

Expected: all selected tests pass.

Result: `12 passed`.

### Task 4: Optional image-generation registry

**Files:**
- Create: `travis/ai/images.py`
- Create: `travis/ai/image_types.py`
- Modify: `travis/ai/__init__.py`
- Create: `tests/test_ai_images.py`

**Interfaces:**
- Produces: `ImageModel`, `ImageGenerationOptions`, `GeneratedImage`
- Produces: `register_image_provider(name, generate)`
- Produces: `async generate_images(model, prompt, options) -> tuple[GeneratedImage, ...]`

- [x] **Step 1: Write registry and redaction tests**

Test provider registration, unknown providers, API-key resolution through the existing auth context, abort handling, binary/base64 outputs, URL outputs, and error redaction.

- [x] **Step 2: Define independent image types**

```python
@dataclass(frozen=True)
class ImageModel:
    id: str
    provider: str
    api: str
    sizes: tuple[str, ...] = ()


@dataclass(frozen=True)
class GeneratedImage:
    mime_type: str
    data: bytes | None = None
    url: str | None = None
    revised_prompt: str | None = None
```

Require exactly one of `data` or `url` on a generated result.

- [x] **Step 3: Add an OpenRouter-compatible provider adapter**

Use `httpx.AsyncClient`, existing auth resolution, bounded response bodies, abort-aware timeouts, and provider error formatting. Do not add image models to chat model selection.

- [x] **Step 4: Run image tests**

```bash
.venv/bin/python -m pytest -q tests/test_ai_images.py tests/test_auth_storage_hardening.py
```

Expected: all tests pass without live network calls.

Result: `7 passed`.

### Task 5: Program-level contract audit

**Files:**
- Modify: `tests/test_reference_runtime_contract.py`
- Create: `tests/test_pi_behavioral_parity.py`
- Create: `tests/test_hermes_compaction_parity.py`
- Modify: `scripts/check_repository_hygiene.py`
- Modify: `scripts/verify_acceptance.py`

**Interfaces:**
- Consumes: pinned reference contracts and completed implementation
- Produces: machine-readable parity and safety report

- [x] **Step 1: Define the parity manifest**

The Pi manifest lists loop invariants, 33 extension events, resource behaviors, package operations, CLI modes/options, session operations, and SDK surfaces. The Hermes manifest lists threshold bands, full-request accounting, prompt-only usage, tail fields, head decay, boundary stripping, cooldown, fallback wording, and auxiliary capacity.

- [x] **Step 2: Characterize each manifest entry**

Every entry names one proving test. A missing test fails the manifest check. Explicit Travis divergences require a reason and a safety or preserved-invariant test.

- [x] **Step 3: Exclude reference trees from runtime and distributions**

Add checks proving no active source imports `pi`, `hermes-agent`, or `appv231`, and no wheel/sdist/npm package contains them or the planning reports.

- [x] **Step 4: Run contract tests**

```bash
.venv/bin/python -m pytest -q \
  tests/test_reference_runtime_contract.py \
  tests/test_pi_behavioral_parity.py \
  tests/test_hermes_compaction_parity.py \
  tests/architecture/test_repository_hygiene.py
```

Expected: all tests pass and every manifest entry points to passing evidence.

Result: `92 passed`; the report validates 77 Pi contracts (33 extension events) and 11 Hermes contracts with zero unresolved evidence.

### Task 6: Full production qualification

**Files:**
- Modify: `README.md`
- Modify: `docs/verification/acceptance-matrix.md`
- Modify: `docs/verification/full-suite.md`
- Modify: `evals/verify_run.py`
- Modify: `evals/container_smoke.py`

**Interfaces:**
- Consumes: all completed phase gates
- Produces: final company-wide-safe acceptance record

- [x] **Step 1: Run focused security and memory gates**

```bash
.venv/bin/python -m pytest -q \
  tests/test_project_trust.py \
  tests/test_catalog_generation.py \
  tests/test_context_estimate.py \
  tests/test_compaction_policy.py \
  tests/test_compaction.py \
  tests/test_compaction_timing.py \
  tests/test_compaction_integration.py
```

Expected: all tests pass.

Result: `177 passed`.

- [x] **Step 2: Run focused parity gates**

```bash
.venv/bin/python -m pytest -q \
  tests/test_extension_event_parity.py \
  tests/test_resource_runtime_parity.py \
  tests/test_package_manager.py \
  tests/test_automation_modes.py \
  tests/test_rpc_mode.py \
  tests/test_session_parity.py \
  tests/test_agent_harness.py \
  tests/test_pi_behavioral_parity.py \
  tests/test_hermes_compaction_parity.py
```

Expected: all tests pass.

Result: `49 passed`.

- [x] **Step 3: Run the complete Python and launcher suites**

```bash
.venv/bin/python -m pytest -q
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Expected: all commands exit zero.

Result: final Python rerun `1534 passed`; npm launcher `20 passed`; npm dry-run contains five declared files.

- [x] **Step 4: Build Python packages and smoke the installed entry**

```bash
.venv/bin/python -m build
```

Install the wheel into a fresh Python 3.13 virtual environment, run `pip check`, run `travis234 --help`, and execute faux-provider print/JSON/RPC smoke turns outside the checkout.

Result: wheel and sdist built; fresh Python 3.13.13 install passed dependency check, CLI help, and print/JSON/RPC faux turns outside the checkout.

- [x] **Step 5: Build and smoke the release container**

Build `Dockerfile.release` without cache. Run as the unprivileged `travis` user. Prove isolated `~/.travis234`, no dotenv forwarding, untrusted project extension suppression, manual and automatic compaction, managed-process cleanup, npm launcher operation, and clean exit.

Result: no-cache `travis234:phase5-local` build and expanded unprivileged container smoke passed.

- [x] **Step 6: Execute the acceptance verifier**

```bash
.venv/bin/python scripts/verify_acceptance.py
```

Expected: every offline/company-wide safety and parity row passes. Provider-paid scenarios remain explicitly separate and cannot be represented as passed without credentials and captured evidence.

Result: 23 acceptance rows validated; 77 Pi and 11 Hermes contracts have zero unresolved evidence. Paid-provider acceptance remains `blocked`.

- [x] **Step 7: Completion audit and non-Git handoff**

For every design requirement, identify its test or runtime evidence and inspect the current files directly. Run syntax, formatting, and whitespace checks that do not invoke Git. Report the planned/touched file list, pass counts, build artifacts, container results, and any external credential-dependent gate. Do not invoke Git.

Result: direct-filesystem audit completed; compileall, hygiene, acceptance, distributions, clean install, and release-container evidence are recorded without Git metadata.
