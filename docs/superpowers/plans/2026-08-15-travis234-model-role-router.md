# Travis234 Model Role Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a session-scoped, explainable model-role router and migrate compression, internal worker/reviewer subagents, and image turns without changing provider ownership or the generic agent loop.

**Architecture:** `ModelRoleRouter` is a focused coding-agent policy owner over existing `ModelRegistry`, `SettingsManager`, and `ScopedModel` contracts. It returns immutable resolutions with sanitized traces; `AgentSession` owns its primary snapshot, while `CodingApp`, internal subagents, and turn routing consume optional roles through narrow APIs.

**Tech Stack:** Python 3.13, dataclasses, existing Travis234 model/settings/session/provider abstractions, pytest, npm launcher tests, uv packaging, Docker release smoke.

## Global Constraints

- Product and CLI names remain `Travis234` and `travis234`; the Python import package remains `travis`.
- Preserve all user data under `~/.travis234`; introduce no alternate state path or migration alias.
- Store no credentials, headers, environment values, or provider responses in role settings or traces.
- Do not modify `travis/agent/agent_loop.py`, tool scheduling, iteration budgets, cancellation, steering, follow-up ordering, or bounded parallel execution.
- `ModelRegistry` and `AuthStorage` remain the only provider catalog/authentication owners.
- Project role-setting writes remain trust-gated through `SettingsManager`.
- Keep each focused owner at or below 750 lines and do not raise architecture limits.
- Add and observe a failing regression test before every behavior fix or feature implementation.
- Do not touch npm launcher behavior, MCP packages, or Phase 1C+ code.
- Do not push, publish, tag, or change account/package permissions.

---

### Task 1: Core model-role contracts and deterministic router

**Files:**
- Create: `travis/coding_agent/model_roles.py`
- Create: `tests/test_model_role_router.py`
- Modify: `travis/coding_agent/__init__.py`
- Modify: `tests/architecture/test_facade_boundaries.py`

**Interfaces:**
- Consumes: `ScopedModel`, `parse_model_pattern()`, `Model`, and the public `ModelRegistry` methods `get_all()`, `is_selectable()`.
- Produces: `MODEL_ROLES`, `CONFIGURABLE_MODEL_ROLES`, `ModelRole`, `ModelRoleTraceStep`, `ModelRoleResolution`, and `ModelRoleRouter`.

- [ ] **Step 1: Write failing router contract tests**

Create models and a duck-typed registry so routing tests do not depend on ambient provider credentials:

```python
from dataclasses import replace

from travis.ai.model_resolver import ScopedModel
from travis.ai.types import Model
from travis.coding_agent.model_roles import ModelRoleRouter


def _model(provider: str, model_id: str, *, inputs=("text",), reasoning=True) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider=provider,
        base_url="https://example.invalid/v1",
        reasoning=reasoning,
        input=list(inputs),
        context_window=128_000,
        max_tokens=8_192,
    )


class _Registry:
    def __init__(self, models, selectable=None):
        self.models = list(models)
        self.selectable = {
            (model.provider, model.id)
            for model in (self.models if selectable is None else selectable)
        }

    def get_all(self):
        return list(self.models)

    def is_selectable(self, model):
        return (model.provider, model.id) in self.selectable


class _Settings:
    def __init__(self, roles=None, sources=None):
        self.roles = dict(roles or {})
        self.sources = dict(sources or {})

    def get_model_role(self, role):
        return self.roles.get(role)

    def get_model_role_source(self, role):
        return self.sources.get(role)
```

Add tests that assert:

```python
def test_explicit_override_wins_and_emits_sanitized_trace():
    primary = _model("primary", "main")
    worker = _model("worker", "cheap")
    override = _model("override", "focused")
    events = []
    router = ModelRoleRouter(
        _Registry([primary, worker, override]),
        _Settings({"worker": "worker/cheap"}, {"worker": "global"}),
        ScopedModel(primary, "medium"),
        event_sink=events.append,
    )

    result = router.resolve("worker", override=ScopedModel(override, "high"))

    assert result.available is True
    assert result.scoped_model == ScopedModel(override, "high")
    assert result.source == "call_override"
    assert result.selected_role == "worker"
    assert result.fallback_trace[-1].outcome == "selected"
    assert events == [result.as_event()]
    assert "secret-test-key" not in repr(events)
```

```python
def test_reviewer_falls_back_to_configured_worker_before_primary():
    primary = _model("primary", "main")
    worker = _model("worker", "cheap")
    router = ModelRoleRouter(
        _Registry([primary, worker]),
        _Settings({"worker": "worker/cheap:low"}, {"worker": "project"}),
        ScopedModel(primary, "medium"),
    )

    result = router.resolve("reviewer")

    assert result.selected_role == "worker"
    assert result.source == "project"
    assert result.scoped_model == ScopedModel(worker, "low")
    assert [(step.role, step.outcome) for step in result.fallback_trace] == [
        ("reviewer", "missing"),
        ("worker", "selected"),
    ]
```

```python
def test_active_primary_switch_updates_only_implicit_fallback():
    first = _model("primary", "first")
    second = _model("primary", "second")
    explicit_worker = _model("worker", "fixed")
    settings = _Settings({"worker": "worker/fixed"}, {"worker": "global"})
    router = ModelRoleRouter(
        _Registry([first, second, explicit_worker]),
        settings,
        ScopedModel(first, "low"),
    )

    before = router.resolve("compression")
    router.set_primary(second, "high")
    after = router.resolve("compression")
    worker = router.resolve("worker")

    assert before.scoped_model == ScopedModel(first, "low")
    assert after.scoped_model == ScopedModel(second, "high")
    assert worker.scoped_model == ScopedModel(explicit_worker, None)
```

```python
def test_vision_rejects_incompatible_candidates_and_returns_unavailable():
    text_primary = _model("primary", "text-only")
    text_vision = _model("configured", "also-text")
    router = ModelRoleRouter(
        _Registry([text_primary, text_vision]),
        _Settings({"vision": "configured/also-text"}, {"vision": "global"}),
        ScopedModel(text_primary, "off"),
    )

    result = router.resolve("vision")

    assert result.available is False
    assert result.scoped_model is None
    assert result.source == "unavailable"
    assert [step.outcome for step in result.fallback_trace] == [
        "incompatible",
        "incompatible",
    ]
```

Also cover session bindings, unauthenticated configured models falling back to primary,
literal colon-suffixed model IDs, selector thinking suffixes, unknown roles, and supplying
both override forms.

- [ ] **Step 2: Run the tests and verify the red state**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest tests/test_model_role_router.py -q
```

Expected: collection fails because `travis.coding_agent.model_roles` does not exist.

- [ ] **Step 3: Implement the focused router**

Define the closed roles and immutable records:

```python
ModelRole = Literal["primary", "compression", "worker", "reviewer", "vision"]
MODEL_ROLES: tuple[ModelRole, ...] = (
    "primary", "compression", "worker", "reviewer", "vision"
)
CONFIGURABLE_MODEL_ROLES = frozenset(MODEL_ROLES[1:])
_ROLE_FALLBACKS: dict[ModelRole, tuple[ModelRole, ...]] = {
    "reviewer": ("worker",),
}


@dataclass(frozen=True)
class ModelRoleTraceStep:
    role: ModelRole
    source: str
    selector: str | None
    outcome: str
    model_ref: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ModelRoleResolution:
    requested_role: ModelRole
    selected_role: ModelRole | None
    scoped_model: ScopedModel | None
    source: str
    fallback_trace: tuple[ModelRoleTraceStep, ...]

    @property
    def available(self) -> bool:
        return self.scoped_model is not None

    def as_event(self) -> dict[str, object]:
        return {
            "role": self.requested_role,
            "selectedRole": self.selected_role,
            "source": self.source,
            "model": _model_ref(self.scoped_model.model) if self.scoped_model else None,
            "fallbackTrace": [asdict(step) for step in self.fallback_trace],
        }
```

Implement `ModelRoleRouter` with these rules:

```python
def resolve(
    self,
    role: ModelRole,
    *,
    override: ScopedModel | None = None,
    selector_override: str | None = None,
    required_inputs: Iterable[str] | None = None,
) -> ModelRoleResolution:
    # validate the closed role and exclusive overrides
    # snapshot primary/session bindings under the router lock
    # try call override, requested session binding, requested setting
    # try worker session/setting only for reviewer
    # try primary with capability validation
    # construct one immutable result and emit result.as_event() outside the lock
```

Resolve selector candidates against `model_registry.get_all()` with
`parse_model_pattern(..., allow_invalid_thinking_level_fallback=False)`, then call only the
public `is_selectable()` method. Do not access registry internals. A `ScopedModel` trusted
override or session binding still undergoes the required-input check but does not perform a
second auth lookup. Clone returned `ScopedModel` records so callers cannot mutate router
state through the result.

- [ ] **Step 4: Export the contracts and enforce the owner boundary**

Add the new public names to `travis/coding_agent/__init__.py`. Add
`travis/coding_agent/model_roles.py` to `OWNER_GLOBS` in
`tests/architecture/test_facade_boundaries.py`; do not add a forbidden-import exception or
raise the line limit.

- [ ] **Step 5: Run core and architecture tests**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_model_role_router.py \
  tests/architecture/test_facade_boundaries.py \
  tests/test_provider_ownership_architecture.py -q
```

Expected: all pass and `model_roles.py` is at most 750 lines.

- [ ] **Step 6: Commit Task 1**

```bash
git add travis/coding_agent/model_roles.py travis/coding_agent/__init__.py \
  tests/test_model_role_router.py tests/architecture/test_facade_boundaries.py
git commit -m "feat(models): add explainable role router"
```

---

### Task 2: Trust-aware role settings and provenance

**Files:**
- Modify: `travis/coding_agent/settings_manager.py`
- Create: `tests/test_model_role_settings.py`

**Interfaces:**
- Consumes: `CONFIGURABLE_MODEL_ROLES` from Task 1 and existing global/project settings persistence.
- Produces: snake-case model-role getters/setters with global/project provenance.

- [ ] **Step 1: Write failing settings tests**

Add tests for merged precedence, removal, validation, persistence, and trust:

```python
def test_model_role_reads_project_precedence_and_reports_source():
    settings = SettingsManager(
        InMemorySettingsStorage(),
        {"modelRoles": {"worker": "global/worker", "vision": "global/vision"}},
        {"modelRoles": {"worker": "project/worker"}},
        project_trusted=True,
    )

    assert settings.get_model_role("worker") == "project/worker"
    assert settings.get_model_role_source("worker") == "project"
    assert settings.get_model_role("vision") == "global/vision"
    assert settings.get_model_role_source("vision") == "global"
```

```python
def test_clearing_project_role_reveals_global_without_persisting_null():
    storage = InMemorySettingsStorage()
    settings = SettingsManager(
        storage,
        {"modelRoles": {"worker": "global/worker"}},
        {"modelRoles": {"worker": "project/worker"}},
        project_trusted=True,
    )

    settings.set_project_model_role("worker", None)

    assert settings.get_model_role("worker") == "global/worker"
    assert settings.get_model_role_source("worker") == "global"
    assert "null" not in (storage.project_content or "")
```

```python
@pytest.mark.parametrize("role", ["", "primary", "unknown role"])
def test_role_writes_reject_unsupported_names(role):
    settings = SettingsManager.in_memory()
    with pytest.raises(ValueError):
        settings.set_model_role(role, "provider/model")


def test_project_role_write_requires_trust():
    settings = SettingsManager.in_memory()
    with pytest.raises(RuntimeError, match="not trusted"):
        settings.set_project_model_role("worker", "provider/model")
```

Also verify blank selectors fail, malformed hand-edited non-string values are ignored, and
file-backed settings survive reload.

- [ ] **Step 2: Run the tests and verify the red state**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest tests/test_model_role_settings.py -q
```

Expected: failures because model-role accessors do not exist.

- [ ] **Step 3: Implement role settings accessors**

Add narrow helpers that copy and filter mappings:

```python
def get_model_roles(self) -> dict[str, str]:
    raw = self.settings.get("modelRoles")
    if not isinstance(raw, dict):
        return {}
    return {
        role: value.strip()
        for role, value in raw.items()
        if role in CONFIGURABLE_MODEL_ROLES
        and isinstance(value, str)
        and value.strip()
    }

def get_model_role(self, role: str) -> str | None:
    return self.get_model_roles().get(role)

def get_model_role_source(self, role: str) -> SettingsScope | None:
    if _valid_role_value(self.project_settings, role):
        return "project"
    if _valid_role_value(self.global_settings, role):
        return "global"
    return None
```

Use one validated mutation helper for global and project scope. On `None`, remove the role
key and remove an empty `modelRoles` mapping. Project mutation must call the existing trust
guard before changing in-memory data. Do not add camel-case aliases: these are new Python
APIs and every Phase 1B caller uses their snake-case names.

- [ ] **Step 4: Run settings and existing configuration tests**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_model_role_settings.py \
  tests/test_coding_resources_and_services.py \
  tests/test_cli.py -q
```

Expected: all pass with no credential text in assertion output.

- [ ] **Step 5: Commit Task 2**

```bash
git add travis/coding_agent/settings_manager.py tests/test_model_role_settings.py
git commit -m "feat(settings): persist model role mappings"
```

---

### Task 3: Session-scoped ownership and primary synchronization

**Files:**
- Modify: `travis/coding_agent/agent_session.py`
- Modify: `travis/coding_agent/session_models.py`
- Modify: `travis/coding_agent/agent_session_services.py`
- Modify: `travis/app.py`
- Create: `tests/test_model_role_session_integration.py`

**Interfaces:**
- Consumes: `ModelRoleRouter` and settings API from Tasks 1–2.
- Produces: `AgentSession.model_role_router`, `AgentSession.resolve_model_role()`, trusted session bindings, and optional sanitized event-sink wiring.

- [ ] **Step 1: Write failing session ownership tests**

Create authenticated in-memory models with `AuthStorage.set_runtime_api_key()` and these
explicit helpers:

```python
def _model(provider: str, model_id: str) -> Model:
    return Model(
        id=model_id,
        name=model_id,
        api="openai-completions",
        provider=provider,
        base_url="https://example.invalid/v1",
        reasoning=True,
        input=["text", "image"],
        context_window=128_000,
        max_tokens=8_192,
    )


def _registry_for(*models: Model) -> ModelRegistry:
    auth = AuthStorage.in_memory()
    registry = ModelRegistry.in_memory(auth)
    for model in models:
        registry.ensure_model(model)
        auth.set_runtime_api_key(model.provider, "secret-test-key")
    return registry
```

Then assert:

```python
def test_session_primary_route_tracks_model_and_thinking_switches(tmp_path):
    first = _model("roles", "first")
    second = _model("roles", "second")
    registry = _registry_for(first, second)
    session = AgentSession(
        cwd=str(tmp_path),
        model=first,
        model_registry=registry,
        thinking_level="low",
    )

    assert session.resolve_model_role("primary").scoped_model == ScopedModel(first, "low")
    session.set_model(second)
    session.set_thinking_level("high")

    assert session.resolve_model_role("primary").scoped_model == ScopedModel(second, "high")
```

```python
def test_session_binding_survives_primary_switch_without_rewriting_setting(tmp_path):
    primary = _model("roles", "primary")
    second = _model("roles", "second")
    compression = _model("roles", "compression")
    registry = _registry_for(primary, second, compression)
    settings = SettingsManager.in_memory({"modelRoles": {"worker": "roles/worker"}})
    session = AgentSession(
        cwd=str(tmp_path),
        model=primary,
        model_registry=registry,
        settings_manager=settings,
        model_role_bindings={"compression": ScopedModel(compression, "off")},
    )

    session.set_model(second)

    assert session.resolve_model_role("compression").scoped_model.model is compression
    assert settings.get_model_role("compression") is None
```

Also assert each SDK-created/switch-created session gets a distinct router, shares the
injected registry/settings owners, and produces sanitized route events through an injected
sink.

- [ ] **Step 2: Run the tests and verify the red state**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_model_role_session_integration.py -q
```

Expected: constructor/API failures because sessions do not own a role router.

- [ ] **Step 3: Construct the router in `AgentSession`**

Extend `_SessionRuntime.__init__` with:

```python
model_role_bindings: Mapping[ModelRole, ScopedModel] | None = None,
model_role_event_sink: Callable[[dict[str, object]], None] | None = None,
```

After `model_registry` and `settings_manager` are assigned, construct:

```python
self.model_role_router = ModelRoleRouter(
    self.model_registry,
    self.settings_manager,
    ScopedModel(model, thinking_level),
    session_bindings=model_role_bindings,
    event_sink=model_role_event_sink,
)
```

Add a narrow `resolve_model_role()` delegate in `SessionModelController`. After model and
thinking changes have been applied, call
`self.model_role_router.set_primary(self.model, self.thinking_level)`. Do not persist role
bindings in session JSONL and do not emit `model_select` for an auxiliary route.

- [ ] **Step 4: Thread trusted bindings through factories**

Pass `modelRoleBindings`/`model_role_bindings` and
`modelRoleEventSink`/`model_role_event_sink` through
`create_agent_session_from_services()`. Add `CodingApp(model_role_bindings=...)`, store a
defensive copy, and pass it into every `_create_session()` call so new, switched, cloned,
and forked sessions get independent routers over the same binding values.

Wire the app event sink as:

```python
model_role_event_sink=(
    (lambda fields: self._trace("model_role_resolved", fields))
    if self.event_trace is not None
    else None
)
```

Capture the lambda without model credentials or settings objects in emitted fields.

- [ ] **Step 5: Run session, persistence, SDK, and architecture tests**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_model_role_session_integration.py \
  tests/test_coding_persistence_and_compaction.py \
  tests/test_session_parity.py \
  tests/test_coding_resources_and_services.py \
  tests/architecture/test_facade_boundaries.py -q
```

Expected: all pass; the session JSONL schema remains unchanged.

- [ ] **Step 6: Commit Task 3**

```bash
git add travis/coding_agent/agent_session.py travis/coding_agent/session_models.py \
  travis/coding_agent/agent_session_services.py travis/app.py \
  tests/test_model_role_session_integration.py
git commit -m "feat(models): bind role routing to sessions"
```

---

### Task 4: Compression role projection with legacy compatibility

**Files:**
- Modify: `travis/app.py`
- Modify: `tests/test_app_integration.py`
- Modify: `tests/test_compaction_integration.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: session role bindings and `resolve_model_role("compression")`.
- Produces: dedicated routed compression when configured and dynamic-primary fallback when absent.

- [ ] **Step 1: Add failing settings-selected compression test**

Extend the existing auxiliary compaction scenario:

```python
def test_coding_app_routes_compaction_through_settings_role(tmp_path):
    calls: list[tuple[Model, object | None]] = []

    def stream(model, context, options=None):
        del context
        calls.append((model, options))
        events = create_assistant_message_event_stream()
        for event in text_response_events(
            model, "## Historical Task Snapshot\nrole summary"
        ):
            events.push(event)
        return events

    register_api_provider(ApiProvider(api="capturing", stream=stream, stream_simple=stream))
    auth = AuthStorage.in_memory()
    registry = ModelRegistry.in_memory(auth)
    primary = Model(
        id="coding-model", name="Coding", api="capturing",
        provider="main-provider", base_url="https://main.invalid/v1",
    )
    summary = Model(
        id="summary-model", name="Summary", api="capturing",
        provider="summary-provider", base_url="https://summary.invalid/v1",
    )
    for model in (primary, summary):
        registry.ensure_model(model)
        auth.set_runtime_api_key(model.provider, "secret-test-key")
    settings = SettingsManager.in_memory(
        {"modelRoles": {"compression": f"{summary.provider}/{summary.id}:low"}}
    )
    app = CodingApp(
        cwd=str(tmp_path),
        model=primary,
        model_registry=registry,
        settings_manager=settings,
        context_length=2_000,
        enable_tui=False,
    )
    app.session.agent.state.messages = [
        UserMessage(content=f"old context {index} " * 200, timestamp=now_ms())
        for index in range(16)
    ]

    status = app.compaction.compress_manual_with_status(app.messages)

    assert status.compressed is True
    assert calls[-1][0] is summary
    assert calls[-1][1].reasoning == "low"
    assert status.summary_model_requested == f"{summary.provider}/{summary.id}"
```

Keep the existing explicit compression-model test and add an assertion that its dedicated
API key and timeout still reach only that exact model.

- [ ] **Step 2: Run the focused tests and verify the red state**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_app_integration.py::test_coding_app_routes_compaction_through_settings_role \
  tests/test_app_integration.py::test_coding_app_routes_compaction_through_configured_auxiliary_model -q
```

Expected: the settings-selected test fails because `CodingApp` still reads only
`compression_model`.

- [ ] **Step 3: Map the legacy constructor to a session binding**

Build app bindings before session creation:

```python
self._model_role_bindings = dict(model_role_bindings or {})
if compression_model is not None:
    self._model_role_bindings["compression"] = ScopedModel(compression_model, "off")
```

The explicit `compression_model` wins if both mechanisms provide a compression binding;
this preserves the older constructor's behavior. Retain the existing explicit API key,
timeout, and generation parameters as app-owned compatibility data.

- [ ] **Step 4: Resolve and configure the compression consumer**

In `_configure_session_components()`, resolve once for the newly bound session. Treat an
`active_primary` result as no dedicated auxiliary model. For a dedicated result, construct
`_model_summarizer()` with the selected model and its explicit thinking suffix; use the
legacy API key/timeout/generation values only when the selected model is the exact legacy
compression object.

Store the selected dedicated model in `_active_compression_model` and use it for compaction
policy sizing and `summary_model_override`. Keep the existing callable primary summarizer
when the role fell back to primary, so later `/model` changes continue to affect implicit
compression without rewriting settings.

- [ ] **Step 5: Run compaction, CLI, and TUI regression matrices**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_app_integration.py \
  tests/test_compaction_integration.py \
  tests/test_tui_runtime_compaction_and_models.py \
  tests/test_cli.py -q
```

Expected: all pass, including existing dotenv compression assertions and summary fallback
diagnostics.

- [ ] **Step 6: Commit Task 4**

```bash
git add travis/app.py tests/test_app_integration.py tests/test_compaction_integration.py tests/test_cli.py
git commit -m "feat(compaction): resolve auxiliary model role"
```

---

### Task 5: Worker and reviewer routing for internal subagents

**Files:**
- Modify: `travis/coding_agent/session_subagents.py`
- Create: `tests/test_model_role_subagents.py`

**Interfaces:**
- Consumes: `resolve_model_role("worker" | "reviewer", selector_override=...)`.
- Produces: model/thinking selection for internal child sessions while external Codex behavior remains unchanged.

- [ ] **Step 1: Write failing internal child routing tests**

Use direct `_run_internal_subagent()` calls with these explicit helpers:

```python
def _model(provider: str, model_id: str) -> Model:
    return Model(
        id=model_id, name=model_id, api="openai-completions", provider=provider,
        base_url="https://example.invalid/v1", reasoning=True, input=["text"],
        context_window=128_000, max_tokens=8_192,
    )


def _registry_for(*models: Model) -> ModelRegistry:
    auth = AuthStorage.in_memory()
    registry = ModelRegistry.in_memory(auth)
    for model in models:
        registry.ensure_model(model)
        auth.set_runtime_api_key(model.provider, "secret-test-key")
    return registry


def _completed_stream(model, context, options=None):
    del context, options
    events = create_assistant_message_event_stream()
    for event in text_response_events(model, "child complete"):
        events.push(event)
    return events


def _capture_child_factory(parent, captured):
    def factory(**kwargs):
        captured.update(kwargs)
        kwargs.setdefault("model_registry", parent.model_registry)
        kwargs.setdefault("settings_manager", SettingsManager.in_memory())
        return AgentSession(**kwargs)
    return factory


@pytest.fixture
def routed_session(tmp_path):
    primary = _model("roles", "primary")
    worker = _model("roles", "worker")
    review = _model("roles", "review")
    registry = _registry_for(primary, worker, review)
    session = AgentSession(
        cwd=str(tmp_path),
        model=primary,
        model_registry=registry,
        settings_manager=SettingsManager.in_memory(),
        stream_fn=_completed_stream,
    )
    try:
        yield session, {"primary": primary, "worker": worker, "review": review}
    finally:
        session.shutdown()
```

Then assert:

```python
def test_internal_reviewer_uses_reviewer_role_model_and_thinking(routed_session):
    session, models = routed_session
    captured = {}
    session._session_factory = _capture_child_factory(session, captured)
    session.settings_manager.set_model_role("reviewer", "roles/review:high")
    task = SubagentTask(role="reviewer", goal="review", cwd=session.cwd)

    result = session._run_internal_subagent(task)

    assert result.status == "completed"
    assert captured["model"] is models["review"]
    assert captured["thinking_level"] == "high"
```

```python
def test_internal_nonreviewer_uses_worker_and_explicit_reasoning_wins(routed_session):
    session, models = routed_session
    captured = {}
    session._session_factory = _capture_child_factory(session, captured)
    session.settings_manager.set_model_role("worker", "roles/worker:low")
    task = SubagentTask(
        role="explorer",
        goal="inspect",
        cwd=session.cwd,
        reasoning="medium",
    )

    session._run_internal_subagent(task)

    assert captured["model"] is models["worker"]
    assert captured["thinking_level"] == "medium"
```

Also test reviewer fallback to worker, trusted `task.model` selector override, primary
fallback, and that `CodexExecBackend` receives exactly its existing explicit task model.

- [ ] **Step 2: Run the tests and verify the red state**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest tests/test_model_role_subagents.py -q
```

Expected: routed-model assertions fail because internal children inherit `self.model`.

- [ ] **Step 3: Stop pre-filling task reasoning from the parent**

In `_build_subagent_task()`, preserve `None` when trusted options omit reasoning:

```python
"reasoning": options.get("reasoning"),
```

The consumer, not task construction, chooses the role suffix or parent fallback. Keep all
existing reasoning validation in `SubagentTask`.

- [ ] **Step 4: Resolve the internal child route**

At the start of `_run_internal_subagent()`:

```python
routed_role = "reviewer" if task.role == "reviewer" else "worker"
resolution = self.resolve_model_role(
    routed_role,
    selector_override=task.model,
)
if not resolution.available:
    return _failed_model_route_result(task, resolution, started)
binding = resolution.scoped_model
child_thinking = task.reasoning or binding.thinking_level or self.thinking_level
```

Create the child with `binding.model` and `child_thinking`. The bounded failure result may
include role, source, and outcome codes but no selector values beyond model references and
no credentials. Do not add `model` to the model-facing `spawn_subagent` tool schema.

- [ ] **Step 5: Run subagent and extension matrices**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_model_role_subagents.py \
  tests/test_subagents.py \
  tests/test_coding_tools_and_subagents.py \
  tests/test_app_integration.py \
  tests/test_coding_policy_and_extensions.py -q
```

Expected: all pass; spawn limits, cancellation, result packs, and external backend argument
tests remain unchanged.

- [ ] **Step 6: Commit Task 5**

```bash
git add travis/coding_agent/session_subagents.py tests/test_model_role_subagents.py
git commit -m "feat(subagents): route worker and reviewer models"
```

---

### Task 6: Fail-closed call-scoped vision routing

**Files:**
- Modify: `travis/coding_agent/session_turns.py`
- Create: `tests/test_model_role_vision.py`

**Interfaces:**
- Consumes: `resolve_model_role("vision")` and existing injected session stream function.
- Produces: a turn-scoped stream wrapper that upgrades image-bearing provider calls to the selected vision model without mutating session selection.

- [ ] **Step 1: Write failing vision-route tests**

Add these explicit fixtures and a capturing stream:

```python
def _model(provider: str, model_id: str, *, inputs: tuple[str, ...]) -> Model:
    return Model(
        id=model_id, name=model_id, api="openai-completions", provider=provider,
        base_url="https://example.invalid/v1", reasoning=True, input=list(inputs),
        context_window=128_000, max_tokens=8_192,
    )


def _registry_for(*models: Model) -> ModelRegistry:
    auth = AuthStorage.in_memory()
    registry = ModelRegistry.in_memory(auth)
    for model in models:
        registry.ensure_model(model)
        auth.set_runtime_api_key(model.provider, "secret-test-key")
    return registry


def _capturing_stream(calls, response):
    def stream(model, context, options=None):
        calls.append((model, options, context))
        events = create_assistant_message_event_stream()
        for event in text_response_events(model, response):
            events.push(event)
        return events
    return stream


@pytest.fixture
def vision_session(tmp_path):
    primary = _model("roles", "primary", inputs=("text",))
    vision = _model("roles", "vision", inputs=("text", "image"))
    text_only = _model("roles", "text-only", inputs=("text",))
    registry = _registry_for(primary, vision, text_only)
    session = AgentSession(
        cwd=str(tmp_path),
        model=primary,
        model_registry=registry,
        settings_manager=SettingsManager.in_memory(),
    )
    try:
        yield session, {"primary": primary, "vision": vision, "text_only": text_only}
    finally:
        session.shutdown()
```

Then assert:

```python
def test_image_turn_uses_configured_vision_model_without_switching_primary(vision_session):
    session, models = vision_session
    calls = []
    session._stream_fn = _capturing_stream(calls, "vision response")
    session.settings_manager.set_model_role("vision", "roles/vision:high")

    session.prompt("inspect", images=[ImageContent(data="aW1hZ2U=", mime_type="image/png")])

    assert calls[0][0] is models["vision"]
    assert calls[0][1].reasoning == "high"
    assert session.model is models["primary"]
    assert session.settings_manager.get_model_role("vision") == "roles/vision:high"
```

```python
def test_text_only_primary_and_vision_setting_fail_before_provider_request(vision_session):
    session, _models = vision_session
    calls = []
    session._stream_fn = _capturing_stream(calls, "must not run")
    session.settings_manager.set_model_role("vision", "roles/text-only")

    with pytest.raises(RuntimeError, match="image-capable"):
        session.prompt("inspect", images=[ImageContent(data="aW1hZ2U=", mime_type="image/png")])

    assert calls == []
```

Also test an image-capable primary fallback, plain text avoiding vision resolution, multiple
tool continuations staying on the selected vision model, and a queued steering image
upgrading the next provider call.

- [ ] **Step 2: Run the tests and verify the red state**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest tests/test_model_role_vision.py -q
```

Expected: configured vision routing assertions fail because provider calls still receive
the active primary model.

- [ ] **Step 3: Add bounded image-context detection**

Implement a private helper that scans backward from the end of `Context.messages` to the
latest assistant boundary and returns true when a pending `UserMessage` contains an
`ImageContent`. The helper must handle string and list content and must not decode or copy
base64 image data.

```python
def _pending_context_has_images(context: Context) -> bool:
    for message in reversed(context.messages):
        if isinstance(message, AssistantMessage):
            break
        if isinstance(message, UserMessage) and isinstance(message.content, list):
            if any(isinstance(block, ImageContent) for block in message.content):
                return True
    return False
```

- [ ] **Step 4: Wrap one agent run without changing session state**

Create a closure around the active stream function. It resolves and caches the vision
binding on the first image-bearing provider call, then keeps that binding for every tool
continuation in the same run. If a steering image appears after an initial text call, the
closure may upgrade from primary to vision but never downgrade during that run.

```python
def routed_stream(_model, context, options):
    if selected["binding"] is None and _pending_context_has_images(context):
        resolution = self.resolve_model_role("vision")
        if not resolution.available:
            raise RuntimeError("No image-capable model is available for the vision role.")
        selected["binding"] = resolution.scoped_model
    binding = selected["binding"]
    if binding is None:
        return active_stream(_model, context, options)
    reasoning = binding.thinking_level
    routed_options = replace(
        options,
        max_tokens=binding.model.max_tokens or options.max_tokens,
        reasoning=(None if reasoning == "off" else reasoning)
        if reasoning is not None
        else options.reasoning,
    )
    return active_stream(binding.model, context, routed_options)
```

Use this closure only for the current `_run_agent_prompt()` execution. Do not call
`set_model()`, append a model-change JSONL event, emit `model_select`, or alter the footer.

- [ ] **Step 5: Run input, provider, loop, and TUI regression matrices**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_model_role_vision.py \
  tests/test_input_expansion.py \
  tests/test_ai_provider_capabilities.py \
  tests/test_coding_policy_and_extensions.py \
  tests/test_agent_loop.py \
  tests/test_tui_runtime_compaction_and_models.py -q
```

Expected: all pass; agent-loop source has no diff.

- [ ] **Step 6: Commit Task 6**

```bash
git add travis/coding_agent/session_turns.py tests/test_model_role_vision.py
git commit -m "feat(models): route image turns through vision role"
```

---

### Task 7: Documentation, architecture, and focused acceptance

**Files:**
- Modify: `README.md`
- Modify: `tests/test_model_role_router.py`

**Interfaces:**
- Consumes: all Phase 1B contracts and consumer behavior.
- Produces: operator configuration documentation and one complete focused verification matrix.

- [ ] **Step 1: Document role configuration and behavior**

Add this operator-facing section near the existing model configuration documentation:

````markdown
### Model roles

Travis234 can route focused work to already configured models without changing the active
conversation model. Add any of the four optional roles to
`~/.travis234/agent/settings.json`:

```json
{
  "modelRoles": {
    "compression": "provider/summary-model:low",
    "worker": "provider/fast-model:medium",
    "reviewer": "provider/review-model:high",
    "vision": "provider/image-model"
  }
}
```

A trusted project can override the same keys in `.travis234/settings.json`. The suffix
after the final colon is an optional thinking level. Missing roles fall back to the active
primary model; `reviewer` first falls back to `worker`. The `vision` route must select a
model that advertises image input and fails before a provider call when none is available.
`/model` changes the implicit primary fallback only and never rewrites an explicit role.
External Codex subagents keep their own explicit model contract and do not consume these
Travis model-role settings.
````

- [ ] **Step 2: Add a no-secret event-shape regression**

Extend router tests to serialize `result.as_event()` and assert the event contains only
the documented fields and no values from a sentinel API key/header placed on the test
model or settings fixture.

- [ ] **Step 3: Run the complete focused Phase 1B matrix**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_model_role_router.py \
  tests/test_model_role_settings.py \
  tests/test_model_role_session_integration.py \
  tests/test_model_role_subagents.py \
  tests/test_model_role_vision.py \
  tests/test_model_registry.py \
  tests/test_ai_model_resolver.py \
  tests/test_app_integration.py \
  tests/test_compaction_integration.py \
  tests/test_coding_tools_and_subagents.py \
  tests/test_subagents.py \
  tests/test_coding_policy_and_extensions.py \
  tests/test_input_expansion.py \
  tests/architecture/test_facade_boundaries.py \
  tests/test_provider_ownership_architecture.py -q
```

Expected: zero failures.

- [ ] **Step 4: Audit architecture and forbidden paths**

Run:

```bash
git diff --check
git diff --exit-code 6e21970...HEAD -- \
  travis/agent travis/compaction travis/ai/providers \
  packages/travis234-cli packages/travis234-mcp-adapter
wc -l travis/coding_agent/model_roles.py travis/coding_agent/session_models.py \
  travis/coding_agent/session_subagents.py travis/coding_agent/session_turns.py
```

Expected: no forbidden diff and every owner remains within its enforced limit.

- [ ] **Step 5: Commit Task 7**

```bash
git add README.md tests/test_model_role_router.py
git commit -m "docs: explain Travis234 model roles"
```

---

### Task 8: Repository, wheel, live, and container verification

**Files:**
- No production or test changes expected.

**Interfaces:**
- Consumes: the complete Phase 1B branch.
- Produces: fresh repository, distribution, installed-wheel, and release-container evidence.

- [ ] **Step 1: Run the complete Python suite**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest tests -q
```

Expected: zero failures; record exact count and elapsed time.

- [ ] **Step 2: Run npm launcher and package inventory**

```bash
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

Expected: 23 launcher tests pass and the npm inventory remains the declared five files.

- [ ] **Step 3: Build Python distributions outside the worktree**

```bash
phase1b_dist_dir=$(mktemp -d /tmp/travis234-phase1b.XXXXXX)
uv build --clear --out-dir "$phase1b_dist_dir" .
/Users/htooayelwin/orca/travis234/.venv/bin/python -m zipfile -l "$phase1b_dist_dir"/*.whl
shasum -a 256 "$phase1b_dist_dir"/*
```

Expected: wheel and sdist build; the wheel contains `travis/coding_agent/model_roles.py`
and excludes `.env`, `oh-my-pi`, `.worktrees`, and design-plan source documents.

- [ ] **Step 4: Run installed-wheel offline role smoke**

Create a temporary virtual environment and isolated home, install the exact wheel, and run
this offline smoke without loading the repository `.env`:

```bash
phase1b_smoke_dir=$(mktemp -d /tmp/travis234-phase1b-smoke.XXXXXX)
python3 -m venv "$phase1b_smoke_dir/venv"
phase1b_wheel=$(find "$phase1b_dist_dir" -maxdepth 1 -name '*.whl' -print -quit)
HOME="$phase1b_smoke_dir/home" "$phase1b_smoke_dir/venv/bin/pip" install "$phase1b_wheel"
HOME="$phase1b_smoke_dir/home" "$phase1b_smoke_dir/venv/bin/python" - <<'PY'
from travis.ai.model_resolver import ScopedModel
from travis.ai.types import Model
from travis.coding_agent.auth_storage import AuthStorage
from travis.coding_agent.model_registry import ModelRegistry
from travis.coding_agent.model_roles import ModelRoleRouter
from travis.coding_agent.settings_manager import SettingsManager


def model(model_id, inputs):
    return Model(
        id=model_id, name=model_id, api="openai-completions", provider="smoke",
        base_url="https://example.invalid/v1", reasoning=True, input=inputs,
        context_window=128_000, max_tokens=8_192,
    )


primary = model("primary", ["text"])
second = model("second", ["text"])
worker = model("worker", ["text"])
auth = AuthStorage.in_memory()
registry = ModelRegistry.in_memory(auth)
for candidate in (primary, second, worker):
    registry.ensure_model(candidate)
auth.set_runtime_api_key("smoke", "offline-smoke-key")
settings = SettingsManager.in_memory({"modelRoles": {"worker": "smoke/worker:low"}})
router = ModelRoleRouter(registry, settings, ScopedModel(primary, "medium"))
assert router.resolve("worker").scoped_model == ScopedModel(worker, "low")
assert router.resolve("reviewer").selected_role == "worker"
assert not router.resolve("vision").available
router.set_primary(second, "high")
assert router.resolve("compression").scoped_model == ScopedModel(second, "high")
assert router.resolve("worker").scoped_model == ScopedModel(worker, "low")
PY
```

- [ ] **Step 5: Run acceptance mapping**

```bash
/Users/htooayelwin/orca/travis234/.venv/bin/python scripts/verify_acceptance.py --parity-json
```

Expected: exit zero with no invalid mapped contracts.

- [ ] **Step 6: Build and smoke the release container without credentials**

```bash
docker build --no-cache -f Dockerfile.release -t travis234:model-role-router .
/Users/htooayelwin/orca/travis234/.venv/bin/python evals/container_smoke.py \
  --image travis234:model-role-router
```

Expected: build and smoke exit zero as the unprivileged `travis` user. Supply no dotenv or
host credential file.

- [ ] **Step 7: Perform final self-review and Git-scope verification**

Review every commit and the aggregate diff from `6e21970`. Check requirements against the
focused spec, inspect exception paths and trace fields, and run:

```bash
git diff --check
git diff --exit-code 6e21970...HEAD -- \
  travis/agent travis/compaction travis/ai/providers \
  packages/travis234-cli packages/travis234-mcp-adapter
test -z "$(git ls-files oh-my-pi)"
git status --short --branch
git log --oneline --decorate 6e21970..HEAD
```

Expected: clean worktree, no forbidden or research-tree changes, and a reviewable Phase 1B
commit series. Preserve the branch and worktree. Do not merge, push, publish, tag, or begin
Phase 1C without a separate integration decision.

---

## Execution mode

The user selected inline execution and explicitly requested autonomous self-review without
clarification pauses. Execute this plan with `superpowers:executing-plans`. Repository
guidance prohibits subagents unless explicitly requested, so all checkpoint reviews are
performed inline against the committed diff and fresh test evidence.
