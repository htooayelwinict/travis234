# appv231 TUI Handoff And Subagent Evaluation Design

## Purpose

Preserve the proven 21-prompt real-TUI evaluation protocol for future developers, then run a separate five-prompt evaluation of appv231 subagent behavior.

## Documentation

Extend `appV2.3.1/evals/README.md` with a developer handoff section for the 21-prompt SDLC run only, covering:

- `python -m appv231.cli` as the supported TUI entrypoint.
- A fresh temporary workspace and an explicitly selected dotenv file.
- One continuous TUI session for the complete scenario set.
- `/model mimo`, picker row `1`, thinking `medium`, and temperature `0.2`.
- Sanitized terminal, lifecycle, conversation, and result artifacts.
- Exact prompt and response reporting plus independent scenario verifiers.
- Scheduled `/compact` operations and explicit capability grants.
- Real-user `Ctrl-C` recovery for a stalled turn.
- Restart from Prompt 1 whenever appv231 runtime code is changed.
- No publishing, GHCR push, or git operation as part of evaluation.

The README must not describe the five-prompt subagent evaluation as part of this protocol.

## Separate Subagent Run

Run a one-off five-prompt subagent evaluation after the README handoff is saved. Reuse `TuiDriver` as the PTY operator without adding a permanent subagent runner. Its child command remains:

```text
python -m appv231.cli
```

The evaluation creates a fresh fixture workspace, selects the configured model through the actual TUI, submits five prompts sequentially in one session, records sanitized evidence under `/tmp`, independently validates expected subagent events and parent responses, then exits through `/exit`.

## Five Prompts

1. Activate `subagent-delegation`; spawn one reviewer for an exact documentation path and report task id, role, status, summary, and blocker state.
2. Activate the skill again; spawn one explorer for an exact source path and require a bounded behavioral summary without parent rereads.
3. Activate the skill again; spawn one QA reviewer for an exact test path and report coverage gaps without modifying files.
4. Exercise `/delegate --backend internal` on an exact path, then query `/agents` and validate visible supervisor status.
5. Request a prohibited child write and validate that the read-only guardrail rejects it without mutating the fixture.

Each prompt is explicit because the skill is request-scoped and must not remain active implicitly across turns.

## Evidence And Pass Criteria

The temporary output directory contains:

- `trace.jsonl`: sanitized TUI and subagent lifecycle events.
- `conversation.jsonl`: exact user prompts and final parent responses.
- `terminal.log`: sanitized actual TUI transcript.
- A manually verified five-row result matrix derived from the trace, conversation, terminal, and fixture state.

The run passes only when all five prompts finish, expected child lifecycle/status evidence is present, guardrails behave as specified, no fixture file is mutated by a child, the TUI shuts down cleanly, and no fatal event occurs.

## Failure Handling

- Provider latency is allowed within the configured bounded turn timeout.
- On timeout, send one `Ctrl-C` and require the same TUI session to return to idle.
- Record failures instead of silently switching models, sessions, or runners.
- If a proven appv231 defect is fixed, discard the partial run and restart from Prompt 1 in a fresh workspace.

## Constraints

- Do not modify `appV2.3.1/appv231/compaction/`.
- Do not publish npm packages or container images.
- Do not perform git operations.
- Do not expose dotenv values or provider credentials in any artifact.
