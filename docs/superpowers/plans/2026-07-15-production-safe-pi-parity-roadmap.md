# Travis234 Production-Safe Pi Parity Implementation Roadmap

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this roadmap task-by-task. Do not use subagents. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a company-wide-safe Travis234 core followed by broad Python-native Pi behavioral parity, using pinned Pi and Hermes sources as behavioral oracles.

**Architecture:** Preserve Travis234's core loop, provider control plane, process manager, compaction transaction coordinator, and Pi-compatible session persistence. Add one trust control plane and one canonical request-envelope authority, then port current Hermes compaction behavior and Pi coding-agent capabilities into focused Python modules.

**Tech Stack:** Python 3.13, pytest, asyncio, JSONL v3 sessions, SQLite index, PyYAML 6, argparse, httpx, Node launcher tests, Docker release image.

## Global Constraints

- The repository root is the only active application tree.
- Product and CLI remain `Travis234` and `travis234`; Python imports remain `travis`.
- State remains under `~/.travis234`; no migration alias or alternate state path is allowed.
- Pi `1f0dbc008c9b3e88017d42e8a1b46d416ad2b6b6` is the coding-agent behavioral source.
- Hermes `af250d84948179834820a62bfd870c0df6f264a1` is the compaction behavioral source.
- `appv231/` is a historical regression cross-reference only.
- Preserve core loop ordering, iteration budgeting, source-ordered result persistence, and bounded parallel execution.
- Preserve `summary`, `firstKeptEntryId`, and `tokensBefore` session compatibility.
- Add a failing regression test before every defect correction.
- Do not run state-changing Git commands, including add, commit, checkout, reset, switch, merge, rebase, stash, or worktree.
- Replace every normal commit checkpoint with a test-and-review checkpoint.

---

## Program file map

### New focused owners

- `travis/coding_agent/project_trust.py`: trust store, resource detection, resolution policy, choices
- `travis/coding_agent/package_manager.py`: Python-native package sources and lifecycle
- `travis/coding_agent/prompt_templates.py`: template parsing and expansion
- `travis/coding_agent/skills.py`: skill discovery, ignore semantics, frontmatter validation, commands
- `travis/coding_agent/themes.py`: theme discovery and TUI registration
- `travis/compaction/policy.py`: threshold, tail, summary, and auxiliary-capacity calibration
- `travis/coding_agent/automation.py`: print and JSON mode drivers
- `travis/coding_agent/rpc.py`: framed RPC driver
- `travis/coding_agent/agent_harness.py`: Python public SDK composition root
- `travis/ai/images.py`: image-generation API and registry

### Existing owners to converge

- `travis/coding_agent/resource_loader.py`: two-pass trusted orchestration only
- `travis/coding_agent/extensions.py`: event contracts, event bus, registrations
- `travis/coding_agent/session_extensions.py`: missing lifecycle emissions and trusted context actions
- `travis/coding_agent/session_turns.py`: canonical request construction and input expansion
- `travis/coding_agent/session_persistence.py`: canonical context telemetry
- `travis/coding_agent/settings_manager.py`: connected trust, compaction, package, resource, and theme settings
- `travis/ai/catalog_generation.py`: route-specific model limits
- `travis/ai/context_estimate.py`: sole request-envelope and prompt-usage authority
- `travis/compaction/compressor.py`: transcript transformation and summarization
- `travis/compaction/timing.py`: scheduling, real-usage verification, cooldown coordination
- `travis/cli.py`: trust flags, modes, package commands, explicit resources, input parsing
- `travis/tui/interactive_*`: trust, session, theme, and package UI behavior
- `travis/ai/models.py`: async-safe model discovery API

## Phase plans

### Phase 1: Production trust and route-capacity safety

Detailed plan: `docs/superpowers/plans/2026-07-15-production-trust-and-capacity.md`

Release deliverable:

- arbitrary repositories cannot execute project Python without an explicit trust decision
- no-UI startup fails closed
- `--approve`, `--no-approve`, `/trust`, saved decisions, and `defaultProjectTrust` work
- OpenRouter route-specific capacity matches pinned Pi behavior

Gate:

```bash
.venv/bin/python -m pytest -q \
  tests/test_project_trust.py \
  tests/test_extension_loading_and_reload.py \
  tests/test_catalog_generation.py \
  tests/test_reference_runtime_contract.py
```

Expected: all selected tests pass and the untrusted execution sentinel is absent.

### Phase 2: Canonical envelope and Hermes compaction

Detailed plan: `docs/superpowers/plans/2026-07-15-context-envelope-and-compaction.md`

Release deliverable:

- one request-envelope authority serves clamping, compaction, TUI, and evaluation
- provider output no longer inflates next-input pressure
- post-compaction telemetry includes system and tools immediately
- merged summaries rehydrate cleanly
- protected head decays, cooldown blocks automatic rewrites, and summarizer capacity calibrates early

Gate:

```bash
.venv/bin/python -m pytest -q \
  tests/test_context_estimate.py \
  tests/test_compaction.py \
  tests/test_compaction_timing.py \
  tests/test_compaction_integration.py \
  tests/test_app_integration.py
```

Expected: all selected tests pass, including second-compaction and estimate-continuity regressions.

### Phase 3: Extension, resource, and package parity

Detailed plan: `docs/superpowers/plans/2026-07-15-extension-resource-package-parity.md`

Release deliverable:

- all pinned Pi extension lifecycle events have Python-native equivalents
- shared event bus works across extensions and reloads
- prompt templates, skill commands, themes, YAML frontmatter, and ignore files affect runtime behavior
- local, Git, and Python package sources support trusted install/remove/update/list workflows

Gate:

```bash
.venv/bin/python -m pytest -q \
  tests/test_extension_event_parity.py \
  tests/test_extension_loading_and_reload.py \
  tests/test_resource_runtime_parity.py \
  tests/test_package_manager.py \
  tests/test_coding_resources_and_services.py \
  tests/test_tui_commands_and_extensions.py
```

Expected: all selected tests pass and the extension event manifest reports complete parity.

### Phase 4: CLI, TUI, and session parity

Detailed plan: `docs/superpowers/plans/2026-07-15-cli-tui-session-parity.md`

Release deliverable:

- print, JSON, and RPC modes share the same session and loop owners
- tool allow/deny, explicit resources, offline startup, `@file`, and image inputs work
- session name, fork, clone, tree, switch, and import/export behaviors match the pinned Pi contract
- trust and theme commands are available in the TUI

Gate:

```bash
.venv/bin/python -m pytest -q \
  tests/test_cli.py \
  tests/test_automation_modes.py \
  tests/test_rpc_mode.py \
  tests/test_session_parity.py \
  tests/test_tui_commands_and_extensions.py \
  tests/test_tui_terminal_and_input.py
```

Expected: all selected tests pass and JSON/RPC golden outputs contain no human-only TUI text.

### Phase 5: SDK and production qualification

Detailed plan: `docs/superpowers/plans/2026-07-15-sdk-and-release-qualification.md`

Release deliverable:

- `AgentHarness`, async model discovery, stream proxy, and optional image API are public and tested
- acceptance evidence proves company-wide-safe defaults and broad Pi behavioral parity
- source, packages, installed entry, and release container pass

Gate:

```bash
.venv/bin/python -m pytest -q
npm --prefix packages/travis234-cli test
npm --prefix packages/travis234-cli run pack:dry-run
.venv/bin/python -m build
.venv/bin/python scripts/verify_acceptance.py
```

Expected: all commands exit zero. Then build and run the documented release-container smoke without provider credentials.

## Program execution order

- [x] **Step 1: Complete Phase 1 and inspect its focused evidence**

Run the Phase 1 plan from top to bottom. Do not begin context refactoring while project code still executes by default.

- [x] **Step 2: Complete Phase 2 and inspect long-session behavior**

Run the faux-provider long-session smoke after the focused suite. Confirm displayed post-compaction context does not jump solely because a small follow-up was submitted.

- [x] **Step 3: Complete Phase 3 in two review gates**

First land extension-event and event-bus behavior in the working tree, run its tests, and inspect. Then implement resource and package workflows and run the combined gate.

- [x] **Step 4: Complete Phase 4 by transport bundle**

Implement print/JSON first, RPC second, then session/TUI commands. Each transport must reuse `CodingApp` and `AgentSession` rather than introducing another loop.

- [x] **Step 5: Complete Phase 5 and run the completion audit**

Check every requirement in the design against current files and command output. Treat missing or indirect evidence as incomplete.

- [x] **Step 6: Record final handoff evidence without invoking Git**

Provide the planned/touched file list from the phase records and direct filesystem inspection, test commands with pass counts, builds, smoke results, and any external credential-dependent gate that remains unexecuted. Do not invoke Git, including status or diff commands.
