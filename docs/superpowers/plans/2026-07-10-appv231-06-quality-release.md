# appv231 Quality Evaluation and Release Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure appv231 as a coding agent through deterministic fault tests and twenty-one real-provider TUI scenarios, then prevent GHCR publication before code, image, and installed-runtime gates pass.

**Architecture:** Deterministic tests remain network-free and prove mechanics. A separate opt-in PTY harness drives the actual `appv231.cli` TUI with OpenRouter credentials, clean generated fixtures, fixed settings, scheduled compaction, sanitized event traces, and executable verifiers. CI builds and smokes a no-cache local image before the push job becomes eligible.

**Tech Stack:** Python 3.13 standard-library PTY/selectors/subprocess, pytest, JSON manifests, OpenRouter through appv231, Docker Buildx, Node.js test runner, GitHub Actions.

## Global Constraints

- Complete Plans 1-5 first.
- Do not edit compaction files or perform mutating git operations while creating or executing this plan; read-only status and diff checks are permitted.
- Never print, persist, or include `.env` values, API keys, authorization headers, or OAuth tokens in artifacts.
- Mocks are allowed for deterministic protocol tests but forbidden for the live quality score.
- Every live scenario uses a fresh generated directory outside the repository source tree.
- Default live settings are model query `mimo`, selection index `1`, thinking `medium`, and temperature `0.2`; command-line overrides must be recorded in the result metadata.
- Image builds in the release gate use no cache.
- This plan prepares workflow logic but does not publish an image or npm package.

---

### Task 1: Sanitized Evaluation Trace Contract

**Files:**
- Create: `appV2.3.1/appv231/coding_agent/eval_trace.py`
- Modify: `appV2.3.1/appv231/cli.py`
- Modify: `appV2.3.1/appv231/tui/interactive_mode.py`
- Create: `appV2.3.1/tests/test_eval_trace.py`

**Interfaces:**
- Produces CLI option: `--event-trace PATH`
- Produces: `SecretRedactor(secret_values: Iterable[str])`
- Produces: `EvalTraceWriter.write(event_type, fields) -> None`
- Produces JSONL events: `tui_ready`, `model_picker_ready`, `model_selected`, `turn_start`, `tool_end`, `compaction_end`, `turn_end`, `fatal`, `shutdown`
- Guarantees: trace contains identifiers, status, timing, and counts but no prompt/result bodies or credentials

- [ ] **Step 1: Write redaction and lifecycle tests**

```python
def test_eval_trace_records_lifecycle_without_sensitive_content(tmp_path):
    path = tmp_path / "trace.jsonl"
    writer = EvalTraceWriter(path, redactor=SecretRedactor(["sk-secret", "private prompt text"]))
    with pytest.raises(ValueError, match="unsafe trace field"):
        writer.write("tool_end", {"tool": "bash", "result": "private prompt text sk-secret"})
    writer.write("tool_end", {"tool": "bash", "status": "ok", "duration_ms": 5})
    text = path.read_text(encoding="utf-8")
    assert "tool_end" in text
    assert "duration_ms" in text
    assert "sk-secret" not in text
    assert "private prompt text" not in text
```

Add an end-to-end CLI test that starts a deterministic TUI turn and observes `tui_ready`, `turn_start`, `turn_end`, and `shutdown` in order.

- [ ] **Step 2: Implement a strict allowlist**

Do not redact an arbitrary event dictionary after collection. Build each event from an allowlist of safe fields:

```python
SAFE_FIELDS = {
    "run_id", "turn_id", "tool_call_id", "tool", "status", "error_code",
    "duration_ms", "input_tokens", "output_tokens", "compression_count",
    "provider", "model", "model_count", "picker_query",
}
```

Reject unknown fields in tests. Write mode-`0600` JSONL, flush terminal events, and never include headers, arguments, prompts, or result text.

- [ ] **Step 3: Wire optional tracing**

When `--event-trace` is absent, instantiate no writer and change no behavior. When present, `CodingApp`, AgentSession, and InteractiveMode emit only the typed lifecycle events.

- [ ] **Step 4: Run trace tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_eval_trace.py appV2.3.1/tests/test_cli.py -k trace
```

Expected: pass with no sensitive values in captured output.

### Task 2: Actual-TUI PTY Evaluation Harness

**Files:**
- Create: `appV2.3.1/evals/__init__.py`
- Create: `appV2.3.1/evals/schema.py`
- Create: `appV2.3.1/evals/fixtures.py`
- Create: `appV2.3.1/evals/tui_driver.py`
- Create: `appV2.3.1/evals/run_sdlc_eval.py`
- Create: `appV2.3.1/evals/README.md`
- Create: `appV2.3.1/tests/test_eval_harness.py`

**Interfaces:**
- Produces: `Scenario(id, setup, turns, compact_after, verifiers, timeout_seconds)` where each verifier is an argument tuple
- Produces: `TuiDriver.start(command, cwd, trace_path) -> TuiDriver`
- Produces: `send_line(text)`, `wait_for_event(type, timeout)`, `select_model(query, index)`, `close()`
- Produces command: `python -m evals.run_sdlc_eval`
- Produces result JSON without transcripts or secrets

- [ ] **Step 1: Write a fake-process PTY state-machine test**

```python
def test_driver_sends_turns_and_compacts_at_declared_intervals(fake_tui_process, tmp_path):
    scenario = Scenario(
        id="canary",
        setup="canary",
        turns=("first", "second", "third"),
        compact_after=(2,),
        verifiers=((sys.executable, "-c", "raise SystemExit(0)"),),
        timeout_seconds=10,
    )
    result = run_scenario(scenario, process_factory=fake_tui_process, root=tmp_path)
    assert fake_tui_process.lines == [
        "/model mimo", "<select:1>", "first", "second", "/compact", "third", "/exit"
    ]
    assert result.verifier_exit_codes == [0]
```

- [ ] **Step 2: Implement PTY startup**

Use `pty.openpty()` and `subprocess.Popen()` with the slave fd for stdin/stdout/stderr. The command is:

```text
python -m appv231.cli --cwd <fixture> --dotenv <path> --thinking medium --temperature 0.2 --event-trace <trace>
```

Pass the dotenv path as an argument without reading its contents. Preserve only a bounded ANSI-stripped diagnostic tail on failure.

- [ ] **Step 3: Synchronize on the event trace, not terminal wording**

Wait for `tui_ready` before setup commands, `turn_end` after each prompt, and `compaction_end` after `/compact`. The trace is authoritative; terminal output is diagnostic only.

- [ ] **Step 4: Implement model selection**

Send `/model mimo`, wait for `model_picker_ready`, then send `index - 1` Down-key sequences followed by Enter. Wait for `model_selected` and record its provider/model ID from safe metadata. Fail rather than silently using a different index.

- [ ] **Step 5: Execute verifiers without a shell**

Store verifier commands as argument arrays and call `subprocess.run(list, cwd=fixture, timeout=...)`. Capture bounded stdout/stderr and exit code. Never use `shell=True`.

- [ ] **Step 6: Add deterministic harness tests**

Cover timeout, model-picker failure, provider fatal event, scheduled compaction, verifier failure, process cleanup, ANSI-tail bounding, and secret redaction.

- [ ] **Step 7: Run harness tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_eval_harness.py appV2.3.1/tests/test_eval_trace.py
```

Expected: pass without network access.

### Task 3: Define SDLC Scenarios 01-07

**Files:**
- Create fixture builders in: `appV2.3.1/evals/fixtures.py`
- Create: `appV2.3.1/evals/scenarios.json`
- Extend: `appV2.3.1/tests/test_eval_harness.py`

**Interfaces:**
- Produces scenario IDs 01-07 with deterministic setup and verifier commands

- [ ] **Step 1: Add scenario 01 `python-cli-feature`**

Seed a Python CLI with text output and tests. Turns require adding `--format json`, preserving text mode, updating help/README, and running tests. Verifier:

```json
["python", "-m", "pytest", "-q"]
```

- [ ] **Step 2: Add scenario 02 `python-async-race`**

Seed an async cache whose duplicate concurrent misses call the loader twice. Require diagnosis, a per-key single-flight repair, cancellation behavior, and tests. Verifier: `python -m pytest -q`.

- [ ] **Step 3: Add scenario 03 `python-parser-refactor`**

Seed a 500-line parser with mixed lexing/validation and characterization tests. Require splitting it into focused modules without changing public imports, adding edge-case tests, and documenting ownership. Verifier: pytest plus `python -c "import package.parser"` as two verifier commands.

- [ ] **Step 4: Add scenario 04 `config-migration`**

Seed version-1 JSON config loading. Require version-2 nested settings, backward-compatible migration, malformed-file diagnostics, atomic persistence, and tests. Schedule `/compact` after turn 2. Verifier: pytest.

- [ ] **Step 5: Add scenario 05 `http-client-retry`**

Seed an httpx client with unconditional retries. Require retry classification, bounded backoff injection, cancellation, `Retry-After`, and fake-transport tests. Verifier: pytest with network disabled by fixture.

- [ ] **Step 6: Add scenario 06 `path-traversal-repair`**

Seed an archive extraction utility vulnerable to `../` and symlink escapes. Require exploit regression tests, canonical containment, and safe extraction. Verifier: pytest.

- [ ] **Step 7: Add scenario 07 `streaming-memory-bound`**

Seed a log collector retaining all chunks. Require a bounded tail plus complete mode-`0600` spool, binary replacement decoding, and a 10 MiB stress test. Schedule `/compact` after turn 2. Verifier: pytest.

- [ ] **Step 8: Validate manifest completeness**

```python
def test_scenarios_01_07_have_setup_turns_compaction_and_verifiers():
    scenarios = load_scenarios()
    selected = scenarios[:7]
    assert [item.id[:2] for item in selected] == [f"{index:02d}" for index in range(1, 8)]
    assert all(item.turns and item.verifiers for item in selected)
```

### Task 4: Define SDLC Scenarios 08-14

**Files:**
- Extend: `appV2.3.1/evals/fixtures.py`
- Extend: `appV2.3.1/evals/scenarios.json`
- Extend: `appV2.3.1/tests/test_eval_harness.py`

**Interfaces:**
- Produces scenario IDs 08-14

- [ ] **Step 1: Add scenario 08 `jsonl-session-recovery`**

Seed an append-only session store that crashes on a partial final record and hides middle corruption. Require tail quarantine/recovery, hard middle failure, atomic append, and tests. Verifier: pytest.

- [ ] **Step 2: Add scenario 09 `node-cli-dry-run`**

Seed a Node CLI that mutates files. Require `--dry-run`, identical planning output, no writes in dry mode, help updates, and Node tests. Verifier:

```json
["node", "--test"]
```

- [ ] **Step 3: Add scenario 10 `node-package-install`**

Seed a zero-dependency Node formatter with tests requiring the `kleur` package. Before the implementation prompt, send `/allow package-install`. Require adding the dependency, using it only when color is enabled, lockfile update, and tests. Verifiers: `npm test` and `npm ls kleur`.

- [ ] **Step 4: Add scenario 11 `node-abort-controller`**

Seed a streaming fetch wrapper that ignores abort. Require signal propagation, cleanup, timeout composition, and Node tests using a fake server. Verifier: `node --test`.

- [ ] **Step 5: Add scenario 12 `javascript-module-refactor`**

Seed a large CommonJS workflow engine. Require extracting scheduler/state/reporter modules while preserving the entry API and adding ordering tests. Schedule `/compact` after turn 2. Verifier: `node --test`.

- [ ] **Step 6: Add scenario 13 `frontend-accessibility`**

Seed a keyboard-inaccessible task board in HTML/CSS/JS plus DOM-source tests. Require semantic buttons, focus behavior, labels, contrast tokens, reduced motion, and tests. Verifier: `node --test`.

- [ ] **Step 7: Add scenario 14 `frontend-responsive-overflow`**

Seed a dashboard with toolbar/card text overlap at narrow widths and source-based viewport assertions. Require stable grid constraints, wrapped labels, no horizontal overflow, and tests. Verifier: `node --test`.

- [ ] **Step 8: Assert scenarios 08-14 are isolated and deterministic**

Build each fixture twice, hash all seeded files, and assert equal hashes with no `.env`, credentials, `node_modules`, or cache directories.

### Task 5: Define SDLC Scenarios 15-21

**Files:**
- Extend: `appV2.3.1/evals/fixtures.py`
- Complete: `appV2.3.1/evals/scenarios.json`
- Extend: `appV2.3.1/tests/test_eval_harness.py`

**Interfaces:**
- Produces all 21 required scenarios

- [ ] **Step 1: Add scenario 15 `sqlite-migration`**

Seed a Python SQLite app at schema v1. Require transactional v2 migration, idempotency, rollback on malformed legacy rows, compatibility tests, and migration notes. Verifier: pytest.

- [ ] **Step 2: Add scenario 16 `python-node-contract`**

Seed a Python producer and Node consumer with mismatched event schemas. Require one documented JSON contract, compatible encoders/decoders, malformed-event errors, and both test suites. Verifiers: pytest and `node --test`.

- [ ] **Step 3: Add scenario 17 `failing-suite-diagnosis`**

Seed three failures where only two are implementation bugs and one test encodes the stated requirement. Require root-cause notes, implementation-only fixes, and no weakened expectations. Verifier: pytest plus a hash assertion that protected expectations remain unchanged.

- [ ] **Step 4: Add scenario 18 `multi-file-domain-rename`**

Seed a Python package using an obsolete domain term across API, persistence, docs, and tests. Require a backward-compatible external alias, complete internal rename, migration note, and no stale internal references. Verifiers: pytest and an `rg`-equivalent Python scan.

- [ ] **Step 5: Add scenario 19 `docs-code-alignment`**

Seed a CLI whose README examples and flags drift from code. Require choosing code as authority for one case and documented behavior as authority for another, repairing both, adding executable docs examples, and tests. Verifier: pytest.

- [ ] **Step 6: Add scenario 20 `long-context-compaction`**

Seed a seven-module service and issue brief with twelve nonlocal requirements. Use four implementation turns and schedule `/compact` after turns 1, 2, and 3. The final verifier checks every original requirement, cross-module behavior, and absence of reverted earlier work. Verifier: pytest.

- [ ] **Step 7: Add scenario 21 `release-packaging`**

Seed a Python package, Node launcher, and Dockerfile with version mismatch, private npm metadata, missing runtime tool, and a release smoke test. Require aligned metadata, publishable npm dry-run, non-root runtime, Node/npm availability, and no actual publish. Verifiers: pytest, `node --test`, and `npm pack --dry-run --json`.

- [ ] **Step 8: Validate exactly 21 scenarios**

```python
def test_manifest_contains_exactly_21_unique_scenarios():
    scenarios = load_scenarios()
    assert len(scenarios) == 21
    assert len({item.id for item in scenarios}) == 21
    assert [item.id[:2] for item in scenarios] == [f"{index:02d}" for index in range(1, 22)]
```

### Task 6: Live Scoring and Reports

**Files:**
- Create: `appV2.3.1/evals/scoring.py`
- Create: `appV2.3.1/evals/report.py`
- Modify: `appV2.3.1/evals/run_sdlc_eval.py`
- Extend: `appV2.3.1/tests/test_eval_harness.py`

**Interfaces:**
- Produces per-scenario JSON and aggregate Markdown/JSON reports
- Produces metrics: verifier pass, turns, tool calls/failures, policy blocks, compactions, retained requirements, tokens, cost, latency
- Exit code is nonzero when any mandatory scenario fails

- [ ] **Step 1: Define deterministic scoring**

Use verifier pass as the primary gate. Secondary metrics do not convert a verifier failure into success. Score policy false positives/negatives from scenario expectations, not model self-report.

- [ ] **Step 2: Bound and sanitize report data**

Reports contain scenario IDs, resolved model ID, numeric settings, safe lifecycle counts, verifier command names/exit codes, bounded failure tails, and scores. They exclude prompts, generated source contents, environment values, and authorization data.

- [ ] **Step 3: Add report tests**

Test all-pass, partial failure, timeout, compaction-count mismatch, secret-shaped diagnostics, and aggregate exit code.

- [ ] **Step 4: Document the live command**

```bash
PYTHONPATH=appV2.3.1 uv run --dev python -m evals.run_sdlc_eval \
  --dotenv .env \
  --model-query mimo \
  --model-index 1 \
  --thinking medium \
  --temperature 0.2 \
  --output-dir /tmp/appv231-sdlc-eval
```

Expected: 21 isolated scenario results plus aggregate JSON/Markdown. The command must refuse to overwrite a nonempty output directory without an explicit `--resume`.

- [ ] **Step 5: Run deterministic evaluation tests only**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_eval_harness.py appV2.3.1/tests/test_eval_trace.py
```

Expected: pass without API calls.

### Task 7: No-Cache Installed-Image Smoke Gate

**Files:**
- Modify: `Dockerfile.appv231.release` only if a smoke failure proves a runtime omission
- Create: `appV2.3.1/evals/container_smoke.py`
- Extend: `appV2.3.1/tests/test_sandbox_launcher.py`
- Extend: `packages/appv231-cli/test/appv231-cli.test.js`

**Interfaces:**
- Produces: deterministic container smoke command with no provider credentials
- Verifies: non-root user, app CLI, Node, npm, writable workspace, local package install, deterministic TUI lifecycle

- [ ] **Step 1: Build without cache**

```bash
docker build --no-cache -f Dockerfile.appv231.release -t appv231:hardening-smoke .
```

Expected: successful local image build.

- [ ] **Step 2: Verify image user**

```bash
docker run --rm --entrypoint id appv231:hardening-smoke -un
```

Expected output: `appv231`.

- [ ] **Step 3: Verify installed runtimes separately**

```bash
docker run --rm --entrypoint appv231 appv231:hardening-smoke --help
docker run --rm --entrypoint node appv231:hardening-smoke --version
docker run --rm --entrypoint npm appv231:hardening-smoke --version
```

Expected: all commands exit `0`.

- [ ] **Step 4: Verify writable local dependency installation**

Run `container_smoke.py` with a temporary host workspace. It creates a minimal package, mounts only that workspace, runs `npm install --ignore-scripts --no-audit --no-fund is-number`, verifies `node_modules/is-number`, and removes the temporary workspace. No global install or sudo is used.

- [ ] **Step 5: Drive a deterministic TUI workflow inside the image**

Use the PTY harness with the repository's deterministic provider fixture, submit one coding prompt and `/compact`, assert `turn_end`, `compaction_end`, and clean `/exit`. This image smoke is deterministic and does not replace the live OpenRouter evaluation.

- [ ] **Step 6: Run launcher tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_sandbox_launcher.py
node --test packages/appv231-cli/test/appv231-cli.test.js
```

Expected: pass.

### Task 8: Release Workflow Gates Before Push

**Files:**
- Modify: `.github/workflows/appv231-release-image.yml`
- Modify: `pyproject.toml` dev dependencies to add `pyyaml>=6,<7`
- Create: `appV2.3.1/tests/test_release_workflow.py`

**Interfaces:**
- Produces workflow jobs: `test`, `image-smoke`, `build-and-push`
- Guarantees: push job has `needs: [test, image-smoke]`
- Guarantees: both image builds set no-cache

- [ ] **Step 1: Write structural workflow tests**

Parse workflow YAML with `yaml.safe_load()` and assert:

```python
from pathlib import Path
import yaml

def load_release_workflow():
    path = Path(__file__).parents[2] / ".github" / "workflows" / "appv231-release-image.yml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))

def test_release_push_depends_on_tests_and_smoke():
    workflow = load_release_workflow()
    push = workflow["jobs"]["build-and-push"]
    assert set(push["needs"]) == {"test", "image-smoke"}
    assert push["steps"][-1]["with"]["push"] is True
    assert push["steps"][-1]["with"]["no-cache"] is True
```

Also assert no earlier step has `push: true`.

- [ ] **Step 2: Add `test` job**

Install the locked uv environment, run the complete Python suite, run Node launcher tests, and run `npm pack --dry-run --json` without publishing.

- [ ] **Step 3: Add `image-smoke` job**

Build the current platform image with `load: true`, `push: false`, and `no-cache: true`; then run `container_smoke.py` against it.

- [ ] **Step 4: Gate the multi-platform push job**

Add `needs: [test, image-smoke]`. Keep registry login only in the push job. Set `no-cache: true` on `docker/build-push-action` and preserve explicit amd64/arm64 platforms and tags.

- [ ] **Step 5: Run workflow tests**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_release_workflow.py
```

Expected: pass.

### Task 9: Quality and Release Completion Gate

**Files:**
- Modify: none

**Interfaces:**
- Produces evidence required for the roadmap completion audit

- [ ] **Step 1: Run all deterministic tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests
node --test packages/appv231-cli/test/appv231-cli.test.js
```

Expected: zero failures.

- [ ] **Step 2: Run the complete live evaluation**

Run the documented 21-scenario command with the authorized `.env`. Expected: all mandatory verifiers pass; report records actual model/settings and scheduled compactions.

- [ ] **Step 3: Run the no-cache image gate**

Build `appv231:hardening-smoke` with `--no-cache`, then run `container_smoke.py`. Expected: non-root CLI, Node, npm, local install, and deterministic TUI checks pass.

- [ ] **Step 4: Verify architecture, redzone, and release ordering**

```bash
PYTHONPATH=appV2.3.1 uv run --dev pytest -q -p no:cacheprovider appV2.3.1/tests/test_agent_core_boundary.py appV2.3.1/tests/test_release_workflow.py
git diff --exit-code -- appV2.3.1/appv231/compaction
```

Expected: tests pass and no compaction diff.

- [ ] **Step 5: Stop before publication**

Report image tag, test totals, live scenario pass rate, and remaining failures. Do not push GHCR, publish npm, commit, tag, release, or perform any mutating git operation without a new explicit Lewis request.
