---
name: subagent-delegation
description: Use only when the user explicitly asks to spawn, delegate to, hand off to, or verify work through subagents, child agents, reviewer agents, explorer agents, research agents, web-search agents, or agent-to-agent workflows.
---

# Subagent Delegation

Use this skill only when the user explicitly asks for subagents, delegation, handoff, reviewer/explorer/research child agents, `/delegate`, or `/subagents`.

## Default behavior

- Keep the parent agent normal unless this skill is explicitly triggered.
- Use one delegation wave by default, with at most 3 child agents.
- Do not launch a second wave unless the user explicitly asks for it after seeing the first child summaries.
- Do not describe or plan "Wave 2", "Wave 3", or future waves in the same answer.
- If the task is larger than 3 children can cover, process only the first bounded slice and ask the user whether to continue.
- Do not let children spawn more subagents.
- Do not write files unless the user explicitly asks for written artifacts.

## Scope control

- If the requested scope is vague, ask one concise clarification instead of broadening it.
- For phrases like "those files" or "those md files", use only exact files already named in the current conversation or visible parent output.
- If no exact file list is available, ask which files or directory to use.
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

- Make one diagnostic attempt after a missing path or failed tool call, then stop and report the blocker.
- Do not retry the same tool call with the same arguments.
- Do not scan parent directories outside the assigned scope.
- Do not include full tool traces in the final answer.

## Parent reporting

After children finish, report only:

- Child task id.
- Child role.
- Child status.
- Concise child summary.
- Any blocker or guardrail status.

If a child hits a guardrail or cancellation, report it and stop. Do not retry automatically.

Do not compensate for child failure by directly scanning the remaining parent scope. Report the child status and ask the user for the next step.
