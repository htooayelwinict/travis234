# appv231 TUI Handoff And Subagent Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan inline.

**Goal:** Save the proven 21-prompt TUI protocol as developer handoff documentation, then run and verify a separate five-prompt subagent TUI evaluation.

**Architecture:** The durable change is limited to `appV2.3.1/evals/README.md`. The subagent evaluation is a one-off real TUI session driven through the existing `TuiDriver`, with fixtures and evidence stored under `/tmp` rather than added to the repository.

**Tech Stack:** Markdown, Python 3.13, `appv231.cli`, PTY `TuiDriver`, JSONL traces, OpenRouter MiMo.

## Global Constraints

- Do not modify `appV2.3.1/appv231/compaction/`.
- Do not publish npm packages or container images.
- Do not perform git operations.
- Do not expose dotenv values or provider credentials.
- Use one continuous TUI session per evaluation run.
- If appv231 runtime code is fixed, restart the five-prompt run from Prompt 1 in a fresh workspace.

---

### Task 1: Save The 21-Prompt Developer Handoff

**Files:**
- Modify: `appV2.3.1/evals/README.md`

**Interfaces:**
- Consumes: existing `evals.run_continuous_sdlc_eval` and `TuiDriver` commands.
- Produces: a copy-pasteable protocol for future developers.

- [ ] Add a `21-prompt developer handoff` section with exact launch command, configuration, session rules, evidence paths, compaction cadence, capability grant, timeout recovery, verifier rules, restart rule, and non-publishing constraints.
- [ ] State explicitly that the PTY harness operates the actual `python -m appv231.cli` TUI rather than replacing it.
- [ ] Verify every documented CLI option exists with `python -m evals.run_continuous_sdlc_eval --help`.
- [ ] Scan the README for secrets, stale script-only instructions, and accidental inclusion of the separate five-prompt subagent task.

### Task 2: Prepare The Separate Subagent Fixture

**Files:**
- Create under `/tmp`: one documentation file, one source file, one test file, and immutable checksums.
- Do not add repository files.

**Interfaces:**
- Consumes: `~/.appv231/agent/AGENTS.md` and `~/.appv231/agent/skills/subagent-delegation/SKILL.md` through normal appv231 profile loading.
- Produces: exact paths for five bounded prompts and a before-state mutation baseline.

- [ ] Verify the current profile and subagent skill exist; do not copy from missing `~/.agents` paths.
- [ ] Create the fresh fixture and record SHA-256 hashes before the run.
- [ ] Start `python -m appv231.cli` through `TuiDriver` with the real dotenv, event trace, conversation log, `--thinking medium`, and `--temperature 0.2`.
- [ ] Select `/model mimo` row `1` and verify the selected model event.

### Task 3: Run Five Subagent Prompts

**Files:**
- Write evidence only under the temporary output directory.

**Interfaces:**
- Consumes: the fixture paths from Task 2.
- Produces: five parent responses plus child lifecycle/status evidence.

- [ ] Prompt 1: explicitly activate the skill and delegate exact documentation review to one reviewer child.
- [ ] Prompt 2: explicitly activate the skill and delegate exact source inspection to one explorer child without parent rereads.
- [ ] Prompt 3: explicitly activate the skill and delegate exact test inspection to one QA child without writes.
- [ ] Prompt 4: execute `/delegate --backend internal reviewer <exact path task>`, wait for completion, then execute `/agents` and capture supervisor status.
- [ ] Prompt 5: request a child file mutation and verify the read-only guardrail rejects the request without spawning or mutating files.
- [ ] Relay every exact prompt and final parent response to the user as each turn completes.

### Task 4: Verify And Report

**Files:**
- Read: temporary `trace.jsonl`, `conversation.jsonl`, `terminal.log`, fixture hashes.

**Interfaces:**
- Consumes: Task 3 evidence.
- Produces: a five-row pass/fail matrix and proven defect list.

- [ ] Verify five expected responses, child task ids/roles/statuses/summaries, runtime `/agents` output, and zero fatal events.
- [ ] Recompute fixture hashes and prove Prompt 5 caused no mutation.
- [ ] Exit through `/exit` and verify the TUI process returns zero.
- [ ] If a product defect is proven, add a failing regression test, apply one root-cause fix outside compaction, run the full appv231 suite, and restart Task 2 from a fresh workspace.
- [ ] Run `PYTHONPATH=appV2.3.1 .venv/bin/python -m pytest -q appV2.3.1/tests` and report the final result.
