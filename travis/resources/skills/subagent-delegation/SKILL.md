---
name: subagent-delegation
description: Use for explicitly requested in-process subagents, parallel workers/reviewers, bounded delegation, /delegate, or /subagents.
---

# Subagent Delegation

Use only for this request. Explicit refusal wins.

## Choose

- Use in-process children for bounded current-session work or review.
- Choose `orchestration` for independent Travis B needing tmux, a worktree, durable ping-pong, recovery, or full handoff; never substitute mechanisms.
- When coordination selects this route, planner and workers share the three-spawn ceiling. Refusal wins; orchestration semantics remain separate.

## Delegate

1. Use one wave, at most 3 children; later waves require explicit requests. Give each a bounded objective, stop condition, output budget, and disjoint ownership.
2. Read `spawn_subagent` metadata and use a matching typed role. Roles narrow models, allowed tools, tool and effect ceilings, timeout, structured results, and artifact policy. Otherwise use a short role.
3. Pass exact user paths or names. Do not pre-read, find, list, grep, or resolve delegated target files in the parent. Never turn vague scope into a workspace sweep.
4. Start independent children with `wait: false`, do useful parent work, then collect every result. Do not let children spawn more subagents.

Children use a workspace-write catalog: `read`, `grep`, `find`, `ls`, `bash`, `process`, `edit`, `write`, and `tmux`, narrowed by parent tools and roles. Use bash for finite commands, bash plus process for interactive PTY work, and tmux for development servers, watchers, REPLs, test loops, long builds, or cross-turn waits.

## Child contract

Every child receives this Subagent system contract:

- Current working directory: its workspace; use relative paths unless the Goal gives an absolute path.
- Do not drop leading project directories from paths in the Goal.
- Allowed tools are the child's complete tool catalog. Use nothing else.
- For file discovery, use `find` or `ls`.
- Modify only assigned files; execute now and verify before success.
- Separate evidence, uncertainty, unknowns, and failed attempts.
- After two failed attempts for one path or unavailable tool, stop and report the blocker.
- Report changed files, evidence, failed attempts, artifacts, live tmux sessions, and blockers without full traces.

A typed role may require a JSON envelope and declared artifact list. Declare only workspace-relative UTF-8 files. The runtime validates the structured result, promotes permitted files to durable `artifact-...` references, and rejects host paths.

## Supervise

Use `/agents status`, `/agents inspect <id>`, `/agents steer <id> <message>`, or `/agents cancel <id>`. They control this session's supervisor only.

Treat child results as reports; verify claims and ownership. A truncated child result is not a failed child result. Use `expand_subagent_result` and `offset`; do not re-read files in the parent merely to reconstruct truncation.

Forbidden fallback: do not say "Let me read the key files directly." The only allowed recovery paths are summary use, expansion, paging, blocker reporting, or—after explicit user authorization—spawn a narrower follow-up child task.

Read artifact IDs with bounded `read`. Report task ID, role, status, summary, changed files, artifacts, and blockers. Cancellation, timeout, schema failure, duplicate suppression, and the three-child limit are terminal; never conceal or auto-retry.
