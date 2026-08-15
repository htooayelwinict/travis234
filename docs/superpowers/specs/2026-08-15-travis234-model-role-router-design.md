# Travis234 Model Role Router Design

## Status and scope

This specification defines Phase 1B of the approved Travis234 contract-parity
architecture. It depends on the Phase 1A capability-registry branch and covers one
session-scoped model-routing boundary plus its first consumers. It does not authorize
Phase 1C or later work.

The product remains `Travis234`, the CLI remains `travis234`, and the import package
remains `travis`. Provider authentication, catalogs, transports, and streaming remain
owned by the existing AI and `ModelRegistry` layers. The generic agent loop is not
modified.

## Problem

Travis currently has several model-selection paths:

- the active conversation model lives in `AgentSession`;
- an optional compression model is injected into `CodingApp`;
- internal subagents inherit the parent model;
- image turns use the active model even when a different image-capable model would be
  more appropriate.

Adding workers, reviewers, and richer vision workflows directly to those consumers would
duplicate selection, authentication checks, capability checks, and fallback behavior.
The result would be difficult to explain and unsafe to evolve.

Phase 1B introduces one policy boundary that answers: “Which already-registered model
should perform this purpose?” It does not become another model catalog and never stores
credentials.

## Considered approaches

### Session-owned router — selected

Create a focused `ModelRoleRouter` beside the coding-agent model/session owners. It reads
model-role selectors from `SettingsManager`, resolves them through `ModelRegistry`, and
returns an immutable selection with a complete fallback trace.

This gives each session coherent policy without changing provider ownership. Consumers
depend on one small contract and can migrate independently.

### Add role policy to `ModelRegistry` — rejected

This would make lookup convenient, but it would mix provider/catalog/authentication truth
with session-specific purpose and fallback policy. A registry refresh and a session model
switch have different lifecycles; combining them would make both harder to reason about.

### Route independently in every consumer — rejected

This has the smallest initial abstraction, but compaction, subagents, and vision would
quickly acquire different selector grammars, fallback rules, and error messages. It also
makes it impossible to explain a route consistently.

## Roles

The initial closed role set is:

- `primary`: the active conversation model and thinking level;
- `compression`: context summarization;
- `worker`: ordinary bounded internal subagent work;
- `reviewer`: an internal subagent explicitly spawned with role `reviewer`;
- `vision`: a turn containing image input.

`primary` is implicit and always follows the active session model. It is not persisted in
`modelRoles`; attempting to set it through the model-role settings API is rejected. This
prevents an old setting from silently overriding an explicit `/model` switch.

Only `reviewer` has a role-to-role fallback: it tries `worker` before `primary`. The other
optional roles fall back directly to `primary`. `vision` accepts the primary fallback only
when the primary model advertises image input.

## Settings contract

Existing global and trusted-project settings ownership remains authoritative. Optional
role selectors live in the merged settings object:

```json
{
  "modelRoles": {
    "compression": "openrouter/openai/gpt-5.6-luna-pro",
    "worker": "openrouter/xiaomi/mimo-v2.5-pro:medium",
    "reviewer": "openai-codex/gpt-5.6:high",
    "vision": "openrouter/google/gemini-3.1-pro-preview"
  }
}
```

The value is a single existing Travis model selector with an optional thinking-level
suffix. Resolution reuses the established model-pattern parser, including the rule that a
literal model ID ending in a colon suffix wins before that suffix is interpreted as a
thinking level.

`SettingsManager` exposes:

```python
get_model_roles() -> dict[str, str]
get_model_role(role: str) -> str | None
get_model_role_source(role: str) -> Literal["global", "project"] | None
set_model_role(role: str, selector: str | None) -> None
set_project_model_role(role: str, selector: str | None) -> None
```

Project writes retain the existing trust requirement. `None` removes a role from the
chosen scope instead of persisting an ambiguous null override. Invalid or blank role names
and selectors are rejected on write. Reads of malformed hand-edited values ignore them and
allow the same traceable fallback used for a missing mapping; malformed values never reach
the model selector parser.

Settings changes affect subsequent resolutions. Long-lived app components resolve their
route when a session is bound; internal subagents and image turns resolve immediately
before use. Phase 1B does not introduce a new filesystem watcher.

## Router contract

The focused owner is `travis/coding_agent/model_roles.py`. It defines concepts equivalent
to:

```python
ModelRole = Literal["primary", "compression", "worker", "reviewer", "vision"]

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
    def available(self) -> bool: ...

class ModelRoleRouter:
    def __init__(
        self,
        model_registry: ModelRegistry,
        settings_manager: object,
        primary: ScopedModel,
        *,
        session_bindings: Mapping[ModelRole, ScopedModel] | None = None,
        event_sink: Callable[[dict[str, object]], None] | None = None,
    ) -> None: ...

    def set_primary(self, model: Model, thinking_level: str | None) -> None: ...

    def resolve(
        self,
        role: ModelRole,
        *,
        override: ScopedModel | None = None,
        selector_override: str | None = None,
        required_inputs: Iterable[str] | None = None,
    ) -> ModelRoleResolution: ...
```

Supplying both override forms is an error. `ScopedModel` overrides are trusted
application-owned bindings; selector overrides still resolve through the registered,
selectable model catalog. The router uses a lock only for its primary and session-binding
snapshot. It does not hold a lock while checking settings or provider availability.

Every resolution returns all attempted steps. Trace fields contain only role names, model
selectors/references, result codes, and bounded explanations. They never contain API keys,
headers, environment values, or provider responses. When an application supplies an
`event_sink`, the router emits one `model_role_resolved`-shaped dictionary after the
result is complete and outside its lock. `CodingApp` connects this to its existing
evaluation trace; direct SDK sessions may omit the sink without changing resolution.

## Resolution algorithm

For a requested role, the router performs these steps in order:

1. Validate the role and required input capabilities. `vision` requires `image` by
   default; other roles require `text`.
2. Try a trusted call-scoped `ScopedModel` or selector override.
3. Try the session binding for the requested role.
4. Try the merged settings selector for the requested role and record whether it came
   from trusted project or global settings.
5. For `reviewer`, repeat session/settings lookup for `worker`.
6. Try the active primary model.
7. Return a typed unavailable result if no candidate is compatible.

A configured selector is matched against the complete registered catalog first. If it is
not found, the trace says `not_found`. If it exists but `ModelRegistry.is_selectable()` is
false, the trace says `unavailable`. If it lacks a required input kind, the trace says
`incompatible`. Each of these outcomes continues to the next safe fallback.

The active primary model is already running and is therefore not re-authenticated during
fallback. Capability checks still apply. Changing the session model or thinking level
immediately replaces the router's primary snapshot but never rewrites an explicit role
mapping.

## Consumer integration

### Session ownership

`AgentSession` constructs one router unless an application supplies one. It exposes the
router and a narrow `resolve_model_role()` method. `set_model()` and
`set_thinking_level()` update the implicit primary route after changing session state.
Session restore, switch, clone, and fork each receive their own router initialized from
the restored active model and the shared settings/registry owners.

### Compression

`CodingApp` maps the existing `compression_model` constructor argument to a session
binding with thinking `off`. Its existing dedicated API key, timeout, and generation
parameters remain paired with that exact binding, preserving dotenv behavior.

When a session is bound, the app resolves `compression`:

- a configured or injected auxiliary model creates the existing dedicated summarizer;
- an implicit-primary result keeps the existing dynamic primary summarizer;
- an unusable optional mapping falls back to primary without breaking the turn.

Settings-selected auxiliary models obtain authentication through `ModelRegistry`.
Injected legacy compression bindings may continue using their existing explicitly passed
API key. No credential is stored in the router or trace.

### Internal worker and reviewer subagents

Immediately before creating an internal child session, Travis resolves `reviewer` when
the normalized task role is exactly `reviewer`; every other internal child resolves
`worker`. A trusted extension-provided task model becomes the call-scoped selector
override. Model-facing tools still cannot provide this field.

The child uses the selected model. An explicit task reasoning value wins, followed by the
role selector's thinking suffix, followed by the parent thinking level. External `codex`
subagents retain their existing explicit model contract because Codex model names and
credentials are not owned by Travis `ModelRegistry`.

### Image turns

After user references are expanded, a prompt containing images resolves `vision`. The
session wraps only that agent run's provider stream so every provider continuation in the
same run uses the resolved vision model and its effective thinking level. The active
session model, persisted model selection, and `/model` UI do not change.

If neither a configured vision model nor the active primary model accepts images, the
turn fails before a provider request with a bounded error. This is safer than silently
dropping an image or sending it to a text-only transport. Text-only turns do not resolve
or invoke the vision route.

## Error handling and rollback

- Unknown roles and mutually exclusive override arguments raise `ValueError` at the API
  boundary.
- Missing optional mappings are normal fallback steps, not warnings.
- Invalid, unauthenticated, or capability-incompatible configured models are explainable
  fallback steps and do not break primary text turns.
- An unavailable vision route fails before provider execution.
- Provider errors after a model is selected retain existing retry and error behavior.
- Removing the three consumer integrations restores current direct behavior while the
  standalone router and settings remain harmless.

## Security and trust

- The router stores `Model` objects and selector strings, never credentials.
- Authentication checks delegate to `ModelRegistry`; provider auth and transport
  selection remain unchanged.
- Project model-role settings are read only after existing project trust resolution and
  can be written only through the existing trusted-project settings guard.
- Model-facing subagent calls cannot inject an arbitrary model selector.
- Traces are safe for tests, diagnostics, and evaluation logs because they contain no raw
  request arguments or secret-bearing configuration.

## Architecture boundaries and blast radius

Expected new owner:

- `travis/coding_agent/model_roles.py`

Expected focused modifications:

- `travis/coding_agent/settings_manager.py` for role settings access;
- `travis/coding_agent/agent_session.py` and `session_models.py` for session ownership;
- `travis/coding_agent/session_subagents.py` for internal worker/reviewer consumption;
- `travis/coding_agent/session_turns.py` for call-scoped vision consumption;
- `travis/app.py` for compression projection;
- `travis/coding_agent/__init__.py` for public Python exports;
- architecture tests, focused model-role tests, integration tests, and documentation.

The following remain outside the intended diff: `travis/agent`, provider transports,
provider authentication, MCP packages, npm launcher behavior, session JSONL schema, and
the capability-registry implementation.

## Verification

Contract tests cover:

- explicit override, session binding, project/global mapping, fallback, and unavailable
  traces;
- sanitized evaluation events matching the returned source and fallback trace;
- reviewer-to-worker fallback ordering;
- active model and thinking switches updating only the implicit primary route;
- literal selector and thinking-suffix resolution;
- unavailable configured auth degrading to primary;
- vision incompatibility failing closed before a provider call;
- legacy compression dotenv/injected-model behavior remaining unchanged;
- settings-selected compression using the routed model;
- worker and reviewer internal children receiving the routed model and thinking level;
- session switch/clone/fork producing correctly initialized session-scoped routers;
- architecture limits and forbidden imports.

Completion requires the focused matrix, the complete Python suite, npm launcher tests,
Python and npm package builds/inventories, acceptance mapping, an installed-wheel routing
smoke, the relevant release-container smoke, and a final forbidden-path audit. No live
provider credentials are required for automated verification.

## Tradeoffs

- One router adds indirection to model selection, but the immutable result and trace make
  that indirection observable.
- Exact role configuration creates more settings surface, but the initial closed role set
  avoids importing Oh My Pi's larger taxonomy.
- Call-scoped vision routing means an assistant message may come from a model other than
  the active footer model; preserving the active selection avoids surprising persistent
  switches. The response already records its actual provider/model.
- External Codex subagents do not inherit Travis role mappings. Crossing that ownership
  boundary would conflate unrelated catalogs and credentials.
- Settings changes are not watched continuously. Resolving at session bind or immediately
  before short-lived work provides deterministic behavior without a new background
  lifecycle.

## Success criteria

Phase 1B succeeds when every initial role produces an explainable immutable resolution,
current direct behavior remains the fallback, auxiliary compression and internal child
work can use independent authenticated Travis models, image turns cannot reach a
text-only model, active model changes update only primary, and all repository boundaries
and release gates remain green.
