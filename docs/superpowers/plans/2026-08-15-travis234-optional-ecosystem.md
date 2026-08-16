# Travis234 Optional Ecosystem Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Repository guidance prohibits subagents unless the user explicitly requests them, so implementation and review are inline by default.

**Goal:** Finish contract parity with explicit opt-in memory, bounded MCP resources/prompts/reconnection, integration conformance for optional browser/desktop tools, and an evidence-based native-acceleration gate—without making any of them mandatory for core turns.

**Architecture:** Add a disabled-by-default project memory store and one explicit memory tool; facts are never auto-retained or injected. Extend the separately packaged single-proxy MCP adapter with bounded resource/prompt operations and connection recovery that never replays a failed request. Define an executable conformance harness for optional browser/computer integrations instead of adding core dependencies. Profile representative hot paths and retain Python unless a separately reviewed candidate clears strict thresholds. Only after all Phase 5 work and non-container gates pass, run the program's deferred release-container qualification.

**Tech Stack:** Python 3.13, stdlib SQLite, Phase 1C artifacts, Phase 1D policy, existing MCP Python SDK v2 adapter, pytest fixture MCP servers, native TUI, deterministic benchmarks, npm, wheel/sdist builds, and Docker only at the final gate.

## Global Constraints

- Start from the verified Phase 4 commit on branch `codex/optional-ecosystem`.
- `memory.enabled` defaults false and can be enabled only by global/user-owned settings or an explicit runtime override. Project settings may lower limits but cannot enable memory or add scopes.
- Memory is accessed only by explicit `status`, `recall`, `retain`, and `delete` operations. Do not auto-retain, summarize into memory, reflect, or inject recalled facts into a prompt.
- Retrieved memory is labeled untrusted data below system, user, repository, and skill instructions.
- Keep all memory state at `~/.travis234/agent/memory.sqlite3`; do not add an alternate state root or migration alias.
- Preserve the separately packaged generic MCP adapter and exactly one Travis tool named `mcp`. Do not generate per-server tools or restore any Ghost-specific integration.
- MCP retry may restore a connection but must never replay a failed tool/resource/prompt request.
- OAuth is explicitly outside this executable scope because Travis234 has no approved credential-broker contract for refresh tokens. Existing strict environment references remain the only credential path; OAuth requires a separately approved design.
- Browser and desktop integrations remain optional extension packages or MCP servers. Do not add them to root dependencies or grant them a policy bypass.
- Add no native dependency unless a reproducible candidate is at least 2x faster, the target accounts for at least 5% of measured end-to-end wall time, variance is acceptable, and packaging receives separate approval.
- Do not run container work until Tasks 1–7 and all non-container verification in Task 8 pass.

---

### Task 1: Implement bounded opt-in memory storage

**Files:**
- Create: `travis/coding_agent/memory/__init__.py`
- Create: `travis/coding_agent/memory/types.py`
- Create: `travis/coding_agent/memory/store.py`
- Modify: `travis/coding_agent/settings_manager.py`
- Test: `tests/test_memory_settings.py`
- Test: `tests/test_memory_store.py`

**Interfaces:**
- Add `MemoryScope = Literal["project", "global"]` and immutable `MemoryFact` with ID, content, tags, scope, project key, provenance, created/updated timestamps, optional expiry, and source-session fingerprint.
- IDs are `mem_` plus 32 lowercase hexadecimal characters.
- Add `MemorySettings(enabled, allowed_scopes, max_fact_bytes, max_facts_per_scope, max_total_bytes, recall_limit, recall_bytes)`.
- Defaults: disabled, project-only, 64 KiB/fact, 5,000 facts/project or global scope, 1 GiB total database/WAL footprint, 20 recall results, and 32 KiB inline recall.
- Add `MemoryStore.retain`, `recall`, `get`, `delete`, `counts`, and `close` at agent root `memory.sqlite3`.

- [ ] **Step 1: Write failing settings, scope, and storage tests**

```python
def test_project_settings_cannot_enable_memory(tmp_path):
    settings = settings_with(global_memory={"enabled": False},
                             project_memory={"enabled": True}, trusted=True)
    assert settings.get_memory_settings().enabled is False

def test_default_memory_is_disabled_and_project_scoped():
    value = SettingsManager.in_memory().get_memory_settings()
    assert value.enabled is False
    assert value.allowed_scopes == ("project",)
```

Cover global enable, project lower-only limits, scope widening rejection, 64 KiB byte accounting, 5,000-row scope cap, 1 GiB database-cap refusal, Unicode normalization, idempotent same-scope retain by content/tags digest, maximum 16 tags of 64 bytes, 1 KiB recall-query cap, exact project hash isolation, global scope only when globally allowed, expiry exclusion, deterministic recall ordering, exact-ID deletion, concurrent access, reopen, `0600`, WAL, corruption/read-only failure, and no automatic pruning.

- [ ] **Step 2: Run memory storage tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_memory_settings.py tests/test_memory_store.py -q
```

- [ ] **Step 3: Implement strict persistence and deterministic recall**

Use ordinary indexed SQLite tables rather than relying on optional FTS extensions. Within one scope/project key, normalized content plus sorted tags forms an idempotency digest; retaining it again updates provenance/expiry/timestamp and returns the existing ID. Tokenize/casefold the bounded query, rank exact tag matches before content token matches, then sort by score, updated time, and ID. Recall project facts by default; global facts require both allowed configuration and an explicit request. Expired rows are invisible but remain until exact delete/admin maintenance.

- [ ] **Step 4: Verify privacy and limits**

Store project identity only as SHA-256 of the canonical workspace path. Provenance is a bounded enum/source fingerprint, never a prompt or username. Reject serialized metadata over its limit. Verify database text columns do not contain canonical workspace paths or session IDs.

- [ ] **Step 5: Verify and commit**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_memory_settings.py tests/test_memory_store.py \
  tests/test_operation_store.py tests/test_session_index.py -q
wc -l travis/coding_agent/memory/types.py travis/coding_agent/memory/store.py
git add travis/coding_agent/memory travis/coding_agent/settings_manager.py \
  tests/test_memory_settings.py tests/test_memory_store.py
git commit -m "feat(memory): add bounded opt-in fact storage"
```

Use the actual existing SQLite-owner test names if repository discovery shows those checks are consolidated elsewhere.

### Task 2: Expose one explicit, policy-controlled memory tool

**Files:**
- Create: `travis/coding_agent/memory/tool.py`
- Create: `travis/coding_agent/memory/safety.py`
- Modify: `travis/coding_agent/agent_session.py`
- Modify: `travis/coding_agent/agent_session_services.py`
- Modify: `travis/coding_agent/agent_session_runtime.py`
- Test: `tests/test_memory_tool.py`
- Test: `tests/test_memory_safety.py`

**Interfaces:**
- Register exactly one `memory` tool only when memory is enabled.
- Respect existing tool allow/exclude/no-tools filtering; enabling memory cannot reactivate an excluded tool and must not open its store solely because the tool is unavailable.
- Actions: `status`, `recall`, `retain`, `delete`; validate an exact discriminated schema.
- Conservatively declare effects `{read, write}` because Phase 1D metadata is tool-level.
- Provide policy safe context containing only memory action and requested scope, never fact/query content or tags.
- `recall` returns no more than 20 records/32 KiB inline; promote a complete larger normalized result through Phase 1C and return its artifact ID.
- `retain` rejects content when the existing credential redactor would change it or when secret-pattern validation detects credential material.
- `delete` requires an exact authorized fact ID and cannot query by content.

- [ ] **Step 1: Write failing tool and injection-resistance tests**

Test disabled tool absence, exact schema/effects, status without content, project/global isolation, explicit recall, idempotent explicit retain, exact delete, malformed/oversized inputs, expiry, artifact spill, cancellation, unavailable/corrupt store as a shaped tool error without turn failure, Phase 1D denial before store access, and Phase 4 operation metadata containing only action/fingerprint. Seed prompt-injection-like memory and assert it is returned inside an `[Untrusted memory data]` envelope, never added directly to agent messages/system prompt.

- [ ] **Step 2: Run memory tool tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_memory_tool.py tests/test_memory_safety.py -q
```

- [ ] **Step 3: Implement explicit operations and safety shaping**

Normalize action inputs before store calls. Require a non-empty provenance label chosen from `user_requested`, `agent_explicit`, or `imported_explicit`; hash any source session. Return bounded IDs/timestamps/tags/content, with the untrusted label repeated per record. A detected credential returns stable code `sensitive_content` and stores nothing; error text cannot echo the input.

- [ ] **Step 4: Bind and close only when enabled**

Compose one scoped store connection per session through session services at the existing agent root and close that connection during session teardown. SQLite coordinates connections to the shared database across session switches/processes; do not share a closable session object across owners. Do not open/create the database when disabled or when only checking settings.

- [ ] **Step 5: Verify integrations and commit**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_memory_tool.py tests/test_memory_safety.py \
  tests/test_tool_policy_integration.py tests/test_operation_tool_effects.py \
  tests/test_durable_artifact_store.py -q
git add travis/coding_agent/memory travis/coding_agent/agent_session.py \
  travis/coding_agent/agent_session_services.py travis/coding_agent/agent_session_runtime.py \
  tests/test_memory_tool.py tests/test_memory_safety.py
git commit -m "feat(memory): expose explicit recall and retention"
```

### Task 3: Add memory status UX, lifecycle diagnostics, and acceptance

**Files:**
- Create: `travis/tui/interactive_memory.py`
- Modify: `travis/tui/interactive_mode.py`
- Modify: `travis/tui/interactive_command_dispatcher.py`
- Modify: `travis/tui/user_commands.py`
- Modify: `docs/architecture/contract-parity.md`
- Modify: `docs/settings.md`
- Modify: `README.md`
- Modify: `scripts/verify_acceptance.py`
- Test: `tests/tui/test_interactive_memory.py`
- Test: `tests/architecture/test_acceptance_matrix.py`

**Interfaces:**
- Add read-only `/memory status`; data recall/retain/delete remain behind the policy-controlled tool.
- Status reports enabled flag, allowed scopes, effective limits, project/global counts, and store availability—never fact content, query history, project path, or session identity.
- Acceptance reports the same bounded contract and whether automatic injection/retention are false.

- [ ] **Step 1: Write failing status/acceptance tests**

Cover disabled without DB creation, enabled empty/non-empty counts, unavailable/corrupt store, project switch, dispatcher ownership, narrow terminal width, and secret/path absence. Require explicit parity fields `automaticRetention: false` and `automaticInjection: false`.

- [ ] **Step 2: Run the UX tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/tui/test_interactive_memory.py tests/architecture/test_acceptance_matrix.py -q
```

- [ ] **Step 3: Implement status and document authority**

Explain enabling/scopes, lower-only project settings, capacity/expiry, deletion, credential rejection, untrusted recall, artifacts, and the conservative read/write approval tradeoff. Do not document any automatic memory workflow.

- [ ] **Step 4: Verify architecture and commit**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/tui/test_interactive_memory.py tests/architecture/test_acceptance_matrix.py \
  tests/architecture/test_facade_boundaries.py -q
git add travis/tui/interactive_memory.py travis/tui/interactive_mode.py \
  travis/tui/interactive_command_dispatcher.py travis/tui/user_commands.py \
  docs README.md scripts/verify_acceptance.py tests/tui/test_interactive_memory.py \
  tests/architecture/test_acceptance_matrix.py
git commit -m "docs: define explicit memory boundaries"
```

### Task 4: Extend the single MCP proxy with bounded resources and prompts

**Files:**
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/proxy_tool.py`
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/runtime.py`
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/results.py`
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/output_guard.py`
- Modify: `packages/travis234-mcp-adapter/tests/fixtures/server.py`
- Create: `packages/travis234-mcp-adapter/tests/test_proxy_resources.py`
- Create: `packages/travis234-mcp-adapter/tests/test_proxy_prompts.py`
- Modify: `packages/travis234-mcp-adapter/tests/test_proxy_tool.py`

**Interfaces:**
- Retain legacy tool-list/search/describe/call shapes and normalize them into an explicit internal operation enum.
- Add operations `resources.list`, `resources.read`, `prompts.list`, and `prompts.get` under the same `mcp` schema.
- Require an explicit server for every connected operation; status remains connection-free.
- Resource listing returns per-generation opaque `mcp-resource-<32hex>` references mapped internally to server URIs; model-visible results never expose URI userinfo/query secrets or host spill paths.
- Resource bodies and retrieved prompt messages are labeled untrusted server-supplied data and never promoted to system/user instruction authority.
- Bounds: 100 catalog pages, 5,000 resource/prompt entries, 20 search matches, 100 prompt messages, 8 MiB raw resource/prompt response, and existing guarded inline/spill output.
- Reject repeated cursors, duplicate ambiguous entries, unsupported subscriptions, and non-text/blob content not handled by current structured conversion.

- [ ] **Step 1: Write failing one-proxy schema and pagination tests**

Test exact one-tool registration, compatibility aliases, each new operation, opaque resource reference generation/collision handling, rejection of raw/foreign/stale references, valid prompt name/arguments, invalid mixed shapes before connection, pagination termination/repetition/page/entry bounds, resource templates in listing metadata, URI query/userinfo non-disclosure, blob spill cleanup, untrusted prompt labeling/message ordering, output truncation/spill, cancellation, stale generation, and no absolute spill paths in results.

- [ ] **Step 2: Run MCP resource/prompt tests red**

```bash
PYTHONPATH=packages/travis234-mcp-adapter:. \
  /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  packages/travis234-mcp-adapter/tests/test_proxy_resources.py \
  packages/travis234-mcp-adapter/tests/test_proxy_prompts.py \
  packages/travis234-mcp-adapter/tests/test_proxy_tool.py -q
```

- [ ] **Step 3: Add bounded protocol adapters without generated tools**

Extend `ConnectedServer` with list/read resource and list/get prompt calls, maintaining the runtime's timeout/cancellation boundary. Normalize catalog summaries without embedded schemas/content; only the explicit read/get operation returns content. Resolve opaque resource references only within the current server/generation catalog. Use existing spill ownership and cleanup on session reload/shutdown.

- [ ] **Step 4: Verify all transports and commit**

```bash
PYTHONPATH=packages/travis234-mcp-adapter:. \
  /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  packages/travis234-mcp-adapter/tests -q
git add packages/travis234-mcp-adapter/travis234_mcp_adapter/proxy_tool.py \
  packages/travis234-mcp-adapter/travis234_mcp_adapter/runtime.py \
  packages/travis234-mcp-adapter/travis234_mcp_adapter/results.py \
  packages/travis234-mcp-adapter/travis234_mcp_adapter/output_guard.py \
  packages/travis234-mcp-adapter/tests/fixtures/server.py \
  packages/travis234-mcp-adapter/tests/test_proxy_resources.py \
  packages/travis234-mcp-adapter/tests/test_proxy_prompts.py \
  packages/travis234-mcp-adapter/tests/test_proxy_tool.py
git commit -m "feat(mcp): add bounded resources and prompts"
```

### Task 5: Add bounded connection recovery and richer status without replay

**Files:**
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/config.py`
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/runtime.py`
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/extension.py`
- Modify: `packages/travis234-mcp-adapter/travis234_mcp_adapter/proxy_tool.py`
- Create: `packages/travis234-mcp-adapter/tests/test_reconnect.py`
- Modify: `packages/travis234-mcp-adapter/tests/test_config.py`
- Modify: `packages/travis234-mcp-adapter/tests/test_extension.py`
- Modify: `packages/travis234-mcp-adapter/README.md`

**Interfaces:**
- Add strict per-server `reconnect` object: `automatic` default false, `maxAttempts` 1..3, and `baseDelayMs` 100..500 capped to delays no greater than 500/1000/2000 ms.
- Add explicit proxy operation `reconnect`.
- Add runtime states `disconnected`, `connecting`, `connected`, `reconnecting`, `failed`, and `closing`, with bounded last-error type and timestamps.
- Automatic recovery may establish a fresh connection after transport loss, but never repeats the failed request; the caller receives the original shaped failure and must issue a new explicit call.

- [ ] **Step 1: Write failing recovery/state tests**

Cover strict config, default disabled, explicit success, 1/2/3 failures, backoff timing via fake clock, cancellation during sleep/connect, concurrent reconnect coalescing, connect-vs-close, reload generation, credential re-resolution without logging values, stdio child cleanup, HTTP cleanup, status without connection, and a side-effecting fixture call proving invocation count remains one after transport failure.

- [ ] **Step 2: Run reconnect tests red**

```bash
PYTHONPATH=packages/travis234-mcp-adapter:. \
  /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  packages/travis234-mcp-adapter/tests/test_reconnect.py \
  packages/travis234-mcp-adapter/tests/test_config.py \
  packages/travis234-mcp-adapter/tests/test_extension.py -q
```

- [ ] **Step 3: Implement connection-only retry**

Centralize attempts in runtime; proxy operations call it but never loop their protocol request. Status reads snapshots and cannot trigger connection or resolve secrets. Maintain one connection attempt per server/generation and await it from concurrent callers. Shutdown cancels pending sleeps/connects and reaps children.

- [ ] **Step 4: Verify adapter package and commit**

```bash
PYTHONPATH=packages/travis234-mcp-adapter:. \
  /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  packages/travis234-mcp-adapter/tests -q
mcp_dist=$(mktemp -d /tmp/travis234-phase5-mcp.XXXXXX)
uv build --clear --out-dir "$mcp_dist" packages/travis234-mcp-adapter
git add packages/travis234-mcp-adapter/travis234_mcp_adapter/config.py \
  packages/travis234-mcp-adapter/travis234_mcp_adapter/runtime.py \
  packages/travis234-mcp-adapter/travis234_mcp_adapter/extension.py \
  packages/travis234-mcp-adapter/travis234_mcp_adapter/proxy_tool.py \
  packages/travis234-mcp-adapter/tests/test_reconnect.py \
  packages/travis234-mcp-adapter/tests/test_config.py \
  packages/travis234-mcp-adapter/tests/test_extension.py \
  packages/travis234-mcp-adapter/README.md
git commit -m "feat(mcp): add bounded connection recovery"
```

Document that OAuth, subscriptions, sampling, roots mutation, and failed-request replay are not included.

### Task 6: Define optional browser/computer integration conformance

**Files:**
- Create: `evals/optional_tool_conformance.py`
- Create: `evals/optional_integration_fixture.py`
- Create: `tests/test_optional_tool_conformance.py`
- Modify: `evals/README.md`
- Modify: `docs/architecture/contract-parity.md`

**Interfaces:**
- Add `run_optional_tool_conformance(extension_path, expected_tools)` returning structured pass/fail checks.
- Fixture registers synthetic `browser_fixture` and `computer_fixture` tools but performs no real desktop/browser action.
- Required checks: explicit effects, trusted loading, untrusted project suppression, Phase 1D denial/approval, cancellation propagation, bounded output/artifact spill, Phase 4 sanitized effects, shutdown cleanup, and zero root dependency imports.

- [ ] **Step 1: Write failing conformance tests**

Test a compliant fixture and deliberately broken fixtures for missing effects, ignored cancellation, unbounded output, leaked secret, child-process leak, project execution before trust, and direct generic-loop/TUI imports. Assert each failure has a stable code.

- [ ] **Step 2: Run conformance tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_optional_tool_conformance.py -q
```

- [ ] **Step 3: Implement a public-contract harness, not an adapter**

Exercise the extension through normal resource loading/session composition. Do not add Playwright, accessibility, desktop permissions, browser binaries, or a production browser/computer tool. Document that optional packages and MCP servers must pass this harness before recommendation.

- [ ] **Step 4: Prove core imports stay optional and commit**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_optional_tool_conformance.py tests/architecture/test_repository_hygiene.py -q
rg -n "^(from|import) (playwright|selenium|pyautogui|Quartz|AppKit)" travis
```

Expected: no mandatory core import.

```bash
git add evals/optional_tool_conformance.py evals/optional_integration_fixture.py \
  evals/README.md docs/architecture/contract-parity.md \
  tests/test_optional_tool_conformance.py
git commit -m "test: define optional tool conformance gate"
```

### Task 7: Add a reproducible native-acceleration benchmark gate

**Files:**
- Create: `benchmarks/contract_parity_hotpaths.py`
- Create: `tests/test_contract_parity_benchmark.py`
- Create: `docs/verification/native-acceleration-gate.md`
- Modify: `docs/architecture/contract-parity.md`
- Modify: `scripts/verify_acceptance.py`
- Test: `tests/architecture/test_acceptance_matrix.py`

**Interfaces:**
- Benchmark artifact hashing/verification, policy decisions, LSP frame parsing, supervisor snapshot projection, SQLite journal writes, memory recall, and MCP result conversion using deterministic seeded inputs.
- CLI supports `--json`, `--rounds`, `--warmups`, and optional candidate timing JSON; it writes no repository file by default.
- Add pure `decide_native_gate(baseline, candidate)` returning `retain_python`, `candidate_rejected`, or `candidate_requires_packaging_review`.
- A candidate advances only at >=2.0x median speedup, >=5% end-to-end wall share, coefficient of variation <=0.15, and no correctness/conformance regression.

- [ ] **Step 1: Write failing decision/math/CLI tests**

Cover threshold boundaries, missing candidate, invalid/negative/NaN samples, high variance, low wall share, deterministic JSON schema, warmup exclusion, seeded repeatability tolerance, and no imports of optional native modules during baseline.

- [ ] **Step 2: Run benchmark tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_contract_parity_benchmark.py -q
```

- [ ] **Step 3: Implement representative bounded workloads**

Keep correctness assertions inside each timed case and report timing outside the timed region. Use temporary directories for SQLite/artifacts. The end-to-end denominator is a deterministic mixed contract workflow, not an invented production percentage. A missing candidate must always decide `retain_python`.

- [ ] **Step 4: Run and record the baseline decision**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python \
  benchmarks/contract_parity_hotpaths.py --rounds 7 --warmups 2 --json
```

Record platform/Python/commit, median/CV/wall shares, and the decision in `docs/verification/native-acceleration-gate.md`; do not record hostnames, usernames, paths, or environment. With no approved candidate, the expected decision is `retain_python` and no dependency change.

- [ ] **Step 5: Verify acceptance and commit**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_contract_parity_benchmark.py tests/architecture/test_acceptance_matrix.py -q
git add benchmarks/contract_parity_hotpaths.py tests/test_contract_parity_benchmark.py \
  docs/verification/native-acceleration-gate.md \
  docs/architecture/contract-parity.md \
  scripts/verify_acceptance.py tests/architecture/test_acceptance_matrix.py
git commit -m "perf: add native acceleration evidence gate"
```

### Task 8: Final repository, installed-wheel, and deferred container qualification

**Files:**
- Modify: `evals/container_qualification.py`
- Create: `evals/mcp_container_smoke.py`
- Create: `packages/travis234-mcp-adapter/Dockerfile.smoke`
- Modify: `tests/test_release_workflow.py`

**Interfaces:**
- Consumes: complete Phase 5 branch and all verified predecessor contracts.
- Produces: final repository/build/TUI/container evidence; does not merge, push, publish, tag, or version.

- [ ] **Step 1: Add failing credential-free qualification contracts**

Extend `tests/test_release_workflow.py` before changing either evaluator. Require the root qualification result to prove: artifacts across app restart, policy audit/enforce denial, one fixture LSP server and clean shutdown, typed supervision/cancel, observe-only dead-owner uncertainty with no replay, memory disabled/enabled project isolation, and absent credential environment. Require the MCP smoke command builder to use a derived test-only image, one proxy fixture, a read resource, a prompt, a failed side-effecting call with invocation count one, reconnect, and child/spill cleanup. Mock Docker at this step; do not build an image yet.

- [ ] **Step 2: Run the release-workflow test red, then implement the evaluators**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_release_workflow.py -q
```

Expected: the new qualification fields and optional MCP smoke entry point are missing.

Implement the root checks with faux providers and local fixtures only. `Dockerfile.smoke` derives from a caller-supplied root image and copies the exact adapter wheel through a named BuildKit context; it does not alter `Dockerfile.release` or make MCP a root-image dependency. `mcp_container_smoke.py` must shape errors, use bounded timeouts, and reap every container/fixture on failure.

Re-run the same `tests/test_release_workflow.py` command and require zero failures before continuing.

- [ ] **Step 3: Run all Phase 5 focused suites**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_memory_settings.py tests/test_memory_store.py tests/test_memory_tool.py \
  tests/test_memory_safety.py tests/tui/test_interactive_memory.py \
  tests/test_optional_tool_conformance.py tests/test_contract_parity_benchmark.py -q
PYTHONPATH=packages/travis234-mcp-adapter:. \
  /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  packages/travis234-mcp-adapter/tests -q
```

- [ ] **Step 4: Run complete repository and package qualification**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest tests -q
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
final_root_dist=$(mktemp -d /tmp/travis234-final-root.XXXXXX)
final_mcp_dist=$(mktemp -d /tmp/travis234-final-mcp.XXXXXX)
uv build --clear --out-dir "$final_root_dist" .
uv build --clear --out-dir "$final_mcp_dist" packages/travis234-mcp-adapter
/Users/htooayelwin/orca/travis234/.venv/bin/python scripts/verify_acceptance.py --parity-json
```

Audit both archives for expected modules and absence of `.env`, worktrees, research clones, credentials, raw operation databases, memory databases, benchmark temp data, and planning documents.

- [ ] **Step 5: Run installed-wheel native-TUI end-to-end scenarios**

Install the exact root and adapter wheels into an isolated Python 3.13 environment. Use only the documented `--dotenv` boundary and never print values. Across a deterministic sequence of native-TUI PTY runs, prove:

1. memory disabled creates no DB/tool; enable it globally, explicitly retain/recall/delete a benign fact, reject a seeded credential, and show untrusted labeling;
2. the single MCP proxy lists/reads fixture resources, lists/gets a prompt, survives bounded reconnect without replaying a side-effecting call, and cleans all processes/spills;
3. a synthetic optional integration is denied then approved by policy, cancelled cleanly, and appears sanitized in operations;
4. prior artifact resume/fork, LSP preview/apply, typed worker/reviewer supervision, and uncertain-operation inspection still work;
5. every exit leaves no language server, subagent, MCP fixture, process, approval future, SQLite lock, or TUI thread.

- [ ] **Step 6: Run final static/privacy/scope audits**

```bash
git diff --check
git diff --exit-code ec53c69...HEAD -- \
  travis/agent/agent_loop.py travis/ai/providers
rg -n "mcp__|ghost|Ghost OS" travis packages/travis234-mcp-adapter docs README.md
git status --short --branch
```

Interpret existing retirement-history mentions separately; no Ghost code/config/package may exist. Search seeded secrets across JSONL, artifact objects/manifests, operations SQLite, memory SQLite, MCP spill files, logs, and acceptance output; all must be absent.

- [ ] **Step 7: Only now run the deferred final container gate**

```bash
docker build --no-cache -f Dockerfile.release -t travis234:contract-parity .
/Users/htooayelwin/orca/travis234/.venv/bin/python evals/container_smoke.py \
  --image travis234:contract-parity
docker build --no-cache \
  --build-arg BASE_IMAGE=travis234:contract-parity \
  --build-context adapter_dist="$final_mcp_dist" \
  -f packages/travis234-mcp-adapter/Dockerfile.smoke \
  -t travis234-mcp-adapter:contract-parity .
/Users/htooayelwin/orca/travis234/.venv/bin/python evals/mcp_container_smoke.py \
  --image travis234-mcp-adapter:contract-parity
```

The derived MCP smoke image may resolve public adapter dependencies during its test-only build, but no host credential, dotenv file, package token, or secret build argument is forwarded. It is never tagged as a release image or published. The root release image remains adapter-free and proves core behavior independently.

- [ ] **Step 8: Re-run verification after any container-only fix, record evidence, and stop**

If a container failure requires a fix, first add a failing regression, then run its focused tests plus the full Python suite again and rebuild/re-smoke both affected images. Commit the qualification slice only after it is green:

```bash
git add evals/container_qualification.py evals/mcp_container_smoke.py \
  packages/travis234-mcp-adapter/Dockerfile.smoke tests/test_release_workflow.py
git commit -m "test: qualify contract parity containers"
```

Record command outputs, artifact hashes, image IDs, and benchmark decision in the execution handoff. Do not merge, push, publish, tag, change permissions, bump versions, or promote an image; wait for a separate integration/release instruction.
