# Travis234 Bounded LSP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Repository guidance prohibits subagents unless the user explicitly requests them, so implementation and review are inline by default.

**Goal:** Add useful language intelligence through one bounded `lsp` tool with user-managed servers, reviewed workspace edits, deterministic shutdown, and no automatic installation or unreviewed mutation.

**Architecture:** A focused `language_services` package owns strict configuration, framed stdio JSON-RPC, document versions, server lifecycle, normalized results, and expiring preview tokens. `LanguageServiceManager` is composed into `AgentSession`; a single tool dispatches all supported actions. Read actions return bounded data, while rename and code-action edits must be previewed and then applied through the existing per-file mutation queues with explicit rollback reporting.

**Tech Stack:** Python 3.13 asyncio/subprocess, LSP 3.17-compatible JSON-RPC framing, existing settings/trust and process cleanup, Phase 1C artifacts, Phase 1D policy effects, pytest fixture servers, native TUI, npm launcher tests, and wheel/sdist builds.

## Global Constraints

- Start from the verified Phase 1D commit on branch `codex/bounded-lsp`.
- Do not auto-download, auto-install, or infer a language server executable. Users configure commands explicitly.
- Ignore project language-server settings until project trust is granted.
- Register no tool when the effective `languageServers` list is empty.
- Respect existing `--no-tools`, explicit `--tools`, and `--exclude-tools` filtering; configuration never re-enables an excluded `lsp` tool.
- Expose exactly one `lsp` tool; do not generate one tool per server or LSP method.
- Conservatively declare all four Phase 1D effects because one configured server can execute, read/write workspace data, and access a network; document the approval tradeoff.
- Keep zero-based LSP line/character coordinates at the tool boundary and document them.
- Define tool `character` values as UTF-16 code units; negotiate server position encoding and normalize UTF-8/UTF-16/UTF-32 server positions back to that stable boundary.
- Bound active servers to 3, startup to 10 seconds, requests to 20 seconds, restarts to 2 per 60 seconds, raw frames to 2 MiB, normalized inline output to 256 KiB, and one reviewed apply to 64 MiB of original bytes.
- Launch servers with an explicit minimal environment; never forward provider/dotenv credentials by inheriting the full Travis234 process environment.
- Mutations require a preview token, exact document hashes, workspace containment, and Phase 1D authorization. Never claim cross-file crash atomicity.
- Reject resource operations (`create`, `rename`, `delete`) in `WorkspaceEdit` for this phase.
- Preserve generic-loop scheduling and route oversized normalized results through Phase 1C artifact promotion.
- Do not build or smoke a container in this phase.

---

### Task 1: Define strict server configuration and normalized contracts

**Files:**
- Create: `travis/coding_agent/language_services/__init__.py`
- Create: `travis/coding_agent/language_services/types.py`
- Create: `travis/coding_agent/language_services/config.py`
- Modify: `travis/coding_agent/settings_manager.py`
- Test: `tests/test_language_service_config.py`

**Interfaces:**
- Add `LanguageServerConfig(name, command, args, languages, extensions, root_markers, initialization_options)`, where `languages` is a non-empty tuple of language IDs and `extensions` maps normalized suffixes to one of those IDs.
- Add immutable `DocumentPosition`, `DocumentLocation`, `NormalizedDiagnostic`, `NormalizedSymbol`, `NormalizedWorkspaceEdit`, and `LanguageServiceLimits`.
- Add `SettingsManager.get_language_server_configs()` with global plus trusted-project merge by server name.
- Reject shell strings; `command` is one executable and `args` is a string list passed without a shell.

- [ ] **Step 1: Write failing parsing and trust tests**

```python
def test_untrusted_project_server_command_is_ignored(tmp_path):
    settings = settings_with_project_server(tmp_path, trusted=False)
    assert settings.get_language_server_configs() == []

def test_config_rejects_shell_command_string():
    with pytest.raises(SettingsValidationError):
        parse_language_servers([{"name": "py", "command": "pyright --stdio"}])
```

Cover duplicate names, missing command, non-string args, absolute and bare executables, suffix-to-language validation, extension normalization, initialization option JSON types, sensitive initialization-option keys, unknown keys, and the exact default limits.

- [ ] **Step 2: Run the configuration tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_language_service_config.py -q
```

- [ ] **Step 3: Implement strict parsing and trusted overlay semantics**

Global definitions load first; a trusted project definition with the same name replaces it atomically. An invalid entry is reported through settings diagnostics and is not partially accepted. No executable probing occurs while parsing. When multiple servers match a suffix, choose the server with the nearest discovered root, then configuration order, then name; cover the tie-break in tests.

- [ ] **Step 4: Verify and commit the contracts**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_language_service_config.py tests/test_model_role_settings.py -q
wc -l travis/coding_agent/language_services/types.py \
  travis/coding_agent/language_services/config.py
git add travis/coding_agent/language_services travis/coding_agent/settings_manager.py \
  tests/test_language_service_config.py
git commit -m "feat(lsp): define bounded server configuration"
```

### Task 2: Implement framed stdio JSON-RPC with cancellation and bounds

**Files:**
- Create: `travis/coding_agent/language_services/jsonrpc.py`
- Create: `tests/fixtures/lsp_fixture_server.py`
- Test: `tests/test_language_service_jsonrpc.py`

**Interfaces:**
- Add `JsonRpcStdioClient.start()`, `request(method, params, signal)`, `notify(...)`, and `close()`.
- Parse case-insensitive `Content-Length` headers across arbitrary chunk boundaries.
- Correlate integer request IDs and send `$/cancelRequest` when a signal cancels an outstanding request.
- Shape protocol, timeout, EOF, oversized-frame, stderr-tail, and server-error failures without leaking environment values.

- [ ] **Step 1: Build a deterministic fixture protocol server and failing tests**

Fixture modes must split headers/body one byte at a time, combine frames, send notifications between responses, delay responses, emit malformed headers, emit a frame over 2 MiB, exit early, and record received cancellations. Do not depend on Pyright or a network install.

- [ ] **Step 2: Run the transport tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_language_service_jsonrpc.py -q
```

- [ ] **Step 3: Implement one reader task and bounded pending-request map**

Use `asyncio.create_subprocess_exec`, never `shell=True`. Pass an explicit allowlist containing only required process keys such as `PATH`, locale, home/temp, and configured non-sensitive runtime markers; strip names matching provider credentials, token/key/secret/password/auth/cookie/credential patterns and test with seeded dotenv values. Cap captured stderr to a sanitized tail. On close, cancel pending futures, request graceful shutdown/exit when initialized, then terminate and finally kill within existing bounded shutdown windows. Await all reader/wait tasks.

- [ ] **Step 4: Verify race and leak cases**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_language_service_jsonrpc.py -q -k 'fragment or cancel or timeout or close or oversized or eof'
```

Assert no task-destroyed warnings and no fixture process remains after every test.

- [ ] **Step 5: Commit the transport**

```bash
git add travis/coding_agent/language_services/jsonrpc.py \
  tests/fixtures/lsp_fixture_server.py tests/test_language_service_jsonrpc.py
git commit -m "feat(lsp): add bounded stdio json-rpc transport"
```

### Task 3: Manage roots, document versions, and server restart budgets

**Files:**
- Create: `travis/coding_agent/language_services/documents.py`
- Create: `travis/coding_agent/language_services/manager.py`
- Test: `tests/test_language_service_documents.py`
- Test: `tests/test_language_service_manager.py`

**Interfaces:**
- Add `DocumentTracker.open_or_update(path)`, `mark_saved(path)`, `close(path)`, and `snapshot(path)` returning version plus SHA-256.
- Add `LanguageServiceManager.for_path(path)`, `request(path, method, params, signal)`, `status()`, and `close()`.
- Choose the nearest matching root marker inside the trusted workspace; fall back to the workspace root.
- Use least-recently-used idle eviction at the 3-server cap and enforce the restart budget per configuration/root key.

- [ ] **Step 1: Write failing root/version/lifecycle tests**

Cover UTF-8 URIs, astral/combining characters across negotiated UTF-8/UTF-16/UTF-32 positions, symlink escape rejection, version increments only on changed content, didOpen/didChange/didSave/didClose sequencing, concurrent first requests starting one server, LRU eviction, trusted reload generation, trust revocation closing project servers, crash restart, restart exhaustion, startup timeout, and close during startup.

- [ ] **Step 2: Run the manager slice red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_language_service_documents.py tests/test_language_service_manager.py -q
```

- [ ] **Step 3: Implement lifecycle without duplicating process ownership**

The manager owns only language-server subprocesses. It must not register them as user-managed `process` tool jobs. Initialize with workspace folders and client capabilities limited to implemented methods. Track server generation so a response from a dead generation cannot satisfy a new request.

- [ ] **Step 4: Verify bounded concurrency and shutdown**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_language_service_documents.py tests/test_language_service_manager.py -q
wc -l travis/coding_agent/language_services/documents.py \
  travis/coding_agent/language_services/manager.py
```

- [ ] **Step 5: Commit manager behavior**

```bash
git add travis/coding_agent/language_services/documents.py \
  travis/coding_agent/language_services/manager.py \
  tests/test_language_service_documents.py tests/test_language_service_manager.py
git commit -m "feat(lsp): manage documents and server lifecycles"
```

### Task 4: Normalize bounded read-only language actions

**Files:**
- Create: `travis/coding_agent/language_services/tool.py`
- Modify: `travis/coding_agent/agent_session.py`
- Test: `tests/test_language_service_tool_reads.py`

**Interfaces:**
- Register one `lsp` tool with actions `status`, `diagnostics`, `symbols`, `hover`, `definition`, `references`, and `code_actions` in this task.
- Required common field is `action`. `diagnostics` needs `path`; `symbols` uses `path` for document symbols or `query` for workspace symbols; hover/definition/references need `path`, `line`, and `character`; `code_actions` needs `path` plus an explicit start/end range.
- Declare `effects={read, write, execute, network}` from first registration because the one schema includes later mutation actions and a user-managed server's behavior is not locally provable.
- Normalize server results to workspace-relative paths and bounded stable JSON; never expose server command/environment or absolute object-store paths.

- [ ] **Step 1: Add a failing exact schema and action matrix**

Assert there is one tool, its Phase 1D effect set is exact, CLI/session filtering can exclude it without server startup, invalid actions/coordinates fail before server start, locations outside the workspace are omitted with a count, results are deterministically sorted, and a result over 256 KiB becomes a Phase 1C artifact reference with a bounded preview.

- [ ] **Step 2: Run read-action tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_language_service_tool_reads.py -q
```

- [ ] **Step 3: Implement normalization and conditional composition**

Construct the tool only when at least one valid server is configured. Use byte-based output accounting. Diagnostics can consume cached publish notifications, but return their server generation and document hash so staleness is visible.

- [ ] **Step 4: Verify artifact and policy integration**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_language_service_tool_reads.py tests/test_tool_policy_integration.py \
  tests/test_durable_artifact_store.py -q
```

- [ ] **Step 5: Commit read actions**

```bash
git add travis/coding_agent/language_services/tool.py \
  travis/coding_agent/agent_session.py tests/test_language_service_tool_reads.py
git commit -m "feat(lsp): expose bounded semantic read actions"
```

### Task 5: Normalize workspace edits into expiring preview tokens

**Files:**
- Create: `travis/coding_agent/language_services/workspace_edit.py`
- Modify: `travis/coding_agent/language_services/tool.py`
- Test: `tests/test_language_service_workspace_edit.py`

**Interfaces:**
- Add `WorkspaceEditPreviewStore.create(edit, document_snapshots)` and `consume(token)`.
- Token form is `lsp-preview-` plus 32 lowercase hex characters.
- Action and preview tokens expire after 10 minutes and are each capped at 32 per session with oldest-first eviction; preview tokens are single-use on successful apply.
- Add `rename_preview(path, line, character, newName)` and `code_action_preview(actionToken)`. `code_actions` returns opaque `lsp-action-<32hex>` tokens; preview resolves only the selected action and rejects command-only or edit-plus-command actions.

- [ ] **Step 1: Write failing normalization/security tests**

Cover `changes` and `documentChanges`, UTF-16 ranges over astral text, CRLF preservation, overlapping edits, invalid ranges, duplicate URIs, edit ordering, same-position inserts, outside-workspace paths, symlink escapes, unsupported create/delete/rename operations, command-only/edit-plus-command code actions, action-token server/generation binding, version mismatch, token entropy/TTL/cap, trust/config generation invalidation, and bounded human-readable diff output.

- [ ] **Step 2: Run workspace-edit tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_language_service_workspace_edit.py -q
```

- [ ] **Step 3: Implement canonical previews without touching files**

Sort files by normalized relative path and apply each file's edits from the end of the document toward the start. Record original content hash, target hash, server generation, and a normalized edit payload in memory only. If preview display exceeds 256 KiB, promote the display to an artifact; keep the apply payload in the capped preview store.

- [ ] **Step 4: Verify preview purity**

Snapshot the workspace before and after every preview test and assert byte-for-byte equality. Verify no preview token enters session JSONL unless returned as ordinary tool-result text.

- [ ] **Step 5: Commit previews**

```bash
git add travis/coding_agent/language_services/workspace_edit.py \
  travis/coding_agent/language_services/tool.py \
  tests/test_language_service_workspace_edit.py
git commit -m "feat(lsp): add reviewed workspace edit previews"
```

### Task 6: Apply reviewed edits with deterministic locking and rollback reports

**Files:**
- Modify: `travis/coding_agent/tools/file_mutation_queue.py`
- Modify: `travis/coding_agent/language_services/workspace_edit.py`
- Modify: `travis/coding_agent/language_services/tool.py`
- Test: `tests/test_language_service_apply.py`
- Test: `tests/test_coding_tools_and_subagents.py`

**Interfaces:**
- Add `with_file_mutation_queues(paths, fn)` acquiring canonical path locks in sorted order.
- Add tool action `apply` with only `previewToken`.
- Return explicit `changed`, `restored`, and `unresolved` relative-path arrays plus `applied` boolean.
- Applying consumes the preview only after hashes match and mutation begins; failed preconditions leave it available until expiry.

- [ ] **Step 1: Write failing apply/race/fault tests**

Test exact success, stale source hash, expired/unknown/replayed token, edit/write queue serialization, reversed path inputs without deadlock, existing mode preservation, rejection of missing/non-regular files, permission failure before writes, injected failure after each file, successful rollback, rollback failure, symlink replacement after preview, cancellation before lock, and cancellation during writes.

- [ ] **Step 2: Run apply tests red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_language_service_apply.py \
  tests/test_coding_tools_and_subagents.py -q -k 'mutation_queue or language_service'
```

- [ ] **Step 3: Implement best-effort transactional apply**

Acquire all canonical locks, re-resolve containment, compare all hashes, stage original bytes in memory subject to a 64 MiB total apply limit, then write deterministic siblings with preserved file modes and replace existing regular files. On a later failure, restore prior bytes/modes in reverse write order. Report unresolved restoration honestly. Do not use the words atomic or transaction in user-facing success claims.

- [ ] **Step 4: Verify Phase 1D enforcement and cancellation**

The whole `lsp` tool remains conservatively `{read, write, execute, network}` after mutation actions are added. In enforce mode a denied request must not contact the server or consume the token. Approval cancellation must release all queues and preserve originals.

- [ ] **Step 5: Commit reviewed apply**

```bash
git add travis/coding_agent/tools/file_mutation_queue.py \
  travis/coding_agent/language_services/workspace_edit.py \
  travis/coding_agent/language_services/tool.py \
  tests/test_language_service_apply.py tests/test_coding_tools_and_subagents.py
git commit -m "feat(lsp): apply reviewed edits with rollback reporting"
```

### Task 7: Bind shutdown, TUI status, documentation, and acceptance

**Files:**
- Modify: `travis/coding_agent/agent_session_runtime.py`
- Modify: `travis/app.py`
- Create: `travis/tui/interactive_lsp.py`
- Modify: `travis/tui/interactive_mode.py`
- Modify: `travis/tui/interactive_command_dispatcher.py`
- Modify: `travis/tui/interactive_shutdown.py`
- Modify: `travis/tui/user_commands.py`
- Modify: `docs/architecture/contract-parity.md`
- Modify: `docs/settings.md`
- Modify: `README.md`
- Modify: `scripts/verify_acceptance.py`
- Test: `tests/test_language_service_shutdown.py`
- Test: `tests/tui/test_interactive_lsp.py`
- Test: `tests/architecture/test_facade_boundaries.py`
- Test: `tests/architecture/test_acceptance_matrix.py`

**Interfaces:**
- `AgentSession.close()` closes all language services before process-owner teardown completes.
- Add read-only `/lsp status`; it does not start a server.
- Acceptance reports configured/active counts and limits, never commands or initialization options.

- [ ] **Step 1: Add failing shutdown, owner, and command tests**

Assert normal exit, Ctrl-C, session switch, fork, constructor failure, and application close leave no server. Assert TUI updates happen through the dispatcher and language-service modules import neither TUI nor façades.

- [ ] **Step 2: Run the lifecycle slice red**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_language_service_shutdown.py tests/tui/test_interactive_lsp.py \
  tests/architecture/test_facade_boundaries.py -q
```

- [ ] **Step 3: Wire close ordering and document the threat model**

Document trust, executable ownership, bounds, zero-based coordinates, preview/apply semantics, rollback limitations, policy effects, artifacts, and troubleshooting. `/lsp status` must show configured/running/restart-exhausted state without absolute commands.

- [ ] **Step 4: Verify architecture and commit**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_language_service_shutdown.py tests/tui/test_interactive_lsp.py \
  tests/architecture/test_facade_boundaries.py tests/architecture/test_acceptance_matrix.py -q
git add travis/coding_agent/agent_session_runtime.py travis/app.py \
  travis/tui/interactive_lsp.py travis/tui/interactive_mode.py \
  travis/tui/interactive_command_dispatcher.py \
  travis/tui/interactive_shutdown.py travis/tui/user_commands.py \
  docs/architecture/contract-parity.md docs/settings.md README.md \
  scripts/verify_acceptance.py tests/test_language_service_shutdown.py \
  tests/tui/test_interactive_lsp.py tests/architecture/test_facade_boundaries.py \
  tests/architecture/test_acceptance_matrix.py
git commit -m "docs: integrate bounded language services"
```

### Task 8: Phase 2 repository and installed-wheel qualification

**Files:**
- No source changes expected.

**Interfaces:**
- Consumes: complete Phase 2 branch.
- Produces: fresh non-container evidence and the exact base commit for Phase 3.

- [ ] **Step 1: Run the full language-service and invariant slices**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest \
  tests/test_language_service_config.py tests/test_language_service_jsonrpc.py \
  tests/test_language_service_documents.py tests/test_language_service_manager.py \
  tests/test_language_service_tool_reads.py tests/test_language_service_workspace_edit.py \
  tests/test_language_service_apply.py tests/test_language_service_shutdown.py \
  tests/test_agent_loop.py tests/test_agent_loop_compatibility.py -q
```

- [ ] **Step 2: Run complete non-container qualification**

```bash
PYTHONPATH=. /Users/htooayelwin/orca/travis234/.venv/bin/python -m pytest tests -q
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
phase2_dist=$(mktemp -d /tmp/travis234-phase2.XXXXXX)
uv build --clear --out-dir "$phase2_dist" .
/Users/htooayelwin/orca/travis234/.venv/bin/python scripts/verify_acceptance.py --parity-json
```

- [ ] **Step 3: Run installed-wheel native-TUI scenarios**

Install the exact wheel in an isolated Python 3.13 environment and use only the documented `--dotenv` boundary. Configure the fixture server globally, then prove hover, definition, diagnostics, oversized artifact reading, rename preview with no mutation, approved apply, stale-token rejection, server crash/restart status, Ctrl-C cancellation, and clean exit with no remaining fixture process. Do not print dotenv values.

- [ ] **Step 4: Audit scope and record the phase gate**

```bash
git diff --check
git diff --exit-code codex/uniform-tool-policy...HEAD -- \
  travis/agent/agent_loop.py travis/ai/providers \
  packages/travis234-cli packages/travis234-mcp-adapter
git status --short --branch
```

Do not run a container build or smoke. Record exact evidence and use the verified `HEAD` as the only Phase 3 base.
