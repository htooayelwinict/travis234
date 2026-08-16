# Combined parity and orchestration verification

Date: 2026-08-16

Branch: `codex/combined-parity-orchestration`

This record qualifies the local integration of the Phase 1–5 parity stack,
typed in-process subagents, and the independent tmux/worktree orchestration
skill. It does not authorize or record publication, remote Git changes, or a
container build.

## Integration evidence

- The branch contains orchestration history through `34d1aea` and Phase 1–5
  history through `0856f10` via merge commit `1e9a060`.
- The Python and npm copies of `subagent-delegation/SKILL.md` are byte-identical.
- Both subagent skill copies pass the official `quick_validate.py` validator
  and contain 494 words.
- Generic MCP remains optional and separately packaged. No Ghost component was
  restored.

## Automated qualification

- Final repository Python suite, with unhandled thread exceptions promoted to
  errors: **2544 passed in 237.07 seconds**.
- npm launcher suite: **24 passed**; `npm run pack:dry-run` passed.
- Generic MCP adapter suite: **125 passed in 12.86 seconds**.
- Final focused LSP, memory, terminal-input, and editor group:
  **323 passed in 13.84 seconds**.
- Combined resource/orchestration contract group after the final skill edit:
  **89 passed in 7.75 seconds**.
- Earlier combined focused TUI group: **48 passed in 44.31 seconds**, including
  the deterministic 21-scenario orchestration coverage.
- Twine accepted both root Python artifacts and both adapter Python artifacts.
- A clean Python 3.13 environment installed the exact root and adapter wheels,
  imported both packages as versions 2.4.6 and 0.1.3, and rendered
  `travis234 --help`.
- A clean npm prefix installed the exact npm tarball and rendered the packaged
  launcher help without starting Docker.

The final artifacts were built from clean source commit
`a90810894a151b19d60c3e0d549b5be95914e5d3` into
`/tmp/travis234-combined-final.w9Yk6r`:

| Artifact | SHA-256 |
| --- | --- |
| `travis234-2.4.6-py3-none-any.whl` | `25fec2580fde3fc9208734b8057116d4944f3ab392f35e269c2e3f2ec190da26` |
| `travis234-2.4.6.tar.gz` | `84c4a1cff8ebcc8840e8bcb2cf07c2d9db0c50fc4ac03a10fc397c45fcbe7480` |
| `travis234_mcp_adapter-0.1.3-py3-none-any.whl` | `b6da816331fd7aa102826ce9419e3574b853ad814da7e0ac3c23f5e2e0fc0d71` |
| `travis234_mcp_adapter-0.1.3.tar.gz` | `9361623c8d25d41b6b13a860ee86a7764e6ee996b10ff10a09c3fe9a051ac102` |
| `htooayelwinict-travis234-2.4.6.tgz` | `36048aa0080d49d56ee8262bc79c4071f6446f0b8afe6b99baf710eb5e8176b6` |

The installed Python wheel and npm tarball contain the same subagent skill
payload, SHA-256
`b3c609d543838f897ea4fb8462c677035e6407d6da13ecc6ea04a376d70acc04`.
Their orchestration skill payloads also match, SHA-256
`df996cb58844c540cc8f7f75d26c28695180187008849be0d840891a66444a5b`.

## Live native-TUI acceptance

A wheel containing the live-qualification corrections was installed into a
fresh Python 3.13 environment and started through its real `travis234` console
entry point in an attached PTY. It loaded the ignored repository `.env` only
through `--dotenv`; no credential value was printed or copied. The active model
for every model-backed prompt was `openrouter/minimax/minimax-m3` with medium
thinking. Durable evidence is under `/tmp/t234-full-bg-tui.GxrfC6/evidence`.

All 21 scenarios reached their acceptance condition:

| # | Prompt scenario | Result | Observable acceptance evidence |
| --- | --- | --- | --- |
| 1 | Capability trust, reload, and projection | **PASS** | The reloaded `/cap-probe after-reload` command returned `CAPABILITY-REGISTRY-PASS after-reload`. |
| 2 | Typed role/model/schema/artifact/result expansion | **PASS** | `evidence-reviewer` task `subagent-6a2dbab6c053` returned structured evidence and expanded artifact `artifact-5e6925a63cb84e01bbcbf977c05978e4`. |
| 3 | Asynchronous typed child | **PASS** | The parent returned while supervised task `subagent-0a327afcc58d` remained active. |
| 4 | `/agents status` and inspect | **PASS** | The active supervised worker appeared in the roster and its bounded inspection exposed its role and state. |
| 5 | Steering an active child | **PASS** | A follow-up instruction was accepted by the existing child without replacing its task identity. |
| 6 | Cancelling an active child | **PASS** | The cancellation request settled the long-running task instead of allowing its natural completion marker. |
| 7 | Large-output spill and bounded artifact read | **PASS** | Fresh artifact `artifact-71c67631acb54d6c873d12ccfb8cf993` contained `LARGE_OUTPUT_BEGIN` in bytes 0–127. |
| 8 | Artifact resume after process restart | **PASS** | A new Travis process resumed the exact session and read the pre-restart artifact without regenerating it. |
| 9 | Native policy deny | **PASS** | One denied write produced `S09-PASS-DENIED` and no target file. |
| 10 | Native policy allow-once | **PASS** | One approved write succeeded and an exact read verified `POLICY_ALLOW_ONCE_SENTINEL`. |
| 11 | Lazy LSP diagnostics and hover | **PASS** | Diagnostics returned `acceptance fixture diagnostic`; hover returned `fixture-symbol: integer`. |
| 12 | LSP rename preview without mutation | **PASS** | Preview token `lsp-preview-4aee2fc5fd6c869059164bca0d36aa7f` described exactly two edits while the file stayed unchanged. |
| 13 | Applying a reviewed LSP action | **PASS** | The preview token applied once, changed only `main.py`, and reported no restored or unresolved paths. |
| 14 | Crash uncertainty and never-replay behavior | **PASS** | The interrupted action remained explicitly uncertain and was not automatically replayed. |
| 15 | Explicit durable memory retention | **PASS** | Exact consented content was retained as `mem_65872afa2c5348e88484914a272e2e32` through the provider-compatible schema. |
| 16 | Untrusted memory recall | **PASS** | Recall returned `S16-PASS MEMORY_DATA_ONLY`; instruction-shaped stored text remained bounded as untrusted data. |
| 17 | Exact memory deletion and absence | **PASS** | Delete reported true and the following recall returned zero matches with no untrusted block. |
| 18 | Generic MCP tools and resources | **PASS** | Tool listing/call and resource listing/read succeeded; resource text remained inside untrusted MCP boundaries. |
| 19 | Generic MCP prompt data | **PASS** | Ordered prompt roles and topic were returned inside explicit untrusted boundaries and were not executed. |
| 20 | MCP reconnect and stale-reference rejection | **PASS** | Reconnect invalidated the old opaque reference; relisting produced a different usable reference. |
| 21 | tmux/worktree ping-pong, recovery, correction, and release | **PASS** | Travis A recovered the same Travis B, completed two handoff rounds, verified `ORCH_FINAL\n`, released B, and observed its tmux session disappear. |

Scenario 21 used run `run_3fa34eb8279e95d9c81a1bd4`, task
`task_053a96f4861d3fe0d9600b3a`, worker
`worker_f8c6819f8274dc0d408a4ee2`, tmux session
`travis234-orch-90b71a20ff50ad35`, and Travis session
`c4031036dd1a42ceac4958049430d8cd`. Round 1 asked a blocking
question and wrote exact bytes for `ORCH_BETA\n`. After a fresh coordinator
process performed inspect-only recovery, round 2 overwrote the file with exact
bytes `4f5243485f46494e414c0a` (`ORCH_FINAL\n`), SHA-256
`94005ce382e9c4430aa59e0a02335ff4f6dd62fa338e743ce35b03a88ae5a48d`.
Both terminal handoffs were acknowledged. No replay, integration, commit, push,
branch deletion, or worktree deletion occurred inside the protocol.

The first scenario-21 driver attempt submitted only the first line because the
PTY harness sent ordinary newline bytes, which the native TUI correctly treats
as Enter. The failed worker was protocol-cancelled and released cleanly. The
harness was corrected to emit the terminal bracketed-paste protocol around the
multiline prompt. The rerun visibly submitted all six lines as one message and
passed. Focused terminal/editor tests also proved that Travis already preserves
newlines in real paste events, so no product change was made for this
harness-only defect.

Other expected exercise-and-retry evidence is retained rather than hidden:

- Scenario 7 first exposed a mismatched fixture sentinel; the corrected fixture
  produced a new artifact and passed without a product change.
- Scenario 11 exposed a real relative-path LSP bug; its regression and fix are
  recorded below.
- Scenario 13 first used the wrong model-selected token property; the exact
  retry passed without a product change.
- Scenario 15 exposed a real provider-schema compatibility bug; its regression
  and fix are recorded below.

## Regression-first corrections found during qualification

1. The typed-role prompt renderer pushed `session_subagents.py` beyond the
   750-line collaborator boundary. The existing architecture test failed first;
   the pure renderer moved to `subagent_roles.py`, leaving the owner at 749
   lines. Focused and full suites passed.
2. Out-of-order same-digest memory retention could move `updated_at_ms`
   backward and raise inside a worker thread. A deterministic failing test was
   added first. Duplicate retention now preserves
   `max(existing.updated_at_ms, incoming_now_ms)` inside the serialized
   transaction; 46 focused memory tests and the strict full suite passed.
3. The concise subagent skill rewrite had omitted established npm safety and
   recovery wording. The npm contract failed first. Both mirrors now retain the
   legacy workspace/process/truncation safeguards plus typed roles, artifacts,
   `/agents`, and independent orchestration within the 500-word ceiling.
4. Relative LSP paths were resolved against the Travis process current
   directory before the workspace containment check. A failing regression
   changed the process current directory and selected `main.py` relative to a
   separate workspace. Relative sources now resolve against that workspace;
   absolute-path and escape checks are unchanged. The focused LSP suite and the
   live diagnostics/hover retry passed.
5. The memory provider schema used a root `oneOf` that MiniMax transformed
   incorrectly, turning the requested JSON tags array into an object before the
   tool boundary. A failing contract test now requires a bounded flat provider
   schema. Travis advertises that compatible schema while retaining the strict
   per-action `oneOf` as its private runtime validator, so malformed action
   combinations remain rejected. Focused schema/memory suites and the live
   retain/recall/delete sequence passed.

## Deferred boundary

No container image was built or smoke-tested because the user explicitly held
container work. No image was pushed, no branch was pushed, no pull request was
opened, and no PyPI, npm, or GHCR state was changed. Container qualification
remains a separate gate requiring the user's later instruction.
