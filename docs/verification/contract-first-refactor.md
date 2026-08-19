# Travis234 Contract-First Refactor Verification

This ledger records summarized, credential-free evidence for the approved contract-first
refactor. Commands run from the assigned Orca worktree unless noted otherwise. Generated
coverage data, build artifacts, temporary environments, and TUI state are not retained in
Git.

## Planning baseline

- Planning commit: `e60d83478d5935bb85d499eb7a91c62818efe684`
- Reference commit: `7838749452b567940bd5b69a715b6184b8f9f13e`
- Branch: `htooakalewis/contract-first-refactor`
- Environment: macOS 26.5.2 (`Darwin 25.5.0 arm64`), Python 3.13.13,
  pytest 9.1.1, uv 0.11.24, Node.js 26.4.0, npm 11.17.0, Git 2.50.1
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`
- Protected-loop diff from `7838749452b567940bd5b69a715b6184b8f9f13e`: empty

Baseline commands and summarized outcomes:

- `PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest -q -p no:cacheprovider tests`
  — 2,650 passed in 306.20s (308.22s wall time).
- `npm --prefix packages/travis234-cli test` — 24 passed in 1.45s
  (1.93s wall time).
- `npm --prefix packages/travis234-cli run pack:dry-run` — passed in 0.50s;
  11 package files.

## Phase 0 — Truthful guardrails and confirmed regression

### Task 0.1 — Protected runtime characterization

- Commit: `a7db0d01d18ced44fee4b0cd468a1f9139462efe`
- RED command and expected failure: not applicable; these are characterization tests
  added against unchanged production behavior and must pass before later moves.
- Focused GREEN command:
  `PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest -q tests/test_runtime_facade_contract.py tests/coding_agent/test_agent_session_characterization.py tests/tui/test_interactive_dispatch_characterization.py tests/tui/test_interactive_shutdown_characterization.py tests/ai/providers/test_provider_characterization.py`
  — 31 passed in 2.64s (3.46s wall time).
- Phase suite command: completed; see Task 0.6.
- Installed-wheel TUI scenario: completed; see Task 0.6.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`
- Notes/remaining risks: later composition work must preserve these contracts; Phase 0
  does not begin that work.

### Task 0.2 — Runtime Python PATH alias regression

- Commit: `6d394b248f4cc6c18af6d91b25e707f58243947a`
- RED command and expected failure:
  `PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest -q tests/test_coding_exports_and_boundaries.py -k runtime_python_bin_through_path_alias`
  — failed as expected because the canonical `real-runtime/bin` entry remained in
  `PATH` (1 failed, 42 deselected in 2.53s; 3.40s wall time).
- Focused GREEN command: the same exact selector — 1 passed, 42 deselected in 0.07s
  (0.77s wall time).
- Surrounding GREEN command:
  `PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest -q tests/test_coding_exports_and_boundaries.py tests/test_subprocess_environment.py tests/test_process_context.py`
  — 56 passed in 4.21s (5.01s wall time).
- Phase suite command: completed; see Task 0.6.
- Installed-wheel TUI scenario: completed; see Task 0.6.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`
- Notes/remaining risks: the fix canonicalizes the apparent executable parent while
  preserving the system-Python early return and resolved-entry comparison.

### Task 0.3 — Locked quality toolchain and monotonic scopes

- Commits:
  - `171f7c2aba8c1e9b838650670afabad5d8dbf4d7` — resolve the CLI model-list
    helper's runtime type hint through a regression-first import fix.
  - `fcfecc76f5b7a3ed7fadf4012f50634ca9e04053` — lock the development
    toolchain and add scoped Ruff/Pyright gates.
- RED command and expected failure:
  `PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest -q tests/architecture/test_quality_configuration.py`
  — 5 failed as expected for the ignored/untracked lock, missing development group,
  absent Pyright configuration, absent Ruff configuration, and absent migrated-owner
  lint gate (0.35s; 1.34s wall time).
- Additional CLI RED:
  `uv run --locked --all-extras --dev pytest -q tests/test_cli.py -k registered_models_helper_type_hints_resolve`
  — failed with `NameError: Iterable` as expected (1 failed, 49 deselected in 0.25s;
  1.17s wall time).
- Focused GREEN commands:
  - the CLI selector — 1 passed, 49 deselected in 0.12s (0.92s wall time);
  - `uv run --locked --all-extras --dev pytest -q tests/test_cli.py` — 50 passed in
    2.88s (3.71s wall time);
  - `uv run --locked --all-extras --dev pytest -q tests/architecture/test_quality_configuration.py`
    — 5 passed in 0.27s (1.02s wall time).
- Toolchain gates:
  - `uv lock --check` — 65 packages resolved from the committed lock in 30ms;
  - `uv run --locked --all-extras --dev ruff check --select E9,F63,F7,F82 travis tests`
    — passed in 0.04s;
  - `uv run --locked --all-extras --dev pyright` — 0 errors, 0 warnings, 0
    informations in 1.09s.
- Lock safety: no credential-bearing URL, absolute workspace reference, or external
  local source; the sole non-registry source is the expected root
  `travis234` `{editable = "."}` entry.
- Named exact-file `F821` deferrals:
  - `CF-P1-session-facade-cycle` — `travis/coding_agent/subagent_trace.py` uses
    `AgentSession` annotations but importing the façade back into its composed owner
    would preserve the cycle Phase 1/2 removes.
  - `CF-P2-tui-facade-cycle` — `travis/tui/footer_data.py` and
    `travis/tui/interactive_extensions.py` use `InteractiveMode` annotations but
    importing the façade back would preserve the TUI composition cycle Phase 2 removes.
  These are exact-file `F821` entries only; repository-wide syntax and undefined-name
  rules remain enabled.
- Phase suite command: completed; see Task 0.6.
- Installed-wheel TUI scenario: completed; see Task 0.6.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`
- Notes/remaining risks: the Pyright include list is intentionally scoped to current
  contract/config owners and must only expand in later phases.

### Task 0.4 — Truthful acceptance evidence and source CI

- Commit: `2e9cd0a7f0037293a1dac124cc92ded6b63da081`
- RED command and expected failure:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/architecture/test_acceptance_matrix.py`
  — 9 failed and 2 passed in 1.15s as expected because matrix rows had no evidence
  class, current-commit verification treated live/manual rows as automated, the
  recorder was absent, and malformed class data was accepted.
- Focused GREEN command:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/architecture/test_acceptance_matrix.py tests/test_ci_workflow.py tests/test_release_workflow.py`
  — 29 passed in 1.84s (2.78s wall time).
- Current-commit evidence smoke: a task-owned temporary file recorded all 20
  `automated-required` rows and strict verification passed for the current commit;
  parity remained Pi 78 / Hermes 11, while one blocked and one pending
  `live-required` row and one passed `manual` row were reported separately. The
  temporary file was removed and was not tracked.
- Quality gates: fatal Ruff passed; scoped Pyright reported 0 errors, 0 warnings, and
  0 informations.
- Phase suite command: completed; see Task 0.6.
- Installed-wheel TUI scenario: completed; see Task 0.6.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`
- Notes/remaining risks: source-workflow semantics are locally contract-tested; the
  workflow has not run on a remote GitHub runner. Live and manual acceptance remain
  explicitly outside the automated source gate.

### Task 0.5 — Independent statement and branch coverage floors

- Commit: `11bdcc8307430d08afba1b1bcebec4332efb7e93`
- Checker RED command and expected failure:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/test_coverage_floor.py`
  — 4 failed in 0.25s (2.70s wall time) because the coverage-floor checker did not
  exist.
- Source-workflow RED selector:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/test_ci_workflow.py -k reproducible_statement_and_branch_coverage`
  — failed as expected because the workflow had no coverage pipeline (1 failed,
  4 deselected in 0.19s).
- Root-environment regression RED: the first real covered suite stopped during
  collection because `tests/test_contract_parity_benchmark.py` imports the optional
  MCP adapter while the root lock intentionally does not own `mcp` (1 collection
  error in 2.71s; 5.06s wall time). A failing workflow contract was added before the
  narrow fix; the root coverage command now uses the same ephemeral adapter `--with`
  layer as release qualification, while adapter source tests remain in their own
  locked project.
- Focused GREEN command:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/test_coverage_floor.py tests/test_ci_workflow.py`
  — 9 passed in 0.79s (1.81s wall time).
- Real clean coverage command:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. uv run --locked --all-extras --dev --with "./packages/travis234-mcp-adapter" coverage run -m pytest -q -p no:cacheprovider tests`
  — 2,686 passed in 588.94s (597.36s wall time).
- Measured coverage from this 2,686-test pre-correction run after combining the
  single parallel-safe data file:
  37,412 / 44,559 statements = 83.96%; 10,369 / 15,132 branches = 68.52%.
  The independent checker passed the approved 83.0% statement and 68.0% branch
  floors without changing exclusions or lowering either floor.
- Generated `.coverage` data and `coverage.json` were removed after the summarized
  counts were recorded.
- Phase suite command: completed; see Task 0.6.
- Installed-wheel TUI scenario: completed; see Task 0.6.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`
- Notes/remaining risks: coverage timing is materially slower than the ordinary root
  suite. Adapter behavior is still qualified independently; the root-only ephemeral
  layer exists solely for the benchmark contract that imports its public types.

### Task 0.6 — Phase 0 qualification and installed-wheel TUI

- Qualification corrections discovered test-first:
  - `a0be864ffe9d48d1e129e7a69544452907dc126a` makes the migrated-owner
    quality contract invoke Ruff through the locked uv toolchain. The first pinned
    full-suite run proved the regression with 1 failure and 2,685 passes in 321.97s
    (324.05s wall): the planning interpreter correctly remained unchanged and did not
    contain the newly locked Ruff package. The isolated RED selector failed with
    1 failure / 4 deselected in 1.96s; after the test-only fix it passed with
    1 pass / 4 deselected in 0.36s (1.27s wall), and all 5 quality-configuration
    tests passed in 2.50s.
  - `6695c5e7a6b96f1d14159014939fa91a256252bb` adds a non-published adapter
    `source-test` dependency group and locks the editable local Travis234 host in the
    adapter's own environment. The first isolated adapter run produced 11 collection
    errors in 4.57s (6.78s wall) because its previous test extra lacked the host and
    host runtime dependencies. A new workflow contract then failed as expected with
    1 failure / 5 deselected in 0.14s. The locked adapter suite passed 125 tests in
    16.25s (16.81s wall), and the focused workflow/package/release group passed
    37 tests in 8.65s (10.24s wall). Published adapter dependencies are unchanged.
- Final root checkpoint:
  `PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest -q -p no:cacheprovider tests`
  — 2,687 passed in 385.00s (386.75s wall time). This is 37 Phase 0 tests above
  the 2,650-test planning baseline.
- Adapter checkpoint:
  `uv run --project packages/travis234-mcp-adapter --locked --group source-test pytest -q -p no:cacheprovider packages/travis234-mcp-adapter/tests`
  — 125 passed in 16.25s (16.81s wall time).
- npm launcher: 24 passed in 1.17s (1.76s wall time).
- npm dry-pack: passed in 1.04s wall time with the expected 11-file inventory.
- Package qualification:
  - root wheel and sdist built in 6.16s wall time;
  - adapter wheel and sdist built in 2.76s wall time;
  - Twine passed all four artifacts in 5.05s wall time.
- Installed-wheel qualification used the exact root wheel in a fresh Python 3.13.13
  environment, an isolated `TRAVIS234_CODING_AGENT_DIR`, the actual installed
  `travis234` console entry, an attached PTY, `--offline`, and an in-memory faux
  provider supplied by a task-owned global extension. No dotenv or live provider was
  used. Scenario outcomes:
  1. **PASS — help:** the installed console displayed complete help and exited 0.
  2. **PASS — normal read-only prompt:** the faux provider returned
     `PHASE0_READ_ONLY_OK` and the TUI returned to Idle.
  3. **PASS — `/coordination --plan`:** the exact no-goal invocation produced the
     preserved `A coordination goal is required` validation error, made no provider
     or tool turn, and returned to Idle.
  4. **PASS — aliased runtime PATH:** the console ran through an interpreter path
     whose `bin` symlink resolved to the canonical installed environment `bin`, while
     the incoming `PATH` contained that canonical directory. The real bash tool exited
     0 with `RUNTIME_BIN_OMITTED`, proving the tool environment removed it.
  5. **PASS — clean shutdown:** `/exit` returned 0, displayed `status: Exiting`,
     restored the cursor and bracketed-paste terminal modes, and left no owned
     Travis234 or managed-process child.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`
- Protected-loop diff from `7838749452b567940bd5b69a715b6184b8f9f13e`:
  empty.
- Remaining risks: source CI semantics are locally proven but have not run on a
  remote GitHub runner. Live 21-prompt and public-repository evidence remain explicitly
  blocked/pending outside the automated source gate. Coverage qualification is
  materially slower than the ordinary suite. Phase 1 has not started.

### Phase 0 checkpoint correction — fully locked source coverage

- Review finding: the source-coverage command used uv's `--with` package layer. Live
  `uv run --help` identifies `--with` as adding packages while `--locked` only asserts
  that the project lock remains unchanged, so the adapter and MCP graph used by coverage
  was not governed by the committed root lock.
- Correction commits:
  - `d3f99f4d610bea09c10bafc6d1f9b6aed9e50b95` — add the root `coverage-test`
    dependency group, map `travis234-mcp-adapter` through `[tool.uv.sources]`, update
    the committed root lock, and sync/run that group under `--locked` in source CI.
  - `b31fb93021c81fc17b89969f259c15191bd9c82e` — require the adapter lock to remain
    current for its editable root host and refresh its single root-host group metadata
    row. No adapter package or dependency version changed.
- Primary RED command:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/test_ci_workflow.py -k 'source_ci_runs_reproducible_statement_and_branch_coverage or source_coverage_dependencies_are_owned_by_the_committed_root_lock'`
  — 2 failed and 5 deselected in 0.31s (1.81s wall): one failure found `--with` and
  the other found the missing `coverage-test` group.
- Primary GREEN selector: the same command — 2 passed and 5 deselected in 0.32s
  (3.44s wall).
- Secondary adapter-lock RED:
  `uv run --locked --all-extras --dev --group coverage-test pytest -q -p no:cacheprovider tests/test_ci_workflow.py -k adapter_source_tests_lock_the_local_host_in_their_own_group`
  — 1 failed and 6 deselected in 0.23s (1.31s wall) because the adapter lock's
  editable-root metadata did not yet contain the new group.
- Secondary adapter-lock GREEN: the same selector — 1 passed and 6 deselected in
  3.83s (5.09s wall). Both `uv lock --check` and
  `uv lock --project packages/travis234-mcp-adapter --check` pass at the final code
  commit, resolving 84 and 56 packages respectively.
- Published package boundary: `travis234-mcp-adapter` remains absent from root
  `[project].dependencies` and all root extras. Its local source exists only in the
  non-published `coverage-test` dependency group; no release-image file changed.
- Focused final workflow/config command:
  `uv run --locked --all-extras --dev --group coverage-test pytest -q -p no:cacheprovider tests/test_ci_workflow.py tests/architecture/test_quality_configuration.py tests/test_release_workflow.py tests/test_coverage_floor.py`
  — 30 passed in 8.89s (11.48s wall).
- Clean current-commit coverage command:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. uv run --locked --all-extras --dev --group coverage-test coverage run -m pytest -q -p no:cacheprovider tests`
  — 2,688 passed in 595.04s (598.02s wall). In this distinct post-correction,
  fully locked run, the independent checker passed 83.96% statement coverage and
  68.53% branch coverage against the unchanged 83.0% statement and 68.0% branch
  floors; the earlier 68.52% entry belongs to the separate 2,686-test
  pre-correction run recorded in Task 0.5.
- Final root checkpoint:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest -q -p no:cacheprovider tests`
  — 2,688 passed in 409.27s (410.99s wall).
- Adapter checkpoint:
  `uv run --project packages/travis234-mcp-adapter --locked --group source-test pytest -q -p no:cacheprovider packages/travis234-mcp-adapter/tests`
  — 125 passed in 27.22s (28.66s wall).
- npm launcher: 24 passed in 1.63s (2.70s wall). npm dry-pack passed in 2.38s
  wall time with the expected 11-file inventory.
- Static and acceptance gates: repository fatal Ruff passed in 0.15s; scoped Pyright
  reported 0 errors, warnings, or informations in 2.15s; current-commit acceptance
  passed with 20 automated results, Pi 78 / Hermes 11 parity, one blocked and one
  pending live-required row, and one passed manual row.
- Package qualification: the root wheel/sdist built in 5.83s and the adapter
  wheel/sdist in 3.06s; Twine passed all four artifacts in 1.35s.
- Installed-wheel qualification again used the actual installed `travis234` console
  from the final root wheel in real attached PTYs, a fresh Python 3.13.13 environment,
  isolated task-owned agent state, `--offline`, and an in-memory faux provider. No
  dotenv or live provider was used:
  1. **PASS — help:** the installed console displayed complete help and exited 0.
  2. **PASS — normal read-only prompt:** the faux provider returned
     `PHASE0_READ_ONLY_OK` and the TUI returned to Idle.
  3. **PASS — `/coordination --plan`:** the exact command produced
     `A coordination goal is required` and returned to Idle without a provider turn.
  4. **PASS — aliased runtime PATH:** a console whose interpreter shebang used an
     aliased environment path ran with the canonical environment `bin` in incoming
     `PATH`; the real bash tool selected `RUNTIME_BIN_OMITTED` and exited 0.
  5. **PASS — clean shutdown:** `/exit` exited 0, rendered `status: Exiting`, restored
     cursor and bracketed-paste terminal modes, and left no task-owned console process.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`.
- Protected-loop diff from `7838749452b567940bd5b69a715b6184b8f9f13e`: empty.
- Generated coverage files were erased after recording metrics. The task-owned build,
  install, extension, acceptance, and TUI directory is deleted after final checks.
- Remaining risks: source CI has still not run on a remote GitHub runner; live-provider
  and public-repository evidence remain blocked/pending by policy; clean coverage remains
  materially slow. No container was built, and Phase 1 has not started.

## Phase 1 — Explicit contracts and composition shell

### Task 1.1 — Supported façade inventory

- Commit: `9c3b7921d246e192a9c8cffb2ec62c4710488ae7`.
- RED command and expected failure:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/test_runtime_facade_contract.py tests/architecture/test_refactor_contracts.py tests/coding_agent/test_session_owner_boundaries.py tests/tui/test_interactive_owner_boundaries.py`
  — collection failed as expected because `session_contracts` and
  `interactive_contracts` did not exist.
- Focused GREEN command: the same command — 16 passed in 1.23s at the final Phase 1
  source commit. The contract modules expose immutable supported-member inventories and
  narrow structural ports without importing either concrete façade or composition root.
- Phase suite command: completed; see Task 1.6.
- Installed-wheel TUI scenario: completed; see Task 1.6.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`.
- Notes/remaining risks: private dynamic forwarding remains covered by existing
  compatibility tests and was not promoted into the supported public inventory.

### Task 1.2 — Typed session dependency composition

- Commit: `ae8a927a48e7c30e9fdd1eb1da3dd1ae777f78a8`.
- RED command and expected failure:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/coding_agent/test_session_composition.py`
  — collection failed as expected because `_build_session_dependencies` and the typed
  dependency record were absent.
- Focused GREEN command:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/coding_agent/test_session_composition.py tests/test_coding_resources_and_services.py tests/test_app_integration.py tests/test_cli_runtime_controls.py`
  — 134 passed in 16.17s at the final Phase 1 source commit.
- Phase suite command: completed; see Task 1.6.
- Installed-wheel TUI scenario: completed; see Task 1.6.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`.
- Notes/remaining risks: the public factory still accepts and returns the exact legacy
  camelCase mapping; only the internal construction boundary uses the frozen, slotted
  record.

### Task 1.3 — Structural session factory boundary

- Commit: `6f38bb6fe6f4cafe3c01df2e6192b9cf97a4e10a`.
- RED command and expected failure:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/architecture/test_refactor_contracts.py tests/test_app_integration.py -k session_factory`
  — 2 failed as expected because the service/runtime owners still imported the
  concrete session façade and an injected factory was not used.
- Focused GREEN command:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/architecture/test_refactor_contracts.py tests/test_app_integration.py tests/test_session_parity.py tests/test_session_commands.py`
  — 62 passed in 7.38s at the final Phase 1 source commit. Both clean-interpreter import
  orders also printed `ok`.
- Qualification correction: the first repository checkpoint exposed one stale test
  that monkeypatched the deliberately removed concrete module symbol. Commit
  `94f5553934b08ddb8dad5fedfd262ebd7b28402a` changed that existing failure regression
  to use the injected `sessionFactory` seam; its focused selector passed 4 tests with
  48 deselected in 0.53s before the full rerun.
- Phase suite command: completed; see Task 1.6.
- Installed-wheel TUI scenario: completed; see Task 1.6.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`.
- Notes/remaining risks: normal construction still resolves and returns the concrete
  `AgentSession` through a late leaf factory; no module-level service locator was added.

### Task 1.4 — Single bootstrap option normalization boundary

- Commit: `356f5cc8a85b1b38104dae246957e9164693fd91`.
- RED command and expected failure:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/coding_agent/test_session_options.py`
  — collection failed as expected because `SessionBootstrapOptions` did not exist. A
  subsequent explicit-`None` regression failed before provided-key tracking was added.
- Focused GREEN command:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/coding_agent/test_session_options.py tests/coding_agent/test_session_composition.py tests/test_cli_runtime_controls.py tests/test_coding_resources_and_services.py`
  — 159 passed in 8.34s at the final Phase 1 source commit.
- Phase suite command: completed; see Task 1.6.
- Installed-wheel TUI scenario: completed; see Task 1.6.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`.
- Notes/remaining risks: all 34 supported snake/camel aliases normalize once; unknown
  compatibility keys remain in a read-only extras mapping and the safe representation
  omits values.

### Task 1.5 — Resolvable public annotations

- Commit: `c4f16ed56953eea3f94ee4407cf136b06960b857`.
- RED command and expected failure:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/architecture/test_public_type_hints.py`
  — after strengthening the clean-interpreter walker to include public class members,
  it failed on unresolved `CodingApp` and `AgentSession` annotations in `AgentHarness`.
  The monotonic quality-scope selector also failed until every Phase 1 owner was added.
- Focused GREEN command:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/architecture/test_public_type_hints.py tests/architecture/test_refactor_contracts.py tests/architecture/test_quality_configuration.py`
  — 11 passed in 2.06s at the final Phase 1 source commit.
- Static GREEN gates: full scoped `pyright` reported 0 errors, 0 warnings, and 0
  informations; the migrated-owner Ruff rules `E4,E7,E9,F,I,UP,B,SIM` passed.
- Phase suite command: completed; see Task 1.6.
- Installed-wheel TUI scenario: completed; see Task 1.6.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`.
- Notes/remaining risks: Pyright remains intentionally incremental, but its include
  scope is monotonic and now owns every Phase 1 module and focused contract test.

### Task 1.6 — Phase 1 qualification and installed-wheel TUI

- First root checkpoint:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest -q -p no:cacheprovider tests`
  — 1 failed and 2,776 passed in 324.02s. The sole stale-factory test was corrected in
  `94f5553934b08ddb8dad5fedfd262ebd7b28402a` as described under Task 1.3.
- Final Phase 1 source checkpoint before the evidence commit: the same command —
  2,777 passed in 321.17s.
- Exact reviewed Phase 1 HEAD rerun after the evidence commit
  `262a4d830d01cc8c3b78c9793e2d99625f37762a`: the same command — 2,777 passed in
  353.10s.
- Final Phase 1 owner slice:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/architecture tests/coding_agent tests/tui/test_interactive_owner_boundaries.py tests/tui/test_interactive_dispatch_characterization.py tests/tui/test_interactive_shutdown_characterization.py tests/test_runtime_facade_contract.py tests/test_coding_resources_and_services.py tests/test_app_integration.py tests/test_session_parity.py tests/test_session_commands.py tests/test_cli.py tests/test_cli_runtime_controls.py tests/test_operation_coordinator.py`
  — 358 passed in 30.79s.
- Adapter checkpoint:
  `uv run --project packages/travis234-mcp-adapter --locked --group source-test pytest -q -p no:cacheprovider packages/travis234-mcp-adapter/tests`
  — 125 passed in 23.21s.
- npm launcher: 24 passed in 0.89s. npm dry-pack passed with the expected 11-file
  inventory.
- Static gates: full scoped Pyright reported 0 errors, 0 warnings, and 0 informations;
  the Phase 1 migrated-owner Ruff rules passed.
- Package qualification:
  `uv build --out-dir <task-root>/root .` and
  `uv build --out-dir <task-root>/adapter packages/travis234-mcp-adapter` produced both
  wheel and source distributions; locked Twine validation passed all four artifacts.
  The exact root wheel installed into a fresh Python 3.13.13 environment, and an import
  check from outside the repository resolved `travis` from that environment's
  `site-packages`.
- Installed-wheel qualification used the actual installed `travis234` console entry,
  real attached PTYs, isolated task-owned `TRAVIS234_CODING_AGENT_DIR` state,
  `--offline`, and a keyless in-memory faux provider supplied by a task-owned global
  extension. No dotenv, credential, live provider, user state, or project extension was
  used:
  1. **PASS — startup/help:** the installed console rendered the complete CLI help,
     then the native TUI started at Idle and exposed the expected command inventory.
  2. **PASS — normal prompt:** the faux provider returned `PHASE1_CONTRACTS_OK` and the
     TUI returned to Idle.
  3. **PASS — `/login` discovery:** the authentication-method picker displayed
     subscription and API-key choices; a blank selection cancelled without collecting
     or storing a credential and returned to Idle.
  4. **PASS — `/coordination --plan`:** the exact no-goal invocation produced the
     preserved `A coordination goal is required` validation error and returned to Idle.
  5. **PASS — new/resume/fork:** `/new` created and switched to a fresh session,
     `/resume` selected and restored the original prompt transcript, and `/fork`
     selected that user entry and switched to a new branch with the prompt preloaded.
  6. **PASS — clean shutdown:** after clearing the preloaded fork editor, `/exit`
     returned 0, rendered `status: Exiting`, restored cursor and bracketed-paste terminal
     modes, and left no task-owned console process.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`.
- Protected-loop diff from `7838749452b567940bd5b69a715b6184b8f9f13e`: empty.
- Generated build, install, extension, and TUI state remain only under the task-owned
  temporary root and are removed after the final checks. No container was built because
  the approved master plan reserves container qualification for Phase 5.
- Remaining risks: remote CI has not run; live-provider and comprehensive 21-prompt
  evidence remain deferred to their approved later gate. Phase 2 has not started.

### Phase 1 coordinator review corrections

- Legacy service-mapping compatibility correction:
  `cc5880aafc6c8bdc453228387b9fc8abbf72e167`. The public
  `create_agent_session_from_services` regressions failed first for mappings that
  omitted `authStorage`, `sessionPath`, or both (3 failed and 1 passed). The typed
  dependency boundary now derives an omitted authentication owner from the existing
  `ModelRegistry`, continues to reject an explicitly mismatched owner, and represents
  an omitted session path as `None`; the exact normal wrapper mapping remains unchanged.
  The focused composition/resource/application/CLI slice passed 138 tests in 15.22s.
- Structural runtime-boundary correction:
  `5608a9a57edd725c71afa20805cf838118b9478e`. The missing-`cwd` and six
  non-callable-member regressions all failed first because `_coerce_result` accepted
  the incomplete objects. Boundary validation now rejects missing data members and
  missing or non-callable required methods while continuing to accept the valid
  structural fake; the focused runtime/parity slice passed 69 tests in 6.88s.
- The first root review checkpoint exposed two existing language-service test doubles
  that did not implement the newly explicit runtime port: 2 failed and 2,786 passed in
  301.78s. Commit `0c175b8743c6144b1d9132dc4b96c7e5ecc2f09d` completed only that fake's
  protocol surface, after which its focused slice passed 69 tests in 6.50s.
- Final correction qualification:
  - both lock checks passed (84 root packages and 56 adapter packages);
  - the expanded Phase 1 owner slice passed 369 tests in 27.20s;
  - full scoped Pyright reported 0 errors, 0 warnings, and 0 informations, and the
    migrated-owner Ruff gate passed;
  - the final source checkpoint passed 2,788 tests in 345.08s;
  - the locked adapter suite passed 125 tests in 16.27s;
  - the npm launcher passed all 24 tests and dry-pack produced the expected 11-file
    inventory;
  - fresh root and adapter wheel/source builds completed, and locked Twine validation
    passed all four artifacts.
- The exact root wheel installed into a fresh Python 3.13.13 environment and resolved
  `travis.coding_agent` from that environment's `site-packages`. Its actual installed
  `travis234` console passed the real-PTY Phase 1 help/startup, keyless faux-provider
  prompt (`PHASE1_REVIEW_OK`), cancelled `/login`, no-goal `/coordination --plan`,
  `/new`, `/resume`, `/fork`, preloaded-editor clearing, and clean `/exit` scenarios.
  Exit status was 0, `status: Exiting` rendered, terminal modes were restored, and no
  task-owned console remained.
- Protected-loop SHA-256 remains
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`, and its diff
  from `7838749452b567940bd5b69a715b6184b8f9f13e` remains empty. No credential,
  container, provider, persistence-format, TUI-command, user-state, or protected-loop
  change was made. Phase 2 has not started.

## Phase 2 — Session and TUI collaborator extraction

### Task 2.1 — Composition containers and explicit ports

- Commit: `0a3e11e6471d0e575de0edbbb1b4da3cabfda562`.
- RED command and expected failure:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/coding_agent/test_session_controller_composition.py tests/tui/test_interactive_controller_composition.py tests/architecture/test_refactor_contracts.py tests/architecture/test_facade_boundaries.py`
  — the new composition-contract tests failed against the reviewed Phase 1 base because
  the controller containers, narrow ports, and mutable state records did not exist.
- Focused GREEN command: the same command passed after adding frozen/slotted controller
  bundles, responsibility-specific structural ports, and cohesive session/TUI state
  records. Leaf-controller dependency tests reject either complete runtime or public
  façade as a constructor dependency.
- Phase suite and installed-wheel scenario: completed under Task 2.9.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`.
- Notes/remaining risks: the containers expose named controller fields; no generic
  controller/service dictionary or runtime-checkable catch-all protocol was introduced.

### Tasks 2.2–2.4 — TUI collaborators and mixin removal

- Commits:
  `2f6cfea07f541dc70f93df81b9df615529914732` (view, motion, routing),
  `d6bf890a82033f64783acd168c347307bcc41c84` (model and parameters),
  `f72d3c8bd6de2957f68ca2f3d5cd711b40a3a499` (process and inspection),
  `d3cb7c325eafe9fd406bd1ee9f2fac89a2fbdb28` (subagent and session), and
  `6fb9f6c855b6aae4c28011de121dd3a0aa9c178b` (extensions, turns, shutdown, and final
  TUI mixin removal).
- Characterization-first command:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/tui tests/test_tui_*.py tests/test_extension_host_runtime.py tests/test_extension_event_parity.py tests/test_session_commands.py`
  — the pre-move command/renderer, model/auth/parameter, process/LSP/memory/operation,
  subagent/session-rebind, extension/turn, cancellation, and shutdown characterizations
  passed before their respective moves and after explicit composition.
- RED command and expected failure: not applicable to the behavior moves; they used
  GREEN-before/GREEN-after characterization as required. New ownership assertions failed
  until each runtime base was removed and the corresponding named controller was owned.
- Phase suite and installed-wheel scenario: completed under Task 2.9.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`.
- Notes/remaining risks: command precedence, session replacement rollback, extension
  ordering, active-turn steering/follow-up, Escape cancellation, repeated Ctrl-C,
  bounded shutdown, and terminal restoration retain focused regression coverage.

### Tasks 2.5–2.7 — Session collaborators and mixin removal

- Commits:
  `9dacabd48bf47f5cc564979420bfd2fb3e738b36` (low-coupling ports),
  `83a0c2d50441f3fb71b397877fed17fac043a6bc` (model, policy, operations),
  `7fd7ebb312e32eaf9947d5091e19db0cb6d6da9a` (persistence),
  `ecd848a9867e66d9279de1329713b54fbc463ec4` (tools and extensions),
  `a6a4aabac8d63ba379c58d8841c9403f5b2be31c` (subagents),
  `a2cd5c4727afc98951097b8a687933507a7550b3` (controller composition), and
  `4ec962ba0e4f7ddf2fc118ea23c380fe023ffcc9` (turn ownership and final session mixin
  removal).
- Characterization-first command:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/coding_agent tests/test_agent_loop.py tests/test_agent_runtime_hardening.py tests/test_abort_context.py tests/test_coding_mailbox.py tests/test_coding_persistence_and_compaction.py tests/test_compaction_integration.py tests/test_session_parity.py tests/test_session_commands.py tests/test_coding_policy_and_extensions.py tests/test_extension_event_parity.py tests/test_coding_tools_and_subagents.py tests/test_subagent_controls.py tests/test_subagent_artifact_results.py tests/test_subagent_structured_results.py`
  — the event/model/parameter/bash/policy/operation, JSONL/compaction, tool/extension,
  subagent/result/expansion, turn ordering, cancellation, steering, follow-up, and
  source-ordered continuation characterizations passed before and after their moves.
- RED command and expected failure: not applicable to the behavior moves; ownership tests
  rejected inherited mixins until the named collaborators and delegate boundary were in
  place.
- Phase suite and installed-wheel scenario: completed under Task 2.9.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`.
- Notes/remaining risks: the turn collaborator continues to call the public session/agent
  contracts and introduces no replacement queue, thread, async bridge, or scheduler.
  Expanded subagent results, artifact references, coordination, and orchestration remain
  additive and are covered by the unchanged full-suite behavior tests.

### Task 2.8 — Explicit imports and migrated-owner quality gates

- Commits: `18c966c5a4f761efd9fe3290ff047e4512478259` (explicit imports and exports),
  `5d5e954f7b087f2dac735f359ed67eebf5834c56` (bounded delegate-map ownership), and
  `487d7a3c93fb56c42ebe57ae2a7cde42b51ce2b4` (diagnostic-free collaborator checks).
- RED command and expected failure:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/architecture/test_quality_configuration.py -k migrated_owners_pass_pyright_without_diagnostics`
  — 1 failed because scoped Pyright returned eight `reportInvalidTypeVarUse` warnings at
  the dynamic, generic controller-port compatibility bridges.
- Focused GREEN command:
  `uv run --locked --all-extras --dev pytest -q -p no:cacheprovider tests/architecture/test_quality_configuration.py tests/architecture/test_repository_hygiene.py`
  — 8 passed after exact line-level diagnostic annotations were limited to those
  unconstrained compatibility signatures. Replacing the signatures with `object` was
  rejected after producing 1,348 type errors; no broad configuration suppression or
  `Any` escape hatch was added.
- Static GREEN gates:
  `uv run --locked --all-extras --dev pyright` reported 0 errors, 0 warnings, and 0
  informations; fatal repository Ruff and migrated-owner Ruff both passed. An independent
  source and installed-wheel AST walk found 0 production star imports.
- Phase suite and installed-wheel scenario: completed under Task 2.9.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`.
- Notes/remaining risks: public re-exports and the dynamic private compatibility bridge
  remain additive; supported members are explicitly inventoried and delegated.

### Task 2.9 — Phase 2 qualification and installed-wheel TUI

- Reviewed predecessor: Phase 1 head
  `e67d94d` (`docs: record phase 1 review qualification`).
- Root coverage checkpoint:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. uv run --locked --all-extras --dev --group coverage-test coverage run -m pytest -q -p no:cacheprovider tests`
  — 2,807 passed in 445.01s. Coverage JSON contained 37,445 / 44,596 statements
  (83.96%) and 10,431 / 15,196 branches (68.64%); the independent checker passed the
  unchanged 83.0% statement and 68.0% branch floors.
- Exact pinned-interpreter checkpoint:
  `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest -q -p no:cacheprovider tests`
  — 2,807 passed in 313.66s.
- Lock and package checkpoints: the root and adapter locks passed with 84 and 56 packages.
  The locked adapter source suite passed 125 tests in 25.84s. The npm launcher passed
  24/24 tests, and npm dry-pack passed with its expected 11-file inventory.
- Build qualification: fresh root and adapter wheel/source builds passed, and locked
  Twine validation passed all four artifacts. SHA-256 values were root wheel
  `84ebc09c623f31a21d295b54cde019cea97f0d6eaf492c9bcb5027125ee7d354`, root sdist
  `6b1569b4f94e36d71f3215f962a91921dadec8d895df81ad4b1f9300d9e82aad`, adapter wheel
  `64014e811b37ef2fb4a68da1b2d7e4835b858b2867e667d9e5993b3a8fef642c`, and adapter
  sdist `0e7a45d22d6b80981f53494cd7d73e61b918de43e16572acb943efab27d23fe2`.
  The exact root wheel installed into a fresh Python 3.13.13 environment; an import from
  outside the repository resolved `travis` from that environment's `site-packages`, and
  a keyless offline faux-provider console print smoke returned
  `PHASE2_FAUX_OK:installed smoke`.
- Architecture invariants: both source and installed-wheel checks reported
  `_SessionRuntime.__bases__ == (object,)`, `_InteractiveRuntime.__bases__ == (object,)`,
  and 0 production star imports.
- Installed-wheel qualification used the exact installed `travis234` console entry in a
  real attached PTY, isolated task-owned `HOME` and `TRAVIS234_CODING_AGENT_DIR`,
  `--offline`, and a task-owned keyless faux provider extension. No dotenv, credential,
  live provider, project extension, or user state was loaded or changed. The retained
  matrix reported each scenario immediately:
  1. **PASS — help and command inventory.**
  2. **PASS — ordinary faux-model prompt and streamed rendering.**
  3. **PASS — model inventory.**
  4. **PASS — generation parameter display, set, and reset.**
  5. **PASS — motion status and disable.**
  6. **PASS — unknown slash command remains local.**
  7. **PASS — managed-process inspection.**
  8. **PASS — actual user command and process output.**
  9. **PASS — language-service status.**
  10. **PASS — memory status remains read-only.**
  11. **PASS — operation-journal inspection.**
  12. **PASS — subagent supervisor status.**
  13. **PASS — extension reload and session rebind.**
  14. **PASS — extension command dispatch.**
  15. **PASS — partial provider streaming.**
  16. **PASS — active-turn steering continuation.**
  17. **PASS — Escape cancellation and recovery to Idle.**
  18. **PASS — manual compaction boundary.**
  19. **PASS — session naming and metadata.**
  20. **PASS — session clone.**
  21. **PASS — new, resume, and fork with transactional rebind.**
- Shutdown qualification: repeated idle Ctrl-C rendered `Press Ctrl-C again to exit`
  and `Exiting`, emitted a successful shutdown trace, restored terminal modes, and exited
  0. The first verifier wait stopped draining the PTY and filled its output buffer; a
  process sample proved the main thread was blocked in `write`, so the task-owned harness
  was corrected to drain concurrently. The unchanged wheel then passed 21/21 plus clean
  shutdown in 10.2s.
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`.
- Protected-loop diff from `7838749452b567940bd5b69a715b6184b8f9f13e`: empty.
- Remaining risks: remote CI has not run. No container was built because the approved
  master plan reserves container qualification for Phase 5. Generated coverage and
  task-owned build/install/TUI artifacts are removed after the final repository checks;
  Phase 3 has not started.
