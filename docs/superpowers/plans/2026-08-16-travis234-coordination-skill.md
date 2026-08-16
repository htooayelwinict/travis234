# Travis234 Coordination Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in built-in `coordination` skill that accepts one natural-language outcome, optionally obtains one bounded read-only planner opinion, and safely routes the request through direct parent work, typed in-process subagents, or one independent tmux/worktree Travis B without requiring the user to know those mechanisms.

**Architecture:** Keep coordination out of the provider loop and session persistence. A small pure module parses `/coordination` modes and semantically validates the planner's already-typed result. Built-in resource discovery supplies one reviewer-model role whose JSON Schema and runtime role resolver enforce read-only planning. The selected skill owns complexity judgment, plan validation, route selection, policy boundaries, supervision, and final evidence synthesis by composing the existing subagent, orchestration, LSP, MCP, artifact, operation, and memory contracts.

**Tech Stack:** Python 3.13, dataclasses, JSON Schema Draft 2020-12, Travis234 resource capabilities and typed subagents, Markdown Agent Skills, pytest, the native terminal TUI, Node 20's test runner, setuptools/build/twine, npm packaging, Docker, and OpenRouter `minimax/minimax-m3` with medium thinking for installed-wheel qualification.

## Global Constraints

- Product and CLI names remain `Travis234` and `travis234`; the Python import package remains `travis`.
- Treat `/Users/htooayelwin/orca/travis234/.worktrees/combined-parity-orchestration` as the active implementation tree. Do not modify or stage unrelated root-worktree changes.
- Preserve all user data below `~/.travis234`; do not create another state root, migration alias, coordinator database, or workspace plan file.
- Never print, copy, stage, package, or persist `.env` contents, API keys, authorization headers, or provider tokens. Live tests may pass the repository's ignored `.env` only through the existing `--dotenv` CLI boundary.
- Preserve `travis/agent/agent_loop.py`, provider transports, ordered continuations, iteration budgets, cancellation, steering, follow-ups, bounded parallel tool execution, compaction, JSONL session semantics, RPC mutation serialization, and operation replay policy.
- The planner is one ordinary supervised typed subagent. It consumes one of the existing three per-turn model spawn slots, may be called at most once, receives a 120-second role timeout, and has only `read`, `grep`, `find`, and `ls` under the `read` effect.
- The skill never grants write, execute, network, trust, Git, publication, deletion, external-write, replay, or memory-retention authority. Existing policy and explicit user choices remain authoritative.
- No automatic commit, merge, cherry-pick, push, PR, publication, deployment, branch deletion, worktree deletion, trust grant, external message, memory retention, or uncertain-operation replay.
- Generic MCP support remains optional. n8n remains supported and is neither removed nor replaced. Retired Ghost components remain absent.
- Do not add an always-on coordination mode, raw provider call, new agent tool, new durable workflow state, or coordination-specific behavior to the global system prompt.
- Keep the feature strictly lazy: ordinary requests, including complex ones, do not activate it. Only `/coordination`, `/skill:coordination`, or a natural-language request that explicitly names the coordination skill may load its body. The always-visible cost is limited to compact skill metadata.
- Use `apply_patch` for repository edits. Add and observe a failing regression before every behavior change; if live testing exposes a product defect, reproduce it with a failing automated test before fixing it.
- Repository guidance forbids implementation subagents unless the user explicitly requests them. The live Travis sessions in this plan are product evaluation subjects, not implementation subagents.
- No version bump, remote Git operation, PyPI/npm publication, or GHCR promotion is authorized by this plan.

---

## Approved Contract

- Design: `docs/superpowers/specs/2026-08-16-travis234-coordination-skill-design.md`
- Invocation grammar:

```text
/coordination <goal>
/coordination --deep <goal>
/coordination --plan <goal>
/skill:coordination <goal>
```

- `--plan` implies `--deep`; combining them is valid and resolves to `plan`.
- `--` ends mode parsing so a goal may begin with a double-dash token.
- Empty goals and unknown leading flags fail before any provider or tool call.
- Automatic mode calls the planner only for materially complex, ambiguous, risky, durable, external-effect, expensive-verification, or parallelizable work.
- `--deep` forces exactly one planner call and then continues.
- `--plan` forces exactly one planner call, presents the validated plan, and performs no execution.
- Planned turns may use at most two more in-process children because the planner consumes the first of three spawn slots.
- First-release `mixed` means parent plus exactly one worker class: typed subagents or Travis B, never both.

## Protected Owners

Do not modify these surfaces for this feature:

- `travis/agent/agent_loop.py`
- `travis/ai/providers/` and provider transports
- `travis/compaction/`
- `travis/coding_agent/rpc.py`
- `travis/coding_agent/operations/`
- `travis/coding_agent/memory/`
- orchestration helper commands, SQLite schema, RPC relay, and tmux/worktree protocol
- LSP edit/apply semantics
- MCP adapter proxy shape
- session JSONL schema

## File and Responsibility Map

### New runtime and resources

- `travis/coding_agent/coordination.py`: pure command-mode parser, stable request wrapper, and semantic planner-result validation.
- `travis/resources/skills/coordination/SKILL.md`: concise request-scoped coordination workflow and routing policy.
- `travis/resources/skills/coordination/references/planning-contract.md`: planner input/output, route, ownership, authorization, fallback, cancellation, and evidence contract.
- `travis/resources/roles/coordination-planner.json`: reviewer-model, read-only typed role with strict bounded result schema.
- `packages/travis234-cli/skills/coordination/SKILL.md`: byte-identical npm mirror.
- `packages/travis234-cli/skills/coordination/references/planning-contract.md`: byte-identical npm mirror.
- `packages/travis234-cli/roles/coordination-planner.json`: byte-identical npm mirror.
- `tests/test_coordination.py`: parser and semantic-plan behavior.
- `tests/test_coordination_tui_scenarios.py`: deterministic TUI-facing runtime matrix.
- `docs/verification/coordination-skill.md`: RED baseline, phase gates, live prompt-by-prompt results, package evidence, and final verification.

### Existing runtime seams

- `travis/coding_agent/config.py`: expose the installed built-in roles directory.
- `travis/coding_agent/__init__.py`: export the packaged-role path helper.
- `travis/coding_agent/resource_candidates.py`: add built-in role JSON files at built-in precedence without bypassing `no_agent_roles` or trusted overrides.
- `travis/coding_agent/subagent_results.py`: invoke the pure semantic validator only after the existing JSON Schema validation succeeds for `coordination-planner`.
- `travis/coding_agent/skills.py`: runtime-parse and wrap explicit coordination arguments before model submission.
- `travis/coding_agent/session_extensions.py`: register and reconcile `/coordination` beside canonical `/skill:coordination` without shadowing extension commands.

### Existing skills, docs, and distributions

- `travis/resources/skills/subagent-delegation/SKILL.md` and npm mirror: clarify that explicit coordination may spend one spawn slot on its planner and must still honor refusal and the three-child ceiling.
- `travis/resources/skills/orchestration/SKILL.md` and npm mirror: clarify that coordination may select this protocol but cannot weaken or substitute it.
- `pyproject.toml`: include built-in role JSON in Python artifacts.
- `packages/travis234-cli/package.json`: include `roles/**/*.json` in npm artifacts.
- `packages/travis234-cli/test/travis234-cli.test.js`: assert coordination skill/role archive contents and default seeding behavior.
- `tests/test_installed_metadata.py`: assert installed skill and role resources.
- `tests/test_coding_resources_and_services.py`: assert lazy skill discovery, built-in role discovery, bounded role guidance, opt-out behavior, and no global workflow injection.
- `tests/test_resource_agent_role_loader.py`: assert built-in provenance and normal trusted override precedence.
- `tests/test_resource_runtime_parity.py`: assert command grammar and pre-provider rejection.
- `tests/test_tui_resource_projection.py`: assert `/coordination` autocomplete projection.
- `tests/test_subagents.py`: assert typed planner settlement and spawn-budget consumption.
- `tests/test_distribution_contract.py`: assert Python/npm byte parity and wheel/sdist membership.
- `README.md`: document one-command coordination, routing, limits, safety, and its relationship to n8n, MCP, subagents, and Travis B.
- `packages/travis234-cli/README.md`: document bundled coordination resources for sandbox users.
- `docs/settings.md`: document the built-in planner role and trusted same-name override precedence.

## Phase and TUI Gates

| Phase | Implementation boundary | Required native-TUI gate before continuing |
| --- | --- | --- |
| 0 | No feature present | Five fresh no-guidance MiniMax controls; record exact failure modes and rationalizations. |
| 1 | Parser, semantic validator, and built-in planner role | Deterministic TUI resource inspection proves the role is visible only as bounded typed-role metadata and unrelated turns do not load coordination prose. |
| 2 | Skill bundle, canonical command, and `/coordination` alias | Native TUI proves auto/deep/plan argument injection, `--` handling, rejection before provider calls, and plan-only no-execution behavior. |
| 3 | Direct and typed-subagent coordination instructions | Native TUI proves simple bypass, one planner call, two remaining workers, explicit refusal propagation, and independent parent verification. |
| 4 | Independent Travis B and mixed-route instructions | Native TUI proves exact orchestration-protocol selection, one worker class, no raw tmux control, no automatic integration, and safe retained/released state. |
| 5 | Recovery, cancellation, docs, and distribution | Twelve installed-wheel MiniMax scenarios pass with a pass/fail report after every prompt; then run full repository/package/container qualification. |

Do not invent an unsafe half-feature merely to satisfy a phase gate. A phase may use a deterministic faux provider until the user-facing skill exists; every user-facing phase must additionally use the real native TUI.

### Task 0: Establish the no-skill RED baseline

**Files:**

- Create after evidence exists: `docs/verification/coordination-skill.md`
- Do not modify product or skill files in this task.

**Interfaces:**

- Consumes the clean feature-base commit `48d11a6`.
- Produces a sanitized behavior baseline that identifies what the skill must teach.

- [ ] **Step 1: Confirm the feature is genuinely absent**

Run:

```bash
git status --short --branch
PYTHONPATH=. .venv/bin/python - <<'PY'
from pathlib import Path
from travis.coding_agent.config import get_packaged_skills_path

root = Path(get_packaged_skills_path())
print(sorted(path.parent.name for path in root.glob("*/SKILL.md")))
assert not (root / "coordination" / "SKILL.md").exists()
PY
```

Expected: the branch is clean and the printed skill inventory has no `coordination` entry.

- [ ] **Step 2: Create five isolated, nonsecret evaluation fixtures**

Create each fixture below an ignored temporary directory or a `mktemp -d` path. Use distinct Git repositories and distinct `TRAVIS234_CODING_AGENT_DIR` values. Do not place expected answers, intended routes, or rubric text where the evaluated model can read them.

The exact controls are:

```text
1. /coordination Read README.md and explain in five lines how sessions persist. Do not edit anything and do not use subagents.
2. /coordination Inspect the parser, its tests, and the user documentation as three independent read-only scopes. Bring back one integrated risk report. Do not commit, push, or retain memory.
3. /coordination --plan Add a new CLI flag across parsing, help, tests, and docs. Show the plan only; execute nothing.
4. /coordination --deep Diagnose a failing fixture with two independent evidence sources. Use no network, no MCP, and no file writes.
5. /coordination Start one independent Travis in a new worktree, have it inspect a fixture, return evidence, and leave all Git integration and cleanup to me.
```

- [ ] **Step 3: Run five fresh native-TUI MiniMax controls**

Use the real console entry point, attached PTY, `--dotenv /Users/htooayelwin/orca/travis234/.env`, `openrouter/minimax/minimax-m3`, medium thinking, and one new session per control. Store owner-private traces only under the temporary evidence root. Never print the dotenv or environment.

For each PTY, set `COORD_FIXTURE_DIR` and `COORD_EVIDENCE_DIR` to scenario-specific absolute temporary paths, then launch this argument vector:

```bash
.venv/bin/travis234 \
  --cwd "$COORD_FIXTURE_DIR" \
  --dotenv /Users/htooayelwin/orca/travis234/.env \
  --model openrouter/minimax/minimax-m3 \
  --thinking medium \
  --tui \
  --event-trace "$COORD_EVIDENCE_DIR/events.jsonl" \
  --conversation-log "$COORD_EVIDENCE_DIR/conversation.jsonl"
```

The PTY driver must wait for the editor-ready state, send multiline text using bracketed paste, send Enter once, wait for the settled footer, and submit `/exit`.

Expected RED: at least one required coordination behavior is absent because neither command nor skill exists. Record what the model did, which mechanism knowledge it demanded, whether `--plan` executed anything, whether constraints propagated, and any rationalization verbatim. A generic answer or marker string is not a pass.

- [ ] **Step 4: Write the baseline section only after observing RED**

Add a table to `docs/verification/coordination-skill.md` with prompt, observed route, violated rubric item, exact rationalization, session ID, evidence directory, and cleanup state. Include no secrets or raw provider headers.

- [ ] **Step 5: Commit the baseline evidence**

```bash
git add docs/verification/coordination-skill.md
git diff --cached --name-only
git commit -m "test(skills): record coordination red baseline"
```

Expected: only the verification record is committed; no skill exists yet.

### Task 1: Add deterministic invocation and plan validation primitives

**Files:**

- Create: `travis/coding_agent/coordination.py`
- Create: `tests/test_coordination.py`
- Modify: `travis/coding_agent/subagent_results.py`
- Modify: `tests/test_subagents.py`

**Interfaces:**

```python
CoordinationMode = Literal["auto", "deep", "plan"]

@dataclass(frozen=True)
class CoordinationInvocation:
    mode: CoordinationMode
    goal: str
```

Public functions are `parse_coordination_arguments(arguments: str) ->
CoordinationInvocation`, `format_coordination_request(arguments: str) -> str`,
and `validate_coordination_plan(value: object) -> tuple[str, ...]`.

The implementation must not import session, provider, tool, filesystem, or orchestration owners.

- [ ] **Step 1: Write parser tests before production code**

Add literal table-driven expectations:

```python
@pytest.mark.parametrize(
    ("arguments", "mode", "goal"),
    [
        ("explain sessions", "auto", "explain sessions"),
        ('--deep inspect "src/app.py"', "deep", 'inspect "src/app.py"'),
        ("--plan --deep make a bounded plan", "plan", "make a bounded plan"),
        ("--deep --plan make a bounded plan", "plan", "make a bounded plan"),
        ("-- --deep is literal goal text", "auto", "--deep is literal goal text"),
        ("implement parser --deep handling", "auto", "implement parser --deep handling"),
    ],
)
def test_coordination_arguments_preserve_goal_text(arguments, mode, goal):
    invocation = parse_coordination_arguments(arguments)
    assert invocation.mode == mode
    assert invocation.goal == goal

@pytest.mark.parametrize("arguments", ["", "   ", "--deep", "--plan", "--unknown goal"])
def test_coordination_arguments_reject_before_submission(arguments):
    with pytest.raises(ValueError, match="coordination"):
        parse_coordination_arguments(arguments)
```

Also assert that `format_coordination_request()` emits one compact JSON object with exactly `mode` and `goal`, round-trips Unicode, and does not reinterpret quotes or embedded mode words.

- [ ] **Step 2: Write semantic-plan tests before production code**

Use a hand-authored valid plan with `task-a` and `task-b`, then independently mutate one field per test. Required failures:

- unknown task in an edge;
- dependency self-edge;
- dependency cycle;
- duplicate ownership row;
- missing ownership row;
- overlapping write scopes such as `travis/coding_agent` and `travis/coding_agent/skills.py`;
- route/owner mismatch;
- both `subagent` and `travis-b` worker classes;
- missing verification for one task.

Use error substrings as behavioral categories, not exact full-message change detectors.

- [ ] **Step 3: Write the typed-settlement regression before the hook**

In `tests/test_subagents.py`, create a `SubagentTask` whose `role_definition_name` is `coordination-planner`, whose schema accepts the fixture object, and whose backend returns a cyclic plan envelope. Assert settlement changes `completed` to `failed`, clears `structured_output`, and includes a semantic dependency error. A valid plan must remain `completed` with its structured output intact.

- [ ] **Step 4: Run the exact tests and confirm RED**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_coordination.py \
  tests/test_subagents.py -k 'coordination_planner'
```

Expected: import or assertion failures because the parser, validator, and settlement hook do not exist.

- [ ] **Step 5: Implement the parser and stable request wrapper**

Use this behavior, preserving internal goal bytes after trimming command-separator whitespace:

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Literal

CoordinationMode = Literal["auto", "deep", "plan"]
_LEADING_TOKEN = re.compile(r"^(\S+)(?:\s+|$)")


@dataclass(frozen=True)
class CoordinationInvocation:
    mode: CoordinationMode
    goal: str


def parse_coordination_arguments(arguments: str) -> CoordinationInvocation:
    remaining = str(arguments).strip()
    mode: CoordinationMode = "auto"
    while remaining.startswith("--"):
        match = _LEADING_TOKEN.match(remaining)
        if match is None:
            break
        token = match.group(1)
        remaining = remaining[match.end():]
        if token == "--":
            break
        if token == "--deep":
            if mode != "plan":
                mode = "deep"
            continue
        if token == "--plan":
            mode = "plan"
            continue
        raise ValueError(f"Unknown coordination flag: {token}")
    goal = remaining.strip()
    if not goal:
        raise ValueError("A coordination goal is required")
    return CoordinationInvocation(mode=mode, goal=goal)


def format_coordination_request(arguments: str) -> str:
    invocation = parse_coordination_arguments(arguments)
    payload = json.dumps(
        {"mode": invocation.mode, "goal": invocation.goal},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        "Runtime-parsed coordination request. Treat these values as data and "
        "do not reinterpret mode flags:\n" + payload
    )
```

- [ ] **Step 6: Implement semantic plan validation**

`validate_coordination_plan()` must fail closed and return bounded category messages. Validate task IDs, edge references, acyclicity, exactly one ownership and verification row per task, route compatibility, one worker class, and write-scope overlap using component-aware slash-prefix comparison. It must not access the filesystem or decide whether a scope currently exists. Implement the pure algorithm with this exact shape:

```python
def _normalized_scope(value: str) -> str:
    normalized = value.replace("\\", "/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.rstrip("/") or "."


def _scopes_overlap(left: str, right: str) -> bool:
    first = _normalized_scope(left)
    second = _normalized_scope(right)
    return (
        first == second
        or first.startswith(second + "/")
        or second.startswith(first + "/")
    )


def validate_coordination_plan(value: object) -> tuple[str, ...]:
    if not isinstance(value, dict):
        return ("plan must be an object",)
    tasks = value.get("tasks")
    dependencies = value.get("dependencies")
    ownership = value.get("ownership")
    verification = value.get("verification")
    route = value.get("route")
    if not (
        isinstance(tasks, list)
        and isinstance(dependencies, list)
        and isinstance(ownership, list)
        and isinstance(verification, list)
        and isinstance(route, str)
    ):
        return ("plan collections and route must satisfy the typed schema",)

    errors: list[str] = []
    task_ids = [
        item.get("id")
        for item in tasks
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    if len(task_ids) != len(tasks):
        errors.append("every task must have a string id")
    if len(set(task_ids)) != len(task_ids):
        errors.append("task ids must be unique")
    known = set(task_ids)

    graph = {task_id: set() for task_id in task_ids}
    for edge in dependencies:
        if not isinstance(edge, dict):
            errors.append("dependency entries must be objects")
            continue
        before = edge.get("before")
        after = edge.get("after")
        if before not in known or after not in known:
            errors.append("dependency references an unknown task")
            continue
        if before == after:
            errors.append("dependency cannot reference the same task")
            continue
        graph[before].add(after)

    state = {task_id: 0 for task_id in task_ids}

    def visit(task_id: str) -> bool:
        if state[task_id] == 1:
            return True
        if state[task_id] == 2:
            return False
        state[task_id] = 1
        if any(visit(next_id) for next_id in graph[task_id]):
            return True
        state[task_id] = 2
        return False

    if any(visit(task_id) for task_id in task_ids if state[task_id] == 0):
        errors.append("dependencies must be acyclic")

    ownership_count = {task_id: 0 for task_id in task_ids}
    write_scopes: list[tuple[str, str]] = []
    for row in ownership:
        if not isinstance(row, dict):
            errors.append("ownership entries must be objects")
            continue
        task_id = row.get("taskId")
        if task_id not in known:
            errors.append("ownership references an unknown task")
            continue
        ownership_count[task_id] += 1
        if row.get("access") == "write" and isinstance(row.get("scopes"), list):
            write_scopes.extend(
                (task_id, scope)
                for scope in row["scopes"]
                if isinstance(scope, str)
            )
    if any(count != 1 for count in ownership_count.values()):
        errors.append("every task must have exactly one ownership entry")
    for index, (left_task, left_scope) in enumerate(write_scopes):
        for right_task, right_scope in write_scopes[index + 1:]:
            if left_task != right_task and _scopes_overlap(left_scope, right_scope):
                errors.append("write ownership scopes must be disjoint")
                break

    verification_count = {task_id: 0 for task_id in task_ids}
    for row in verification:
        if not isinstance(row, dict):
            errors.append("verification entries must be objects")
            continue
        task_id = row.get("taskId")
        if task_id not in known:
            errors.append("verification references an unknown task")
            continue
        verification_count[task_id] += 1
    if any(count != 1 for count in verification_count.values()):
        errors.append("every task must have exactly one verification entry")

    owners = {
        item.get("owner")
        for item in tasks
        if isinstance(item, dict) and isinstance(item.get("owner"), str)
    }
    exact_routes = {
        "direct": frozenset({"parent"}),
        "subagents": frozenset({"subagent"}),
        "travis-b": frozenset({"travis-b"}),
    }
    if route in exact_routes and owners != exact_routes[route]:
        errors.append("route does not match task owners")
    elif route == "mixed" and owners not in (
        {"parent", "subagent"},
        {"parent", "travis-b"},
    ):
        errors.append("mixed route must use parent plus one worker class")
    elif route not in {*exact_routes, "mixed"}:
        errors.append("route is unsupported")

    return tuple(dict.fromkeys(errors))[:8]
```

The route matrix embedded above is authoritative:

```python
allowed_owners = {
    "direct": frozenset({"parent"}),
    "subagents": frozenset({"subagent"}),
    "travis-b": frozenset({"travis-b"}),
}
```

For `mixed`, require `parent` plus exactly one of `subagent` or `travis-b`. For every other route, require the exact owner set shown above. Normalize scopes by replacing backslashes, collapsing repeated slashes, removing leading `./`, and removing one trailing slash. Two write scopes conflict when equal or when either is a slash-component parent of the other.

- [ ] **Step 7: Connect semantic validation at the typed-result owner**

After the existing Draft 2020-12 schema validation succeeds in `settle_typed_result()`, add a bounded built-in-role validator map:

```python
_BUILTIN_RESULT_VALIDATORS = {
    "coordination-planner": validate_coordination_plan,
}
```

Call only the matching validator, prefix each returned category with `typed result semantic mismatch:`, cap the emitted list to eight entries, and use the existing failure settlement path. Do not retry the child and do not alter generic typed-role settlement.

- [ ] **Step 8: Run GREEN and the typed-result neighborhood**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_coordination.py \
  tests/test_subagents.py \
  tests/test_subagent_role_resolution.py \
  tests/test_model_role_subagents.py
```

Expected: all tests pass and generic typed results remain unchanged.

- [ ] **Step 9: Commit**

```bash
git add travis/coding_agent/coordination.py \
  travis/coding_agent/subagent_results.py \
  tests/test_coordination.py \
  tests/test_subagents.py
git diff --cached --name-only
git commit -m "feat(coordination): validate bounded planner requests"
```

### Task 2: Package and discover the read-only planner role

**Files:**

- Create: `travis/resources/roles/coordination-planner.json`
- Create: `packages/travis234-cli/roles/coordination-planner.json`
- Modify: `travis/coding_agent/config.py`
- Modify: `travis/coding_agent/__init__.py`
- Modify: `travis/coding_agent/resource_candidates.py`
- Modify: `pyproject.toml`
- Modify: `packages/travis234-cli/package.json`
- Modify: `tests/test_installed_metadata.py`
- Modify: `tests/test_resource_agent_role_loader.py`
- Modify: `tests/test_distribution_contract.py`
- Modify: `packages/travis234-cli/test/travis234-cli.test.js`

**Interfaces:**

```python
def get_packaged_roles_path() -> str:
    """Return the installed read-only built-in role directory."""
```

The role is named `coordination-planner`; its `modelRole` is `reviewer`; its complete tool/effect ceiling is read-only; its timeout is 120 seconds; it cannot spawn or declare artifacts.

- [ ] **Step 1: Add failing installed-resource and loader tests**

Add assertions that:

```python
roles_root = Path(get_packaged_roles_path())
planner_path = roles_root / "coordination-planner.json"
assert planner_path.is_file()

planner = loader.get_agent_roles().get("coordination-planner")
assert planner is not None
assert planner.source.source == "builtin"
assert planner.source.scope == "builtin"
assert planner.model_role == "reviewer"
assert planner.allowed_tools == ("read", "grep", "find", "ls")
assert planner.allowed_effects == ("read",)
assert planner.can_spawn is False
assert planner.default_timeout_seconds == 120
assert planner.artifact_policy == "none"
```

Add a trusted project role with the same name and assert project precedence remains higher than built-in, with both candidates visible in the capability resolution. Add `no_agent_roles=True` coverage proving the built-in is absent.

- [ ] **Step 2: Add failing distribution tests**

Assert Python/npm role trees match byte-for-byte, `pyproject.toml` declares `resources/roles/*.json`, npm declares `roles/**/*.json`, and built archives contain `coordination-planner.json` at their correct paths.

- [ ] **Step 3: Run RED**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_installed_metadata.py \
  tests/test_resource_agent_role_loader.py \
  tests/test_distribution_contract.py
npm --prefix packages/travis234-cli test
```

Expected: missing helper/resource/archive assertions fail.

- [ ] **Step 4: Add built-in role path discovery**

In `config.py`:

```python
def get_packaged_roles_path() -> str:
    """Return the installed read-only built-in role directory."""

    return _packaged_resource_path("roles")
```

Export it from `travis.coding_agent.__init__`. In `build_resource_content()`, collect JSON files with the existing `collect_resource_files(Path(get_packaged_roles_path()), "roles")` only when `no_agent_roles` is false. Add metadata for each built-in path:

```python
{
    "source": "builtin",
    "scope": "builtin",
    "origin": "package",
    "baseDir": get_packaged_roles_path(),
}
```

Append built-ins after resolved and explicit role paths. Their priority remains zero, so global, temporary, and trusted-project definitions continue to win through the capability registry. Do not teach `load_agent_roles()` to accept directories.

- [ ] **Step 5: Add the exact role schema in both mirrors**

Use this top-level role shape and no unlisted role fields:

```json
{
  "name": "coordination-planner",
  "description": "Produce one bounded read-only coordination recommendation for a complex or forced coordination request.",
  "modelRole": "reviewer",
  "allowedTools": ["read", "grep", "find", "ls"],
  "allowedEffects": ["read"],
  "canSpawn": false,
  "maxDepth": 1,
  "skills": [],
  "context": [],
  "defaultTimeoutSeconds": 120,
  "artifactPolicy": "none",
  "resultSchema": {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": false,
    "required": [
      "route",
      "rationale",
      "tasks",
      "dependencies",
      "ownership",
      "risks",
      "approvalGates",
      "verification",
      "stopConditions"
    ],
    "properties": {
      "route": {"enum": ["direct", "subagents", "travis-b", "mixed"]},
      "rationale": {"type": "string", "minLength": 1, "maxLength": 800},
      "tasks": {
        "type": "array",
        "minItems": 1,
        "maxItems": 6,
        "items": {
          "type": "object",
          "additionalProperties": false,
          "required": ["id", "objective", "owner"],
          "properties": {
            "id": {"type": "string", "pattern": "^task-[a-z0-9-]{1,32}$"},
            "objective": {"type": "string", "minLength": 1, "maxLength": 600},
            "owner": {"enum": ["parent", "subagent", "travis-b"]}
          }
        }
      },
      "dependencies": {
        "type": "array",
        "maxItems": 15,
        "items": {
          "type": "object",
          "additionalProperties": false,
          "required": ["before", "after"],
          "properties": {
            "before": {"type": "string", "pattern": "^task-[a-z0-9-]{1,32}$"},
            "after": {"type": "string", "pattern": "^task-[a-z0-9-]{1,32}$"}
          }
        }
      },
      "ownership": {
        "type": "array",
        "minItems": 1,
        "maxItems": 6,
        "items": {
          "type": "object",
          "additionalProperties": false,
          "required": ["taskId", "access", "scopes"],
          "properties": {
            "taskId": {"type": "string", "pattern": "^task-[a-z0-9-]{1,32}$"},
            "access": {"enum": ["read", "write"]},
            "scopes": {
              "type": "array",
              "minItems": 1,
              "maxItems": 12,
              "uniqueItems": true,
              "items": {"type": "string", "minLength": 1, "maxLength": 300}
            }
          }
        }
      },
      "risks": {
        "type": "array",
        "maxItems": 8,
        "items": {"type": "string", "minLength": 1, "maxLength": 400}
      },
      "approvalGates": {
        "type": "array",
        "maxItems": 8,
        "items": {
          "type": "object",
          "additionalProperties": false,
          "required": ["kind", "condition"],
          "properties": {
            "kind": {
              "enum": [
                "tool-policy",
                "commit",
                "integrate",
                "push",
                "publish",
                "deploy",
                "delete",
                "external-write",
                "trust",
                "memory-retain",
                "replay"
              ]
            },
            "condition": {"type": "string", "minLength": 1, "maxLength": 400}
          }
        }
      },
      "verification": {
        "type": "array",
        "minItems": 1,
        "maxItems": 6,
        "items": {
          "type": "object",
          "additionalProperties": false,
          "required": ["taskId", "evidence"],
          "properties": {
            "taskId": {"type": "string", "pattern": "^task-[a-z0-9-]{1,32}$"},
            "evidence": {
              "type": "array",
              "minItems": 1,
              "maxItems": 8,
              "items": {"type": "string", "minLength": 1, "maxLength": 400}
            }
          }
        }
      },
      "stopConditions": {
        "type": "object",
        "additionalProperties": false,
        "required": ["success", "failure", "cancellation", "blocker"],
        "properties": {
          "success": {"type": "string", "minLength": 1, "maxLength": 400},
          "failure": {"type": "string", "minLength": 1, "maxLength": 400},
          "cancellation": {"type": "string", "minLength": 1, "maxLength": 400},
          "blocker": {"type": "string", "minLength": 1, "maxLength": 400}
        }
      }
    }
  }
}
```

- [ ] **Step 6: Update package declarations and archive tests**

Add `resources/roles/*.json` to the `travis` package-data list and `roles/**/*.json` to npm `files`. Do not change npm launcher import behavior: the Python wheel inside the image supplies the built-in role, while the npm copy exists for package parity and inspection.

- [ ] **Step 7: Run GREEN and role-resolution tests**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_installed_metadata.py \
  tests/test_resource_agent_role_loader.py \
  tests/test_distribution_contract.py \
  tests/test_subagent_role_resolution.py \
  tests/test_model_role_subagents.py \
  tests/test_coding_tools_and_subagents.py -k 'typed_role'
npm --prefix packages/travis234-cli test
```

- [ ] **Step 8: Run the Phase 1 deterministic TUI gate**

Use `CodingApp`, `FakeTerminal`, and the real resource loader. Assert the system prompt contains only the planner role's bounded name/description through existing tool metadata, contains no result schema or coordination workflow body, and an unrelated prompt creates no subagent task.

- [ ] **Step 9: Commit**

```bash
git add travis/resources/roles/coordination-planner.json \
  packages/travis234-cli/roles/coordination-planner.json \
  travis/coding_agent/config.py \
  travis/coding_agent/__init__.py \
  travis/coding_agent/resource_candidates.py \
  pyproject.toml \
  packages/travis234-cli/package.json \
  tests/test_installed_metadata.py \
  tests/test_resource_agent_role_loader.py \
  tests/test_distribution_contract.py \
  packages/travis234-cli/test/travis234-cli.test.js
git diff --cached --name-only
git commit -m "feat(coordination): package read-only planner role"
```

### Task 3: Register the command alias and reject malformed requests pre-provider

**Files:**

- Modify: `travis/coding_agent/skills.py`
- Modify: `travis/coding_agent/session_extensions.py`
- Modify: `tests/test_resource_runtime_parity.py`
- Modify: `tests/test_tui_resource_projection.py`

**Interfaces:**

- Canonical `/skill:coordination` and convenience `/coordination` must inject the same effective skill and the same runtime-parsed request block.
- Existing commands named `coordination` win; the skill alias never receives a suffixed fallback name.
- Removing/disabling the effective coordination skill or disabling skill commands removes only skill-owned coordination registrations.

- [ ] **Step 1: Add failing provider-boundary tests**

Create a temporary coordination skill fixture and a faux provider that records submitted `UserMessage` text. Assert:

```python
session.prompt('/coordination --deep inspect "src/app.py"')
session.prompt('/skill:coordination --plan inspect tests')
```

Both submissions contain the selected skill once plus literal compact JSON request data. Then invoke empty, `--deep` without a goal, and `--unknown goal`; assert `ValueError` and zero additional provider calls.

Add `-- --deep literal` coverage, Unicode coverage, alias autocomplete coverage, skill-command-disabled coverage, `no_skills` coverage, `disable-model-invocation: true` coverage for coordination, and an extension command collision proving the extension keeps `/coordination` while canonical `/skill:coordination` remains available when allowed.

- [ ] **Step 2: Run RED**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_resource_runtime_parity.py -k 'coordination' \
  tests/test_tui_resource_projection.py -k 'coordination'
```

Expected: alias and pre-provider parsing assertions fail.

- [ ] **Step 3: Parse coordination arguments inside skill formatting**

In `format_skill_invocation()`:

```python
if skill.name == "coordination":
    additional_instructions = format_coordination_request(additional_instructions)
```

Keep every other skill byte-for-byte behavior unchanged. This location covers canonical commands, the alias, and direct registered-command invocation without modifying the turn loop.

- [ ] **Step 4: Reconcile the alias safely**

Refactor `_register_skill_commands()` just enough to:

1. collect the effective loaded `coordination` skill;
2. remove prior `coordination` and `skill:coordination` registrations only when their `source_info.source == "skill"`;
3. keep extension-owned commands untouched;
4. register canonical names for ordinary skills as before;
5. register both names for an eligible, model-invocable coordination skill;
6. register neither coordination name when skill commands are disabled or the effective coordination skill is absent/disabled.

Both coordination registrations use `replace(skill.source_info, source="skill")`, the same handler closure, and the skill description. Do not change `ExtensionRunner.register_command()` collision semantics globally.

- [ ] **Step 5: Run GREEN and nearby command tests**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_resource_runtime_parity.py \
  tests/test_tui_resource_projection.py \
  tests/test_tui_commands_and_extensions.py
```

- [ ] **Step 6: Commit**

```bash
git add travis/coding_agent/skills.py \
  travis/coding_agent/session_extensions.py \
  tests/test_resource_runtime_parity.py \
  tests/test_tui_resource_projection.py
git diff --cached --name-only
git commit -m "feat(coordination): add explicit skill command alias"
```

### Task 4: Author the coordination skill with RED/GREEN agent evaluation

**Files:**

- Create: `travis/resources/skills/coordination/SKILL.md`
- Create: `travis/resources/skills/coordination/references/planning-contract.md`
- Create: `packages/travis234-cli/skills/coordination/SKILL.md`
- Create: `packages/travis234-cli/skills/coordination/references/planning-contract.md`
- Modify: `travis/resources/skills/subagent-delegation/SKILL.md`
- Modify: `packages/travis234-cli/skills/subagent-delegation/SKILL.md`
- Modify: `travis/resources/skills/orchestration/SKILL.md`
- Modify: `packages/travis234-cli/skills/orchestration/SKILL.md`
- Modify: `tests/test_installed_metadata.py`
- Modify: `tests/test_coding_resources_and_services.py`
- Modify: `tests/test_distribution_contract.py`
- Modify: `packages/travis234-cli/test/travis234-cli.test.js`

**Interfaces:**

- Frontmatter name is exactly `coordination`.
- Description starts with `Use when`, is third-person, limits triggering to an explicit command or explicit naming of the coordination skill, does not summarize the workflow, and stays below 500 characters.
- `SKILL.md` stays below 500 words and references only `references/planning-contract.md` one level deep.
- The reference contains a contents list when it exceeds 100 lines.
- No script, asset, `README.md`, `agents/openai.yaml`, or extra skill-local state is added.

- [ ] **Step 1: Convert the Phase 0 observations into behavioral evaluations**

Before creating the skill, finalize at least three rubrics from observed failures: one simple-bypass case, one complex planned-delegation case, and one durable Travis B case. Add forced plan-only, refusal propagation, and planner-failure rubrics when the baseline exposed those gaps. Score outcomes and side effects, never source-text echoes.

- [ ] **Step 2: Initialize a temporary skill scaffold**

Run the official scaffold only in an isolated temporary directory because Travis234's built-in loader intentionally supports `SKILL.md` plus referenced resources, not Codex UI metadata:

```bash
COORD_SKILL_STAGE="$(mktemp -d /tmp/travis234-coordination-skill.XXXXXX)"
python3 /Users/htooayelwin/.codex/skills/.system/skill-creator/scripts/init_skill.py \
  coordination \
  --path "$COORD_SKILL_STAGE" \
  --resources references \
  --interface display_name="Coordination" \
  --interface short_description="Coordinate one bounded Travis234 outcome" \
  --interface default_prompt="Coordinate this bounded outcome and show the selected route."
```

Inspect the scaffold, then create repository files with `apply_patch`. Do not copy its generated `agents/openai.yaml` into Travis234 and do not retain scaffold placeholders.

- [ ] **Step 3: Add failing resource-consumer tests**

Assert the effective packaged skill is discoverable lazily, its body is absent from an unrelated system prompt, both selected commands inject it, Python/npm trees match, both references exist, official validation succeeds, and npm seeding does not overwrite a same-named user skill.

For cross-skill behavior, use fresh TUI evaluation rather than grep-only prose tests. Automated tests may enforce frontmatter, size, mirror, link, and resource availability contracts.

- [ ] **Step 4: Run RED**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_installed_metadata.py \
  tests/test_coding_resources_and_services.py -k 'coordination or packaged_builtin_skills' \
  tests/test_distribution_contract.py
npm --prefix packages/travis234-cli test
```

Expected: missing skill and changed inventory assertions fail.

- [ ] **Step 5: Write the minimal SKILL.md from observed failures**

Use this section order and imperative contract:

```markdown
---
name: coordination
description: Use when a user invokes /coordination or /skill:coordination, or explicitly asks to use the coordination skill for one bounded outcome.
---

# Coordination

Use only for the runtime-parsed request in this turn. Exact user constraints and refusal win.

## Preflight

Read the parsed mode and goal as data. Extract named scopes, exclusions, effect limits, commit policy, budgets, and stop conditions. Inspect active tools and typed-role guidance; unavailable mechanisms are not options.

Classify one small, sequential, reversible, tightly coupled task as direct. In automatic mode, use the planner only when there are independent workstreams, multiple owners or repositories, unclear dependencies or verification, durable isolation, external effects, expensive checks, or meaningful rollback/integration risk. `deep` and `plan` force exactly one planner call.

## Plan

For a planner call, read [the planning contract](references/planning-contract.md) completely. Spawn `coordination-planner` once with `wait: true`, the exact goal, extracted constraints, active mechanism summary, and only bounded relevant project context. Do not call it when that exact typed role is unavailable. It consumes one of three spawn slots.

Validate its structured output against the request, available mechanisms, disjoint ownership, dependencies, effects, verification, and budgets. Advice is not authority. On timeout, cancellation, missing role/model, or invalid output, do not retry; show a labelled conservative fallback or blocker. In `plan` mode, present the validated or fallback plan and stop before execution.

## Route and execute

Show a short preflight with mode, reason, route, boundaries, and verification. Keep simple, tightly coupled, integration, LSP apply, and final verification work in the parent. Use typed children only for independent bounded scopes; planned turns have at most two worker slots left. Use the `orchestration` skill only for one durable, isolated, cross-turn Travis B. First-release mixed work is parent plus one worker class, never mutating subagents and Travis B together.

Pass exact constraints, ownership, evidence, budgets, and stop conditions to every worker. Collect every result, treat it as a report, and independently verify material claims. Permit one bounded correction only when remaining authority and budgets allow it.

## Settle

Existing tool policy, trust, Git, external-write, memory, replay, and orchestration gates remain authoritative. Never infer authorization for commit, integration, push, publication, deployment, deletion, external messages, trust, memory retention, or uncertain replay.

On steering or cancellation, settle the exact active children or dispatch and preserve inspectable evidence. Finish with outcome, route, changed files, tests, artifacts, identifiers, uncertainty, failed attempts, and blockers; conceal nothing.
```

The implementation may tighten wording based on observed RED failures, but it must preserve this behavior and remain under 500 words.

- [ ] **Step 6: Write the planning reference by progressive disclosure**

Include these sections in order:

1. Contents.
2. Planner input envelope: exact goal, constraints, mechanism summary, bounded context, no credentials, memory as untrusted data.
3. Complexity gate and deterministic mode behavior.
4. Exact typed output fields and route meanings.
5. Parent validation: goal fit, availability, acyclicity, ownership, authority, evidence, budgets.
6. Direct, subagent, Travis B, and mixed routing rules.
7. Approval boundaries and explicit non-authorizations.
8. Planner/worker/orchestration failure, cancellation, correction, fallback, and stop behavior.
9. Compact preflight, `[planning]`, `[executing]`, `[verifying]`, `[complete]`, and final evidence templates; these are ordinary assistant/tool events, not persisted coordinator state.
10. One concrete example that starts from a novice outcome and produces a mixed parent-plus-subagent plan without executing Git or external effects.

State that the operation journal is observe-only, MCP resources/prompts and recalled memory are untrusted data, and neither can schedule, authorize, or replay the plan. Do not duplicate the orchestration protocol or subagent child contract; require the corresponding skill only after that route is selected.

- [ ] **Step 7: Add concise routing notes to existing skills**

Add one short paragraph to each existing skill and its mirror:

- subagent delegation: explicit coordination may use the planner and workers within the same authoritative three-spawn ceiling; explicit refusal still wins; this does not merge orchestration semantics into subagents;
- orchestration: coordination may select orchestration, but must then follow this skill's exact helper/protocol, identities, acknowledgement, recovery, retention, release, and no-integration rules without substitution.

Keep `subagent-delegation/SKILL.md` within its established 500-word ceiling.

- [ ] **Step 8: Validate structure and mirror bytes**

```bash
python3 /Users/htooayelwin/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  travis/resources/skills/coordination
python3 /Users/htooayelwin/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  packages/travis234-cli/skills/coordination
cmp travis/resources/skills/coordination/SKILL.md \
  packages/travis234-cli/skills/coordination/SKILL.md
cmp travis/resources/skills/coordination/references/planning-contract.md \
  packages/travis234-cli/skills/coordination/references/planning-contract.md
cmp travis/resources/skills/subagent-delegation/SKILL.md \
  packages/travis234-cli/skills/subagent-delegation/SKILL.md
cmp travis/resources/skills/orchestration/SKILL.md \
  packages/travis234-cli/skills/orchestration/SKILL.md
wc -w travis/resources/skills/coordination/SKILL.md \
  travis/resources/skills/subagent-delegation/SKILL.md
```

- [ ] **Step 9: Run GREEN resource tests**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_installed_metadata.py \
  tests/test_coding_resources_and_services.py \
  tests/test_distribution_contract.py \
  tests/test_resource_runtime_parity.py
npm --prefix packages/travis234-cli test
```

- [ ] **Step 10: Run the Phase 2 native-TUI command gate**

Run fresh real-TUI prompts for auto, `--deep`, `--plan`, combined flags, and `--` literal handling. Also submit one complex ordinary prompt that does not invoke or name coordination and prove it neither loads the skill body nor starts the planner. Prove empty/unknown inputs do not reach the provider by using the session/provider request log. `--plan` must show a bounded plan and produce no write, execute, network, worker, orchestration, Git, memory, or replay effect.

Run five fresh-context wording repetitions for the plan-only and refusal cases. Manually inspect every response; prompt echoes do not count. If a repeated failure appears, record its exact rationalization, minimally revise the skill, rerun official validation and automated tests, then repeat all five.

- [ ] **Step 11: Commit**

```bash
git add travis/resources/skills/coordination \
  packages/travis234-cli/skills/coordination \
  travis/resources/skills/subagent-delegation/SKILL.md \
  packages/travis234-cli/skills/subagent-delegation/SKILL.md \
  travis/resources/skills/orchestration/SKILL.md \
  packages/travis234-cli/skills/orchestration/SKILL.md \
  tests/test_installed_metadata.py \
  tests/test_coding_resources_and_services.py \
  tests/test_distribution_contract.py \
  packages/travis234-cli/test/travis234-cli.test.js \
  docs/verification/coordination-skill.md
git diff --cached --name-only
git commit -m "feat(skills): add bounded coordination workflow"
```

### Task 5: Exercise direct, planner, subagent, and orchestration routes deterministically

**Files:**

- Create: `tests/test_coordination_tui_scenarios.py`
- Modify only if a regression is first observed: runtime files already named in Tasks 1–4.

**Interfaces:**

- Uses real `CodingApp`, `AgentSession`, resource loader, command expansion, tool definitions, typed role resolution, supervisor, and orchestration helper fixtures.
- Faux providers may choose deterministic calls; assertions must inspect real effects, task metadata, structured results, files, and state rather than mock call existence.

- [ ] **Step 1: Add the simple and forced-planning scenarios first**

Write failing scenarios for:

1. automatic simple request returns directly with zero planner tasks;
2. automatic complex request spawns exactly one `coordination-planner` task;
3. `--deep` spawns exactly one planner even for a small request;
4. `--plan` returns a plan and performs no execution after planning;
5. planner invalid JSON/schema/semantics settles failed, is not retried, and yields a labelled conservative fallback.

The provider fake must return complete real streamed tool-call structures. The planner backend must return the exact typed envelope, not a partial mock.

- [ ] **Step 2: Add spawn-budget and refusal scenarios**

Write failing scenarios proving:

- the planner increments `_model_subagents_spawned_this_turn` through the public tool path;
- a planned turn can spawn two workers and the fourth model spawn is blocked with `subagent_spawn_limit_per_turn`;
- no-planner direct coordination retains all three worker slots;
- `no subagents`, `no network`, `no MCP`, `no commit`, and `local only` remain in every chosen worker goal/context and override a conflicting planner recommendation;
- child output is treated as a report and false success is caught by parent-observed evidence.

- [ ] **Step 3: Add route and ownership scenarios**

Write failing scenarios proving:

- tightly coupled or integration work stays parent-owned;
- independent disjoint work uses typed children;
- durable new-worktree work selects the existing orchestration protocol;
- a plan with overlapping mutation scopes fails before worker execution;
- a plan containing mutating subagents and Travis B fails before worker execution;
- the parent never invokes LSP apply inside a child;
- orchestration selection uses the version-matched helper and never raw tmux keystrokes or screen scraping;
- no route integrates, pushes, publishes, deletes, retains memory, grants trust, or replays uncertainty.

- [ ] **Step 4: Add recovery, correction, and cancellation scenarios**

Write failing scenarios proving:

- planner timeout/cancellation receives no retry;
- one bounded child correction uses only a remaining spawn slot;
- exhausted slots produce a visible blocker;
- steering and cancellation settle exact in-process child IDs;
- Travis B recovery reconnects the exact worker/session and does not replay a dispatch;
- retained and released workers follow the existing protocol;
- terminal failures and uncertainty remain visible in final synthesis.

- [ ] **Step 5: Confirm RED before changing skill wording or runtime**

Run each newly added test node immediately after writing it. A test must fail because the behavior or instruction is absent, not because its fixture is malformed. If a scenario passes without the skill behavior, strengthen the observable rubric rather than adding production code.

- [ ] **Step 6: Make the minimal GREEN change**

Prefer tightening `SKILL.md` or `planning-contract.md` for model-judgment failures. Modify Python only for deterministic runtime defects reproduced independently of model wording. Never add a new tool or provider path to make a scripted test easier.

- [ ] **Step 7: Run the focused deterministic matrix**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_coordination.py \
  tests/test_coordination_tui_scenarios.py \
  tests/test_resource_runtime_parity.py \
  tests/test_subagents.py \
  tests/test_orchestration_tui_scenarios.py
```

- [ ] **Step 8: Run Phase 3 and Phase 4 native-TUI gates**

Phase 3 prompts cover simple bypass, automatic planning, `--deep`, two typed workers after planning, refusal propagation, and false-child-success verification. Phase 4 prompts cover one supervised Travis B worktree round trip, one retained/recovered round trip, mixed parent-plus-B routing, and no automatic Git integration or cleanup.

After every prompt, print and record `PASS` or `FAIL` with observable evidence. Fix every product failure regression-first and rerun the failed prompt plus all earlier prompts in that phase before continuing.

- [ ] **Step 9: Commit**

```bash
git add tests/test_coordination_tui_scenarios.py \
  travis/resources/skills/coordination/SKILL.md \
  packages/travis234-cli/skills/coordination/SKILL.md \
  travis/resources/skills/coordination/references/planning-contract.md \
  packages/travis234-cli/skills/coordination/references/planning-contract.md \
  docs/verification/coordination-skill.md
git diff --cached --name-only
git commit -m "test(coordination): exercise bounded routing and recovery"
```

If a regression required Python changes, stage only its named failing test and minimal owner file in a separate fix commit before this scenario commit.

### Task 6: Document the novice-facing product surface

**Files:**

- Modify: `README.md`
- Modify: `packages/travis234-cli/README.md`
- Modify: `docs/settings.md`
- Modify: `tests/test_distribution_contract.py`
- Modify: `packages/travis234-cli/test/travis234-cli.test.js`

**Interfaces:**

- Human docs explain what the user types, what Travis decides, what remains visible, what costs an extra model call, and what is never automatic.
- Docs distinguish coordination from subagents, Travis B, n8n, MCP, LSP, artifacts, operations, and memory without teaching private helper grammar.

- [ ] **Step 1: Define the documentation acceptance rubric before editing**

The updated README must make these answers discoverable in one reading:

- a novice can type `/coordination` plus an outcome;
- simple work skips the planner;
- complex/forced work uses at most one planner call;
- `--plan` stops before execution;
- the selected route and boundaries are visible;
- planned in-process work has two remaining worker slots;
- direct, subagents, and Travis B have distinct purposes;
- n8n and generic MCP remain additive;
- no Git integration, publication, deletion, trust, external write, memory retention, or replay is implied;
- disabling skills or skill commands removes the command behavior.

- [ ] **Step 2: Add consumer-facing docs**

Update `README.md`'s built-in skill count and add a `### One-command coordination` section immediately before durable multi-Travis orchestration. Include the four command forms, a compact route table, one novice example, planning latency/cost tradeoff, and explicit non-authorizations.

Update npm README's bundled skill list to include all four skills and mention the mirrored planner role. Update `docs/settings.md` to explain the built-in role, its default read-only contract, collision precedence, and that trusted overrides remain operator-owned configuration while the coordination skill still validates plans and authority.

- [ ] **Step 3: Update distribution assertions for user-visible behavior**

Assert archive membership and skill counts, not exact prose sentences. Keep human documentation free to improve without brittle source-text tests.

- [ ] **Step 4: Run docs/package tests**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_distribution_contract.py \
  tests/test_installed_metadata.py \
  tests/test_coding_resources_and_services.py
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

- [ ] **Step 5: Commit**

```bash
git add README.md \
  packages/travis234-cli/README.md \
  docs/settings.md \
  tests/test_distribution_contract.py \
  packages/travis234-cli/test/travis234-cli.test.js
git diff --cached --name-only
git commit -m "docs: explain one-command coordination"
```

### Task 7: Build the wheel and run the final 12-prompt MiniMax TUI qualification

**Files:**

- Modify: `docs/verification/coordination-skill.md`
- Modify product/tests only through a new regression-first fix cycle when a live defect is observed.

**Interfaces:**

- Runs the exact built wheel through its `travis234` console entry point in an attached PTY.
- Loads the ignored `.env` only via `--dotenv` and never reports credential values.
- Uses a fresh temporary agent directory and disposable Git fixtures.

- [ ] **Step 1: Build and install an isolated candidate wheel**

```bash
COORD_RELEASE_DIR="$(mktemp -d /tmp/travis234-coordination-release.XXXXXX)"
mkdir -p "$COORD_RELEASE_DIR/root"
.venv/bin/python -m build --outdir "$COORD_RELEASE_DIR/root" .
python3.13 -m venv "$COORD_RELEASE_DIR/venv"
"$COORD_RELEASE_DIR/venv/bin/python" -m pip install \
  "$COORD_RELEASE_DIR/root/travis234-2.4.6-py3-none-any.whl"
"$COORD_RELEASE_DIR/venv/bin/travis234" --help
```

Record the exact source commit and wheel SHA-256 before live testing. Do not reuse the development editable install.

- [ ] **Step 2: Run the final prompt matrix in the native background TUI**

Use `openrouter/minimax/minimax-m3`, medium thinking, bracketed paste for multiline prompts, one persistent session where continuity is under test, and fresh sessions where baseline isolation matters.

Set `COORD_FIXTURE_DIR` and `COORD_EVIDENCE_DIR` to concrete temporary paths, then launch the installed wheel with this argument shape:

```bash
"$COORD_RELEASE_DIR/venv/bin/travis234" \
  --cwd "$COORD_FIXTURE_DIR" \
  --dotenv /Users/htooayelwin/orca/travis234/.env \
  --model openrouter/minimax/minimax-m3 \
  --thinking medium \
  --tui \
  --event-trace "$COORD_EVIDENCE_DIR/events.jsonl" \
  --conversation-log "$COORD_EVIDENCE_DIR/conversation.jsonl"
```

Never emit the dotenv contents. Use `/session` to capture the active session ID and `/exit` for clean termination.

The 12 required scenarios are:

1. novice one-command request with a visible direct route;
2. simple automatic route with no planner task;
3. automatic complex route with exactly one planner task;
4. forced `--deep` with exactly one planner task;
5. `--plan` with no execution effects;
6. planned typed parallel workers with two remaining slots and independent parent verification;
7. independent Travis B in a new worktree using the existing protocol;
8. explicit policy denial and refusal propagation;
9. planner invalid result or timeout with no retry and labelled fallback;
10. one bounded worker correction followed by visible settlement;
11. user steering/cancellation and exact child/dispatch cleanup or retention;
12. final evidence synthesis with route, changed files, tests, artifacts, IDs, uncertainty, failed attempts, and blockers, with no concealed failure.

Across scenarios 3–12, require concise visible phase updates using `[planning]`, `[executing]`, `[verifying]`, and `[complete]` only when that phase actually occurs. Absence of a skipped phase is correct; fabricated progress is a failure.

- [ ] **Step 3: Report each prompt immediately**

After each scenario, append one row with `PASS` or `FAIL`, exact prompt, selected route, planner call count, worker/dispatch IDs, effects observed, acceptance evidence, and cleanup state. A final marker, claimed success, or model narration without observed state is a failure.

- [ ] **Step 4: Exercise and fix until every row passes**

For each failure:

1. classify harness, provider/model judgment, instruction gap, or product defect;
2. preserve the failed trace;
3. add a failing automated regression for a product defect before editing Python;
4. add the failure's exact rationalization to the skill evaluation record before tightening skill wording;
5. rerun official skill validation, focused automated tests, the failed scenario, and every earlier scenario that shares its route;
6. continue until all 12 rows pass.

Do not weaken acceptance, hide failed attempts, enlarge timeouts without evidence, or convert a model failure into a scripted success.

- [ ] **Step 5: Scan evidence for secrets and live-resource leaks**

Use credential-name patterns only; never search for actual secret values. Confirm no owned process, tmux worker, unacknowledged dispatch, or disposable worktree remains unless the scenario explicitly requires retained evidence and the record names it.

- [ ] **Step 6: Commit live qualification and any separate fixes**

Each product fix receives its own regression-first commit. Then:

```bash
git add docs/verification/coordination-skill.md
git diff --cached --name-only
git commit -m "docs: qualify coordination TUI scenarios"
```

### Task 8: Run repository, package, clean-install, and container gates

**Files:**

- Modify: `docs/verification/coordination-skill.md`
- No production changes expected. Any failure-induced fix returns to RED/GREEN first.

**Interfaces:**

- Completion evidence must come from the final committed source tree, not an earlier candidate.
- Container work starts only after every feature phase and live TUI scenario is complete.

- [ ] **Step 1: Run focused coordination and neighboring suites**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q \
  tests/test_coordination.py \
  tests/test_coordination_tui_scenarios.py \
  tests/test_resource_agent_role_loader.py \
  tests/test_resource_runtime_parity.py \
  tests/test_coding_resources_and_services.py \
  tests/test_subagents.py \
  tests/test_subagent_role_resolution.py \
  tests/test_model_role_subagents.py \
  tests/test_orchestration_tui_scenarios.py \
  tests/test_distribution_contract.py \
  tests/test_installed_metadata.py
```

- [ ] **Step 2: Run the complete Python suite with thread warnings fatal**

```bash
PYTHONPATH=. .venv/bin/python -m pytest -q -W error::pytest.PytestUnhandledThreadExceptionWarning tests
```

Expected: every repository Python test passes with no unhandled thread exception.

- [ ] **Step 3: Run npm and optional adapter suites**

```bash
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run build
npm --prefix packages/travis234-cli run pack:dry-run
PYTHONPATH=packages/travis234-mcp-adapter \
  .venv/bin/python -m pytest -q packages/travis234-mcp-adapter/tests
```

- [ ] **Step 4: Build final Python and npm artifacts from the exact tree**

```bash
COORD_FINAL_DIR="$(mktemp -d /tmp/travis234-coordination-final.XXXXXX)"
mkdir -p "$COORD_FINAL_DIR/root" "$COORD_FINAL_DIR/adapter" "$COORD_FINAL_DIR/npm"
.venv/bin/python -m build --outdir "$COORD_FINAL_DIR/root" .
.venv/bin/python -m build \
  --outdir "$COORD_FINAL_DIR/adapter" \
  packages/travis234-mcp-adapter
npm pack --prefix packages/travis234-cli \
  --pack-destination "$COORD_FINAL_DIR/npm"
.venv/bin/python -m twine check \
  "$COORD_FINAL_DIR/root"/* \
  "$COORD_FINAL_DIR/adapter"/*
```

Inspect wheel, sdist, and npm member lists. Assert both coordination skill files, the planning reference, and planner role exist; Python/npm mirrors have identical SHA-256 values; `.env`, `.git`, sessions, orchestration state, traces, and temporary evidence are absent.

- [ ] **Step 5: Run clean-install smoke tests**

Create fresh Python 3.13 and npm prefixes under the final temporary directory. Install exact artifacts and run the installed-resource probe:

```bash
python3.13 -m venv "$COORD_FINAL_DIR/clean-python"
"$COORD_FINAL_DIR/clean-python/bin/python" -m pip install \
  "$COORD_FINAL_DIR/root/travis234-2.4.6-py3-none-any.whl"
"$COORD_FINAL_DIR/clean-python/bin/travis234" --help
TRAVIS234_CODING_AGENT_DIR="$COORD_FINAL_DIR/clean-agent" \
  "$COORD_FINAL_DIR/clean-python/bin/python" - <<'PY'
import os
from pathlib import Path
from travis.coding_agent import DefaultResourceLoader

root = Path.cwd()
loader = DefaultResourceLoader(
    cwd=str(root),
    agent_dir=os.environ["TRAVIS234_CODING_AGENT_DIR"],
    project_trusted=False,
)
loader.reload({"projectTrustOverride": False})
assert any(skill.name == "coordination" for skill in loader.get_skills()["skills"])
role = loader.get_agent_roles().get("coordination-planner")
assert role is not None
assert role.allowed_effects == ("read",)
assert role.default_timeout_seconds == 120
print("coordination-installed-resource-smoke: PASS")
PY
npm install \
  --prefix "$COORD_FINAL_DIR/clean-npm" \
  "$COORD_FINAL_DIR/npm/htooayelwinict-travis234-2.4.6.tgz"
"$COORD_FINAL_DIR/clean-npm/node_modules/.bin/travis234" --help
```

- [ ] **Step 6: Run the relevant container smoke only now**

```bash
docker build -f Dockerfile.release -t travis234:coordination-local .
docker run --rm travis234:coordination-local --help
docker run --rm --network none --entrypoint python \
  travis234:coordination-local \
  -c 'from pathlib import Path; from travis.coding_agent import DefaultResourceLoader; loader=DefaultResourceLoader(cwd="/tmp", agent_dir="/tmp/agent", project_trusted=False); loader.reload({"projectTrustOverride":False}); assert any(item.name=="coordination" for item in loader.get_skills()["skills"]); role=loader.get_agent_roles().get("coordination-planner"); assert role is not None and role.allowed_effects==("read",); print("coordination-container-resource-smoke: PASS")'
```

The resource probe receives no credential or dotenv input. Do not publish or tag a remote image.

- [ ] **Step 7: Perform final architecture and hygiene checks**

```bash
git diff --check
git status --short --branch
git diff 48d11a6 -- travis/agent/agent_loop.py travis/ai/providers travis/compaction
rg -n "TODO|TBD|FIXME|PLACEHOLDER" \
  travis/resources/skills/coordination \
  packages/travis234-cli/skills/coordination \
  docs/verification/coordination-skill.md
```

Expected: no protected-owner diff, no placeholder text, no unexpected worktree changes, and no credential-shaped tracked content.

- [ ] **Step 8: Finalize verification evidence**

Record exact commands, counts, durations, source commit, artifact filenames and SHA-256 values, clean-install results, Docker image ID, all 12 live scenario rows, failed attempts, and deferred publication. Do not describe any artifact as published.

- [ ] **Step 9: Commit the final verification record**

```bash
git add docs/verification/coordination-skill.md
git diff --cached --name-only
git commit -m "docs: verify built-in coordination skill"
git status --short --branch
```

Expected: the feature branch is clean and remains local.

## Final Success Checklist

- [ ] A novice needs only `/coordination` and an outcome.
- [ ] `/skill:coordination` remains the canonical resource command.
- [ ] Empty goals and unknown leading flags fail before provider/tool calls.
- [ ] Simple automatic work makes zero planner calls.
- [ ] Complex, `--deep`, and `--plan` work makes exactly one planner call.
- [ ] `--plan` performs no execution after planning.
- [ ] The planner is reviewer-routed, read-only, non-spawning, artifact-free, schema-bounded, semantic-plan validated, and capped at 120 seconds.
- [ ] A planned turn has at most two remaining in-process worker slots.
- [ ] Direct, subagent, Travis B, and mixed routes obey exact ownership and availability rules.
- [ ] Mutating subagents and Travis B never run together in one coordination plan.
- [ ] Explicit refusals and policy limits propagate and override planner advice.
- [ ] Child and Travis B results remain reports until parent verification.
- [ ] Planner/worker failure, timeout, invalid output, correction, steering, cancellation, and uncertainty settle visibly without unbounded retry.
- [ ] No global system-prompt workflow, new agent tool, provider bypass, durable coordinator state, or operation replay path was added.
- [ ] Python/npm skill and role mirrors are byte-identical and included in wheel, sdist, and npm archives.
- [ ] All five phase TUI gates and all 12 final MiniMax scenarios pass with per-prompt evidence.
- [ ] Full Python, npm, adapter, package, clean-install, and local container gates pass.
- [ ] No remote Git operation, publication, deployment, or GHCR promotion occurred.
