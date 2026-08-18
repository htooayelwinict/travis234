# Phase 0 — Truthful Guardrails and Confirmed Regression

> **Required skills:** `superpowers:executing-plans`,
> `superpowers:test-driven-development`, and
> `superpowers:verification-before-completion`.

**Goal:** Establish trustworthy behavioral, static-analysis, coverage, acceptance, and
CI gates before moving architecture; fix the confirmed macOS path-alias defect through
a failing regression.

**Architecture:** Characterization tests freeze supported behavior. Development tools
and their dependency graph become locked. CI runs the same source commands as local
qualification. Acceptance evidence distinguishes automated requirements from external
or manual evidence.

**Do not modify:** `travis/agent/agent_loop.py`, provider transport behavior, session
composition, TUI composition, JSONL schemas, public command/tool names.

---

## Task 0.1: Record the clean baseline and expand characterization coverage

**Files:**

- Create: `docs/verification/contract-first-refactor.md`
- Create: `tests/test_runtime_facade_contract.py`
- Modify: `tests/coding_agent/test_agent_session_characterization.py`
- Modify: `tests/tui/test_interactive_dispatch_characterization.py`
- Modify: `tests/tui/test_interactive_shutdown_characterization.py`
- Modify: `tests/ai/providers/test_provider_characterization.py`

- [ ] **Step 1: Run and record the master-plan baseline commands**

Record commit, pass counts, timings, protected SHA, and environment versions in the
verification document. Record no environment values.

- [ ] **Step 2: Add façade forwarding characterization**

In `tests/test_runtime_facade_contract.py`, create a tiny runtime/facade fixture and
assert the current contract:

```python
class _Runtime:
    value = "runtime"

    def action(self) -> str:
        return "done"


class _Facade(RuntimeFacade):
    def __init__(self) -> None:
        object.__setattr__(self, "_runtime", _Runtime())


def test_runtime_facade_forwards_get_set_dir_and_overrides() -> None:
    facade = _Facade()
    assert facade.value == "runtime"
    assert facade.action() == "done"
    assert "action" in dir(facade)
    facade.value = "changed"
    facade.action = lambda: "override"
    assert facade._runtime.value == "changed"
    assert facade.action() == "override"
```

Add focused `AgentSession` and `InteractiveMode` assertions for explicit lifecycle
methods plus current dynamic override behavior. These tests are compatibility contracts,
not approval to add new dynamic-only APIs.

- [ ] **Step 3: Characterize lifecycle and command behavior**

Add focused tests for:

- failed/cancelled session replacement retains the active session;
- `dispose` and `shutdown` close optional owners once;
- exact command classification for `/coordination`, `/agents`, `/lsp status`, `/memory
  status`, `/operations`, `/login`, `/model`, and ordinary prompts;
- owner-thread shutdown dispatch remains bounded;
- provider SSE fixture preserves start/content/end/done event types and final text.

Use existing faux providers, `tests._support_tui`, and isolated `tmp_path` state. Do not
make network calls.

- [ ] **Step 4: Prove characterization tests pass before implementation moves**

Run:

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest -q \
  tests/test_runtime_facade_contract.py \
  tests/coding_agent/test_agent_session_characterization.py \
  tests/tui/test_interactive_dispatch_characterization.py \
  tests/tui/test_interactive_shutdown_characterization.py \
  tests/ai/providers/test_provider_characterization.py
```

Expected: all tests pass against the unchanged production architecture.

- [ ] **Step 5: Commit**

```bash
git add docs/verification/contract-first-refactor.md \
  tests/test_runtime_facade_contract.py \
  tests/coding_agent/test_agent_session_characterization.py \
  tests/tui/test_interactive_dispatch_characterization.py \
  tests/tui/test_interactive_shutdown_characterization.py \
  tests/ai/providers/test_provider_characterization.py
git commit -m "test(refactor): characterize protected runtime contracts"
```

---

## Task 0.2: Fix runtime Python bin removal across path aliases

**Files:**

- Modify: `tests/test_coding_exports_and_boundaries.py`
- Modify: `travis/coding_agent/tools/bash.py`

- [ ] **Step 1: Add the failing cross-platform alias regression**

Build a fixture where the apparent virtual-environment directory has an aliased parent
and its `python` executable is a symlink to the base interpreter. Patch `sys.executable`,
`sys.prefix`, `PATH`, and the isolated agent directory. Assert the canonical runtime bin
is removed from `get_shell_env()`.

The essential fixture shape is:

```python
real_bin = tmp_path / "real-runtime" / "bin"
real_bin.mkdir(parents=True)
(real_bin / "python").symlink_to(Path(sys.executable).resolve())
alias_root = tmp_path / "alias-runtime"
alias_root.symlink_to(real_bin.parent, target_is_directory=True)
monkeypatch.setattr(sys, "executable", str(alias_root / "bin" / "python"))
monkeypatch.setattr(sys, "prefix", str(alias_root))
monkeypatch.setenv("PATH", str(real_bin))
assert str(real_bin) not in get_shell_env()["PATH"].split(os.pathsep)
```

- [ ] **Step 2: Run the exact test and confirm RED**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest -q \
  tests/test_coding_exports_and_boundaries.py \
  -k runtime_python_bin_through_path_alias
```

Expected failure: the canonical `real_bin` entry remains because the implementation
stores the unresolved executable parent and the resolved executable target's parent,
but not the resolved executable-parent directory.

- [ ] **Step 3: Apply the minimal canonical-parent fix**

Change only `_without_runtime_python_bin`:

```python
runtime_executable = Path(sys.executable).expanduser()
runtime_python_bins = {
    runtime_executable.parent.resolve(),
    runtime_executable.resolve().parent,
}
```

Keep the system-Python early return and existing entry resolution unchanged.

- [ ] **Step 4: Run focused GREEN and surrounding environment tests**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest -q \
  tests/test_coding_exports_and_boundaries.py \
  tests/test_subprocess_environment.py \
  tests/test_process_context.py
```

- [ ] **Step 5: Commit**

```bash
git add travis/coding_agent/tools/bash.py tests/test_coding_exports_and_boundaries.py
git commit -m "fix(tools): normalize runtime python path aliases"
```

---

## Task 0.3: Lock development tools and introduce monotonic analysis scopes

**Files:**

- Modify: `.gitignore`
- Modify: `pyproject.toml`
- Force-add/regenerate: `uv.lock`
- Create: `pyrightconfig.json`
- Create: `ruff.toml`
- Create: `tests/architecture/test_quality_configuration.py`

- [ ] **Step 1: Write failing configuration-contract tests**

Test that:

- root `uv.lock` is no longer ignored and is tracked by `git ls-files` after commit;
- the dev dependency group includes pytest, coverage, Ruff, Pyright, build, and Twine;
- Pyright's initial include scope contains new contract/config owners and excludes no
  included file through a broad wildcard;
- Ruff runs repository-wide `E9`, `F63`, `F7`, and `F82` rules and applies the normal
  selected rules to migrated modules;
- neither config enables blanket ignores for `travis/**`.

- [ ] **Step 2: Confirm RED**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest -q \
  tests/architecture/test_quality_configuration.py
```

- [ ] **Step 3: Add locked development dependencies**

Remove only the root `uv.lock` ignore rule; leave generated environments and the adapter
lock policy unchanged. Add a PEP 735 development group equivalent to:

```toml
[dependency-groups]
dev = [
  "build>=1,<2",
  "coverage>=7,<8",
  "pyright>=1.1,<2",
  "pytest>=8,<10",
  "ruff>=0.12,<1",
  "twine>=6,<7",
]
```

Run `uv lock`, then `uv sync --locked --all-extras --dev`. Force-add the formerly
ignored lock only after checking it contains no local path or credential.

- [ ] **Step 4: Add scoped Pyright and Ruff configuration**

Start Pyright with Python 3.13 and only known-clean/new boundary modules. Do not claim
the entire repository is typed. Start Ruff with fatal parser/undefined-name rules across
the repository and an explicit migrated-module file list for broader rules. Any existing
`F821` must be fixed through a focused test or explicitly deferred with a named issue in
the verification ledger; do not globally ignore it.

- [ ] **Step 5: Run configuration, lock, and initial analysis gates**

```bash
uv lock --check
uv run --locked --all-extras --dev pytest -q \
  tests/architecture/test_quality_configuration.py
uv run --locked --all-extras --dev ruff check --select E9,F63,F7,F82 travis tests
uv run --locked --all-extras --dev pyright
```

- [ ] **Step 6: Commit**

```bash
git add .gitignore pyproject.toml uv.lock pyrightconfig.json ruff.toml \
  tests/architecture/test_quality_configuration.py
git commit -m "build: lock refactor quality toolchain"
```

---

## Task 0.4: Make acceptance evidence and source CI truthful

**Files:**

- Create: `.github/workflows/ci.yml`
- Create: `tests/test_ci_workflow.py`
- Modify: `scripts/verify_acceptance.py`
- Modify: `tests/architecture/test_acceptance_matrix.py`
- Modify: `docs/verification/acceptance-matrix.md`
- Modify: `tests/test_release_workflow.py`

- [ ] **Step 1: Add failing evidence-classification tests**

Extend each acceptance row with one class:

```python
VALID_CLASSES = {
    "automated-required",
    "live-required",
    "manual",
    "informational",
}
```

Tests must prove:

- malformed or missing classes fail parsing;
- current-commit verification requires every `automated-required` result to pass;
- `blocked` live evidence is reported but does not create a false automated pass or
  fail the source job;
- stale commit evidence fails;
- a `passed` status in Markdown alone is not current-commit evidence.

- [ ] **Step 2: Confirm RED**

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/architecture/test_acceptance_matrix.py
```

- [ ] **Step 3: Implement classified current-commit evidence**

Add the seventh matrix column `Class`. Change `verify_current_commit` to receive the
loaded rows and require results only for the automated-required IDs. Return structured
blocked/pending live/manual information for display. Add a bounded
`--record-automated-evidence PATH` mode used only after all automated workflow steps have
passed; it writes the current commit and automated-required `passed` results.

Do not check generated current-commit evidence into Git.

- [ ] **Step 4: Add a least-privilege source workflow**

`.github/workflows/ci.yml` triggers on pull requests and pushes to `main`, sets
`permissions: contents: read`, pins setup action major versions, and runs:

1. `uv sync --locked --all-extras --dev`;
2. fatal Ruff rules and scoped Pyright;
3. root tests without pytest cache;
4. adapter source tests in its own environment;
5. npm launcher tests and dry-pack;
6. root and adapter `uv build` plus Twine check;
7. temporary automated evidence recording and strict verification.

Do not add publishing, GHCR login, or container work to source CI. Keep the existing
release workflow manual/release-triggered.

- [ ] **Step 5: Test workflow semantics**

`tests/test_ci_workflow.py` parses YAML as text or through the existing workflow helper
and asserts triggers, read-only permissions, locked sync, root/adapter separation,
build checks, npm checks, strict evidence, and absence of publish/login steps.

Run:

```bash
uv run --locked --all-extras --dev pytest -q \
  tests/architecture/test_acceptance_matrix.py \
  tests/test_ci_workflow.py \
  tests/test_release_workflow.py
```

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/ci.yml scripts/verify_acceptance.py \
  docs/verification/acceptance-matrix.md \
  tests/architecture/test_acceptance_matrix.py \
  tests/test_ci_workflow.py tests/test_release_workflow.py
git commit -m "ci: add truthful source qualification"
```

---

## Task 0.5: Establish separate statement/branch coverage floors

**Files:**

- Create: `.coveragerc`
- Create: `scripts/check_coverage_floor.py`
- Create: `tests/test_coverage_floor.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/verification/contract-first-refactor.md`

- [ ] **Step 1: Write failing floor-calculation tests**

Use small JSON fixtures and prove the checker independently rejects statement coverage
below `83.0` and branch coverage below `68.0`. Prove it rejects missing branch data and
accepts metrics exactly at the floor.

- [ ] **Step 2: Confirm RED**

```bash
uv run --locked --all-extras --dev pytest -q tests/test_coverage_floor.py
```

- [ ] **Step 3: Implement reproducible coverage configuration**

Configure `source = travis`, `branch = True`, parallel-safe data, and explicit exclusion
of tests, worktrees, build output, and temporary audit trees. CI sets
`PYTHONDONTWRITEBYTECODE=1`, erases prior data, runs root tests under coverage, writes
JSON, and calls:

```bash
uv run --locked --all-extras --dev python scripts/check_coverage_floor.py \
  coverage.json --statements 83.0 --branches 68.0
```

- [ ] **Step 4: Run the real coverage baseline**

```bash
rm -f .coverage coverage.json
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  uv run --locked --all-extras --dev coverage run -m pytest \
  -q -p no:cacheprovider tests
uv run --locked --all-extras --dev coverage json -o coverage.json
uv run --locked --all-extras --dev python scripts/check_coverage_floor.py \
  coverage.json --statements 83.0 --branches 68.0
```

Remove only `.coverage` and `coverage.json` after recording summarized metrics. If the
clean baseline is below a floor, set the floor down to the measured tenth of a percent
and document why; never manufacture exclusions to meet the target.

- [ ] **Step 5: Commit**

```bash
git add .coveragerc .github/workflows/ci.yml scripts/check_coverage_floor.py \
  tests/test_coverage_floor.py docs/verification/contract-first-refactor.md
git commit -m "test(coverage): establish truthful branch and statement floors"
```

---

## Task 0.6: Phase 0 qualification and installed-wheel TUI smoke

- [ ] **Step 1: Run the master phase checkpoint**

- [ ] **Step 2: Build root and adapter artifacts**

```bash
phase_dist="$(mktemp -d /tmp/travis234-refactor-phase0.XXXXXX)"
uv build --out-dir "$phase_dist/root" .
uv build --out-dir "$phase_dist/adapter" packages/travis234-mcp-adapter
uv run --locked --all-extras --dev twine check \
  "$phase_dist"/root/* "$phase_dist"/adapter/*
```

- [ ] **Step 3: Install and exercise the wheel as a normal user**

Create a fresh uv environment and isolated `TRAVIS234_AGENT_DIR`. Install the exact root
wheel. Launch that environment's actual `travis234` console entry in a real attached PTY
using the execution tool's TTY/session support. Do not substitute `python -m`, a fake
terminal, `evals.tui_driver`, or a scripted prompt runner. Run offline faux-provider
scenarios:

1. start and display help;
2. ask a normal read-only prompt;
3. invoke `/coordination --plan` and exit;
4. start with a PATH containing the aliased runtime bin and prove tool shell PATH omits it;
5. cleanly shut down.

Record PASS/FAIL per prompt. Do not use a live `.env` in Phase 0.

- [ ] **Step 4: Update the ledger and commit evidence**

```bash
git add docs/verification/contract-first-refactor.md
git commit -m "docs: record phase 0 refactor qualification"
```

- [ ] **Step 5: Report and stop for review**

Do not begin Phase 1 until the coordinating agent reviews the diff and evidence.
