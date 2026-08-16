# Native acceleration evidence gate

- Date: 2026-08-16
- Benchmark source commit: `6a96203078d75383de5cf2df8e3ccc22d807c77e`
- Python: 3.13.13
- Platform: Darwin arm64
- Command: `python benchmarks/contract_parity_hotpaths.py --rounds 7 --warmups 2 --json`
- Candidate supplied: no
- Decision: `retain_python`

No hostname, username, filesystem path, environment value, or credential is
recorded.

## Baseline result

The repeated invocation below used seed 234 and the fixed work units encoded by
the benchmark. Wall share is each isolated case median divided by the measured
5.267 ms mixed-workflow median; it is evidence for this benchmark mix, not a
production traffic estimate.

| Hot path | Work units | Median | CV | Wall share | Gate observation |
| --- | ---: | ---: | ---: | ---: | --- |
| Artifact verification | 1 | 0.118 ms | 0.017 | 2.2% | Below the 5% share threshold |
| Policy decision | 256 | 0.400 ms | 0.002 | 7.6% | Eligible for candidate measurement |
| LSP frame parsing | 32 | 0.402 ms | 0.034 | 7.6% | Eligible for candidate measurement |
| Supervisor snapshot | 128 | 0.573 ms | 0.001 | 10.9% | Eligible for candidate measurement |
| Operation-journal write | 8 | 2.241 ms | 0.207 | 42.6% | Baseline is too variable; reject a candidate |
| Memory recall over 64 facts | 64 | 0.568 ms | 0.010 | 10.8% | Eligible for candidate measurement |
| MCP result conversion | 32 | 0.081 ms | 0.017 | 1.5% | Below the 5% share threshold |

The mixed workflow CV was 0.112. No optional native module was imported. The
journal case remained above the 0.15 stability ceiling after batching eight
writes per sample, so the gate reports that limitation instead of weakening the
threshold. A future candidate must provide finite positive timings for exactly
one named target and prove correctness plus optional-integration conformance.

## Decision boundary

`retain_python` is mandatory when no candidate is supplied. A candidate is
`candidate_rejected` if any input is invalid or if wall share, stability,
speedup, correctness, or conformance misses its bound. Only a candidate with at
least 2.0x median speedup, at least 5% baseline wall share, CV at most 0.15 for
both measurements, and no regression becomes
`candidate_requires_packaging_review`.

That final state does not install or ship native code. It opens a separate
review of wheels, source distributions, architecture coverage, fallback
behavior, licensing, and supply-chain cost. The Python implementation remains
the authoritative path in this phase.

## Phase qualification

- Focused benchmark, acceptance, optional-conformance, and repository-hygiene
  tests: 32 passed.
- Exact root wheel: `travis234-2.4.6-py3-none-any.whl`, SHA-256
  `cbca322c151abd865f72e27dde6ccbb9171d5a1c614ccb17885e7bf8980d2d94`.
- Isolated wheel installation: `pip check` reported no broken requirements and
  the installed `travis234` console entry resolved inside the isolated venv.
- Native TUI: the installed console entry ran in a real PTY with isolated
  Travis234 state and the repository's ignored dotenv supplied only by path.
  The phase prompt forbade edits and subagents, read this evidence fixture, and
  returned exactly `retain_python`, `2.0x`, `5%`, and `0.15`. The TUI returned
  to Idle and `/exit` returned code 0 with cursor and bracketed-paste teardown.
- The fixture retained exactly its original evidence file, no subagent event
  occurred, no owned process remained, and no dotenv content was copied or
  printed.

Container qualification remains intentionally deferred until every design
phase is implemented.
