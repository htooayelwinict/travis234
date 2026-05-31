# Worker Role Skills (Capability-Aligned Super Skills)

These worker skill drafts are informed by `plan/live-decompressor-planner-runs-20260531-232621.json` and the current planner/kernel runtime contract. They are written as reusable worker-role instructions, not as scenario-specific prompts.

## Runtime contract these skills assume

- Decompressor emits descriptive `Envelope` context only. It does not choose workers, steps, or runtime permissions.
- Planner emits a validated `Plan` with explicit phases, modes, artifact lineage, permissions, and budget ceilings.
- Worker kernel compiles each step into a `Task` and passes only the prior runtime artifacts named in `step.input_artifacts`.
- Every worker must emit every requested artifact ID in `task.expected_outputs`, even when blocked; blocked artifacts should contain concise failure explanations.
- Execution halts when a worker returns `failed`, `blocked`, or `budget_exceeded`.
- `envelope.artifacts` are planning hints only. Worker runtime inputs come from prior step outputs, not directly from envelope hints.

## Capability model

These files use four distinct capability gates. They must not be blurred together.

### `read_files`

`read_files=true` allows non-mutating repository inspection and search operations only. This includes:

- listing directories and file trees
- opening and reading file contents
- grep-style content search through repository search tooling
- file-name search, symbol lookup, and semantic/code search
- extracting snippets, paths, symbols, and evidence anchors from existing files

`read_files` does not allow shell or subprocess execution. Even if a search behaves like grep conceptually, it is still a read/search operation when done through read/search tooling rather than through a shell command.

### `run_commands`

`run_commands=true` allows shell or subprocess execution such as:

- test runners
- language toolchains
- linters and formatters
- package manager commands
- scripts
- `git` CLI or other terminal commands

If `run_commands=false`, do not open a terminal to compensate for missing read/search tools. Use read/search tooling only when `read_files=true`.

### `web_research`

`web_research=true` allows external retrieval and source inspection. It does not imply repository read access or shell access.

### `write_files`

`write_files=true` allows mutation only within explicit write scope declared by literal write paths or scope artifacts such as `mutation_scope`.

## Budget model

- `task.max_tool_calls` applies to all tool usage, including read/search tools, web retrieval tools, and command execution.
- `task.max_model_calls` applies to model-backed reasoning or synthesis.
- If a worker approaches either budget before completion, it must stop broadening scope and use the remaining budget to emit required outputs with partial-progress or blocked explanations.

## Blocked-output rule

- If a permission gate, missing artifact, write gate, or budget constraint blocks completion, start the status text with `STATUS: BLOCKED` and name the precise missing gate or blocker.
- Still emit each required artifact ID from `task.expected_outputs` with concise failure content so the planner/kernel can reason about the omission.
- Never hide a blocked state by emitting a success-sounding summary.

## Files

- `repo_worker/skill.md`
- `research_worker/skill.md`
- `web_research_worker/skill.md`
- `code_worker/skill.md`
- `verify_worker/skill.md`
- `direct_worker/skill.md`
- `infra_worker/skill.md`
