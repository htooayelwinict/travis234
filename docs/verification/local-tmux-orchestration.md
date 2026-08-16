# Local Tmux Orchestration Verification

**Date:** 2026-08-16 (Asia/Yangon)

This record qualifies the built-in Travis234 `orchestration` skill and its
local tmux/RPC helper. It begins with a no-skill control and will be extended
after each implementation phase. It does not authorize or record publication.

## Secret and state boundary

- The ignored repository `.env` is supplied only through Travis234's explicit
  dotenv boundary. Its contents are never copied into this record.
- Every live scenario uses an isolated `TRAVIS234_CODING_AGENT_DIR`, disposable
  nested Git repository, owner-private evidence directory, and isolated tmux
  server.
- Evidence records lifecycle categories and bounded diagnostics, not raw
  environments, credentials, authorization headers, or tool transcripts.
- Existing `~/.travis234` state, generic MCP configuration, and unrelated
  working-tree changes remain outside scenario cleanup.

## Phase 0: no-skill RED baseline

The control uses five fresh attached TUI sessions with OpenRouter
`minimax/minimax-m3`, thinking `medium`. The `orchestration` skill is absent.
Each session receives the same natural-language request for a durable
new-worktree Travis B research round trip without subagents or integration.
The pre-feature Python and npm inventories contained only
`subagent-delegation` and `web-search`, confirming that no orchestration skill
was available to the model.

The hand-checked rubric is:

1. starts a distinct durable Travis234 worker;
2. uses tmux plus structured RPC rather than screen scraping;
3. isolates ownership in a new Git worktree;
4. returns stable Run, Task, Worker, Dispatch, tmux, and Travis session identities;
5. returns a structured evidence handoff to Travis A;
6. never injects keystrokes directly from B into A; and
7. cleans up only the exact worker after A processes the result.

| Attempt | Result | First missing required behavior | Safe observations |
|---|---|---|---|
| 1 | **FAIL** | Structured RPC | Timed out after 309.2 s and 40 tool calls. A distinct worktree and isolated tmux session existed, but no handoff or stable identity set returned. |
| 2 | **FAIL** | Structured RPC | Returned after 297.6 s and 66 tool calls. It improvised a worktree, a named tmux session, one-shot print mode, a handoff file, and an artificial tail-sleep; it did not use RPC or return Run/Task/Worker/Dispatch identities. No direct-keystroke term appeared, and its exact tmux session had stopped. |
| 3 | **FAIL** | Structured RPC | Timed out after 309.2 s and 44 tool calls. A distinct worktree and isolated tmux session existed, but no handoff or stable identity set returned. |
| 4 | **FAIL** | Structured RPC | Timed out after 309.2 s and 61 tool calls with an empty response. A distinct worktree and isolated tmux session remained, with no handoff or stable identity set. |
| 5 | **FAIL** | Structured RPC | Timed out after 309.2 s and 41 tool calls. A distinct worktree and isolated tmux session existed, but no handoff or stable identity set returned. |

The no-skill baseline passes RED when at least one attempt misses a required
behavior. A response marker alone is not evidence. The observed result was
**0/5 full passes** across 252 tool calls. The harness inspected and stopped
only each attempt's uniquely isolated tmux session after scoring; disposable
worktrees remain only below the ignored baseline fixture for auditability.

The retained evidence is not tracked. A post-run scan found no API-key or
Bearer-token shape. Some raw logs contained the configured worker provider or
model value, so those logs were not quoted or copied into this record; only
the bounded, redacted observations above were preserved.

## Implementation phase gates

| Phase | Result | Prompt scenario | Focused evidence |
|---|---|---|---|
| 1 — package and lazy discovery | **PASS** | A faux-provider TUI turn answered an unrelated prompt while exposing the `orchestration` name and description only. The heading, body, and private-relay recipe were absent; no orchestration state or worktree appeared. | 14 focused Python tests and the broader 77-test phase suite passed; all 23 npm launcher tests passed; the package-directory npm dry-run contained exactly the three orchestration resources plus existing declared files, with no dotenv, state, or transcript entry. |
| 2 — private durable state | **PASS** | A faux-provider Travis A explicitly loaded the skill and created visible Run/Task receipts, then a fresh coordinator recovered the same IDs. Receipts included supervised mode, ownership, acceptance criteria, four-round budget, and a safe next action. No Worker, worktree, tmux socket, or duplicate row appeared. | 13 helper tests passed, including permissions, SQLite pragmas, schema mismatch, strict request files, idempotency, status validation, and secret-safe single-frame failures. The combined helper/TUI suite passed 15 tests, and the Python/npm helper bytes matched. |
| 3 — worktree and worker relay | **PASS** | A faux-provider TUI request used the public helper to create a new-worktree Worker and displayed its receipt only after real isolated tmux plus fake Travis RPC readiness. It showed Run, Task, Worker, worktree, base commit, dirty-transfer status, tmux, Travis session, workspace, branch, and `ready`, while omitting launch/dotenv paths, capabilities, environments, and raw stderr. | 11 real-Git ownership tests and 9 relay/Worker tests passed; the combined helper/worktree/relay suite passed 33 tests; the exact relay/TUI selector passed 10 tests. An incompatible frame did not mutate or close the relay, transient capability text was absent from every state file, and credential-shaped stderr was stored only as byte/hash metadata. |
| 4 — supervised dispatch and terminal handoff | **PASS** | Two faux-provider TUI scenarios drove real isolated tmux/RPC Workers: one returned read-only research evidence and one returned a committed code result. Both produced accepted Dispatch receipts and durable terminal packets; the coordinator HEAD remained unchanged and no capability appeared in visible tool results. | The dispatch RED tests first failed on the absent API. The completed focused dispatch suite passed 3 tests, the combined helper/worktree/relay/dispatch suite passed 36 tests, and both phase TUI scenarios passed. Capability plaintext was injected only into the rotated Worker process, while SQLite and all orchestration files contained no plaintext value. |
| 5 — durable dialogue and bounded corrections | **PASS** | The question scenario delivered one Worker question only after RPC idle, then A acknowledged it, replied, and received/acknowledged the final handoff in the same Travis session. The correction scenario acknowledged an initial handoff, created a parent-linked immutable correction Dispatch, and received the corrected handoff from the same Worker/session. | Focused API tests passed for authenticated/idempotent questions, delivery without implicit acknowledgement, idempotent acknowledgement, same-session reply, correction parent ordering, separate Dispatch/prompt counters, and rejection of the fifth prompt before RPC. Both dialogue TUI scenarios passed; database Message order and acknowledgements matched the visible flow and coordinator Git HEAD stayed unchanged. |
| 6 — full handoff and lifecycle recovery | **PASS** | Full handoff returned all durable identities with `monitoring:false` and no wait. A coordinator restart redelivered one unacknowledged packet and stopped only after explicit acknowledgement. Exact cancellation preserved Git state and recovery reported no replay. Two live Workers were accepted and a third default attempt was rejected before worktree/tmux mutation. | Five lifecycle/recovery API tests and five selected lifecycle TUI cases passed. The matrix covered compatible reconnect, missing tmux→`lost`, live/unavailable or version-mismatched relay→`outcome_unknown`, inspect-only nonmutation, exact private stale-launch cleanup, retain/release, supervised cancel, abandonment, and stale late evidence. Receipts explicitly withheld replay, integration, push, branch deletion, and worktree deletion. |

The plan's root-level `npm pack --workspace` spelling was not applicable because
the root manifest does not declare npm workspaces. Verification therefore ran
`npm pack --dry-run --json` with `packages/travis234-cli` as the working
directory, which exercised the intended package manifest without changing the
repository's established npm layout.

## Skill-authoring qualification

- [x] RED scenarios were defined before the skill body and run without the skill.
- [x] The five baseline failures recorded missing structured RPC/stable identity
  behavior without copying secret-bearing traces.
- [x] The folder/name is lowercase `orchestration`; frontmatter contains only
  `name` and a third-person `Use when...` description below 500 characters.
- [x] Trigger terms cover Travis A/B, another independent Travis, tmux,
  worktree, ping-pong, recovery, and handoff.
- [x] The imperative body has one core ownership principle and remains below
  500 words.
- [x] Guidance converts baseline gaps into a positive coordinator recipe,
  structured identity fields, and observable safety guards.
- [x] One compact natural-language example is present and not duplicated.
- [x] Deterministic logic remains in `scripts/orchestrate.py`; detailed schemas,
  states, limits, recovery, and failure behavior live in the single protocol
  reference.
- [x] The mode decision is a compact table; no flowchart is needed.
- [x] Common mistakes cover unsafe tmux, ownership, replay, trust, secrets,
  integration, cleanup, and subagent substitution.
- [x] The bundle contains no extra README, quick reference, changelog, asset,
  or product-specific agent metadata.
- [x] Both official validators passed via an ephemeral PyYAML environment;
  instruction tests and Python/npm byte parity passed.
- [x] The pre-task system prompt and existing `subagent-delegation` skill retain
  their pinned SHA-256 values.
- [ ] Five installed-wheel MiniMax M3 repetitions are recorded in the live
  qualification section below.

No new model rationalization was observed in deterministic GREEN scenarios.
One test-only bytecode cache appeared during dynamic helper import; the exact
cache was removed and all three test loaders now suppress bytecode generation,
so distribution parity remains stable without ignoring arbitrary files.

## Final 21-prompt deterministic TUI matrix

All scenarios use natural-language user prompts, the real `CodingApp`/TUI tool
continuation path, isolated state/repositories/tmux servers, and fake provider
and RPC endpoints for deterministic behavior. Each row is one independently
reported prompt result; no failed attempt is replaced by a retry.

| # | Scenario | Result | Bounded reason |
|---:|---|---|---|
| 1 | lazy discovery | **PASS** | Unrelated turn exposed metadata only and created no orchestration state. |
| 2 | durable Run/Task restart | **PASS** | Fresh A recovered the same IDs without duplicate rows. |
| 3 | worktree Worker readiness | **PASS** | Receipt followed real tmux/RPC readiness and preserved dirty A state. |
| 4 | research handoff | **PASS** | B returned bounded evidence; A did not integrate. |
| 5 | verified code return | **PASS** | B committed in its worktree; A HEAD remained unchanged. |
| 6 | question and reply | **PASS** | One idle-delivered question, acknowledgement, same-session reply, and final packet. |
| 7 | bounded correction ping-pong | **PASS** | Acknowledged parent produced one immutable correction Dispatch. |
| 8 | full handoff | **PASS** | All identities returned with monitoring off and no wait. |
| 9 | coordinator restart | **PASS** | Unacknowledged packet redelivered, then stopped after acknowledgement. |
| 10 | failure/cancel/recovery | **PASS** | Exact cancellation preserved Git and recovery performed no replay. |
| 11 | two-Worker bound | **PASS** | Two Workers started; third was rejected before worktree/tmux mutation. |
| 12 | Worker-declared failure | **PASS** | Durable failure evidence remained failure, never synthesized success. |
| 13 | wrong capability | **PASS** | Terminal mutation was rejected without echoing the supplied value. |
| 14 | Dispatch wait timeout | **PASS** | Zero wait returned nonterminal timeout without invention. |
| 15 | Message check timeout | **PASS** | Empty mailbox remained empty and nonterminal. |
| 16 | safe release | **PASS** | Idle Worker stopped while repository/session evidence remained. |
| 17 | release with unacknowledged packet | **PASS** | Release was blocked before relay/tmux shutdown. |
| 18 | abandon plus late packet | **PASS** | Monitoring stopped; late result remained stale evidence. |
| 19 | recover lost Worker | **PASS** | Missing owned tmux became `lost` with no replay. |
| 20 | recover uncertain Worker | **PASS** | Live tmux with absent RPC became `outcome_unknown`. |
| 21 | prompt-limit rejection | **PASS** | Extra correction was rejected before RPC mutation. |

Fresh aggregate evidence: **21 passed in 41.31 seconds**. Teardown used each
fixture's exact isolated tmux server and left no owned test server alive.

## Distribution and final verification

Not run yet.
