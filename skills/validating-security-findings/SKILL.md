---
name: validating-security-findings
description: Use when reviewing a suspected vulnerability, exploit result, scanner alert, incident conclusion, proof of concept, security report, severity claim, or remediation retest.
---

# Validating Security Findings

## Overview

Judge the claim independently from the submitter's confidence. A valid result requires target-derived evidence that proves the claimed behavior, exploitability, boundary crossed, and material impact.

## Validation loop

1. **Restate:** Express the claim as actor, prerequisites, action, affected boundary, observed result, and claimed impact. Mark every missing element.
2. **Inspect:** Review the exact command or PoC, target, inputs, output, artifacts, timestamps, and environment. Identify hardcoded success messages, local-only effects, fabricated responses, stale artifacts, and success conditions unrelated to target behavior.
3. **Reproduce independently:** Prefer a minimal test you control. Record tool versions and exact inputs. A zero exit code, HTTP 200, open port, reflected payload, scanner label, crash, or error message is supporting evidence—not the conclusion.
4. **Compare controls:** Use a positive control when a known-valid behavior exists and a negative control that should not trigger the result. Change one material variable. Explain the behavioral delta.
5. **Prove exploitability:** Show that attacker-controlled input reaches the relevant operation or crosses the claimed trust, identity, tenant, privilege, confidentiality, integrity, or availability boundary.
6. **Calibrate impact:** Claim only the consequence actually demonstrated. Separate a vulnerable primitive from a complete exploit chain, and separate exposure from sensitive exposure.
7. **Verdict:** Report status, confidence, evidence, reproduction steps, controls, impact, concerns, limitations, and the smallest next test that would resolve remaining uncertainty.

## Evidence requirements

Strong evidence comes from live target responses, independently captured network behavior, process or file effects on the target, authenticated identity comparisons, preserved artifacts, or repeatable execution. Quote only the decisive observation and point to the complete artifact.

If reproduction is blocked, say so. Do not convert missing evidence into acceptance or rejection. A finding may be real but unverified; that distinction belongs in the Verdict.

## Quick reference

| Evidence | What it proves |
|---|---|
| PoC prints “success” | Only that the print statement ran |
| Target response changes with one input | A behavioral delta worth explaining |
| Negative control behaves the same | Claimed trigger is unsupported |
| Different identity accesses protected data | Potential authorization boundary crossing |
| Crash repeats on target input | Reliability; exploitability and impact remain separate |
| Remediation blocks exploit and preserves control | Strong retest evidence |

## Example

For a claimed authorization bypass, repeat the same resource request with the entitled identity, the allegedly unauthorized identity, and an unauthenticated control. Record tokens only as redacted labels. Validate that the returned resource belongs outside the second identity's permitted boundary and that the response contains material data.

## Common mistakes

Do not validate prose instead of behavior, reuse only the submitter's success predicate, confuse local execution with target execution, inflate severity beyond observed impact, or omit contradictory evidence. High confidence requires repeatability and controls; low confidence requires explicit concerns and limitations.
