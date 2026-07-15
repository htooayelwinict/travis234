# Read-only Pi/Hermes cross-check report

Honest verdict: Travis234 is not an exact Pi/Hermes port, and I would not approve unrestricted corporate rollout yet—even with a SOTA model. The core agent loop and provider transports are strong. The main blockers are project-extension trust and several context/compaction accounting defects.

No files were modified during the audit.

## Baselines

- Travis: `f1032e6a552f4346291d0542936921a0e02f01fc`
- Pi: `1f0dbc008c9b3e88017d42e8a1b46d416ad2b6b6`
- Hermes: `af250d84948179834820a62bfd870c0df6f264a1`
- `appv231/`: historical cross-fit reference only

| Layer | Assessment |
|---|---|
| Core agent loop | Strong semantic Pi parity |
| Provider transports | Strong Pi parity across all nine chat APIs |
| Model catalog | Same IDs, but important OpenRouter limit drift |
| Coding tools | All seven Pi tools ported; Travis adds managed processes |
| System prompt | Close branded Pi port |
| Session persistence | Pi JSONL v3 compatible, with useful SQLite improvements |
| Extensions | Partial parity; trust and lifecycle defects remain |
| CLI/TUI | Functional but significantly narrower than Pi |
| Compaction | Genuine dual-pass Hermes design, but not current-Hermes parity |

## High findings

### 1. OpenRouter context limits do not follow Pi’s routing contract

Offline comparison found:

- 1,057 Pi model IDs
- 1,057 Travis model IDs
- No missing or extra IDs
- 40 model records with limit differences
- 35 context-window differences
- 7 maximum-output differences

Pi prioritizes `top_provider.context_length`; Travis uses model-level `context_length`. Compare [`pi/packages/ai/scripts/generate-models.ts:726`](pi/packages/ai/scripts/generate-models.ts#L726) with [`travis/ai/catalog_generation.py:31`](travis/ai/catalog_generation.py#L31).

For `openrouter/xiaomi/mimo-v2.5`:

- Pi route capacity: 32,000
- Travis: 1,048,576
- Pi-compatible 50% trigger after reserving 4,096 output: 13,952
- Travis trigger: 522,240

That is approximately 37× later.

This does not claim MiMo’s architecture lacks a 1M window. It means Travis disregards the route-specific capacity Pi uses for OpenRouter. The incorrect divergence is explicitly locked into [`tests/test_reference_runtime_contract.py:342`](tests/test_reference_runtime_contract.py#L342).

This can directly explain sessions reaching provider limits or becoming incoherent before auto-compaction.

### 2. Project extensions are trusted and executed by default

Travis resolves an unknown trust decision to `True` at [`travis/coding_agent/resource_loader.py:236`](travis/coding_agent/resource_loader.py#L236), then executes project Python using `exec(compile(...))` at [`travis/coding_agent/resource_loader.py:450`](travis/coding_agent/resource_loader.py#L450).

It also reports `isProjectTrusted` as unconditionally true to extensions at [`travis/coding_agent/session_extensions.py:493`](travis/coding_agent/session_extensions.py#L493).

Pi instead:

- Detects trust-requiring project resources
- Consults stored decisions
- Prompts in an interactive UI
- Defaults to untrusted when no UI exists
- Supports `--approve` and `--no-approve`

See [`pi/packages/coding-agent/src/core/project-trust.ts:46`](pi/packages/coding-agent/src/core/project-trust.ts#L46).

A repository containing `.travis234/extensions` can therefore execute code under the developer’s credentials simply by opening it. This is a corporate deployment blocker.

### 3. Context accounting has multiple inconsistent authorities

Travis has three materially different token-accounting paths:

- Compaction uses the text-oriented `estimate_tokens()` at [`travis/compaction/compressor.py:253`](travis/compaction/compressor.py#L253).
- Provider output reservation uses the more complete estimator in [`travis/ai/context_estimate.py:37`](travis/ai/context_estimate.py#L37).
- TUI/session context uses another implementation in [`travis/coding_agent/session_persistence.py:112`](travis/coding_agent/session_persistence.py#L112).

Consequences:

- Preflight compaction primarily sees message text, not the complete system-prompt/tool-schema request.
- Post-compaction display initially uses a message-only estimate.
- The next provider response reports a much larger envelope.
- `_assistant_prompt_tokens()` includes output/reasoning totals at [`travis/app.py:723`](travis/app.py#L723), even though generated output does not occupy the next request’s input context.
- Tail budgeting does not consistently count provider replay envelopes, signatures, and full serialized tool-call structures.

Hermes uses full-request estimates for pressure decisions at [`hermes-agent/agent/conversation_loop.py:959`](hermes-agent/agent/conversation_loop.py#L959), uses prompt-only real usage at [`hermes-agent/agent/conversation_loop.py:4769`](hermes-agent/agent/conversation_loop.py#L4769), and counts replay/tool envelopes in [`hermes-agent/agent/context_compressor.py:325`](hermes-agent/agent/context_compressor.py#L325).

This is the primary explanation for the apparent “small prompt suddenly consumed half the context” spike: most of that envelope already existed but was excluded from the first estimate.

### 4. Merged compaction summaries contaminate subsequent compactions

In the role-collision path, Travis prepends the summary and end marker to the first retained tail message at [`travis/compaction/compressor.py:973`](travis/compaction/compressor.py#L973).

On rehydration, `_strip_summary_prefix()` only removes the end marker when it is at the very end at [`travis/compaction/compressor.py:1022`](travis/compaction/compressor.py#L1022). In a merged message, retained tail content follows the marker.

The offline reproduction returned:

```text
body_has_end_marker=True
body_has_latest_tail=True
body='SUMMARY BODY\n\n--- END OF CONTEXT SUMMARY ...\n\nLATEST USER TASK'
```

Therefore, the next iterative summarizer receives the end marker and retained user text as if they belonged to the previous summary. This is a concrete context-poisoning path.

Current Hermes separates prior content and summary with explicit delimiters and strips them during rehydration at [`hermes-agent/agent/context_compressor.py:2239`](hermes-agent/agent/context_compressor.py#L2239) and [`hermes-agent/agent/context_compressor.py:3163`](hermes-agent/agent/context_compressor.py#L3163).

Travis has a merge test, but it checks only initial assembly—not subsequent rehydration: [`tests/test_compaction.py:574`](tests/test_compaction.py#L574).

### 5. Early conversation turns are permanently fossilized

Travis always protects the system message plus `protect_first_n`, defaulting to three, at [`travis/compaction/compressor.py:877`](travis/compaction/compressor.py#L877).

Current Hermes protects those messages only during the first compaction. After a previous summary exists, protection decays to zero at [`hermes-agent/agent/context_compressor.py:2431`](hermes-agent/agent/context_compressor.py#L2431).

Travis therefore keeps up to three early non-system messages permanently. If they contain an obsolete task or constraint, that stale material remains raw and authoritative-looking through every later compaction.

### 6. Summary failure cooldown does not prevent repeated compaction

Travis’s compressor owns a summary cooldown, but `should_compress()` ignores it at [`travis/compaction/compressor.py:557`](travis/compaction/compressor.py#L557).

`CompactionManager` owns a separate cooldown at [`travis/compaction/timing.py:133`](travis/compaction/timing.py#L133), but it is set only when `compress()` raises. Normal summarizer failures are caught inside `compress()`, so the manager never observes them.

The existing test explicitly confirms that two compression calls during cooldown both rewrite the transcript using fallback context, while only the LLM retry is skipped: [`tests/test_compaction_timing.py:341`](tests/test_compaction_timing.py#L341).

Hermes prevents `should_compress()` from firing during summary cooldown at [`hermes-agent/agent/context_compressor.py:1278`](hermes-agent/agent/context_compressor.py#L1278) and persists cooldown state across session resumes.

This can produce repeated compaction activity, confusing fallback summaries, and apparent loops.

### 7. The auxiliary summarizer’s context capacity is not calibrated

Travis configures a separate compaction model but does not verify that its context window can accept the middle transcript being summarized.

Hermes:

- Resolves the auxiliary model’s own context capacity
- Rejects models below its minimum
- Adjusts the live compaction threshold when the summarizer is smaller than the main model

See [`hermes-agent/agent/conversation_compression.py:236`](hermes-agent/agent/conversation_compression.py#L236).

Travis’s summarizer correctly omits a hard output cap at [`travis/app.py:654`](travis/app.py#L654), but it lacks the corresponding input-capacity calibration. A large-context main model paired with a smaller summary model can repeatedly overflow the summary call.

## Medium findings

### 8. Threshold policy differs, but the larger defect is disconnected configuration

Current Travis uses:

- Trigger: `50% × (context window − max output)`
- Tail target: 20% of trigger
- Tail soft ceiling: 1.5× the tail target

For 128K context and 8,192 output:

| Policy | Trigger | Tail target | Soft ceiling |
|---|---:|---:|---:|
| Travis | 59,904 | 11,980 | 17,970 |
| Hermes | 89,856 | 17,971 | 26,956 |
| Pi | 111,616 | 20,000 | — |

Hermes raises models below 512K to at least 75%, includes a 64K floor and an 85% small-window fallback. At 512K and above, both generic policies use 50%.

The 50% threshold was an explicit Travis policy choice, so I classify it as intentional drift—not automatically a defect. The defect is that Pi-style `reserveTokens` and `keepRecentTokens` remain exposed in [`travis/coding_agent/settings_manager.py:329`](travis/coding_agent/settings_manager.py#L329) but have no active consumer.

### 9. Failure fallback repeats stale task text

Travis’s deterministic fallback places the same recovered user ask into:

- Historical task
- Historical in-progress state
- Historical pending asks
- Remaining-work guidance

See [`travis/compaction/compressor.py:1413`](travis/compaction/compressor.py#L1413).

That conflicts with the surrounding “reference only” framing and is risky for weaker models. Current Hermes records the ask once and explicitly says it is not necessarily outstanding at [`hermes-agent/agent/context_compressor.py:1733`](hermes-agent/agent/context_compressor.py#L1733).

Travis also lacks Hermes’s automatic recent-user focus derivation at [`hermes-agent/agent/context_compressor.py:2286`](hermes-agent/agent/context_compressor.py#L2286).

### 10. Extension lifecycle parity is 29 of 33 events

The missing actual extension emissions are:

- `project_trust`
- `session_info_changed`
- `model_select`
- `thinking_level_select`

Pi defines the complete event surface at [`pi/packages/coding-agent/src/core/extensions/types.ts:1165`](pi/packages/coding-agent/src/core/extensions/types.ts#L1165).

Travis has an internal TUI `session_info_changed` event, but it is not emitted through the extension runner. Extensions relying on these four hooks silently cannot implement equivalent behavior.

### 11. The extension event bus is constructed but disconnected

`DefaultResourceLoader` creates `self.event_bus` at [`travis/coding_agent/resource_loader.py:211`](travis/coding_agent/resource_loader.py#L211), but creates each `ExtensionRunner` without passing it at [`travis/coding_agent/resource_loader.py:404`](travis/coding_agent/resource_loader.py#L404).

Pi explicitly passes the shared bus into extension loading at [`pi/packages/coding-agent/src/core/extensions/loader.ts:367`](pi/packages/coding-agent/src/core/extensions/loader.ts#L367).

Therefore, Pi-style extension-to-extension event communication is not operational in Travis.

### 12. Several resource surfaces load successfully but are not usable

- Prompt templates are loaded and exposed as a property, but no invocation or `/template` expansion path consumes them.
- `enableSkillCommands` is persisted but has no command-expansion consumer.
- Themes are loaded but never registered with the TUI.
- `/reload` claims to reload prompts and themes even though those resources do not affect user behavior.

Pi performs prompt/skill command expansion and registers discovered themes in interactive mode.

### 13. Skill/frontmatter compatibility is incomplete

Travis’s frontmatter parser is a manual `key: value` splitter at [`travis/coding_agent/resource_loader.py:809`](travis/coding_agent/resource_loader.py#L809). Pi uses a real YAML parser.

Consequences include incomplete support for arrays, multiline values, nested data, quoting, and YAML syntax.

Travis also:

- Ignores `.gitignore`, `.ignore`, and `.fdignore` during skill discovery
- Does not enforce Pi’s 1,024-character description limit

Name validation itself is correctly present.

### 14. Extension package management is far narrower than Pi

Pi’s package manager supports npm and Git install, remove, update, configured sources, scopes, version checks, and ignore handling. Travis’s `DefaultPackageManager` only resolves already-present local resources.

JavaScript Pi extensions cannot execute directly; they must be ported to Python. That is a legitimate language boundary, but it means Travis is not ecosystem-compatible with Pi’s extension packages.

Other differences:

- No entry renderer equivalent
- Duplicate command names overwrite earlier registrations; Pi generates `name:1`, `name:2`
- No general extension-defined CLI flags
- Hypa is an optional Python adapter with local tests, not proof of arbitrary Pi extension compatibility

### 15. CLI/TUI product parity is incomplete

Travis has a usable interactive experience, plus managed processes, delegation, and deep compaction. However, compared with Pi it lacks important product surfaces:

- Text, JSON, and RPC modes
- Tool allow/deny controls
- Trust flags and `/trust`
- Session naming/fork/clone/tree controls
- Arbitrary extension, skill, template, and theme paths
- `@file` and image initial arguments
- Offline startup mode
- Extension-defined flags
- General package install/remove/update
- Several export/import/share/copy/hotkey commands

Compare [`travis/cli.py:291`](travis/cli.py#L291) with [`pi/packages/coding-agent/src/cli/args.ts:250`](pi/packages/coding-agent/src/cli/args.ts#L250).

### 16. Public SDK parity is incomplete

Travis lacks Pi’s newer generic `AgentHarness`, which unifies resources, compaction, branch summaries, session navigation, prompt templates, skills, hooks, and stream options.

It also lacks:

- Pi’s `streamProxy`
- Pi’s image-generation model/API layer
- An asynchronous `Models` API

Travis’s sync `Models` implementation settles awaitables using `asyncio.run()` at [`travis/ai/models.py:483`](travis/ai/models.py#L483), which fails if invoked from an already-running Python event loop.

These are SDK and integration gaps, not core TUI-loop failures.

## Areas that align well

### Core loop

The core red zone is a good semantic port:

- Outer follow-up and inner steering loops
- Ordered turn/message/tool events
- Sequential-tool handling
- Parallel preparation and bounded concurrent execution
- Source-order tool-result persistence
- Before/after tool hooks
- Pending-call completion after truncated assistant output
- Termination only when every finalized tool result requests termination

Compare [`pi/packages/agent/src/agent-loop.ts:169`](pi/packages/agent/src/agent-loop.ts#L169) and [`travis/agent/agent_loop.py:186`](travis/agent/agent_loop.py#L186).

Travis also adds useful protections:

- Maximum eight parallel tool executions by default
- Run leases preventing unsafe reset during an active turn
- Better stream abort handling
- Sync and async Python entry points

I found no evidence that the core loop itself caused the repeated read behavior.

### Provider transport

Travis supports all nine Pi chat APIs:

- OpenAI completions
- OpenAI responses
- Azure responses
- Codex responses
- Anthropic messages
- Bedrock Converse
- Google Generative AI
- Google Vertex
- Mistral Conversations

Provider conversion correctly omits aborted/error assistant messages and reconstructs tool pairs at [`travis/ai/providers/message_translation.py:151`](travis/ai/providers/message_translation.py#L151).

There is no evidence of a general transport-layer tool-call corruption bug. The major provider-related smoking gun is model metadata and context accounting, not streaming conversion.

### System prompt

The default system prompt is a close, branded mechanical port of Pi’s prompt. Compare [`pi/packages/coding-agent/src/core/system-prompt.ts:28`](pi/packages/coding-agent/src/core/system-prompt.ts#L28) and [`travis/coding_agent/system_prompt.py:32`](travis/coding_agent/system_prompt.py#L32).

Travis’s conditional documentation listing is safer for installed wheels because it does not advertise nonexistent files.

### Tools and processes

All seven Pi tools are present:

- read
- bash
- edit
- write
- grep
- find
- ls

Travis adds a managed `process` tool with background sessions, stdin, polling, timeouts, interrupt escalation, and cleanup.

Previously reported process findings appear corrected:

- Monitor failure reaps the live transport before publishing failure: [`travis/coding_agent/processes/service.py:706`](travis/coding_agent/processes/service.py#L706)
- Stdin write waits for acknowledgement and surfaces pump failure: [`travis/coding_agent/processes/service.py:284`](travis/coding_agent/processes/service.py#L284)
- Repeated Ctrl-C escalates: [`travis/tui/user_commands.py:165`](travis/tui/user_commands.py#L165)
- Shutdown joins are bounded: [`travis/coding_agent/session_commands.py:61`](travis/coding_agent/session_commands.py#L61)

The old broad bash mutation guardrail is no longer in the active Travis core.

### Sessions and persistence

Travis represents all Pi JSONL v3 entry types and preserves:

- Compaction summary
- `firstKeptEntryId`
- `tokensBefore`
- Model and thinking changes
- Branch summaries
- Session names, labels, and custom entries

It adds file locking, truncated-tail quarantine, and a SQLite session index. Therefore, the former O(total-history) session discovery problem appears resolved.

Existing Pi-format sessions should not require migration for compaction corrections.

## What appv231 tells us

`appv231` is useful as a historical bridge, but should not be copied as a source of truth.

It carries several older defects still visible in Travis:

- Text-heavy token estimation
- Permanent first-message protection
- The merged-summary rehydration problem
- Split cooldown ownership

It also used a hybrid Pi-reserve/static-prompt threshold that current Travis removed. Current Travis has improved auth/network failure preservation, reasoning stripping, dedicated summary-model reporting, and model-switch recalibration.

No active Travis source imports `appv231`; it remains an untracked local reference tree.

## Root-cause conclusion for the observed loops

The most credible harness-side causes are:

1. Incorrect OpenRouter route capacity delaying compaction.
2. Message-only preflight and post-compaction estimates hiding the real request envelope.
3. Total-token accounting making the next provider reading appear larger than the actual next input.
4. Permanent stale head messages.
5. Merged-summary carry-forward contamination.
6. Failure fallback repeating historical asks as pending work.
7. Repeated compaction during summary cooldown.

A lower-quality model can still independently fall into read/reason loops. But the current harness can amplify that behavior by preserving stale or misleading context. I found no evidence that provider streaming generally malformed tool calls, and no active strict guardrail explains the loops.

## Verification

Previously completed at the same Travis revision:

- Python: 1,361 passed, 4 failed
- npm: 20 passed
- Offline merged-summary reproduction: confirmed contamination
- No live provider/API calls
- No paid TUI scenario
- No modifications during the audit

The four Python failures were repository/README hygiene failures:

- Untracked `appv231/`
- `.gitignore` mentions local Pi/Hermes reference clones
- Two exact README wording assertions

No runtime/provider/compaction functional test failed, but important defects are currently untested or explicitly locked in by tests.

Final worktree at the end of the audit was:

```text
## main...origin/main
 M .gitignore
?? appv231/
```

Those changes were pre-existing; the audit changed nothing.

## Production verdict

Travis234 is suitable for controlled internal evaluation on explicitly trusted repositories. It is not yet ready for a company-wide default rollout.

A SOTA model improves reasoning quality but cannot correct:

- Automatic execution of untrusted repository extensions
- Incorrect context-window metadata
- Incomplete request accounting
- Compaction rehydration contamination
- Missing corporate automation and extension surfaces

The core loop does not need replacement. The eventual corrective scope should be surgical: trust resolution, one canonical request estimator, OpenRouter route limits, and current-Hermes head/merge/cooldown behavior.

Audit goal usage: 964,011 tokens over approximately 37 minutes.
