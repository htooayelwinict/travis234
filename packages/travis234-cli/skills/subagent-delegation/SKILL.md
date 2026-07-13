---
name: subagent-delegation
description: Use only when the user explicitly asks to spawn, delegate to, hand off to, or verify work through subagents, child agents, reviewer agents, explorer agents, research agents, web-search agents, or agent-to-agent workflows.
---

# Subagent Delegation

Use this skill only when the user explicitly asks for subagents, delegation, handoff, reviewer/explorer/research child agents, `/delegate`, or `/subagents`.

## Default behavior

- Keep the parent agent normal unless this skill is explicitly triggered.
- This skill is active for the current user request only. After the parent reports child results, return to normal main-agent behavior.
- Do not use this skill on a later user request unless that later request explicitly asks for subagents, delegation, handoff, reviewer/explorer/research child agents, `/delegate`, or `/subagents`.
- Use one delegation wave by default, with at most 3 child agents.
- Do not launch a second wave unless the user explicitly asks for it after seeing the first child summaries.
- Do not describe or plan "Wave 2", "Wave 3", or future waves in the same answer.
- If the task is larger than 3 children can cover, process only the first bounded slice and ask the user whether to continue.
- Do not let children spawn more subagents.
- Do not write files unless the user explicitly asks for written artifacts.
- Subagents must remain read-only.
- Subagents must not write files, edit files, create files, delete files, or receive `write`/`edit` tools.
- If Lewis requests a written artifact from delegated work, the child should inspect only and the parent should write the artifact from the child summary.

## Scope control

- If the requested scope is vague, ask one concise clarification instead of broadening it.
- For phrases like "those files" or "those md files", use only exact files already named in the current conversation or visible parent output.
- If no exact file list is available, ask which files or directory to use.
- Do not pre-read, find, list, grep, or resolve delegated target files in the parent before spawning the child. The parent may read this skill, but the child must inspect and validate the assigned file, directory, report, or repo area.
- Never convert a vague request into a whole-repo or whole-workspace sweep.
- Never run whole-workspace file-count or whole-workspace discovery commands such as `find /workspace -type f`.
- Avoid unbounded discovery commands. Prefer a named directory and a capped listing, for example `find docs -maxdepth 1 -name '*.md' | head -20`.
- If there are more than 12 candidate files, ask the user to narrow scope before spawning children.

## Child task contract

Before spawning, the parent must give each child:

- A role.
- Exact paths or one narrow directory.
- A clear stop condition.
- A small output budget.
- A requirement to report status, blockers, and a concise summary.

Child instructions must say:

- Subagent system contract:
  - Current working directory: include the child's cwd or selected workspace.
  - Use paths relative to the current working directory unless the goal gives an absolute path.
  - Do not drop leading project directories from paths in the Goal; preserve prefixes like `travis234/`.
  - Allowed tools are the child's complete tool catalog. Do not use tool names outside Allowed tools.
  - For file discovery, use `find` or `ls`.
  - After two failed attempts for the same path or unavailable tool, stop retrying and report the blocker.
- Make one diagnostic attempt after a missing path or failed tool call, then stop and report the blocker.
- Do not retry the same tool call with the same arguments.
- For file discovery, use `ls`, `find`, `read`, or a bounded `bash` command when needed.
- Do not scan parent directories outside the assigned scope.
- Do not include full tool traces in the final answer.

Parent instructions:

- Spawn first when the user explicitly delegates inspection.
- Pass exact user-provided paths or names to the child. Do not use parent `read`, `bash`, `find`, `ls`, `grep`, or equivalent tools to inspect, validate, locate, or resolve delegated targets before or after spawning.
- Keep child tools read-only. Do not grant write/edit tools to a child task.
- Do not include file-writing instructions in a child goal. If the final answer requires a report file, ask the child for findings only, then the parent should write the file.
- Use `expand_subagent_result` if the child summary is bounded and more child output is needed.

## Parent reporting

After children finish, report only:

- Child task id.
- Child role.
- Child status.
- Concise child summary.
- Any blocker or guardrail status.

If a child hits a guardrail or cancellation, report it and stop. Do not retry automatically.

Do not compensate for child failure by directly scanning the remaining parent scope. Report the child status and ask the user for the next step.

## Bounded child results

- A truncated child result is not a failed child result.
- Do not re-read files in the parent to compensate for bounded or summarized child output.
- Treat a completed child summary as authoritative unless the user explicitly asks the parent to inspect the files directly.
- If the summary is insufficient, call `expand_subagent_result` with the smallest useful `section` and `budget`.
- If `expand_subagent_result` is still truncated, page it with `offset` instead of rereading child-scoped files in the parent.
- Spawn a narrower follow-up child task only when the expansion still cannot answer the user or the user explicitly asks for another child.
- Forbidden fallback: after seeing `... [truncated]`, do not say "Let me read the key files directly" or any equivalent.
- Do not call `read`, `bash`, `grep`, `find`, or other parent tools to reconstruct files that were assigned to the child.
- The only allowed recovery paths are: answer from the bounded child summary, call `expand_subagent_result`, ask the user whether to expand further, or spawn a narrower follow-up child task after explicit user authorization.
- Treat truncation as the delegation boundary doing its job, not as a failure that the parent should repair by taking over.

Do not carry this workflow into the next user message. A completed child result is not permission to call more subagent tools later.
