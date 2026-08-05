# Main Coding Runtime Parity Design

**Date:** 2026-08-05

**Status:** Approved for inline implementation

## Goal

Bring every reusable runtime, terminal, process, delegation, and launcher improvement proven on `offsec-agent` into the main Travis234 coding profile while preserving the main product identity, provider/model catalog, agent loop, compaction system, package names, state path, and release line.

## Integration Strategy

Use a selective forward port, not a branch merge or broad cherry-pick. Each capability is reintroduced against current `main` behind its own failing regression. OffSec-specific prompt content, target handling, Kali packaging, security skills, docs, versions, image names, and release metadata remain isolated to `offsec-agent`.

The main runtime remains authoritative. The protected agent loop and compaction zones are not edited. Provider and model catalog changes already present on main are retained.

## Capability Map

### Managed-process argument recovery

At the process-tool preparation boundary, recover only a closed vocabulary of common model-generated aliases:

- `sessionid`, `sessionId`, and `processid` become `session_id`.
- `nextCursor` becomes `cursor`.
- `yieldTimeMs` becomes `yield_time_ms`.
- `waitTimeMs` and `waittimems` become `wait_time_ms`.
- `maxBytes` becomes `max_bytes`.
- An exact `proc` plus 32 lowercase hexadecimal characters becomes `proc_` plus the same identifier.

Canonical-plus-alias values must agree after integer coercion. Conflicts fail validation. Unknown fields remain unknown and fail the existing strict schema. A process observation wait is capped at 60 seconds; this does not alter the command deadline or process state.

### Managed-process lifecycle hardening

The existing `bash -> ProcessSessionService -> process` control plane remains intact. The service gains:

- A shared foreground-update cadence, defaulting to 100 ms, that coalesces output before constructing snapshots.
- An inactivity-based reader drain deadline that resets while output is still growing.
- Honest PTY EOF behavior: pipe half-close remains supported, while `eof=true` on a PTY is rejected without closing or poisoning its input.
- Explicit prompt guidance to send terminal control characters, including Ctrl-D, through `write_raw`.
- Completion records that carry the already-known total line count.
- Same-filesystem hard-link promotion of private completed output, with the existing secure atomic copy retained for cross-device, permission, or unsupported-link failures.

The process state precedence, ownership, cursor semantics, output sanitization, 64 MiB bound, command timeout, retention, and restart recovery are unchanged.

### Native-scrollback-safe TUI input path

The TUI keeps complete logical history and native terminal scrollback. After a full render, it records a validated snapshot of the live input suffix: editor, below-editor widgets, status, and footer. Ordinary stable-line-count editor keystrokes rerender only that suffix and splice it onto the cached complete prefix.

Resize, overlays, images, focus changes, component reshaping, and editor wrapping fall back to the unchanged complete renderer. This optimization does not touch messages, session persistence, context accounting, compaction, provider requests, or agent iteration.

### Workspace-scoped tmux tool

Add a built-in `tmux` tool with `start`, `send`, `capture`, `list`, and `stop`. Logical names resolve to physical names namespaced by a stable hash of the canonical workspace. A tool instance accepts its own logical names and resolved names, rejects resolved names belonging to another workspace, preserves fast-exiting command output using `remain-on-exit`, and removes partial sessions when setup fails.

The coding prompt describes tmux for durable development servers, watchers, REPLs, test loops, and long builds. Main Docker images install `tmux`. The tool is active by default with `read`, `bash`, `edit`, and `write`.

### Tool-capable workspace-writing subagents

Internal coding children receive this fixed catalog:

`read`, `grep`, `find`, `ls`, `bash`, `process`, `tmux`, `edit`, `write`.

Children use a fixed `workspace_write` sandbox rooted at the parent workspace. The model cannot change child `cwd`, sandbox, or inject tools outside the catalog. Existing bounded concurrency and the three-spawn-per-turn limit remain. Children cannot recursively delegate.

The parent shares its managed-process service with each internal child but gives each child a distinct `ProcessOwner`. Active managed processes owned by a child are terminated when that child finishes, fails, times out, or is cancelled. Parent processes, sibling processes, and durable tmux sessions are not terminated by that cleanup.

Child prompts require immediate execution, appropriate tool calls, verification before success, evidence-backed conclusions, disjoint file ownership for concurrent children, and reporting of changed files. Tool traces retain edit/write paths inside the workspace so the result pack can report actual changed files.

### Coding orchestration and delegation discoverability

Ordinary coding sessions always expose only the two core delegation primitives, `spawn_subagent` and `wait_subagent`, so users do not need to know or name agent-management tools. Advanced management schemas remain on demand. The existing per-turn activation gate recognizes explicit child/delegation language plus parallel-agent, parallel-worker, multiple-agent, split-work, and independent-review language. An explicit request not to use subagents hides every subagent tool for that turn and restores the normal coding catalog afterward.

The default coding system prompt operates as a senior software-engineering policy. It maps finite commands to `bash`, interactive input or incremental terminal output to PTY plus `process`, durable servers/watchers/REPLs to `tmux`, and two or more independent bounded engineering workstreams to concurrent children. It keeps shared architecture, overlapping edits, integration, and final validation with the parent. It also treats child summaries as leads rather than proof: material claims, exact test counts, and changed files must be independently verified before reporting, and invented files, tests, command results, or verification are prohibited.

When two or more independent tasks are delegated, guidance asks the model to emit the independent `spawn_subagent` calls together with `wait: false`, continue useful parent work, collect every child result, and synthesize verified evidence rather than duplicate or blindly trust the children. Restricted children and explicit opt-out turns do not receive unavailable delegation instructions.

### npm dotenv forwarding

The npm Docker launcher owns an explicit `--dotenv` argument. It resolves relative paths against `--cwd`, expands `~/`, rejects missing or non-regular files, passes the path only to Docker as `--env-file`, does not mount the file, and does not pass the host path to the Python CLI. Host provider credentials remain excluded unless the user explicitly selects a dotenv file.

Containerized proxies must use a container-reachable hostname such as `host.docker.internal`; a host `localhost` URL is not rewritten automatically.

### Main release identity

The release is main Travis234 `2.4.0`:

- Python distribution: `travis234==2.4.0`
- npm launcher: `@htooayelwinict/travis234@2.4.0`
- GHCR image: `ghcr.io/htooayelwinict/travis234:2.4.0` and the existing main production tag policy

No OffSec distribution, image, version, prompt, or package name enters main.

## Protected Zones

The implementation must not edit:

- `travis/agent/agent_loop.py` or agent-loop ordering.
- `travis/compaction/`.
- Provider streaming or the model catalog.
- Iteration budgeting or bounded parallel execution.
- `~/.travis234` state ownership or path.
- OffSec branch files, release identities, or security resources.

## Qualification

Every behavior change begins with a failing regression and a focused red-green cycle. Completion requires focused Python tests, the full Python suite, npm launcher tests, Python and npm package builds, main container smoke checks, and a real installed-console-entry-point TUI session containing ten coding prompts that exercise long history, managed processes, PTY follow-up, tmux durability, workspace-writing subagents, parallel delegation, changed-file reporting, explicit delegation opt-out, and post-load editor responsiveness.
