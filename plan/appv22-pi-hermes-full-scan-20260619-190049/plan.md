# appv22 Pi Hermes Compliance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port appv22 to the Pi 3 package agent/AI/coding-agent patterns and Hermes dual-pass timing-compaction design, directly in Python code under `appV2.2/appv22`.

**Architecture:** Treat `pi/` and `hermes-agent/` as read-only specs. Each port adds a narrow regression against appv22 behavior first, then implements the smallest Python equivalent in the matching appv22 module. Remove or replace appv22-only behavior when it diverges from the reference model.

**Tech Stack:** Python appv22 package, pytest, local TypeScript/Python reference source under `pi/` and `hermes-agent/`.

---

## Current Status

- [x] TUI entry now launches a user-facing interactive loop instead of only a JSON/RPC bridge.
- [x] `Agent.prompt()` and `Agent.continue_()` reject concurrent runs, matching Pi `activeRun` semantics.
- [x] Full appv22 suite rerun after the run-lifecycle changes: `83 passed`.
- [x] Full appv22 suite rerun after stream option propagation: `84 passed`.
- [x] Full appv22 suite rerun after queued `continue_()` parity: `86 passed`.
- [x] Full appv22 suite rerun after listener/idle parity: `88 passed`.
- [x] Full appv22 suite rerun after failure lifecycle parity: `89 passed`.
- [x] Full appv22 suite rerun after provider/session option parity: `90 passed`.
- [x] Full appv22 suite rerun after public queue API parity: `91 passed`.
- [x] Full appv22 suite rerun after tool mutation-queue parity: `92 passed`.
- [x] Full appv22 suite rerun after edit multi-edit schema parity: `93 passed`.
- [x] Full appv22 suite rerun after bash status parity: `94 passed`.
- [x] Full appv22 suite rerun after bash output accumulator parity: `95 passed`.
- [x] Full appv22 suite rerun after read image handling parity: `96 passed`.
- [x] Full appv22 suite rerun after Hermes tool-pair boundary/sanitization parity: `98 passed`.
- [x] Full appv22 suite rerun after Hermes latest user/assistant tail anchoring parity: `100 passed`.
- [x] Full appv22 suite rerun after Hermes historical image stripping parity: `101 passed`.
- [x] Full appv22 suite rerun after Hermes summary role/merge/end-marker parity: `102 passed`.
- [x] Full appv22 suite rerun after Hermes summary-prefix rehydration parity: `103 passed`.
- [x] Full appv22 suite rerun after Hermes protected system-head sizing parity: `105 passed`.
- [x] Full appv22 suite rerun after Hermes protected-tail bounded floor parity: `106 passed`.
- [x] Full appv22 suite rerun after Hermes summary prompt safety/redaction parity: `108 passed`.
- [x] Full appv22 suite rerun after Hermes focused manual compression parity: `109 passed`.
- [x] Full appv22 suite rerun after Hermes summary failure fallback/bookkeeping parity: `112 passed`.
- [x] Full appv22 suite rerun after Hermes summary-model fallback parity: `113 passed`.
- [x] Full appv22 suite rerun after Hermes real-usage preflight deferral parity: `115 passed`.
- [x] Full appv22 suite rerun after Hermes compressor-owned summary cooldown parity: `117 passed`.
- [x] Full appv22 suite rerun after Hermes session lineage persistence parity: `118 passed`.
- [x] Full appv22 suite rerun after Hermes manual compression feedback parity: `120 passed`.
- [x] Full appv22 suite rerun after Pi agent-loop update-drain and `prepareNextTurn` signal parity: `122 passed`.
- [x] Full appv22 suite rerun after Pi agent-loop emit settlement and terminate coverage: `125 passed`.
- [x] Full appv22 suite rerun after Pi agent-loop prepare snapshot parity: `127 passed`.
- [x] Full appv22 suite rerun after coding-agent definition-first tool registry parity: `129 passed`.
- [x] Full appv22 suite rerun after coding-agent session queue update events parity: `130 passed`.
- [x] Full appv22 suite rerun after coding-agent prompt streaming-behavior preflight parity: `131 passed`.
- [x] Full appv22 suite rerun after write tool operation/abort queue parity: `132 passed`.
- [x] Full appv22 suite rerun after find/grep/ls/path/truncation parity: `138 passed`.
- [x] Full appv22 suite rerun after bash operations/streaming/prefix/abort parity: `141 passed`.
- [x] Full appv22 suite rerun after read operations/image/render parity: `146 passed`.
- [x] Full appv22 suite rerun after coding-agent session event/retry/compaction parity: `151 passed`.
- [x] Full appv22 suite rerun after coding-agent session persistence/branching parity: `153 passed`.
- [x] Full appv22 suite rerun after TUI/rendering component parity: `157 passed`.
- [x] Full appv22 suite rerun after TUI compaction visibility and transcript ordering follow-up: `160 passed`.
- [x] Full appv22 suite rerun after raw tool-argument render hardening follow-up: `162 passed`.
- [x] Full appv22 suite rerun after ToolInfo source metadata and TUI viewport follow-up: `164 passed`.
- [x] Full appv22 suite rerun after extension-registered tool refresh follow-up: `165 passed`.
- [x] Full appv22 suite rerun after extension lifecycle handler follow-up: `167 passed`.
- [x] Full appv22 suite rerun after resource-loader reload/context follow-up: `169 passed`.
- [x] Full appv22 suite rerun after TUI `/compact` local command routing follow-up: `170 passed`.
- [x] Full appv22 suite rerun after runtime-host new/switch/dispose follow-up: `171 passed`.
- [x] Full appv22 suite rerun after runtime-host fork follow-up: `172 passed`.
- [x] Full appv22 suite rerun after runtime-host import follow-up: `173 passed`.
- [x] Full appv22 suite rerun after tree navigation/session summary follow-up: `175 passed`.
- [x] Full appv22 suite rerun after custom session entries follow-up: `177 passed`.
- [x] Full appv22 suite rerun after default branch-summary generation follow-up: `178 passed`.
- [x] Full appv22 suite rerun after package resource loading follow-up: `179 passed`.
- [x] Full appv22 suite rerun after AI env API-key fallback and JSON bridge removal follow-up: `179 passed`.
- [x] Full appv22 suite rerun after special-message rendering and compaction-summary reload follow-up: `182 passed`.
- [x] Full appv22 suite rerun after user/skill invocation rendering follow-up: `184 passed`.
- [x] Full appv22 suite rerun after bash execution message/rendering follow-up: `186 passed`.
- [x] Full appv22 suite rerun after interactive `!`/`!!` bash command routing follow-up: `188 passed`.
- [x] Full appv22 suite rerun after pending bash-message deferral follow-up: `190 passed`.
- [x] Full appv22 suite rerun after session-level bash abort/running-state follow-up: `191 passed`.
- [x] Full appv22 suite rerun after interactive `user_bash` extension interception follow-up: `193 passed`.
- [x] Full appv22 suite rerun after prompt `input` extension interception follow-up: `195 passed`.
- [x] Full appv22 suite rerun after extension `message_end` replacement follow-up: `197 passed`.
- [x] Full appv22 suite rerun after extension `tool_result` mutation follow-up: `199 passed`.
- [x] Full appv22 suite rerun after extension `before_agent_start` injection follow-up: `201 passed`.
- [x] Focused extension context-transform regressions after Pi `emitContext` follow-up: `10 passed, 64 deselected`.
- [x] Full appv22 suite rerun after extension `context` transform follow-up: `203 passed`.
- [x] Focused extension `tool_call` block regressions after Pi `emitToolCall` follow-up: `11 passed, 64 deselected`.
- [x] Full appv22 suite rerun after extension `tool_call` block follow-up: `204 passed`.
- [x] Focused provider extension hook regressions after Pi provider payload/response follow-up: `13 passed, 64 deselected`.
- [x] Full appv22 suite rerun after provider extension hook follow-up: `206 passed`.
- [x] Focused extension command dispatch regressions after Pi `registerCommand` follow-up: `15 passed, 64 deselected`.
- [x] Full appv22 suite rerun after extension command dispatch follow-up: `208 passed`.
- [x] Focused extension command queue-guard regressions after Pi `steer`/`followUp` follow-up: `17 passed, 63 deselected`.
- [x] Full appv22 suite rerun after extension command queue-guard follow-up: `209 passed`.
- [x] Focused extension flag regressions after Pi `registerFlag` follow-up: `12 passed, 69 deselected`.
- [x] Full appv22 suite rerun after extension flag follow-up: `210 passed`.
- [x] Focused extension message-renderer regressions after Pi `registerMessageRenderer` follow-up: `13 passed, 69 deselected`.
- [x] Full appv22 suite rerun after extension message-renderer follow-up: `211 passed`.
- [x] Focused extension shortcut regressions after Pi `registerShortcut` follow-up: `10 passed, 73 deselected`.
- [x] Full appv22 suite rerun after extension shortcut follow-up: `212 passed`.
- [x] Focused TUI extension message-renderer handoff regression: `1 passed`; related TUI subset: `10 passed, 20 deselected`.
- [x] Full appv22 suite rerun after TUI extension renderer handoff: `213 passed`.
- [x] Focused TUI extension shortcut dispatch regression: `1 passed`; related TUI subset: `11 passed, 20 deselected`.
- [x] Full appv22 suite rerun after TUI extension shortcut dispatch: `214 passed`.
- [x] Focused extension command context action regression: `1 passed`; related command subset: `5 passed, 79 deselected`.
- [x] Full appv22 suite rerun after extension command context actions: `215 passed`.
- [x] Focused extension command `sendUserMessage` regression: `1 passed`; related command subset: `6 passed, 79 deselected`.
- [x] Full appv22 suite rerun after extension command `sendUserMessage`: `216 passed`.
- [x] Focused extension command session/tool metadata regression: `1 passed`; related command subset: `7 passed, 79 deselected`.
- [x] Full appv22 suite rerun after extension command session/tool metadata: `217 passed`.
- [x] Focused extension command thinking-level regression: `1 passed`; related command subset: `8 passed, 79 deselected`.
- [x] Full appv22 suite rerun after extension command thinking-level: `218 passed`.
- [x] Focused extension command set-label regression: `1 passed`; related command subset: `9 passed, 79 deselected`.
- [x] Full appv22 suite rerun after extension command set-label: `219 passed`.
- [x] Focused extension command exec regression: `1 passed`; related command subset: `10 passed, 79 deselected`.
- [x] Full appv22 suite rerun after extension command exec: `220 passed`.
- [x] Focused extension command wait/compact regression: `1 passed`; related command subset: `11 passed, 79 deselected`.
- [x] Full appv22 suite rerun after extension command wait/compact: `221 passed`.
- [x] Focused extension command set-model regression: `1 passed`; related command subset: `12 passed, 79 deselected`.
- [x] Full appv22 suite rerun after extension command set-model: `222 passed`.
- [x] Focused extension command-time provider override regression: `1 passed`; related command subset: `13 passed, 79 deselected`.
- [x] Full appv22 suite rerun after extension provider override: `223 passed`.
- [x] Focused extension unregister provider override regression: `1 passed`; related command subset: `14 passed, 79 deselected`.
- [x] Full appv22 suite rerun after extension provider unregister restoration: `224 passed`.
- [x] Focused extension provider model cleanup regression: `1 passed`; related command subset: `14 passed, 80 deselected`.
- [x] Full appv22 suite rerun after extension provider model cleanup: `225 passed`.
- [x] Focused extension provider model restoration regression: `1 passed`; related command subset: `14 passed, 81 deselected`; AI/model subset: `5 passed`.
- [x] Full appv22 suite rerun after extension provider model restoration: `226 passed`.
- [x] Focused TUI extension status regression: `1 passed`; related TUI subset: `5 passed, 27 deselected`.
- [x] Full appv22 suite rerun after TUI extension status hook: `227 passed`.
- [x] Focused TUI extension working-message regression: `1 passed`; related TUI subset: `6 passed, 27 deselected`.
- [x] Full appv22 suite rerun after TUI extension working-message hook: `228 passed`.
- [x] Focused TUI extension working-visible regression: `1 passed`; related TUI subset: `7 passed, 27 deselected`.
- [x] Full appv22 suite rerun after TUI extension working-visible hook: `229 passed`.
- [x] Focused TUI extension working-indicator regression: `1 passed`; related TUI subset: `8 passed, 27 deselected`.
- [x] Full appv22 suite rerun after TUI extension working-indicator hook: `230 passed`.
- [x] Focused TUI extension input dialog regression: `1 passed`; related TUI subset: `9 passed, 27 deselected`.
- [x] Full appv22 suite rerun after TUI extension input hook: `231 passed`.
- [x] Focused TUI extension select dialog regression: `1 passed`; related TUI subset: `10 passed, 27 deselected`.
- [x] Full appv22 suite rerun after TUI extension select hook: `232 passed`.
- [x] Focused TUI extension confirm dialog regression: `1 passed`; related TUI subset: `11 passed, 27 deselected`.
- [x] Full appv22 suite rerun after TUI extension confirm hook: `233 passed`.
- [x] Focused TUI extension terminal-input listener regression: `1 passed`; related TUI subset: `12 passed, 27 deselected`.
- [x] Full appv22 suite rerun after TUI extension terminal-input hook: `234 passed`.
- [x] Focused TUI extension hidden-thinking-label regression: `1 passed`; related TUI subset: `14 passed, 26 deselected`.
- [x] Full appv22 suite rerun after TUI extension hidden-thinking-label hook: `235 passed`.
- [x] Focused TUI extension terminal-title regression: `1 passed`; related shortcut subset: `11 passed, 30 deselected`.
- [x] Full appv22 suite rerun after TUI extension terminal-title hook: `236 passed`.
- [x] Focused TUI extension widget regression: `1 passed`; related shortcut subset: `12 passed, 30 deselected`; prompt layout subset: `3 passed, 39 deselected`.
- [x] Full appv22 suite rerun after TUI extension widget hook: `237 passed`.
- [x] Focused TUI extension footer regression: `1 passed`; related shortcut subset: `13 passed, 30 deselected`; footer/layout subset: `7 passed, 36 deselected`.
- [x] Full appv22 suite rerun after TUI extension footer hook: `238 passed`.
- [x] Focused TUI extension header regression: `1 passed`; related shortcut subset: `14 passed, 30 deselected`; startup/layout subset: `8 passed, 36 deselected`.
- [x] Full appv22 suite rerun after TUI extension header hook: `239 passed`.
- [x] Focused TUI extension editor-text regression: `1 passed`; related shortcut subset: `15 passed, 30 deselected`; input/prompt subset: `4 passed, 41 deselected`.
- [x] Full appv22 suite rerun after TUI extension editor-text hooks: `240 passed`.
- [x] Focused TUI extension multi-line editor regression: `1 passed`; related shortcut subset: `16 passed, 30 deselected`; dialog/input subset: `7 passed, 39 deselected`.
- [x] Full appv22 suite rerun after TUI extension multi-line editor hook: `241 passed`.
- [x] Focused TUI extension autocomplete provider regression: `1 passed`; related shortcut/input subset: `18 passed, 29 deselected`; command/extension subset: `36 passed, 59 deselected`.
- [x] Full appv22 suite rerun after TUI extension autocomplete provider hook: `242 passed`.
- [x] Focused TUI extension custom component regression: `1 passed`; related shortcut/input subset: `19 passed, 29 deselected`.
- [x] Full appv22 suite rerun after TUI extension custom component hook: `243 passed`.
- [x] Focused extension provider config validation regression: `1 passed`; related provider/extension subset: `39 passed, 57 deselected`.
- [x] Full appv22 suite rerun after extension provider config validation: `244 passed`.
- [x] Focused extension provider auth status/OAuth regression: `1 passed`; related provider/auth/model subset: `41 passed, 56 deselected`; AI model/stream subset: `9 passed`.
- [x] Full appv22 suite rerun after provider auth status/OAuth metadata: `245 passed`.
- [x] Focused extension OAuth login/logout/refresh regression: `1 passed`; adjacent auth-status pair: `2 passed`; related provider/auth/OAuth/model subset: `42 passed, 56 deselected`; AI model/stream subset: `9 passed`.
- [x] Full appv22 suite rerun after OAuth login/logout/refresh lifecycle: `246 passed`.
- [x] Focused TUI OAuth login/logout local command regression: `1 passed`; related TUI auth/compact/autocomplete subset: `4 passed, 45 deselected`; related provider/auth/OAuth/model subset: `42 passed, 56 deselected`; AI model/stream subset: `9 passed`.
- [x] Full appv22 suite rerun after TUI OAuth login/logout routing: `247 passed`.
- [x] Focused TUI API-key login local command regression: `1 passed`; adjacent OAuth auth command regression: `1 passed`; related TUI auth/compact/autocomplete subset: `5 passed, 45 deselected`; related provider/auth/OAuth/model subset: `42 passed, 56 deselected`; AI model/stream subset: `9 passed`.
- [x] Full appv22 suite rerun after TUI API-key login routing: `248 passed`.
- [x] Focused provider request auth/header regressions after Pi `getApiKeyAndHeaders` follow-up: `6 passed`; related AI model/stream subset: `15 passed`; related coding-agent subset: `96 passed, 2 deselected`; syntax check passed.
- [x] Full appv22 suite rerun after provider request auth/header resolution: `252 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused provider display-name/TUI auth regressions after Pi `getProviderDisplayName` follow-up: `2 passed`; related display/auth subset: `3 passed, 56 deselected`; related AI model/TUI suites: `59 passed`; syntax check passed.
- [x] Full appv22 suite rerun after provider display-name resolver: `253 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused default-model map regressions after Pi `defaultModelPerProvider` follow-up: `2 passed`; related env/CLI/model suites: `15 passed`; syntax check passed.
- [x] Full appv22 suite rerun after default-model map/startup fallback: `254 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused model-resolver core regressions after Pi `model-resolver.ts` follow-up: `5 passed`; related resolver/env/model suites: `18 passed`; syntax check passed.
- [x] Full appv22 suite rerun after model-resolver core port: `259 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused CLI model-resolution regression after Pi CLI `--provider/--model` follow-up: `1 passed`; related CLI/resolver/env/model suites: `21 passed`; syntax check passed.
- [x] Full appv22 suite rerun after CLI model-resolution wiring: `260 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused CLI thinking-level startup regressions after Pi `--thinking` and model-suffix follow-up: `4 passed`; related CLI/app/resolver/env/model suites: `29 passed`; syntax check passed.
- [x] Full appv22 suite rerun after CLI thinking-level startup wiring: `264 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused scoped-model regressions after Pi `resolveModelScope` and scoped cycling follow-up: `3 passed`; related resolver/CLI/app/coding-agent suites: `115 passed, 2 deselected`; syntax check passed.
- [x] Full appv22 suite rerun after scoped model resolver/startup/cycling wiring: `267 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused available-model cycling fallback regressions after Pi `cycleModel` follow-up: `2 passed`; related coding-agent/CLI/resolver suites: `111 passed, 2 deselected`; syntax check passed.
- [x] Full appv22 suite rerun after available-model cycling fallback: `268 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused thinking-level capability regressions after Pi `getSupportedThinkingLevels`/`clampThinkingLevel` and session helper follow-up: `4 passed`; related AI/coding-agent/CLI/app suites: `123 passed, 2 deselected`; syntax check passed.
- [x] Full appv22 suite rerun after thinking-level capability helpers: `272 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused session queue-mode regression after Pi `setSteeringMode`/`setFollowUpMode` follow-up: `1 passed`; related queue/agent-loop subsets: `10 passed`; syntax check passed.
- [x] Full appv22 suite rerun after session queue-mode facade: `273 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused session utility regressions after Pi `getUserMessagesForForking`/`getLastAssistantText` follow-up: `2 passed`; related branching/session subset: `9 passed`; syntax check passed.
- [x] Full appv22 suite rerun after session utility facade: `275 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused session stats/context usage regressions after Pi `getSessionStats`/`getContextUsage` follow-up: `2 passed`; related session/TUI subsets: `19 passed`; syntax check passed.
- [x] Full appv22 suite rerun after session stats/context usage facade: `277 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused TUI shortcut context-usage regression after Pi session facade follow-up: red/green verified; related shortcut/compact subset: `19 passed, 31 deselected`; session facade subset: `2 passed, 105 deselected`; syntax check passed.
- [x] Full appv22 suite rerun after TUI shortcut context-usage facade: `277 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused session state/resource-loader facade regression after Pi `state`/`resourceLoader`/`promptTemplates` follow-up: red/green verified; adjacent resource-loader subset: `4 passed, 104 deselected`; syntax check passed.
- [x] Full appv22 suite rerun after session state/resource-loader facade: `278 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused session retry facade regressions after Pi `abortRetry`/`isRetrying`/`autoRetryEnabled` follow-up: red/green verified; related retry subset: `4 passed, 106 deselected`; compaction/retry subset: `7 passed, 103 deselected`; syntax check passed.
- [x] Full appv22 suite rerun after session retry facade: `280 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused session JSONL export regression after Pi `exportToJsonl` follow-up: red/green verified; adjacent persistence/branching subset: `7 passed, 104 deselected`; syntax check passed.
- [x] Full appv22 suite rerun after session JSONL export facade: `281 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused session HTML export regression after Pi `exportToHtml` follow-up: red/green verified; related export/session subset: `7 passed, 105 deselected`; related TUI rendering subset: `5 passed, 45 deselected`; syntax check passed.
- [x] Full appv22 suite rerun after session HTML export facade: `282 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused replaced-session context regression after Pi `createReplacedSessionContext` follow-up: red/green verified; related command-context subset: `9 passed, 104 deselected`; runtime/context subset: `13 passed, 100 deselected`; syntax check passed.
- [x] Full appv22 suite rerun after replaced-session context facade: `283 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused bind/reload extension lifecycle regressions after Pi `bindExtensions`/`reload` follow-up: red/green verified; added UI context, command context, abort handler, shutdown handler, error listener, reload lifecycle, and resource rediscovery coverage; adjacent resource/reload/extension subset: `22 passed, 93 deselected`; compaction suites: `36 passed`; TUI compact/extension subset: `22 passed, 28 deselected`; syntax check passed.
- [x] Full appv22 suite rerun after bind/reload extension lifecycle facade: `285 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused HTML custom-tool pre-render regression after Pi export `renderedTools` follow-up: red/green verified; export pair subset: `2 passed, 115 deselected`; export/session/resource subset: `14 passed, 103 deselected`; TUI render/extension subset: `32 passed, 18 deselected`; compaction suites: `36 passed`; syntax check passed.
- [x] Full appv22 suite rerun after HTML custom-tool pre-render export: `287 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Fresh verification after full Pi extension binding surface follow-up: new binding regression `1 passed`; bind/reload lifecycle subset `3 passed, 113 deselected`; broader extension subset `26 passed, 91 deselected`; coding-agent suite `115 passed, 2 deselected`; full appv22 suite `287 passed, 2 deselected`; syntax and diff checks passed.
- [x] Focused Pi export-from-file/CLI export regression after standalone HTML export follow-up: red/green verified; direct export-from-file `1 passed, 117 deselected`; CLI export route `1 passed, 7 deselected`; export subset `4 passed, 114 deselected`; CLI suite `8 passed`; compaction suites unchanged at `36 passed`; available TUI suite `50 passed`; syntax check passed.
- [x] Full appv22 suite rerun after Pi export-from-file/CLI export follow-up: `289 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused Pi browser-shell HTML export regression after template/UI export follow-up: red/green verified; focused shell/export set `3 passed, 117 deselected`; export subset `5 passed, 115 deselected`; CLI suite `8 passed`; compaction suites unchanged at `36 passed`; coding-agent suite `118 passed, 2 deselected`; available TUI suite `50 passed`; syntax and diff checks passed.
- [x] Full appv22 suite rerun after Pi browser-shell HTML export follow-up: `291 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused Pi markdown/highlight HTML export regression after vendored renderer follow-up: red/green verified; markdown renderer `1 passed, 120 deselected`; export subset `6 passed, 115 deselected`; CLI suite `8 passed`; compaction suites unchanged at `36 passed`; coding-agent suite `119 passed, 2 deselected`; available TUI suite `50 passed`; syntax and diff checks passed.
- [x] Full appv22 suite rerun after Pi markdown/highlight HTML export follow-up: `292 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused extension runner context regression after Pi `bindCore` / `createContext` / `createCommandContext` follow-up: red/green verified; extension/session-command subset `24 passed, 96 deselected`; TUI render/extension subset `32 passed, 18 deselected`; compaction suites unchanged at `36 passed`; syntax check passed.
- [x] Full appv22 suite rerun after extension runner context facade: `291 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused Pi export tree search/filter regression after browser-shell UI follow-up: red/green verified; search/filter renderer `1 passed, 121 deselected`; export subset `7 passed, 115 deselected`; CLI suite `8 passed`; compaction suites unchanged at `36 passed`; coding-agent suite `120 passed, 2 deselected`; available TUI suite `50 passed`; syntax and diff checks passed.
- [x] Full appv22 suite rerun after Pi export tree search/filter follow-up: `293 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused Pi export copy-link/deep-link regression after browser-shell navigation follow-up: red/green verified; copy/deep-link renderer `1 passed, 124 deselected`; export subset `8 passed, 117 deselected`; extension action binding regression fixed and related subset `25 passed, 100 deselected`; CLI suite `8 passed`; compaction suites unchanged at `36 passed`; coding-agent suite `123 passed, 2 deselected`; available TUI suite `50 passed`; syntax and diff checks passed.
- [x] Full appv22 suite rerun after Pi export copy-link/deep-link and extension action binding follow-up: `296 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused Pi extension runner `ExtensionActions` regression after action-surface follow-up: red/green verified; direct runner/session binding regressions `2 passed`; extension subset `26 passed, 99 deselected`; TUI render/extension subset `32 passed, 18 deselected`; compaction suites unchanged at `36 passed`; coding-agent suite `123 passed, 2 deselected`; syntax check passed.
- [x] Full appv22 suite rerun after Pi extension runner action-surface follow-up: `296 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused Pi export header stats/JSONL download regression after browser-shell header follow-up: red/green verified; focused header renderer `1 passed`; export subset `9 passed, 117 deselected`; CLI suite `8 passed`; compaction suites unchanged at `36 passed`; coding-agent suite `124 passed, 2 deselected`; available TUI suite `50 passed`; syntax and diff checks passed.
- [x] Full appv22 suite rerun after Pi export header stats/JSONL download follow-up: `297 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused Pi export image rendering/modal regression after browser-shell image follow-up: red/green verified; focused image renderer `1 passed`; export subset `9 passed, 118 deselected`; CLI suite `8 passed`; compaction suites unchanged at `36 passed`; coding-agent suite `125 passed, 2 deselected`; available TUI suite `50 passed`; syntax check passed.
- [x] Full appv22 suite rerun after Pi export image rendering/modal follow-up: `298 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused Pi export sidebar resize/keyboard regression after browser-shell controls follow-up: red/green verified; focused sidebar renderer `1 passed`; export subset `11 passed, 117 deselected`; CLI suite `8 passed`; compaction suites unchanged at `36 passed`; coding-agent suite `126 passed, 2 deselected`; available TUI suite `50 passed`; syntax and diff checks passed.
- [x] Full appv22 suite rerun after Pi export sidebar resize/keyboard follow-up: `299 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused Pi export tool-call/result rendering regression after browser-shell navigation follow-up: red/green verified; focused tool renderer `1 passed`; export subset `12 passed, 117 deselected`; CLI suite `8 passed`; compaction suites unchanged at `36 passed`; coding-agent suite `127 passed, 2 deselected`; available TUI suite `50 passed`; syntax check passed.
- [x] Full appv22 suite rerun after Pi export tool-call/result rendering follow-up: `300 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused Pi export rich tool body regression after tool-rendering follow-up: red/green verified; focused rich-tool renderer `1 passed`; export subset `12 passed, 118 deselected`; CLI suite `8 passed`; compaction suites unchanged at `36 passed`; coding-agent suite `128 passed, 2 deselected`; available TUI suite `50 passed`; syntax check passed.
- [x] Full appv22 suite rerun after Pi export rich tool body follow-up: `301 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused Pi export tree display/navigation regression after browser-shell tree follow-up: red/green verified; focused tree renderer `1 passed`; export subset `14 passed, 117 deselected`; CLI suite `8 passed`; compaction suites unchanged at `36 passed`; TUI suite `50 passed`; generated export JS `node --check` passed; coding-agent suite `129 passed, 2 deselected`; syntax check passed.
- [x] Full appv22 suite rerun after Pi export tree display/navigation follow-up: `302 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused Pi export transcript entry block regression after browser-shell body follow-up: red/green verified; focused transcript renderer `1 passed`; export subset `15 passed, 117 deselected`; CLI suite `8 passed`; compaction suites unchanged at `36 passed`; coding-agent suite `130 passed, 2 deselected`; available TUI suite `50 passed`; syntax and generated export JS checks passed.
- [x] Full appv22 suite rerun after Pi export transcript entry block follow-up: `303 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused Pi export theme/layout and visual-edge regressions after browser-shell styling follow-up: red/green verified; focused theme renderer `1 passed`; focused visual-edge renderer `1 passed`; export subset `17 passed, 117 deselected`; CLI suite `8 passed`; compaction suites unchanged at `36 passed`; coding-agent suite `132 passed, 2 deselected`; available TUI suite `50 passed`; compileall and generated export JS checks passed.
- [x] Full appv22 suite rerun after Pi export theme/layout follow-up: `305 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused Pi export ANSI component renderer and selector-closure regressions after browser-shell final edge audit: red/green verified; focused custom ANSI/visual/raw custom renderers `3 passed`; Pi export selector/function scan missing no CSS classes, CSS variables, or JS functions; export subset `18 passed, 117 deselected`; CLI suite `8 passed`; compaction suites unchanged at `36 passed`; coding-agent suite `133 passed, 2 deselected`; available TUI suite `53 passed`; compileall and generated export JS checks passed.
- [x] Full appv22 suite rerun after Pi export ANSI component renderer follow-up: `309 passed, 2 deselected` because this macOS environment lacks a usable `python` executable for two unrelated shell/exec tests.
- [x] Focused Pi TUI cursor-marker render regression after live-rendering follow-up: red/green verified; TUI suite `51 passed`; compaction suites unchanged at `36 passed`; syntax check passed.
- [x] Focused Pi TUI input keybinding regressions after live-editor follow-up: red/green verified; TUI suite `53 passed`; compaction suites unchanged at `36 passed`; full excluded appv22 suite `309 passed, 2 deselected`; syntax check passed.
- [x] Focused Pi TUI horizontal input render regression after live-editor rendering follow-up: red/green verified; TUI suite `54 passed`; compaction suites unchanged at `36 passed`; full excluded appv22 suite `310 passed, 2 deselected`; syntax check passed.
- [x] Focused Pi TUI Alt+D delete-word-forward regression after live-editor keybinding follow-up: red/green verified; TUI suite `55 passed`; compaction suites unchanged at `36 passed`; full excluded appv22 suite `311 passed, 2 deselected`; syntax check passed.
- [x] Focused Pi TUI bracketed paste sanitization regression after live-editor input follow-up: red/green verified; TUI suite `56 passed`; compaction suites unchanged at `36 passed`; full excluded appv22 suite `312 passed, 2 deselected`; syntax check passed.
- [x] Focused Pi TUI Delete-key forward-deletion regression after live-editor keybinding follow-up: red/green verified; TUI suite `57 passed`; compaction suites unchanged at `36 passed`; full excluded appv22 suite `313 passed, 2 deselected`; syntax check passed.
- [x] Focused Pi TUI Ctrl-minus undo regression after live-editor undo follow-up: red/green verified; TUI suite `58 passed`; compaction suites unchanged at `36 passed`; full excluded appv22 suite `314 passed, 2 deselected`; syntax check passed.
- [x] Focused Pi TUI Alt+B/Alt+F word-navigation regression after live-editor keybinding follow-up: red/green verified; TUI suite `59 passed`; compaction suites unchanged at `36 passed`; full excluded appv22 suite `315 passed, 2 deselected`; syntax check passed.
- [x] Focused Pi TUI Alt+Backspace delete-word-backward regression after live-editor keybinding follow-up: red/green verified; TUI suite `60 passed`; compaction suites unchanged at `36 passed`; full excluded appv22 suite `316 passed, 2 deselected`; syntax check passed.

## Phase 1: Agent Runtime Parity

- [x] Add a regression for concurrent `prompt()` rejection while the first prompt is streaming.
- [x] Port the Pi `activeRun` guard shape with an appv22 run-state lock.
- [x] Add a regression proving abort does not poison future runs.
- [x] Reset the abort signal per run, mirroring Pi's per-run `AbortController`.
- [x] Pass stream options through to `stream_fn`, including the active signal and provider/runtime options.
- [x] Add queued `continue_()` behavior when the transcript ends with an assistant message and queued steering/follow-up messages exist.
- [x] Add remaining queue parity: public clearing and queue status.
- [x] Make listener settlement explicit so an `agent_end` listener finishes before the run is considered idle.
- [x] Pass the active abort signal to Pi-style event listeners.
- [x] Emit Pi-style synthetic assistant lifecycle events on uncaught run failure.
- [x] Forward Pi-style provider/session runtime fields into stream options.

## Phase 2: Agent Loop Parity

- [x] Port async-equivalent event ordering details still missing after completed slices: `prepareNextTurn` signal and update-drain semantics.
- [x] Add regression coverage for update-event draining before `tool_execution_end`.
- [x] Add regression coverage for sequential vs parallel tool ordering and terminate behavior.
- [x] Replace appv22 simplifications that do not match Pi `agent-loop.ts`.

## Phase 3: Coding-Agent Session Parity

- [x] Replace heuristic prompt-word tool activation with Pi's definition-first tool registry/session model.
- [x] Port session event subscription and queue update events.
- [x] Port remaining session events: compaction events, retry events, thinking-level/model changes, and session-info updates.
- [x] Port prompt preflight behavior for streaming: steer vs follow-up.
- [x] Port session persistence/branching hooks only after the core event/session API is stable.

## Phase 4: Tool Parity

- [x] Port `file-mutation-queue` and use it for `write` and `edit`.
- [x] Replace single `old_string/new_string` edit with Pi `edits[]` exact replacement behavior, legacy argument preparation, BOM preservation, line-ending preservation, diff/patch details, and overlapping edit rejection.
- [x] Port `write` queueing and abort checks after each filesystem await-equivalent step.
- [x] Port `bash` streaming updates, abort process-tree kill behavior, and command prefix/spawn hook.
- [x] Port `bash` output tail truncation and full-output temp file persistence.
- [x] Remove appv22-only bash success exit-code footer and treat nonzero exits as tool errors.
- [x] Port `read` auto-resize/vision notices, async-style abort handling, and compact render classification.
- [x] Port `read` supported image MIME detection and image content return.
- [x] Recheck `find`, `grep`, `ls`, path utils, and truncation against Pi regression tests before expanding behavior.

## Phase 5: Hermes Compaction Parity

- [x] Port Hermes tool-call/tool-result boundary alignment and post-compression pair sanitization.
- [x] Port Hermes latest user/latest visible assistant tail anchoring after token-budget boundary selection.
- [x] Port Hermes historical image stripping before the newest image-bearing user turn.
- [x] Port Hermes summary role selection, merge-into-tail fallback, and explicit context-summary end marker.
- [x] Port Hermes persisted summary-prefix detection and iterative summary rehydration.
- [x] Port Hermes protected head sizing so a leading system message is preserved in addition to configured non-system head messages.
- [x] Port Hermes protected-tail bounded message floor, soft ceiling, and raw-budget fallback for meaningful compression windows.
- [x] Port Hermes summary prompt safety: redaction instructions, temporal anchoring, historical headings, and summary input/output redaction.
- [x] Port Hermes focused manual compression prompt guidance for `/compress <focus>`-style flows.
- [x] Port Hermes summary failure fallback/bookkeeping fields, deterministic fallback handoff, and abort-on-summary-failure mode.
- [x] Port Hermes summary-model fallback behavior: separate auxiliary summarizer, fallback-to-main retry, aux failure fields, and summary-model clearing.
- [x] Add compaction timing regressions for preflight, post-response, overflow recovery, manual force, summary failure cooldown, and real-usage deferral.
- [x] Port remaining Hermes compressor-owned summary failure cooldown semantics and manual force cooldown clearing.
- [x] Port session rotation/lineage persistence enough to survive restarts, not only in-memory `SessionLineage`.
- [x] Add a manual compression feedback path matching Hermes user-facing status semantics.

## Phase 6: TUI and Rendering Parity

- [x] Expand the Python TUI beyond line input: editor behavior, keybindings, selection/list components, markdown rendering, status/footer surfaces, and tool call/result components.
- [x] Port incremental rendering behaviors from Pi TUI where feasible in Python terminal primitives.
- [x] Add focused render tests for assistant messages, tool execution, diff/full redraw behavior, prompt input, and narrow terminal wrapping.

## Verification

- Narrow tests first, for example:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests/test_agent_loop.py::<test_name> -q`
- Broader package tests after each runtime-affecting phase:
  - `cd appV2.2 && PYTHONPATH=. uv run pytest tests -q`
- Static syntax check after Python edits:
  - `cd appV2.2 && PYTHONPATH=. uv run python -m py_compile $(rg --files appv22 -g '*.py')`
- Diff hygiene:
  - `git diff --check`
