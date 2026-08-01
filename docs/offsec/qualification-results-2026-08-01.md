# Travis234 OffSec qualification — 2026-08-01

All runs used the `offsec-agent` branch and a local Docker/Kali environment. No
credential values, authorization headers, or external target details are
recorded here.

## Automated and package gates

| Gate | Result | Evidence |
|---|---|---|
| Focused OffSec/runtime suite | Pass | `427 passed, 2 skipped` before final packaging adjustments; targeted delegation regression: `75 passed` |
| Full Python suite | Pass | `1856 passed, 2 skipped in 123.19s` after the final child-keyword regression |
| npm launcher suite | Pass | `24` tests passed |
| npm pack dry run | Pass | Package is `@htooayelwinict/travis234-offsec` with the launcher and five bundled skills |
| Wheel and sdist | Pass | `travis234_offsec-2.3.5-py3-none-any.whl` and matching sdist built successfully |
| Kali release image | Pass | `travis234-offsec:refactor-final` rebuilt without cache |
| Container smoke | Pass | Python `3.13.14`, Node `v20.20.2`, and bash/curl/file/git/ip/jq/nc/nmap/openssl/rg/socat/tmux/npm/npx/travis234 all resolved in a login shell |

## Real TUI protocol

Provider/model: `opencode-go/mimo-v2.5-pro`, thinking `medium`.

The persistent packaged-image session used target context
`local-ctf-fixture` and a private Docker workspace. The test state directory
was mounted at `/travis-home/agent`; it contained normal local authentication
state but no credentials are included in this record.

| Scenario | Result | Runtime evidence |
|---|---|---|
| 1. Role and target context | Pass | Agent identified Travis234 OffSec and `local-ctf-fixture`; no tools were run. |
| 2. Finite bash | Pass | `printf 'FINITE-RECON-OK\\n'` returned `FINITE-RECON-OK` with exit `0`. |
| 3. Managed PTY follow-up | Pass | Process accepted `INTERACTIVE-OK`, emitted `PTY-OK:INTERACTIVE-OK`, and exited `0`. |
| 4. Writable child | Pass | Internal child created, edited, and bash-verified `evidence/child.txt` as `CHILD-EDIT-OK`; parent expanded/inspected the child result pack. |
| 5. Three parallel children | Pass after regression fix | The exact `three parallel children` wording initially did not activate the opt-in child-tool catalog. Root cause was the keyword recognizer accepting `child agent` but not `children`. A failing regression test was added, the recognizer gained `child` and `children`, and the rebuilt package started three direct internal children concurrently. They produced and parent-reconciled `evidence/a.txt = A-OK`, `b.txt = B-OK`, and `c.txt = C-OK`. |
| 6. Named tmux lifecycle | Pass | Resolved session `travis234-c52ddf65534b-callback-check` emitted `TMUX-CALLBACK-OK`; the agent listed, captured, stopped, and proved it absent. |
| 7. Compact and resume | Pass | `/compact` compressed `60 → 26` messages (about `8,952 → 3,628` tokens). `--continue` restored the same workspace/target and remembered the tmux callback fact. |

### Model-quality observations

- In the first Scenario 5 attempt, the model chose a child delegation shape
  that placed further delegation inside a child. Nested child delegation is
  intentionally unavailable. The underlying activation defect was then found
  and fixed; the exact prompt passed after rebuilding.
- In the final Scenario 5 rerun, two children first tried `xxd`, which is not
  in the intentionally minimal Kali image. They recovered using available
  tooling and returned successful evidence. This is a model command-choice
  note, not a runtime failure.

## Local Juice Shop exercise

The packaged agent was also run against an OWASP Juice Shop container on the
private Docker network (`juice-shop:3000`), with the host binding restricted to
`127.0.0.1:3000`. It successfully used the Kali toolset, saved landing-page
headers/body, and produced a bounded discovery report. It also fetched
`robots.txt` despite a stricter landing-page-only instruction; that is recorded
as a prompt-adherence/model-quality miss. No authentication, data mutation,
fuzzing, brute force, or exploitation was requested or performed.

## Structural audit

- Legacy `travis/offsec` and tracked `tests/offsec/**` are absent.
- The CLI help contains no profile/engagement/challenge/CTFd/worker flags.
- The targeted delegation fix is limited to `travis/coding_agent/session_types.py`;
  it does not alter `travis/agent/**`, `travis/compaction/**`, or
  `travis/ai/providers/**`.
- Credential scans use patterns only and produced no tracked credentials.
