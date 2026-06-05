# Live Worker Runtime Probe Guide

This guide shows junior developers how to run and inspect the live
`decompressor -> planner -> worker kernel` probe.

The probe script is:

```bash
scripts/live_worker_runtime_probe.py
```

It creates or refreshes a mock repo, runs baseline tests, sends a real prompt
through the decompressor and planner, executes the plan with LLM-backed workers,
runs tests again, and saves a full JSON envelope/plan/result/matrix payload.

## What The Probe Tests

The live probe exercises the full runtime pipeline:

```text
prompt
  -> DecompressorRuntime
  -> PlannerRuntime
  -> WorkerKernelRuntime
  -> worker groups and tool-gated worker instances
  -> final Result
```

It is meant to answer these questions:

- Did the decompressor produce a useful intent envelope?
- Did the planner produce a valid phase-aware plan?
- Did the worker kernel compile tasks and enforce budgets/permissions?
- Did workers use the right tools instead of raw shell access?
- Did mutation stay inside scoped write paths?
- Did verification run and prove the fix?
- Did the runtime matrix explain where latency, retries, or failures happened?

## Prerequisites

Run from the repo root:

```bash
cd /Users/htooayelwin/Documents/VScode/allthebest
uv sync
```

Create a local `.env`:

```bash
cp .env.example .env
```

Fill in real local credentials. Never commit secrets.

Minimum live LLM config:

```bash
DECOMPRESSOR_LLM_ENABLED=true
DECOMPRESSOR_LLM_API_KEY=...
DECOMPRESSOR_LLM_BASE_URL=https://openrouter.ai/api/v1
DECOMPRESSOR_LLM_MODEL=...

PLANNER_LLM_ENABLED=true
PLANNER_LLM_API_KEY=...
PLANNER_LLM_BASE_URL=https://openrouter.ai/api/v1
PLANNER_LLM_MODEL=...

WORKER_LLM_API_KEY=...
WORKER_LLM_BASE_URL=https://openrouter.ai/api/v1
```

Worker settings are mostly injected by the probe script at runtime:

```text
WORKER_LLM_ENABLED=true
WORKER_LLM_MODEL=<--worker-model>
WORKER_LLM_PROVIDER_SORT=latency
WORKER_LLM_TEMPERATURE=0
WORKER_LLM_RESPONSE_FORMAT=json_schema
WORKER_LLM_MAX_TOKENS=<--max-tokens>
WORKER_MAX_PARALLEL_INSTANCES=<--max-parallel-instances>
WORKER_TOOL_TIMEOUT_SECONDS=<--tool-timeout>
WORKER_WEB_SEARCH_PROVIDER=<--web-search-provider>
WORKER_WEB_SEARCH_MAX_RESULTS=<--web-search-max-results>
```

The script does not set `WORKER_LLM_API_KEY` or `WORKER_LLM_BASE_URL`; those
must come from `.env`.

## Quick Smoke Test

Run the default payment retry scenario:

```bash
uv run python scripts/live_worker_runtime_probe.py \
  --worker-model qwen/qwen3.7-max \
  --scenario payment_retry \
  --matrix-poll-interval 1 \
  --out-dir plan
```

Expected ending:

```text
PHASE worker done status=completed
PHASE after_pytest returncode=0
OUTPUT_PATH=/.../plan/live-worker-qwen-qwen3-7-max-YYYYMMDD-HHMMSS.json
{"after_returncode": 0, "baseline_returncode": 1, "result_status": "completed", ...}
```

For these seeded mock repos, `baseline_returncode=1` is expected because the
repo starts with a bug. A good run ends with `result_status=completed` and
`after_returncode=0`.

## File-Management Scenario

Run the workspace cleanup scenario to validate file reads/writes and manipulation:

```bash
uv run python scripts/live_worker_runtime_probe.py \
  --worker-model qwen/qwen3.7-max \
  --scenario file_workspace_cleanup \
  --matrix-poll-interval 1 \
  --out-dir plan
```

This scenario expects:

- markdown artifacts moved into `docs/`,
- logs and json artifacts moved into `artifacts/logs/`,
- `docs/workspace_manifest.json` to summarize what changed,
- a final test run that validates resulting repository layout.

## Policy Archive File Management Scenario

Run the more complex policy handoff scenario to validate nested file
classification, protected/held items, JSON/log/export routing, and exact archive
index schema preservation:

```bash
uv run python scripts/live_worker_runtime_probe.py \
  --worker-model qwen/qwen3.7-max \
  --scenario file_policy_archive_reorg \
  --repo live_worker_policy_archive_repo_$(date +%Y%m%d-%H%M%S) \
  --matrix-poll-interval 1 \
  --out-dir plan
```

This scenario expects:

- eligible markdown policy/client notes moved into `records/policies/`,
- JSON evidence moved into `records/evidence/`,
- logs moved into `records/logs/`,
- CSV exports moved into `records/exports/`,
- files marked `hold`, `keep`, or `do_not_move` left in place,
- `records/archive_index.json` with exact keys `moved_documents`,
  `moved_evidence`, `moved_logs`, `moved_exports`, `held_items`, and
  `total_moved`.

## Greenfield Calculator API Scenario

Run the empty-repo creation scenario to validate discovery, runtime capability
detection, scoped batch writes, verification, and final summary quality:

```bash
uv run python scripts/live_worker_runtime_probe.py \
  --worker-model qwen/qwen3.7-max \
  --scenario greenfield_calculator_api \
  --repo live_worker_greenfield_calculator_api_$(date +%Y%m%d-%H%M%S) \
  --matrix-poll-interval 1 \
  --out-dir plan
```

This scenario intentionally starts with an empty git repo. The script refuses
to reuse a non-empty target directory; pass a fresh `--repo` name for repeat
runs. Expected successful behavior:

- `repo_snapshot.is_empty=true` appears in worker evidence,
- the planner selects a write-capable mutation worker for scaffolding,
- the mutating worker uses scoped write tools, ideally `write_many_files`,
- after-run tests execute from the generated project,
- final JSON contains created source, tests, README, and deploy metadata.

## Web Research Scenario

The webhook scenario may produce a `web_research_worker` step. Configure a web
search provider when you want the full path to complete.

Use an environment variable for the key:

```bash
export WORKER_WEB_SEARCH_API_KEY="..."
```

Run:

```bash
uv run python scripts/live_worker_runtime_probe.py \
  --worker-model qwen/qwen3.7-max \
  --scenario webhook_fulfillment \
  --web-search-provider brave \
  --web-search-api-key "$WORKER_WEB_SEARCH_API_KEY" \
  --matrix-poll-interval 1 \
  --out-dir plan
```

Expected successful ending:

```text
PHASE worker done status=completed
PHASE after_pytest returncode=0
```

If the key is missing, the run should block cleanly:

```text
status=blocked
tool_unavailable
Brave web_search provider requires WORKER_WEB_SEARCH_API_KEY
```

That is a kernel/tool-provider configuration issue, not a planner replan issue.

## Custom Repo And Prompt

You can point the probe at a custom mock repo name and a custom task prompt.
The repo path is relative to the workspace root unless an absolute path is
provided.

```bash
uv run python scripts/live_worker_runtime_probe.py \
  --worker-model qwen/qwen3.7-max \
  --repo live_worker_mock_repo \
  --prompt "In the repo rooted at live_worker_mock_repo, inspect the retry bug, make the smallest safe fix, and verify tests." \
  --matrix-poll-interval 1 \
  --out-dir plan
```

Use precise prompts. Include:

- repo root name
- bug or goal
- whether research is allowed or useful
- expected mutation scope
- required verification
- final summary requirement

## Probe Arguments

```text
--scenario
  Built-in scenario. Supported values:
  payment_retry
  webhook_fulfillment
  file_workspace_cleanup
  file_policy_archive_reorg
  greenfield_calculator_api

--repo
  Override the scenario repo directory.

--prompt
  Override the scenario prompt.

--worker-model
  Worker LLM model. Example: qwen/qwen3.7-max.

--dotenv
  Env file passed into decompressor, planner, and worker runtimes.
  Default: .env

--out-dir
  Directory for saved JSON payloads.
  Default: plan

--max-parallel-instances
  Worker group parallelism setting.
  Default: 3

--worker-timeout
  Worker model call timeout in seconds.
  Default: 90

--tool-timeout
  Worker tool timeout in seconds.
  Default: 20

--max-tokens
  Worker model max tokens.
  Default: 2400

--matrix-poll-interval
  How often live matrix rows are printed.
  Default: 1.0

--web-search-provider
  Web search provider for web_research_worker.
  Default: brave

--web-search-api-key
  Web search API key for this process only.

--web-search-max-results
  Max search results per web_search call.
  Default: 5
```

## Output Files

Each run writes a JSON file:

```text
plan/live-worker-<model-slug>-<YYYYMMDD-HHMMSS>.json
```

Important top-level fields:

```text
generated_at
scenario
prompt
repo_path
worker_env
runtime_matrix
baseline_pytest
envelope
plan
result
after_pytest
git_status
final_files
```

The `worker_env` field redacts `WORKER_WEB_SEARCH_API_KEY`.

## How To Inspect A Run

Replace the path with the latest output path.

```bash
uv run python - <<'PY'
import json
from pathlib import Path

p = Path("plan/live-worker-qwen-qwen3-7-max-YYYYMMDD-HHMMSS.json")
data = json.loads(p.read_text())
result = data["result"]
rows = data["runtime_matrix"]["rows"]

print("scenario:", data["scenario"])
print("baseline:", data["baseline_pytest"]["returncode"])
print("status:", result["status"])
print("after:", data["after_pytest"]["returncode"])
print("usage:", result["usage"])
print("issues:", result.get("metadata", {}).get("issues", []))
print("matrix rows:", len(rows))
PY
```

Inspect completed worker attempts:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

p = Path("plan/live-worker-qwen-qwen3-7-max-YYYYMMDD-HHMMSS.json")
rows = json.loads(p.read_text())["runtime_matrix"]["rows"]

for row in rows:
    if row["event"] == "attempt_completed":
        details = row.get("details") or {}
        print(
            row["stage"],
            row.get("worker_type"),
            row["status"],
            "model_calls=", details.get("model_calls"),
            "tool_calls=", details.get("tool_calls"),
        )
PY
```

Inspect retries and replans:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

p = Path("plan/live-worker-qwen-qwen3-7-max-YYYYMMDD-HHMMSS.json")
rows = json.loads(p.read_text())["runtime_matrix"]["rows"]

for row in rows:
    event = row.get("event", "")
    if "retry" in event or "replan" in event:
        print(row)
PY
```

Inspect key artifacts:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

p = Path("plan/live-worker-qwen-qwen3-7-max-YYYYMMDD-HHMMSS.json")
artifacts = {a["id"]: a for a in json.loads(p.read_text())["result"]["artifacts"]}

for artifact_id in [
    "change_summary",
    "patch_diff",
    "rollback_patch",
    "verification_results",
    "test_results",
    "final_report",
]:
    artifact = artifacts.get(artifact_id)
    if artifact:
        print("\\n==", artifact_id, "==")
        print(json.dumps(artifact["content"], indent=2)[:1600])
PY
```

## Reading The Runtime Matrix

Live rows start with `MATRIX`.

Example:

```text
MATRIX seq=80 component=worker_agentic_group stage=VERIFY event=worker_tool_call_completed status=completed step_id=verify_patch_behavior worker_type=verify_worker details={"tool_name": "run_focused_tests", "returncode": 0}
```

Core fields:

```text
seq
  Increasing row number.

component
  Runtime component emitting the row.
  Common values:
  decompressor_runtime
  planner_runtime
  worker_kernel_runtime
  worker_agentic_group

stage
  Runtime stage or plan phase.
  Common values:
  decompress_request
  draft_plan
  plan_execution
  DISCOVER
  ANALYZE
  RESEARCH
  DESIGN
  MUTATE
  VERIFY
  FINALIZE

event
  What happened.

status
  started, completed, blocked, failed, skipped, or other structured status.

request_id / plan_id / run_id / step_id / attempt_id / worker_type
  IDs that connect rows across the full run.

details
  Event-specific JSON, truncated in live console output but complete in the
  saved JSON file.
```

Important worker events:

```text
worker_group_started
worker_instance_started
worker_model_call_started
worker_model_call_completed
worker_model_call_failed
worker_tool_call_started
worker_tool_call_completed
worker_tool_call_failed
worker_instance_completed
worker_instance_skipped
worker_group_completed
```

Important kernel events:

```text
run_started
plan_normalized
preflight_completed
step_started
task_compiled
attempt_started
attempt_completed
step_completed
step_terminal
run_completed
```

## Healthy Run Pattern

For seeded coding scenarios, a healthy run normally looks like this:

```text
PHASE baseline_pytest returncode=1
PHASE decompressor done request_id=req_001
PHASE planner done plan_id=... steps=N
MATRIX ... DISCOVER ... repo_snapshot ...
MATRIX ... ANALYZE ... read_many_files ...
MATRIX ... MUTATE ... replace_in_file ...
MATRIX ... VERIFY ... run_focused_tests ... returncode=0
PHASE worker done status=completed
PHASE after_pytest returncode=0
```

Good signs:

- `result.status == "completed"`
- `after_pytest.returncode == 0`
- `result.metadata.issues` is empty
- `result.usage.retries == 0` or low
- no `replan` rows unless the plan truly drifted from reality
- mutation has `change_summary`, `patch_diff`, and `rollback_patch`
- verify has `verification_results` and test evidence
- repo/web worker groups may skip later instances once expected artifacts exist

## Common Failure Modes

### Missing Worker LLM Key

Symptoms:

```text
worker_model_call_failed
```

Fix:

```bash
grep WORKER_LLM .env
```

Make sure `WORKER_LLM_API_KEY` and `WORKER_LLM_BASE_URL` are set.

### Missing Web Search Key

Symptoms:

```text
worker_tool_call_failed status=blocked tool_name=web_search
tool_unavailable
Brave web_search provider requires WORKER_WEB_SEARCH_API_KEY
```

Fix:

```bash
export WORKER_WEB_SEARCH_API_KEY="..."
```

Then pass it:

```bash
--web-search-api-key "$WORKER_WEB_SEARCH_API_KEY"
```

### Budget Hit

Symptoms:

```text
status=budget_exceeded
model_budget_exhausted_before_final_result
```

Interpretation:

This is a worker/runtime-owned issue. The kernel may retry the same step with a
replacement instance. It should not automatically become planner replan unless
the worker returns a planner-level `needs_replan` issue.

Useful knobs:

```bash
--max-tokens 3200
--worker-timeout 120
```

### Blocked Mutation Scope

Symptoms:

```text
status=blocked
invalid_write_scope
```

Interpretation:

The runtime could not resolve a safe write scope from `mutation_scope` or
explicit permissions. This is a control-plane/tool-gate block, not a normal
worker model failure.

Inspect:

```bash
uv run python - <<'PY'
import json
from pathlib import Path
p = Path("plan/live-worker-qwen-qwen3-7-max-YYYYMMDD-HHMMSS.json")
for artifact in json.loads(p.read_text())["result"]["artifacts"]:
    if artifact["id"] == "mutation_scope":
        print(json.dumps(artifact["content"], indent=2))
PY
```

### Planner-Level Replan

Expected only when the worker discovers a plan-level issue, such as:

- planner requested an artifact that does not exist
- repo shape contradicts the plan
- required sources/evidence cannot be obtained
- mutation target is logically wrong
- planner assumptions are out of reach for worker tools

Replan should show matrix rows containing `replan`.

## Quality Checklist

Before calling a live run good, check:

- The JSON output file exists under `plan/`.
- `baseline_pytest.returncode` is understood.
- `result.status` matches the actual outcome.
- `after_pytest.returncode` matches verification expectations.
- `result.usage.model_calls` and `result.usage.tool_calls` are reasonable.
- `result.usage.retries` is not hiding repeated worker weakness.
- `result.metadata.issues` is empty for success runs.
- `runtime_matrix.rows` explain every long wait.
- Web research artifacts include cited source content when web research ran.
- Mutation artifacts include `patch_diff`, `rollback_patch`, and changed paths.
- `git_status.stdout` only contains intended mock-repo changes.

## Cleanup

The probe intentionally creates or refreshes mock repos:

```text
live_worker_mock_repo/
live_worker_webhook_repo/
```

It also writes live JSON files:

```text
plan/live-worker-*.json
```

Keep useful QA artifacts when they document a behavior change. Remove noisy
local probe output before committing unless the artifact is intentionally part
of the review.

## Reference Commands

Focused unit/regression tests:

```bash
uv run pytest tests/test_worker_agentic.py
uv run pytest tests/test_decompressor.py tests/test_planner.py tests/test_worker_kernel.py tests/test_graph.py tests/test_worker_agentic.py
```

Payment retry live probe:

```bash
uv run python scripts/live_worker_runtime_probe.py \
  --worker-model qwen/qwen3.7-max \
  --scenario payment_retry \
  --matrix-poll-interval 1 \
  --out-dir plan
```

Webhook live probe with web research:

```bash
uv run python scripts/live_worker_runtime_probe.py \
  --worker-model qwen/qwen3.7-max \
  --scenario webhook_fulfillment \
  --web-search-provider brave \
  --web-search-api-key "$WORKER_WEB_SEARCH_API_KEY" \
  --matrix-poll-interval 1 \
  --out-dir plan
```

File-management live probe:

```bash
uv run python scripts/live_worker_runtime_probe.py \
  --worker-model qwen/qwen3.7-max \
  --scenario file_workspace_cleanup \
  --matrix-poll-interval 1 \
  --out-dir plan
```
