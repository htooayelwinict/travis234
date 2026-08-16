# Travis234 Local Tmux Orchestration Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task, and use `superpowers:test-driven-development` for every behavior change. Repository policy forbids subagents unless the user explicitly requests them.

**Goal:** Ship a built-in `orchestration` skill that lets a user-facing Travis234 session create or reuse a Git worktree, start a durable tmux-hosted Travis234 worker, exchange bounded structured handoffs, recover coordinator state, and perform an explicit full ownership transfer without changing the core agent loop or existing subagent behavior.

**Architecture:** Package one byte-identical skill bundle in the Python and npm distributions. Its standard-library helper owns a private SQLite control plane, safe Git worktree creation, a tmux-hosted relay, and Travis234's existing stdin/stdout RPC transport. Travis A remains the coordinator; Travis B receives an explicit lifecycle preamble and writes questions or handoff packets to the durable mailbox. No global system prompt, native TUI command, new tool, or subagent integration is added.

**Tech Stack:** Python 3.13, `argparse`, `dataclasses`, `hashlib`, `json`, `os`, `pathlib`, `secrets`, `socket`, `sqlite3`, `subprocess`, `threading`, Git, tmux, Travis234 JSONL RPC, pytest, Node 20's test runner, `uv build`, npm packaging, and OpenRouter `minimax/minimax-m3` for the final installed-wheel TUI qualification.

**Approved design:** `docs/superpowers/specs/2026-08-16-travis-tmux-orchestration-skill-design.md`

## Global Constraints

- Treat the repository root as the only active application tree.
- Preserve the existing unrelated edits in `.gitignore` and `packages/travis234-mcp-adapter/travis234_mcp_adapter/proxy_tool.py`; never stage them with this work.
- Product and CLI remain `Travis234` and `travis234`; Python imports remain `travis`.
- Persist orchestration state only below `get_agent_dir() / "orchestration"`, which defaults to `~/.travis234/agent/orchestration`. Do not add another state environment variable, migration alias, or state root.
- Keep credentials, dotenv contents, authorization headers, dispatch capabilities, and full process environments out of tracked files, command output, SQLite plaintext, transcripts, receipts, and verification records.
- Preserve `travis/agent/agent_loop.py`, iteration budgeting, ordered continuations, cancellation/steering/follow-up behavior, bounded parallel tool execution, compaction, provider streaming, session JSONL semantics, and RPC's one-active-mutation rule.
- Do not change the existing subagent runtime or `subagent-delegation` skill. Its modernization is a separate future design cycle.
- Do not add a global system-prompt rule, native TUI command, extension, core orchestration service, or new agent tool.
- Require the coordinator to have both `bash` and `tmux` available. The skill must refuse orchestration when the tmux tool was excluded; it must not use bash to bypass the active tool policy.
- Add a failing regression test before each production behavior. Observe the named RED failure before implementing the GREEN change.
- Do not weaken existing assertions or broaden timeouts to make tests pass.
- Never merge, cherry-pick, rebase, push, publish, delete a branch, delete a worktree, change Git identity, or persist a new trust decision automatically.
- Run the container smoke only after every implementation and TUI task in this plan is complete. Do not publish Python, npm, or GHCR artifacts under this plan.
- Commit only the files named by each task. Inspect `git diff --cached --name-only` before every commit.

## Protected Surfaces

The implementation must not modify these owners. If a task appears to require one, stop and return to design review:

- `travis/agent/agent_loop.py`
- `travis/compaction/`
- `travis/coding_agent/rpc.py`
- `travis/coding_agent/subagents.py`
- `travis/coding_agent/session_subagents.py`
- `travis/resources/skills/subagent-delegation/`
- `packages/travis234-cli/skills/subagent-delegation/`
- provider catalogs, transports, and streaming adapters
- global system-prompt builders and prompt policy

## Execution Phases And TUI Gates

- Phase 0 — baseline: Task 0, five no-skill product prompts.
- Phase 1 — package and durable control state: Tasks 1–2, lazy-discovery and durable-state TUI gates.
- Phase 2 — isolation and worker transport: Tasks 3–4, one combined worktree-plus-ready-worker TUI gate after the public `worker-start` command is safe.
- Phase 3 — supervised return: Task 5, research and committed-code TUI gates.
- Phase 4 — dialogue: Task 6, question/reply and correction TUI gates.
- Phase 5 — ownership and recovery: Task 7, full-handoff, restart, failure/cancellation, and worker-bound TUI gates.
- Phase 6 — instruction and distribution qualification: Tasks 8–10, five GREEN TUI repetitions, the full deterministic matrix, and installed-wheel Minimax M3 TUI.
- Completion — Task 11, repository suites and container smoke after every design phase is implemented.

Do not manufacture an intermediate TUI path for an unsafe half-feature. Phase 2 intentionally tests Task 3 and Task 4 together because a worktree without a ready relay is not a valid user outcome.

## File Map

### New packaged skill files

- `travis/resources/skills/orchestration/SKILL.md`: concise trigger, mode selection, safety, coordinator loop, and lazy reference routing.
- `travis/resources/skills/orchestration/references/protocol.md`: exact command recipes, request/receipt schemas, states, packet fields, recovery, and failure presentation.
- `travis/resources/skills/orchestration/scripts/orchestrate.py`: deterministic control plane, Git worktree owner, tmux relay, RPC client, durable mailbox, and JSON CLI.
- `packages/travis234-cli/skills/orchestration/SKILL.md`: byte-identical npm mirror.
- `packages/travis234-cli/skills/orchestration/references/protocol.md`: byte-identical npm mirror.
- `packages/travis234-cli/skills/orchestration/scripts/orchestrate.py`: byte-identical npm mirror.

### New tests and verification record

- `tests/test_orchestration_helper.py`: import/CLI envelope, state permissions, schema negotiation, SQLite transactions, idempotency, and secret boundaries.
- `tests/test_orchestration_worktrees.py`: repository discovery, ignored/fallback placement, conflict rejection, dirty-state receipts, and non-destructive creation.
- `tests/test_orchestration_worker_relay.py`: fake and real tmux relay/RPC lifecycle, readiness, resume, serialization, transcript bounds, and trust behavior.
- `tests/test_orchestration_dispatch.py`: lifecycle preamble, prompt acceptance, messages, completion packet, acknowledgement, ping-pong, round limits, and full handoff.
- `tests/test_orchestration_recovery.py`: coordinator restart, exactly-once delivery, cancellation, retain/release, lost and uncertain workers, and fail-closed versions.
- `tests/test_orchestration_tui_scenarios.py`: nine deterministic TUI-facing scenarios with isolated state and fake provider/relay fixtures; the tenth scenario is the installed-wheel live TUI run.
- `docs/verification/local-tmux-orchestration.md`: commands, counts, package hashes, installed-wheel TUI receipts, cleanup, and explicitly deferred publication.

### Existing files to modify

- `pyproject.toml`: include packaged Python helper files and update the exact package-data contract.
- `packages/travis234-cli/package.json`: include packaged Python helper files in the npm tarball.
- `tests/test_coding_resources_and_services.py`: expect lazy discovery of the third built-in skill without loading its body.
- `tests/test_installed_metadata.py`: expect the third installed built-in skill and its companion files.
- `tests/test_distribution_contract.py`: compare the complete Python/npm skill trees and assert wheel contents.
- `tests/test_agent_harness.py`: expect the third lazy built-in skill in SDK discovery.
- `packages/travis234-cli/test/travis234-cli.test.js`: assert npm source/package content and absence of a global prompt.
- `README.md`: document natural-language orchestration, supervised versus full handoff, safety, recovery, and intentional separation from subagents.

## Finalized Public Command Contract

Invoke the helper with the current Python interpreter and the path resolved relative to the loaded `SKILL.md`:

```bash
python3 /absolute/skill/path/scripts/orchestrate.py <command> [arguments]
```

Every command emits exactly one compact JSON object. Success goes to stdout with exit code `0`; failure goes to stderr with a nonzero exit code. Both use this envelope:

```json
{"ok":true,"schemaVersion":1,"protocolVersion":1,"command":"run-show","result":{},"nextActions":[]}
```

```json
{"ok":false,"schemaVersion":1,"protocolVersion":1,"command":"worker-start","error":{"code":"trust_required","message":"Worker project trust is unresolved"},"nextActions":["Resolve project trust in an interactive Travis234 session, then retry with the same idempotency key."]}
```

Public commands are exactly:

- `guide`
- `run-create`, `run-show`, `run-list`
- `task-create`, `task-show`, `task-list`
- `worker-start`, `worker-show`, `worker-list`, `worker-retain`, `worker-release`
- `dispatch-start`, `dispatch-show`, `dispatch-wait`, `dispatch-cancel`, `dispatch-abandon`
- `message-send`, `message-check`, `message-ack`, `message-reply`
- `worker-complete`, `worker-fail`
- `recover`

The private relay entry point is `_relay`; `SKILL.md` must never tell the model to invoke it directly.

All request bodies use a regular, nonsymlink, owner-readable JSON file passed as `--request-file`. This avoids shell quoting of long objectives or handoff packets. Group/world permission bits are rejected. Skill recipes create only nonsecret request files under an exact `mktemp` path with `umask 077` and pass `--consume-request-file`, which authorizes the helper to unlink that one validated file in `finally`. Mutations also require `--idempotency-key`; repeated keys within the same command scope return the same domain IDs and canonical result with `effect: "reused"` instead of repeating the mutation. The helper never copies a request file into durable state.

The request schemas are:

```python
RUN_CREATE_KEYS = {"objective", "coordinatorSessionId"}
TASK_CREATE_KEYS = {
    "objective", "ownership", "acceptanceCriteria", "dependencies",
    "mode", "maxRounds", "commitPolicy",
}
WORKER_START_KEYS = {
    "repository", "workspaceMode", "worktreeName", "branch", "base",
    "dotenvPath", "model", "thinking",
}
DISPATCH_START_KEYS = {
    "prompt", "context", "requiredVerification", "parentMessageId",
}
MESSAGE_KEYS = {"body", "evidence", "artifacts"}
HANDOFF_KEYS = {
    "outcome", "summary", "evidence", "changedFiles", "commit",
    "tests", "artifacts", "failedAttempts", "blockers", "questions",
    "recommendedNextAction",
}
```

Required and optional fields are fixed:

| Request | Required | Optional/conditional |
|---|---|---|
| Run | `objective` | `coordinatorSessionId` |
| Task | `objective`, `ownership`, `acceptanceCriteria`, `mode`, `commitPolicy` | `dependencies` defaults to `[]`; `maxRounds` defaults to `4` |
| Worker | `repository`, `workspaceMode` | `worktreeName`, `branch`, and `base` are required for `worktree`; `dotenvPath`, `model`, and `thinking` are always optional |
| Dispatch | `prompt`, `requiredVerification` | `context` defaults to `[]`; `parentMessageId` is required after the first round |
| Message | `body` | `evidence` and `artifacts` default to `[]` |
| Handoff | every key in `HANDOFF_KEYS` | values may be empty lists or `null` only where their declared types allow it |

Unknown keys fail closed. `dotenvPath` may be consumed for the worker launch but is deleted from the normalized request before the public receipt or idempotency receipt is serialized.

## State And Relay Contract

Use these version and limit constants in both the helper and protocol reference:

```python
SCHEMA_VERSION = 1
PROTOCOL_VERSION = 1
DEFAULT_MAX_WORKERS = 2
HARD_MAX_WORKERS = 3
DEFAULT_MAX_ROUNDS = 4
HARD_MAX_ROUNDS = 12
MAX_WAIT_SECONDS = 60
MAX_MESSAGE_LIMIT = 50
MAX_REQUEST_BYTES = 256 * 1024
MAX_TRANSCRIPT_BYTES = 2 * 1024 * 1024
TRANSCRIPT_TAIL_BYTES = 128 * 1024
MAX_UNIX_SOCKET_PATH_BYTES = 100
```

Use these lifecycle values without synonyms:

```python
RUN_STATUSES = {"active", "completed", "abandoned"}
TASK_STATUSES = {
    "pending", "active", "awaiting_coordinator", "succeeded", "failed",
    "cancelled", "abandoned",
}
WORKER_STATUSES = {
    "starting", "ready", "busy", "idle", "retained", "stopped", "lost",
    "outcome_unknown",
}
DISPATCH_STATUSES = {
    "queued", "accepted", "running", "awaiting_coordinator", "succeeded",
    "failed", "cancelled", "abandoned", "outcome_unknown",
}
MESSAGE_KINDS = {"question", "reply", "status", "handoff", "failure", "heartbeat"}
```

One Dispatch represents one assignment round for one Task. The Task's `prompt_count` counts every A-to-B initial prompt, question reply, and focused correction. A correction after a terminal handoff creates a new immutable Dispatch with the acknowledged handoff as its parent. To rotate the dispatch-scoped capability while preserving conversation history, the relay stops only an idle RPC child and restarts it with `travis234 --session <session-id> --mode rpc` plus the new capability in its environment.

The coordinator generates the dispatch capability with `secrets.token_urlsafe(32)`, stores only `sha256(capability).hexdigest()` in SQLite, sends plaintext once over the private relay socket, and immediately drops its local reference. The relay does not log inbound control frames. The child receives the capability as `TRAVIS234_ORCHESTRATION_CAPABILITY`; worker-side mutation commands read it only from the environment and use `hmac.compare_digest()` against the database hash.

## Task 0: Capture The No-Skill RED Baseline

**Files:**

- Create: `docs/verification/local-tmux-orchestration.md`

**Produces:** A five-run, sanitized no-guidance control that demonstrates which orchestration behaviors the skill must teach. This task runs before any `orchestration` skill file exists.

- [ ] **Step 1: Pin the baseline fixture and scoring rubric**

Use five fresh Travis234 sessions, five isolated `TRAVIS234_CODING_AGENT_DIR` directories, and five disposable nested Git repositories below the already ignored repository `tmp/` directory. Each fixture contains only a short README and two evidence files. Give each session this exact prompt:

```text
Start another durable Travis234 in tmux in a new Git worktree, ask it to inspect this fixture and return an evidence-backed research handoff to you, then summarize that handoff for me. Do not use subagents, do not edit the coordinator-owned checkout, do not integrate anything, and leave no ambiguity about which Travis session produced the evidence.
```

Score each response manually for seven observable behaviors: distinct durable worker, tmux plus structured RPC rather than screen scraping, new worktree ownership, stable identities, structured handoff, no direct B-to-A keystrokes, and exact worker cleanup. A marker string alone never counts as a pass.

- [ ] **Step 2: Run five fresh Minimax M3 controls without the new skill**

Run the current pre-feature Travis234 with the root `.env` through the normal CLI boundary, `openrouter/minimax/minimax-m3`, thinking `medium`, owner-private traces, and no copied credentials. The current packaged skill inventory is the proof that `orchestration` was absent; record its names without loading unrelated skill bodies.

Use bounded per-run and aggregate deadlines. After every attempt, report `PASS` or `FAIL` plus the first missing observable behavior. Stop and inspect any live uniquely identified tmux worker before ending it; never use a broad tmux kill command.

- [ ] **Step 3: Verify RED and preserve only sanitized evidence**

Expected: at least one of the five controls misses one or more required behaviors. Record exact behavior categories and bounded non-secret excerpts, not credentials, dotenv paths, full prompts from tool calls, or raw process environments.

If all five controls unexpectedly satisfy all seven behaviors, stop execution and revise the planned skill down to the missing command/reference surface; do not add redundant behavior-shaping prose.

- [ ] **Step 4: Commit the baseline record alone**

```bash
git commit -m "test(skills): capture orchestration baseline"
```

## Task 1: Lock the Package and Lazy-Discovery Contract

**Files:**

- Create: `travis/resources/skills/orchestration/SKILL.md`
- Create: `travis/resources/skills/orchestration/references/protocol.md`
- Create: `travis/resources/skills/orchestration/scripts/orchestrate.py`
- Create: matching files under `packages/travis234-cli/skills/orchestration/`
- Modify: `pyproject.toml`
- Modify: `packages/travis234-cli/package.json`
- Modify: `tests/test_coding_resources_and_services.py`
- Modify: `tests/test_installed_metadata.py`
- Modify: `tests/test_distribution_contract.py`
- Modify: `tests/test_agent_harness.py`
- Modify: `packages/travis234-cli/test/travis234-cli.test.js`
- Create: `tests/test_orchestration_helper.py`

**Produces:** A discoverable lazy skill, byte-identical mirrors, and package metadata that includes `.md` and `.py` skill resources.

- [ ] **Step 1: Add failing built-in inventory, lazy-loading, mirror, and package-data tests**

Update exact skill sets to include `orchestration`, but assert the full body remains absent from the initial system prompt:

```python
assert set(skills) == {"orchestration", "subagent-delegation", "web-search"}
skill_prompt = format_skills_for_prompt(list(skills.values()))
assert "orchestration" in skill_prompt
assert "# Local Tmux Orchestration" not in skill_prompt
assert "Run the private relay" not in skill_prompt
```

Replace the two-name distribution loop with whole-tree comparison:

```python
python_files = {
    path.relative_to(python_skills).as_posix(): path.read_bytes()
    for path in python_skills.rglob("*") if path.is_file()
}
npm_files = {
    path.relative_to(npm_skills).as_posix(): path.read_bytes()
    for path in npm_skills.rglob("*") if path.is_file()
}
assert npm_files == python_files
assert {
    "orchestration/SKILL.md",
    "orchestration/references/protocol.md",
    "orchestration/scripts/orchestrate.py",
} <= python_files.keys()
```

Assert `pyproject.toml` uses both patterns and npm declares both file classes:

```python
assert metadata["tool"]["setuptools"]["package-data"]["travis"] == [
    "resources/**/*.md",
    "resources/skills/**/*.py",
]
```

```javascript
assert.deepEqual(packageJson.files, [
  "bin/travis234.js",
  "skills/**/*.md",
  "skills/**/*.py",
  "README.md",
  "package.json",
]);
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
uv run pytest -q \
  tests/test_coding_resources_and_services.py::test_packaged_builtin_skills_load_as_lazy_defaults \
  tests/test_installed_metadata.py::test_packaged_builtin_skills_exist \
  tests/test_distribution_contract.py::test_packaged_builtin_skills_match_npm_distribution \
  tests/test_agent_harness.py::test_agent_harness_composes_existing_owners_inside_async_context
node --test packages/travis234-cli/test/travis234-cli.test.js
```

Expected: the Python assertions report the missing `orchestration` skill and the npm assertion reports the missing Python resource pattern.

- [ ] **Step 3: Add the minimal valid mirrored bundle**

First run the generic skill initializer in a disposable directory, as required by `skill-creator`, to validate the intended `scripts` and `references` structure:

```bash
skill_scratch=$(mktemp -d)
python3 /Users/htooayelwin/.codex/skills/.system/skill-creator/scripts/init_skill.py \
  orchestration \
  --path "$skill_scratch" \
  --resources scripts,references \
  --interface 'display_name=Travis234 Orchestration' \
  --interface 'short_description=Coordinate durable Travis234 workers' \
  --interface 'default_prompt=Use $orchestration to start another Travis234 in a Git worktree and bring back its result.'
```

Do not copy its product-specific `agents/openai.yaml`: Travis234's loader does not consume that interface and the approved repository package contract contains only `SKILL.md`, `references/protocol.md`, and `scripts/orchestrate.py`. Create those repository files with `apply_patch`, then discard the scratch directory through the system's recoverable temporary-file cleanup.

Use this exact frontmatter in both `SKILL.md` files:

```markdown
---
name: orchestration
description: Use when the user asks one Travis234 session to start, supervise, ping-pong with, recover, or hand work to another durable Travis234 session in tmux, especially in a Git worktree.
---
```

Initially make `orchestrate.py` expose only the stable envelope and `guide` command so packaging tests can import it without implementing later behavior out of order:

```python
SCHEMA_VERSION = 1
PROTOCOL_VERSION = 1


def envelope(command: str, result: dict[str, object]) -> dict[str, object]:
    return {
        "ok": True,
        "schemaVersion": SCHEMA_VERSION,
        "protocolVersion": PROTOCOL_VERSION,
        "command": command,
        "result": result,
        "nextActions": [],
    }
```

Mirror all three files byte-for-byte. Add `"resources/skills/**/*.py"` to Python package data and `"skills/**/*.py"` to npm files.

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run the Step 2 commands plus:

```bash
uv run pytest -q tests/test_orchestration_helper.py tests/test_distribution_contract.py
npm pack --dry-run --json --workspace @htooayelwinict/travis234
```

Expected: all selected tests pass; dry-run paths include all three orchestration files and no state, dotenv, or transcript file.

- [ ] **Step 5: Run the lazy-discovery TUI checkpoint**

Add a focused faux-provider test in `tests/test_orchestration_tui_scenarios.py` that starts a normal unrelated turn and asserts the resource metadata contains `orchestration` while the loaded context does not contain its Markdown heading or protocol recipes.

Run:

```bash
uv run pytest -q tests/test_orchestration_tui_scenarios.py -k lazy_discovery
```

Expected: `1 passed`; no helper subprocess, tmux session, SQLite file, or worktree is created.

- [ ] **Step 6: Commit the package skeleton**

Stage only the files named by Task 1 and verify the staged list. Commit:

```bash
git commit -m "feat(skills): package local orchestration bundle"
```

## Task 2: Implement Private Versioned SQLite State And JSON CLI

**Files:**

- Modify: `travis/resources/skills/orchestration/scripts/orchestrate.py`
- Modify: `packages/travis234-cli/skills/orchestration/scripts/orchestrate.py`
- Modify: `tests/test_orchestration_helper.py`

**Produces:** Owner-private state, strict schemas, stable identifiers, idempotent mutations, query commands, and secret-safe receipts.

- [ ] **Step 1: Write failing path, permission, schema, and idempotency tests**

Load the helper with `importlib.util.spec_from_file_location`, set only the existing `TRAVIS234_CODING_AGENT_DIR`, and assert:

```python
state = module.StateStore.open()
assert state.root == agent_dir / "orchestration"
assert stat.S_IMODE(state.root.stat().st_mode) == 0o700
assert stat.S_IMODE(state.path.stat().st_mode) == 0o600
assert state.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
assert state.connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
assert state.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
```

Create the same Run and Task twice with one idempotency key and assert stable IDs, one row, `effect: "created"` on the first receipt, and `effect: "reused"` on the second. Add failures for oversized JSON, unknown keys, invalid status, invalid mode, max rounds above twelve, malformed IDs, incompatible schema metadata, symlink/nonprivate request files, and credential-shaped strings or sensitive keys. Verify `--consume-request-file` removes only a validated exact file even after a parse error and never follows or removes a symlink.

Invoke malformed commands in subprocesses and assert stdout/stderr contain only one parseable JSON object: no argparse usage prose, Python traceback, secret value, or second frame.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
uv run pytest -q tests/test_orchestration_helper.py -k "state or schema or idempotency or secret"
```

Expected: import or attribute failures for `StateStore`, request validation, and the unimplemented CLI commands.

- [ ] **Step 3: Implement state-root creation and database initialization**

Resolve the root only through the existing config owner:

```python
ENV_AGENT_DIR = "TRAVIS234_CODING_AGENT_DIR"


def agent_dir() -> Path:
    configured = os.environ.get(ENV_AGENT_DIR)
    return (
        Path(configured).expanduser().resolve()
        if configured
        else (Path.home() / ".travis234" / "agent").resolve()
    )


def orchestration_root() -> Path:
    root = agent_dir() / "orchestration"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    (root / "sockets").mkdir(exist_ok=True, mode=0o700)
    (root / "runs").mkdir(exist_ok=True, mode=0o700)
    return root
```

Capture the caller's umask with `original_umask = os.umask(0o077)` at helper process entry before opening SQLite, sockets, launch files, or logs so WAL/SHM sidecars and relay-created files are private too. Tests inspect every regular file below the orchestration root, not only `state.sqlite3`. Put only the numeric original umask in the mode-0600 one-use launch file; the relay passes it through `Popen(umask=original_umask)` to Travis B so worker-created repository files retain the operator's normal permission policy instead of the control plane's private umask.

Create `meta`, `runs`, `tasks`, `workers`, `dispatches`, `messages`, and `idempotency` tables in one `BEGIN IMMEDIATE` transaction. Required columns are:

```text
meta(key PRIMARY KEY, value NOT NULL)
runs(run_id PRIMARY KEY, objective, coordinator_session_id, status, created_at, updated_at)
tasks(task_id PRIMARY KEY, run_id REFERENCES runs, objective, ownership_json,
      acceptance_json, dependencies_json, mode, max_rounds, prompt_count,
      commit_policy, status, created_at, updated_at)
workers(worker_id PRIMARY KEY, run_id REFERENCES runs, workspace, repository,
        branch, base_commit, worktree_path, tmux_session UNIQUE, socket_path UNIQUE,
        travis_session_id, status, retained, protocol_version, created_at, updated_at)
dispatches(dispatch_id PRIMARY KEY, task_id REFERENCES tasks,
           worker_id REFERENCES workers, capability_hash, round_number,
           parent_message_id, status, accepted_at, settled_at, created_at, updated_at)
messages(message_id PRIMARY KEY, dispatch_id REFERENCES dispatches, sender, kind,
         parent_message_id, payload_json, created_at, last_delivered_at,
         delivery_count, acknowledged_at)
idempotency(scope, key, response_json, created_at, PRIMARY KEY(scope, key))
```

Set `schema_version=1` and `protocol_version=1` in `meta`. Existing mismatches permit read-only `guide`, `run-show`, `task-show`, `worker-show`, `dispatch-show`, and `recover --inspect-only`; all mutations fail with `incompatible_state` and do not run migration SQL.

- [ ] **Step 4: Implement strict JSON loading, IDs, envelopes, and base commands**

Use prefixed opaque IDs from `secrets.token_hex(12)`:

```python
def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


ID_PATTERNS = {
    "run": re.compile(r"^run_[0-9a-f]{24}$"),
    "task": re.compile(r"^task_[0-9a-f]{24}$"),
    "worker": re.compile(r"^worker_[0-9a-f]{24}$"),
    "dispatch": re.compile(r"^dispatch_[0-9a-f]{24}$"),
    "message": re.compile(r"^message_[0-9a-f]{24}$"),
}
```

Implement `guide`, Run create/show/list, and Task create/show/list. Mutations execute and save their canonical idempotency result in the same transaction as domain rows; a repeat reconstructs the receipt with `effect: "reused"` and the current lifecycle state. Lists use creation time plus ID ordering and a hard limit of 50. Generate UTC timestamps with millisecond precision and a trailing `Z`; order by `(created_at, opaque_id)`.

Subclass `argparse.ArgumentParser.error()` to raise a typed helper error, use `add_help=False`, and map both `-h` and `--help` to the JSON `guide` envelope. Catch typed validation failures, `KeyboardInterrupt`, and unexpected exceptions at `main()`; unexpected failures return the fixed code/message `internal_error` / `Command failed; inspect safe state and retry only with the same idempotency key` without a traceback.

Before persistence, recursively reject request keys matching `api[_-]?key`, `authorization`, `cookie`, `password`, `secret`, or `token` (case-insensitive), and strings matching authorization headers, private-key headers, common provider token prefixes, or any currently known dispatch capability. Return `secret_like_input` without echoing the value. The skill is still responsible for omitting arbitrary confidential prose that cannot be recognized mechanically.

- [ ] **Step 5: Verify GREEN and mirrored bytes**

Run:

```bash
uv run pytest -q tests/test_orchestration_helper.py
cmp \
  travis/resources/skills/orchestration/scripts/orchestrate.py \
  packages/travis234-cli/skills/orchestration/scripts/orchestrate.py
```

Expected: all helper tests pass and `cmp` exits zero.

- [ ] **Step 6: Run the durable-state TUI checkpoint**

Use a faux-provider TUI turn that explicitly asks Travis A to prepare a supervised orchestration Run and Task but stop before creating a worktree or worker. Verify the visible tool receipts contain stable Run/Task IDs, mode, ownership, acceptance criteria, prompt budget, and safe next action. Restart the coordinator against the same isolated agent directory and verify `run-show`/`task-show` return the same objects without duplicate rows.

Run:

```bash
uv run pytest -q tests/test_orchestration_tui_scenarios.py -k durable_run_task_receipts
```

Expected: the scenario passes, no tmux process or Git worktree exists, and the initial prompt context still contains only lazy skill metadata until the scenario explicitly loads the skill.

- [ ] **Step 7: Commit state and CLI**

```bash
git commit -m "feat(orchestration): add durable local state"
```

## Task 3: Add Non-Destructive Git Worktree Ownership

**Files:**

- Modify: `travis/resources/skills/orchestration/scripts/orchestrate.py`
- Modify: `packages/travis234-cli/skills/orchestration/scripts/orchestrate.py`
- Create: `tests/test_orchestration_worktrees.py`
- Modify: `tests/test_orchestration_helper.py`
- Modify: `tests/test_orchestration_tui_scenarios.py`

**Produces:** Validated current-workspace or isolated-worktree selection, honest dirty-state receipts, and zero automatic integration or cleanup.

- [ ] **Step 1: Write failing temporary-repository tests**

Create real temporary Git repositories with a local test-only identity. Cover:

1. `.worktrees/` already ignored: use `<repo>/.worktrees/<name>`.
2. `.worktrees/` not ignored, or ignored only by a global excludes file: use `<agent>/orchestration/worktrees/<sha256-real-repo-prefix>/<name>` and leave `.gitignore` byte-identical.
3. dirty coordinator state: record `dirty: true`, `baseCommit`, and `uncommittedChangesTransferred: false`.
4. invalid worktree name, invalid branch, missing base, existing branch, occupied path, existing registered worktree, non-repository path, and detached conflicting base: fail before mutation.
5. successful creation: `git worktree list --porcelain` contains the path and `git rev-parse HEAD` equals the resolved base.
6. current workspace: no branch, worktree, or index mutation.

- [ ] **Step 2: Run and verify RED**

Run:

```bash
uv run pytest -q tests/test_orchestration_worktrees.py
```

Expected: import failures for `WorktreeRequest`, `inspect_repository`, and `prepare_workspace`.

- [ ] **Step 3: Implement repository inspection and placement**

Use argument arrays, never shell interpolation:

```python
def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        text=True,
        timeout=30,
    )
```

Resolve the repository with `git rev-parse --show-toplevel`, the common directory with `git rev-parse --git-common-dir`, the base with `git rev-parse --verify <base>^{commit}`, and dirtiness with `git status --porcelain=v1 --untracked-files=normal`. Select a repository-local path only when `git check-ignore -v` reports that the rule came from this repository's tracked ignore files or its Git common directory's `info/exclude`:

```bash
git -C <repo> check-ignore -v --no-index .worktrees/.travis234-orchestration-probe
```

Do not treat a global excludes file as repository-provided. Otherwise use the agent-state fallback without editing ignore rules. Validate worktree names with `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` and branches through `git check-ref-format --branch`.

- [ ] **Step 4: Implement one non-destructive creation transaction**

Before `git worktree add`, reject every existing branch, path, or worktree registration. Then execute exactly:

```python
git(repo, "worktree", "add", "-b", request.branch, str(target), base_commit)
```

If the Git command fails after creating a partial registration, return `outcome_unknown` with the exact inspection commands; do not run `git worktree remove`, `prune`, branch deletion, reset, or checkout cleanup.

- [ ] **Step 5: Verify the worktree owner before adding transport**

Run focused helper tests and inspect the safe receipt produced by `prepare_workspace()` directly. It must contain repository, branch, worktree, base commit, and dirty-state transfer status while excluding dotenv path and automatic integration actions. The Phase 2 TUI gate remains in Task 4 after public `worker-start` can produce a valid ready worker.

Run:

```bash
uv run pytest -q \
  tests/test_orchestration_worktrees.py \
  tests/test_orchestration_helper.py -k worktree
```

Expected: all selected tests pass and the source repository remains unchanged except for the intentionally registered test worktree; no TUI or worker is started at this half-phase boundary.

- [ ] **Step 6: Commit worktree ownership**

```bash
git commit -m "feat(orchestration): create isolated worktrees safely"
```

## Task 4: Build The Durable Tmux Relay Around Existing RPC

**Files:**

- Modify: `travis/resources/skills/orchestration/scripts/orchestrate.py`
- Modify: `packages/travis234-cli/skills/orchestration/scripts/orchestrate.py`
- Create: `tests/test_orchestration_worker_relay.py`
- Modify: `tests/test_orchestration_helper.py`
- Modify: `tests/test_orchestration_tui_scenarios.py`

**Produces:** Bounded worker identities, a private Unix socket, an RPC child with a durable Travis session, serialized mutations, safe readiness, trust gating, and retained diagnostics.

- [ ] **Step 1: Write failing identity, launch, readiness, trust, and bound tests**

Use a fake `travis234` executable placed first on a temporary `PATH`. It must speak the real JSONL methods `get_state`, `prompt`, `abort`, and `close`, record no environment values, and optionally fail or delay readiness. Assert:

```python
assert worker.tmux_session == f"travis234-orch-{digest[:16]}"
assert Path(worker.socket_path).name == f"{digest[:24]}.sock"
assert len(worker.socket_path.encode()) < 100
assert stat.S_IMODE(Path(worker.socket_path).stat().st_mode) == 0o600
assert worker.travis_session_id == "fake-session-1"
```

Cover missing tmux, unavailable RPC executable, startup timeout, malformed stdout, mismatched `cwd`, an agent directory whose socket path exceeds 100 encoded bytes, unresolved trust-requiring project resources, persisted trusted and untrusted decisions, two active workers accepted, third rejected at the default limit, explicit limit three accepted, and a fourth always rejected.

- [ ] **Step 2: Run the relay tests and verify RED**

Run:

```bash
uv run pytest -q tests/test_orchestration_worker_relay.py -k "identity or startup or trust or worker_limit"
```

Expected: import failures for `RelayClient`, `RelayServer`, `start_worker`, and the missing Worker CLI commands.

- [ ] **Step 3: Implement bounded identities, private launch files, and trust inspection**

Derive names from stable opaque IDs rather than user text:

```python
def worker_digest(worker_id: str) -> str:
    return hashlib.sha256(worker_id.encode("utf-8")).hexdigest()


def tmux_name(worker_id: str) -> str:
    return f"travis234-orch-{worker_digest(worker_id)[:16]}"


def socket_path(root: Path, worker_id: str) -> Path:
    return root / "sockets" / f"{worker_digest(worker_id)[:24]}.sock"
```

Reject an encoded socket path longer than `MAX_UNIX_SOCKET_PATH_BYTES` before writing a launch file or mutating tmux. The error points only to the existing `TRAVIS234_CODING_AGENT_DIR` override; it does not fall back to `/tmp` or create a second socket root.

Write a mode-0600 one-use relay launch file below `runs/<run-id>/workers/<worker-id>/launch.json`. It may contain the workspace, operator-selected dotenv path, model, and thinking level but never a credential value or capability. The relay reads it, validates ownership/mode, and unlinks it before starting RPC. Recovery removes a stale launch file only after confirming that no tmux session exists.

Implement a read-only standard-library mirror of `ProjectTrustStore.get_entry()` and `has_trust_requiring_project_resources()` so the bundled helper remains executable with `python3` even when the `travis234` entry point came from an isolated uv-tool environment. Read only `agent_dir() / "trust.json"`, walk nearest ancestors, recognize the same behavior-changing resource names, and fail closed on malformed JSON. Add fixture tests that compare every mirrored decision with the canonical repository owners; never write the trust file.

Apply these target-path outcomes:

- persisted `True`: launch without synthesizing or saving another decision;
- persisted `False`: append `--no-approve`;
- no decision and no trust-requiring resource: append `--no-approve`;
- no decision and a trust-requiring resource: return `trust_required` before tmux creation.

Never write `trust.json` and never append `--approve` to suppress a prompt.

- [ ] **Step 4: Implement `_relay` and its private socket protocol**

Wrap tmux calls in `TmuxClient(command: tuple[str, ...] = ("tmux",))`; production uses the default while tests inject `("tmux", "-L", unique_test_server)`. Start the relay with an argument array scoped to the exact tmux name and workspace:

```python
relay_command = shlex.join([
    sys.executable,
    str(Path(__file__).resolve()),
    "_relay",
    "--worker-id", worker_id,
    "--launch-file", str(launch_file),
])
subprocess.run(
    [
        *tmux_client.command, "new-session", "-d", "-s", tmux_session,
        "-c", str(workspace), relay_command,
    ],
    check=True,
    capture_output=True,
    text=True,
    timeout=30,
)
```

tmux defines its final operand as one shell command, so construct that operand only with `shlex.join()` over validated arguments. Add metacharacter/space path tests and reject NUL/newline inputs; never concatenate user text into the command.

The relay binds one Unix stream socket, calls `os.chmod(socket_path, 0o600)`, and accepts one JSON object per connection. Each control request includes `protocolVersion`, `requestId`, and one action from:

```python
RELAY_ACTIONS = {
    "health", "state", "configure_dispatch", "prompt", "abort", "close",
}
```

Reject an incompatible protocol before mutation. The socket directory's owner-only mode is the local authorization boundary. Never log raw inbound requests because `configure_dispatch` contains the transient capability and `prompt` contains user context.

- [ ] **Step 5: Implement the RPC child and serialized frame router**

Build the command with no shell:

```python
command = ["travis234", "--cwd", str(workspace)]
if dotenv_path is not None:
    command += ["--dotenv", str(dotenv_path)]
if model is not None:
    command += ["--model", model]
if thinking is not None:
    command += ["--thinking", thinking]
if session_id is not None:
    command += ["--session", session_id]
command += ["--mode", "rpc"]
```

Start it with `subprocess.Popen(..., stdin=PIPE, stdout=PIPE, stderr=PIPE, text=True, bufsize=1, cwd=workspace, env=child_env, umask=original_umask)`. One reader thread parses JSON frames and routes them by request ID. A lock permits one mutation but allows `get_state` while a prompt is active. Readiness requires a successful `get_state` whose `cwd` resolves to the worker workspace and whose nonempty `sessionId` is saved in SQLite.

Record only bounded metadata in `rpc.jsonl`: timestamp, direction, request ID, method or event type, status, stop reason, and error code. Do not record prompt params, assistant text, tool arguments, environment, or capability. Rotate at 2 MiB while preserving a 128 KiB tail.

Build an in-memory redaction set from the dispatch capability, credential-shaped child environment values, and values parsed from an explicitly selected dotenv; never persist that set. Redact exact values plus authorization, cookie, API-key assignment, and common token patterns before writing bounded stderr. Lines that cannot be proven safe become `{"category":"stderr","bytes":N,"sha256":"...","contentOmitted":true}` rather than raw text.

- [ ] **Step 6: Implement Worker start/show/list and verify real tmux behavior**

`worker-start --task-id --request-file --consume-request-file --idempotency-key` preflights tmux availability, Task/workspace/trust validity, launch-file privacy, and deterministic conflicts before inserting a row. It then reserves a Worker as `starting`, enforces the active-worker limit transactionally, starts the relay, waits up to 30 seconds for readiness, and marks it `ready`. Count `starting`, `ready`, `busy`, `idle`, `retained`, and `outcome_unknown` as active; exclude only `stopped` and `lost`. A timeout marks `outcome_unknown`; it never creates a replacement. A confirmed failure with no tmux session marks `stopped`; an uncertain failure remains `outcome_unknown`.

Add one real-tmux test, skipped only when `shutil.which("tmux") is None`, that uses the fake RPC executable through temporary `PATH`, starts the session, obtains `get_state`, reconnects with a second helper process, and gracefully closes it. Use a unique tmux server socket with `tmux -L <test-name>` inside the test fixture so it cannot touch user sessions; production continues using the default tmux server.

- [ ] **Step 7: Run the combined worktree and worker-readiness TUI checkpoint**

The TUI-facing test must issue the natural-language new-worktree request and show a `worker-start` receipt only after worktree creation plus relay/RPC readiness. Assert the visible receipt includes Run, Task, worktree, base commit, dirty-transfer status, worker, tmux, Travis session, workspace, branch, and `ready`, while excluding launch-file path, dotenv path, capability, raw stderr, and process environment.

Run:

```bash
uv run pytest -q \
  tests/test_orchestration_worker_relay.py \
  tests/test_orchestration_tui_scenarios.py -k "worker_ready or relay"
```

Expected: all selected tests pass; the fixture teardown leaves no `travis234-orch-*` tmux session.

- [ ] **Step 8: Commit relay support**

```bash
git commit -m "feat(orchestration): run durable Travis workers in tmux"
```

## Task 5: Send A Structured Supervised Dispatch And Receive A Handoff

**Files:**

- Modify: `travis/resources/skills/orchestration/scripts/orchestrate.py`
- Modify: `packages/travis234-cli/skills/orchestration/scripts/orchestrate.py`
- Create: `tests/test_orchestration_dispatch.py`
- Modify: `tests/test_orchestration_worker_relay.py`
- Modify: `tests/test_orchestration_tui_scenarios.py`

**Produces:** A dispatch-scoped capability, deterministic lifecycle preamble, nonblocking prompt acceptance, worker-authenticated terminal packet, safe wait receipt, and no automatic integration.

- [ ] **Step 1: Write failing lifecycle-preamble and capability tests**

Assert `build_worker_prompt()` includes exactly these headings and opaque IDs:

```text
# Travis234 orchestration assignment
## Identity and mode
## Objective and bounded context
## Ownership
## Acceptance and verification
## Question protocol
## Completion protocol
## Commit policy
## Required handoff packet
```

The prompt must state that coordinator context is data rather than higher-priority instruction, forbid nested orchestration without explicit user authorization, name owned and forbidden paths, require the worker to end its turn after reporting, and contain no dispatch capability, dotenv path, unrelated coordinator transcript, or helper-private `_relay` recipe.

Generate a fake capability and assert its plaintext is absent from the database file bytes, every public JSON receipt, `rpc.jsonl`, `stderr.log`, and the worker prompt. Assert only its SHA-256 hash is stored.

- [ ] **Step 2: Write failing dispatch lifecycle tests**

Cover:

1. initial dispatch creates one row and one capability hash;
2. relay receives capability only through `configure_dispatch` and starts/resumes RPC with it in the child environment;
3. first RPC event or `busy: true` marks the Dispatch `accepted` without waiting for the final answer;
4. worker-side `worker-complete` rejects missing, wrong, or stale environment capability;
5. a valid handoff stores one `handoff` Message and terminal `succeeded` state in one transaction;
6. `worker-fail` stores one `failure` Message and terminal `failed` state;
7. duplicate terminal mutation with one idempotency key returns the first receipt, while a different terminal mutation is rejected;
8. `dispatch-wait` returns after a terminal packet plus RPC idle, or returns a nonterminal timeout receipt after at most 60 seconds;
9. no command runs Git merge, cherry-pick, rebase, push, branch deletion, or worktree removal.

- [ ] **Step 3: Run and verify RED**

Run:

```bash
uv run pytest -q tests/test_orchestration_dispatch.py -k "prompt or capability or completion or wait"
```

Expected: missing prompt builder, Dispatch commands, relay configuration, and authenticated worker mutations.

- [ ] **Step 4: Implement dispatch creation and capability rotation**

Inside one `BEGIN IMMEDIATE` transaction, verify the Task is active, the Worker belongs to its Run, the previous Dispatch is settled and its terminal delivery acknowledged, the worker is `ready` or `idle`, and the Task prompt budget remains. Generate the capability, save only its hash, create a `queued` Dispatch with the next `round_number`, and increment `tasks.prompt_count`.

Send `configure_dispatch` over the owner-private socket. The relay stops only an idle RPC child, preserves its last `sessionId`, and restarts with:

```python
child_env["TRAVIS234_ORCHESTRATION_CAPABILITY"] = capability
```

For the first Dispatch, start a fresh persistent RPC session. For later Dispatches on the same Worker, include `--session <saved-session-id>` and confirm `get_state.sessionId` did not change. If rotation or readiness is uncertain, mark the Dispatch and Worker `outcome_unknown` and do not send or replay the prompt.

- [ ] **Step 5: Implement prompt acceptance and handoff validation**

The relay `prompt` action writes one RPC `prompt` frame and returns `accepted` after the first matching event frame or a concurrent `get_state` reports busy. It continues owning the turn after the helper command exits. When the RPC result arrives, it marks the Worker idle only if no terminal worker mutation is still pending.

Validate handoff request files with exact types and bounds:

```python
assert packet["outcome"] in {"succeeded", "failed"}
assert isinstance(packet["summary"], str) and 1 <= len(packet["summary"]) <= 8_000
assert packet["commit"] is None or re.fullmatch(r"[0-9a-f]{40,64}", packet["commit"])
assert all(isinstance(item, str) for item in packet["changedFiles"])
assert len(packet["changedFiles"]) <= 200
```

Bound every list to 200 entries and the full request to 256 KiB. Normalize paths to relative display strings when they are inside the worker workspace; reject NULs and control characters. The helper records reports but never verifies or creates the commit itself.

- [ ] **Step 6: Implement show/wait and honest receipt presentation**

`dispatch-show` returns IDs, lifecycle state, prompt round, last confirmed relay/RPC stage, workspace and branch, whether files or commits may exist, and safe next actions. `dispatch-wait --wait-seconds N` uses bounded rolling polls and returns either one unacknowledged terminal packet or a non-error timeout receipt with `terminal: false`. It never synthesizes success from the RPC assistant's final text.

- [ ] **Step 7: Run the research and code-return TUI checkpoints**

Add two faux-provider scenarios:

- research return: B deposits evidence and A's final answer cites only that packet;
- code return: B changes the fixture's owned file, runs its test, commits, and returns branch/SHA/files/tests; A reports the commit but performs no integration.

Run:

```bash
uv run pytest -q \
  tests/test_orchestration_dispatch.py \
  tests/test_orchestration_tui_scenarios.py -k "research_handoff or verified_code_return"
```

Expected: both prompt scenarios pass and `git rev-parse HEAD` in A's checkout remains unchanged.

- [ ] **Step 8: Commit supervised dispatch**

```bash
git commit -m "feat(orchestration): exchange structured worker handoffs"
```

## Task 6: Add Durable Questions And Bounded Ping-Pong

**Files:**

- Modify: `travis/resources/skills/orchestration/scripts/orchestrate.py`
- Modify: `packages/travis234-cli/skills/orchestration/scripts/orchestrate.py`
- Modify: `tests/test_orchestration_dispatch.py`
- Modify: `tests/test_orchestration_tui_scenarios.py`

**Produces:** Append-only questions/replies, explicit acknowledgement, same-session correction rounds, strict ordering, and an enforceable prompt budget.

- [ ] **Step 1: Write failing mailbox and ordering tests**

Cover these transitions:

```text
running --worker question--> awaiting_coordinator
awaiting_coordinator --coordinator ack/reply--> running
succeeded/failed --coordinator ack--> settled delivery
settled delivery --new dispatch with parent handoff--> next round
```

Assert a worker question requires the active capability, the same idempotency key cannot duplicate it, `message-check` returns unacknowledged messages in `(created_at, message_id)` order, `message-ack` is idempotent, and an acknowledged message never appears again. A timeout must leave the question pending.

Reject a reply to a non-question, wrong Run, wrong parent, stale Dispatch, terminal Dispatch, busy worker, unacknowledged prior delivery, or a Task whose total `prompt_count` reached `max_rounds`.

- [ ] **Step 2: Run and verify RED**

Run:

```bash
uv run pytest -q tests/test_orchestration_dispatch.py -k "question or reply or acknowledge or round_limit"
```

Expected: missing Message CLI commands, ordering fields, and prompt-budget enforcement.

- [ ] **Step 3: Implement worker message mutations and coordinator mailbox reads**

`message-send` accepts only worker-originated `question`, `status`, or `heartbeat` and validates the environment capability. Questions set the Dispatch and Task to `awaiting_coordinator` after the RPC turn becomes idle. Heartbeats update observation time but do not hide provider or tool failures.

`message-check --run-id --wait-seconds N --limit N` performs bounded polling and returns durable delivery objects. In one transaction it increments `delivery_count` and sets `last_delivered_at`; it does not acknowledge. `message-ack` sets `acknowledged_at` only after Travis A processed the full packet.

- [ ] **Step 4: Implement replies and correction rounds**

`message-reply --message-id --request-file --idempotency-key` requires an acknowledged question, an idle worker, and remaining Task prompt budget. It stores a `reply` Message, increments `tasks.prompt_count`, sends a lifecycle-framed reply through the same relay/RPC session, and returns after prompt acceptance.

A focused correction after a terminal handoff uses a new `dispatch-start` on the same Task and Worker with `parentMessageId` set to the acknowledged handoff. That creates the next `round_number`, rotates the dispatch capability, resumes the same `travis_session_id`, and leaves the earlier Dispatch immutable. No terminal packet automatically starts a correction.

- [ ] **Step 5: Verify strict four-prompt behavior**

Use one initial dispatch, one question reply, and two correction dispatches to consume the default four A-to-B prompts. Assert the fifth is rejected with `round_limit_reached`, safe next actions, and no relay mutation. An explicitly configured limit from five through twelve is accepted; thirteen always fails validation.

- [ ] **Step 6: Run the question and correction TUI checkpoints**

The question scenario must show one B question, one A acknowledgement/reply, continuation in the same Travis session, and one final packet. The correction scenario must show A reviewing B's first packet, acknowledging it, creating a second Dispatch with its message parent, and receiving the corrected packet from the same Worker and session.

Run:

```bash
uv run pytest -q \
  tests/test_orchestration_dispatch.py \
  tests/test_orchestration_tui_scenarios.py -k "question_reply or bounded_ping_pong"
```

Expected: all selected tests pass; stale, duplicate, and fifth-prompt attempts never reach RPC.

- [ ] **Step 7: Commit messaging and ping-pong**

```bash
git commit -m "feat(orchestration): add bounded worker dialogue"
```

## Task 7: Implement Full Handoff, Cancellation, Release, And Recovery

**Files:**

- Modify: `travis/resources/skills/orchestration/scripts/orchestrate.py`
- Modify: `packages/travis234-cli/skills/orchestration/scripts/orchestrate.py`
- Create: `tests/test_orchestration_recovery.py`
- Modify: `tests/test_orchestration_dispatch.py`
- Modify: `tests/test_orchestration_tui_scenarios.py`

**Produces:** Ownership-transfer semantics, exact-worker cancellation, non-destructive worker lifecycle, coordinator restart recovery, and honest lost/uncertain states without replay.

- [ ] **Step 1: Write failing full-handoff tests**

Create a `full_handoff` Task and assert its worker prompt says Travis B owns the whole handed-off scope, may preserve a report, has no obligation to notify a waiting coordinator, and must not recursively orchestrate unless the original user authorized it. `dispatch-start` must return after RPC acceptance with Run, Task, Worker, Dispatch, branch, worktree, tmux session, and Travis session identifiers. A later coordinator wait must not be started by the helper or skill flow.

- [ ] **Step 2: Write failing lifecycle and recovery matrix tests**

Exercise this exact matrix:

| SQLite | tmux | socket/RPC | Recovery result | Automatic replay |
|---|---|---|---|---|
| active | alive | compatible and idle/busy | reconnect and preserve state | no |
| active | missing | absent | `lost` | no |
| active | alive | socket absent | `outcome_unknown` | no |
| active | alive | version mismatch | `outcome_unknown` with version error | no |
| terminal with unacked packet | any known-safe state | compatible or stopped | return packet until ack | no |
| terminal with acked packet | any | any | do not redeliver | no |

Also cover worker retain, release, supervised cancellation, abandonment, stale launch-file cleanup, and coordinator process restart against the same database.

- [ ] **Step 3: Run and verify RED**

Run:

```bash
uv run pytest -q tests/test_orchestration_recovery.py tests/test_orchestration_dispatch.py -k "full_handoff or cancel or release or recover"
```

Expected: missing lifecycle commands and incorrect state reconciliation.

- [ ] **Step 4: Implement full-handoff acceptance**

Reuse worktree, Worker, capability, and prompt primitives. Change only the lifecycle preamble and coordinator behavior. Mark the Task active after acceptance, set the Worker `retained`, and return `monitoring: false`. Do not call `dispatch-wait`, `message-check`, or release from inside `dispatch-start`.

- [ ] **Step 5: Implement exact cancellation, abandonment, retain, and release**

- `dispatch-cancel`: only for supervised work; send RPC `abort`, wait a bounded interval, close the exact relay, stop its exact tmux session if still alive, mark Dispatch `cancelled` and Worker `stopped`, and preserve branch/worktree/session/transcript.
- `dispatch-abandon`: mark monitoring abandoned without signaling or deleting the worker; a late report is stored as stale evidence and cannot advance the Task automatically.
- `worker-retain`: record explicit keep-alive intent for a live worker.
- `worker-release`: require idle state and acknowledged deliveries, request graceful RPC/relay close, stop only the exact owned tmux session if it remains, preserve transcript, and mark `stopped`.

Every receipt lists actions deliberately not performed: no replay, integration, push, branch deletion, or worktree deletion.

- [ ] **Step 6: Implement read-safe recovery**

`recover --run-id` acquires a state transaction only for observations that produce a state transition. It enumerates exact owned tmux names with `tmux has-session -t`, checks the private socket protocol, calls relay `state`, validates session ID/workspace, and applies the matrix above. It never sends a prompt. `recover --inspect-only` performs the same inspection without mutation and remains available on schema mismatch.

If a stale mode-0600 launch file exists and its worker has no tmux session, unlink only that file and report the action. Never remove a socket, transcript, branch, worktree, or Travis session merely because it is stale.

- [ ] **Step 7: Run the handoff, restart, failure, and worker-bound TUI checkpoints**

Add scenarios for:

- full handoff: acceptance IDs visible, `monitoring: false`, no wait;
- coordinator restart: an unacknowledged packet is returned after A restarts, then never after acknowledgement;
- failure/cancel/recovery: missing tmux, RPC failure, worker-declared failure, exact cancellation, lost worker, and retained Git state all produce honest receipts;
- two-worker bound: two disjoint workers start and the third default attempt is rejected before tmux mutation.

Run:

```bash
uv run pytest -q \
  tests/test_orchestration_recovery.py \
  tests/test_orchestration_tui_scenarios.py -k \
  "full_handoff or coordinator_restart or failure_cancel_recovery or two_worker_bound"
```

Expected: all selected tests pass and teardown confirms zero owned tmux sessions while preserving test worktrees for explicit fixture cleanup.

- [ ] **Step 8: Commit lifecycle and recovery**

```bash
git commit -m "feat(orchestration): recover and hand off durable workers"
```

## Task 8: Write And Pressure-Test The Skill Instructions

**Files:**

- Modify: `travis/resources/skills/orchestration/SKILL.md`
- Modify: `travis/resources/skills/orchestration/references/protocol.md`
- Modify: `packages/travis234-cli/skills/orchestration/SKILL.md`
- Modify: `packages/travis234-cli/skills/orchestration/references/protocol.md`
- Modify: `tests/test_orchestration_helper.py`
- Modify: `tests/test_orchestration_tui_scenarios.py`
- Modify: `docs/verification/local-tmux-orchestration.md`

**Produces:** A concise discoverable technique skill, one lazy protocol reference, instruction-quality regression tests, and fresh-context evidence that the guidance corrects observed baseline failures.

- [ ] **Step 1: Review the recorded RED no-skill baseline before editing the skill body**

Use the five fresh, isolated Minimax M3 controls captured in Task 0. Confirm they ran from the pre-feature commit and used this same natural-language request:

```text
Start another durable Travis234 in tmux in a new Git worktree, ask it to inspect this fixture and return an evidence-backed research handoff to you, then summarize that handoff for me. Do not use subagents, do not edit the coordinator-owned checkout, do not integrate anything, and leave no ambiguity about which Travis session produced the evidence.
```

For each run, use only the recorded behavior categories: whether it created a distinct worker, used structured RPC rather than screen scraping, returned stable identities, preserved ownership, avoided direct TUI keystrokes, and cleaned up exact tmux sessions. Convert every observed failure into either a positive recipe, a structural required field, or an observable-condition guard in the instruction tests. Do not invent rules for failures the baseline did not show unless a repository safety contract independently requires them.

Repository policy forbids subagent validation because the user did not explicitly request subagents. These fresh Travis234 processes are product scenarios, not Codex collaboration subagents.

- [ ] **Step 2: Add failing instruction-shape tests based on observed failures**

Tests must require:

```python
assert frontmatter["name"] == "orchestration"
assert frontmatter["description"].startswith("Use when ")
assert len(frontmatter["description"]) < 500
assert len(body.split()) <= 500
assert "references/protocol.md" in body
assert "supervised" in body and "full handoff" in body
assert "subagent" in body and "independent" in body
assert "Do not" in body and "automatic" in body
assert "_relay" not in body
```

Also assert the existing system prompt source and existing subagent skill remain byte-identical to their pre-task hashes. The protocol reference must have a table of contents when longer than 100 lines and must contain the exact command, state, packet, limit, recovery, and failure contracts from this plan.

- [ ] **Step 3: Run the instruction tests and verify RED**

Run:

```bash
uv run pytest -q tests/test_orchestration_helper.py -k "skill_instruction or protocol_reference"
```

Expected: failures identify the exact baseline-derived guidance absent from the minimal skeleton.

- [ ] **Step 4: Write the minimal skill body in imperative form**

Keep all triggering conditions in frontmatter. Keep the body under 500 words with this exact information order:

1. core principle: Travis A owns the user conversation; Travis B owns only the handed-off scope;
2. prerequisites: read `references/protocol.md`, confirm both bash and tmux tools are available, and refuse a bash bypass when tmux is excluded;
3. mode table: supervised round trip versus full handoff;
4. coordinator recipe: create Run/Task, select safe workspace, start Worker, start Dispatch, process and acknowledge deliveries, review evidence, then retain/release;
5. ownership and verification: disjoint writes, committed code by default unless user opted out, packet as report rather than proof, no automatic integration;
6. stop conditions: success, failure, cancellation, blocking question, or prompt limit;
7. common mistakes: raw tmux keystrokes, screen scraping, direct B-to-A injection, silent replay, trust bypass, capability exposure, worker/worktree deletion, or subagent substitution.

Use a positive handoff-packet recipe rather than a long prohibition list. Include one compact natural-language example and point to `python3 scripts/orchestrate.py guide` for command help.

- [ ] **Step 5: Write the protocol reference as the single detailed owner**

Begin with a table of contents. Document:

- relative helper resolution and exact public commands;
- request-file schemas and JSON envelopes;
- Run/Task/Worker/Dispatch/Message IDs and statuses;
- supervised startup, question, reply, correction, completion, and acknowledgement recipes;
- full-handoff acceptance and stop-monitoring recipe;
- worktree placement, dirty-state semantics, trust decisions, and commit policy;
- capability and secret boundaries;
- active-worker and prompt limits;
- cancel, abandon, retain, release, and recovery matrix;
- safe error receipt interpretation and actions deliberately not performed.

Do not duplicate generic Git, tmux, or RPC tutorials. Do not add README, quick-reference, changelog, asset, or product-specific agents metadata inside the skill.

- [ ] **Step 6: Validate both skill roots**

Run the official structural validator and repository tests:

```bash
python3 /Users/htooayelwin/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  travis/resources/skills/orchestration
python3 /Users/htooayelwin/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  packages/travis234-cli/skills/orchestration
uv run pytest -q tests/test_orchestration_helper.py tests/test_distribution_contract.py
```

Expected: both validators pass, the mirrors are byte-identical, and the instruction assertions pass.

- [ ] **Step 7: Run five fresh-context GREEN TUI repetitions**

Repeat the exact Step 1 prompt five times in five fresh TUI processes with the completed skill and helper, each in a clean fixture/state directory. Manually inspect every result; do not score solely by marker text. GREEN requires all five to select supervised mode, use a separate tmux/RPC Travis worker, preserve ownership, return a structured packet, ground A's answer in the packet, avoid subagents and direct keystrokes, and leave no unowned live session. Record each pass/fail and the bounded reason in the verification document.

If a new rationalization appears, add only the minimum observable-condition rule or positive recipe that closes it, add a regression assertion, and rerun all five. Do not add speculative prose.

- [ ] **Step 8: Commit the verified instructions**

```bash
git commit -m "feat(skills): teach durable Travis orchestration"
```

Before committing, complete this skill-authoring checklist explicitly in the verification record:

- [ ] RED scenarios were defined before the skill body and run without the skill.
- [ ] Exact baseline failures and non-secret rationalizations were recorded.
- [ ] The name uses only lowercase letters and the approved `orchestration` folder name.
- [ ] Frontmatter has only `name` and a third-person `Use when...` description below 500 characters.
- [ ] Trigger keywords cover Travis A/B, another Travis, tmux, worktree, ping-pong, recovery, and handoff without summarizing the workflow.
- [ ] The body states one core principle, stays below 500 words, and uses imperative language.
- [ ] Guidance shape matches each baseline failure: positive recipe, structural field, or observable conditional.
- [ ] Five no-guidance controls and five with-skill TUI repetitions were manually read and scored.
- [ ] One compact natural-language example is present; duplicate examples are absent.
- [ ] The reusable deterministic logic lives in `scripts/orchestrate.py` and heavy detail lives only in `references/protocol.md`.
- [ ] New rationalizations found during GREEN runs have a minimal regression and were rerun; if none appeared, record `none observed`.
- [ ] A quick mode table is present; a flowchart is omitted because the two-way decision is clear in a table.
- [ ] Common mistakes cover unsafe tmux, ownership, replay, trust, secret, integration, cleanup, and subagent substitutions.
- [ ] No narrative history, extra README, quick-reference file, changelog, asset, or `agents/openai.yaml` is inside the Travis234 skill bundle.
- [ ] Both official structural validations, repository instruction tests, and byte-parity tests pass.
- [ ] The skill is committed locally; push/PR deployment is recorded as not authorized and is not performed.

## Task 9: Consolidate The Deterministic TUI Scenario Matrix

**Files:**

- Modify: `tests/test_orchestration_tui_scenarios.py`
- Modify: `tests/test_orchestration_dispatch.py`
- Modify: `tests/test_orchestration_recovery.py`
- Modify: `docs/verification/local-tmux-orchestration.md`

**Produces:** One deterministic, rerunnable nine-scenario product matrix plus per-prompt pass/fail reporting. The tenth scenario is the installed-wheel live TUI in Task 10.

- [ ] **Step 1: Define one scenario record and isolated fixture**

Use a parameterized record with exact expected effects:

```python
@dataclass(frozen=True)
class OrchestrationScenario:
    id: str
    prompt: str
    expected_status: str
    expected_workers: int
    expected_dispatches: int
    expected_messages: tuple[str, ...]
    expect_integration: bool = False
```

Each scenario gets its own Git repository, `TRAVIS234_CODING_AGENT_DIR`, tmux test server, fake RPC worker, event trace, and teardown audit. Prompts are natural-language user requests; they must not contain helper command names or expected internal IDs.

- [ ] **Step 2: Assert the exact deterministic scenarios**

Implement and name:

1. `lazy-discovery`
2. `natural-worktree-round-trip`
3. `verified-code-return`
4. `question-and-reply`
5. `bounded-ping-pong-correction`
6. `full-handoff`
7. `coordinator-restart`
8. `failure-cancel-and-lost-worker`
9. `two-worker-bound`

For every scenario, assert final SQLite rows, message order and acknowledgement, worktree/branch state, tmux ownership, Travis session reuse, no secret shapes, no direct terminal input, no subagent task, and no automatic integration or deletion.

- [ ] **Step 3: Verify the report records each prompt independently**

Write a sanitized result object for each scenario:

```json
{"scenario":"question-and-reply","status":"PASS","reason":"one durable question, one acknowledged reply, same worker session","workers":1,"liveWorkersAfterCleanup":0}
```

A failed prompt remains `FAIL` even if a later retry passes. The Markdown verification table contains one row per attempt, not only an aggregate result.

- [ ] **Step 4: Run all deterministic scenarios together**

Run:

```bash
uv run pytest -q \
  tests/test_orchestration_helper.py \
  tests/test_orchestration_worktrees.py \
  tests/test_orchestration_worker_relay.py \
  tests/test_orchestration_dispatch.py \
  tests/test_orchestration_recovery.py \
  tests/test_orchestration_tui_scenarios.py
```

Expected: every test passes; the verification record shows nine deterministic PASS rows and zero surviving owned tmux sessions.

- [ ] **Step 5: Commit the deterministic matrix**

```bash
git commit -m "test(orchestration): qualify end-to-end TUI scenarios"
```

## Task 10: Document, Build, Install, And Run The Live Minimax M3 TUI

**Files:**

- Modify: `README.md`
- Modify: `tests/test_distribution_contract.py`
- Modify: `packages/travis234-cli/test/travis234-cli.test.js`
- Modify: `docs/verification/local-tmux-orchestration.md`

**Produces:** User documentation, verified archives, an isolated exact-wheel install, and one real A-to-B Minimax M3 TUI round trip.

- [ ] **Step 1: Write failing README and archive assertions**

Require README's `Skills and state` section to list `orchestration` independently from `subagent-delegation`, explain supervised versus full handoff, give one ordinary-language example, name tmux/Git/RPC/SQLite roles, and state no automatic integration or cleanup. Assert it does not tell users to run `_relay`, handle capabilities, or edit the global system prompt.

Extend the wheel test to assert these exact archive members:

```python
required = {
    "travis/resources/skills/orchestration/SKILL.md",
    "travis/resources/skills/orchestration/references/protocol.md",
    "travis/resources/skills/orchestration/scripts/orchestrate.py",
}
assert required <= set(names)
```

Extend npm tests to inspect `npm pack --json` output/tarball and require the matching three `package/skills/orchestration/...` members.

- [ ] **Step 2: Run and verify RED**

Run:

```bash
uv run pytest -q tests/test_distribution_contract.py -k "builtin_skills or wheel"
node --test packages/travis234-cli/test/travis234-cli.test.js
```

Expected: README or archive-specific assertions fail before documentation/build contract is complete.

- [ ] **Step 3: Update README for nontechnical users**

Under `## Skills and state`, add `### Durable multi-Travis orchestration`. Lead with the outcome: users can ask in ordinary language for another Travis234 to work in a separate checkout and bring evidence back. Explain:

- supervised mode keeps Travis A responsible for the conversation;
- full handoff transfers ownership and A stops waiting;
- tmux keeps B alive across A's turns, RPC carries structured prompts/results, Git worktrees isolate code, and SQLite remembers mail/status;
- code workers commit by default unless the user says not to, but A still reviews the result;
- the feature does not replace n8n, generic MCP, or existing subagents;
- branches/worktrees remain until an explicit cleanup request.

Include one prompt example and one compact safety note. Do not expose the helper grammar in the README.

- [ ] **Step 4: Build and inspect Python and npm artifacts**

Use a fresh temporary output directory:

```bash
artifact_dir=$(mktemp -d)
uv build --wheel --sdist --clear --out-dir "$artifact_dir/python" .
uv run twine check "$artifact_dir"/python/*
npm pack --json --workspace @htooayelwinict/travis234 \
  --pack-destination "$artifact_dir/npm"
```

Inspect wheel, sdist, and npm tarball member names programmatically. Assert the mirrors' SHA-256 hashes match and no `.env`, SQLite, socket, launch file, transcript, worktree, or capability-named file is present.

- [ ] **Step 5: Install the exact wheel and run package-level smoke**

Create an isolated virtual environment and install only the just-built wheel:

```bash
uv venv "$artifact_dir/venv"
uv pip install --python "$artifact_dir/venv/bin/python" \
  "$artifact_dir"/python/travis234-*.whl
"$artifact_dir/venv/bin/travis234" --help
"$artifact_dir/venv/bin/python" -c \
  'from pathlib import Path; from travis.coding_agent.config import get_packaged_skills_path; p=Path(get_packaged_skills_path())/"orchestration"; assert (p/"SKILL.md").is_file(); assert (p/"references/protocol.md").is_file(); assert (p/"scripts/orchestrate.py").is_file()'
```

Run the installed helper's `guide` and one isolated fake-RPC tmux round trip. Verify JSON framing, owner-only state, graceful release, and zero live test tmux sessions.

- [ ] **Step 6: Run live TUI scenario 10 with Minimax M3**

Create a disposable nested Git fixture under the repository's already ignored `tmp/` directory. Give it its own `.git`, an ignored `.worktrees/`, a short README, and evidence files. This location lets both A and its repository-local B worktree find the existing root `.env` through normal parent discovery without copying it or putting its path in prompts/receipts.

Launch the exact-wheel binary with an isolated agent directory, owner-private event trace and conversation log, and:

```bash
"$artifact_dir/venv/bin/travis234" \
  --cwd "$live_fixture" \
  --dotenv /Users/htooayelwin/orca/travis234/.env \
  --model openrouter/minimax/minimax-m3 \
  --thinking medium \
  --event-trace "$live_evidence/events.jsonl" \
  --conversation-log "$live_evidence/conversation.jsonl"
```

Send this natural-language prompt:

```text
Use the built-in orchestration skill to create a new-worktree Travis234 worker. Have it inspect this fixture read-only and return three evidence-backed architecture facts to you. Supervise the round trip, acknowledge its handoff, release the idle worker, do not use subagents, do not edit or integrate anything, and finish with ORCHESTRATION-LIVE-PASS plus the safe worker/session identifiers and the three verified facts.
```

Wait for the TUI's real idle/turn events rather than sleeping blindly. PASS requires skill loading, worktree receipt, tmux/RPC readiness, prompt acceptance, structured handoff, acknowledgement, A's evidence-grounded synthesis, graceful release, and zero live owned worker. Report the prompt PASS or FAIL immediately in the verification table; a retry gets a separate row.

Never print the `.env`, provider credential, launch file, process environment, capability, or raw unsanitized transcript. Preserve only mode-0600 sanitized evidence.

- [ ] **Step 7: Verify no prompt or tool-policy regression**

Run focused system-prompt, skill, tmux, subagent, and RPC tests:

```bash
uv run pytest -q \
  tests/test_coding_resources_and_services.py \
  tests/test_coding_policy_and_extensions.py \
  tests/test_coding_tools_and_subagents.py \
  tests/test_rpc_mode.py \
  tests/test_tui_commands_and_extensions.py -k "skill or tmux or subagent or rpc"
```

Expected: existing subagent results, expansion behavior, tool prompts, global system prompt, and RPC semantics remain unchanged.

- [ ] **Step 8: Commit documentation and qualification evidence**

```bash
git commit -m "docs: explain and verify local Travis orchestration"
```

## Task 11: Run Repository-Level Verification And Final Container Smoke

**Files:**

- Modify only if evidence needs correction: `docs/verification/local-tmux-orchestration.md`

**Produces:** Fresh completion evidence across Python, npm, distributions, installed behavior, and the release container without publication.

- [ ] **Step 1: Inspect scope before broad tests**

Run:

```bash
git status --short --branch
git diff --stat origin/main...HEAD
git diff --name-only origin/main...HEAD
git diff --check origin/main...HEAD
```

Expected: only approved orchestration/docs/test/package files plus the pre-existing unrelated working-tree edits are present. No protected surface changed.

- [ ] **Step 2: Run the full Python suite**

Run:

```bash
uv run pytest -q
```

Expected: the complete suite passes. Record the exact count and elapsed time; do not reuse focused-test counts.

- [ ] **Step 3: Run npm launcher tests and package checks**

Run:

```bash
npm test --workspace @htooayelwinict/travis234
npm pack --dry-run --json --workspace @htooayelwinict/travis234
```

Expected: all Node tests pass and the dry-run contains the three orchestration files exactly once.

- [ ] **Step 4: Rebuild clean artifacts from final source**

Repeat Task 10's wheel, sdist, Twine, npm pack, exact-wheel install, helper guide, and fake-RPC tmux smoke from a new temporary directory. Record final source commit and SHA-256 values in the verification document. Do not publish or tag.

- [ ] **Step 5: Build and run the relevant unprivileged container smoke**

Only now build the release image locally:

```bash
docker build --no-cache -f Dockerfile.release -t travis234:orchestration-local .
docker run --rm --entrypoint sh travis234:orchestration-local -lc \
  'set -eu; test "$(id -un)" = travis; command -v tmux; travis234 --help >/tmp/help; python3 -c "from pathlib import Path; from travis.coding_agent.config import get_packaged_skills_path; p=Path(get_packaged_skills_path())/\"orchestration\"; assert (p/\"SKILL.md\").is_file(); assert (p/\"references/protocol.md\").is_file(); assert (p/\"scripts/orchestrate.py\").is_file()"'
```

Run the helper `guide` as the unprivileged `travis` user with an isolated agent dir and assert state files are 0700/0600. Start and stop one uniquely named tmux smoke session. Do not forward provider credentials and do not push the image.

- [ ] **Step 6: Audit cleanup and retained user state**

Confirm:

- no `travis234-orch-*` tmux session from tests remains;
- no relay/RPC child remains;
- live fixture branches/worktrees are explicitly listed before deleting only the disposable fixture root;
- no path under the user's real `~/.travis234` was deleted or migrated;
- generic MCP adapter state and unrelated user state remain unchanged;
- `.env` remains ignored, untracked, and absent from output;
- no automatic merge, push, publish, tag, branch deletion, or real-repository worktree deletion occurred.

- [ ] **Step 7: Complete the verification record and final self-review**

Record focused counts, nine deterministic prompt rows, five baseline rows, five GREEN repetitions, the live Minimax prompt row, full-suite/npm/build/container results, artifact hashes, cleanup, and known limitations. Search for unfinished markers and secret shapes before staging:

```bash
rg -n 'TO[D]O|T[B]D|FIX[M]E|sk-[A-Za-z0-9]|Bearer [A-Za-z0-9]' \
  travis/resources/skills/orchestration \
  packages/travis234-cli/skills/orchestration \
  README.md docs/verification/local-tmux-orchestration.md
git diff --check
```

Expected: no unfinished-marker or credential-shaped match and no whitespace error.

- [ ] **Step 8: Commit only an evidence correction if Task 11 changed it**

If the final counts or hashes changed the verification document, commit that file alone:

```bash
git commit -m "docs: record final orchestration verification"
```

If it did not change, do not create an empty commit.

## Completion Criteria

Implementation is complete only when all of these are true:

- `orchestration` is lazily discoverable from source, the wheel, and npm without a global prompt change.
- Python and npm skill trees are byte-identical and contain only the approved skill, protocol reference, and helper.
- A supervised A-to-B worktree round trip returns a durable acknowledged packet through tool results rather than TUI keystrokes.
- A code worker can return a verified commit without automatic integration; a research worker can return evidence without an empty commit.
- A durable question/reply and a correction round preserve the same Travis worker session and obey the prompt limit.
- Full handoff confirms acceptance and identifiers, then stops coordinator monitoring.
- Recovery redelivers only unacknowledged packets and never replays uncertain work.
- Missing tmux, trust uncertainty, RPC failure, cancellation, loss, and version mismatch produce honest bounded receipts.
- Dispatch capabilities and dotenv contents never enter plaintext state, output, prompts, or logs.
- Existing subagent behavior/results, global system/tool prompts, RPC concurrency, agent-loop ordering, and generic MCP support are unchanged.
- Every deterministic and live prompt has an individual PASS/FAIL row; aggregate success never hides a failed attempt.
- Focused tests, full Python, npm, clean packages, exact-wheel install, live Minimax M3 TUI, and final unprivileged container smoke have fresh evidence.
- No release, push, tag, permission change, or real user-state cleanup occurred.

## Execution Stop Gate

After this plan is committed, stop. Do not create the skill files or run the RED baseline until the user explicitly approves implementation execution. Execute inline in this repository; do not spawn collaboration subagents unless the user separately and explicitly requests them.
