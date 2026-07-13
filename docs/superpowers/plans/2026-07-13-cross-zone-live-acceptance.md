# Cross-Zone Verification and 21-Prompt TUI Acceptance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove all eleven findings, rebranding, packaging, red/yellow/green boundaries, and actual agent quality through automated checks and one real 21-prompt Travis234 TUI session.

**Architecture:** Run narrow fault-injection gates first, then zone/full/package checks, then safely migrate the ignored `.env`. Drive the installed `travis234` console entry through a PTY for 21 verified coding prompts in one persistent session and audit events, artifacts, processes, session identity, secrets, and fixture tests before publishing `main`.

**Tech Stack:** Python 3.13, pytest, Node.js, npm, Setuptools build, PTY driver, real configured LLM provider, Git/GitHub CLI.

## Global Constraints

- `.env` remains ignored, mode `0600`, and no value is printed to terminal, test output, transcript, report, package, image, or Git.
- The live run uses the installed `travis234` console script, not a fake terminal or direct class call.
- Exactly 21 coding prompts are sent in one TUI process and one persistent session ID/path.
- Each prompt has an external verifier; TUI survival alone is not success.
- Transcript/event logs redact API keys, bearer tokens, and provider credential values.
- No owned child process or non-daemon Travis234 worker survives the run.
- Publishing occurs only after current-state automated and live evidence passes.

---

### Task 1: Add finding-by-finding acceptance matrix

**Files:**
- Create: `docs/verification/acceptance-matrix.md`
- Create: `scripts/verify_acceptance.py`
- Create: `tests/architecture/test_acceptance_matrix.py`

**Interfaces:**
- Produces one machine-readable row per finding/rebrand/live requirement with command and evidence path.

- [ ] **Step 1: Define required acceptance IDs**

```python
REQUIRED_IDS = {
    "rebrand",
    "finding-01-monitor-ownership",
    "finding-02-stdin-ack",
    "finding-03-ctrl-c-escalation",
    "finding-06-installed-metadata",
    "finding-07-bounded-shutdown",
    "finding-08-facade-decomposition",
    "finding-09-provider-ownership",
    "finding-10-session-index",
    "finding-11-compaction-transactions",
    "finding-12-advisory-classifier",
    "finding-14-cleanup",
    "red-zone-parity",
    "yellow-zone-faults",
    "green-zone-package",
    "live-21-prompt-tui",
    "public-repository",
}
```

- [ ] **Step 2: Add failing matrix-completeness test**

```python
def test_acceptance_matrix_has_every_required_row() -> None:
    matrix = load_acceptance_matrix(ROOT / "docs/verification/acceptance-matrix.md")
    assert set(matrix) == REQUIRED_IDS
    assert all(row.command and row.expected and row.evidence for row in matrix.values())
```

- [ ] **Step 3: Populate exact commands and evidence destinations**

Each Markdown table row uses columns `ID`, `Requirement`, `Command`, `Expected`,
`Evidence`, `Status`. Initial status is `pending`; the verifier changes it only
after the command exits zero and the evidence assertion passes. The matrix does
not accept manual “looks good” entries.

- [ ] **Step 4: Run matrix structure green and commit**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/architecture/test_acceptance_matrix.py -q`

Expected: PASS.

```bash
git add docs/verification/acceptance-matrix.md scripts/verify_acceptance.py tests/architecture/test_acceptance_matrix.py
git commit -m "test: define Travis234 completion matrix"
```

### Task 2: Run focused finding and zone gates

**Files:**
- Update generated status/evidence sections in `docs/verification/acceptance-matrix.md`.

**Interfaces:**
- Consumes all previous plan outputs.
- Produces focused current-state evidence before the expensive full/live run.

- [ ] **Step 1: Run process/cancellation/shutdown faults**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_process_service.py tests/test_process_tools.py tests/test_tui_user_commands.py tests/test_session_commands.py tests/test_tui_shutdown.py tests/test_model_loader.py -q`

Expected: PASS, including monitor signal ordering, broken-pipe propagation,
interrupt/terminate/kill ordering, and sub-second stuck-operation shutdown.

- [ ] **Step 2: Run provider/session/policy faults**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_provider_control_plane.py tests/architecture/test_provider_ownership.py tests/test_session_index.py tests/test_session_catalog.py tests/test_session_catalog_performance.py tests/policies/test_bash_classification.py -q`

Expected: PASS, with isolated control planes, warm JSONL counters equal to zero,
and all required mutation examples classified correctly.

- [ ] **Step 3: Run red-zone parity**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_agent_loop.py tests/test_agent_runtime_hardening.py tests/test_compaction.py tests/test_compaction_timing.py tests/test_compaction_integration.py tests/compaction tests/architecture/test_red_zone.py -q`

Expected: PASS with unchanged normalized control-flow ASTs and transaction observations.

- [ ] **Step 4: Run architecture/cleanup gates**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/architecture -q`

Expected: PASS with size, dependency direction, no private compaction/provider
access, no compatibility/camel surfaces, no helper duplicates, and test-file ceilings.

### Task 3: Run full source and installed-package verification

**Files:**
- Create/update: `docs/verification/full-suite.md`

**Interfaces:**
- Produces complete Python/npm/build/install/launcher evidence from the final tree.

- [ ] **Step 1: Compile and run full Python suite**

Run: `.venv/bin/python -m compileall -q travis tests evals scripts`

Expected: PASS.

Run: `PYTHONPATH=. .venv/bin/python -m pytest --collect-only -q`

Expected: collection succeeds and count is not below the imported 1,360-test baseline.

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests -q`

Expected: every collected test passes.

- [ ] **Step 2: Run npm tests and package inspection**

Run: `npm --prefix packages/travis234-cli test`

Expected: PASS.

Run: `npm --prefix packages/travis234-cli run pack:dry-run`

Expected: PASS and list only declared package assets.

- [ ] **Step 3: Build/install Python artifacts in a clean venv**

Run: `.venv/bin/python -m build`

Expected: `travis234-2.3.1` wheel and sdist are produced.

Run: `.venv/bin/python -m venv artifacts/installed-venv && artifacts/installed-venv/bin/python -m pip install dist/travis234-2.3.1-py3-none-any.whl && artifacts/installed-venv/bin/travis234 --help`

Expected: install and help exit 0; output identifies Travis234 and contains no former command/name.

- [ ] **Step 4: Verify launcher path/containment contract**

Run: `node packages/travis234-cli/bin/travis234.js --cwd . --dry-run --no-pull`

Expected: command mounts only workspace and `~/.travis234/sandbox-home`, uses
`/travis-home`, `TRAVIS234_*`, public Travis234 image, cap drop,
no-new-privileges, PID limit, and host UID/GID; it contains no `.env` or credential value.

- [ ] **Step 5: Record exact output summaries**

Record current date/commit, tool versions, collected/passed counts, artifact
names, npm file list, installed entry result, and launcher assertions in
`docs/verification/full-suite.md`.

### Task 4: Safely migrate the ignored `.env`

**Files:**
- Modify but never track: `.env`

**Interfaces:**
- Produces recognized `TRAVIS234_*` keys while preserving all values and generic provider keys.

- [ ] **Step 1: Verify ignore and permissions without printing values**

Run: `git check-ignore -q .env && chmod 600 .env && test "$(stat -f '%Lp' .env 2>/dev/null || stat -c '%a' .env)" = 600`

Expected: exit 0.

- [ ] **Step 2: Rename application prefixes mechanically**

Run: `perl -pi -e 's/^(?:APPV24|APPV231|APPV2)_/TRAVIS234_/' .env`

Expected: only key prefixes change; values/comments remain byte-for-byte otherwise.

- [ ] **Step 3: Verify key names only**

Run:

```bash
awk 'BEGIN{FS="="} /^[[:space:]]*(#|$)/{next} /^[A-Za-z_][A-Za-z0-9_]*[[:space:]]*=/{key=$1; gsub(/[[:space:]]/,"",key); print key}' .env | sort -u
```

Expected: application-specific keys begin only with `TRAVIS234_`; generic
provider keys such as `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and
`OPENROUTER_API_KEY` remain generic. No output contains `=` or values.

- [ ] **Step 4: Run non-interactive provider/model smoke**

Run: `artifacts/installed-venv/bin/travis234 --dotenv .env --list-models`

Expected: exit 0, at least one configured model, no secret value, and no former application name.

### Task 5: Extend the 21-prompt PTY scenario with feature assertions

**Files:**
- Modify: `evals/run_continuous_sdlc_eval.py`
- Modify: `evals/scenarios.json`
- Modify: `evals/tui_driver.py`
- Create: `evals/feature_audit.py`
- Modify: `evals/report.py`
- Modify: `tests/test_eval_harness.py`

**Interfaces:**
- Produces exactly 21 `ScenarioResult` rows, one session identity, `FeatureAudit`, sanitized transcript/trace, and per-fixture verifier codes.

- [ ] **Step 1: Add failing manifest/session tests**

```python
def test_continuous_manifest_has_exactly_21_prompts() -> None:
    scenarios = load_scenarios()
    assert len(scenarios) == 21
    assert len({scenario.id for scenario in scenarios}) == 21


def test_driver_records_one_session_identity_for_every_turn(fake_driver, tmp_path) -> None:
    results = run_continuous_scenarios(fake_scenarios(21), root=tmp_path, dotenv=tmp_path / ".env", driver_factory=fake_driver.start)
    assert len(results) == 21
    assert len({result.session_id for result in results}) == 1
    assert len({result.session_path for result in results}) == 1
```

- [ ] **Step 2: Add failing feature-audit contract**

```python
REQUIRED_FEATURES = {
    "read", "search", "write", "edit", "bash", "process_start", "process_poll",
    "process_write", "process_interrupt", "ctrl_c_escalation", "subagent",
    "compaction", "session_persistence", "guardrail", "capability_grant",
    "provider_model", "tdd", "debugging", "review", "package_build", "shutdown",
}


def test_feature_audit_requires_every_feature() -> None:
    audit = FeatureAudit.from_artifacts(FIXTURE_ARTIFACTS)
    assert audit.missing_features == ()
    assert set(audit.observed_features) == REQUIRED_FEATURES
```

- [ ] **Step 3: Make prompt manifest explicitly exercise features**

Keep the 21 existing complex Python/Node/frontend/SQLite/cross-language/release
fixtures. Strengthen selected prompts:

- parser refactor requests one reviewer subagent and integration of its result;
- streaming-memory scenario requests a managed producer, poll, stdin write, and cleanup;
- abort-controller scenario requests a managed fake server and interrupt cleanup;
- failing-suite diagnosis requests systematic root-cause evidence and guardrail-safe recovery;
- package-install retains one bounded `/allow package-install` grant;
- long-context scenario retains compaction after each dependency-safe group;
- release scenario requires wheel/npm pack inspection without publishing.

No prompt asks the model to reveal credentials or modify Travis234 itself.

- [ ] **Step 4: Extend trace/session/process audit**

Each result records `session_id` and `session_path` from trace/session events.
`FeatureAudit` maps tool start/end events and command events to the required set,
checks five scheduled compactions, confirms one session identity, verifies all
child process terminal events, checks subagent completion/integration, and scans
transcript/trace/conversation for secret patterns. The driver injects repeated
Ctrl-C into one deliberately stuck managed user command between coding turns and
requires interrupt/terminate/kill status events before continuing.

- [ ] **Step 5: Run eval harness tests green**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/test_eval_harness.py -q`

Expected: PASS with exactly 21 fake prompts, one fake session identity, and every
required feature observed by deterministic fixtures.

- [ ] **Step 6: Commit live acceptance harness**

```bash
git add evals tests/test_eval_harness.py
git commit -m "test: audit 21-prompt TUI feature scenario"
```

### Task 6: Run the actual TUI session in the background

**Files:**
- Runtime artifacts only: `artifacts/live-21/` (ignored)

**Interfaces:**
- Produces `report.md`, `report.json`, `trace.jsonl`, `conversation.jsonl`, sanitized `terminal.log`, 21 result JSON files, and persistent session JSONL.

- [ ] **Step 1: Start the installed TUI scenario**

Run:

```bash
TRAVIS234_CODING_AGENT_DIR="$PWD/artifacts/live-21/agent" \
PYTHONPATH=. artifacts/installed-venv/bin/python -m evals.run_continuous_sdlc_eval \
  --dotenv "$PWD/.env" \
  --output-dir "$PWD/artifacts/live-21" \
  --model-query "$TRAVIS234_ACCEPTANCE_MODEL_QUERY" \
  --model-index "${TRAVIS234_ACCEPTANCE_MODEL_INDEX:-1}" \
  --thinking medium \
  --temperature 0.2
```

Launch through the execution tool as a background PTY session and poll at intervals
under 60 seconds. Do not stream raw provider headers or `.env` contents.

- [ ] **Step 2: Require process exit zero and 21 passed results**

Run: `artifacts/installed-venv/bin/python -m evals.feature_audit artifacts/live-21`

Expected: exit 0; 21/21 scenarios passed, exactly one session ID/path, no missing
feature, no leaked secret, scheduled compactions observed, all child processes
terminal, and graceful shutdown observed.

- [ ] **Step 3: Re-run every fixture verifier independently**

Run: `artifacts/installed-venv/bin/python -m evals.verify_run artifacts/live-21`

Expected: exit 0 and all Python, Node, npm, source-contract, and package verifiers pass.

- [ ] **Step 4: Verify session persistence and resume using the actual entry**

Run: `TRAVIS234_CODING_AGENT_DIR="$PWD/artifacts/live-21/agent" artifacts/installed-venv/bin/travis234 --dotenv .env --cwd artifacts/live-21/workspace --continue --plain <<< '/session'`

Expected: resolves the same session ID/path recorded by all 21 turns and exits cleanly.

### Task 7: Completion audit and public publication

**Files:**
- Finalize: `docs/verification/acceptance-matrix.md`
- Finalize: `docs/verification/full-suite.md`
- Create: `docs/verification/live-21-summary.md`

**Interfaces:**
- Produces requirement-by-requirement completion evidence and public `origin/main`.

- [ ] **Step 1: Verify repository cleanliness and secret exclusion**

Run: `git status --short && git ls-files .env && git check-ignore -q .env`

Expected: clean status, `git ls-files .env` prints nothing, ignore check exits zero.

Run: `git grep -n -I -E '(sk-[A-Za-z0-9_-]{8,}|Bearer[[:space:]]+[A-Za-z0-9._-]{8,})' -- ':!docs/superpowers'`

Expected: no credential-like tracked content.

- [ ] **Step 2: Run machine completion audit**

Run: `PYTHONPATH=. .venv/bin/python scripts/verify_acceptance.py --require-current-commit`

Expected: all required matrix rows are `passed` with evidence generated from the current commit.

- [ ] **Step 3: Commit verification records**

```bash
git add docs/verification
git commit -m "docs: record Travis234 acceptance evidence"
```

- [ ] **Step 4: Re-run fast post-commit gates**

Run: `PYTHONPATH=. .venv/bin/python -m pytest tests/architecture tests/compaction tests/test_process_service.py tests/test_eval_harness.py -q && npm --prefix packages/travis234-cli test`

Expected: PASS on the committed tree.

- [ ] **Step 5: Push final `main` to the already-created public repository**

Run: `git push -u origin main`

Expected: push succeeds to `https://github.com/htooayelwinict/travis234`.

- [ ] **Step 6: Verify public remote state**

Run: `gh repo view htooayelwinict/travis234 --json name,visibility,url,defaultBranchRef`

Expected: name `travis234`, visibility `PUBLIC`, default branch `main` at the local HEAD commit.
