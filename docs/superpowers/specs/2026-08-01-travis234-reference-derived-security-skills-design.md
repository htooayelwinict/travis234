# Reference-Derived Security Skills Design

**Status:** Approved  
**Branch:** `offsec-agent`  
**References reviewed:** `pensarai/apex@545f12fe`, `yeyintminthuhtut/awesome-ai-offensive-security@b8fee45`

## Objective

Add compact, lazy-loaded tactical resources that improve Travis234 OffSec across offensive testing, CTF work, DFIR, incident response, and finding validation without replacing its agent loop, compaction system, provider runtime, or general tactical identity.

## Design choice

Ship three reusable skills:

1. `investigating-security-targets` for hypothesis-driven reconnaissance, testing, exploitation verification, and evidence capture across network, web, host, binary, cloud, and CTF missions.
2. `triaging-security-incidents` for evidence-preserving DFIR, incident response, malware triage, timelines, scoping, containment analysis, and handoff.
3. `validating-security-findings` for independent reproduction, control comparisons, false-positive rejection, impact calibration, and evidence-backed verdicts.

This is preferable to one large field manual because each task loads only relevant context. It is preferable to dozens of tool-specific skills because the useful invariant is reasoning discipline, not memorizing a static Kali command catalog.

## Techniques retained from the references

- Capability-conditioned guidance: instructions refer only to tools actually available in the session.
- Atomic objective decomposition: one hypothesis, test, observable, and stop condition at a time.
- Ranked work queues: choose the next action by evidence value, likelihood, impact, and cost.
- Explicit phase completion: discovery and action loops end with verified evidence and a durable handoff.
- Independent finding judgment: command success is not proof of vulnerability or impact.
- Evidence-first reporting: confirmed observations remain separate from inference, failed attempts, unknowns, and limitations.
- Specialized context: detailed methods load lazily instead of bloating the global system prompt.

## Deliberate exclusions

- No Apex runtime, fixed web-pentest state machine, approval framework, scope manifest, target allowlist, container policy, report registry, or vendor-specific tools.
- No replacement of `travis/agent/**`, `travis/compaction/**`, or `travis/ai/providers/**`.
- No domain-centric assumption: the same agent and evidence model must remain useful for hosts, networks, binaries, cloud artifacts, logs, disks, memory, malware, and incident response.
- No copied prompt blocks. The skills synthesize general techniques into Travis-native terminology and its `bash`/`process`/`tmux`/subagent interfaces.

## Distribution contract

Every skill exists byte-identically in:

- `skills/<name>/SKILL.md`
- `travis/resources/skills/<name>/SKILL.md`
- `packages/travis234-cli/skills/<name>/SKILL.md`

Each frontmatter description starts with `Use when`, describes trigger conditions rather than the workflow, and keeps the body below 500 words. Python package-data and npm package globs must include all three automatically.

## Verification

- Contract tests enumerate all packaged skill names and compare the three copies byte-for-byte.
- Each skill gets focused content tests for its required reasoning and evidence contract.
- Loader tests prove discovery and lazy prompt inclusion.
- Python wheel/sdist and npm dry-run contents prove distribution.
- Final TUI qualification exercises offensive, DFIR, finding-validation, PTY, tmux, subagent, and resumed-session behavior.
