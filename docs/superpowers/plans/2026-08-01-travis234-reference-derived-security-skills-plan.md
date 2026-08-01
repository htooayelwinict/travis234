# Reference-Derived Security Skills Implementation Plan

> Execute inline with `superpowers:executing-plans`; do not dispatch subagents.

**Goal:** Add the three approved lazy security skills while preserving the Travis234 runtime and packaging them identically across source, Python, and npm surfaces.

## Task A: Extend resource contracts first

- Update installed metadata, resource-loader, distribution, npm, and OffSec product tests to expect the three new names.
- Add focused trigger/content assertions and byte-equality assertions.
- Run the tests and retain the expected missing-skill failures.

## Task B: Add `investigating-security-targets`

- Write a sub-500-word skill covering mission framing, ranked hypotheses, atomic tests, Kali discovery, `bash`/`process`/`tmux` selection, evidence, pivots, verification, and handoff.
- Copy it byte-identically to all three distribution trees.
- Run the focused resource and package tests before starting the next skill.

## Task C: Add `triaging-security-incidents`

- Write a sub-500-word skill covering evidence preservation, acquisition, hashing, time normalization, volatile/durable artifacts, timeline construction, scoping, containment analysis, and handoff.
- Copy it byte-identically to all three distribution trees.
- Run the focused resource and package tests before starting the next skill.

## Task D: Add `validating-security-findings`

- Write a sub-500-word skill covering independent reproduction, positive/negative controls, target-derived evidence, exploitability, impact, confidence, limitations, and verdict shape.
- Copy it byte-identically to all three distribution trees.
- Run all skill/resource tests, Python build content checks, and npm dry-run checks.

## Task E: Resume the parent implementation plan

- Continue Tasks 10–12 of the single-agent refactor plan: Kali runtime and npm/GHCR surfaces, beginner/TUI documentation, then full repository/package/container/TUI/red-zone qualification.
