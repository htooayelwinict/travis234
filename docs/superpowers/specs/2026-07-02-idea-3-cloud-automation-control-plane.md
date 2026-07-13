# Idea 3: Cloud Automation Control Plane

Date: 2026-07-02
Status: idea captured
Scope: appV2.3.1 / appv231

## Summary

Extend the workflow idea beyond host-only automation.

appv231 can become a natural-language automation control plane where users create, approve, and monitor workflows from mobile or web, while cloud runners execute approved workflow packages. Infrastructure as Code is a first-class part of the automation lifecycle.

The stronger product is:

```text
Natural language -> workflow package -> IaC plan -> human approval -> cloud runner -> audit trail
```

## Why People Use Automation

People use automation to:

```text
remove repetitive manual work
connect SaaS tools and APIs
reduce coordination overhead
scale small teams
make processes repeatable
create audit trails
avoid missed steps
run work on schedules or events
delegate low-risk operational actions
```

AI automation adds:

```text
natural-language intent capture
planning across multiple systems
adaptive troubleshooting
summarized outcomes
human-in-the-loop approvals
less need to manually design every node
```

## Product Example

Mobile/web prompt:

```text
Create a client demo environment for Acme.
Use the latest main branch.
Deploy web + API + Postgres.
Seed fake data.
Expire it in 6 hours.
Send me the URL and estimated cost before apply.
```

appv231 response:

```text
I will create:
- temporary network resources
- web service
- API service
- Postgres instance
- seed-data job
- preview URL
- auto-destroy schedule after 6 hours

Estimated cost: $3.20-$6.80
Risk: creates public URL and cloud resources
Required approval: IaC plan apply

Approve plan preview?
```

## Architecture

```text
mobile app / web app
  -> appv231 cloud gateway
  -> workflow-builder profile
  -> workflow spec + IaC spec
  -> Terraform/Pulumi preview
  -> human approval
  -> isolated cloud runner
  -> execution logs + rollback/destroy plan
```

Core services:

```text
cloud gateway
profile runtime
workflow package store
IaC plan service
approval service
runner scheduler
execution log store
secrets/identity broker
audit trail
```

## IaC Role

IaC should not be hidden behind direct cloud mutations.

Cloud-changing workflows should produce:

```text
Terraform or Pulumi project
plan/preview output
cost estimate when available
policy checks
approval request
apply record
rollback or destroy plan
state reference
```

The model should generate or modify IaC artifacts, but deployment should happen only through validated runners with preview/apply gates.

## Compatibility With Idea 1

Compatible if cloud automation is built as profiles/apps/plugins, not kernel behavior.

Allowed shape:

```text
profiles/cloud_workflow_builder.py
apps/workflows/
apps/iac/terraform_adapter.py
apps/iac/pulumi_adapter.py
apps/cloud_gateway/
apps/approvals/
apps/runners/
tests/cloud_workflows/
```

Allowed red-zone touch:

```text
app.py pass-through for generic profile/session configuration only
```

Not allowed:

```text
cloud APIs inside agent_loop.py
IaC state inside AgentSession
workflow execution semantics in provider streaming
compaction changes for cloud workflows
base tools that secretly perform cloud mutations
```

## Security Model

Cloud/mobile automation introduces a larger attack surface.

Required principles:

```text
no raw cloud secrets in model context
short-lived scoped credentials
OIDC or brokered identity where possible
per-workflow permission manifests
mandatory preview before apply
mandatory approval for create/update/delete cloud resources
hard budgets and concurrency limits
runner isolation
audit logs
rollback/destroy plans
```

## Staged Path

Stage 1: local workflow compiler.

```text
language to workflow package
schema validation
permissions
dry run
```

Stage 2: IaC package generator.

```text
language to Terraform/Pulumi project
terraform plan or pulumi preview only
no apply
```

Stage 3: approval-gated cloud runner.

```text
apply only after explicit approval
isolated runner
limited credentials
logs and outputs
destroy plan
```

Stage 4: mobile/web control plane.

```text
create workflows
review plans
approve/deny
monitor runs
pause or destroy resources
```

Stage 5: reusable marketplace.

```text
workflow templates
IaC templates
connectors
team policies
organization approvals
```

## Differentiation

Do not frame this as host automation or an n8n clone.

Frame it as:

```text
agent-native cloud automation
workflow-as-code
IaC-as-automation
mobile approval and monitoring
safe execution through preview, policy, and rollback
```

## Rule Compatibility

Compatible with `rules.md` if:

```text
cloud/IaC functionality stays in green-zone apps/profiles/plugins
kernel control flow remains stable
profile framework remains the first foundation
contract tests protect tool schemas and permissions
dangerous operations require approval
```

Violation risk:

```text
trying to build cloud orchestration into the core agent loop
using AgentSession as the workflow database
letting generated workflows run cloud mutations without IaC plans
mixing secrets into prompts/session history
skipping policy and budget enforcement
```

## Decision

Capture as Idea 3:

```text
appv231 Cloud Automation Control Plane
mobile/web intent capture
cloud runners
IaC preview/apply lifecycle
approval-first execution
kernel-safe architecture
```
