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
- Phase suite command: pending until Task 0.6
- Installed-wheel TUI scenario: pending until Task 0.6
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
- Phase suite command: pending until Task 0.6
- Installed-wheel TUI scenario: pending until Task 0.6
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
- Phase suite command: pending until Task 0.6
- Installed-wheel TUI scenario: pending until Task 0.6
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
- Phase suite command: pending until Task 0.6
- Installed-wheel TUI scenario: pending until Task 0.6
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`
- Notes/remaining risks: source-workflow semantics are locally contract-tested; the
  workflow has not run on a remote GitHub runner. Live and manual acceptance remain
  explicitly outside the automated source gate.

### Task 0.5 — Independent statement and branch coverage floors

- Commit: recorded at the next ledger checkpoint after this task's commit is created.
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
- Measured coverage after combining the single parallel-safe data file:
  37,412 / 44,559 statements = 83.96%; 10,369 / 15,132 branches = 68.52%.
  The independent checker passed the approved 83.0% statement and 68.0% branch
  floors without changing exclusions or lowering either floor.
- Generated `.coverage` data and `coverage.json` were removed after the summarized
  counts were recorded.
- Phase suite command: pending until Task 0.6
- Installed-wheel TUI scenario: pending until Task 0.6
- Protected-loop SHA-256:
  `b332f3ae0dffb0df8bdf97cb0113818342ed5c83dc03198e215b344fa4adf5c7`
- Notes/remaining risks: coverage timing is materially slower than the ordinary root
  suite. Adapter behavior is still qualified independently; the root-only ephemeral
  layer exists solely for the benchmark contract that imports its public types.
