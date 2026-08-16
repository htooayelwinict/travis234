# Native acceleration evidence gate

- Date: 2026-08-16
- Source base commit at invocation: `4009bd42c9e6c9d6a2008abbb0d6ddd9ed9c74c3`
- Python: 3.13.13
- Platform: Darwin arm64
- Command: `python benchmarks/contract_parity_hotpaths.py --rounds 7 --warmups 2 --json`
- Candidate supplied: no
- Decision: `retain_python`

The source-base commit identifies the checked-out parent while the Task 7
benchmark and documentation were still pending in the worktree. No hostname,
username, filesystem path, environment value, or credential is recorded.

## Baseline result

The repeated invocation below used seed 234 and the fixed work units encoded by
the benchmark. Wall share is each isolated case median divided by the measured
6.280 ms mixed-workflow median; it is evidence for this benchmark mix, not a
production traffic estimate.

| Hot path | Work units | Median | CV | Wall share | Gate observation |
| --- | ---: | ---: | ---: | ---: | --- |
| Artifact verification | 1 | 0.137 ms | 0.068 | 2.2% | Below the 5% share threshold |
| Policy decision | 256 | 0.447 ms | 0.002 | 7.1% | Eligible for candidate measurement |
| LSP frame parsing | 32 | 0.452 ms | 0.041 | 7.2% | Eligible for candidate measurement |
| Supervisor snapshot | 128 | 0.649 ms | 0.009 | 10.3% | Eligible for candidate measurement |
| Operation-journal write | 8 | 2.711 ms | 0.272 | 43.2% | Baseline is too variable; reject a candidate |
| Memory recall over 64 facts | 64 | 0.646 ms | 0.010 | 10.3% | Eligible for candidate measurement |
| MCP result conversion | 32 | 0.092 ms | 0.024 | 1.5% | Below the 5% share threshold |

The mixed workflow CV was 0.070. No optional native module was imported. The
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
