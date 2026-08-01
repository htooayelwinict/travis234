---
name: investigating-security-targets
description: Use when investigating a security target, CTF challenge, host, network, service, application, binary, cloud asset, or unknown technical environment where evidence-driven discovery and testing are required.
---

# Investigating Security Targets

## Overview

Turn an open-ended mission into a sequence of evidence-producing decisions. Maintain ranked hypotheses and choose the next action for information gain, likelihood, impact, and cost.

## Working loop

1. **Orient:** Restate the objective, supplied targets, success condition, constraints, credentials, artifacts, and unknowns. Inspect the current host, routes, interfaces, files, and available tools before assuming the environment.
2. **Model:** Record the observed attack surface and ranked hypotheses. For each hypothesis define one atomic test, its expected observable, and a stop condition.
3. **Acquire:** Collect the smallest evidence that discriminates between hypotheses. Preserve raw output or artifacts when later reasoning depends on them.
4. **Act:** Execute the highest-value test. Change one important variable at a time and compare against a baseline or control.
5. **Verify:** Reproduce consequential results, prove the relevant boundary or impact, and distinguish target behavior from local tool behavior.
6. **Record:** Update Facts, Hypotheses, Unknowns, Failed attempts, artifacts, live sessions, and the next recommended action.

## Kali and terminal strategy

- Detect reality first: use `command -v TOOL`, `TOOL --version`, or `TOOL --help`. Check `ip -brief address`, `ip route`, DNS, listeners, and VPN interfaces when network context matters.
- Prefer an installed specialist tool when it materially improves evidence. Discover package names with `apt-cache`; use a venv or `pipx` for Python tools when appropriate.
- Use bash for finite commands, bash plus process for an interactive PTY needing follow-up input, and tmux for listeners, callbacks, relays, servers, or work that must survive turns.
- Keep commands reproducible. Capture exact targets, important flags, timestamps, exit status, and output locations without exposing credentials.

## Delegation

Delegate independent hypotheses or disjoint artifacts. Give each child the known facts, exact objective, observable, stop condition, file ownership, and return contract. Integrate evidence; do not merge unsupported conclusions.

## Quick reference

| Situation | Next move |
|---|---|
| Unknown service | Fingerprint gently, then validate with a second signal |
| Failed technique | Read the error, change the hypothesis or one variable |
| Apparent success | Reproduce and compare with a negative control |
| Long-lived interaction | Start tmux, record its logical name, capture later |
| Large output | Save an artifact and summarize evidence locations |

## Example

Hypothesis: an exposed service accepts a default credential. Atomic test: one authenticated request using the candidate. Observable: server-issued authenticated state and access to a protected operation. Stop condition: one definitive accept/reject response; no repeated guessing.

## Common mistakes

Do not treat a tool banner, HTTP status, open port, command exit code, reflected string, or scanner label as proof by itself. Do not repeat unchanged commands. Do not bury failed attempts; they narrow the search space and prevent duplicate work.
