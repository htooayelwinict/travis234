# Process-tool quality: 21-prompt Minimax M3 TUI qualification

Date: 2026-08-10
Repository commit under test: `8093ee7b06fccc798cd69286299839039df34d7c`
Installed artifact: `travis234-2.4.3-py3-none-any.whl`
Python: 3.13.13
Provider/model: OpenRouter, `minimax/minimax-m3`
Thinking: medium

## Scope

This qualification exercised the process-tool schema, dynamic Bash-to-process handoff, PTY and open-pipe input, cursor continuity, polling, waiting, interruption, terminal sizing, cleanup, and context-envelope telemetry. It used one continuous attached TUI session for prompts 1-21, followed by a fresh-session replay of the interrupted prompt 13.

The installed wheel ran in an isolated temporary Python environment against a deterministic temporary Git fixture. The repository-owned `.env` was passed by path for provider configuration; no credential values were printed or copied into tracked files. Both authorized trace files were scanned for common credential patterns and returned zero matches.

## Continuous-session prompts and outcomes

1. Read `README.md`; identify the fixture and language. **PASS**
2. Run the five fixture tests as a finite command. **PASS**
3. Run delayed output as a non-PTY managed process and collect terminal state. **PASS**
4. Run single-line PTY input with `QUALITY-PTY`. **PASS**
5. Repeat the PTY path with `PROMPT-FIVE-RECOVERY`. **PASS**
6. Run two-step PTY input with `FIRST-A` and `SECOND-B`. **PASS**
7. Run open-pipe input with `PIPE-QUALITY`. **PASS**
8. Consume incremental output without replay and wait to terminal state. **PASS**
9. Observe slow work while running, then continue from its cursor. **PASS**
10. Verify a nondefault PTY terminal size. **PASS**
11. Interrupt a signal-aware managed process and collect terminal state. **PASS**
12. Rerun tests, audit process/tmux state, clean fixture caches, and reread the fixture README. **PASS**
13. Start two concurrent delayed workers with distinct labels, list both while running, and collect each independently. **ABORTED BY OPERATOR after repeated non-progressing launches**
14. Inspect the fixture limitation and confirm no managed process remained. **PASS**
15. Repeat the exact input-first PTY handoff with `PROMPT-FIFTEEN`. **PASS**
16. Repeat open-pipe input with `PIPE-SIXTEEN`. **PASS**
17. Repeat cursor-aware incremental output consumption. **PASS**
18. Repeat short-poll-to-wait handoff for slow work. **PASS**
19. Repeat two-step PTY input with new values. **PASS**
20. Verify a 28-row by 96-column PTY and terminal cleanup. **PASS**
21. Rerun tests, audit managed processes and tmux, clean fixture caches, reread the README, and classify prompt 13. **PASS**

The continuous session therefore produced 20 requested PASS markers, one operator-aborted turn, and a clean TUI shutdown.

## Context-envelope audit

Every `turn_ready` event was inspected. The trace contained exactly 21 envelope samples, one after every prompt:

| Prompt | Context tokens | Window use | Estimated | Compactions |
|---:|---:|---:|:---:|---:|
| 1 | 4,142 | 0.4142% | no | 0 |
| 2 | 5,094 | 0.5094% | no | 0 |
| 3 | 6,519 | 0.6519% | no | 0 |
| 4 | 7,631 | 0.7631% | no | 0 |
| 5 | 8,542 | 0.8542% | no | 0 |
| 6 | 10,078 | 1.0078% | no | 0 |
| 7 | 11,705 | 1.1705% | no | 0 |
| 8 | 12,815 | 1.2815% | no | 0 |
| 9 | 15,559 | 1.5559% | no | 0 |
| 10 | 16,196 | 1.6196% | no | 0 |
| 11 | 17,262 | 1.7262% | no | 0 |
| 12 | 18,637 | 1.8637% | no | 0 |
| 13 | 25,333 | 2.5333% | yes | 0 |
| 14 | 25,914 | 2.5914% | no | 0 |
| 15 | 27,443 | 2.7443% | no | 0 |
| 16 | 28,498 | 2.8498% | no | 0 |
| 17 | 29,908 | 2.9908% | no | 0 |
| 18 | 32,150 | 3.2150% | no | 0 |
| 19 | 33,728 | 3.3728% | no | 0 |
| 20 | 34,278 | 3.4278% | no | 0 |
| 21 | 35,611 | 3.5611% | no | 0 |

The prompt-13 loop added 6,696 context tokens, but the envelope stayed internally consistent and below 2.6% immediately after the abort. No compaction occurred anywhere in the run. Prompts 14-21 recovered normally in the same session.

## Continuous trace audit

- Turns: 21 total; 20 `ok`, 1 operator-aborted.
- Tool completions: 94 total; all 94 reported `ok`.
- Tool mix: 37 Bash, 49 process, 6 read, 2 tmux.
- Process mix: 9 write, 12 poll, 10 list, 1 interrupt, and 17 wait completions (wait events omit the optional action label in this sanitized trace format).
- Managed-process result store: 37 agent-origin completions; all 37 state `exited`, exit code 0.
- Process-schema errors: 0.
- Empty or missing-action process failures: 0.
- Compactions: 0.
- Active managed processes after shutdown: 0.
- Travis234 tmux sessions after shutdown: 0.
- External fixture rerun: 5 passed.
- Fixture-generated `.pytest_cache` and `__pycache__` were removed after the external rerun.

## Prompt-13 diagnosis and exact replay

The first prompt-13 attempt happened after twelve turns that repeatedly used `worker.py` modes. Minimax anchored on that pattern and repeatedly launched `python worker.py delayed`. That mode emits only fixed `DELAYED-START` and `DELAYED-DONE` text, so those launches could never satisfy the requested `ALPHA-13` and `BETA-13` labels. The calls themselves remained schema-valid and their processes exited cleanly, but the model failed to change strategy. The operator interrupted after the repetition was clearly non-progressing.

The exact prompt was then replayed unchanged in a fresh Minimax M3 TUI session. This time the model selected two labeled shell workers. Its first 2- and 3-second workers completed before `process list`; the model recognized that timing race without user help, retried with 10- and 15-second delays, confirmed both independent sessions as `running`, waited with their respective live IDs and cursors, verified each labeled output once, and emitted `PROCESS-QUALITY-13-PASS`.

Fresh replay evidence:

- Turn status: `ok`; required marker present.
- Tool completions: 12; all `ok`.
- Tool mix: 4 Bash, 6 process, 2 read.
- Process mix: 2 list, 2 poll, 2 wait.
- Final envelope: 6,938 tokens, 0.6938% of 1,000,000; 0 compactions.
- Managed-process result store after replay: 41 total agent completions; all state `exited`, exit code 0.
- Credential-pattern matches in replay traces: 0.

The replay shows that the original task was ambiguous but solvable. The continuous-session failure was a context-conditioned model planning/strategy failure, not a process schema, cursor, process-lifecycle, or context-envelope corruption. The fresh retry also demonstrates that the model can self-repair a timing race when it recognizes the failed condition. Travis234 did not automatically detect the repeated semantic non-progress in the first attempt; its existing generic iteration budget remained the ultimate bound.

## Conclusion

The process-tool quality changes passed the intended runtime scenarios, including the former prompt-5 PTY failure shape. Across both sessions there were no invalid process arguments, missing-action failures, stale managed processes, compactions, or credential-pattern matches. The one interrupted turn isolates a separate semantic replanning limitation: a model can repeat valid calls that cannot satisfy the task when prior context strongly anchors it to the wrong implementation pattern.
