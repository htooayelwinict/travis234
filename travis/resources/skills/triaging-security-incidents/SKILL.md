---
name: triaging-security-incidents
description: Use when handling DFIR, incident response, malware triage, suspicious activity, compromised hosts, logs, packet captures, disk or memory images, timelines, scoping, containment analysis, or forensic evidence.
---

# Triaging Security Incidents

## Overview

Build a defensible account of what happened, when, how far it spread, and what evidence supports each conclusion. Preserve evidence before transforming it, and keep investigative actions distinguishable from original activity.

## Triage loop

1. **Frame:** State the incident question, affected assets, reporting timezone, known time window, available telemetry, business impact, and immediate unknowns.
2. **Preserve:** Prefer copies, snapshots, exports, and read-only access. Record source, acquisition method, acquisition time, collector, destination, and SHA-256 before analysis. Never silently replace an original artifact.
3. **Acquire:** Collect volatile evidence first when it may disappear: processes, network connections, logged-on users, memory, mounts, containers, temporary files, and live response output. Then gather durable evidence such as disks, logs, registry data, journals, browser artifacts, mail, cloud audit events, and backups.
4. **Normalize:** Preserve original timestamps and also normalize working events to UTC. Record clock skew, timezone, timestamp semantics, log gaps, retention limits, and parser assumptions.
5. **Analyze:** Build a timeline around anchors. Correlate identity, process, file, network, persistence, authentication, and control-plane evidence. Maintain separate Facts, Hypotheses, Unknowns, and Failed attempts.
6. **Scope:** Search for the same indicators and behaviors across adjacent hosts, identities, applications, and time ranges. Treat an indicator match as a lead until context confirms it.
7. **Decide:** Describe containment, eradication, and recovery options with expected benefit, evidence impact, operational cost, and reversibility. Do not confuse a proposed action with an action already performed.
8. **Handoff:** Report findings, confidence, affected scope, timeline, evidence locations and hashes, gaps, live sessions, actions taken, and next decisions.

## Terminal strategy

Use bash for finite collection or parsing, bash plus process for interactive consoles, and tmux for long acquisitions, packet capture, log streaming, or monitoring across turns. On Kali, verify tools with `command -v` and record versions. Store large output as artifacts rather than pasting it into conclusions.

## Quick reference

| Question | Evidence examples |
|---|---|
| What executed? | process tree, command line, parent, user, hash, signer |
| What persisted? | services, tasks, autoruns, cron, startup files, cloud changes |
| What communicated? | sockets, DNS, proxy, firewall, flow, packet capture |
| What changed? | file metadata, package history, configuration, audit events |
| How far? | identity reuse, lateral movement, shared indicators, peer baselines |

## Example

A suspicious binary is a lead, not a verdict. Hash the preserved sample, correlate its execution parent and time with network and identity events, compare peer hosts, and record which observations confirm or contradict compromise.

## Common mistakes

Avoid changing evidence while exploring it, mixing local and UTC times, treating absence of one log as proof of absence, over-scoping from a single indicator, and performing containment without recording its effect on evidence and operations. Chain of custody is part of the analysis, not paperwork added later.
