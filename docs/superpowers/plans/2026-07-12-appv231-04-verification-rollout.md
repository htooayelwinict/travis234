# appv231 Process Orchestration v2 Verification and Rollout Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove every process, TUI, session, compaction, and performance finding is closed in source and the production image before any release action.

**Architecture:** A high-level regression module exercises cross-component contracts that focused unit tests cannot prove. Verification then expands through the complete Python suite, direct attached TUI sessions using the real provider, a no-cache production image, npm launcher packaging, leak/performance checks, and immutable redzone gates.

**Tech Stack:** pytest, real `python -m appv231.cli` TUI through a PTY, OpenRouter credentials loaded from `.env` without printing, Docker production image, npm pack/build, Git diff gates.

## Global Constraints

- Complete plans 01, 02, and 03 first, in that order unless a task explicitly states otherwise.
- Do not modify any file under `appV2.3.1/appv231/agent/`.
- Do not modify any file under `appV2.3.1/appv231/compaction/`.
- Do not use an eval runner, hidden session API, mock provider, or separate session per prompt for TUI acceptance.
- Attach to the actual `python -m appv231.cli` process and type through its terminal input.
- Never print, copy, persist, or include `.env` values in logs/artifacts.
- Use the real OpenRouter configuration already present in `.env`.
- Use model selection query `mimo`, choose result 1, thinking `medium`, and temperature `0.2`.
- Use a temporary demo directory, never the repository as scenario cwd.
- Do not prefix scenario prompts with labels or scenario names.
- Build the production image with `--no-cache`.
- Do not push GHCR, publish npm, create a release, or modify deployment workflows in this plan.

---

## Finding Coverage Matrix

| Proven finding | Primary proof |
| --- | --- |
| Long chatty job consumed repeated model polls | `test_required_chatty_job_uses_one_wait_result` plus direct TUI transcript |
| Cooperative process wait hit generic guardrail | coding-policy test and absence of guardrail halt in TUI JSONL |
| Terminal output expired before delayed poll | restart/TTL completion-store regression |
| Completion retention could become another full-scan append path | indexed 10,000-row/count/size pruning and two-store concurrency tests |
| Durable tail lookup could load the whole artifact | sparse 64 MiB fixture with a bounded read-byte budget |
| Session retained stale running handles | provider overlay and exit/resume TUI check |
| Compaction dropped process handles | process-ledger compaction regression and `/compact` TUI check |
| `!` blocked behind active turn | live TUI command during process wait |
| `/allow` blocked behind active turn | same-turn package capability integration test and live control check |
| Ctrl-C needed repeated presses | focused cancellation live test |
| Steering queue race lost messages | deterministic barrier stress test |
| SessionStore append was quadratic | 2,000-append parse-byte budget |
| Spool failure published exited | fault-injection state-machine regression |
| Active output spool was unbounded | per-process/app-wide budget regression with `output_limit` terminal state |
| Hidden workspace consumed global slots | owner-scope integration regression |
| `setsid` descendant survived timeout | Linux production-image process-tree test |
| Detached large output lacked artifact | 2 MiB one-result borrowed-artifact regression, including restart/read authorization |
| Process actions lacked execution order | batched wait/terminate sequential-order regression |
| Core iteration summary adds one call | unchanged behavior documented; no redzone edit; process wait avoids routine exhaustion |

### Task 1: Cross-Component Regression Matrix

**Files:**
- Create: `appV2.3.1/tests/test_process_v2_regressions.py`

**Interfaces:**
- Consumes: completed runtime, TUI, mailbox, completion, overlay, compaction, and SessionStore APIs.
- Produces: one authoritative named regression per original failure.
- Guarantees: tests observe public/cross-component behavior rather than private implementation alone.

- [ ] **Step 1: Add the chatty-job single-wait regression**

```python
import shlex
import sys
from pathlib import Path

from appv231.ai.providers.faux import create_faux_provider, faux_model, text_response_events, tool_call_response_events
from appv231.ai.stream import register_api_provider, reset_api_providers
from appv231.app import CodingApp


def setup_function() -> None:
    reset_api_providers()


def test_required_chatty_job_uses_one_wait_result(tmp_path: Path) -> None:
    source = "import time; [(print(i, flush=True), time.sleep(.002)) for i in range(120)]; time.sleep(.2)"
    command = f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"
    process_actions: list[str] = []

    def provider(model, context):
        results = [message for message in context.messages if message.role == "toolResult"]
        if not results:
            return tool_call_response_events(
                model,
                "bash",
                {"command": command, "yield_time_ms": 0},
            )
        latest = results[-1]
        if latest.tool_name == "bash":
            process_actions.append("wait")
            return tool_call_response_events(
                model,
                "process",
                {
                    "action": "wait",
                    "session_id": latest.details["sessionId"],
                    "cursor": latest.details["nextCursor"],
                    "wait_time_ms": 60_000,
                },
            )
        return text_response_events(model, "complete")

    register_api_provider(create_faux_provider(provider))
    app = CodingApp(cwd=str(tmp_path), model=faux_model(), agent_dir=str(tmp_path / "agent"))
    try:
        app.run_turn("run the chatty job and wait for its result")

        tool_results = [message for message in app.messages if message.role == "toolResult"]
        assert [message.tool_name for message in tool_results] == ["bash", "process"]
        assert process_actions == ["wait"]
        assert tool_results[-1].details["status"] == "exited"
        assert "119" in tool_results[-1].content[0].text
    finally:
        app.close()
```

- [ ] **Step 2: Add the remaining high-level regressions**

Implement these exact tests with the listed setup and assertions. Reuse the
concrete fixtures created by Plans 01-03; do not duplicate their fake process
implementations.

| Test | Setup and exact assertions |
| --- | --- |
| `test_terminal_output_recovers_after_live_ttl_and_new_app` | Complete a process, advance the live-service clock beyond 900 seconds, close the app, construct a new app over the same agent directory, poll from cursor zero, and assert terminal status, exit code, complete output, and `durableOutput=true`. |
| `test_process_overlay_marks_unrecoverable_running_handle_unavailable` | Persist a historical running result with no completion record, open a new app, capture provider context, and assert exactly one non-displayed overlay containing `status=unavailable` and `reason=application-restarted`. |
| `test_compaction_round_trip_preserves_live_process_ledger` | Start a running process, compact, rebuild SessionStore context, and assert the process ID/status exists in `managedProcesses` and converted compaction text. |
| `test_bang_and_allow_complete_while_turn_waits` | Hold an active process wait with a barrier, submit `!printf ready` and `/allow package-install 1`, and assert both return before releasing the barrier, output renders, and capability remaining is one. |
| `test_single_ctrl_c_routes_to_focused_operation` | Run an agent turn and focused user command, send one Ctrl-C, and assert one user interrupt, zero agent aborts, and continued TUI input. |
| `test_duplicate_concurrent_steering_messages_both_arrive` | Enqueue equal text from two threads during provider streaming and assert two distinct queue IDs and two delivered user messages. |
| `test_spool_failure_is_failed_not_exited` | Fault `SanitizedOutputSpool.append`, emit output, and assert terminal `failed`, `failureCode=output_failure`, and process-tree stop signal. |
| `test_live_spool_budget_is_bounded_and_not_a_timeout` | Configure tiny per-process/app-wide spool limits, cross each with concurrent producers, and assert bounded captured bytes, exact accounting after eviction, producer-only tree stop, `failed`, `failureCode=output_limit`, and never `timed_out`. |
| `test_owner_scope_quota_does_not_starve_other_workspace` | Fill one workspace's four slots, start one job in a second workspace, and assert the second starts while a fifth first-workspace job is rejected. |
| `test_two_megabyte_detached_output_has_durable_artifact` | Emit 2 MiB, complete, wait from cursor zero, and assert one result with `nextCursor == outputSize`, bounded tail/truncation metadata, a mode-0600 path, readable `artifactId`, exact 2 MiB sanitized length, survival after registry close, and reauthorization after app restart. |
| `test_batched_process_controls_execute_sequentially` | Emit wait then terminate in one assistant response, record service call order, and assert `wait` completes/returns before `terminate` begins. |

Each test must also assert the original wrong outcome is absent, not merely
that a new field exists.

- [ ] **Step 3: Run the matrix before final implementation cleanup**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -vv -p no:cacheprovider appV2.3.1/tests/test_process_v2_regressions.py
```

Expected: all tests pass. Any failure returns to the owning earlier plan; do not
weaken expected behavior in this matrix.

- [ ] **Step 4: Stress the matrix five times**

```bash
for run in 1 2 3 4 5; do
  TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_process_v2_regressions.py || exit 1
done
```

Expected: five complete passes.

- [ ] **Step 5: Commit the cross-component matrix**

```bash
git add appV2.3.1/tests/test_process_v2_regressions.py
git commit -m "test(appv231): lock process v2 regressions"
```

### Task 2: Documentation and Operator Contract

**Files:**
- Modify: `appV2.3.1/README.md`
- Modify: `packages/appv231-cli/README.md`
- Extend: `appV2.3.1/tests/test_cli.py`

**Interfaces:**
- Produces: exact user/model distinction among yield, poll, wait, and execution timeout; async `!` behavior; durable terminal recovery limits.
- Guarantees: docs no longer describe `!` as synchronous or imply polling is the required wait path.

- [ ] **Step 1: Add documentation contract assertions**

```python
def test_readme_documents_process_wait_and_async_user_shell() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "process.wait" in readme
    assert "does not change the command timeout" in readme
    assert "command is not killed" in readme
    assert "64 MiB per process" in readme
    assert "output_limit" in readme
    assert "!command and !!command run asynchronously" in readme
    assert "cannot reattach a running process after an application restart" in readme
```

- [ ] **Step 2: Run and witness stale documentation**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_cli.py -k readme_documents_process_wait
```

Expected: failure because the README still says user shell shortcuts are
synchronous and only describes poll.

- [ ] **Step 3: Update managed-command documentation**

Document these exact contracts in both READMEs:

```text
- bash's default 10-second yield does not kill the command.
- process.poll is for quick/interactive incremental observation.
- process.wait waits 1-900 seconds for terminal state without returning on every output chunk.
- process.wait duration does not change bash.timeout.
- if a wait deadline expires first, it returns `running`; the command is not killed and another wait can continue.
- terminal metadata/output is recoverable for seven days subject to a 256 MiB bounded store.
- live sanitized output defaults to 64 MiB per process and 512 MiB app-wide; reaching a spool limit fails/stops the producer with `output_limit` but elapsed time alone never does.
- running processes still cannot be reattached after app/container restart.
- !command and !!command run asynchronously; !! remains excluded from model context.
- /allow package-install can be granted while a turn is active.
- user_bash extension handlers keep their payload/order but run on a command worker and custom operations must honor cancellation.
```

Describe Ctrl-C focus priority and `/processes` control for agent/user jobs.

- [ ] **Step 4: Run docs/CLI tests**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests/test_cli.py -k "readme or help or process"
```

Expected: pass.

- [ ] **Step 5: Commit operator docs**

```bash
git add appV2.3.1/README.md packages/appv231-cli/README.md appV2.3.1/tests/test_cli.py
git commit -m "docs(appv231): explain process wait and async shell"
```

### Task 3: Full Source Test and Static Gate

**Files:**
- Verify only; production edits return to their owning plan.

**Interfaces:**
- Consumes: complete source tree.
- Produces: full-suite and source-integrity evidence.

- [ ] **Step 1: Run syntax and package import checks**

```bash
.venv/bin/python -m compileall -q appV2.3.1/appv231
PYTHONPATH=appV2.3.1 .venv/bin/python -c "import appv231; import appv231.coding_agent.processes.completions; import appv231.tui.user_commands"
```

Expected: exit zero with no output.

- [ ] **Step 2: Run the complete Python suite**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests
```

Expected: every collected test passes; report exact pass/skip counts.

- [ ] **Step 3: Run process/session/TUI tests under repetition**

```bash
for run in 1 2 3; do
  TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider \
    appV2.3.1/tests/test_process_v2_regressions.py \
    appV2.3.1/tests/test_process_service.py \
    appV2.3.1/tests/test_process_local.py \
    appV2.3.1/tests/test_session_store_recovery.py \
    appV2.3.1/tests/test_session_store_performance.py \
    appV2.3.1/tests/test_tui_user_commands.py \
    appV2.3.1/tests/test_tui.py \
    -k "process or wait or bang or allow or ctrl_c or steering or session_store" || exit 1
done
```

Expected: three passes without flakes or lingering non-daemon threads.

- [ ] **Step 4: Verify no tracked generated files**

```bash
git status --short
git diff --check
```

Expected: only intentional source/docs/tests and unrelated pre-existing entries;
no caches, process logs, session files, or credentials are staged.

### Task 4: Direct Source TUI Five-Prompt Acceptance Plus Resume Audit

**Files:**
- Runtime verification only.
- Store the sanitized result matrix under a temporary directory, not the repository.

**Interfaces:**
- Consumes: actual TUI entry point and real OpenRouter configuration.
- Produces: one attached session transcript and JSONL evidence across exit/resume.

- [ ] **Step 1: Prepare an isolated demo workspace**

```bash
demo="$(mktemp -d /tmp/appv231-process-v2-source.XXXXXX)"
agent_home="$(mktemp -d /tmp/appv231-process-v2-source-home.XXXXXX)"
printf 'print("demo")\n' > "$demo/app.py"
printf '# Demo\n' > "$demo/README.md"
```

Do not use the repository as `--cwd`.

- [ ] **Step 2: Start the actual TUI process**

```bash
APPV231_CODING_AGENT_DIR="$agent_home/agent" \
PYTHONPATH="$PWD/appV2.3.1" "$PWD/.venv/bin/python" -m appv231.cli \
  --cwd "$demo" --dotenv "$PWD/.env" --thinking medium --temperature 0.2
```

Attach through a real PTY. Do not print environment variables. In the TUI,
type `/model mimo`, press Enter, type `1`, and press Enter.

- [ ] **Step 3: Send prompt 1 and controls during the wait**

Send exactly:

```text
Create a Python script that prints one numbered progress line per second for twenty seconds, run it as a managed command, and wait for its final result before continuing.
```

After the process wait begins, enter these controls through the same TUI:

```text
!printf 'user-shell-responsive\n'
/allow package-install 1
```

Expected: user shell output and capability acknowledgment appear before the
agent's twenty-second job finishes; the TUI still accepts input.

- [ ] **Step 4: Send prompts 2 and 3**

```text
Install no packages. Show the completed command's exit status and confirm that all twenty progress lines were observed without repeatedly polling for each line.
```

```text
Start another Python progress command for thirty seconds as an intentional background job, then update README.md with its purpose while it continues running and report its process ID and current state.
```

Expected: first answer is terminal and evidence-based; second performs useful
work after detachment and reports a live handle.

- [ ] **Step 5: Compact and send prompts 4 and 5**

Enter `/compact`, wait for visible completion, then send:

```text
After compaction, inspect the background process using the appropriate process operation and report its current state without repeating output already consumed.
```

```text
Wait for that background process to finish, verify README.md still contains the intended update, and summarize the two process results plus the user shell result.
```

Expected: process ID survives compaction context, final wait reaches terminal,
and the summary distinguishes agent jobs from user shell.

- [ ] **Step 6: Exit and resume the same session**

Enter `/session` and record only session ID/path metadata. Enter `/exit`, assign
the displayed absolute path to a shell variable named `session_path`, relaunch
the same source command and agent directory with `--session "$session_path"`,
select the same model if needed, and send:

```bash
APPV231_CODING_AGENT_DIR="$agent_home/agent" \
PYTHONPATH="$PWD/appV2.3.1" "$PWD/.venv/bin/python" -m appv231.cli \
  --cwd "$demo" --dotenv "$PWD/.env" --thinking medium --temperature 0.2 \
  --session "$session_path"
```

```text
Recap the completed process work from this resumed session and state whether any running operating-system process was reattached.
```

Expected: terminal results are recoverable; response correctly says no running
OS process was reattached.

- [ ] **Step 7: Inspect JSONL structurally without printing content**

Use `jq`/a read-only parser to record counts of user, assistant, tool result,
compaction, process wait, poll, guardrail code, and terminal status. Expected:

```text
process wait calls >= 2
same-handle rapid poll loop = 0
process guardrail hard stops = 0
compactions >= 1
session IDs = 1
malformed JSONL lines = 0
```

Delete the demo workspace, isolated agent home, and sanitized temporary matrix
after recording the result in the implementation report.

### Task 5: Full 21-Prompt Direct TUI Handoff Protocol

**Files:**
- Read: `appV2.3.1/evals/README.md`
- Read: `appV2.3.1/evals/scenarios.json`
- Runtime verification only; keep notes outside the repository and demo workspace.

**Interfaces:**
- Consumes: the repository's authoritative 21-scenario definitions and direct-TUI protocol.
- Produces: a 21-row prompt/response/verifier matrix over one persistent conversation and three attached TUI processes.
- Guarantees: five visible compactions, two exit/resume boundaries, one unchanged session ID/file, actual user input, and no mock/eval-runner substitution.

- [ ] **Step 1: Re-read and adopt the checked-in protocol exactly**

```bash
sed -n '17,90p' appV2.3.1/evals/README.md
jq 'length' appV2.3.1/evals/scenarios.json
```

Expected: protocol requires 21 prompts and JSON reports `21`. The attached
process must be `python -m appv231.cli`; `TuiDriver`, `run_sdlc_eval`,
`run_continuous_sdlc_eval`, hidden APIs, and mock providers are forbidden.

- [ ] **Step 2: Create fresh fixture and evidence directories**

Use the checked-in fixture preparation API only to create files before TUI
startup; do not use it to send prompts.

```bash
demo="$(mktemp -d /tmp/appv231-sdlc-direct.XXXXXX)"
notes="$(mktemp -d /tmp/appv231-sdlc-notes.XXXXXX)"
PYTHONPATH="$PWD/appV2.3.1" "$PWD/.venv/bin/python" - \
  "$demo" "$PWD/appV2.3.1/evals/scenarios.json" <<'PY'
import json
import sys
from pathlib import Path

from evals.fixtures import build_fixture

demo = Path(sys.argv[1])
scenarios = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
for scenario in scenarios:
    build_fixture(
        scenario["setup"],
        demo / "scenarios" / scenario["id"],
    )
PY
```

Require exactly 21 non-empty directories under `"$demo/scenarios"`. Do not edit
fixture content to make scenarios easier.

- [ ] **Step 3: Start the first actual TUI and select the model**

```bash
APPV231_CODING_AGENT_DIR="$notes/agent" \
PYTHONPATH="$PWD/appV2.3.1" \
  "$PWD/.venv/bin/python" -m appv231.cli \
  --cwd "$demo" \
  --dotenv "$PWD/.env" \
  --thinking medium \
  --temperature 0.2
```

At the visible prompt type `/model mimo`, Enter, `1`, Enter. Require visible
switch to `openrouter/xiaomi/mimo-v2.5-pro` and `medium` in the footer.

- [ ] **Step 4: Execute prompts 1-7 and the first resume boundary**

For each scenario, read its `turns` array from `scenarios.json`, combine those
requirements into one natural end-user prompt, and include its absolute
`$demo/scenarios/<id>` directory inline. Type it manually. Do not prefix the
prompt with scenario ID/name. Wait for visible `Idle`, copy the exact visible
final response to the notes matrix, then run every `verifiers` command with
that scenario directory as the command cwd.

After prompts 4, type `/compact` and wait for visible completion. After prompt
7, type `/session`, record the full ID/path, `/exit`, require exit zero, then
relaunch the same command with `--continue`. Require unchanged ID/path, restored
history, model, and thinking before prompt 8.

- [ ] **Step 5: Execute prompts 8-14 and the picker resume boundary**

After prompts 8 and 12, type `/compact` and wait for `Idle`. Before prompt 10,
type `/allow package-install` and require the one-use confirmation before
sending the prompt. After prompt 14, record `/session`, exit zero, relaunch with
`--resume`, choose the recorded session in the visible picker, and require the
same ID/path before prompt 15.

- [ ] **Step 6: Execute prompts 15-21 and final checks**

After prompts 16 and 20, type `/compact` and wait for `Idle`. After prompt 21,
type `/session` and `/exit`; require the same ID/path and exit zero. The matrix
must contain exact prompt, visible final response, model/thinking, verifier exit
codes, compaction count, duration, and failure/guardrail notes for every row.

- [ ] **Step 7: Apply strict restart-on-fix discipline**

On a bounded stall, send one real Ctrl-C and require `Aborting`, `Operation
aborted`, and return to idle. If investigation proves a runtime defect, add a
failing regression and root-cause fix in the owning plan, rerun focused/full
tests, discard this partial matrix and demo directory, then restart from prompt
1. Never continue a partially fixed 21-prompt run.

- [ ] **Step 8: Verify the final JSONL structurally**

Read the recorded session file without printing prompt/tool bodies. Require:

```text
user scenarios = 21
session headers = 1
session IDs observed by /session = 1
visible compactions = 5
malformed JSONL lines = 0
process guardrail hard-stop loops = 0
all external verifiers = exit 0
all three TUI processes = exit 0
```

Keep the sanitized matrix for the final report, then remove demo/notes after the
report is accepted. This task never performs Git, GHCR, or npm publication.

### Task 6: No-Cache Production Image and TUI Acceptance

**Files:**
- Runtime verification only.

**Interfaces:**
- Consumes: `Dockerfile.appv231.release` and complete source checkout.
- Produces: local production-image evidence; no registry push.

- [ ] **Step 1: Build the production image without cache**

```bash
docker build --no-cache --progress=plain -f Dockerfile.appv231.release -t appv231-process-v2:verify .
```

Expected: build exits zero and installs `psutil`, Node, npm, sudo, and appv231.

- [ ] **Step 2: Run container dependency and descendant checks**

```bash
docker run --rm --entrypoint sh appv231-process-v2:verify -lc \
  'python -c "import appv231,psutil" && node --version && npm --version && sudo -n true'
```

Expected: exit zero; no password prompt.

Run the Linux-only process tests from a test-mounted checkout or a dedicated
test stage:

```bash
docker run --rm \
  -v "$PWD/appV2.3.1/tests:/tests:ro" \
  --entrypoint python appv231-process-v2:verify \
  -m pytest -q -p no:cacheprovider /tests/test_process_local.py -k 'setsid or descendant'
```

Expected: escaped descendants are gone after timeout/close.

- [ ] **Step 3: Start actual container TUI with isolated persisted home**

```bash
demo="$(mktemp -d /tmp/appv231-process-v2-image.XXXXXX)"
agent_home="$(mktemp -d /tmp/appv231-process-v2-home.XXXXXX)"
docker run --rm -it \
  --env-file .env \
  -e HOME=/agent-home \
  -e APPV231_CODING_AGENT_DIR=/agent-home/agent \
  -v "$demo:/workspace:rw" \
  -v "$agent_home:/agent-home:rw" \
  appv231-process-v2:verify --cwd /workspace --thinking medium --temperature 0.2
```

In the real TUI type `/model mimo`, press Enter, type `1`, and press Enter.

- [ ] **Step 4: Send three container prompts and active controls**

```text
Create and run a Python command that prints progress for fifteen seconds, then wait for its final output and verify its exit code.
```

While waiting, enter:

```text
!node --version
/allow package-install 1
```

Then send:

```text
Start a twenty-second command as an intentional background process, write NOTES.md while it runs, and report the process state.
```

Enter `/compact`, then send:

```text
Wait for the background process to finish and confirm its terminal result and NOTES.md survived compaction.
```

Expected: responsive controls, no rapid poll loop, terminal artifacts, one
visible compaction, and final idle state.

- [ ] **Step 5: Exit, resume, and verify durable terminal output**

Exit, restart the image against the same `agent_home`, resume the prior session,
and ask for terminal process recap. Expected: terminal state/output resolves;
no running process reattachment is claimed.

```bash
docker run --rm -it \
  --env-file .env \
  -e HOME=/agent-home \
  -e APPV231_CODING_AGENT_DIR=/agent-home/agent \
  -v "$demo:/workspace:rw" \
  -v "$agent_home:/agent-home:rw" \
  appv231-process-v2:verify \
  --cwd /workspace --thinking medium --temperature 0.2 --resume
```

Choose the recorded session in the visible picker and require the same session
ID/path before entering the recap prompt.

Keep the local image through Task 7 so the packed npm launcher can target it.
After that evidence is recorded, remove the image and temporary directories:

```bash
docker image rm appv231-process-v2:verify
rm -rf "$demo" "$agent_home"
```

### Task 7: npm Launcher Packaging Check

**Files:**
- Verify only unless documentation packaging exposes a missing file.

**Interfaces:**
- Consumes: `packages/appv231-cli` and production image contract.
- Produces: installable npm tarball inventory; no publication.

- [ ] **Step 1: Run npm package tests/build**

```bash
npm --prefix packages/appv231-cli test
npm --prefix packages/appv231-cli pack --dry-run
```

Expected: both exit zero; tarball remains public/non-private and includes the
launcher, README, AGENTS file, and built-in skills.

- [ ] **Step 2: Test local packed launcher against the verification image**

Create the tarball in a temporary directory, install it into an isolated npm
prefix, and run `--dry-run` plus `--help`. Do not publish.

```bash
packdir="$(mktemp -d /tmp/appv231-npm-pack.XXXXXX)"
launcher_demo="$(mktemp -d /tmp/appv231-npm-demo.XXXXXX)"
launcher_home="$(mktemp -d /tmp/appv231-npm-home.XXXXXX)"
npm --prefix packages/appv231-cli pack --pack-destination "$packdir"
npm install --prefix "$packdir/install" "$packdir"/*.tgz
"$packdir/install/node_modules/.bin/appv231" --help
"$packdir/install/node_modules/.bin/appv231" \
  --cwd "$launcher_demo" --agent-home "$launcher_home" \
  --image appv231-process-v2:verify --no-pull --dry-run
rm -rf "$packdir" "$launcher_demo" "$launcher_home"
```

Expected: launcher resolves, help documents session and sandbox options, and
the dry run targets the verified local production image without a registry
pull. Then execute the cleanup command from Task 6 Step 5.

### Task 8: Final Completion Audit

**Files:**
- Verify only.

**Interfaces:**
- Consumes: all implementation and evidence.
- Produces: requirement-by-requirement completion report.

- [ ] **Step 1: Prove redzone integrity over the whole implementation range**

Use design commit `96b38b9`, the immutable boundary immediately before Plan 01
implementation, then run:

```bash
base_commit="96b38b9"
if git diff --name-only "$base_commit"..HEAD | rg '^appV2\.3\.1/appv231/(agent|compaction)/'; then
  echo 'redzone modified' >&2
  exit 1
fi
```

Expected: no output and exit zero.

- [ ] **Step 2: Re-run the complete source suite fresh**

```bash
TERM=xterm-256color PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q -p no:cacheprovider appV2.3.1/tests
```

Expected: all tests pass. Use this fresh output, not an earlier run, in the final
report.

- [ ] **Step 3: Audit every finding against authoritative evidence**

For each row in the Finding Coverage Matrix, record:

```text
finding | implementation commit | focused test | full-suite result | TUI/image evidence | status
```

Status is `complete` only when every evidence column is present. A passing unit
test cannot substitute for the required direct TUI or Linux containment proof.

- [ ] **Step 4: Inspect final diff and worktree**

```bash
git diff --stat "$base_commit"..HEAD
git diff --check "$base_commit"..HEAD
git status --short
```

Expected: scoped coding-agent/TUI/docs/tests/dependency changes, no redzone path,
no credentials, no generated session/process artifacts, and unrelated existing
untracked files left untouched.

- [ ] **Step 5: Stop without publishing**

Report the completed commit range, exact tests, source TUI result, image TUI
result, npm pack result, and remaining risks. Do not push, publish, or release
until the user issues a separate explicit instruction.
