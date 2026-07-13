# appv23 Growth Rules

appv23 should grow as an agent runtime/framework, not as one ever-expanding coding agent.

The coding agent is the first product built on the runtime. New use cases should be added as profiles, apps, plugins, tools, examples, or documentation that sit beside the coding agent instead of being wired directly into the kernel.

## Core Model

```text
appv23 = runtime kernel
coding-agent = first-class product on the kernel
future use cases = profiles/apps/plugins on the kernel
```

Keep the runtime stable and reusable:

```text
appv23-core
  agent loop
  provider streaming
  context compaction
  session store
  tool protocol
  safety and guardrails

appv23-coding-agent
  read/edit/write/bash tools
  coding prompt
  TUI behavior
  coding session UX

appv23-usecases
  SRE assistant
  research assistant
  document analyst
  security-lab assistant
  support-ticket assistant
  browser workflow assistant
  local knowledgebase assistant
```

## Red Zone: Kernel Contracts

Do not modify frozen kernel files unless the task explicitly says `kernel change`.

Frozen kernel files:

```text
appV2.3/appv23/agent/agent.py
appV2.3/appv23/agent/agent_loop.py
appV2.3/appv23/agent/types.py
appV2.3/appv23/ai/types.py
appV2.3/appv23/ai/stream.py
appV2.3/appv23/ai/validation.py
appV2.3/appv23/compaction/compressor.py
appV2.3/appv23/compaction/timing.py
appV2.3/appv23/coding_agent/session_store.py
appV2.3/appv23/app.py
```

Allowed changes in the red zone:

```text
bug fixes
compatibility fixes
performance fixes
carefully reviewed architecture changes
```

Red-zone changes must explain:

```text
why the kernel must change
which behavior must not regress
which tests prove the behavior
how the change can be rolled back
```

Do not:

```text
rewrite runtime control flow casually
rename public dataclasses or fields without migration tests
change tool schemas without snapshot tests
remove or weaken tests to make new behavior pass
add use-case-specific logic to the kernel
```

## Yellow Zone: Careful Evolution

These areas may evolve, but every meaningful change needs tests:

```text
appV2.3/appv23/ai/providers/
appV2.3/appv23/coding_agent/resource_loader.py
appV2.3/appv23/coding_agent/extensions.py
appV2.3/appv23/coding_agent/settings_manager.py
appV2.3/appv23/coding_agent/subagents.py
appV2.3/appv23/tui/
packages/appv23-cli/
Dockerfile.appv23.release
.github/workflows/
```

Good yellow-zone improvements:

```text
provider reliability
model catalog updates
stream recovery
sandbox hardening
observability
extension/plugin API
subagent result quality
TUI usability
release packaging
docs and examples
```

Provider streaming and subagents are high-value but high-risk areas. Any change there should include regression coverage.

## Green Zone: Fast Expansion

Build new use cases in green-zone areas:

```text
apps/
profiles/
skills/
examples/
docs/
tests/fixtures/
packages/
```

For new use cases, create profiles, skills, examples, docs, or new tool modules.

Do not:

```text
add SRE assistant logic to agent_loop.py
add research assistant logic to CodingApp
add security assistant logic to AgentSession
```

## Profiles

New products should usually start as profiles.

A profile may define:

```text
name
system prompt
enabled tools
disabled tools
model defaults
compaction policy
guardrail settings
output style
session behavior
optional skills
```

Candidate profiles:

```text
profiles/coding_agent.py
profiles/sre_agent.py
profiles/research_agent.py
profiles/document_agent.py
profiles/security_lab_agent.py
```

Target CLI shape:

```bash
appv23 --profile coding --cwd .
appv23 --profile research --cwd .
appv23 --profile sre --cwd .
appv23 --profile docs --cwd .
appv23 --profile security-lab --cwd .
```

## Growth Phases

Phase 1: declare boundaries without moving much code.

```text
docs/architecture/kernel-boundaries.md
docs/architecture/extension-points.md
docs/architecture/frozen-contracts.md
```

Phase 2: add a small profile abstraction without rewriting the agent loop.

```python
@dataclass
class AppProfile:
    name: str
    system_prompt: str | None
    append_system_prompt: str | None
    active_tools: list[str]
    tool_loop_guardrails: dict
    enable_subagents: bool
    default_model: str | None
```

Phase 3: split packages only after boundaries are stable.

```text
appv23-core
appv23-coding
appv23-profiles
appv23-cli
```

## Change Discipline

Use this lifecycle for appv23 changes:

```text
1. Idea
2. Tiny spec
3. Risk label
4. Test plan
5. Feature branch
6. Small diff
7. Run verification
8. Review frozen-zone impact
9. Merge
10. Release note
```

Before changing code, answer:

```text
What layer am I changing?
Does this touch frozen files?
If yes, why?
What behavior must not regress?
Which tests prove it?
How do I roll back?
```

## Branch Rules

Use small branches:

```text
main          stable released code only
next/appv23   integration branch for the next release
feature/*     experiments and new functionality
profile/*     new use-case profiles
fix/*         bug fixes
release/*     final hardening before tag
```

Do not directly commit broad changes to `main` or `next/appv23`.

Good branch names:

```text
feature/sre-profile-v1
feature/research-profile-v1
fix/provider-stream-tool-json
fix/compaction-secret-redaction
docs/kernel-boundaries
```

## Contract Tests

Add contract tests before growing the runtime surface.

Lock tool schemas for:

```text
read
write
edit
bash
grep
find
ls
spawn_subagent
wait_subagent
```

Maintain golden fake-provider workflows for:

```text
read file -> answer
edit file -> run test -> answer
write report -> answer
bash timeout -> recover
bad path -> stop retrying
partial streamed tool JSON -> recover
malformed mutating tool call -> drop safely
context overflow -> compact -> continue
subagent inspect -> parent summarizes
package install request without consent -> block
```

Track simple regression benchmarks:

```text
startup time
one fake-provider turn
one tool-call turn
large transcript compaction
session export
npm dry run
Docker command generation
```

Lock the sandbox threat model with tests, especially around Docker flags, mounts, network access, user IDs, process limits, and write permissions.

## Product Direction

Recommended build order:

```text
1. appv23 profile system
2. SRE / infrastructure agent
3. defensive security-lab assistant
4. document / knowledgebase agent
5. browser/web workflow agent
```

The SRE profile should emphasize:

```text
read logs
inspect docker compose
analyze nginx config
generate incident reports
explain deployment failures
write runbooks
compare env configs
investigate slow queries
```

Default SRE guardrails:

```text
no destructive commands by default
no package install without consent
no cloud mutation without explicit approval
```

The security-lab profile should be defensive by default:

```text
audit code
summarize attack surface
generate local-lab exploit notes only when clearly bounded
write mitigation checklists
create detection rules
review auth flows
```

## Version Direction

```text
2.3.x = stabilize current appv23
2.4.x = profile system + first non-coding profiles
2.5.x = plugin/tool API hardening
3.0.x = package split / public framework API
```

For `2.3.x`, focus on:

```text
bug fixes
tests
docs
sandbox clarity
packaging polish
provider stability
```

For `2.4.x`, add:

```text
profiles
SRE profile
research/docs profile
security-lab profile
profile tests
```

For `2.5.x`, improve:

```text
plugin API
tool registry
permissions
observability
profile marketplace/local packs
```

For `3.0.x`, consider:

```text
physical package split
stable public API
migration docs
```

## Core Principle

Do not ask:

```text
How do I add more features into appv23?
```

Ask:

```text
How do I let new apps sit on top of appv23 without changing the kernel?
```

Keep growing appv23 by freezing the kernel, growing through profiles, using small branches, adding contract tests, and releasing in small, reversible steps.
