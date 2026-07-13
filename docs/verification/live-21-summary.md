# Provider-backed TUI acceptance status

Status: blocked by external provider access; not accepted as production-ready.

## Requested Qwen3 Coder Next probe

The installed console entry opened the real TUI, selected `openrouter/qwen/qwen3-coder-next`, created one persisted session, emitted finalized footer telemetry, and shut down cleanly after the provider terminal error.

Exact prompt:

```text
SDLC scenario 01-python-cli-feature. Work only in scenarios/01-python-cli-feature. Add --format json while preserving text output; update help and tests. Run tests, repair edge cases, and align README examples.
```

Exact agent output:

```text
Error: OpenRouter billing or quota failed (HTTP 402) for model qwen/qwen3-coder-next. Add credits or update billing with that provider, then retry. Provider message: Payment Required
```

Finalized footer evidence: 8,576 / 262,144 tokens, 3.271484375%, provider-real confidence, zero compactions. The harness classified this as `provider_billing_failure`, ran no task verifier, stopped after one request, found no secret leak, and found no nonterminal child process.

Artifacts: `artifacts/live-21-qwen-next-9198bc9/` (ignored, local evidence).

## Earlier Qwen provider run

An earlier actual OpenRouter Qwen run completed 11 prompts in one persisted TUI session before quota prevented continuation. Eight scenario verifiers passed; scenarios 3, 10, and 11 were model-task failures. Prompt 11 reached 191,222 reported prompt tokens.

That run used an incorrect fallback context window of 1,048,576, showing 18.236%. Qwen3 Coder Next's documented window is 262,144, so the same prompt load was actually about 72.9% of the model window. The bad denominator was a Travis234 configuration defect that could delay auto-compaction and exacerbate low-quality repeated reads. The `.env` now binds the model explicitly to 262,144, and model-switch tests prove calibration reset. The repeated-read behavior itself was mixed: high context pressure was a harness/config factor; ignoring unchanged tool evidence was model behavior; stopping for recoverable loop guidance was a runtime-policy defect fixed by same-turn steering.

No auto-compaction was observed in the earlier run, so it cannot satisfy the acceptance row.

Artifacts: `artifacts/live-21-qwen-next-0975b3b/` (ignored, local evidence).

## StepFun probe

Exact prompt:

```text
SDLC scenario 01-python-cli-feature. Work only in scenarios/01-python-cli-feature. Add --format json while preserving text output; update help and tests. Run tests, repair edge cases, and align README examples.
```

Exact agent output:

```text
Error: stepfun authentication failed (HTTP 401) for model step-3.7-flash. Check the configured API key and re-authenticate if needed. Provider message: Incorrect API key provided
```

The direct StepFun credential was absent/invalid. This is `provider_authentication_failure`, not a model or runtime defect.

Artifacts: `artifacts/live-21-stepfun-direct-40e08b9/` (ignored, local evidence).

## Unblock condition

The acceptance run must be repeated from a fresh output directory after the OpenRouter account has usable credit for `qwen/qwen3-coder-next` or a valid direct StepFun credential is configured. It must continue beyond prompt 21 if necessary until one automatic compaction is observed, with prompt/output, context footer, verifier, session, process, and secret-leak evidence retained for every turn.
