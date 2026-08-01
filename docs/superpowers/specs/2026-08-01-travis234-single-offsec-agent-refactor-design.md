# Travis234 Single OffSec Agent Refactor Design

**Date:** 2026-08-01
**Status:** Approved
**Branch:** `offsec-agent`
**Worktree:** `/Users/htooayelwin/lewis/travis234-offsec`

## Executive summary

The `offsec-agent` branch will be rebuilt from the current `main` commit and
turned into one OffSec-native Travis234 product. It will not retain separate
coding and OffSec profiles. The existing agent loop, provider runtime,
compaction layers, session persistence, TUI, process service, extensions, and
SDK remain the harness. The specialization occurs at the product prompt,
default tools, subagent execution policy, tmux integration, packaging, and
qualification layers.

The current branch's manifest, systemd containment, CTF platform adapter,
case-board, and profile-switching architecture will be removed by the reset.
No replacement policy engine will be introduced. The resulting product is a
direct, operator-driven OffSec agent intended for authorized CTFs, labs, and
assessments.

## Current-state evidence

At design time:

- `main` is commit `c43f6b5861d770c1be4878ade2f3dd64837748c5`.
- `offsec-agent` is 52 commits ahead of `main` and has no main-only commits.
- The committed branch delta adds approximately 13,341 lines across 105 files.
- The worktree also contains an uncommitted beginner-target experiment.
- The branch delta is dominated by `travis/offsec`, manifest parsing, systemd
  execution, platform adapters, case-board UI, and dual-profile wiring.
- `main` already contains the robust agent loop, compaction, persistent
  sessions, managed process PTYs, bounded depth-one subagents, tool registry,
  provider abstraction, extensions, skills, and TUI.

This makes a clean main-based specialization smaller and clearer than removing
the old architecture incrementally.

## Goals

The refactor must produce all of the following:

1. one default OffSec agent with no coder/OffSec profile boundary;
2. a complete OffSec replacement for the default coding system prompt;
3. tool-capable subagents with bash, process, edit, write, and tmux access;
4. first-class tmux control for long-lived and out-of-band workflows;
5. prompt guidance that distinguishes bash, process PTYs, and tmux;
6. a Kali-based packaged runtime containing tmux and baseline CTF tools;
7. preservation of the proven runtime loop and compaction layers; and
8. a regression-first qualification protocol covering the real installed
   product.

## Non-goals

This refactor will not add:

- a second runtime loop;
- separate `coding` and `offsec-ctf` profiles;
- engagement manifests;
- kernel or systemd network containment;
- a dedicated worker account;
- mandatory Docker usage;
- a CTFd control-plane adapter;
- deterministic flag submission;
- a domain-specific case-board database;
- a fixed catalog of pentest worker classes;
- recursive subagents; or
- unbounded worker concurrency.

The operator remains responsible for selecting and authorizing the target.
The agent prompt must reflect that contract, but the first version will not
attempt to turn arbitrary shell execution into a network policy engine.

## Chosen approach

### Clean main-based specialization

Implementation begins by preserving the current OffSec work as an archive,
then resetting the `offsec-agent` branch tree to `main`. Only the new design and
implementation plan are carried onto the clean branch before feature work
starts.

This approach was selected over:

- stripping down the current branch, which would retain coupling to the
  dual-profile design; and
- extracting a new OffSec runtime, which would duplicate the strongest parts
  of Travis234 and create long-term runtime drift.

## Product identity

The specialized branch keeps these public identities:

- product: `Travis234 OffSec`;
- Python distribution: `travis234-offsec`;
- npm launcher: `@htooayelwinict/travis234-offsec`;
- executable: `travis234`;
- Python import package: `travis`; and
- application state root: `~/.travis234`.

The executable name and import package remain compatible with the harness. No
alternate state directory, migration alias, or second executable is added.

## Single-agent CLI

The CLI starts the OffSec agent by default. It does not expose `--profile` or
`--agent-profile`.

The standard beginner flow is:

```bash
travis234 --cwd ~/agent-work --target 10.129.1.23
```

`--target` is optional and repeatable. It accepts a non-empty operator-supplied
target label such as an IP address, hostname, URL, lab name, or challenge name.
Targets are added to the system prompt as operator-authorized context. They do
not create manifests, routes, firewall rules, verifier adapters, or alternate
working directories.

Without `--target`, the agent still starts normally and obtains target context
from the conversation. Existing modes, model configuration, sessions,
extensions, skills, images, and project trust continue to work.

## OffSec system prompt

The default coding-assistant preamble and guidelines will be replaced, not
appended to. `build_system_prompt()` remains the stable composition entrypoint
for SDK and resource compatibility, but its default content becomes an OffSec
operating contract.

The prompt is intentionally concise and capability-derived. It contains these
sections:

### Identity and authorization

- Act as an expert OffSec agent for operator-authorized CTFs, labs, and
  assessments.
- Treat the operator-provided target and engagement context as authoritative.
- Do not invent scope, credentials, findings, or successful exploitation.

### Investigation loop

- Observe the environment before forming conclusions.
- Maintain explicit facts, hypotheses, tests, evidence, and failed attempts in
  the conversation and workspace artifacts.
- Prefer the cheapest discriminating test.
- Pivot when evidence contradicts a hypothesis.
- Treat exploit delivery and command execution as attempts until effects are
  observed.

### Tool strategy

- Use `bash` for finite commands that should finish promptly.
- Use bash plus `process` for interactive programs that require a PTY or
  follow-up input during the current agent session.
- Use `tmux` for listeners, reverse connections, OOB callbacks, relays,
  servers, long waits, and work that must survive multiple turns.
- Use `read`, `grep`, `find`, and `ls` for evidence gathering.
- Use `edit` and `write` for scripts, payloads, wordlists, notes, and reports.
- Delegate independent objectives to subagents and avoid duplicating the same
  work in the parent.

### Evidence and completion

- Preserve exact commands, relevant output, paths, and artifacts.
- Separate confirmed findings from candidates and speculation.
- Do not claim a flag, shell, vulnerability, credential, or impact without
  observed evidence.
- Finish with confirmed results, evidence references, failed approaches,
  running tmux sessions, and blockers.

Project instructions, custom prompt templates, lazy skills, selected tool
descriptions, current date, current working directory, and operator targets
remain dynamically composed through the existing prompt pipeline.

The useful reasoning patterns identified in Pensar Apex are ported
semantically. No large prompt block or product-specific wording is copied.

## Tool-capable subagents

### Default child capability

An internal child receives this default tool catalog:

- `read`
- `grep`
- `find`
- `ls`
- `bash`
- `process`
- `edit`
- `write`
- `tmux`

The default subagent sandbox changes from `read_only` to `workspace_write`.
The model-facing mutation-goal rejection is removed. Role skills may narrow or
reorder this catalog, but cannot grant nested delegation.

### Execution behavior

- The parent still assigns one bounded objective per child.
- A child may inspect, execute, create, and modify files in the selected
  workspace.
- Internal children share the app-owned `ProcessSessionService` so their bash
  calls can return managed PTY handles and accept follow-up input.
- Each child receives a child-specific `ProcessOwner` to keep process handles
  attributable and cleanup deterministic.
- A child's final result continues to contain a bounded summary, changed
  files, artifacts, tool trace, errors, and evidence.
- The parent reviews and integrates child output.

### Bounds retained from main

- maximum three active child workers;
- maximum depth one;
- maximum three model-spawned children per turn;
- duplicate-spawn suppression;
- per-child timeout and cancellation;
- bounded visible summaries and paged expansion; and
- app shutdown cleanup.

Children do not receive `spawn_subagent`, session/model commands, or global
configuration mutation. These are runtime ownership boundaries, not an
OffSec-specific restriction framework.

### Parallel writes

Subagents may write files, so the prompt and tests require disjoint ownership
for parallel tasks. The parent must not assign the same file to multiple
children concurrently. Existing workspace path validation and atomic edit/write
tools remain in force. No new distributed locking system is introduced.

## First-class tmux tool

### Purpose

The existing managed process service is ideal for short-lived and interactive
PTY work. tmux adds a different capability: a named terminal session that
survives model turns, subagent completion, and temporary loss of the
controlling prompt.

### Tool interface

Add a built-in `tmux` tool with one action per call:

```text
start(name, command, cwd?)
send(name, input, enter=true)
capture(name, lines=200)
list()
stop(name)
```

Behavior:

- `start` runs `tmux new-session -d` in the requested workspace directory and
  returns the managed session name.
- `send` uses literal-key transmission and sends Enter separately when
  requested.
- `capture` uses `capture-pane -p` and returns bounded recent output.
- `list` returns only sessions using the Travis234 namespace.
- `stop` kills the named session and is idempotent when it is already absent.

Operator names are validated and stored as
`travis234-<workspace-hash>-<name>`. Every result reports the resolved name so
the operator may attach with the native tmux CLI.

The tool invokes tmux through direct argument vectors. It does not construct a
shell command from model input. Commands executed inside a newly created tmux
session are passed to tmux as one command string, matching native tmux
semantics.

### Failure behavior

- Missing tmux produces a concise installation error.
- Starting a duplicate live session reports that it already exists.
- Sending to or capturing an absent session reports the missing name.
- Invalid names, line counts, working directories, and unexpected fields are
  rejected before invoking tmux.
- Command failures return bounded stderr without credentials or environment
  dumps.

The tool does not auto-install packages or request privilege. The Kali image
guarantees tmux; host-native users install it with their system package manager.

### Selection guidance

Use ordinary bash when the command is finite and no later input is expected.

Use managed bash/process PTY when:

- an interactive command needs prompts or control sequences now;
- output belongs in the current process spool; or
- the process should terminate with the app.

Use tmux when:

- waiting for an OOB callback;
- holding a reverse shell;
- running a listener or relay;
- keeping a development server or tunnel alive;
- the operator may need to attach manually; or
- the work should outlive the child that started it.

The parent owns final reporting of live tmux sessions. tmux sessions are not
silently killed when a child finishes. The operator or agent stops them
explicitly.

## Runtime data flow

1. The CLI resolves the workspace, models, optional targets, resources, and
   session exactly as main does.
2. `CodingApp` creates the existing app-owned process service and primary
   `AgentSession`.
3. `build_system_prompt()` composes the OffSec identity, live tool catalog,
   targets, project instructions, skills, date, and cwd.
4. The parent reasons and uses standard tools, managed processes, or tmux.
5. For an independent objective, the parent creates a workspace-write
   `SubagentTask` with the OffSec child catalog.
6. The internal child receives the shared process service, a unique process
   owner, and the same OffSec prompt contract with its delegation boundary.
7. Child file changes are immediately visible in the shared workspace; child
   evidence and changed-file metadata return through the existing result pack.
8. The parent verifies results, continues the assessment, and reports live
   process/tmux state at completion.

No additional orchestrator sits above the stable agent loop.

## Packaging and runtime environment

### Host-native

The Python distribution remains directly installable with `uv tool install`
or a virtual environment. tmux is an external executable discovered on PATH.
The agent may use bash to install tools when the operator's host permissions
allow it.

### Kali image

The release image uses Kali rolling and contains at least:

- bash;
- ca-certificates;
- curl;
- file;
- git;
- iproute2;
- jq;
- netcat-openbsd;
- nmap;
- openssl;
- Python 3.13;
- ripgrep;
- socat;
- tmux;
- Node 20, npm, and npx.

The npm launcher defaults to the specialized GHCR image and retains persistent
`~/.travis234` and workspace mounts. Docker remains an optional distribution
surface, not the execution architecture.

## File-boundary plan

The refactor should concentrate changes in these areas:

- `travis/coding_agent/system_prompt.py`: OffSec default prompt composition;
- `travis/coding_agent/agent_session.py`: optional target prompt context and
  tmux tool registration inputs;
- `travis/coding_agent/session_types.py`: OffSec child defaults and schemas;
- `travis/coding_agent/session_subagents.py`: writable child policy and shared
  process service wiring;
- `travis/coding_agent/subagents.py`: workspace-write task default and OffSec
  child return contract;
- `travis/coding_agent/tools/tmux.py`: tmux tool implementation;
- `travis/coding_agent/tools/__init__.py`: tmux registry integration;
- `travis/cli.py` and `travis/app.py`: optional repeatable target propagation;
- Docker and package metadata: specialized identity and Kali/tmux runtime;
- tests and docs: qualification evidence.

Files under `travis/agent/` and `travis/compaction/` are red zones for this
refactor and must not change. Provider implementations are also outside scope.

## Reset and history strategy

The existing work must remain recoverable without polluting the new tree:

1. record the exact branch HEAD and dirty file list;
2. create an archive branch or tag for the committed OffSec v1 history;
3. save the dirty beginner experiment as a named stash or patch outside tracked
   source;
4. reset `offsec-agent` to the current local `main` commit;
5. reapply only this approved design and its implementation plan; and
6. verify that the clean baseline equals main before writing production code.

No operation modifies the `main` ref or its worktree.

## Testing strategy

Every behavior change follows red-green-refactor with a failing regression
first.

### Baseline and deletion contracts

- prove the reset tree matches main except the approved design and plan;
- prove `travis/offsec` and old OffSec tests are absent;
- prove CLI help has no `--profile`, `--engagement`, CTFd, fixture, worker, or
  manifest options; and
- prove agent loop and compaction files match main byte-for-byte.

### Prompt contracts

- assert the default prompt identifies an OffSec agent and not a coding
  assistant;
- assert authorization, evidence, recovery, delegation, and completion rules;
- assert bash/process/tmux selection guidance;
- assert selected tool descriptions and repeatable targets are projected;
- assert custom prompts, project context, skills, date, and cwd still compose;
  and
- assert prompt size remains bounded.

### Subagent contracts

- assert the default child sandbox is `workspace_write`;
- assert the exact child catalog includes bash, process, edit, write, and tmux;
- assert a real internal child can create and edit a file and run bash;
- assert a child can start a PTY, send follow-up input, and obtain output;
- assert three disjoint workers run concurrently and return changed files;
- assert nested spawning remains unavailable;
- assert duplicate, timeout, cancellation, summary, and result-pack behavior;
  and
- assert app shutdown cleans managed processes.

### tmux contracts

- unit-test every action with a fake direct-argv runner;
- reject invalid names, directories, line limits, and unexpected arguments;
- test missing executable and failed command messages;
- real-smoke start, send, capture, list, and stop when tmux is installed;
- test a delayed callback/listener-style flow across multiple agent turns; and
- assert child-created sessions remain visible to the parent and operator.

### Product qualification

- focused Python tests;
- complete Python suite;
- npm launcher tests;
- npm pack dry run;
- Python wheel and sdist build;
- clean-wheel installed-entrypoint smoke;
- Kali release-image build;
- container baseline-tool audit including tmux;
- full container smoke;
- seven-scenario TUI protocol;
- red-zone diff audit; and
- credential/secret and whitespace checks.

## Seven-scenario TUI qualification

1. Start the default agent and confirm OffSec identity with no profile option.
2. Run finite recon-style bash and receive a terminal result.
3. Run an interactive PTY command, send follow-up input, and terminate it.
4. Delegate a child that writes and edits a workspace artifact.
5. Run three disjoint child objectives and reconcile their evidence.
6. Start a tmux wait/listener flow, send or observe an event, capture output,
   and stop it.
7. Trigger compaction, resume the session, and continue using prior findings
   plus visible tmux state.

All scenarios use local fixtures or operator-authorized lab targets and redact
provider credentials from recorded evidence.

## Acceptance criteria

The refactor is complete only when:

- the branch is based on main and the old dual-profile architecture is gone;
- Travis234 starts as one OffSec agent without a profile choice;
- the default system prompt is fully OffSec-oriented;
- subagents can use bash, process, edit, write, and tmux;
- tmux supports named long-lived workflows and is prompt-guided for OOB,
  reverse, listener, and relay use;
- the main agent loop and compaction red zones are unchanged;
- Python, npm, package, installed-wheel, Kali image, container, and TUI gates
  pass; and
- the repository contains no credentials or obsolete manifest instructions.
