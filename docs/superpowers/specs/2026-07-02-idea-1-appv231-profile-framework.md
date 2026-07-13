# Idea 1: appv231 Profile Framework

Date: 2026-07-02
Status: idea approved for design capture
Scope: appV2.3.1 / appv231

## Summary

Build `appv231` as an agent runtime with a first-class profile framework.

The profile system should let new apps sit on top of the existing runtime without rewriting the kernel. The coding agent remains the default product, while future SRE, research, docs, and security-lab assistants become profiles that configure prompts, tools, guardrails, model defaults, and session behavior.

## External Reference Facts

Current agent frameworks converge on the same architecture:

- OpenAI Agents SDK defines an agent around `name`, `instructions`, `tools`, handoffs, model settings, guardrails, structured output, MCP servers, sessions, and hooks.
  Source: https://openai.github.io/openai-agents-python/agents/
- OpenAI Agents SDK treats sessions, guardrails, MCP tool calling, tracing, and tool execution as runtime concerns rather than one-off prompt text.
  Source: https://openai.github.io/openai-agents-python/
- LangGraph separates thread-scoped checkpoints from cross-thread stores, which supports keeping appv231 session and compaction internals stable while profiles configure behavior around them.
  Source: https://docs.langchain.com/oss/python/langgraph/persistence
- Claude Agent SDK subagents emphasize context isolation, specialized instructions, and tool restrictions, which supports profile-specific tool allowlists.
  Source: https://code.claude.com/docs/en/agent-sdk/subagents
- Microsoft Agent Framework separates agents from workflows and exposes sessions, middleware, MCP, telemetry, and graph workflows as framework-level concerns.
  Source: https://learn.microsoft.com/en-us/agent-framework/overview/

## Local Repo Facts

The current `appV2.3.1/appv231` clone is still structurally a coding-agent app:

```text
appV2.3.1/appv231/
  agent/
  ai/
  coding_agent/
  compaction/
  tui/
```

Useful existing hooks:

- `AgentSession` already accepts `tools`, `tool_definitions`, `active_tool_names`, `allowed_tool_names`, `excluded_tool_names`, `custom_prompt`, `append_system_prompt`, `resource_loader`, `settings_manager`, `max_iterations`, and `tool_loop_guardrails`.
- `BuildSystemPromptOptions` already supports custom prompts, appended system prompts, selected tools, prompt guidelines, context files, and skills.
- The tool registry already exposes coding tools and read-only tool groupings for `read`, `bash`, `edit`, `write`, `grep`, `find`, and `ls`.
- The CLI already resolves model, cwd, thinking level, scoped models, session path, and runtime options before constructing `CodingApp`.

Important gap:

- `CodingApp` does not currently expose all profile-relevant `AgentSession` options. A complete profile framework needs either a limited no-red-zone version or a narrow explicit pass-through in `app.py`.

## Recommended Direction

Use a minimal kernel pass-through.

Add profile resolution in green/yellow zones, then make `CodingApp` accept a small profile/session configuration object and pass existing fields to `AgentSession`.

This should not change:

```text
agent loop behavior
provider streaming behavior
provider payload conversion
compaction algorithm
session store format
tool schemas
```

Expected touched areas:

```text
profiles/                         green zone
docs/                             green zone
tests/profile_contract            green zone
appV2.3.1/appv231/cli.py           yellow zone
appV2.3.1/appv231/app.py           red zone, pass-through only
```

The `app.py` change is allowed only because it is an explicit kernel API compatibility change. It must remain a constructor/pass-through change and must not alter runtime control flow.

## First Profile Schema

Keep the first schema small:

```text
name
description
system_prompt
append_system_prompt
active_tools
disabled_tools
default_model
context_length
max_iterations
tool_loop_guardrails
enable_subagents
```

Do not add use-case business logic to the kernel. Profiles should be data/configuration first.

## Initial Built-In Profiles

Start with built-ins that prove the framework:

```text
coding
read-only
research-lite
sre-lite
security-lab-lite
```

The first release should prove profile resolution, profile validation, CLI selection, tool restriction, and prompt selection. It should not yet implement full SRE/security/docs workflows.

## CLI Shape

Target shape:

```bash
appv231 --profile coding --cwd .
appv231 --profile read-only --cwd .
appv231 --profile research-lite --cwd .
appv231 --profile sre-lite --cwd .
appv231 --profile security-lab-lite --cwd .
```

CLI overrides should win over profile defaults for model, thinking level, max iterations, and cwd.

## Contract Tests

Add contract tests before expanding profiles:

```text
profile names are unique
default profile is coding
all profile tool names exist in the tool registry
disabled tools are removed from active tools
read-only profile cannot activate bash/edit/write
profile prompts are passed into system prompt construction
CLI --profile resolves the expected profile
CLI overrides beat profile defaults
CodingApp pass-through does not change default coding behavior
```

Regression checks should include existing tool schema snapshots when those are added.

## Risks

Red-zone creep:
Profiles may tempt changes inside `agent_loop.py`, provider streaming, or compaction. Avoid this. Profiles configure startup behavior only.

Hidden behavior:
Do not hide profile behavior inside extensions first. Start with explicit profile resolution so tests and CLI output can inspect what is active.

Tool mismatch:
Profiles can drift from the tool registry. Contract tests must fail if a profile references a missing tool.

Prompt bloat:
Profiles should not become giant copied system prompts. Prefer a small base prompt plus focused profile appendix.

## Success Criteria

The idea is ready for implementation planning when the design proves:

```text
profiles are first-class app configuration
coding remains the default behavior
kernel control flow is unchanged
new use cases can be introduced without modifying agent_loop.py
profile behavior is testable without live model calls
```

## Decision

Proceed with the profile framework as Idea 1:

```text
minimal kernel pass-through
profile resolver in green/yellow zones
no agent-loop/provider/compaction rewrite
contract tests before adding full use-case agents
```
