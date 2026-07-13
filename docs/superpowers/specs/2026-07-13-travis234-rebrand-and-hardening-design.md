# Travis234 Rebrand and Runtime Hardening Design

Date: 2026-07-13

Status: approved

## Objective

Create a focused public repository named `travis234`, hard-cut the application
over to the Travis234 product identity and `travis` internal codename, fix the
eleven validated findings, and prove the result through regression tests,
cross-zone checks, packaging checks, and one real 21-prompt TUI session.

The public repository was created before local implementation work, as required:

- Repository: `htooayelwinict/travis234`
- Visibility: public
- Default branch: `main`

## Source of truth

The imported `appV2.3.1` tree is byte-for-byte identical to the same directory
at `allthebest@next/appv23.1` commit
`d99d04234ca9ffa51ff279229a065d203240a0d3c`. It is the behavior baseline.
Only the useful release launcher, bundled AGENTS instructions, and skill assets
from that branch will be reconstructed in the new focused repository. Legacy
application trees and reference repositories will not be imported.

## Naming contract

This is a hard cutover. No old command, import, environment-variable, config,
session, image, or compatibility alias remains.

| Boundary | Final name |
| --- | --- |
| GitHub repository | `htooayelwinict/travis234` |
| Product name | Travis234 |
| Python distribution | `travis234` |
| Python import package | `travis` |
| User command | `travis234` |
| npm distribution | `@htooayelwinict/travis234` |
| Container image | `ghcr.io/htooayelwinict/travis234` |
| Container user | `travis` |
| Environment prefix | `TRAVIS234_` |
| Host state root | `~/.travis234` |
| Host agent instructions | `~/.travis234/agent/AGENTS.md` |
| Host skills | `~/.travis234/agent/skills/` |
| Host sandbox home | `~/.travis234/sandbox-home` |
| Container home | `/travis-home` |
| Container agent state | `/travis-home/agent` |
| Persistent sessions | `~/.travis234/agent/sessions/` on host-native installs and `/travis-home/agent/sessions/` in the sandbox |

Old state is not silently imported. Documentation will give an explicit,
user-controlled copy command for anyone who needs to migrate old credentials or
sessions. The launcher never reads legacy directories automatically.

## Branding and attribution boundary

Runtime symbols, module names, comments, docstrings, filenames, display text,
HTML identifiers, thread names, temp-file prefixes, test names, and documentation
will use Travis234 or `travis`. Product-style Pi/Hermes labels and porting comments
will be removed or rewritten as behavior-focused explanations.

`LICENSE` and `NOTICE.md` will retain the upstream copyright holders and the MIT
permission notice. This is legal attribution rather than application branding.
No runtime behavior, path, metadata field, or UI text depends on those names.

## Repository and release layout

The versioned `appV2.3.1` wrapper is removed. The focused layout is:

```text
travis234/
  travis/                 Python package
  tests/                  Python tests grouped by owner
  evals/                  evaluation and TUI drivers
  scripts/                native development entry points
  packages/travis234-cli/ npm Docker launcher and bundled assets
  docs/                   design, plans, user documentation
  Dockerfile              local development image
  Dockerfile.release      production image
  pyproject.toml
  package.json
  README.md
  LICENSE
  NOTICE.md
```

There is one canonical public launcher implementation:
`packages/travis234-cli/bin/travis234.js`. Python development launchers may call
shared Python configuration, but must not independently implement host seeding,
pull caching, skill copying, or containment policy. Any root helper delegates to
the canonical launcher instead of duplicating it.

Bundled AGENTS and skill installation is non-destructive: missing defaults are
seeded under `~/.travis234/agent`, existing user files are preserved, symlinks and
unsafe traversal are rejected, and the exact copied destination is covered by
tests.

## Finding fixes

### 1. Monitor failures must not orphan OS processes

Process ownership ends only after transport liveness is resolved. A shared
failure-finalization helper will:

1. record the monitoring failure;
2. transition the record through a stopping state;
3. send the normal termination signal;
4. wait for a bounded grace period;
5. send the kill signal if the transport is still alive;
6. drain/finalize output and only then publish `FAILED`.

Terminal-record cleanup will defensively terminate any transport that still
reports alive, so a future unexpected path cannot make terminal state exempt an
owned process from cleanup.

### 2. Asynchronous stdin writes need acknowledgements

Each queued stdin item becomes a request carrying a completion primitive. The
input pump completes it only after the transport accepted all bytes, or completes
it exceptionally with the actual broken-pipe/write error. `write()` waits for
that acknowledgement and never reports the pre-write running snapshot as a
successful write. Pump shutdown fails all queued requests deterministically.

### 3. Repeated Ctrl-C must escalate

Managed user commands use an explicit interrupt state machine instead of a
permanent boolean latch:

- first Ctrl-C sends an interrupt;
- second Ctrl-C during the grace window escalates cancellation/termination;
- a further attempt forces final process cleanup if it is still owned;
- completion resets the state.

Cancellation waits use short bounded deadlines. The additional uninterruptible
60-second wait is removed. Status text identifies which escalation was applied.

### 4. Installed-package metadata must be real package metadata

Configuration will not search upward for `package.json` or infer a source-tree
layout. Following the official Setuptools guidance verified through Context7:

- `importlib.metadata` provides the installed distribution version;
- `importlib.resources` locates packaged runtime data;
- `pyproject.toml` is the authoritative name/version/dependency definition;
- all required assets are explicitly included and tested from an installed wheel.

Tests install the wheel into a clean temporary environment and verify version,
README/resource availability, package root semantics, and console entry point.

### 5. Shutdown must be bounded

Active-turn cancellation, future waits, and worker joins have named timeout
budgets. `/exit` requests cancellation, waits only to the deadline, closes owned
resources, reports any abandoned provider/extension operation, and returns
control to terminal restoration. `SessionCommandExecutor.close()` is idempotent
and bounded. Worker threads that cannot be synchronously stopped cannot keep the
process alive.

### 6. God objects must be decomposed around stable contracts

Decomposition preserves the agent-loop and compaction algorithms while moving
unrelated responsibilities behind narrow collaborators:

- `AgentSession`: lifecycle facade over persistence, tool execution, policy,
  model/auth, subagent, and compaction collaborators;
- `InteractiveMode`: input loop facade over turn control, command dispatch,
  shutdown, and rendering controllers;
- TUI component module: editor state, layout/rendering, completion/picker, and
  terminal-event components;
- provider adapter: request translation, stream decoding, retry/error mapping,
  and provider-specific authentication/configuration modules.

Public behavior remains covered by characterization tests before extraction.
New architecture tests enforce dependency direction and maximum owner-module
sizes so the objects cannot silently regrow.

### 7. Provider state needs explicit ownership

`ModelRegistry` receives public registration, replacement, removal, and snapshot
operations. `ProviderControlPlane` uses only those operations, owns an injected
registry/provider catalog, and either uses its environment dependency explicitly
or does not accept it. No global mutable provider registry is used for session
state. Isolation tests create two control planes and prove mutations do not leak.

### 8. Session discovery must not scale with history bytes

Session listing uses a persistent catalog index under the Travis234 agent state.
Session creation and metadata-changing writes update the index transactionally.
Discovery queries indexed summaries without deserializing message histories.
Legacy/unindexed JSONL files are backfilled by reading only bounded header/tail
metadata and file stat data; unchanged files are not reparsed. Corruption remains
isolated to the affected entry. Performance tests compare small and large
histories and assert bounded bytes/records parsed during listing.

### 9. Compaction transactions need one orchestration path

A public compaction transaction coordinator owns begin, execute, apply, error,
and end semantics. All manual, overflow, automatic, and recovery entry paths in
the application call it. The application does not read
`_last_compression_result` or invoke private session methods. The mature
compaction algorithm is not redesigned; differential tests prove output and
event-order parity across all entry paths.

### 10. Bash mutation classification must be conservative and advisory

The classifier returns an explicit `read_only`, `mutating`, or `unknown` result.
It recognizes redirects without whitespace, in-place editor flags, interpreter
file-write forms, and destructive version-control restoration. `unknown` is
never promoted to a strict authorization verdict. Policy/progress code treats the
classification as a hint, while real write boundaries continue to be enforced by
tool and sandbox controls.

### 11. Dependency, compatibility, duplication, and test ownership debt

- Remove runtime dependencies with no runtime import or documented plugin role.
- Remove camelCase compatibility aliases and compatibility-only modules; wire
  protocol field names may remain only at serialization boundaries.
- Consolidate exact helper duplicates into owner-specific shared utilities.
- Split the three oversized test modules by subsystem without reducing coverage.
- Add repository checks for unused direct dependencies, forbidden compatibility
  aliases/modules, exact helper duplication, and test-file size ceilings.

## Change zones

### Red zone

The agent-loop runtime and compaction algorithms are behavior-preservation zones.
Only rebranding/import movement and the public compaction orchestration boundary
may touch them. Ordered tool results, iteration budgeting, bounded parallelism,
overflow recovery, summary contents, and compaction timing require focused
contract and differential tests before and after every related refactor.

### Yellow zone

Process ownership, TUI cancellation/shutdown, session persistence/catalog,
provider ownership, and facade decomposition are concurrency or stateful areas.
Every change begins with a failing regression or characterization test and gets
targeted stress/fault-injection tests before broader suites.

### Green zone

Package metadata, repository layout, names, launchers, documentation, dependency
cleanup, compatibility removal, helper consolidation, and test-file splitting may
change mechanically, but still require search-based and packaging acceptance
checks.

## TDD and commit discipline

For each behavior fix:

1. add the smallest regression test and run it to observe the expected failure;
2. implement the minimum behavior needed for green;
3. run the focused owner suite;
4. refactor with the focused suite green;
5. run the appropriate red/yellow/green cross-check;
6. commit the coherent change with the red/green command evidence recorded in
   the implementation log.

Mechanical rebranding uses contract tests first: final paths, imports, metadata,
commands, assets, and forbidden-name scans are encoded before the move.

## Verification

Completion requires all of the following current-state evidence:

- no forbidden former runtime names outside `LICENSE`, `NOTICE.md`, historical
  design records, or an explicit migration note;
- Python compile and full pytest suite pass;
- npm launcher tests and pack dry-run pass;
- wheel/sdist build and clean-environment installation pass;
- source and installed console entry points pass smoke tests;
- process fault-injection proves monitor cleanup and stdin error propagation;
- repeated-interrupt and stuck-provider shutdown tests finish within deadlines;
- session listing performance is independent of total history bytes;
- provider isolation and compaction entry-path parity tests pass;
- architecture/dependency/duplication/test-size checks pass;
- Docker launcher dry run proves Travis234 paths, user, image, skills, AGENTS,
  session persistence, and containment options;
- the public repository contains the final commits on `main`.

## Real TUI acceptance scenario

Only after automated checks pass, `.env` is migrated to the required
`TRAVIS234_*` keys without printing values. An isolated disposable coding fixture
is opened through the actual `travis234` TUI entry point. A PTY driver sends 21
prompts in one persistent session. The scenario exercises:

- project discovery and planning;
- read, search, edit, write, and synchronous bash tools;
- managed background processes, stdin, polling, and completion;
- interrupt escalation and recovery;
- guardrails and advisory mutation classification;
- model/provider resolution and error presentation;
- subagent delegation and result integration;
- compaction and continued work after context pressure;
- session inspection, resume, and persistence;
- tests, debugging, review, and final summary quality.

The fixture, prompt manifest, sanitized event transcript, assertions, session ID,
and scoring report are retained as test artifacts. Success means all prompts were
processed in the same session, no owned process survived, no secret appeared in
output, the fixture's tests passed, and feature-specific assertions passed. It is
not enough that the TUI merely stayed open.

## Non-goals

- Importing legacy application/reference trees.
- Preserving automatic compatibility with old names or state paths.
- Redesigning the core agent loop or compaction algorithms.
- Publishing credentials or copying `.env` into an image, package, or commit.
