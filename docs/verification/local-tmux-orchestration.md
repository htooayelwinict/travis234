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

## Distribution and final verification

Not run yet.
