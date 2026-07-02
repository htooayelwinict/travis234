# Idea 2: Natural-Language Workflow Runtime

Date: 2026-07-02
Status: idea captured
Scope: appV2.3.1 / appv231

## Summary

Build `appv231` as a natural-language workflow compiler and guarded runtime.

This is not an n8n clone. n8n is visual-first: users drag workflow nodes, connect triggers, configure credentials, and inspect executions on a canvas. The appv231 direction is language-first: the user describes an operational outcome, and appv231 creates an inspectable automation package with a workflow spec, permissions, dry-run fixtures, tests, execution logs, and rollback notes.

## Product Shape

```text
natural language request
  -> workflow-builder profile
  -> structured workflow package
  -> schema validation
  -> permission review
  -> dry run
  -> approval
  -> guarded execution
  -> audit trail
```

Example user request:

```text
Every Friday, inspect my repo dependencies, check for high/critical CVEs,
create a patch branch if safe, run tests, and draft a PR.
Never merge. Never publish. Ask before package upgrades.
```

Expected generated package:

```text
automation/
  dependency-security-weekly.workflow.yaml
  permissions.yaml
  dry_run_fixtures/
  tests/
  rollback.md
  execution_log.jsonl
```

## Target Users

```text
solo technical founders
SRE and devops teams
security teams
automation agencies
internal platform teams
technical operators who prefer reviewed automation-as-code over no-code canvases
```

## Differentiation

n8n and Zapier focus on workflow composition across apps. appv231 should focus on agent-created, git-friendly automation packages.

Positioning:

```text
Natural language -> reviewed automation package -> guarded runtime execution
```

The value is not a prettier workflow builder. The value is automation that can be inspected, tested, versioned, code-reviewed, dry-run, and rolled back.

## Compatibility With Idea 1

This is compatible with Idea 1 approach 2 if implemented as a profile/app on top of the runtime.

Allowed shape:

```text
profiles/workflow_builder.py
apps/workflows/schema.py
apps/workflows/compiler.py
apps/workflows/validator.py
apps/workflows/executor.py
apps/workflows/store.py
tests/workflows/
```

Required boundary:

```text
workflow logic lives in green-zone apps/profiles
CLI/profile selection lives in yellow zone
app.py may expose only narrow generic profile/session pass-through
agent_loop, provider streaming, compaction, and session store do not gain workflow semantics
```

## MVP

Phase 1: generate only.

```text
user describes workflow
appv231 emits workflow YAML/JSON
schema validator checks structure
permission summary is shown
no execution yet
```

Phase 2: local guarded execution.

```text
support safe primitives: read, grep, find, ls, write report, read-only bash
require approval before mutating operations
store execution logs outside normal chat history
```

Phase 3: workflow package tests.

```text
dry-run fixtures
expected output snapshots
permission-contract tests
rollback notes
```

Phase 4: integrations.

```text
HTTP calls
GitHub issues/PRs
Slack/Discord notifications
email summaries
cloud/IaC adapters later
```

## What Not To Do

Do not:

```text
put DAG traversal into agent_loop.py
put workflow state into AgentSession
turn CodingApp into a workflow engine
hardcode Slack, Gmail, GitHub, Docker, or cloud APIs into base tools
change provider streaming or compaction to support workflow execution
let the model directly execute unreviewed mutating workflows
```

## Rule Compatibility

Compatible with `rules.md`:

```text
new use case lives in profiles/apps/docs/tests
kernel remains runtime foundation
workflow system is built beside coding agent
guardrails and tests come before broad execution
```

Possible violations:

```text
adding workflow-specific logic to red-zone files
expanding base tools with integration-specific behavior
skipping approval gates for side effects
storing workflow execution blobs directly in model context
```

## Decision

Capture as Idea 2:

```text
appv231 Workflow Compiler
language-first automation
workflow packages as code
guarded execution
no workflow semantics in the kernel
```
