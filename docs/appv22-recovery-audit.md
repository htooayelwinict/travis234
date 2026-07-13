# appv22 Recovery Audit

## 1. Current State Summary

No files were edited during the audit.

`appv22` already exists as a real Python app under `appV2.2/appv22`, with these main areas:

- `agent/`: Pi-style agent loop, event types, iteration budget, tool guardrails.
- `ai/`: provider registry, env/model resolution, stream/event models, OpenAI/OpenRouter-compatible provider, faux provider.
- `coding_agent/`: session orchestration, tools, resource loading, prompts, extensions, session store, bash executor.
- `compaction/`: Hermes-style compression and timing.
- `tui/`: actual terminal UI stack, not just JSON bridge.
- `scripts/appv22_tui.py`: runtime entrypoint.

Current verified entrypoint:

```bash
.venv/bin/python appV2.2/scripts/appv22_tui.py --dotenv .env --cwd <existing-dir>
```

Current tests:

```bash
PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -q
```

Result:

```text
536 passed
```

Post-audit fix already applied:

- Commit: `cdfbee6 appv22 stop redundant compaction rewrites`
- PR: `https://github.com/htooayelwinict/allthebest/pull/18`
- Root cause fixed: after a first `/compact` inserted a fallback summary, an immediate second `/compact` could treat the existing summary as the whole compressible middle window, call the LLM again, and accept a larger rewritten summary.
- Fix: `ContextCompressor.compress()` now no-ops before the LLM call when the existing compaction summary consumes the whole compressible window and there are no new turns to incorporate.
- Overflow recovery adjustment: if forced overflow compression is a no-op because the transcript is already compacted, appv22 can still retry once with that already-compacted transcript.
- Validation after fix: `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests -q` -> `536 passed`.

Pi concepts already ported:

- `packages/agent`: agent loop, events, tool calls, iteration budget.
- `packages/ai`: model/provider abstractions, stream events, validation, overflow handling, faux provider.
- `packages/coding-agent`: session, tools, resource loader, system prompt, settings/session pieces, extensions.
- `packages/tui`: terminal input/rendering/keybinding pieces.

Hermes concepts already exist:

- Deterministic pruning: old tool-result pruning, duplicate-output pruning, arg truncation, media stripping.
- LLM summary compaction: summary message, fallback marker, prior-summary handling, and redundant-summary rewrite guard.
- Timing manager: preflight, post-response, overflow recovery, manual compression.
- Guardrails around repeated no-progress tool calls.

Files that look partial, duplicated, dead, or confused:

- `appV2.2/appv22/coding_agent/agent_session.py`: too much mixed responsibility; should be stabilized, not expanded.
- `appV2.2/appv22/coding_agent/extensions.py`: large optional Pi surface, not required for the spine.
- `appV2.2/appv22/coding_agent/export_html.py`: optional export feature, not core.
- `appV2.2/appv22/coding_agent/system_prompt.py`: still Pi-branded and may be encouraging bash-heavy behavior.
- `appV2.2/appv22/tui/*`: broad UI port exists, but UX/control behavior is still risky.
- Untracked scratch/reference items: `appV2.2/bot/`, `appV2.2/test_write.txt`, `appV2.2/test_write2.txt`, `pi/`, `hermes-agent/`.

## 2. Target Spine

Minimum working vertical slice:

```text
Input request
-> session/context load
-> tool/model call loop placeholder
-> deterministic compaction pass
-> LLM summary compaction pass placeholder
-> final response/state save
```

Out of scope for this spine:

- Optional Pi extensions.
- Themes.
- Export HTML.
- Advanced TUI polish.
- Multi-provider matrix.
- Plugin/package discovery beyond necessary context.
- Extra worker abstractions.

## 3. Gap Map

| Component | Status | Source | Required next action | File path to touch | Risk |
|---|---:|---|---|---|---:|
| CLI entrypoint | done | appv22 native | Keep stable, test only | `appV2.2/appv22/cli.py` | Low |
| App composition spine | partial | appv22 + Hermes | Add explicit spine contract test | `appV2.2/appv22/app.py` | Medium |
| Session/context load/save | partial | Pi coding-agent | Verify minimal persistence path | `appV2.2/appv22/coding_agent/session_store.py` | Medium |
| Agent loop | done/partial | Pi agent | Freeze unless regression proves bug | `appV2.2/appv22/agent/agent_loop.py` | Medium |
| Tool loop guardrails | partial | appv22/Hermes-inspired | Test against repeated scan/read loops | `appV2.2/appv22/agent/tool_guardrails.py` | High |
| Model provider | partial | Pi AI | Keep one provider spine; avoid full matrix now | `appV2.2/appv22/ai/providers/appv2_env.py` | Medium |
| Deterministic compaction | done | Hermes | Preserve behavior, add contract coverage | `appV2.2/appv22/compaction/compressor.py` | Medium |
| LLM summary compaction | partial/done | Hermes | Guard landed for no-op re-compact after fallback; still verify fallback never traps UI/status | `appV2.2/appv22/compaction/compressor.py` | High |
| Compaction timing | partial | Hermes | Overflow retry with already-compacted transcript landed; still compare one-pass vs bounded multi-pass behavior | `appV2.2/appv22/compaction/timing.py` | High |
| TUI runtime controls | broken/partial | Pi TUI | Stabilize Ctrl-C/Esc/status only | `appV2.2/appv22/tui/interactive_mode.py` | High |
| Prompt/tool instructions | confused | Pi coding-agent | Decide exact Pi vs appv22 identity, then patch narrowly | `appV2.2/appv22/coding_agent/system_prompt.py` | High |
| Extensions/themes/export | duplicate/optional | Pi coding-agent | Freeze; do not extend | `appV2.2/appv22/coding_agent/extensions.py` | Medium |

## 4. Kill List

Do not extend these further during recovery:

- `appV2.2/appv22/coding_agent/export_html.py`
- `appV2.2/appv22/coding_agent/export_html_assets/*`
- `appV2.2/appv22/coding_agent/extensions.py`
- `appV2.2/appv22/coding_agent/agent_session_runtime.py`
- `appV2.2/appv22/coding_agent/agent_session_services.py`
- `appV2.2/appv22/coding_agent/auth_storage.py`
- `appV2.2/appv22/coding_agent/model_registry.py`
- `appV2.2/appv22/coding_agent/settings_manager.py`
- Broad `appV2.2/appv22/tui/*` work, except targeted abort/status/input fixes.
- Untracked scratch files under `appV2.2/test_write*.txt`.
- Reference repos `pi/` and `hermes-agent/` as runtime dependencies.

## 5. Recovery Plan

1. Add one spine smoke contract.
   - Touch max: `appV2.2/tests/test_app_integration.py`
   - Artifact: regression test proving request -> session -> loop -> compaction -> response.
   - Test: `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_app_integration.py -q`

2. Lock Hermes compaction timing behavior.
   - Touch max: `compaction/timing.py`, `tests/test_compaction_timing.py`
   - Artifact: bounded preflight/post-response/manual compression contract.
   - Test: `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_compaction_timing.py -q`

3. Stabilize provider failure recovery.
   - Touch max: `ai/providers/appv2_env.py`, `tests/test_ai_appv2_env_provider.py`
   - Artifact: 403/streaming-error test that never leaves background thread stuck.
   - Test: `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_ai_appv2_env_provider.py -q`

4. Stabilize TUI abort/status only.
   - Touch max: `tui/interactive_mode.py`, `tui/terminal.py`, `tests/test_tui.py`
   - Artifact: Ctrl-C/Esc/status regression coverage.
   - Test: `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_tui.py -q`

5. Narrow prompt/tool-scan behavior.
   - Touch max: `coding_agent/system_prompt.py`, `agent/tool_guardrails.py`, `tests/test_coding_agent.py`
   - Artifact: regression for "read all Python files" avoiding endless bash/read loops.
   - Test: `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_coding_agent.py -q`

## 6. NEXT_PATCH

NEXT_PATCH:

- name: `spine-smoke-contract`
- purpose: Add a no-network regression test that defines the minimum appv22 vertical slice before more porting.
- allowed_files: `appV2.2/tests/test_app_integration.py`
- forbidden_files: production code, `tui/*`, `coding_agent/extensions.py`, `coding_agent/export_html.py`, `pi/*`, `hermes-agent/*`, untracked scratch files.
- exact_changes: Add one integration test that runs `CodingApp` with a faux/model stub and asserts final assistant output exists, session messages persist, deterministic compaction can run, and LLM summary compaction fallback/placeholder path does not block completion.
- test_command: `PYTHONPATH=appV2.2 .venv/bin/python -m pytest appV2.2/tests/test_app_integration.py -q`
- success_criteria: Test passes without network, without OpenRouter credentials, and without touching production code.
- stop_condition: If the spine cannot be tested without production changes, stop and report the missing seam instead of patching around it.
