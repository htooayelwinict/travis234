# Phase 5 — Performance, Packaging, Documentation, and Final Qualification

> **Required skills:** `superpowers:executing-plans`,
> `superpowers:test-driven-development`, `superpowers:systematic-debugging`,
> `superpowers:requesting-code-review`, and
> `superpowers:verification-before-completion`.

**Goal:** Improve measured startup/catalog performance, make builds and documentation
match reality, perform complete source/wheel/TUI/container qualification, and produce a
reviewable final refactor handoff without publishing anything.

**Architecture:** Metadata/help paths avoid application composition. Runtime dependency
resolution is locked. The final wheel—not the source checkout—is the TUI qualification
subject. Container smoke occurs once at the end.

**Do not modify:** `travis/agent/agent_loop.py`, versions, release tags, remote branches,
package registries, GHCR, PyPI, npm, user credentials, or user state.

---

## Task 5.1: Establish repeatable startup and memory benchmarks

**Files:**

- Create: `benchmarks/startup.py`
- Create: `tests/test_startup_benchmark.py`
- Modify: `docs/verification/contract-first-refactor.md`

- [ ] **Step 1: Define the benchmark schema and tests**

Measure at least seven fresh subprocess samples for:

- `python -c 'import travis'`;
- `python -c 'import travis.cli'`;
- installed `travis234 --help` with an empty agent directory;
- installed `travis234 --help` with configured extension resources;
- peak RSS for help and a faux print-mode turn.

The JSON schema records Python/platform/machine, sample list, median, median absolute
deviation, and peak RSS. It contains no absolute home paths, environment values, model
credentials, or extension source text.

- [ ] **Step 2: Run tests and the pre-optimization baseline**

```bash
uv run --locked --all-extras --dev pytest -q tests/test_startup_benchmark.py
uv run --locked --all-extras --dev python benchmarks/startup.py \
  --rounds 7 --json
```

Record summarized medians/RSS in the verification ledger, not the raw environment.

- [ ] **Step 3: Commit the benchmark only**

```bash
git add benchmarks/startup.py tests/test_startup_benchmark.py \
  docs/verification/contract-first-refactor.md
git commit -m "test(performance): benchmark cli startup paths"
```

---

## Task 5.2: Make root exports lazy without breaking imports

**Files:**

- Modify: `travis/__init__.py`
- Create: `tests/test_lazy_package_exports.py`
- Modify: `tests/test_distribution_contract.py`
- Modify: `pyrightconfig.json`, `ruff.toml`

- [ ] **Step 1: Add failing clean-process import tests**

Assert plain `import travis` does not load `travis.app`, TUI modules, session construction,
or providers. Assert these still work and return the same objects:

```python
from travis import AgentHarness, AgentHarnessConfig, CodingApp
```

Assert `dir(travis)` and `travis.__all__` list all three names and unknown attributes
raise normal `AttributeError`.

- [ ] **Step 2: Confirm RED**

```bash
uv run --locked --all-extras --dev pytest -q tests/test_lazy_package_exports.py
```

- [ ] **Step 3: Implement PEP 562 lazy compatibility exports**

Use `TYPE_CHECKING` imports, a constant name-to-module/attribute mapping, and
`__getattr__`. Cache resolved objects in module globals. Do not catch import errors or
return proxy objects.

- [ ] **Step 4: Verify imports and benchmark**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/test_lazy_package_exports.py tests/test_distribution_contract.py
uv run --locked --all-extras --dev python benchmarks/startup.py --rounds 7 --json
```

- [ ] **Step 5: Commit**

```bash
git add travis/__init__.py tests/test_lazy_package_exports.py \
  tests/test_distribution_contract.py pyrightconfig.json ruff.toml
git commit -m "perf(imports): lazily expose application roots"
```

---

## Task 5.3: Defer application composition for metadata/help paths

**Files:**

- Create: `travis/cli_bootstrap.py`
- Create: `travis/cli_runtime.py`
- Modify: `travis/cli.py`
- Create: `tests/test_cli_startup_paths.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_cli_extension_flags.py`
- Modify: `tests/test_cli_runtime_controls.py`

- [ ] **Step 1: Add failing module-load and exact-help tests**

In fresh subprocesses, assert:

- plain `--help` does not import `travis.app`, session runtime, TUI, model catalogs, or
  extension execution;
- help output and exit codes match the pinned baseline;
- explicit `--extension path.py --help` still loads only the authorized extension flag
  schema and displays it;
- project extension code is not loaded merely for plain help before trust;
- package-management subcommands continue dispatching before agent construction;
- invalid extension-help configuration disposes any created extension runtime.

- [ ] **Step 2: Confirm RED**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/test_cli_startup_paths.py \
  tests/test_cli_extension_flags.py -k help
```

- [ ] **Step 3: Split bootstrap parsing from runtime imports**

`cli_bootstrap.py` owns only argparse definitions, primitive path/string parsers, and
metadata dispatch. Heavy model/app/session/TUI imports move behind the point at which a
runtime command is known. Explicit extension-aware help follows the existing trust and
cleanup path; plain help never discovers project code.

Do not create a second CLI option definition. Runtime and bootstrap parsers consume the
same declarative option builders so help cannot drift.

- [ ] **Step 4: Verify and benchmark**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/test_cli_startup_paths.py tests/test_cli.py \
  tests/test_cli_extension_flags.py tests/test_cli_runtime_controls.py
uv run --locked --all-extras --dev python benchmarks/startup.py --rounds 7 --json
```

Compare medians. No measured path may regress more than 10% outside benchmark variance;
at least one import/help median should improve by 20% or more. If not, retain only
changes justified by clearer ownership and record the result honestly.

- [ ] **Step 5: Commit**

```bash
git add travis/cli.py travis/cli_bootstrap.py travis/cli_runtime.py \
  tests/test_cli_startup_paths.py tests/test_cli.py \
  tests/test_cli_extension_flags.py tests/test_cli_runtime_controls.py \
  docs/verification/contract-first-refactor.md
git diff --cached --name-only
git commit -m "perf(cli): defer application startup for metadata paths"
```

---

## Task 5.4: Make Python and container inputs reproducible

**Files:**

- Create: `requirements.lock`
- Modify: `Dockerfile.release`
- Modify: `Dockerfile`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/travis234-release-image.yml`
- Modify: `tests/test_release_workflow.py`
- Modify: `tests/test_pyproject_dependencies.py`
- Modify: `tests/test_distribution_contract.py`

- [ ] **Step 1: Add failing reproducibility tests**

Require:

- `uv lock --check` succeeds;
- `requirements.lock` is generated from root `uv.lock`, contains hashes, excludes dev
  tools and the local project, and contains no local paths/credentials;
- CI and release workflows use locked sync;
- Dockerfiles do not upgrade pip dynamically;
- release installation uses the prebuilt project wheel with `--no-deps` after installing
  hashed runtime requirements;
- Python and Node base image arguments include immutable `sha256:` digests;
- runtime image remains unprivileged and preserves required tools.

- [ ] **Step 2: Generate the runtime lock**

```bash
uv export --locked --no-dev --no-emit-project --all-extras \
  --format requirements-txt --output-file requirements.lock
```

Inspect the file before staging. Resolve current multi-architecture Python 3.13 slim and
Node 20 bookworm-slim manifest digests with Docker Buildx, record them as overridable
`ARG` defaults, and capture the resolution command/digests in the verification ledger.

- [ ] **Step 3: Build the wheel in a builder stage**

Use the locked toolchain to build the root wheel once. The runtime stage installs
`requirements.lock` with hash verification and the exact copied wheel with `--no-deps`.
Do not run tests or install pytest in the production runtime image unless the existing
container qualification contract explicitly requires it; if it does, move test-only
requirements to a qualification target rather than production.

- [ ] **Step 4: Verify package and Dockerfile contracts without building the container**

```bash
uv lock --check
phase5_dist="$(mktemp -d /tmp/travis234-refactor-phase5-dist.XXXXXX)"
uv build --out-dir "$phase5_dist" .
uv run --locked --all-extras --dev twine check "$phase5_dist"/*
uv run --locked --all-extras --dev pytest -q \
  tests/test_release_workflow.py \
  tests/test_pyproject_dependencies.py \
  tests/test_distribution_contract.py
```

The actual container build remains deferred to Task 5.9.

- [ ] **Step 5: Commit**

```bash
git add requirements.lock Dockerfile Dockerfile.release \
  .github/workflows/ci.yml .github/workflows/travis234-release-image.yml \
  tests/test_release_workflow.py tests/test_pyproject_dependencies.py \
  tests/test_distribution_contract.py
git commit -m "build: lock python and container inputs"
```

---

## Task 5.5: Reconcile README, rules, architecture, and verification truth

**Files:**

- Major update: `README.md`
- Modify: `packages/travis234-cli/README.md`
- Modify: `packages/travis234-mcp-adapter/README.md`
- Modify: `rules.md`
- Create: `docs/architecture/session-composition.md`
- Create: `docs/architecture/tui-composition.md`
- Modify: `docs/architecture/provider-control-plane.md`
- Modify: `docs/architecture/contract-parity.md`
- Modify: `docs/verification/full-suite.md`
- Modify: `docs/verification/acceptance-matrix.md`
- Modify: `tests/test_brand_contract.py`
- Modify: `tests/test_distribution_contract.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_cli_extension_flags.py`
- Modify: `tests/test_pyproject_dependencies.py`
- Modify: `tests/architecture/test_acceptance_matrix.py`
- Modify: `tests/architecture/test_repository_hygiene.py`

- [ ] **Step 1: Add failing documentation truth tests**

Check that every referenced repository path exists, every local verification command is
the supported locked/uv command, policy directory names are singular/correct, obsolete
`travis/ai/stream.py` is absent from rules, and documented package/TUI/container entry
points match tests.

- [ ] **Step 2: Major README reconciliation**

Explain all current user capabilities in plain language before internals:

- installation/startup/login/model selection;
- sessions, compaction, generation parameters, themes/motion;
- tools, safe editing, managed processes, tmux;
- built-in lazy skills including `/coordination` and orchestration;
- in-process subagents, typed roles, result expansion, supervision;
- artifacts, policy approvals, LSP, memory, operations;
- extensions, packages, generic MCP adapter;
- print/JSON/RPC and Python SDK;
- security/trust/state/credential boundaries;
- accurate development, wheel, TUI, and container verification.

Describe the new collaborator architecture only in the contributor section. Do not add
internal refactor concepts to system prompts, tool descriptions, or normal-user steps.

- [ ] **Step 3: Correct architecture/rules ownership**

Document explicit session/TUI collaborators, provider family boundaries, compatibility
façades, protected loop, static-analysis scopes, and why `RuntimeFacade` remains. Mark
the old mixin design as historical, not current.

- [ ] **Step 4: Run documentation contracts**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/test_brand_contract.py \
  tests/test_distribution_contract.py \
  tests/test_cli.py -k readme \
  tests/test_cli_extension_flags.py -k readme \
  tests/architecture/test_acceptance_matrix.py \
  tests/architecture/test_repository_hygiene.py
uv run --locked --all-extras --dev python scripts/sync_packaged_resources.py --check
```

- [ ] **Step 5: Commit by documentation owner**

Use separate README/user-guide and architecture/verification commits.

---

## Task 5.6: Build and validate all distribution artifacts

**Files:** No production edits expected; defects start with a failing packaging test.

- [ ] **Step 1: Build from a clean archive of the current commit**

Create explicit temporary directories, export the committed tree, and run:

```bash
qualification_root="$(mktemp -d /tmp/travis234-refactor-clean.XXXXXX)"
mkdir -p "$qualification_root/source" "$qualification_root/root" \
  "$qualification_root/adapter"
git archive HEAD | tar -x -C "$qualification_root/source"
uv build --out-dir "$qualification_root/root" "$qualification_root/source"
uv build --out-dir "$qualification_root/adapter" \
  "$qualification_root/source/packages/travis234-mcp-adapter"
uv run --locked --all-extras --dev twine check \
  "$qualification_root"/root/* "$qualification_root"/adapter/*
npm --prefix "$qualification_root/source/packages/travis234-cli" test
npm --prefix "$qualification_root/source/packages/travis234-cli" run pack:dry-run
```

Do not substitute the dirty worktree for the clean archive.

- [ ] **Step 2: Inspect artifacts**

Verify names and package metadata remain current (no bump), required resources are present, ignored
research/worktree/.env/state files are absent, Ghost remains absent, and generic MCP
remains present only in its separate package.

- [ ] **Step 3: Clean-wheel smoke**

Install each wheel into its own fresh Python 3.13 environment. Run `pip check`, public
imports, console help, installed metadata inspection, packaged skills/themes/roles,
faux print/JSON/RPC turns, and adapter distribution/import tests.

- [ ] **Step 4: Record hashes and results**

Record artifact SHA-256 values in the verification ledger. Do not publish them.

---

## Task 5.7: Actual installed-wheel TUI regression matrix

**Files:**

- Create: `evals/refactor_tui_scenarios.json`
- Modify: `docs/verification/contract-first-refactor.md`
- Production edits only after a reproduced defect and failing regression test

- [ ] **Step 1: Define 21 normal-user prompts focused on refactored behavior**

The matrix covers:

1. help and capability discovery;
2. plain read-only repository question;
3. read/edit/verify workflow;
4. model selection;
5. generation parameters;
6. session new/resume;
7. fork/clone;
8. manual compaction;
9. extension command/reload;
10. managed process lifecycle;
11. tmux lifecycle;
12. tool approval;
13. LSP status/read;
14. memory status with no automatic retention;
15. operation inspection;
16. in-process subagent delegation;
17. subagent result expansion;
18. `/coordination --plan`;
19. orchestration skill discovery without forced dispatch;
20. cancellation/steering/follow-up;
21. final resume and clean shutdown.

Prompts use ordinary nontechnical language and explicitly avoid publication or unrelated
destructive changes.

- [ ] **Step 2: Launch the actual installed console entry in a real PTY**

Use the exact clean-installed root wheel, a fresh workspace, and a fresh
`TRAVIS234_CODING_AGENT_DIR`. Start the `travis234` console entry through a real attached
PTY using the execution tool's TTY/session support. Do **not** substitute
`python -m travis.cli`, fake terminal tests, `evals.tui_driver`, or a scripted prompt
runner.

If the repository's ignored `.env` is present and the configured provider is available,
pass it by path without reading or printing it and select
`openrouter/minimax/minimax-m3` for the live matrix. If provider authentication is
unavailable, record the external block and run the same matrix against the offline faux
provider for runtime qualification; do not call a blocked live run passed.

- [ ] **Step 3: Report every prompt immediately**

For each prompt record PASS/FAIL, final state, tool/command path, session identity,
process/subagent cleanup, and whether any defect is runtime, provider, model quality, or
environment. Never retain raw credentials or unredacted terminal logs.

- [ ] **Step 4: Root-cause and fix every runtime defect**

For each defect:

1. preserve the smoking-gun evidence;
2. use systematic debugging to trace the first wrong state transition;
3. add a failing automated regression;
4. make the smallest fix;
5. rerun the focused test and the affected actual-TUI prompt;
6. rerun all previously passed dependent prompts.

Continue until all offline runtime scenarios pass. Model-quality weaknesses are reported
separately and do not justify changing runtime behavior without evidence.

---

## Task 5.8: Complete local source qualification

- [ ] Run, from clean state:

```bash
uv lock --check
uv sync --locked --all-extras --dev
uv run --locked --all-extras --dev ruff check --select E9,F63,F7,F82 travis tests
uv run --locked --all-extras --dev ruff check \
  travis/coding_agent/session_*.py travis/coding_agent/agent_session.py \
  travis/coding_agent/session_contracts.py travis/coding_agent/session_composition.py \
  travis/coding_agent/session_options.py travis/tui travis/ai/providers
uv run --locked --all-extras --dev pyright
uv run --locked --all-extras --dev python scripts/check_repository_hygiene.py
uv run --locked --all-extras --dev python scripts/sync_packaged_resources.py --check
uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests
uv run --locked --all-extras --dev pytest -q -p no:cacheprovider \
  packages/travis234-mcp-adapter/tests
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
```

- [ ] Run coverage floors, complexity, Bandit, dependency audit, clean builds, Twine,
  clean-wheel smokes, acceptance strict-current evidence, and the 21 actual-TUI matrix.
- [ ] Confirm
  `git diff --exit-code 7838749452b567940bd5b69a715b6184b8f9f13e -- travis/agent/agent_loop.py`
  and the protected SHA.
- [ ] Confirm no owned process, tmux test session, subagent, temporary worktree, or
  credential-bearing test state remains.

Do not claim final completion yet; container qualification remains.

---

## Task 5.9: Final container qualification — first and only container phase

**Files:** Production changes only through failing container regression tests.

- [ ] **Step 1: Build the release image once from the verified tree**

```bash
docker build --no-cache -f Dockerfile.release \
  -t travis234:contract-first-refactor .
```

- [ ] **Step 2: Run root image smoke and qualification**

```bash
uv run --locked --all-extras --dev python evals/container_smoke.py \
  --image travis234:contract-first-refactor
uv run --locked --all-extras --dev python evals/container_qualification.py \
  --image travis234:contract-first-refactor --require-container
```

Verify unprivileged user, isolated home/state, required CLI tools, help, faux
print/JSON/RPC/TUI, trust gating, session/compaction, process cleanup, extension flags,
and zero credential forwarding.

- [ ] **Step 3: Build the adapter test image from the exact wheel**

Use `packages/travis234-mcp-adapter/Dockerfile.smoke` with the exact Phase 5 root/adapter
artifacts. Run `evals/mcp_container_smoke.py`. Do not add the adapter to the root release
image and do not publish either tag.

- [ ] **Step 4: Diagnose any failure through RED/GREEN**

Add a failing source/container regression before changing Dockerfiles or runtime. Rebuild
only after the focused source test passes, then rerun both container smokes.

---

## Task 5.10: Final evidence and code review

- [ ] Update `docs/verification/contract-first-refactor.md` with exact pass counts,
  timings, coverage, complexity, type/lint counts, startup before/after, artifact hashes,
  21 prompt outcomes, container results, and protected hash.
- [ ] Update acceptance evidence for the exact current commit; external/live blocks stay
  blocked rather than passed.
- [ ] Run `git diff --check`, inspect every changed file, and verify no `.env`, state,
  test transcript, build output, benchmark JSON, or ignored research tree is staged.
- [ ] Request an independent final code review focused on behavioral parity, provider
  wire fixtures, session/TUI lifecycle, security, dead code, packaging, and the protected
  loop.
- [ ] Address review findings through regression-first fixes and rerun affected gates.
- [ ] Commit final evidence as `test(qualification): record contract-first refactor`.
- [ ] Report the final verified commit and remaining risks to the coordinating agent.

Stop. Do not merge, push, version, tag, publish, or promote an image.
