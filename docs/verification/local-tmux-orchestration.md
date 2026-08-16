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

No phase results yet.

## Distribution and final verification

Not run yet.
