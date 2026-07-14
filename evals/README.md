# travis SDLC Evaluation

This opt-in harness drives the actual `travis.cli` TUI through a PTY. It uses sanitized JSONL lifecycle events for synchronization and never stores prompts, generated source, credentials, or authorization headers in reports.

```bash
PYTHONPATH=travis234 uv run --dev python -m evals.run_sdlc_eval \
  --dotenv .env \
  --model-query mimo \
  --model-index 1 \
  --thinking medium \
  --temperature 0.2 \
  --output-dir /tmp/travis-sdlc-eval
```

The output directory must be empty unless `--resume` is supplied. No package or image is published by this command.

For a direct-provider continuous run, pin both axes instead of relying on an aggregator-style model ID:

```bash
python -m evals.run_continuous_sdlc_eval \
  --dotenv .env \
  --model-provider stepfun \
  --model-query step-3.7-flash \
  --output-dir /tmp/travis-continuous-eval
```

## 21-prompt developer handoff

Use this protocol for end-user UX and behavior testing of the 21 SDLC scenarios. This is a direct attached-terminal test. Do not use `TuiDriver`, `evals.run_sdlc_eval`, `evals.run_continuous_sdlc_eval`, a mock provider, or another wrapper as the user boundary.

The harness above remains useful for automated regression runs, but it does not replace this direct TUI protocol.

### Start from a clean workspace

Create a disposable demo directory outside the repository. Populate its `scenarios/` directory with the scenario fixtures before launching the app. Never use the repository checkout itself as the coding scenario working directory.

```bash
DEMO_ROOT="$(mktemp -d /tmp/travis-direct-tui.XXXXXX)"
AGENT_ROOT="$(mktemp -d /tmp/travis-direct-agent.XXXXXX)"
mkdir -p "$DEMO_ROOT/scenarios"
```

Launch the app itself in an attached terminal from the repository root:

```bash
TRAVIS234_CODING_AGENT_DIR="$AGENT_ROOT" \
PYTHONPATH="$PWD/travis234" \
  "$PWD/.venv/bin/python" -m travis.cli \
  --cwd "$DEMO_ROOT" \
  --dotenv "$PWD/.env" \
  --thinking medium \
  --temperature 0.2
```

The process attached to the terminal must be `python -m travis.cli` itself. Type into that process with normal terminal input. Do not send prompts through a helper program, JSON command channel, eval runner, or hidden session API. Do not print, copy, or persist dotenv values.

### Session contract

- Use one logical conversation for all 21 prompts, split across the three attached TUI processes described below.
- At the visible `travis>` prompt, type `/model mimo`, press Enter, type `1`, and press Enter. Require the TUI to display `Switched model to openrouter/xiaomi/mimo-v2.5-pro` and show `medium` in the footer.
- Type exactly one combined end-user prompt per scenario into the visible editor. Phrase it naturally with the target directory inline; do not prefix it with a scenario ID or scenario name.
- Wait until the full assistant response is visible and the footer returns to `status: Idle` before sending the next prompt.
- Print the exact prompt and visible final assistant response in the developer's test notes as each turn completes.
- Run each scenario's external verifiers only after its turn finishes.
- Type `/compact` after scenarios `4`, `8`, `12`, `16`, and `20`; wait for visible compaction completion and `Idle` before continuing.
- After prompt `7`, type `/session`, record the session file and full ID, then type `/exit` and require exit code `0`. Relaunch the same command with `--continue`; require the same ID, restored history, selected model, and thinking level before prompt `8`.
- After prompt `14`, type `/session`, record the same ID, then type `/exit` and require exit code `0`. Relaunch the same command with `--resume`, choose the recorded session in the visible picker, and require the same ID and restored history before prompt `15`.
- Finish prompt `21` by typing `/session` and `/exit`; require the same ID and exit code `0`.

### Evidence and pass criteria

Maintain a 21-row test matrix outside the demo workspace:

```text
scenario id
exact prompt
visible final response
model and thinking level
verifier commands and exit codes
compaction count
duration
failure or guardrail notes
```

A scenario passes only when the direct TUI returns to `Idle` with a final response and every configured external verifier exits `0`. The complete run passes only when all 21 scenarios pass, all five visible compactions finish, all three attached TUI processes exit cleanly, and every `/session` checkpoint reports one unchanged session ID and file.

Do not infer success from the assistant's prose. Treat verifier exit codes and final fixture state as authoritative behavior evidence. The direct terminal transcript is authoritative UX evidence.

### Stalls and fixes

- Provider reasoning can be silent for several minutes. Observe the visible TUI status and recent tool output before classifying a turn as stalled.
- On a bounded turn timeout, send one real-user `Ctrl-C`. Require `status: Aborting`, `Operation aborted`, and a return to idle in the same TUI session.
- Record the timeout or abort in the result matrix. Do not silently switch models, start a hidden replacement session, or substitute a mock provider.
- Diagnose a proven runtime defect before editing. Add a failing regression test, make one root-cause fix, and run the focused and full suites.
- After any travis runtime fix, discard the partial evaluation, create a fresh output directory, restart the TUI, and rerun from Prompt 1.
- Do not modify `travis234/travis/compaction/` while repairing issues found by this protocol.

This evaluation never publishes npm packages or images and never performs git operations. Build or release work is a separate explicit task.
