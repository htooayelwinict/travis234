# Live Smoke Test Output Explained

Prompt under test:

```text
Here is the breakdown of how the ratio works for each section:
1. Weekly Usage (38%)
The Ratio: You have used 38% of your weekly allowance.
Visual: The blue bar fills up a little more than one-third of the total length.
Remaining: The dark grey part of the bar represents the 62% you have left to use for the rest of the week.
2. Monthly Usage (19%)
The Ratio: You have used 19% of your monthly allowance.
Visual: The blue bar fills up roughly one-fifth of the total length.
Remaining: The dark grey part of the bar represents the 81% you have left to use for the rest of the month.
```

This note explains every field from the latest live graph smoke test output.

## Envelope

`mode: llm_prompt_chain`

- Meaning: the decompressor used the LLM-based prompt-chain path instead of the deterministic fallback.
- Why it matters: the request was interpreted by the multi-stage LLM flow successfully.

`chain: {'mode': 'completed', 'stages': ['normalize_request', 'extract_artifacts', 'classify_request', 'infer_context_and_risk', 'recommend_planner'], 'fallback': None, 'redacted_prompt_input': False}`

- `mode: completed`
  - Meaning: all prompt-chain stages ran successfully.
- `stages: [...]`
  - Meaning: these are the exact stages that completed.
  - `normalize_request`: rewrites the raw prompt into a cleaner goal statement.
  - `extract_artifacts`: looks for files, paths, components, APIs, URLs, or other concrete targets.
  - `classify_request`: decides what kind of request it is, such as question, mutation, or research.
  - `infer_context_and_risk`: estimates missing context, execution hints, and risk labels.
  - `recommend_planner`: chooses the most suitable planner and budget level.
- `fallback: None`
  - Meaning: no fallback was needed.
- `redacted_prompt_input: False`
  - Meaning: the prompt did not contain text that matched the secret-redaction rules before sending it to the LLM.

`normalized_input: Explain the breakdown of how the ratio works for weekly and monthly usage sections.`

- Meaning: the decompressor rewrote the prompt into a short normalized request.
- Why it matters: downstream planner logic uses a cleaner, standardized summary instead of the full raw text.

`user_goal: Understand the visual and numerical representation of weekly and monthly usage allowances.`

- Meaning: the system's best interpretation of what the user is trying to achieve.
- Why it matters: this becomes the high-level objective for planning.

`input_type: request`

- Meaning: the prompt was treated as a general request, not a code mutation request, bug report, or research task.
- Why it matters: planner choice depends heavily on this field.

`intents: ['question.answer']`

- Meaning: the system classified the intent as asking for an explanation or answer.
- Why it matters: this steers the planner toward a direct response instead of using tools or editing code.

`domains: ['general']`

- Meaning: the request was classified as general-purpose content, not code, infra, docs, or data work.
- Why it matters: domain labels help route the request to the correct planner strategy.

`risks: []`

- Meaning: no execution or modification risks were identified.
- Why it matters: a no-risk request can stay on the cheap, direct-answer path.

`artifacts: []`

- Meaning: no specific file, symbol, path, service, API, or other concrete artifact was found.
- Why it matters: no repository lookup or targeted edit was implied by the prompt.

`context_needed: []`

- Meaning: the decompressor did not think extra repository or environment context was required.
- Why it matters: the system believes it can answer from the prompt alone.

`execution_hints: []`

- Meaning: no extra execution instructions were attached, such as inspecting files first or running verification.
- Why it matters: this supports the direct-answer path rather than a tool-using workflow.

`budget_hint: low`

- Meaning: the request looks cheap to solve.
- Why it matters: the planner should avoid tools, extra workers, or broad investigation.

`confidence: 0.9`

- Meaning: overall confidence in the decomposition result was high.
- Why it matters: the planner can rely on this classification without much defensive branching.

`ambiguity: ['The specific action required (e.g., summarize, format, implement in code) is not explicitly stated.']`

- Meaning: the decompressor noticed that the prompt contains content, but not an explicit action verb like explain, rewrite, implement, or patch.
- Why it matters: this is a useful warning even though the planner still chose a direct answer path.

`assumptions: ["The user is providing context about a dashboard's usage ratio visuals and wants to discuss or utilize this information."]`

- Meaning: the decompressor filled in one missing assumption about user intent.
- Why it matters: when the prompt is underspecified, assumptions help keep the flow moving, but they also mark where the interpretation may be wrong.

## Plan

`planner: direct`

- Meaning: the direct planner was selected.
- Why it matters: this planner is for requests that can be answered immediately without tools.

`strategy: direct_answer`

- Meaning: the chosen strategy is to answer directly.
- Why it matters: no file reads, code patches, or shell commands should be used.

`objective: Answer user question: Explain the breakdown of how the ratio works for weekly and monthly usage sections.`

- Meaning: the planner turned the envelope into a concrete objective statement.
- Why it matters: workers use this as the exact task target.

`budget: {'max_tool_calls': 0, 'max_model_calls': 1, 'max_workers': 1, 'max_retries': 0}`

- `max_tool_calls: 0`
  - Meaning: the plan should not use repository or shell tools.
- `max_model_calls: 1`
  - Meaning: only one model-driven response is needed after planning.
- `max_workers: 1`
  - Meaning: one worker step is enough.
- `max_retries: 0`
  - Meaning: no retry loop is budgeted for this plan.

## Plan Steps

`step: direct_answer direct_worker {'read_files': False, 'write_files': False, 'run_commands': False}`

- `step_id: direct_answer`
  - Meaning: identifier for the only plan step.
- `worker_type: direct_worker`
  - Meaning: the worker is a direct-answer worker, not a repo-reading or code-writing worker.
- `permissions`
  - `read_files: False`
    - Meaning: this step should not inspect files.
  - `write_files: False`
    - Meaning: this step should not modify files.
  - `run_commands: False`
    - Meaning: this step should not invoke shell commands.

`instruction: Provide a concise direct answer without using tools.`

- Meaning: the exact worker instruction produced by the planner.
- Why it matters: this is the concrete execution directive.

## Result

`status: completed`

- Meaning: the worker kernel finished the plan without error.
- Important caveat: in the current Phase 1 runtime, workers are still mostly stubbed, so `completed` means the planned execution path ran, not necessarily that a user-visible answer was generated by a full production worker.

`summary: Plan executed successfully.`

- Meaning: generic success summary from the worker kernel.
- Why it matters: this confirms the plan was accepted and executed end-to-end.

`artifacts: [{'id': 'direct_answer', 'content': 'Provide a concise direct answer without using tools.'}]`

- Meaning: the stub worker returned an artifact representing the direct-answer step.
- Important caveat: this is not the final natural-language explanation itself. It is the execution artifact emitted by the current scaffolded runtime.

`errors: []`

- Meaning: no execution errors were recorded.
- Why it matters: no planner failure, schema failure, or worker failure occurred in this run.

## What This Output Tells Us

- The decompressor is working correctly for plain explanatory prompts.
- The planner can correctly avoid file and command work using semantic fields alone when no implementation request is present.
- The runtime still needs real worker behavior if we want the final `result.artifacts` to contain the actual answer text rather than a stub instruction.

## Why This Prompt Did Not Route To Code Or UI Work

- The prompt contains descriptive content about percentages and progress bars.
- It does not explicitly ask to update a component, edit a file, or implement a UI.
- Because of that, the system interpreted it as an explanation request instead of a build request.
