# Phase 4 — Repository-Wide Quality Convergence

> **Required skills:** `superpowers:executing-plans`,
> `superpowers:test-driven-development`, `superpowers:systematic-debugging`, and
> `superpowers:verification-before-completion`.

**Goal:** Convert the refactored architecture into durable repository quality: monotonic
typing/linting, bounded complexity, explicit dead/dormant ownership, maintainable tests,
canonical packaged resources, and enforceable security/hygiene checks.

**Architecture:** Static analysis expands only after an owner is clean. Complexity is
reduced through tested pure helpers, not suppression. Dead code is proven unused before
removal; deliberately dormant artifact maintenance remains nonautomatic and explicitly
documented.

**Do not modify:** `travis/agent/agent_loop.py`, user prompts/tool schemas unless a test
proves a refactor accidentally changed them, session formats, provider wire fixtures,
skill content semantics.

---

## Task 4.1: Converge Ruff and Pyright scopes

**Files:**

- Modify: `pyrightconfig.json`
- Modify: `ruff.toml`
- Modify: `tests/architecture/test_quality_configuration.py`
- Modify only files named by concrete diagnostics

- [ ] **Step 1: Capture diagnostics by owner**

Run both tools and retain their command output through the execution tool rather than
redirecting it into the repository:

```bash
uv run --locked --all-extras --dev ruff check travis tests --output-format concise
uv run --locked --all-extras --dev pyright
```

Summarize counts by rule/module in the verification ledger. Do not commit raw reports.

- [ ] **Step 2: Add failing monotonic-scope tests**

Require all Phase 1–3 contract, session, TUI, and provider modules in Pyright's checked
execution environments. Require full Ruff selection on those modules. Reject broad
`ignore = ["F401"]`, wildcard Pyright exclusions inside migrated owners, file-wide
`# type: ignore`, and unqualified `# noqa` additions.

- [ ] **Step 3: Fix diagnostics owner by owner**

Order:

1. contracts/composition;
2. provider families;
3. session collaborators;
4. TUI collaborators;
5. remaining `travis/ai` and `travis/coding_agent` leaf owners;
6. tests touched by this program.

Use explicit imports, resolved annotations, narrowing, and domain dataclasses. Do not
replace unknown attributes with `Any` or retain mixin-era imports solely to silence a
tool.

- [ ] **Step 4: Establish repository-wide fatal rules**

The entire `travis` and `tests` trees must pass `E9,F63,F7,F82`. Migrated owners pass
the complete configured set. Record remaining nonfatal legacy counts and an explicit
owner; the count may only fall after this checkpoint.

- [ ] **Step 5: Verify and commit in bounded owner groups**

Run focused tests after every module group, then:

```bash
uv run --locked --all-extras --dev ruff check --select E9,F63,F7,F82 travis tests
uv run --locked --all-extras --dev ruff check \
  travis/coding_agent/session_*.py travis/coding_agent/agent_session.py \
  travis/coding_agent/session_contracts.py travis/coding_agent/session_composition.py \
  travis/coding_agent/session_options.py travis/tui travis/ai/providers
uv run --locked --all-extras --dev pyright
uv run --locked --all-extras --dev pytest -q \
  tests/architecture/test_quality_configuration.py \
  tests/architecture/test_public_type_hints.py
```

Commit each owner separately, using messages such as
`chore(quality): type and lint session composition` and
`chore(quality): type and lint provider transports`.

---

## Task 4.2: Enforce and reduce complexity outside the protected loop

**Files:**

- Create: `scripts/check_python_complexity.py`
- Create: `tests/test_python_complexity.py`
- Modify: `pyproject.toml`, `uv.lock`
- Modify high-complexity modules named by the checker
- Modify focused tests for every decomposed function
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Add a locked complexity tool and failing checker tests**

Add `radon>=6,<7` to the dev group and update the lock. Implement a checker that invokes
Radon's Python API for a requested source tree, fails functions above cyclomatic
complexity 25, and fails newly migrated
modules above average complexity B. The only named production exception is the frozen
`travis/agent/agent_loop.py`; its hash is checked separately and its score may not rise.

Tests use fixture reports to prove threshold, exception, malformed report, and no-new-
exception behavior.

- [ ] **Step 2: Generate the real report and confirm RED**

```bash
uv run --locked --all-extras --dev python scripts/check_python_complexity.py \
  travis --max-complexity 25
```

- [ ] **Step 3: Decompose the known high-complexity owners**

Prioritize:

- responses translation/parsing;
- Anthropic and provider request construction remaining after Phase 3;
- CLI argument/startup dispatch;
- session service construction;
- TUI command dispatch remaining after Phase 2;
- any non-protected function the fresh report ranks above 25.

For each function, add direct tests for branch groups, extract pure named helpers, run
focused tests, then rerun the checker. Do not change error text, option precedence,
request fields, or command order unless a separate red bug test requires it.

- [ ] **Step 4: Add the complexity gate to CI and commit by owner**

Do not combine CLI, provider, session, and TUI complexity work in one commit. Finish with
one gate commit:

```bash
git add scripts/check_python_complexity.py tests/test_python_complexity.py \
  pyproject.toml uv.lock .github/workflows/ci.yml
git commit -m "test(quality): enforce bounded python complexity"
```

---

## Task 4.3: Remove proven dead symbols and classify dormant maintenance

**Files:**

- Create: `tests/architecture/test_dead_code_policy.py`
- Create: `docs/architecture/artifact-retention.md`
- Delete if still unused: `travis/ai/oauth.py`
- Modify if still unused: `travis/ai/model_resolver.py`
- Modify if still unused: `travis/ai/types.py`
- Modify extracted chat transport if still present: remove
  `openrouter_min_coding_score`
- Modify public `__init__`/`__all__` modules only as required
- Modify: `travis/coding_agent/artifact_gc.py`
- Modify: `tests/test_artifact_gc.py`

- [ ] **Step 1: Add failing dead/dormant policy tests**

Tests assert:

- no runtime/test/script reference exists for each proposed dead symbol except the policy
  test itself;
- importing supported package roots never promises those unexported symbols;
- `ArtifactGarbageCollector` has no startup, session-construction, shutdown, or background
  caller;
- its module docstring links the explicit retention document;
- collection remains explicit, locked, fail-closed, and dry-run capable;
- no configuration enables automatic GC.

- [ ] **Step 2: Re-run evidence after Phase 3**

Use AST/reference searches, package `__all__`, clean-wheel import inspection, and docs.
Apply these decisions:

- delete `oauth_credential_is_expired` and its now-empty module if still unreferenced;
- delete `ModelRegistryLike` if still unreferenced;
- delete `ProviderResponse` if still unreferenced and absent from serialized/public APIs;
- remove the unused `openrouter_min_coding_score` named parameter while preserving
  forward-compatible `**kwargs` behavior;
- retain `ProviderTransport` because Phase 3 makes it the real static boundary;
- retain artifact GC only as an explicit SDK maintenance primitive documented as
  nonautomatic. Do not add a startup/shutdown call or CLI deletion command in this
  refactor.

- [ ] **Step 3: Run focused compatibility tests**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/architecture/test_dead_code_policy.py \
  tests/test_artifact_gc.py \
  tests/test_ai_model_resolver.py \
  tests/test_ai_types.py \
  tests/ai/providers/test_provider_contracts.py \
  tests/test_distribution_contract.py
```

- [ ] **Step 4: Commit**

Stage exact paths only and commit:

```text
refactor(hygiene): remove proven dead ai symbols
docs(artifacts): define explicit retention ownership
```

---

## Task 4.4: Split cliff-edge test owners without changing assertions

**Files:**

- Split: `tests/test_tui_commands_and_extensions.py`
- Split: `tests/test_reference_runtime_contract.py`
- Split: `tests/test_coding_tools_and_subagents.py`
- Split: `tests/test_coding_resources_and_services.py`
- Split: `tests/test_agent_loop.py` by moving non-loop fixtures/contracts only; do not
  alter protected expectations
- Modify: `scripts/check_repository_hygiene.py`
- Modify: `tests/architecture/test_repository_hygiene.py`

- [ ] **Step 1: Add a failing 2,000-line test-owner gate**

Lower the oversized test threshold from 2,500 to 2,000. The five named files should fail
before splitting.

- [ ] **Step 2: Split by domain, preserving node IDs where practical**

Use these target owners:

- TUI built-in commands, extension commands/widgets, and session commands;
- reference provider contracts, session/runtime contracts, and package contracts;
- subagent tools, result expansion/artifacts, and control/supervision;
- resource loading, capability projection, and packaged skills;
- agent-loop fixtures/helpers versus core ordering/budget tests.

Move shared fixtures to narrowly named `tests/_support_*.py` modules. Do not create a
global mega-fixture or change assertions while moving.

- [ ] **Step 3: Prove collection and outcomes**

Before moving, record `pytest --collect-only -q` node count for each source file. After
moving, prove the aggregate node count is identical and run every target file. Then run
the full suite.

- [ ] **Step 4: Commit each source-file split separately**

Use explicit messages: `test(ownership): split tui command tests`,
`test(ownership): split reference runtime tests`,
`test(ownership): split subagent tests`,
`test(ownership): split resource tests`, and
`test(ownership): split agent loop fixtures`.

---

## Task 4.5: Canonicalize packaged skill and role mirrors

**Files:**

- Create: `scripts/sync_packaged_resources.py`
- Create: `tests/test_packaged_resource_mirrors.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_distribution_contract.py`
- Keep canonical: `travis/resources/skills/**`, `travis/resources/roles/**`
- Generated mirrors: `packages/travis234-cli/skills/**`,
  `packages/travis234-cli/roles/**`

- [ ] **Step 1: Add failing mirror-manifest tests**

Define an explicit source-to-destination manifest. Tests require:

- exactly the expected files on both sides;
- byte-identical content;
- no symlinks;
- `--check` exits nonzero with a concise path-only diff when a mirror drifts;
- `--write` updates only manifest destinations using atomic replacement;
- no secret/environment expansion occurs;
- source remains the Python packaged tree.

- [ ] **Step 2: Implement and test sync/check**

The script uses only standard library paths and writes. It never deletes an unlisted
destination automatically; unexpected files fail and require deliberate review.

```bash
uv run --locked --all-extras --dev python scripts/sync_packaged_resources.py --check
uv run --locked --all-extras --dev pytest -q \
  tests/test_packaged_resource_mirrors.py \
  tests/test_distribution_contract.py \
  tests/test_coding_resources_and_services.py
```

- [ ] **Step 3: Add CI check and commit**

```bash
git add scripts/sync_packaged_resources.py \
  tests/test_packaged_resource_mirrors.py \
  tests/test_distribution_contract.py .github/workflows/ci.yml
git commit -m "build(resources): enforce canonical packaged mirrors"
```

Skill prose and behavior must remain byte-identical through this task.

---

## Task 4.6: Add reproducible security and dependency gates

**Files:**

- Modify: `pyproject.toml`, `uv.lock`
- Create: `tests/architecture/test_security_configuration.py`
- Modify: `.github/workflows/ci.yml`
- Modify only concrete findings with regression tests

- [ ] **Step 1: Lock security tooling**

Add compatible bounded versions of Bandit and pip-audit to the dev group and update the
lock. Test that CI runs Bandit for high-severity findings and audits the locked environment
without exposing environment variables.

- [ ] **Step 2: Run and classify findings**

```bash
uv run --locked --all-extras --dev bandit -r travis -lll -f json
uv run --locked --all-extras --dev pip-audit --strict
```

Parameterized SQL and deliberate subprocess execution require narrow inline comments
at their exact call sites if Bandit reports them; do not globally skip rule families.
Any real high-severity issue gets a failing regression first.

- [ ] **Step 3: Add stable CI gates**

Bandit high-severity is a normal source gate. Dependency audit runs in source CI when
the advisory service is available and in a scheduled read-only workflow; tool/network
failure is reported distinctly from “no vulnerabilities.” Do not describe an unavailable
audit as passed.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock .github/workflows/ci.yml \
  tests/architecture/test_security_configuration.py
git commit -m "ci(security): add reproducible high severity gates"
```

---

## Task 4.7: Phase 4 qualification

- [ ] Run master checkpoint, full root suite, separate adapter suite, coverage floors,
  complete configured Ruff/Pyright, complexity, repository hygiene, resource mirror,
  Bandit, npm, and package build checks.
- [ ] Install the exact wheel and run TUI scenarios proving prompts, tools, commands,
  built-in skills, subagents, provider selection, and error text did not change during
  static/complexity cleanup.
- [ ] Compare default system prompt and every built-in tool schema to the Phase 3
  characterized snapshots. Expected: no semantic change; this refactor does not need new
  model guidance.
- [ ] Record remaining explicitly deferred legacy diagnostic counts; every count must be
  lower than or equal to Phase 0 and all migrated owners must be clean.
- [ ] Record artifact GC as explicit/dormant and prove it never ran.
- [ ] Commit as `docs: record phase 4 quality qualification`.
- [ ] Report and stop for review before Phase 5.
