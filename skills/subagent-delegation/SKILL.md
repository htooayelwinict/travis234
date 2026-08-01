---
name: subagent-delegation
description: Use when the user explicitly asks for subagents, child agents, delegation, handoff, parallel agents, reviewer agents, explorer agents, research agents, or agent-to-agent workflows.
---

# Subagent Delegation

Use this skill only for the current request when the user explicitly asks for subagents, delegation, handoff, `/delegate`, or `/subagents`.

## Delegation contract

- Use one wave with at most 3 children. A later wave requires a new explicit user request.
- Give each child one bounded objective, a clear stop condition, a small output budget, and disjoint file ownership.
- Children use a workspace-write catalog containing `read`, `grep`, `find`, `ls`, `bash`, `process`, `edit`, `write`, and `tmux`.
- Use bash for finite commands, bash plus process for interactive PTY work, and tmux for listeners, reverse connections, OOB callbacks, relays, servers, and cross-turn waits.
- Do not let children spawn more subagents.
- Avoid duplicate investigation and overlapping mutation ownership among concurrent children.
- Pass exact user-provided paths or names to the child. Do not pre-read, find, list, grep, or resolve delegated target files in the parent.
- Never turn a vague request into an unbounded whole-workspace sweep. Ask for a concrete scope when none is available.

## Child prompt

Every child receives this Subagent system contract:

- Current working directory: the child's selected workspace.
- Use paths relative to the Current working directory unless the goal supplies an absolute path.
- Do not drop leading project directories from paths in the Goal.
- Allowed tools are the child's complete tool catalog. Do not use tools outside it.
- For file discovery, use `find` or `ls`.
- You may create or modify only your assigned workspace files with `edit` and `write`.
- Use evidence to separate confirmed findings, hypotheses, unknowns, and failed attempts.
- After two failed attempts for the same path or unavailable tool, stop repeating it and report the blocker.
- Report changed files, evidence, failed attempts, artifacts, live tmux sessions, and blockers.
- Do not include full tool traces in the final response.

## Parent integration

The parent reviews changed files, evidence, failed attempts, artifacts, live tmux sessions, and blockers before integrating results. Resolve ownership conflicts before accepting overlapping changes.

A truncated child result is not a failed child result. Use `expand_subagent_result` for bounded child output instead of duplicating the child's investigation. Do not re-read files in the parent merely to reconstruct truncated child output. If expansion remains insufficient, page it with `offset`.

Forbidden fallback: do not say "Let me read the key files directly" after a bounded child result. The only allowed recovery paths are to use the available summary, expand the result, page the expansion, report the blocker, or—after explicit user authorization—spawn a narrower follow-up child task.

Report each child task id, role, status, concise summary, changed files, and blockers. Cancellation, timeout, duplicate suppression, and the three-child limit remain terminal runtime outcomes; do not conceal them or retry automatically.
