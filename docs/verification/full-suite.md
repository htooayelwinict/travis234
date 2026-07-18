# Travis234 verification record

Verification date: 2026-07-18 (Asia/Yangon)

The current working tree is verified directly from the filesystem. The qualification workflow does not depend on Git metadata or invoke Git commands.

## Extension runtime and packaged resources — 2.3.3

- Full Python source suite: 1,791 passed. The clean-runner release workflow provisions its bounded pytest dependency explicitly.
- npm launcher suite: 21 passed; npm pack dry-run contained exactly the five declared files for `@htooayelwinict/travis234@2.3.3`.
- Repository hygiene: all seven reported categories are zero. Python compileall and Twine checks passed.
- Acceptance parity: Pi reported 74 parity, four documented safety divergences, and zero invalid contracts; Hermes reported 11 parity, zero divergence, and zero invalid contracts.
- `uv build --clear` produced `travis234-2.3.3-py3-none-any.whl` and `travis234-2.3.3.tar.gz`.
- A clean Python 3.13 wheel install passed dependency checks, console help, packaged-resource discovery, and faux print/JSON turns. The installed wheel contains the authoritative extension guide and the `subagent-delegation` and `web-search` fallback skills; obsolete Hypa resources are absent.
- The no-cache `travis234:2.3.3-release-smoke` build passed the complete unprivileged container qualification, including extension flags, print/JSON/RPC, trust isolation, sessions and compaction, managed processes, npm, and clean shutdown.
- Extension parity changes are confined to extension source/generation guards, host bindings, event/action adapters, TUI command and shortcut dispatch, non-interactive host wiring, and explicit extension/RPC input-source labels. Core agent-loop ordering, bounded tool execution, session persistence, context-envelope construction, and compaction ownership remain unchanged.
- Packaged skills are read-only lazy defaults. Existing project/global/explicit skill sources retain first-wins precedence, and `--no-skills` disables packaged discovery.

## Prior subscription provider wire compatibility — 2026-07-18

- Focused provider-wire module: 17 passed in 0.52 seconds.
- Complete provider contract group: 125 passed in 3.16 seconds, including OpenRouter routing/sampling preservation and Copilot route-containment controls.
- Full source suite: 1,759 passed in 114.49 seconds.
- Repository hygiene: all seven reported categories are zero.
- npm launcher: 20 passed; npm pack dry-run contained exactly the five declared files.
- Python compileall and Twine metadata checks passed. The release build produced `travis234-2.3.2-py3-none-any.whl` and `travis234-2.3.2.tar.gz`.
- Acceptance verification exited zero: Hermes reported 11 parity, zero divergence, and zero invalid contracts; Pi reported 74 parity, four documented safety divergences, and zero invalid contracts.
- The no-cache `travis234:2.3.2-release-smoke` release image build and `evals/container_smoke.py` both exited zero.
- Authenticated Codex smoke: passed. With session `temperature=0.2`, Travis emitted the expected local dropped-parameter warning instead of a provider error, and Codex returned an exact sentinel present only in the isolated project's system instructions.
- Authenticated Claude Code and GitHub Copilot smokes: not run because those credentials were not configured. No result is represented as passed.
- Runtime changes are limited to provider capability normalization, final provider wire serialization, and the built-in model compatibility catalog. Agent-loop, session, context-envelope, compaction, retry, continuation, and provider-request ownership paths are unchanged.

## Rebrand

- The active product is `Travis234`; distribution and command are `travis234`; the Python package is `travis`.
- App-owned state, AGENTS, skills, sessions, sandbox home, image, and environment paths use the Travis234 contract.
- The local Pi, Hermes, and appv231 reference checkouts are retained as read-only design oracles and are excluded from Python/npm distributions and the Docker build context.
- Focused brand, distribution, metadata, release, and architecture contracts passed.

## Process ownership

`tests/test_process_service.py` and `tests/test_process_tools.py` cover monitor-failure termination, acknowledged stdin writes, broken-pipe propagation, process limits, waits, completion recovery, and output ownership. The combined process/cancellation group passed 98 tests.

## Cancellation and shutdown

`tests/test_tui_user_commands.py`, `tests/test_session_commands.py`, and `tests/tui/test_interactive_shutdown_characterization.py` prove repeated Ctrl-C escalation and bounded TUI/session-command shutdown. They were included in the 98-test process/cancellation group.

## Installed package

- `python -m build` produced `travis234-2.3.1-py3-none-any.whl` and `travis234-2.3.1.tar.gz`.
- The wheel installed into a new Python 3.13 virtual environment with `pip check` clean.
- Outside the checkout, installed metadata reported distribution/version `travis234 2.3.1`, app title `Travis234`, and existing packaged `README.md`, `docs`, and `examples` resources under site-packages.
- The installed `travis234 --help` entry exited zero.

## Architecture

Facade and owner-boundary tests passed for AgentSession, InteractiveMode, TUI components, and provider adapters. The provider/session/policy/facade group passed 58 tests.

## Provider ownership

ProviderControlPlane and ModelRegistry ownership tests passed. Runtime credentials remain provider-scoped and are excluded from model-driven tool subprocesses unless explicitly allowlisted.

## Session index

Session index, catalog, and performance tests passed. Warm listing uses indexed metadata rather than deserializing total JSONL history.

## Compaction

Compaction adapter, coordinator, timing, persistence, model-switch recalibration, real-usage anti-thrash, overflow recovery, and boundary tests passed.

The two local upstream implementations were used as design references:

- The loop reference keeps tool-result messages in assistant source order while tool completion events may reflect completion order; Travis234 preserves both invariants.
- Hermes-aligned policy triggers at 75% of effective input below 512K, 50% at or above 512K, and a reachable 85% fallback when the 64K floor would consume a small route.
- Effectiveness is verified using the next real provider prompt count; stale model-bound calibration clears on model switches, and post-compaction estimates include system, tools, messages, images, and replay metadata immediately.

## Policy

The bash mutation classifier is conservative and advisory. Repeated-call guidance recovers inside the same turn by default; administrative hard stops require explicit opt-in.

## Hygiene

`scripts/check_repository_hygiene.py` reported:

```text
unused_dependencies: 0
camel_symbols: 0
duplicate_groups: 0
oversized_tests: 0
forbidden_compatibility: 0
reference_coupling: 0
distribution_leaks: 0
```

Plain root-level pytest discovery is constrained to `tests/`; generated evaluation fixtures under `artifacts/` are not collected.

## Red zone

The focused agent-loop and compaction group passed 149 tests. It covers bounded parallel execution, source-ordered provider results, completion-ordered lifecycle events, iteration budgeting, malformed tool recovery, compaction timing, overflow handling, and core import boundaries.

## Yellow zone

The combined process, cancellation, provider, session, and policy owner suites passed. Fault injection covers live-transport cleanup, stdin failure acknowledgement, repeated interrupts, stuck shutdowns, provider isolation, session indexing, and advisory classification.

## Green zone

- Full source suite: 1,534 passed in 103.92 seconds.
- npm launcher: 20 passed.
- npm pack dry-run: exactly five declared files.
- Python compileall: passed.
- Wheel/sdist build and clean installed-entry smoke: passed.
- No-cache `Dockerfile.release` build: passed.
- Production container smoke: passed as unprivileged user `travis`, with isolated `/travis-home`, no provider credential forwarding, `travis234`, Python/pytest, Node/npm/npx, workspace npm installation, print/JSON/RPC and TUI faux turns, untrusted project suppression, manual and automatic compaction, managed-process reaping, and clean exit.
- The container smoke exposed and regressed two previously untested defects: a stale `travis` executable name in the smoke harness and a missing Python test runner in the release image.

The offline/company-wide safety and parity qualification is complete. The actual provider-backed 21-prompt acceptance remains blocked by external credentials/billing and is recorded separately; no paid-provider result is represented as passed.
