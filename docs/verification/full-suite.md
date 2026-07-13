# Travis234 verification record

Verification date: 2026-07-14 (Asia/Yangon)

Runtime tree verified through commit `9198bc9`. Evidence-only documentation changes are followed by another full-suite run before publication.

## Rebrand

- The active product is `Travis234`; distribution and command are `travis234`; the Python package is `travis`.
- App-owned state, AGENTS, skills, sessions, sandbox home, image, and environment paths use the Travis234 contract.
- The local upstream reference checkouts are intentionally retained as ignored design references and are not distributed runtime trees.
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
- The loop reference triggers compaction close to the context ceiling. Travis234 deliberately retains the earlier compression-derived 50% threshold so mixed providers have recovery headroom.
- The compression reference verifies effectiveness using the next real provider prompt count and clears stale model-bound calibration on model switches; Travis234 implements those safeguards without copying its provider ownership.

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
```

Plain root-level pytest discovery is constrained to `tests/`; generated evaluation fixtures under `artifacts/` are not collected.

## Red zone

The focused agent-loop and compaction group passed 149 tests. It covers bounded parallel execution, source-ordered provider results, completion-ordered lifecycle events, iteration budgeting, malformed tool recovery, compaction timing, overflow handling, and core import boundaries.

## Yellow zone

The combined process, cancellation, provider, session, and policy owner suites passed. Fault injection covers live-transport cleanup, stdin failure acknowledgement, repeated interrupts, stuck shutdowns, provider isolation, session indexing, and advisory classification.

## Green zone

- Full source suite: 1,500 passed in 45.82 seconds.
- npm launcher: 20 passed.
- npm pack dry-run: exactly six declared files.
- Python compileall: passed.
- Wheel/sdist build and clean installed-entry smoke: passed.
- No-cache `Dockerfile.release` build: passed.
- Production container smoke: passed as unprivileged user `travis`, with `travis234`, Python/pytest, Node/npm/npx, workspace npm installation, faux-provider TUI turn, manual compaction, and clean exit.
- The container smoke exposed and regressed two previously untested defects: a stale `travis` executable name in the smoke harness and a missing Python test runner in the release image.

The actual provider-backed 21-prompt acceptance remains blocked by external credentials/billing and is recorded separately. This record does not claim production readiness until that row passes.
