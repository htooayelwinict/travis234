# Travis234 2.4.0 Main Coding TUI Qualification

This protocol qualifies the installed main coding distribution, not the OffSec profile, source-tree module execution, or the eval runner.

## Required launch shape

Build the wheel, create an isolated environment and fixture, then launch the installed console entry point in an attached PTY:

```bash
uv build
python3.13 -m venv /tmp/travis234-main-240-venv
/tmp/travis234-main-240-venv/bin/pip install dist/travis234-2.4.0-py3-none-any.whl
command -v /tmp/travis234-main-240-venv/bin/travis234
/tmp/travis234-main-240-venv/bin/travis234 \
  --cwd /tmp/travis234-main-240-fixture \
  --dotenv /absolute/path/to/operator.env \
  --tui --no-session
```

Use an isolated `HOME` or `TRAVIS234_CODING_AGENT_DIR` for the run. The dotenv path is operator-selected and its contents must not be copied into this record.

## Fixture contract

The fixture is a small git repository with:

- `README.md` containing literal project name `TEN-PROMPT-CODING-FIXTURE` and language `Python`.
- `calculator.py` exporting `add(left, right)`.
- `test_calculator.py` containing a passing literal assertion for `add(2, 3) == 5`.
- An empty `child_outputs/` directory reserved for disjoint child-owned files.

## Ten prompts

Run all prompts in one TUI session. Wait until the TUI is idle before sending the next prompt.

1. `Return exactly 120 numbered short lines, then end with CODING-HISTORY-END.`
   Verify the marker, native scrollback, and responsive typing at the live bottom.
2. `Read README.md and report exactly the project name and language from the file.`
   Require a successful `read` tool result and the two literal values.
3. `Run the fixture tests with bash and report the exact pass count and exit code.`
   Require a successful finite bash call.
4. `Start an interactive Python PTY that prints READY, reads one line, prints FOLLOWUP:<line>, then exits. Use process follow-up input to send CODING-PTY. Collect terminal state.`
   Require `bash` with `tty=true`, a returned process handle, follow-up `process` input, and observed `FOLLOWUP:CODING-PTY`.
5. `Use tmux to start a command named fast-check that prints TMUX-FAST-OK and exits immediately. Capture the retained output, then stop the session.`
   Require the fast-exit marker after command completion and explicit stop.
6. `Use tmux to start a durable Python HTTP server named dev-server on an available loopback port, capture readiness, verify the process is alive, then stop it.`
   Require start, capture or equivalent readiness evidence, liveness evidence, and stop.
7. `Delegate one coding child to create child_outputs/one.txt containing CHILD-ONE, verify it, and report the changed file. The parent must not create that file.`
   Require `spawn_subagent`, child write/edit plus verification evidence, and `filesChanged` containing `child_outputs/one.txt`.
8. `Use two parallel agents. Child A owns only child_outputs/a.txt and writes A-OK; Child B owns only child_outputs/b.txt and writes B-OK. Spawn both without waiting serially, collect both results, and synthesize their changed files.`
   Require both spawn calls in one assistant tool-call response with `wait=false`, collection of both terminal results, disjoint output, and synthesis.
9. `Do not use subagents. Inspect calculator.py in the parent and report the function name and parameter names.`
   Require no subagent management tool call and the literal answer `add(left, right)`.
10. Enter a multiline prompt using Shift+Enter or Alt+Enter: first line `Final audit:` and second line `verify all three child files, run pytest, list active managed processes and tmux sessions, clean up any leftovers, then end with CODING-10-PASS.`
    Require the exact marker, passing tests, all child file contents, and no active fixture-owned managed process or tmux session.

## External verification

After `/exit`:

```bash
cd /tmp/travis234-main-240-fixture
/tmp/travis234-main-240-venv/bin/python -m pytest -q
test "$(cat child_outputs/one.txt)" = CHILD-ONE
test "$(cat child_outputs/a.txt)" = A-OK
test "$(cat child_outputs/b.txt)" = B-OK
tmux list-sessions -F '#{session_name}' 2>/dev/null | rg '^travis234-' || true
```

The terminal must restore cursor visibility, bracketed paste, and mouse reporting state. No fixture-owned process may remain.

## Result matrix

| Scenario | Result | Evidence |
|---|---|---|
| 1. Long history and typing | Pass | OpenRouter Mimo Pro produced exactly 120 numbered lines and `CODING-HISTORY-END`; the editor returned to Idle and accepted the next prompt without visible input lag. |
| 2. Read tool | Pass | `read README.md` returned `TEN-PROMPT-CODING-FIXTURE` and `Python`. |
| 3. Finite bash | Pass | Bash ran pytest and reported `1 passed` with exit code `0`. |
| 4. Managed PTY follow-up | Pass | PTY process `proc_57d5cf7e...` emitted `READY`; `process write` sent `CODING-PTY`; `process wait` at cursor 17 returned `FOLLOWUP:CODING-PTY` and exit code `0`. |
| 5. Fast tmux retention | Pass | `fast-check` retained `TMUX-FAST-OK` after pane exit, then stopped explicitly. |
| 6. Durable tmux server | Pass | `dev-server` captured readiness on loopback port 57041, returned HTTP 200 with PID 17099 alive, then stopped; the parent confirmed the process was gone. |
| 7. Writing child | Pass | Child `subagent-1e16ede7abaa` created and verified `child_outputs/one.txt`; the parent independently read the file and confirmed exact content and size. |
| 8. Parallel writing children | Pass | OpenRouter Mimo Pro emitted both `spawn_subagent` calls together with `wait=false`; their tool completions were 1 ms apart. Children `subagent-1fff5ee1c277` and `subagent-d38c5e117357` wrote disjoint files; the parent and external checks confirmed `A-OK`/`B-OK`. One child attempted unsupported macOS `cat -A`, then completed successfully; parent verification prevented that model-level command choice from weakening the result. |
| 9. Explicit subagent opt-out | Pass | Parent used `read calculator.py`, spawned no child for the turn, and reported `add(left, right)`. |
| 10. Loaded-session final audit | Pass | Alt+Enter created the two-line prompt. Agent verified all child files, pytest (`1 passed`, exit `0`), no managed process, and no Travis234 tmux session, ending with `CODING-10-PASS`. |

## Qualification record

- Date: 2026-08-05 (Asia/Yangon)
- Installed artifact: `dist/travis234-2.4.0-py3-none-any.whl` in an isolated uv environment
- Fixture: `/tmp/travis234-main-240-final-fixture.oVumT3`
- TUI evidence: `/tmp/travis234-main-240-final-events.jsonl` and `/tmp/travis234-main-240-final-conversation.jsonl`
- Provider/model: `openrouter/xiaomi/mimo-v2.5-pro`, thinking `medium`, for all ten turns.
- External verification: pytest passed; all three child contents matched; no fixture tmux session or HTTP server remained.
- Terminal restoration: the attached PTY exit emitted cursor-show and bracketed-paste-disable sequences.

### Five-prompt orchestration preflight

Before the final ten-prompt run, a separate installed-wheel Mimo Pro session tested the new coding-profile routing policy with five natural prompts. The prompts did not name internal tools except the explicit opt-out phrase. The agent:

1. recognized independent frontend/backend work, spawned two children concurrently, then independently read both components and reran both focused tests with exact `1/1` results;
2. selected finite `bash` for the complete suite and reported 2 passed with exit code 0;
3. selected PTY plus `process` poll/write/wait for interactive input and reported exact terminal output with exit code 0;
4. selected tmux for a durable loopback server, captured HTTP 200 evidence, stopped it, and proved the listener was gone; and
5. honored `Do not use subagents`, used only parent reads, and reported the exact function signatures.

The preflight evidence is in `/tmp/travis234-prompt-routing-retest-events.jsonl` and `/tmp/travis234-prompt-routing-retest-conversation.jsonl`. An earlier preflight exposed invented child test counts; the final senior-engineering prompt now treats child summaries as leads rather than proof and requires parent verification of material claims. The repeated scenario then reported the exact observed counts.

### Mimo Pro parallel-dispatch retest

A fresh installed-wheel TUI session launched directly on
`openrouter/xiaomi/mimo-v2.5-pro` repeated the parallel-dispatch scenario with
new, child-owned files. Both `spawn_subagent` calls were part of the same run
and their successful tool completions were 2 ms apart. Child A
(`subagent-f2249ded2b10`) wrote `child_outputs/retest-a.txt` containing
`RETEST-A-OK`; Child B (`subagent-6dd33236a752`) wrote
`child_outputs/retest-b.txt` containing `RETEST-B-OK`. Both terminal results
were collected and both files were independently read back with exact content.
The retest evidence is in
`/tmp/travis234-main-240-parallel-retest-events.jsonl` and
`/tmp/travis234-main-240-parallel-retest-conversation.jsonl`.
